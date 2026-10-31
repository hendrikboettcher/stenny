"""Where the tool keeps everything that isn't for reading. The Obsidian vault gets only what you or Claude
read (transcripts, summaries, glossaries, course files, topic notes). Everything else lives in ONE folder,
`~/Lectures Data/` (or $STENNY_HOME), with the same folder rule as the vault:

    ~/Lectures Data/
    ├── <Course>/   audio (.opus), segments.json, session files, raw Whisper output (… (raw).md)
    ├── _Queue/     final passes waiting (e.g. on battery)
    └── _System/    config.toml, logs, the live session's control socket

The locations before (`~/Lectures Audio/`, `~/.local/share/lecture/`, `~/.config/lecture/`) are moved here
once by `stenny migrate`; until then the config is still found at its old place.
"""

import os
from pathlib import Path

HOME = Path(os.environ.get("STENNY_HOME") or Path.home() / "Lectures Data").expanduser()
SYSTEM_DIR = HOME / "_System"
QUEUE_DIR = HOME / "_Queue"
CONFIG_PATH = SYSTEM_DIR / "config.toml"
SOCKET = SYSTEM_DIR / "live.sock"

# before `stenny migrate`
LEGACY_CONFIG_DIR = Path.home() / ".config/lecture"
LEGACY_STATE_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "lecture"
LEGACY_AUDIO_DIR = Path.home() / "Lectures Audio"


def bundled(name: str) -> str | None:
    """A helper program inside the installed Stenny.app (your own copy first, on a shared Mac)."""
    for apps in (Path.home() / "Applications", Path("/Applications")):
        program = apps / "Stenny.app/Contents/MacOS" / name
        if os.access(program, os.X_OK):
            return str(program)
    return None


def tool(name: str) -> str:
    """ffmpeg or ffprobe: the one shipped with Stenny.app, else whatever is on the PATH (a developer's Mac)."""
    return bundled(name) or name
