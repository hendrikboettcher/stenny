"""Swappable audio sources for live mode.

A source records by itself: ffmpeg writes 16 kHz mono s16le PCM straight into the session's
recording file. No Python sits in that path, so a busy transcriber can't make the device
drop audio (Whisper holds Python's GIL for seconds at a time, which made ffmpeg lose
~1 s of microphone audio per chunk when Python was reading its output).

The session tails the file for the live draft. Pausing stops ffmpeg, so the microphone is
really off (macOS's orange mic indicator goes out too).

The microphone itself is recorded by the native `stenny-rec`: AVAudioEngine on macOS (app/Sources/stenny-rec),
WASAPI on Windows (`stenny-rec.exe`, desktop/stenny-rec), with the same command line and file. On Windows it also
lists the microphones (`--list-json`), where macOS asks system_profiler and ffmpeg.
"""

import contextlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from ..paths import bundled, tool
from ..textproc import SAMPLE_RATE


def _windows() -> bool:
    return sys.platform == "win32"


def _no_window() -> int:
    """Windows: no console window for a helper started from Stenny (it has none to share)."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if _windows() else 0


def windows_devices() -> list[dict]:
    """Windows' active inputs as stenny-rec.exe sees them: [{"name", "default", "builtin", "virtual"}]."""
    recorder = find_recorder()
    if not recorder:
        return []
    try:
        p = subprocess.run([recorder, "--list-json"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=20, creationflags=_no_window())
        devices = json.loads(p.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    return [d for d in devices if isinstance(d, dict) and d.get("name")] if isinstance(devices, list) else []


def default_input_device() -> str | None:
    """Name of the current default input device (macOS: System Settings → Sound → Input; Windows: Settings →
    System → Sound → Input)."""
    if _windows():
        return next((d["name"] for d in windows_devices() if d.get("default")), None)
    out = subprocess.run(["system_profiler", "SPAudioDataType"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    current = None
    for line in out.splitlines():
        if m := re.match(r"^ {8}(\S.*):$", line):
            current = m.group(1)
        elif "Default Input Device: Yes" in line:
            return current
    return None


def builtin_input_device() -> str | None:
    """Name of the computer's own microphone: the Mac's (Transport: Built-in), e.g. "MacBook Air Microphone"; on
    Windows a microphone on the computer's own audio bus (stenny-rec.exe's guess, desktop/stenny-rec/src/devices.rs)."""
    if _windows():
        return next((d["name"] for d in windows_devices() if d.get("builtin") and not d.get("virtual")), None)
    out = subprocess.run(["system_profiler", "SPAudioDataType"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    current, has_input = None, False
    for line in out.splitlines():
        if m := re.match(r"^ {8}(\S.*):$", line):
            current, has_input = m.group(1), False
        elif "Input Channels:" in line:
            has_input = True
        elif "Transport: Built-in" in line and has_input:
            return current
    return None


def virtual_devices() -> set[str]:
    """Audio devices apps install for themselves (Microsoft Teams Audio): they hear no room."""
    if _windows():
        return {d["name"] for d in windows_devices() if d.get("virtual")}
    out = subprocess.run(["system_profiler", "SPAudioDataType"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    current, virtual = None, set()
    for line in out.splitlines():
        if m := re.match(r"^ {8}(\S.*):$", line):
            current = m.group(1)
        elif "Transport: Virtual" in line:
            virtual.add(current)
    return virtual


def input_devices() -> list[str]:
    """Real microphones only. A virtual device can still be used by naming it: `[live] device` in the config."""
    if _windows():
        return [d["name"] for d in windows_devices() if not d.get("virtual")]
    p = subprocess.run([tool("ffmpeg"), "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = p.stderr.split("audio devices:", 1)[-1]
    virtual = virtual_devices()
    return [d for d in re.findall(r"\[\d+\] (.+)", text) if d not in virtual]


class FfmpegSource:
    """Runs ffmpeg with its output appended to a file."""

    name = "ffmpeg"
    microphone = None    # input device name for the transcript's frontmatter (None = unknown)
    clock_rate = 1.0     # seconds of audio per wall-clock second
    restartable = False  # True: if input stops unexpectedly, reconnect instead of ending the session

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._out = None

    def _cmd(self) -> list[str]:
        raise NotImplementedError

    def start(self, path: Path) -> None:
        self._out = open(path, "ab")
        # warnings (e.g. dropped audio packets) go to <recording>.ffmpeg.log for `stenny check`
        self.log_path = path.with_suffix(".ffmpeg.log")
        self._log = open(self.log_path, "ab")
        self._log.write(f"--- start {time.strftime('%H:%M:%S')}\n".encode())
        self._log.flush()
        self._proc = subprocess.Popen(
            [tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "level+warning", *self._cmd(),
             "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-flush_packets", "1", "-"],
            stdout=self._out, stderr=self._log, stdin=subprocess.DEVNULL)

    @property
    def finished(self) -> bool:
        """ffmpeg exited by itself (end of file, device gone)."""
        return self._proc is not None and self._proc.poll() is not None

    def error(self) -> str:
        if self._proc and self._proc.poll() not in (None, 0, -15, 255) and self.log_path.exists():
            lines = self.log_path.read_text(errors="replace").splitlines()
            return " ".join(l for l in lines[-5:] if "error" in l.lower() or "fatal" in l.lower())[-300:]
        return ""

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        for f in (self._out, getattr(self, "_log", None)):
            if f:
                f.close()
        self._proc, self._out, self._log = None, None, None


class MicSource(FfmpegSource):
    restartable = True  # e.g. an iPhone (Continuity) microphone that goes out of range

    def __init__(self, device: str | None = None):
        super().__init__()
        self.device = device or default_input_device() or "default"
        self.name = f"mic ({self.device})"
        self.microphone = None if self.device == "default" else self.device

    def _cmd(self) -> list[str]:
        # a large input queue: if anything ever stalls ffmpeg's output, it buffers instead of dropping
        return ["-thread_queue_size", "4096", "-f", "avfoundation", "-i", f":{self.device}"]


def find_recorder() -> str | None:
    """The native recorder: $STENNY_REC (the app passes its own copy), the installed app's (Stenny.app, or next to
    Stenny.exe on Windows), or a development build
    (app/Sources/stenny-rec on macOS, desktop/stenny-rec on Windows)."""
    repo = Path(__file__).resolve().parents[3]
    if _windows():
        candidates = [os.environ.get("STENNY_REC"), bundled("stenny-rec"),  # next to the installed Stenny.exe
                      str(repo / "desktop/stenny-rec/target/release/stenny-rec.exe")]
        return next((c for c in candidates if c and os.path.isfile(c)), None)
    candidates = [os.environ.get("STENNY_REC"), bundled("stenny-rec"), str(repo / "app/.build/release/stenny-rec")]
    return next((c for c in candidates if c and os.access(c, os.X_OK)), None)


class NativeMicSource(MicSource):
    """Records through AVAudioEngine (CoreAudio) with the bundled `stenny-rec` helper, or WASAPI on Windows.
    ffmpeg's avfoundation input silently dropped ~1 s every ~8 s (12 %) on an M4 MacBook Air,
    with the built-in and the iPhone microphone alike; this path doesn't.

    Stopping: SIGTERM on macOS. Windows has no signal to catch (terminate() ends the process mid-write), so there
    the recorder gets a pipe on stdin and `stop` on it; the pipe closing without one tells it the engine is gone."""

    def __init__(self, device: str | None = None, recorder: str | None = None):
        super().__init__(device)
        self.recorder = recorder or find_recorder()
        self.name = f"mic ({self.device})"

    def start(self, path: Path) -> None:
        self.log_path = path.with_suffix(".rec.log")
        self._log = open(self.log_path, "ab")
        self._log.write(f"--- start {time.strftime('%H:%M:%S')}\n".encode())
        self._log.flush()
        self._out = None
        self._proc = subprocess.Popen([self.recorder, "--out", str(path), "--device", self.device],
                                      stdout=subprocess.DEVNULL, stderr=self._log,
                                      stdin=subprocess.PIPE if _windows() else subprocess.DEVNULL,
                                      creationflags=_no_window())

    def stop(self) -> None:
        p = self._proc
        if p and p.stdin and p.poll() is None:  # Windows: ask, let it write the last audio, only then terminate()
            with contextlib.suppress(OSError):
                p.stdin.write(b"stop\n")
                p.stdin.close()
            with contextlib.suppress(subprocess.TimeoutExpired):
                p.wait(timeout=5)
        if p and p.stdin:
            with contextlib.suppress(OSError):
                p.stdin.close()
        super().stop()


def make_mic(device: str | None, recorder: str = "native") -> MicSource:
    """Native recorder when available (and not disabled in the config), else ffmpeg. On Windows always the native
    one (`[live] recorder` is macOS only): ffmpeg's inputs are no way around it."""
    if _windows():
        if not (found := find_recorder()):
            raise SystemExit("stenny-rec.exe, Stenny's microphone recorder, wasn't found: set STENNY_REC to it, or "
                             "build it with desktop/scripts/stenny-rec.sh.")
        return NativeMicSource(device, found)
    if recorder != "ffmpeg" and find_recorder():
        return NativeMicSource(device)
    return MicSource(device)


class FileSource(FfmpegSource):
    """Plays an audio file in real time (or `speed`× faster), to test live mode without a microphone.
    Pause/resume continue from where playback stopped, like a lecture that went on."""

    def __init__(self, path, speed: float = 1.0):
        super().__init__()
        self.path, self.speed, self.offset = path, speed, 0.0
        self.clock_rate = speed
        self.name = f"file ({path})"
        from ..audio import recording_device
        self.microphone = recording_device(path)  # what recorded the test file, if its metadata says
        self._started_at = None

    def _cmd(self) -> list[str]:
        return ["-readrate", str(self.speed), "-ss", f"{self.offset:.2f}", "-i", str(self.path)]

    def start(self, path: Path) -> None:
        self._started_at = time.monotonic()
        super().start(path)

    def stop(self) -> None:
        if self._started_at is not None:
            self.offset += (time.monotonic() - self._started_at) * self.speed
            self._started_at = None
        super().stop()


def make_backup(setting: str, main: MicSource) -> MicSource | None:
    """A second microphone recorded alongside the main one, so the final pass can fill the main one's
    gaps (an iPhone stops sending audio while it is in use). `setting`: "auto" (the computer's built-in
    microphone, unless that is already the main one), "off", or a device name. Native recorder only."""
    if setting == "off" or not isinstance(main, NativeMicSource):
        return None
    name = builtin_input_device() if setting == "auto" else setting
    primary = main.device if main.device != "default" else default_input_device()
    if not name or name == primary:
        return None
    return NativeMicSource(name, main.recorder)
