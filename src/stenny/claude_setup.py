"""What Settings checks and sets up for Claude (macOS and Windows):

- the AI step: whether the chosen option works right now (`ai_status`): Claude Code installed and signed in, an
  Anthropic API key that Anthropic accepts, or Off. Nothing here spends tokens: Claude Code is asked with
  `claude auth status`, a key is checked by listing the models (free).
- the API key: saved to the system keychain once Anthropic accepts it (keychain.py), never to config.toml.
- the connector: whether Claude Desktop has it, and adding it there. On a Mac Stenny.mcpb is opened with Claude,
  which shows its own Install dialog (Claude removes its extensions itself). On Windows Claude can't be handed the
  file while it runs: Windows has no "open this file" message for a running app (macOS's open-file), and a second
  start of Claude only brings its window up and drops the file. Only a cold start reads it. So Stenny starts Claude
  with the file when Claude isn't running, and otherwise asks the user to quit Claude first and waits
  (`install_connector` answers "waiting" until Claude is gone; desktop/src/lib/ClaudeConnect.svelte). The Store app
  has no launcher to hand a file to: there the app copies the path and shows how to paste it into Claude's Install
  extension dialog (`connector_status`'s "store"). Stenny
  0.5.7 wrote itself into Claude's claude_desktop_config.json on Windows instead, which Claude rewrites when it
  quits: such an entry is still recognised, and can be taken out. Adding it to Claude Code (`claude mcp add`) and
  removing it from Claude Code again.

Where Claude Desktop keeps its data (settings, extensions, logs) on Windows depends on its version and how it was
installed, so every place is checked (`_claude_windows_dirs`): Claude as an app package (what claude.ai/download
installs, or the Microsoft Store) moves it to %LOCALAPPDATA%\\Claude-Data\\<package name> in newer versions
(6.28899.6 does); an older one keeps the package's private copy of %APPDATA%\\Claude; the installer's Claude uses
%APPDATA%\\Claude itself.

The app shows the states; the words for them are the app's (desktop/src/lib/locales)."""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from . import keychain, paths
from .i18n import tr
from .llm.claude_code import claude_env, find_claude


class SetupError(Exception):
    """A message for the person using the app."""


# ---- the AI step ----------------------------------------------------------------------------------------------

