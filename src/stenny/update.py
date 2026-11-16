"""`stenny update`: is there a newer Stenny, and how to get it.

The engine is inside the app (#92), so the app is what gets updated: Check for Updates… in Stenny (Tauri's updater,
desktop/src-tauri/src/updater.rs), or the install line again. `stenny update` only checks and says so; the folder
swap it did for the engine installed with `uv tool install -e` (until 0.5.13) went with that engine (old_engine.py).
`check()` is also the app's fallback when Tauri's updater can't check (menubar::check_updates).

**Invite-only (closed alpha).** Stenny's repo and releases are private; stenny.eu hands them out to invited users
(the stenny-website repo: api/install.js, api/update.js, api/download.js). An invite is a personal code in the
install line (`curl -fsSL https://stenny.eu/i/<code> | zsh`); the install scripts save it here (`stenny invite`,
in the system keychain like every secret, keychain.py), and every update check sends it along in the
X-Stenny-Invite header. A revoked invite gets 403: Stenny keeps working, but gets no more updates.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import keychain, paths
from .i18n import tr
from .version import version

SITE = os.environ.get("STENNY_SITE", "https://stenny.eu").rstrip("/")  # a preview deployment, for testing
UPDATE_API = f"{SITE}/api/update"
HEADER = "X-Stenny-Invite"
INVITE_ACCOUNT = "invite"
CODE = re.compile(r"\d{1,7}-[a-z2-7]{16}")  # the website's lib/invites.js makes them
CHECKOUT = Path(__file__).resolve().parents[2]  # a development checkout, if this engine runs from one


class UpdateError(Exception):
    pass


class InviteError(UpdateError):
    """No invite saved, or stenny.eu no longer accepts it."""


def current_version() -> str:
    return version()


def parse(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.lstrip("v").split("."))


def curl() -> str:
    """macOS's curl, or Windows' own curl.exe (in System32 since Windows 10 1803)."""
    return "curl.exe" if sys.platform == "win32" else "/usr/bin/curl"


# ---------------------------------------------------------------- the invite

def clean_code(text: str) -> str | None:
    """The code as stenny.eu writes it, or None when there is none. Takes a whole install line too."""
    found = re.search(rf"(?<![\w-])({CODE.pattern})(?![\w-])", (text or "").strip().lower())
    return found.group(1) if found else None


def invite() -> str | None:
    """The saved invite: $STENNY_INVITE (the install scripts, testing), else the keychain's."""
    return clean_code(os.environ.get("STENNY_INVITE", "")) or clean_code(keychain.get_secret(INVITE_ACCOUNT) or "")


def save_invite(text: str) -> str:
    code = clean_code(text)
    if not code:
        raise InviteError(tr("That isn't an invite code. It looks like 42-abcdefghjkmnpqrs, at the end of your install line."))
    keychain.save_secret(INVITE_ACCOUNT, code, "Stenny: invite")
    return code


def forget_invite() -> None:
    keychain.delete_secret(INVITE_ACCOUNT)


# ---------------------------------------------------------------- asking stenny.eu

def _ask(url: str, code: str | None) -> tuple[int, str]:
    """(HTTP status, body). The code goes to curl on stdin (`-H @-`), never on its command line."""
    args = [curl(), "-sSL", "--max-time", "60", "-w", "\n%{http_code}"]
    if code:
        args += ["-H", "@-"]
    # curl, not urllib: uv's Python doesn't reliably find macOS's certificates
    try:
        p = subprocess.run([*args, url], input=f"{HEADER}: {code}\n" if code else "", capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        raise UpdateError(tr("Couldn't reach stenny.eu: {error}", error=e)) from e
    if p.returncode != 0:
        raise UpdateError(tr("Couldn't reach stenny.eu: {error}", error=p.stderr.strip()))
    body, _, status = p.stdout.rpartition("\n")
    return int(status or 0), body


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
    current = older(current_version(), app_version)
    code = invite()
    if not code:
        raise InviteError(tr("This Stenny has no invite saved, so it can't look for updates. Run the install line "
                             "from your invite again."))
    status, body = _ask(UPDATE_API, code)
    if status in (401, 403):
        raise InviteError(tr("stenny.eu doesn't accept this Stenny's invite any more, so there are no updates. "
                             "Stenny keeps working as it is."))
    if status == 204:  # the newest release isn't complete yet: nothing to offer for now
        return {"current": current, "latest": current, "update_available": False}
    if status != 200:
        raise UpdateError(tr("Couldn't reach stenny.eu: {error}", error=f"HTTP {status}"))
    try:
        latest = str(json.loads(body)["version"]).lstrip("v")
        newer = parse(latest) > parse(current)
    except (ValueError, KeyError, TypeError) as e:
        raise UpdateError(tr("Couldn't reach stenny.eu: {error}", error=e)) from e
    return {"current": current, "latest": latest, "update_available": newer}


def install_line(code: str | None = None) -> str | None:
    """This invite's line that installs (or updates) Stenny, for this system; None without an invite."""
    code = code or invite()
    if not code:
        return None
    if sys.platform == "win32":
        return f"irm {SITE}/i/{code}/win | iex"
    return f"curl -fsSL {SITE}/i/{code} | zsh"


def how() -> str:
    """How to update this Stenny: in the app, or for a development checkout with git."""
    if not paths.in_app() and (CHECKOUT / ".git").exists():
        return f"{CHECKOUT} is a git checkout: use `git pull` there."
    line = install_line()
    if not line:
        return tr("In Stenny: Check for Updates…. Or run the install line from your invite again.")
    return tr("In Stenny: Check for Updates…. Or run the install line again: {line}", line=line)
