"""`stenny` in the terminal and for Claude Code when the engine is inside the app (#92).

The app's engine has no command of its own on the PATH, so the app (at each start, migrate.on_start) writes a small
launcher into ~/.local/bin (paths.bin_dir()) that runs it: a shell script `stenny` on a Mac, `stenny.cmd` on Windows.
Rewritten at each start, so it follows the app if it moves. Any other `stenny` there is left alone: a developer's
`uv tool install -e .`, or the engine an older setup installed (its own `stenny`/`stenny.exe` keeps working; moving
those installs over is a later step of #92). `stenny uninstall` removes the launcher, and only if it is Stenny's.
"""

import os
import shlex
import sys
from pathlib import Path

from . import paths

MARK = "Stenny's launcher"  # how a launcher is told from someone else's `stenny`


def _windows() -> bool:
    return sys.platform == "win32"


def path() -> Path:
    return paths.bin_dir() / ("stenny.cmd" if _windows() else "stenny")


def text(python: str) -> str:
    """The launcher for the app's Python at `python`. Bytecode is never written: the app's files carry their own
    (bundle-engine.sh), and writing next to them would break the app's signature."""
    if _windows():
        return ("@echo off\r\n"
                f"rem {MARK}: runs the engine inside the Stenny app. Stenny rewrites this file at each start;\r\n"
                "rem delete it and Stenny writes it again, uninstall Stenny to remove it for good.\r\n"
                "setlocal\r\n"
                "set PYTHONDONTWRITEBYTECODE=1\r\n"
                "set PYTHONUTF8=1\r\n"
                f'"{python}" -m stenny %*\r\n')
    return ("#!/bin/sh\n"
            f"# {MARK}: runs the engine inside the Stenny app. Stenny rewrites this file at each start;\n"
            "# delete it and Stenny writes it again, uninstall Stenny to remove it for good.\n"
            f"PYTHONDONTWRITEBYTECODE=1 exec {shlex.quote(python)} -m stenny \"$@\"\n")


def ours(file: Path) -> bool:
    try:
        return file.is_file() and not file.is_symlink() and MARK in file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def taken() -> Path | None:
    """Another `stenny` in the launcher's folder (not Stenny's launcher), which wins or would be overwritten."""
    folder = paths.bin_dir()
    names = ("stenny.exe", "stenny.cmd", "stenny.bat") if _windows() else ("stenny",)
    for name in names:
        other = folder / name
        if (other.exists() or other.is_symlink()) and not ours(other):
            return other
    return None


def install(log=lambda message: print(message, file=sys.stderr)) -> Path | None:
    """Writes or refreshes the launcher when this engine is the app's and no other `stenny` is there. Returns the
    launcher, or None when there is none to write (not the app's engine, or another `stenny` stays)."""
    command = paths.engine_command()
    if command is None:
        return None
    if taken():
        return None  # quietly: an older setup's engine or a developer's, both still work
    target = path()
    content = text(command[0])
    try:
        if target.is_file() and target.read_bytes() == content.encode("utf-8"):
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(content, encoding="utf-8", newline="")
        if not _windows():
            os.chmod(tmp, 0o755)
        os.replace(tmp, target)
        log(f"launcher: {paths.tilde(target)} runs the engine inside the app")
        return target
    except OSError as e:
        log(f"launcher: couldn't write {target}: {e}")
        return None


def remove() -> bool:
    """`stenny uninstall`: the launcher goes, never someone else's `stenny`."""
    target = path()
    if not ours(target):
        return False
    target.unlink()
    return True
