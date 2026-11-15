import sys
from pathlib import Path

import pytest

from stenny import paths, update


def test_newer_versions_compare_as_numbers():
    assert update.parse("v0.10.0") > update.parse("0.9.3")
    assert update.parse("0.4.0") == update.parse("v0.4.0")


def test_update_says_how_for_each_system(monkeypatch):
    """The app is what gets updated (Check for Updates…); the install line is the other way, per system."""
    monkeypatch.setattr(update, "CHECKOUT", Path("/nowhere"))
    monkeypatch.setattr(sys, "platform", "win32")
    assert "install.ps1 | iex" in update.how() and update.curl() == "curl.exe"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert "install.sh | zsh" in update.how() and "Check for Updates" in update.how()
    assert update.curl() == "/usr/bin/curl"


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


def test_an_older_app_is_offered_the_update(monkeypatch):
    class Done:
        stdout = '{"tag_name": "v0.5.9"}'
    monkeypatch.setattr(update, "_curl", lambda *a: Done())
    monkeypatch.setattr(update, "current_version", lambda: "0.5.9")
    assert update.check("0.5.1") == {"current": "0.5.1", "latest": "0.5.9", "update_available": True}
    assert update.check("0.5.9")["update_available"] is False
    assert update.check()["current"] == "0.5.9"
