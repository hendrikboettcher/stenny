"""What Settings checks and sets up for Claude (macOS and Windows):

- the AI step: whether the chosen option works right now (`ai_status`): Claude Code installed and signed in, an
  Anthropic API key that Anthropic accepts, or Off. Nothing here spends tokens: Claude Code is asked with
  `claude auth status`, a key is checked by listing the models (free).
- the API key: saved to the system keychain once Anthropic accepts it (keychain.py), never to config.toml.
- the connector: whether Claude Desktop has the Stenny extension, opening Stenny.mcpb with Claude (which shows
  its own Install dialog), and adding it to Claude Code (`claude mcp add`).

The app shows the states; the words for them are the app's (desktop/src/lib/locales)."""

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
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
            p = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=20, cwd=cwd,
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


def claude_sign_in(cfg) -> dict:
    """Opens a terminal window with `claude auth login`: the browser opens, the user signs in, and the window
    says when to come back. Settings checks again when its window gets the focus back."""
    exe = find_claude()
    if not exe:
        raise SetupError(tr("Claude Code isn't installed yet."))
    if sys.platform == "win32":
        subprocess.Popen(["cmd", "/k", exe, "auth", "login"], creationflags=subprocess.CREATE_NEW_CONSOLE,
                         env=claude_env(exe))
        return {"opened": True}
    script = Path(tempfile.gettempdir()) / "Stenny - Claude sign-in.command"
    script.write_text("#!/bin/zsh\nclear\n"
                      f"echo {shlex.quote(tr('Sign in to Claude Code for Stenny. Your browser opens in a moment.'))}\n"
                      f"echo\n{shlex.quote(exe)} auth login\necho\n"
                      f"echo {shlex.quote(tr('Done. You can close this window and go back to Stenny.'))}\n",
                      encoding="utf-8")
    script.chmod(0o755)
    subprocess.run(["open", str(script)], check=False, timeout=10)
    return {"opened": True}


# ---- the connector --------------------------------------------------------------------------------------------

def claude_data_dir() -> Path:
    """Where Claude Desktop keeps its settings and extensions."""
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "Claude"
    return Path.home() / "Library/Application Support/Claude"


def claude_desktop_installed() -> bool:
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        apps = [local / "AnthropicClaude", local / "Programs/Claude"]
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
    """Stenny.mcpb: next to Stenny's code in a download (setup's folder), or built there by connector/build.sh."""
    root = Path(__file__).resolve().parents[2]
    return next((p for p in (root / "Stenny.mcpb", root / "dist/Stenny.mcpb") if p.is_file()), None)


def stenny_command() -> list[str]:
    """How another program starts this Stenny: the `stenny` command we run as, else this Python with -m."""
    argv0 = Path(sys.argv[0])
    if argv0.stem in ("stenny", "lecture") and argv0.exists():
        return [str(argv0.resolve())]
    return [sys.executable, "-m", "stenny"]


def _claude_code_has_it() -> bool:
    """Claude Code's own settings (~/.claude.json) list a user-wide `stenny` MCP server."""
    try:
        data = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return "stenny" in (data.get("mcpServers") or {})


def connector_status(cfg) -> dict:
    ext = installed_extension()
    file = connector_file()
    return {
        "desktop": claude_desktop_installed(),
        "installed": ext is not None,
        "enabled": bool(ext and ext["enabled"]),
        "version": ext and ext["version"],
        "file": str(file) if file else None,
        "code": {"available": find_claude() is not None, "added": _claude_code_has_it()},
    }


def install_connector(cfg) -> dict:
    """Opens Stenny.mcpb with Claude Desktop, which asks the user to Install it."""
    file = connector_file()
    if not file:
        raise SetupError(tr("Stenny.mcpb isn't in Stenny's folder. Download Stenny again, or build it with "
                            "connector/build.sh."))
    if not claude_desktop_installed():
        raise SetupError(tr("The Claude desktop app isn't installed. Get it at claude.ai/download, then try again."))
    if sys.platform == "win32":
        os.startfile(file)  # noqa: S606 — Claude Desktop registers .mcpb
    else:
        p = subprocess.run(["open", "-a", "Claude", str(file)], capture_output=True, text=True, timeout=15)
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
                           capture_output=True, text=True, timeout=30, cwd=cwd, env=claude_env(exe),
                           stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise SetupError(tr("Claude Code couldn't add the connector: {error}",
                            error=(p.stderr or p.stdout).strip()[-300:]))
    return connector_status(cfg)
