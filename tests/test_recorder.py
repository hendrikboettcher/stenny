"""The native recorder on Windows (stenny-rec.exe, desktop/stenny-rec): how the engine finds it, lists the
microphones through it and stops it. The recorder itself is tested in its crate (`cargo test`); here a small
Python stand-in plays its part, so these run on macOS too."""

import json
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from stenny.live import sources

DEVICES = [
    {"name": "Microphone Array (Intel® Smart Sound Technology)", "default": False, "builtin": True, "virtual": False},
    {"name": "Headset (Alex's AirPods)", "default": True, "builtin": False, "virtual": False},
    {"name": "Microphone (Microsoft Teams Audio)", "default": False, "builtin": False, "virtual": True},
]


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(sources.sys, "platform", "win32")


@pytest.fixture
def listing(monkeypatch, windows):
    calls = []
    monkeypatch.setattr(sources, "find_recorder", lambda: r"C:\Program Files\Stenny\stenny-rec.exe")

    def run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(stdout=json.dumps(DEVICES, ensure_ascii=False), stderr="", returncode=0)

    monkeypatch.setattr(sources.subprocess, "run", run)
    return calls


def test_windows_microphones_come_from_the_recorder(listing):
    assert sources.input_devices() == ["Microphone Array (Intel® Smart Sound Technology)", "Headset (Alex's AirPods)"]
    assert sources.default_input_device() == "Headset (Alex's AirPods)"
    assert sources.builtin_input_device() == "Microphone Array (Intel® Smart Sound Technology)"
    assert sources.virtual_devices() == {"Microphone (Microsoft Teams Audio)"}
    assert all(cmd[1:] == ["--list-json"] for cmd in listing)


def test_backup_auto_is_the_computers_own_microphone_on_windows(listing):
    main = sources.make_mic(None)
    assert isinstance(main, sources.NativeMicSource) and main.device == "Headset (Alex's AirPods)"
    backup = sources.make_backup("auto", main)
    assert backup.device == "Microphone Array (Intel® Smart Sound Technology)"
    # the built-in one as the main microphone: no backup of itself
    assert sources.make_backup("auto", sources.make_mic("Microphone Array (Intel® Smart Sound Technology)")) is None


def test_no_recorder_no_devices_and_a_clear_error(monkeypatch, windows):
    monkeypatch.setattr(sources, "find_recorder", lambda: None)
    assert sources.input_devices() == [] and sources.default_input_device() is None
    with pytest.raises(SystemExit, match="stenny-rec.exe"):
        sources.make_mic("Microphone", "native")


def test_a_broken_listing_is_no_devices(monkeypatch, windows):
    monkeypatch.setattr(sources, "find_recorder", lambda: "stenny-rec.exe")
    monkeypatch.setattr(sources.subprocess, "run", lambda cmd, **kw: SimpleNamespace(stdout="stenny-rec: error: …"))
    assert sources.input_devices() == []


def test_windows_finds_the_recorder_the_app_passes(monkeypatch, windows, tmp_path):
    rec = tmp_path / "stenny-rec.exe"
    rec.write_bytes(b"MZ")
    monkeypatch.setenv("STENNY_REC", str(rec))
    assert sources.find_recorder() == str(rec)
    monkeypatch.setenv("STENNY_REC", str(tmp_path / "gone.exe"))
    assert sources.find_recorder() in (None, str(sources.Path(sources.__file__).resolve().parents[3]
                                                   / "desktop/stenny-rec/target/release/stenny-rec.exe"))


# The stand-in: appends like the recorder, and on `stop` writes its last audio and exits 0. Its stdin closing
# without `stop` is the engine dying: exit 5 (the real one keeps recording for a while first).
STAND_IN = """
import sys
out = sys.argv[sys.argv.index("--out") + 1]
with open(out, "ab") as f:
    f.write(b"\\x01\\x00")
    f.flush()
    for line in sys.stdin:
        if line.strip() == "stop":
            f.write(b"\\x02\\x00")
            sys.exit(0)
sys.exit(5)
"""


def test_windows_stops_the_recorder_by_asking(monkeypatch, windows, tmp_path):
    script = tmp_path / "stand_in.py"
    script.write_text(STAND_IN, encoding="utf-8")
    real = subprocess.Popen
    started = []

    def popen(cmd, **kw):
        started.append((cmd, kw))
        return real([sys.executable, str(script), *cmd[1:]], **kw)

    monkeypatch.setattr(sources.subprocess, "Popen", popen)
    pcm = tmp_path / "Calculus 2026-11-09.live.pcm"
    pcm.write_bytes(b"\x00\x00")  # the lecture so far, from before a pause
    src = sources.NativeMicSource("Headset (Alex's AirPods)", recorder="stenny-rec.exe")
    src.start(pcm)
    proc = src._proc
    deadline = time.monotonic() + 10
    while pcm.stat().st_size < 4 and time.monotonic() < deadline:
        time.sleep(0.02)
    src.stop()
    assert proc.returncode == 0                      # it stopped by itself, not terminated
    assert pcm.read_bytes() == b"\x00\x00\x01\x00\x02\x00"  # appended, and the last audio is in
    cmd, kw = started[0]
    assert cmd == ["stenny-rec.exe", "--out", str(pcm), "--device", "Headset (Alex's AirPods)"]
    assert kw["stdin"] == subprocess.PIPE


def test_macos_keeps_stopping_with_sigterm(monkeypatch, tmp_path):
    monkeypatch.setattr(sources.sys, "platform", "darwin")
    started = []
    monkeypatch.setattr(sources.subprocess, "Popen", lambda cmd, **kw: started.append(kw) or SimpleNamespace(
        stdin=None, poll=lambda: 0))
    src = sources.NativeMicSource("Built-in Microphone", recorder="/Applications/Stenny.app/Contents/MacOS/stenny-rec")
    src.start(tmp_path / "x.live.pcm")
    assert started[0]["stdin"] == subprocess.DEVNULL and started[0]["creationflags"] == 0
    src.stop()


def test_windows_orphans_are_found_by_their_exact_path(monkeypatch, tmp_path):
    from stenny import cli

    monkeypatch.setattr(cli.sys, "platform", "win32")
    out = tmp_path / "a recording.pcm"
    rec = r"C:\Program Files\Stenny\stenny-rec.exe"
    monkeypatch.setattr(cli, "_windows_recorders", lambda: [
        (11, subprocess.list2cmdline([rec, "--out", str(out), "--device", "Microphone (USB Audio)"])),
        (12, subprocess.list2cmdline([rec, "--out", str(out) + "x2"])),  # contains the first path: no match
        (13, "powershell -NoProfile -Command Get-CimInstance … stenny-rec …"),
    ])
    ended = []
    monkeypatch.setattr(cli, "_end_windows_process", ended.append)
    cli.stop_orphan_recorders(tmp_path / "nothing.pcm")
    assert ended == []
    cli.stop_orphan_recorders(out)
    assert ended == [11]


def test_windows_process_listing(monkeypatch):
    from stenny import cli

    def listing(stdout):
        monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: SimpleNamespace(stdout=stdout))
        return cli._windows_recorders()

    assert listing('{"ProcessId":7,"CommandLine":"stenny-rec.exe --out x.pcm"}') == [(7, "stenny-rec.exe --out x.pcm")]
    assert listing('[{"ProcessId":7,"CommandLine":null},{"ProcessId":8,"CommandLine":"a"}]') == [(7, ""), (8, "a")]
    assert listing("") == [] and listing("not json") == []
