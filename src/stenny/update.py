"""`stenny update`: replace this install with the newest release.

A release is a zip of the whole folder with the finished app inside. The old folder is kept next to the new
one, because Stenny is made to be changed: whoever changed its code can carry that over with Claude Code.
"""

import json
import shutil
import subprocess
import tempfile
from importlib.metadata import version as package_version
from pathlib import Path

REPO = "hendrikboettcher/stenny"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
ZIP_URL = f"https://github.com/{REPO}/releases/latest/download/Stenny.zip"
FOLDER = Path(__file__).resolve().parents[2]


class UpdateError(Exception):
    pass


def current_version() -> str:
    return package_version("stenny")


def parse(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.lstrip("v").split("."))


def _curl(*args: str) -> subprocess.CompletedProcess:
    # curl, not urllib: uv's Python doesn't reliably find macOS's certificates
    p = subprocess.run(["/usr/bin/curl", "-fsSL", "--max-time", "600", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise UpdateError(f"Couldn't reach GitHub: {p.stderr.strip()}")
    return p


def check() -> dict:
    latest = json.loads(_curl(LATEST_API).stdout)["tag_name"].lstrip("v")
    current = current_version()
    return {"current": current, "latest": latest, "update_available": parse(latest) > parse(current)}


def uv() -> str:
    found = shutil.which("uv") or next((p for p in (Path.home() / ".local/bin/uv", Path("/opt/homebrew/bin/uv"))
                                        if p.exists()), None)
    if not found:
        raise UpdateError("The `uv` command wasn't found. Ask Claude Code to install it, then try again.")
    return str(found)


def backup_path(folder: Path, version: str) -> Path:
    """A free name next to the folder, e.g. `stenny (before 0.4.1)`. Earlier ones are never overwritten."""
    path = folder.with_name(f"{folder.name} (before {version})")
    n = 2
    while path.exists():
        path = folder.with_name(f"{folder.name} (before {version}, {n})")
        n += 1
    return path


def install_tool(folder: Path) -> None:
    subprocess.run([uv(), "tool", "install", "-e", str(folder), "--force", "--python", "3.12"],
                   check=True, capture_output=True)


def update(folder: Path = FOLDER) -> Path:
    """Installs the newest release into `folder`, reopens the app and returns where the old folder went."""
    if (folder / ".git").exists():
        raise UpdateError(f"{folder} is a git checkout: use `git pull` and `app/build.sh` there.")
    if not (folder / "pyproject.toml").exists():
        raise UpdateError(f"{folder} doesn't look like a Stenny folder.")
    with tempfile.TemporaryDirectory(dir=folder.parent, prefix=".stenny-update-") as tmp:
        archive = Path(tmp) / "Stenny.zip"
        _curl("-o", str(archive), ZIP_URL)
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), tmp], check=True)
        new = Path(tmp) / "stenny"
        if not (new / "app/install.sh").exists() or not (new / "app/build.noindex/Stenny.app").exists():
            raise UpdateError("The download doesn't contain the Stenny app.")
        backup = backup_path(folder, current_version())
        folder.rename(backup)
        new.rename(folder)
        try:
            install_tool(folder)
        except (subprocess.CalledProcessError, UpdateError) as e:
            folder.rename(Path(tmp) / "failed")
            backup.rename(folder)
            install_tool(folder)
            raise UpdateError(f"Installing the new version failed, so the old one is back: {e}") from e
    subprocess.run(["/bin/zsh", str(folder / "app/install.sh")], check=True)
    return backup
