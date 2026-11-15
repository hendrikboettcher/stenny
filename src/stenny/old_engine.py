"""The engine an older setup installed next to the app, removed once the app's own engine takes over (#92).

Until 0.5.13, install.sh / install.ps1 downloaded Stenny's code into a folder of its own (~/Applications/
stenny.noindex on a Mac, %LOCALAPPDATA%\\Programs\\stenny on Windows) and installed the engine from it with
`uv tool install -e`: the `stenny` (Windows stenny.exe) and `lecture` commands in ~/.local/bin. The engine is inside
the app now, so at its start (migrate.on_start) the app's engine removes that one:

1. `uv tool uninstall stenny`: its commands go, and launcher.py can put the app's `stenny` in their place;
2. the queue's background job (launchd agent, Task Scheduler task) and Claude Code's connector are pointed at the
   app's engine, if they used the old one;
3. the folder of code goes to the Trash (Windows: the Recycle Bin), so whoever changed something in it can still
   get it back.

Only what Stenny's own setup installed: an engine installed from anywhere else (a developer's checkout, a git clone)
stays as it is. Never while a recording or a final pass runs (they may run from the old engine); then at the next
start. uv itself stays: Claude Code or the user may use it too.
"""

import json
import os
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path

from . import paths


def folder() -> Path:
    """Where install.sh / install.ps1 kept the code the old engine was installed from."""
    if paths._windows():
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
        return Path(local) / "Programs" / "stenny"
    return Path.home() / "Applications" / "stenny.noindex"


def uv() -> str | None:
    exe = "uv.exe" if paths._windows() else "uv"
    found = shutil.which("uv")
    if found:
        return found
    for candidate in (Path.home() / ".local/bin" / exe, Path("/opt/homebrew/bin/uv")):
        if candidate.is_file():
            return str(candidate)
    return None


def _run(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, stdin=subprocess.DEVNULL)


def installed_from(uv_exe: str) -> Path | None:
    """The folder `uv tool install -e` installed Stenny's engine from, if uv has one (its receipt)."""
    try:
        p = _run(uv_exe, "tool", "dir", timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0 or not p.stdout.strip():
        return None
    receipt = Path(p.stdout.strip()) / "stenny" / "uv-receipt.toml"
    try:
        data = tomllib.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for req in data.get("tool", {}).get("requirements", []):
        if req.get("name") == "stenny" and req.get("editable"):
            return Path(req["editable"])
    return None


def ours(source: Path) -> bool:
    """The old engine came from Stenny's own setup: its folder, and not a git checkout."""
    try:
        same = source.resolve() == folder().resolve()
    except OSError:
        return False
    return same and not (source / ".git").exists()


def busy() -> str | None:
    """A recording or a final pass is running (perhaps from the old engine)."""
    from . import jobs
    from .live.control import is_running
    if is_running():
        return "a recording is running"
    with jobs.QueueLock() as lock:
        if lock is None:
            return "a final pass is running"
    return None


def _claude_code_command() -> str | None:
    """The program Claude Code starts for Stenny's connector (~/.claude.json), if it has one."""
    try:
        data = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = (data.get("mcpServers") or {}).get("stenny") or {}
    return entry.get("command") if isinstance(entry, dict) else None


def _agent_installed() -> bool:
    """The queue's background job is there (Mac: launchd agent; Windows: Task Scheduler task)."""
    from . import jobs
    if paths._windows():
        try:
            return _run("schtasks", "/Query", "/TN", jobs.TASK_NAME, timeout=20).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    return jobs.AGENT_PATH.exists()


def to_trash(path: Path) -> Path | None:
    """`path` into the Trash (Windows: the Recycle Bin); where it went on a Mac, None if it couldn't."""
    if paths._windows():
        script = ("Add-Type -AssemblyName Microsoft.VisualBasic; "
                  "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($env:STENNY_OLD, 'OnlyErrorDialogs', "
                  "'SendToRecycleBin')")
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           env={**os.environ, "STENNY_OLD": str(path)}, capture_output=True, timeout=120)
        return path if p.returncode == 0 and not path.exists() else None
    trash = Path.home() / ".Trash"
    stamp = datetime.now().strftime("%Y-%m-%d %H.%M")
    target = trash / f"stenny (old engine, {stamp}).noindex"
    n = 2
    while target.exists():
        target = trash / f"stenny (old engine, {stamp}, {n}).noindex"
        n += 1
    try:
        trash.mkdir(exist_ok=True)
        path.rename(target)
    except OSError:
        return None
    return target


def retire(log=lambda message: print(message, file=sys.stderr)) -> bool:
    """Removes the old engine when this is the app's engine (see the top). True if it did. Never raises: whatever
    fails is logged and tried again at the next start."""
    if not paths.in_app():
        return False
    uv_exe = uv()
    source = installed_from(uv_exe) if uv_exe else None
    if source is None or not ours(source):
        return False
    if why := busy():
        log(f"old engine: {why}; removing it at the next start")
        return False
    agent, claude = _agent_installed(), _claude_code_command()
    try:
        p = _run(uv_exe, "tool", "uninstall", "stenny", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"old engine: uv couldn't remove it ({e}); trying again at the next start")
        return False
    if p.returncode != 0:
        log(f"old engine: uv couldn't remove it ({(p.stderr or p.stdout).strip()[-300:]}); "
            "trying again at the next start")
        return False
    log(f"old engine: removed the one installed from {paths.tilde(source)}; the app's engine is Stenny's engine now")

    # the launcher first, so whatever ran ~/.local/bin/stenny finds the app's engine there
    from . import launcher
    launcher.install(log=log)
    if agent:
        try:
            from . import jobs
            jobs.install_agent()  # the app's engine runs it from now (paths.engine_command)
            log("old engine: the queue's background job runs the app's engine now")
        except (OSError, subprocess.SubprocessError) as e:
            log(f"old engine: couldn't point the background job at the app's engine: {e}")
    own = paths.engine_command()[0]
    if claude and Path(claude) != Path(own):
        from . import claude_setup
        try:
            claude_setup.remove_from_claude_code(None)
            claude_setup.add_to_claude_code(None)
            log("old engine: Claude Code's connector runs the app's engine now")
        except (claude_setup.SetupError, OSError, subprocess.SubprocessError) as e:
            log(f"old engine: couldn't point Claude Code's connector at the app's engine: {e}")
    if source.exists():
        went = to_trash(source)
        if went is None:
            log(f"old engine: couldn't move {paths.tilde(source)} to the Trash; it can be deleted")
        else:
            log(f"old engine: its folder {paths.tilde(source)} went to the "
                + ("Recycle Bin" if paths._windows() else f"Trash ({went.name})"))
    return True
