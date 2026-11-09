"""The control channel of a running recording (live/control.py): a Unix socket on macOS, a named pipe on Windows.
These run the real channel on whichever system the tests run on, so Windows CI checks the pipe (issue #2)."""

import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from stenny import paths
from stenny.live import control


class FakeSession:
    """What ControlServer needs of a LiveSession: command(cmd, device) → a reply dict."""

    def __init__(self):
        self.state, self.calls = "recording", []

    def command(self, cmd, device=None):
        self.calls.append((cmd, device))
        if cmd == "boom":
            raise ValueError("broken on purpose")
        if cmd == "slow":
            time.sleep(0.5)
        if cmd == "toggle":
            self.state = "paused" if self.state == "recording" else "recording"
        if cmd == "microphone":
            return {"ok": True, "state": self.state, "source": device}
        if cmd == "big":
            return {"ok": True, "transcript": "Wörter " * 60000}  # a long lecture's live transcript, ~420 KB
        return {"ok": True, "state": self.state}


@pytest.fixture
def address():
    # short: a Unix socket's path may not be longer than 104 bytes on macOS (pytest's tmp_path can be)
    folder = Path(tempfile.mkdtemp(prefix="st-"))
    return paths.control_address(folder)


@pytest.fixture
def server(address):
    session = FakeSession()
    srv = control.ControlServer(session, address)
    yield session, address
    srv.close()


def test_the_address_is_a_socket_on_a_mac_and_a_pipe_per_data_place_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert paths.control_address(Path("/data")) == Path("/data/live.sock")
    monkeypatch.setattr(sys, "platform", "win32")
    alice = str(paths.control_address(Path("C:/Users/alice/AppData/Local/Stenny")))
    bob = str(paths.control_address(Path("C:/Users/bob/AppData/Local/Stenny")))
    assert alice.startswith("\\\\.\\pipe\\stenny-live-") and bob.startswith("\\\\.\\pipe\\stenny-live-")
    assert alice != bob  # pipes share one name space on the PC: each user has their own
    assert alice == str(paths.control_address(Path("c:/users/ALICE/AppData/Local/Stenny")))  # Windows ignores case


def test_nothing_running_means_no_session(address):
    assert not control.is_running(address)
    with pytest.raises(control.NoSession):
        control.send("status", address)


def test_commands_and_replies_go_both_ways(server):
    session, address = server
    assert control.is_running(address)
    assert control.send("status", address) == {"ok": True, "state": "recording"}
    assert control.send("toggle", address)["state"] == "paused"
    reply = control.send("microphone", address, device="Mikrofon (Büro) – USB")
    assert reply["source"] == "Mikrofon (Büro) – USB"  # not ASCII, both ways
    assert ("microphone", "Mikrofon (Büro) – USB") in session.calls


def test_a_long_reply_arrives_whole(server):
    _, address = server
    assert control.send("big", address)["transcript"] == "Wörter " * 60000


def test_a_failing_command_answers_and_the_recording_goes_on(server):
    _, address = server
    reply = control.send("boom", address)
    assert reply == {"ok": False, "error": "broken on purpose"}
    assert control.send("status", address)["ok"]


def test_requests_at_the_same_time_are_all_answered(server):
    """The level meter asks ~20×/s while the window asks for the status: a slow request doesn't hold up the rest."""
    _, address = server
    replies = []
    slow = threading.Thread(target=lambda: replies.append(control.send("slow", address)))
    slow.start()
    started = time.monotonic()
    for _ in range(20):
        replies.append(control.send("level", address))
    assert time.monotonic() - started < 0.5 + 2  # didn't queue behind the slow one
    slow.join()
    assert len(replies) == 21 and all(r["ok"] for r in replies)


def test_only_one_recording_answers(server):
    _, address = server
    with pytest.raises(RuntimeError, match="already running"):
        control.ControlServer(FakeSession(), address)
    assert control.send("status", address)["ok"]  # the first one still answers


def test_after_closing_nothing_answers_and_it_can_start_again(address):
    srv = control.ControlServer(FakeSession(), address)
    srv.close()
    assert not control.is_running(address)
    again = control.ControlServer(FakeSession(), address)
    try:
        assert control.is_running(address)
    finally:
        again.close()


@pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets: macOS")
def test_on_a_mac_only_you_can_connect_and_a_crashed_sessions_socket_is_replaced(address):
    import socket
    address.parent.mkdir(parents=True, exist_ok=True)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(address))  # a crashed session's socket: the file is there, nobody answers
    stale.close()
    srv = control.ControlServer(FakeSession(), address)
    try:
        assert address.stat().st_mode & 0o777 == 0o600
        assert control.send("status", address)["ok"]
    finally:
        srv.close()
    assert not address.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="named pipes: Windows")
def test_on_windows_an_old_layouts_socket_file_is_never_taken_for_a_pipe(tmp_path):
    (tmp_path / "live.sock").write_text("")
    assert not control.is_running(tmp_path / "live.sock")
