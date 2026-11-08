"""Where Stenny keeps its own files. This module is the only code that knows these places; everything else (the
app, stenny-agent, the connector) asks the engine (`stenny where --json`, `app-settings`).

Two places:
- **The Stenny folder** (`folder` in config.toml, default ~/Documents/Stenny): everything you or Claude read.
  Courses, Talks/, _Glossaries/, _Course Files/, _Topics/ (see vault.py). Not here.
- **Stenny's own files**, in the system's app-data places, out of sight and never synced:

                    macOS                                     Windows
    config          ~/Library/Application Support/Stenny/     %APPDATA%\\Stenny\\        config.toml, consent.json
    data            ~/Library/Application Support/Stenny/     %LOCALAPPDATA%\\Stenny\\   Recordings/<Course>/ (audio,
                                                                                       segments.json, sessions, raw
                                                                                       output), Queue/, live.sock
    logs            ~/Library/Logs/Stenny/                    %LOCALAPPDATA%\\Stenny\\Logs\\
    cache           ~/Library/Caches/Stenny/                  %LOCALAPPDATA%\\Stenny\\Cache\\   Shared/ (copies to send)
    Whisper models  ~/.cache/huggingface (mlx's own)          %LOCALAPPDATA%\\Stenny\\Models\\

`data_dir` in config.toml moves the recordings elsewhere (an external disk). $STENNY_HOME puts all of Stenny's
own files into one folder (tests, development: config.toml and the data at its top, Logs/ and Cache/ in it), and
$STENNY_CONFIG names another config file.

Older layouts (`~/Lectures Data/`, and before it `~/.config/lecture/` …) are moved here by `stenny migrate`;
until then the config is still found at its old place.
"""

import os
import sys
from pathlib import Path

APP = "Stenny"
RECORDINGS = "Recordings"  # per-course data, inside the data place (or `data_dir`)


def _windows() -> bool:
    return sys.platform == "win32"


def _mac() -> bool:
    return sys.platform == "darwin"


def _override() -> Path | None:
    home = os.environ.get("STENNY_HOME")
    return Path(home).expanduser() if home else None


def _env_dir(name: str, fallback: str) -> Path:
    """%APPDATA% or %LOCALAPPDATA% (always set on Windows; the fallback is for odd environments)."""
    return Path(os.environ.get(name) or Path.home() / fallback)


def config_dir() -> Path:
    """config.toml and consent.json: small, worth roaming with the Windows profile."""
    if home := _override():
        return home
    if _mac():
        return Path.home() / "Library/Application Support" / APP
    if _windows():
        return _env_dir("APPDATA", "AppData/Roaming") / APP
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP


def data_dir() -> Path:
    """Recordings/ (unless `data_dir` in the config says otherwise), Queue/, the live session's socket. Large, so
    Windows' Local folder, which doesn't roam (and which File History doesn't back up)."""
    if home := _override():
        return home
    if _mac():
        return Path.home() / "Library/Application Support" / APP
    if _windows():
        return _env_dir("LOCALAPPDATA", "AppData/Local") / APP
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / APP


def logs_dir() -> Path:
    """stenny.log (the app), live.log (the running recording), queue.log. On a Mac Console.app shows them."""
    if home := _override():
        return home / "Logs"
    if _mac():
        return Path.home() / "Library/Logs" / APP
    if _windows():
        return _env_dir("LOCALAPPDATA", "AppData/Local") / APP / "Logs"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / APP / "Logs"


def cache_dir() -> Path:
    """What can be made again: the clean copies to send (Shared/)."""
    if home := _override():
        return home / "Cache"
    if _mac():
        return Path.home() / "Library/Caches" / APP
    if _windows():
        return _env_dir("LOCALAPPDATA", "AppData/Local") / APP / "Cache"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / APP


def models_dir() -> Path | None:
    """Where the Whisper engine keeps its model. None on a Mac: mlx-whisper uses the Hugging Face cache
    (~/.cache/huggingface), as it always did. On Windows faster-whisper's model goes here."""
    if _windows():
        return (_override() or _env_dir("LOCALAPPDATA", "AppData/Local") / APP) / "Models"
    return None


def documents_dir() -> Path:
    """The user's Documents folder. On Windows the known folder, which may be moved (into OneDrive, say)."""
    if _windows():
        try:
            return Path(_windows_documents())
        except (OSError, AttributeError, ImportError):
            pass
    return Path.home() / "Documents"


def _windows_documents() -> str:
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
    folder = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
    out = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(out)):
        raise OSError("SHGetKnownFolderPath failed")
    try:
        return out.value
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def default_folder() -> Path:
    """The Stenny folder setup and the app suggest: Documents/Stenny."""
    return documents_dir() / APP


def tilde(path: Path | str) -> str:
    """A path as config.toml and messages show it: "~/…" inside the home folder (forward slashes on Windows too)."""
    path = Path(path)
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return path.as_posix()


CONFIG_DIR = config_dir()
DATA_DIR = data_dir()
LOGS_DIR = logs_dir()
CACHE_DIR = cache_dir()
CONFIG_PATH = CONFIG_DIR / "config.toml"
CONSENT_PATH = CONFIG_DIR / "consent.json"
QUEUE_DIR = DATA_DIR / "Queue"
SOCKET = DATA_DIR / "live.sock"
SHARED_DIR = CACHE_DIR / "Shared"

# the layout before (`stenny migrate` moves it, see migrate.py). With $STENNY_HOME (a test copy) an older layout is
# looked for only inside it, so trying the migration never touches the real one.
_OLD = _override() or Path.home()
LEGACY_HOME = _OLD / "Lectures Data"
LEGACY_SYSTEM_DIR = LEGACY_HOME / "_System"
# and the one before that
LEGACY_CONFIG_DIR = _OLD / ".config/lecture"
LEGACY_STATE_DIR = (_OLD / ".local/share" if _override() else
                    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))) / "lecture"
LEGACY_AUDIO_DIR = _OLD / "Lectures Audio"


def places() -> dict[str, str | None]:
    """Stenny's own places, for `stenny where --json` and the app."""
    models = models_dir()
    return {"config": str(CONFIG_PATH), "data": str(DATA_DIR), "queue": str(QUEUE_DIR), "logs": str(LOGS_DIR),
            "cache": str(CACHE_DIR), "shared": str(SHARED_DIR), "socket": str(SOCKET),
            "models": str(models) if models else None}


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
