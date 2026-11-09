"""Local control channel for a running live session, speaking one JSON line per request:

    {"cmd": "status" | "pause" | "resume" | "toggle" | "stop"}  →  {"ok": true, "state": …, …}
    {"cmd": "level"}  →  {"ok": true, "state": …, "level_db": …, "input_lost": …}  (cheap, for the level meter)
    {"cmd": "microphone", "device": "<name>"}  →  switches the main microphone of the running recording

Used by `stenny pause|resume|toggle|stop|status`, the engine behind the app (app_live.py) and the old menu bar app.
Where it listens comes from paths.py (`paths.SOCKET`, `stenny where`):
- macOS: a Unix socket in the data place. Only the current user can connect (socket file mode 0600).
- Windows: a named pipe, `\\\\.\\pipe\\stenny-live-<tag>`, one per data place, so each user (and each $STENNY_HOME)
  has their own. One request and one reply are each one pipe message (the same JSON line). Only the user who started
  the recording can write to it (the pipe's default security: others may only read, so they can't send a command),
  and only one recording can own the name. A pipe goes away with its process, so there's nothing stale to clean up.
"""

import json
import os
import socket
import sys
import threading
from pathlib import Path

from ..paths import SOCKET

WINDOWS = sys.platform == "win32"
PIPE_PREFIX = "\\\\.\\pipe\\"


class NoSession(Exception):
    pass


def _no_session() -> NoSession:
    return NoSession("No live session is running.")


def _reply(session, request: bytes) -> bytes:
    """One request → one reply, both a JSON line. A bad request never kills the recorder."""
    try:
        msg = json.loads(request or b"{}")
        reply = session.command(msg.get("cmd", "status"), msg.get("device"))
    except Exception as e:
        import traceback
        print(f"Control command failed: {e}\n{traceback.format_exc()}", flush=True)
        reply = {"ok": False, "error": str(e)}
    return json.dumps(reply, ensure_ascii=False).encode() + b"\n"


# ------------------------------------------------------------------------------------------ the client

def send(cmd: str, path: Path = SOCKET, timeout: float = 10.0, **extra) -> dict:
    request = json.dumps({"cmd": cmd, **extra}).encode() + b"\n"
    data = (_send_pipe if WINDOWS else _send_socket)(request, path, timeout)
    return json.loads(data or b"{}")


def _send_socket(request: bytes, path: Path, timeout: float) -> bytes:
    if not path.exists():
        raise _no_session()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(str(path))
        except (ConnectionRefusedError, FileNotFoundError):
            raise _no_session() from None
        s.sendall(request)
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    return data


def _send_pipe(request: bytes, path: Path, timeout: float) -> bytes:
    from multiprocessing.connection import Client

    name = str(path)
    if not name.lower().startswith(PIPE_PREFIX):  # an old layout's socket file (migrate.py asks): never a pipe
        raise _no_session()
    try:
        conn = Client(name, family="AF_PIPE")  # no pipe of that name: FileNotFoundError at once
    except FileNotFoundError:
        raise _no_session() from None
    with conn:
        conn.send_bytes(request)
        if not conn.poll(timeout):
            raise TimeoutError(f"The live session didn't answer within {timeout:g} s.")
        try:
            return conn.recv_bytes()
        except EOFError:
            return b""


def is_running(path: Path = SOCKET) -> bool:
    try:
        send("status", path, timeout=2)
        return True
    except (NoSession, OSError, EOFError, json.JSONDecodeError):
        return False


# ------------------------------------------------------------------------------------------ the server

class ControlServer:
    """Answers on `path` from threads of its own until `close()`. Raises RuntimeError if another live session
    already answers there."""

    def __init__(self, session, path: Path = SOCKET):
        self.session, self.path = session, path
        if is_running(path):
            raise RuntimeError("Another live session is already running (`stenny status`).")
        self._server = (_PipeServer if WINDOWS else _SocketServer)(lambda request: _reply(session, request), path)

    def close(self) -> None:
        self._server.close()


class _SocketServer:
    def __init__(self, answer, path: Path):
        import socketserver

        self.path = path

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(answer(self.rfile.readline()))

        if path.exists() or path.is_symlink():
            path.unlink()  # stale socket from a crashed session (a running one answered above)
        path.parent.mkdir(parents=True, exist_ok=True)
        old_umask = os.umask(0o177)
        try:
            self.server = socketserver.ThreadingUnixStreamServer(str(path), Handler)
        finally:
            os.umask(old_umask)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.path.unlink(missing_ok=True)


class _PipeServer:
    READ_TIMEOUT = 10.0  # a client that connects and says nothing doesn't keep its thread forever

    def __init__(self, answer, path: Path):
        from multiprocessing.connection import Listener

        self.name, self.answer, self.closing = str(path), answer, False
        try:
            # the first instance of this name (FILE_FLAG_FIRST_PIPE_INSTANCE): if someone else owns it, this fails
            self.listener = Listener(self.name, family="AF_PIPE")
        except PermissionError:
            raise RuntimeError("Another live session is already running (`stenny status`).") from None
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn = self.listener.accept()
            except OSError:
                if self.closing:
                    return
                continue  # a client that hung up while connecting
            if self.closing:
                conn.close()
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn) -> None:
        with conn:
            try:
                if conn.poll(self.READ_TIMEOUT):
                    conn.send_bytes(self.answer(conn.recv_bytes()))
            except (OSError, EOFError):
                pass  # the client went away

    def close(self) -> None:
        from multiprocessing.connection import Client

        self.closing = True
        try:  # wake the waiting accept(): it sees `closing` and ends
            Client(self.name, family="AF_PIPE").close()
        except OSError:
            pass
        self.thread.join(timeout=5)
        self.listener.close()
