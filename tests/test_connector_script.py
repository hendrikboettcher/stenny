"""connector/server/index.js, the Claude Desktop extension's starter: which Stenny it finds and runs. It runs with
Node (Claude Desktop's own on users' computers); skipped where there's no `node`."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "connector" / "server" / "index.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node.js")
posix = pytest.mark.skipif(sys.platform == "win32", reason="a Mac's Stenny.app")
windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows' install folders")


@pytest.fixture
def home(tmp_path):
    """A home folder with nothing of Stenny in it, and a PATH with only Node on it (no `stenny` of this computer's)."""
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("STENNY", "PYTHON"))}
    env.update(HOME=str(home), USERPROFILE=str(home), PATH=str(Path(NODE).parent),
               LOCALAPPDATA=str(home / "AppData/Local"), ProgramFiles=str(tmp_path / "Program Files"),
               STENNY_HOME=str(tmp_path / "Stenny"))
    if sys.platform == "win32":  # Windows needs its own folder for `reg` and friends
        env["PATH"] += os.pathsep + os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    return home, env


def candidates(env) -> list[dict]:
    done = subprocess.run([NODE, str(SCRIPT), "--candidates"], capture_output=True, text=True, env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def mac_app_engine(home: Path, log: Path) -> Path:
    """~/Applications/Stenny.app with an engine whose python3 is this checkout's Python, noting the environment
    it's started with."""
    engine = home / "Applications/Stenny.app/Contents/Resources/engine"
    (engine / "bin").mkdir(parents=True)
    (engine / "stenny-engine.json").write_text("{}", encoding="utf-8")
    python = engine / "bin/python3"
    python.write_text("#!/bin/sh\n"
                      f'echo "$PYTHONDONTWRITEBYTECODE ${{PYTHONHOME:-none}} $*" >> "{log}"\n'
                      f'exec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    return python


def talk(env, *messages: dict) -> list[dict]:
    """Each message in turn, waiting for the answer to each request, as Claude does (an MCP server stops at the end
    of its input, answered or not)."""
    child = subprocess.Popen([NODE, str(SCRIPT)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, env=env)
    replies = []
    try:
        for message in messages:
            child.stdin.write(json.dumps(message) + "\n")
            child.stdin.flush()
            if "id" in message:
                line = child.stdout.readline()
                assert line, "the connector ended without answering"
                replies.append(json.loads(line))
    finally:
        child.stdin.close()
        child.wait(timeout=60)
    return replies


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
TOOLS = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


@posix
def test_the_engine_inside_the_app_comes_before_an_older_stenny(home):
    home, env = home
    python = mac_app_engine(home, home / "log")
    older = home / ".local/bin/stenny"
    older.parent.mkdir(parents=True)
    older.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    older.chmod(0o755)
    found = candidates(env)
    assert found[0] == {"program": str(python), "args": ["-m", "stenny"], "app": True}
    assert found[1]["program"] == str(older) and found[1]["app"] is False


@posix
def test_an_app_without_its_engine_isnt_taken(home):
    home, env = home
    engine = home / "Applications/Stenny.app/Contents/Resources/engine/bin"
    engine.mkdir(parents=True)
    (engine / "python3").write_text("#!/bin/sh\n", encoding="utf-8")   # no stenny-engine.json: an older app
    assert candidates(env) == []


@posix
def test_runs_the_apps_engine_as_the_app_does(home):
    """`python -m stenny mcp` from the app's engine answers Claude: no PYTHONHOME, no bytecode written."""
    home, env = home
    log = home / "log"
    mac_app_engine(home, log)
    env["PYTHONHOME"] = "/somewhere/else"
    replies = talk(env, INIT, INITIALIZED, TOOLS)
    tools = {t["name"] for t in next(r for r in replies if r.get("id") == 2)["result"]["tools"]}
    assert "list_courses" in tools and "stenny_setup_problem" not in tools
    # started once, at once: no Python start of its own before it (Claude's Cowork and Code sessions don't wait)
    assert log.read_text(encoding="utf-8").splitlines() == ["1 none -m stenny mcp"]


@posix
def test_initialize_behind_a_2026_request_through_the_extension(home):
    """What Cowork and Code sessions send while the connector starts: a 2026-07-28 request, then `initialize`
    behind it. The `initialize` starts the connection."""
    from test_connector import probe_then_initialize

    home, env = home
    mac_app_engine(home, home / "log")
    replies = probe_then_initialize([NODE, str(SCRIPT)], env)
    assert "error" in replies[0]
    assert replies[1]["result"]["serverInfo"]["name"] == "Stenny", replies[1]


def test_without_stenny_claude_is_told_what_to_do(home):
    _, env = home
    replies = talk(env, INIT, TOOLS)
    assert [t["name"] for t in replies[1]["result"]["tools"]] == ["stenny_setup_problem"]
    assert "isn't installed" in replies[0]["result"]["instructions"]


@windows
def test_windows_finds_the_engine_next_to_stenny_exe(home):
    home, env = home
    engine = Path(env["LOCALAPPDATA"]) / "Stenny/engine"
    engine.mkdir(parents=True)
    (engine / "stenny-engine.json").write_text("{}", encoding="utf-8")
    (engine / "python.exe").write_bytes(b"")
    assert candidates(env)[0] == {"program": str(engine / "python.exe"), "args": ["-m", "stenny"], "app": True}