def claude_code_status() -> dict:
    """{"state": "missing" | "signed_out" | "ready" | "unknown", "account": …, "plan": …}."""
    exe = find_claude()
    if not exe:
        return {"state": "missing"}
    try:
        with tempfile.TemporaryDirectory() as cwd:
            p = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, cwd=cwd,
                               env=claude_env(exe), stdin=subprocess.DEVNULL)
        data = json.loads(p.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return {"state": "unknown", "detail": str(e)[:200]}
    if not data.get("loggedIn"):
        return {"state": "signed_out"}
    return {"state": "ready", "account": data.get("email"), "plan": data.get("subscriptionType")}


_checked: dict[str, tuple[float, str]] = {}  # sha256 of a key → (when, "ready" | "bad_key"), for this process
CHECK_FOR = 600


def check_key(key: str) -> tuple[str, str | None]:
    """("ready" | "bad_key" | "unchecked", detail): asks Anthropic for its model list, which costs nothing."""
    digest = hashlib.sha256(key.encode()).hexdigest()
    if (hit := _checked.get(digest)) and time.monotonic() - hit[0] < CHECK_FOR:
        return hit[1], None
    import anthropic
    try:
        anthropic.Anthropic(api_key=key, timeout=15, max_retries=0).models.list(limit=1)
        state, detail = "ready", None
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        state, detail = "bad_key", None
    except anthropic.APIConnectionError:
        return "unchecked", tr("Anthropic couldn't be reached. Check your internet connection.")
    except anthropic.APIError as e:
        return "unchecked", str(e)[:200]
    _checked[digest] = (time.monotonic(), state)
    return state, detail


def api_status() -> dict:
    """{"state": "no_key" | "bad_key" | "ready" | "unchecked", "source": "keychain" | "environment", …}."""
    key = keychain.api_key()
    if not key:
        return {"state": "no_key"}
    state, detail = check_key(key)
    out = {"state": state, "source": "environment" if keychain.from_environment() else "keychain",
           "hint": f"…{key[-4:]}"}
    return out | ({"detail": detail} if detail else {})


def ai_status(cfg) -> dict:
    """The chosen option of the AI step and whether it works: {"backend": …, "state": …, …}."""
    backend = cfg.llm_backend
    if backend == "claude-code":
        return {"backend": backend, **claude_code_status()}
    if backend == "api":
        return {"backend": backend, **api_status()}
    return {"backend": backend, "state": "off"}


def set_api_key(cfg, key: str) -> dict:
    """Saves a key to the keychain once Anthropic accepts it (or can't be reached to say); returns ai_status."""
    key = "".join((key or "").split())
    if not key:
        raise SetupError(tr("Paste your API key first."))
    if not key.startswith("sk-ant-"):
        raise SetupError(tr("That isn't an Anthropic API key: those start with sk-ant-."))
    state, _ = check_key(key)
    if state == "bad_key":
        raise SetupError(tr("Anthropic didn't accept this key. Check that you copied all of it, and that it "
                            "hasn't been deleted in the Claude Console."))
    try:
        keychain.save_key(key)
    except keychain.KeychainError as e:
        raise SetupError(tr("The key couldn't be saved: {error}", error=e)) from None
    return ai_status(cfg)


def remove_api_key(cfg) -> dict:
    try:
        keychain.delete_key()
    except keychain.KeychainError as e:
        raise SetupError(tr("The key couldn't be removed: {error}", error=e)) from None
    return ai_status(cfg)


_sign_in: subprocess.Popen | None = None  # the running `claude auth login`, if any
SIGN_IN_FOR = 15 * 60                      # then it's given up on (the user can click Sign in… again)


def claude_sign_in(cfg) -> dict:
    """Runs `claude auth login` out of sight (no terminal window): it opens the browser, the user signs in and
    clicks Authorize, and the browser hands the login back to it on localhost. Settings checks again when its
    window gets the focus back. A second click starts over; one left unfinished is ended after SIGN_IN_FOR."""
    global _sign_in
    exe = find_claude()
    if not exe:
        raise SetupError(tr("Claude Code isn't installed yet."))
    if _sign_in and _sign_in.poll() is None:
        _sign_in.kill()
    log = Path(tempfile.gettempdir()) / "stenny-claude-sign-in.log"  # what it printed, for troubleshooting
    with open(log, "wb") as out:   # never inherit stdout: the app server's stdout carries its replies
        _sign_in = proc = subprocess.Popen(
            [exe, "auth", "login"], stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
            cwd=tempfile.gettempdir(), env=claude_env(exe),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)

    def end_later():
        try:
            proc.wait(timeout=SIGN_IN_FOR)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    threading.Thread(target=end_later, daemon=True, name="claude-sign-in").start()
    return {"opened": True}


# ---- the connector --------------------------------------------------------------------------------------------

def _local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")


# Windows: the Claude app package's names, before the publisher id: Claude from claude.ai/download
# (Claude_pzs8sxrjxfjjc) and from the Microsoft Store (AnthropicPBC.Claude_fnn82j28hfe8t)
CLAUDE_PACKAGES = ("Claude_*", "AnthropicPBC.Claude_*")
# Windows: a package's name as Claude writes it for its own data folder (lower case, "<name>_<publisher id>")
_PACKAGE_FOLDER = re.compile(r"[a-z0-9.-]+_[a-z0-9]+")


def _claude_packages() -> list[Path]:
    """Windows: Claude Desktop installed as an app package (MSIX), e.g. %LOCALAPPDATA%\\Packages\\Claude_<id>."""
    root = _local_appdata() / "Packages"
    return sorted({p for pattern in CLAUDE_PACKAGES for p in root.glob(pattern)})


def _claude_moved_data() -> list[Path]:
    """Windows: where Claude as an app package keeps its data once it has moved it out of the package (newer
    versions do so at their start; 6.28899.6 does): %LOCALAPPDATA%\\Claude-Data\\<package name, lower case>,
    e.g. claude_pzs8sxrjxfjjc. Only Claude's own folders count, not a copy it is still making ("~copying.<name>")."""
    try:
        entries = sorted((_local_appdata() / "Claude-Data").iterdir())
    except OSError:
        return []
    return [p for p in entries if _PACKAGE_FOLDER.fullmatch(p.name.lower()) and p.is_dir()]


def _claude_windows_dirs() -> list[Path]:
    """Windows: every folder a Claude desktop app may keep its data in, the likeliest first: the folder a newer
    Claude moves its data to (%LOCALAPPDATA%\\Claude-Data\\<package>), the installer's %APPDATA%\\Claude, and
    an older app package's private copy of it (…\\Packages\\Claude_<id>\\LocalCache\\Roaming\\Claude). An
    older Claude keeps using the folders it always had, so none of them is ever dropped."""
    return [*_claude_moved_data(), _roaming_claude(), *(p / "LocalCache/Roaming/Claude" for p in _claude_packages())]


def _roaming_claude() -> Path:
    """Windows: %APPDATA%\\Claude, where the installer's Claude keeps its data."""
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "Claude"


def claude_data_dir() -> Path:
    """Where Claude Desktop keeps its settings and extensions: on Windows the first of `_claude_windows_dirs()`
    that is there (%APPDATA%\\Claude when none is)."""
    if sys.platform == "win32":
        return next((d for d in _claude_windows_dirs() if d.is_dir()), _roaming_claude())
    return Path.home() / "Library/Application Support/Claude"


def claude_desktop_exe() -> Path | None:
    """Windows: the Claude app's own launcher (the installer from claude.ai/download puts it in %LOCALAPPDATA%)."""
    local = _local_appdata()
    return next((p for p in (local / "AnthropicClaude/claude.exe", local / "Programs/Claude/Claude.exe")
                 if p.is_file()), None)


def claude_desktop_store() -> bool:
    """Windows: whether the Claude desktop app is only the Store app (an app package, no launcher of its own)."""
    return sys.platform == "win32" and claude_desktop_exe() is None and bool(_claude_packages())


def claude_desktop_installed() -> bool:
    if sys.platform == "win32":
        apps = [_local_appdata() / "AnthropicClaude", _local_appdata() / "Programs/Claude", *_claude_packages()]
    else:
        apps = [Path("/Applications/Claude.app"), Path.home() / "Applications/Claude.app"]
    return any(a.exists() for a in apps) or claude_data_dir().is_dir()


def _windows_process_paths(image: str) -> list[str]:
    """Windows: the full paths of the running programs called `image` (e.g. claude.exe), through Windows' own calls
    (no PowerShell: the app asks every second or two while it waits). Programs this account may not look into are
    left out."""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):  # PROCESSENTRY32W
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                             ctypes.POINTER(wintypes.DWORD)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = k.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return []
    paths = []
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        more = k.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.szExeFile.lower() == image.lower():
                process = k.OpenProcess(0x1000, False, entry.th32ProcessID)  # PROCESS_QUERY_LIMITED_INFORMATION
                if process:
                    buffer = ctypes.create_unicode_buffer(32768)
                    size = wintypes.DWORD(len(buffer))
                    if k.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                        paths.append(buffer.value)
                    k.CloseHandle(process)
            more = k.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        k.CloseHandle(snapshot)
    return paths


