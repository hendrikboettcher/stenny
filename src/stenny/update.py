"""`stenny update`: is there a newer Stenny, and how to get it.

The engine is inside the app (#92), so the app is what gets updated: Check for Updates… in Stenny (Tauri's updater,
desktop/src-tauri/src/updater.rs), or the install line again. `stenny update` only checks and says so; the folder
swap it did for the engine installed with `uv tool install -e` (until 0.5.13) went with that engine (old_engine.py).
`check()` is also the app's fallback while a release has no latest.json (menubar::check_updates).
"""

import json
import subprocess
import sys
from pathlib import Path

from . import paths
from .version import version

REPO = "hendrikboettcher/stenny"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
ZIP_URL = f"https://github.com/{REPO}/releases/latest/download/Stenny.zip"
RELEASES = f"https://github.com/{REPO}/releases/latest"
CHECKOUT = Path(__file__).resolve().parents[2]  # a development checkout, if this engine runs from one


class UpdateError(Exception):
    pass


def current_version() -> str:
    return version()


def parse(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.lstrip("v").split("."))


def curl() -> str:
    """macOS's curl, or Windows' own curl.exe (in System32 since Windows 10 1803)."""
    return "curl.exe" if sys.platform == "win32" else "/usr/bin/curl"


def _curl(*args: str) -> subprocess.CompletedProcess:
    # curl, not urllib: uv's Python doesn't reliably find macOS's certificates
    try:
        p = subprocess.run([curl(), "-fsSL", "--max-time", "600", *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except OSError as e:
        raise UpdateError(f"Couldn't reach GitHub: {e}") from e
    if p.returncode != 0:
        raise UpdateError(f"Couldn't reach GitHub: {p.stderr.strip()}")
    return p


def older(engine: str, app: str | None) -> str:
    """What Stenny is at: the older of the engine and the app. They are updated separately (git pull doesn't rebuild
    the app), and an update replaces both, so one of them being behind is reason enough to offer it."""
    try:
        return min(engine, app, key=parse) if app else engine
    except ValueError:  # not a version (a dev build's own string): the engine's is the one to go by
        return engine


def check(app_version: str | None = None) -> dict:
    """{"current", "latest", "update_available"}; "current" is the older of the engine and the app (the app passes
    its own version), shown as it is: users don't need to know Stenny has two parts."""
    latest = json.loads(_curl(LATEST_API).stdout)["tag_name"].lstrip("v")
    current = older(current_version(), app_version)
    return {"current": current, "latest": latest, "update_available": parse(latest) > parse(current)}


def install_line() -> str:
    """The line that installs (or updates) Stenny, for this system (README: Install)."""
    if sys.platform == "win32":
        return f"irm {RELEASES}/download/install.ps1 | iex"
    return f"curl -fsSL {RELEASES}/download/install.sh | zsh"


def how() -> str:
    """How to update this Stenny: in the app, or for a development checkout with git."""
    if not paths.in_app() and (CHECKOUT / ".git").exists():
        return f"{CHECKOUT} is a git checkout: use `git pull` there."
    return f"In Stenny: Check for Updates…. Or run the install line again: {install_line()}"
