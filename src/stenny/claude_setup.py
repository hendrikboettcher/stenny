"""What Settings checks and sets up for Claude (macOS and Windows):

- the AI step: whether the chosen option works right now (`ai_status`): Claude Code installed and signed in, an
  Anthropic API key that Anthropic accepts, or Off. Nothing here spends tokens: Claude Code is asked with
  `claude auth status`, a key is checked by listing the models (free).
- the API key: saved to the system keychain once Anthropic accepts it (keychain.py), never to config.toml.
- the connector: whether Claude Desktop has it, and adding it there. On a Mac Stenny.mcpb is opened with Claude
  (which shows its own Install dialog; Claude removes its extensions itself). On Windows nothing opens a .mcpb
  reliably (no file type registered for it, and Claude's own Install Extension fails on its app package), so Stenny
  writes itself into Claude's claude_desktop_config.json (`mcpServers.stenny`), Claude's documented way to add a
  local MCP server, and takes itself out of it again; Claude reads it when it starts. Adding it to Claude Code
  (`claude mcp add`) and removing it from Claude Code again.

The app shows the states; the words for them are the app's (desktop/src/lib/locales)."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import keychain
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


def _claude_packages() -> list[Path]:
    """Windows: Claude Desktop installed as an app package (MSIX), e.g. %LOCALAPPDATA%\\Packages\\Claude_<id>."""
    return sorted((_local_appdata() / "Packages").glob("Claude_*"))


def claude_data_dir() -> Path:
    """Where Claude Desktop keeps its settings and extensions. On Windows %APPDATA%\\Claude; the app package
    (MSIX) keeps it in its own folder instead (…\\Packages\\Claude_<id>\\LocalCache\\Roaming\\Claude)."""
    if sys.platform == "win32":
        roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "Claude"
        packaged = [p / "LocalCache/Roaming/Claude" for p in _claude_packages()]
        return roaming if roaming.is_dir() else next((p for p in packaged if p.is_dir()), roaming)
    return Path.home() / "Library/Application Support/Claude"


def claude_desktop_exe() -> Path | None:
    """Windows: the Claude app's own launcher (the installer from claude.ai/download puts it in %LOCALAPPDATA%)."""
    local = _local_appdata()
    return next((p for p in (local / "AnthropicClaude/claude.exe", local / "Programs/Claude/Claude.exe")
                 if p.is_file()), None)


def claude_desktop_installed() -> bool:
    if sys.platform == "win32":
        apps = [_local_appdata() / "AnthropicClaude", _local_appdata() / "Programs/Claude", *_claude_packages()]
    else:
        apps = [Path("/Applications/Claude.app"), Path.home() / "Applications/Claude.app"]
    return any(a.exists() for a in apps) or claude_data_dir().is_dir()


def installed_extension(data: Path | None = None) -> dict | None:
    """The Stenny extension Claude Desktop has installed: {"id", "version", "enabled"}, else None."""
    data = data or claude_data_dir()
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
    """Stenny.mcpb: the one bundled with the app ($STENNY_CONNECTOR, set by desktop/src-tauri/src/engine.rs), else
    next to Stenny's code in a download (setup's folder), or built there by connector/build.sh."""
    root = Path(__file__).resolve().parents[2]
    bundled = [Path(os.environ["STENNY_CONNECTOR"])] if os.environ.get("STENNY_CONNECTOR") else []
    return next((p for p in (*bundled, root / "Stenny.mcpb", root / "dist/Stenny.mcpb") if p.is_file()), None)


def stenny_command() -> list[str]:
    """How another program starts this Stenny: the `stenny` command we run as (on Windows stenny.exe, which
    sys.argv[0] may name without its .exe), else this Python with -m."""
    argv0 = Path(sys.argv[0])
    if argv0.stem in ("stenny", "lecture"):
        for exe in (argv0, argv0.with_suffix(".exe")):
            if exe.is_file():
                return [str(exe.resolve())]
    return [sys.executable, "-m", "stenny"]


# ---- Claude Desktop's own settings file (Windows' way in) ---------------------------------------------------------

DESKTOP_CONFIG = "claude_desktop_config.json"


def _claude_config_dirs() -> list[Path]:
    """Every folder a Claude desktop app reads claude_desktop_config.json from: on Windows %APPDATA%\\Claude and the
    app package's own (both, if both are there: which one the installed Claude reads can't be told from outside)."""
    if sys.platform == "win32":
        roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "Claude"
        found = [d for d in (roaming, *(p / "LocalCache/Roaming/Claude" for p in _claude_packages())) if d.is_dir()]
        return found or [claude_data_dir()]
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


def add_to_desktop_config(cfg) -> dict:
    """Stenny in Claude Desktop's mcpServers, everything else in the file as it was. Claude reads it when it starts."""
    files = [folder / DESKTOP_CONFIG for folder in _claude_config_dirs()]
    datas = [_read_desktop_config(f) for f in files]   # all read first: one bad file changes none of them
    entry = desktop_config_entry()
    for file, data in zip(files, datas):
        data.setdefault("mcpServers", {})["stenny"] = entry
        try:
            _write_desktop_config(file, data)
        except OSError as e:
            raise SetupError(tr("Stenny couldn't be added to Claude's settings: {error}", error=e)) from None
    return connector_status(cfg)


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


def connector_status(cfg) -> dict:
    """`via`: how Claude Desktop has it, "extension" (Stenny.mcpb) or "config" (claude_desktop_config.json);
    `direct`: Stenny adds it by itself (Windows), so it needs no Stenny.mcpb."""
    ext = installed_extension()
    configured = ext is None and in_desktop_config()
    file = connector_file()
    return {
        "desktop": claude_desktop_installed(),
        "installed": ext is not None or configured,
        "via": "extension" if ext else "config" if configured else None,
        "direct": sys.platform == "win32",
        "enabled": bool(ext and ext["enabled"]) or configured,
        "version": ext and ext["version"],
        "file": str(file) if file else None,
        "code": {"available": find_claude() is not None, "added": _claude_code_has_it()},
    }


def install_connector(cfg) -> dict:
    """Mac: opens Stenny.mcpb with Claude Desktop, which asks the user to Install it ({"opened": True}). Windows:
    Stenny adds itself to Claude's settings file (the connector's status, `via` "config")."""
    if sys.platform == "win32":
        if not claude_desktop_installed():
            raise SetupError(tr("The Claude desktop app isn't installed. Get it at claude.ai/download, then try again."))
        return add_to_desktop_config(cfg)
    file = connector_file()
    if not file:
        raise SetupError(tr("Stenny.mcpb isn't in Stenny's folder. Download Stenny again, or build it with "
                            "connector/build.sh."))
    if not claude_desktop_installed():
        raise SetupError(tr("The Claude desktop app isn't installed. Get it at claude.ai/download, then try again."))
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
