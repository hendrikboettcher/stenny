"""Local control channel for a running live session: a Unix socket speaking one JSON line per request.

    {"cmd": "status" | "pause" | "resume" | "toggle" | "stop"}  →  {"ok": true, "state": …, …}
    {"cmd": "level"}  →  {"ok": true, "state": …, "level_db": …, "input_lost": …}  (cheap, for the level meter)
    {"cmd": "microphone", "device": "<name>"}  →  switches the main microphone of the running recording

Used by `stenny pause|resume|toggle|stop|status` and by the Stenny menu bar app.
Only the current user can connect (socket file mode 0600).
"""

import json
import os
import socket
import socketserver
import threading
from pathlib import Path

from ..paths import SOCKET


class NoSession(Exception):
    pass


def send(cmd: str, path: Path = SOCKET, timeout: float = 10.0, **extra) -> dict:
    if not path.exists():
        raise NoSession("No live session is running.")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(str(path))
        except (ConnectionRefusedError, FileNotFoundError):
            raise NoSession("No live session is running.") from None
        s.sendall(json.dumps({"cmd": cmd, **extra}).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data or b"{}")


def is_running(path: Path = SOCKET) -> bool:
    try:
        send("status", path, timeout=2)
        return True
    except (NoSession, OSError, json.JSONDecodeError):
        return False


class ControlServer:
    def __init__(self, session, path: Path = SOCKET):
        self.session, self.path = session, path
        handler_session = session

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    msg = json.loads(self.rfile.readline() or b"{}")
                    reply = handler_session.command(msg.get("cmd", "status"), msg.get("device"))
                except Exception as e:  # never let a bad request kill the recorder
                    import traceback
                    print(f"Control command failed: {e}\n{traceback.format_exc()}", flush=True)
                    reply = {"ok": False, "error": str(e)}
                self.wfile.write(json.dumps(reply, ensure_ascii=False).encode() + b"\n")

        if path.exists():
            if is_running(path):
                raise RuntimeError("Another live session is already running (`stenny status`).")
            path.unlink()  # stale socket from a crashed session
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
