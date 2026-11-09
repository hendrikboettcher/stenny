"""Deferred processing: a file-based job queue, AC-power / night checks, low-priority execution.
On Windows the same functions use Windows' own calls (winsys.py) and Task Scheduler instead of launchd."""

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
from xml.sax.saxutils import escape

from . import paths, winsys
from .paths import LOGS_DIR, QUEUE_DIR
AGENT_LABEL = "local.stenny.queue"
AGENT_PATH = Path.home() / "Library/LaunchAgents" / f"{AGENT_LABEL}.plist"
TASK_NAME = "Stenny Queue"  # Windows: the task in Task Scheduler's library (the user's own, no admin rights)
QUEUE_INTERVAL_S = 600

PRIORITY_PREFIX = {
    "normal": [],
    # lower CPU priority and QoS clamp: macOS prefers efficiency cores and yields to apps you use
    "low": ["nice", "-n", "10", "taskpolicy", "-c", "utility"],
    # darwin background: CPU, disk I/O and GPU work are throttled heavily (slowest, least noticeable)
    "background": ["taskpolicy", "-b"],
}


def apply_priority(mode: str) -> None:
    """Re-exec this command under nice/taskpolicy (once). Children inherit the policy.
    Windows: no re-exec, the process lowers its own priority (winsys.set_priority)."""
    if mode == "normal" or os.environ.get("STENNY_PRIORITY"):
        return
    if sys.platform == "win32":
        winsys.set_priority(mode)
        os.environ["STENNY_PRIORITY"] = mode
        return
    argv = [*PRIORITY_PREFIX[mode], sys.executable, "-m", "stenny", *sys.argv[1:]]
    os.execvpe(argv[0], argv, {**os.environ, "STENNY_PRIORITY": mode})


def lower_own_priority(mode: str) -> None:
    """For work that starts inside an already running process (the final pass after live mode)."""
    if mode == "normal":
        return
    if sys.platform == "win32":
        winsys.set_priority(mode)
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
        return Power(*winsys.power())
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
    """Only one queue run at a time (launchd or Task Scheduler fires every 10 min, runs can take longer). The final
    pass right after a live recording takes it too, or the next scheduled run would start a second pass on the same
    job."""

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


def keep_awake() -> "subprocess.Popen | winsys.Awake":
    """Prevent idle sleep while this process runs (released automatically when it exits)."""
    if sys.platform == "win32":
        return winsys.keep_awake()
    return subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])


def install_agent(interval_s: int = QUEUE_INTERVAL_S, exe: str | None = None) -> Path | str:
    """The background job that runs the queue: a launchd agent, on Windows a task in Task Scheduler."""
    if sys.platform == "win32":
        return install_task(interval_s, exe)
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
    if sys.platform == "win32":
        return uninstall_task()
    if not AGENT_PATH.exists():
        return False
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(AGENT_PATH)], capture_output=True)
    AGENT_PATH.unlink()
    return True


# ---------------------------------------------------------------- Windows: Task Scheduler

def task_action(exe: str | None = None) -> tuple[str, str]:
    """What the task runs: Stenny's own pythonw.exe, so no console window opens every 10 minutes (it then goes on
    without a window, see run_without_console), else the `stenny` command."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not exe and pythonw.is_file():
        return str(pythonw), "-m stenny queue run --scheduled"
    return exe or shutil.which("stenny") or sys.argv[0], "queue run --scheduled"


def task_xml(command: str, arguments: str, interval_s: int = QUEUE_INTERVAL_S, user: str | None = None,
             start: datetime | None = None) -> str:
    """The task, like the macOS agent: every 10 minutes while the user is logged on, as that user, without admin
    rights. Power and night hours stay in Python (`queue run --scheduled` checks the config each time), so changing
    them in Settings needs no new task; the task itself only stops Windows' defaults from getting in the way (no
    start on battery, stopped when unplugged, killed after 72 hours). Unplugged, a running pass gives way by itself."""
    start = (start or datetime.now()).replace(microsecond=0).isoformat()
    principal = f"<UserId>{escape(user)}</UserId>" if user else ""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Stenny: makes the final transcripts that wait in the queue (stenny schedule uninstall removes this task).</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Repetition>
        <Interval>PT{max(1, interval_s // 60)}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      {principal}<LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, creationflags=winsys.CREATE_NO_WINDOW)


def install_task(interval_s: int = QUEUE_INTERVAL_S, exe: str | None = None) -> str:
    """Adds (or replaces) the task for the current user. OSError with Windows' message if schtasks refuses."""
    user = os.environ.get("USERNAME")
    if user and os.environ.get("USERDOMAIN"):
        user = f"{os.environ['USERDOMAIN']}\\{user}"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    paths.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    xml = paths.CACHE_DIR / "queue-task.xml"
    xml.write_text(task_xml(*task_action(exe), interval_s, user), encoding="utf-16")  # schtasks wants UTF-16
    try:
        p = _schtasks("/Create", "/TN", TASK_NAME, "/XML", str(xml), "/F")
    finally:
        xml.unlink(missing_ok=True)
    if p.returncode != 0:
        raise OSError((p.stderr or p.stdout).strip() or f"schtasks failed ({p.returncode})")
    return TASK_NAME


def uninstall_task() -> bool:
    if _schtasks("/Query", "/TN", TASK_NAME).returncode != 0:
        return False
    p = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    if p.returncode != 0:
        raise OSError((p.stderr or p.stdout).strip() or f"schtasks failed ({p.returncode})")
    return True


def run_without_console() -> None:
    """Windows, when the task started Stenny with pythonw.exe: programs started from a process without a console
    (ffmpeg, claude) would each open a console window of their own. So the same command runs again as a console
    program without a window (its children share that hidden console), writing to queue.log as the macOS agent
    does, and this one exits with its exit code. Does nothing anywhere else."""
    if sys.platform != "win32" or sys.stdout is not None:
        return
    python = Path(sys.executable).with_name("python.exe")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOGS_DIR / "queue.log", "ab") as log:
        code = subprocess.run([str(python), "-m", "stenny", *sys.argv[1:]], stdin=subprocess.DEVNULL, stdout=log,
                              stderr=log, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                              creationflags=winsys.CREATE_NO_WINDOW).returncode
    sys.exit(code)