def _is_claude_desktop(path: str) -> bool:
    """Windows: whether a claude.exe is the desktop app's. Claude Code is a claude.exe too (in ~/.local/bin), so only
    the desktop app's own folders count: the installer's (%LOCALAPPDATA%\\AnthropicClaude or …\\Programs\\Claude,
    where a launcher starts app-<version>\\claude.exe) and the app package's (…\\WindowsApps\\Claude_… from
    claude.ai, …\\WindowsApps\\AnthropicPBC.Claude_… from the Microsoft Store)."""
    p = path.lower().replace("/", "\\")
    homes = (str(d).lower().replace("/", "\\").rstrip("\\") + "\\"
             for d in (_local_appdata() / "AnthropicClaude", _local_appdata() / "Programs/Claude"))
    return _package_version(p) is not None or any(p.startswith(h) for h in homes)


def _package_version(path: str) -> str | None:
    """Windows: the version in a path inside the Claude app package (…\\WindowsApps\\Claude_6.28899.6.0_x64__…\\
    app\\claude.exe → "6.28899.6.0"), None for any other path."""
    p = path.lower().replace("/", "\\")
    for name in CLAUDE_PACKAGES:
        marker = "\\windowsapps\\" + name.lower().rstrip("*")
        if marker in p:
            return p.split(marker, 1)[1].split("_")[0]
    return None


