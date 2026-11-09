"""Deferred processing: a file-based job queue, AC-power / night checks, low-priority execution."""

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from .paths import LOGS_DIR, QUEUE_DIR
AGENT_LABEL = "local.stenny.queue"
AGENT_PATH = Path.home() / "Library/LaunchAgents" / f"{AGENT_LABEL}.plist"

PRIORITY_PREFIX = {
    "normal": [],
    # lower CPU priority and QoS clamp: macOS prefers efficiency cores and yields to apps you use
    "low": ["nice", "-n", "10", "taskpolicy", "-c", "utility"],
    # darwin background: CPU, disk I/O and GPU work are throttled heavily (slowest, least noticeable)
    "background": ["taskpolicy", "-b"],
}


def apply_priority(mode: str) -> None:
    """Re-exec this command under nice/taskpolicy (once). Children inherit the policy."""
    if mode == "normal" or os.environ.get("STENNY_PRIORITY"):
        return
    if sys.platform == "win32":  # no nice/taskpolicy: this process's own priority class, children inherit it
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00000040 if mode == "background" else 0x00004000)  # IDLE / BELOW_NORMAL
        os.environ["STENNY_PRIORITY"] = mode
        return
    argv = [*PRIORITY_PREFIX[mode], sys.executable, "-m", "stenny", *sys.argv[1:]]
    os.execvpe(argv[0], argv, {**os.environ, "STENNY_PRIORITY": mode})


def lower_own_priority(mode: str) -> None:
    """For work that starts inside an already running process (the final pass after live mode)."""
    if mode == "normal":
        return
    os.nice(10)
    if mode == "background":
        subprocess.run(["taskpolicy", "-b", "-p", str(os.getpid())], capture_output=True)


class Power(NamedTuple):
    plugged_in: bool
    battery: int | None  # percent charged; None without a battery (a desktop) or when it can't be read


def power() -> Power:
    """Whether the computer is plugged in, and how full its battery is. Unknown counts as plugged in, so a desktop
    (or a check that fails) never holds work back."""
    if sys.platform == "win32":
        return _power_windows()
    try:
        out = subprocess.run(["pmset", "-g", "ps"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return Power(True, None)
    lines = out.splitlines()
    if not lines:
        return Power(True, None)
    m = re.search(r"\t(\d+)%;", out)  # " -InternalBattery-0 (id=…)\t82%; discharging; …"
    return Power("AC Power" in lines[0], int(m.group(1)) if m else None)


def _power_windows() -> Power:
    """GetSystemPowerStatus: ACLineStatus 0 = on battery, 1 = plugged in, 255 = unknown; BatteryLifePercent 255 =
    unknown; BatteryFlag 128 = no battery."""
    import ctypes
    from ctypes import wintypes

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [("ACLineStatus", wintypes.BYTE), ("BatteryFlag", wintypes.BYTE),
                    ("BatteryLifePercent", wintypes.BYTE), ("SystemStatusFlag", wintypes.BYTE),
                    ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]

    st = SYSTEM_POWER_STATUS()
    try:
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return Power(True, None)
    except (AttributeError, OSError):
        return Power(True, None)
    ac, flag, percent = st.ACLineStatus & 0xFF, st.BatteryFlag & 0xFF, st.BatteryLifePercent & 0xFF
    battery = None if flag == 128 or percent == 255 else percent
    return Power(ac != 0, battery)


def on_ac_power() -> bool:
    return power().plugged_in


def unplugged_check(every_s: float = 20):
    """For long work that gives way when the charger is pulled: the returned function is True once on battery.
    pmset is asked at most every `every_s` seconds (it's called between Whisper chunks)."""
    last = [-every_s, False]

    def unplugged() -> bool:
        if time.monotonic() - last[0] >= every_s:
            last[:] = [time.monotonic(), not on_ac_power()]
        return last[1]
    return unplugged


def in_window(start: str, end: str, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    t = now.strftime("%H:%M")
    return start <= t < end if start <= end else (t >= start or t < end)


def enqueue(file: Path, course: str, lang: str | None, use_llm: bool, live: Path | None = None,
            slides: Path | None = None, kind: str = "lecture") -> Path:
    """`live`: session.json of a live recording (the final pass then keeps its folder and pause times).
    `slides`: a PDF to store in the lecture folder."""
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    job = {"file": str(file.resolve()), "course": course, "kind": kind, "lang": lang, "llm": use_llm,
           "live": str(live) if live else None, "slides": str(slides.resolve()) if slides else None,
           "added": datetime.now().isoformat(timespec="seconds")}
    path = QUEUE_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{file.stem}.json"
    path.write_text(json.dumps(job, indent=1), encoding="utf-8")
    return path


def pending() -> list[Path]:
    return sorted(QUEUE_DIR.glob("*.json")) if QUEUE_DIR.exists() else []


if sys.platform == "win32":
    import msvcrt

    def _lock(f) -> None:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)  # OSError while another process holds it

    def _unlock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(f) -> None:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)  # BlockingIOError (an OSError) while another holds it

    def _unlock(f) -> None:
        fcntl.flock(f, fcntl.LOCK_UN)


class QueueLock:
    """Only one queue run at a time (launchd fires every 10 min, runs can take longer). The final pass right after
    a live recording takes it too, or the next scheduled run would start a second pass on the same job."""

    def __enter__(self):
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        self.f = open(QUEUE_DIR / "queue.lock", "w")
        try:
            _lock(self.f)
        except OSError:
            self.f.close()
            return None
        return self

    def __exit__(self, *exc):
        if not self.f.closed:
            _unlock(self.f)
            self.f.close()


def keep_awake() -> None:
    """Prevent idle sleep while this process runs (released automatically when it exits)."""
    if sys.platform == "win32":  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED, for this process's main thread
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        return
    subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])


def install_agent(interval_s: int = 600, exe: str | None = None) -> Path:
    exe = exe or shutil.which("stenny") or sys.argv[0]
    log = str(LOGS_DIR / "queue.log")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": AGENT_LABEL,
        "ProgramArguments": [exe, "queue", "run", "--scheduled"],
        "StartInterval": interval_s,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        "ProcessType": "Background",
    }
    AGENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENT_PATH.write_bytes(plistlib.dumps(plist))
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(AGENT_PATH)], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(AGENT_PATH)], check=True)
    return AGENT_PATH


def uninstall_agent() -> bool:
    if not AGENT_PATH.exists():
        return False
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(AGENT_PATH)], capture_output=True)
    AGENT_PATH.unlink()
    return True
