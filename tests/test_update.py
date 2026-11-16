import sys
from pathlib import Path

import pytest

from stenny import paths, update


def test_newer_versions_compare_as_numbers():
    assert update.parse("v0.10.0") > update.parse("0.9.3")
    assert update.parse("0.4.0") == update.parse("v0.4.0")


CODE = "42-abcdefghijk2m3p4"


@pytest.fixture
def saved(monkeypatch):
    """A keychain in a dict: what update.py saves and reads there."""
    secrets = {}
    monkeypatch.delenv("STENNY_INVITE", raising=False)
    monkeypatch.setattr(update.keychain, "get_secret", lambda account: secrets.get(account))
    monkeypatch.setattr(update.keychain, "save_secret", lambda account, secret, label: secrets.__setitem__(account, secret))
    monkeypatch.setattr(update.keychain, "delete_secret", lambda account: secrets.pop(account, None))
    return secrets


def test_update_says_how_for_each_system(monkeypatch, saved):
    """The app is what gets updated (Check for Updates…); the invite's own install line is the other way."""
    monkeypatch.setattr(update, "CHECKOUT", Path("/nowhere"))
    assert "from your invite" in update.how() and update.install_line() is None
    saved["invite"] = CODE
    monkeypatch.setattr(sys, "platform", "win32")
    assert f"irm https://stenny.eu/i/{CODE}/win | iex" in update.how() and update.curl() == "curl.exe"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert f"curl -fsSL https://stenny.eu/i/{CODE} | zsh" in update.how() and "Check for Updates" in update.how()
    assert update.curl() == "/usr/bin/curl"


@pytest.mark.parametrize("text", [CODE, f"  {CODE.upper()}\n", f"curl -fsSL https://stenny.eu/i/{CODE} | zsh",
                                  f"irm https://stenny.eu/i/{CODE}/win | iex"])
def test_an_invite_is_saved_from_its_code_or_its_whole_line(saved, text):
    assert update.save_invite(text) == CODE
    assert saved == {"invite": CODE} and update.invite() == CODE
    update.forget_invite()
    assert update.invite() is None


@pytest.mark.parametrize("text", ["", "42", "42-short", "x42-abcdefghijk2m3p4", "42-abcdefghijk2m3p4x", "42-ABCDEFGHIJK0M1P8"])
def test_what_isnt_an_invite_is_refused(saved, text):
    with pytest.raises(update.InviteError):
        update.save_invite(text)
    assert saved == {}


def test_the_install_scripts_invite_wins(saved, monkeypatch):
    saved["invite"] = "1-aaaaaaaaaaaaaaaa"
    monkeypatch.setenv("STENNY_INVITE", CODE)
    assert update.invite() == CODE


def test_the_check_sends_the_invite_on_stdin(saved, monkeypatch):
    """Never on curl's command line, where any process list shows it."""
    saved["invite"] = CODE
    monkeypatch.setattr(update, "current_version", lambda: "0.5.15")
    calls = []

    class Done:
        returncode = 0
        stdout = '{"version": "0.5.16", "platforms": {}}\n200'
        stderr = ""

    def run(args, **kw):
        calls.append((args, kw.get("input")))
        return Done()
    monkeypatch.setattr(update.subprocess, "run", run)
    assert update.check() == {"current": "0.5.15", "latest": "0.5.16", "update_available": True}
    args, given = calls[0]
    assert CODE not in " ".join(args) and given == f"X-Stenny-Invite: {CODE}\n"
    assert args[-1] == "https://stenny.eu/api/update"


@pytest.mark.parametrize("status", [401, 403])
def test_a_revoked_invite_says_so(saved, monkeypatch, status):
    saved["invite"] = CODE
    monkeypatch.setattr(update, "_ask", lambda url, code: (status, '{"ok": false}'))
    with pytest.raises(update.InviteError, match="invite"):
        update.check()


def test_no_invite_no_check(saved, monkeypatch):
    monkeypatch.setattr(update, "_ask", lambda url, code: pytest.fail("asked without an invite"))
    with pytest.raises(update.InviteError):
        update.check()


def test_a_release_being_built_offers_nothing(saved, monkeypatch):
    saved["invite"] = CODE
    monkeypatch.setattr(update, "current_version", lambda: "0.5.15")
    monkeypatch.setattr(update, "_ask", lambda url, code: (204, ""))
    assert update.check()["update_available"] is False


def test_a_git_checkout_is_told_to_pull(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(update, "CHECKOUT", tmp_path)
    assert "git pull" in update.how()


def test_the_version_is_the_one_in_pyproject_toml():
    """Not the editable install's metadata, which keeps the version it was installed with (a git pull made the update
    check say "You have 0.4.0" on a 0.5.9 engine)."""
    import tomllib

    from stenny.version import PYPROJECT, version
    assert version() == tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]


def test_current_is_the_older_of_engine_and_app():
    """App and engine are updated separately; the dialog says "You have <older>", without naming which part."""
    assert update.older("0.5.9", "0.5.1") == "0.5.1"
    assert update.older("0.5.1", "0.5.9") == "0.5.1"
    assert update.older("0.5.10", "0.5.9") == "0.5.9"
    assert update.older("0.5.9", None) == "0.5.9"      # `stenny update --check`: no app asking
    assert update.older("0.5.9", "dev") == "0.5.9"     # not a version: the engine's counts


def test_an_older_app_is_offered_the_update(monkeypatch, saved):
    saved["invite"] = CODE
    monkeypatch.setattr(update, "_ask", lambda url, code: (200, '{"version": "0.5.9"}'))
    monkeypatch.setattr(update, "current_version", lambda: "0.5.9")
    assert update.check("0.5.1") == {"current": "0.5.1", "latest": "0.5.9", "update_available": True}
    assert update.check("0.5.9")["update_available"] is False
    assert update.check()["current"] == "0.5.9"