def claude_desktop_running() -> bool:
    """Windows: whether the Claude desktop app runs (any of its processes)."""
    return any(_is_claude_desktop(path) for path in _windows_process_paths("claude.exe"))


def _start_claude_with(exe: Path, file: Path) -> None:
    """Windows: Claude started on its own (not as Stenny's child, no console), with the file as its argument, the way
    a double-click on the file would. Its launcher passes the argument on to the app."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen([str(exe), str(file)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,  # noqa: S603
                         stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags, cwd=str(exe.parent))
    except OSError as e:
        raise SetupError(tr("Couldn't open Claude: {error}", error=e)) from None


def installed_extension(data: Path | None = None) -> dict | None:
    """The Stenny extension Claude Desktop has installed: {"id", "version", "enabled"}, else None. Without `data`,
    in every folder a Claude app keeps its data in: on Windows %APPDATA%\\Claude and the app package's own can both
    be there, and the extension may be in the one claude_data_dir() doesn't pick (the setup checklist then never
    ticked "Connect Claude", though Claude had Stenny)."""
    if data is None:
        return next((ext for d in _claude_config_dirs() if (ext := installed_extension(d))), None)
    for folder in sorted((data / "Claude Extensions").glob("*")):
        try:
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if manifest.get("name") != "stenny":
            continue
        try:
            settings = json.loads((data / "Claude Extensions Settings" / f"{folder.name}.json").read_text("utf-8"))
        except (OSError, ValueError):
            settings = {}
        return {"id": folder.name, "version": manifest.get("version"), "enabled": settings.get("isEnabled", True)}
    return None


def connector_file() -> Path | None:
    """Stenny.mcpb: the one bundled with the app ($STENNY_CONNECTOR, set by desktop/src-tauri/src/engine.rs, or the
    app's own when this engine is inside it), else next to Stenny's code in a download (setup's folder), or built
    there by connector/build.sh."""
    root = Path(__file__).resolve().parents[2]
    bundled = [Path(os.environ["STENNY_CONNECTOR"])] if os.environ.get("STENNY_CONNECTOR") else []
    if own := paths.app_file("Stenny.mcpb"):
        bundled.append(own)
    return next((p for p in (*bundled, root / "Stenny.mcpb", root / "dist/Stenny.mcpb") if p.is_file()), None)


def stenny_command() -> list[str]:
    """How another program starts this Stenny: the app's own Python when the engine is inside the app (its path stays
    the same across updates), the `stenny` command we run as (on Windows stenny.exe, which sys.argv[0] may name
    without its .exe), else this Python with -m."""
    if own := paths.engine_command():
        return own
    argv0 = Path(sys.argv[0])
    if argv0.stem in ("stenny", "lecture"):
        for exe in (argv0, argv0.with_suffix(".exe")):
            if exe.is_file():
                return [str(exe.resolve())]
    return [sys.executable, "-m", "stenny"]


# ---- Claude Desktop's own settings file (where Stenny 0.5.7 put itself on Windows) ---------------------------------------------------------

DESKTOP_CONFIG = "claude_desktop_config.json"


def _claude_config_dirs() -> list[Path]:
    """Every folder a Claude desktop app keeps its data in (claude_desktop_config.json, its extensions, its logs): on
    Windows each of `_claude_windows_dirs()` that is there (all of them: which one the installed Claude uses can't be
    told from outside, and an older Claude may have left the others)."""
    if sys.platform == "win32":
        return [d for d in _claude_windows_dirs() if d.is_dir()] or [claude_data_dir()]
    return [claude_data_dir()]


def _read_desktop_config(file: Path) -> dict:
    try:
        text = file.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise SetupError(tr("Claude's settings couldn't be read: {error}", error=e)) from None
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
        # someone's own edits gone wrong: never write over them
        raise SetupError(tr("Claude's settings file isn't valid JSON, so Stenny left it alone. Fix or delete it, "
                            "then try again: {file}", file=file))
    return data


def _write_desktop_config(file: Path, data: dict) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    part = file.with_name(file.name + ".stenny-part")
    part.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(part, file)


def desktop_config_entry() -> dict:
    """Stenny's entry in claude_desktop_config.json's mcpServers."""
    command, *rest = stenny_command()
    return {"command": command, "args": [*rest, "mcp"]}


def in_desktop_config() -> bool:
    for folder in _claude_config_dirs():
        try:
            if "stenny" in (_read_desktop_config(folder / DESKTOP_CONFIG).get("mcpServers") or {}):
                return True
        except SetupError:
            continue
    return False


def remove_from_desktop_config(cfg) -> dict:
    for folder in _claude_config_dirs():
        file = folder / DESKTOP_CONFIG
        data = _read_desktop_config(file)
        if "stenny" not in (data.get("mcpServers") or {}):
            continue
        del data["mcpServers"]["stenny"]
        try:
            _write_desktop_config(file, data)
        except OSError as e:
            raise SetupError(tr("Stenny couldn't be removed from Claude's settings: {error}", error=e)) from None
    return connector_status(cfg)


def _claude_code_has_it() -> bool:
    """Claude Code's own settings (~/.claude.json) list a user-wide `stenny` MCP server."""
    try:
        data = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return "stenny" in (data.get("mcpServers") or {})


CONNECTOR_USED = "connector-used.json"  # in the data place: when Claude last read something through the connector


def mark_connector_used() -> None:
    """`stenny mcp` answered a tool call: Claude really talks to Stenny. Never an error that could stop the answer."""
    try:
        paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
        (paths.DATA_DIR / CONNECTOR_USED).write_text(
            json.dumps({"at": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    except OSError:
        pass


def _connector_used_files() -> list[Path]:
    """Where `stenny mcp` may have written CONNECTOR_USED. Its own data place, and on Windows also each Claude app
    package's private copy of it: Claude from the Microsoft Store runs the connector inside its package, and Windows
    puts a file such a program creates in AppData into the package's own folder (…\Packages\Claude_<id>\LocalCache
    \Local\…), which only programs of the package see there. So the file Claude's connector wrote isn't where the
    app reads it."""
    files = [paths.DATA_DIR / CONNECTOR_USED]
    if sys.platform == "win32":
        roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming")
        for base, folder in ((_local_appdata(), "Local"), (roaming, "Roaming")):
            try:
                inside = paths.DATA_DIR.resolve().relative_to(base.resolve())
            except (OSError, ValueError):
                continue
            files += [package / "LocalCache" / folder / inside / CONNECTOR_USED for package in _claude_packages()]
    return files


def connector_used() -> str | None:
    """When Claude last used the connector (ISO time), None if it never did."""
    times = []
    for file in _connector_used_files():
        try:
            at = json.loads(file.read_text(encoding="utf-8")).get("at")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(at, str) and at:
            times.append(at)
    return max(times, default=None)


def connector_status(cfg) -> dict:
    """`via`: how Claude Desktop has it, "extension" (Stenny.mcpb) or "config" (an entry in
    claude_desktop_config.json, from Stenny 0.5.7 on Windows)."""
    ext = installed_extension()
    configured = ext is None and in_desktop_config()
    file = connector_file()
    return {
        "desktop": claude_desktop_installed(),
        "installed": ext is not None or configured,
        "via": "extension" if ext else "config" if configured else None,
        "enabled": bool(ext and ext["enabled"]) or configured,
        "version": ext and ext["version"],
        "file": str(file) if file else None,
        # Windows: Claude from the Microsoft Store, which can't be started with the file (install_connector's
        # "manual"): the app shows how to paste the path into Claude's Install extension dialog right away
        "store": claude_desktop_store(),
        "code": {"available": find_claude() is not None, "added": _claude_code_has_it()},
        # Claude has read through it: connected, whatever Stenny can or can't see of Claude's own settings
        "used": connector_used(),
    }


def install_connector(cfg) -> dict:
    """Hands Stenny.mcpb to Claude Desktop, which asks the user to Install it. Mac: opened with Claude
    ({"opened": True}). Windows (see the top): Claude started with the file ({"opened": True}) if it isn't running;
    {"waiting": True} while it runs, so the app asks the user to quit Claude and calls again until it's gone;
    {"manual": True} for the Store app, which can't be started with a file (the user pastes the path into Claude's
    Install extension dialog)."""
    file = connector_file()
    if not file:
        raise SetupError(tr("Stenny.mcpb isn't in Stenny's folder. Download Stenny again, or build it with "
                            "connector/build.sh."))
    if not claude_desktop_installed():
        raise SetupError(tr("The Claude desktop app isn't installed. Get it at claude.ai/download, then try again."))
    if sys.platform == "win32":
        exe = claude_desktop_exe()
        if exe is None:
            return {"manual": True}
        if claude_desktop_running():
            return {"waiting": True}
        _start_claude_with(exe, file)
        return {"opened": True}
    p = subprocess.run(["open", "-a", "Claude", str(file)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    if p.returncode != 0:
        raise SetupError(tr("Claude couldn't open the connector: {error}", error=p.stderr.strip()))
    return {"opened": True}


def add_to_claude_code(cfg) -> dict:
    """`claude mcp add --scope user stenny -- <stenny> mcp`: the connector in every Claude Code session too."""
    exe = find_claude()
    if not exe:
        raise SetupError(tr("Claude Code isn't installed yet."))
    if _claude_code_has_it():
        return connector_status(cfg)
    with tempfile.TemporaryDirectory() as cwd:
        p = subprocess.run([exe, "mcp", "add", "--scope", "user", "stenny", "--", *stenny_command(), "mcp"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, cwd=cwd, env=claude_env(exe),
                           stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise SetupError(tr("Claude Code couldn't add the connector: {error}",
                            error=(p.stderr or p.stdout).strip()[-300:]))
    return connector_status(cfg)


def open_claude(cfg) -> dict:
    """Opens the Claude desktop app. Its connectors are removed there (Settings ▸ Customize ▸ Connectors): Claude
    keeps the list of extensions in memory, so Stenny can't take one out from outside while Claude runs."""
    if not claude_desktop_installed():
        raise SetupError(tr("The Claude desktop app isn't installed. Get it at claude.ai/download, then try again."))
    if sys.platform == "win32":
        # its launcher if it's there, else claude://, which the app registers (also the app package's way in)
        try:
            os.startfile(claude_desktop_exe() or "claude://")  # noqa: S606
        except OSError as e:
            raise SetupError(tr("Couldn't open Claude: {error}", error=e)) from None
        return {"opened": True}
    p = subprocess.run(["open", "-a", "Claude"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    if p.returncode != 0:
        raise SetupError(tr("Couldn't open Claude: {error}", error=p.stderr.strip()))
    return {"opened": True}

def claude_desktop_version() -> str:
    """The Claude desktop app's kind and version, for a report: "installer 5.11492.5", "store 5.11492.5", "mac
    5.11492.5"; "" if it can't be told."""
    if sys.platform == "win32":
        # the running app's path holds both: …\AnthropicClaude\app-<version>\claude.exe, …\WindowsApps\Claude_<version>_…
        for path in _windows_process_paths("claude.exe"):
            if not _is_claude_desktop(path):
                continue
            if (version := _package_version(path)) is not None:
                return "store " + version
            parts = [s for s in path.replace("/", "\\").split("\\") if s.lower().startswith("app-")]
            return "installer " + (parts[0][4:] if parts else "?")
        return "store" if _claude_packages() and not claude_desktop_exe() else "installer" if claude_desktop_exe() else ""
    import plistlib
    for app in (Path("/Applications/Claude.app"), Path.home() / "Applications/Claude.app"):
        try:
            with open(app / "Contents/Info.plist", "rb") as f:
                return "mac " + plistlib.load(f).get("CFBundleShortVersionString", "?")
        except (OSError, ValueError):
            continue
    return ""


def _claude_log() -> Path | None:
    """Windows: Claude Desktop's own log (logs\\main.log in its data folder); the one written last when more than
    one of Claude's folders has one (an older Claude's log stays behind after Claude moved its data)."""
    logs = []
    for folder in _claude_config_dirs():
        try:
            logs.append(((folder / "logs/main.log").stat().st_mtime, folder / "logs/main.log"))
        except OSError:
            continue
    return max(logs)[1] if logs else None


def _shown(path: Path) -> str:
    """A path for a report, the home folder (it holds the user's name) as ~."""
    return str(path).replace(str(Path.home()), "~")


def _claude_log_lines(count: int = 15) -> list[str]:
    """The last lines of Claude Desktop's own log about installing extensions ("Handling DXT/MCPB file" …), web
    addresses (they name the account's organization) and the home folder (the user's name) left out."""
    if sys.platform == "win32":
        log = _claude_log()
        if log is None:
            return []
    else:
        log = Path.home() / "Library/Logs/Claude/main.log"
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    home = str(Path.home())
    keep = [re.sub(r"https?://\S+", "<url>", line).replace(home, "~")[:300] for line in lines
            if re.search(r"DXT|MCPB|[Ee]xtension (install|local\.)|can_install|Install(ing|ed) .*extension", line)]
    return keep[-count:]


def connector_report(reason: str) -> str:
    """What a "Connect to Claude didn't work" report says, besides Stenny's own details: the system, the Claude app
    (kind and version), whether it runs, whether Claude has Stenny now, on Windows the folders Stenny looked in for
    Claude's data, and Claude's log lines about extensions."""
    ext = None
    try:
        ext = installed_extension()
    except OSError:
        pass
    running = ""
    if sys.platform == "win32":
        try:
            running = "yes" if claude_desktop_running() else "no"
        except OSError:
            running = "?"
    file = connector_file()
    lines = [
        "Connect to Claude didn't work.",
        f"Reason: {reason or 'no extension after Connect'}",
        f"System: {sys.platform}",
        f"Claude app: {claude_desktop_version() or 'not found'}" + (f", running: {running}" if running else ""),
        f"Claude has Stenny: {ext['version'] if ext else 'no'}",
        *([f"Claude's folders: {', '.join(_shown(d) for d in _claude_config_dirs()) or 'none'}"]
          if sys.platform == "win32" else []),
        f"Stenny.mcpb: {file.stat().st_size if file else 'missing'} bytes",
        "Claude's log (extensions):",
        *(_claude_log_lines() or ["(none)"]),
    ]
    return "\n".join(lines)


def remove_from_claude_code(cfg) -> dict:
    """`claude mcp remove --scope user stenny`: no Claude Code session gets the connector any more."""
    exe = find_claude()
    if not exe:
        raise SetupError(tr("Claude Code isn't installed yet."))
    if not _claude_code_has_it():
        return connector_status(cfg)
    with tempfile.TemporaryDirectory() as cwd:
        p = subprocess.run([exe, "mcp", "remove", "--scope", "user", "stenny"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=30, cwd=cwd, env=claude_env(exe), stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise SetupError(tr("Claude Code couldn't remove the connector: {error}",
                            error=(p.stderr or p.stdout).strip()[-300:]))
    return connector_status(cfg)
