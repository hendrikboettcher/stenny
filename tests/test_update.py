import sys
from pathlib import Path

import pytest

from stenny import paths, update


def test_newer_versions_compare_as_numbers():
    assert update.parse("v0.10.0") > update.parse("0.9.3")
    assert update.parse("0.4.0") == update.parse("v0.4.0")


def test_backup_never_overwrites_an_earlier_one(tmp_path):
    folder = tmp_path / "stenny"
    assert update.backup_path(folder, "0.4.0").name == "stenny (before 0.4.0).noindex"
    (tmp_path / "stenny (before 0.4.0).noindex").mkdir()
    assert update.backup_path(folder, "0.4.0").name == "stenny (before 0.4.0, 2).noindex"
    assert update.backup_path(tmp_path / "stenny.noindex", "0.5.0").name == "stenny (before 0.5.0).noindex"


def test_a_git_checkout_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    (tmp_path / ".git").mkdir()
    with pytest.raises(update.UpdateError, match="git checkout"):
        update.update(tmp_path)


def test_on_windows_updates_come_through_the_installer(tmp_path, monkeypatch):
    """The engine can't replace its own running stenny.exe there; the check still works, with Windows' curl.exe."""
    monkeypatch.setattr(sys, "platform", "win32")
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    with pytest.raises(update.UpdateError, match="installer"):
        update.update(tmp_path)
    assert update.curl() == "curl.exe"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert update.curl() == "/usr/bin/curl"


@pytest.mark.skipif(sys.platform == "win32", reason="the folder swap is macOS's (ditto, app/install.sh); Windows "
                                                    "updates through its installer, see the test above")
def test_failed_install_brings_the_old_folder_back(tmp_path, monkeypatch):
    folder = tmp_path / "stenny"
    folder.mkdir()
    (folder / "pyproject.toml").write_text("old", encoding="utf-8")

    def fake_curl(*args):
        new = tmp_path / "zip" / "stenny.noindex"
        (new / "app/build.noindex/Stenny.app").mkdir(parents=True)
        (new / "app/install.sh").write_text("", encoding="utf-8")
        (new / "pyproject.toml").write_text("new", encoding="utf-8")
        import shutil
        shutil.make_archive(str(tmp_path / "made"), "zip", tmp_path / "zip")
        shutil.move(str(tmp_path / "made.zip"), args[1])

    attempts = []

    def fake_install(path):
        attempts.append((path / "pyproject.toml").read_text(encoding="utf-8"))
        if len(attempts) == 1:
            raise update.UpdateError("no network")

    monkeypatch.setattr(update, "_curl", fake_curl)
    monkeypatch.setattr(update, "install_tool", fake_install)
    monkeypatch.setattr(update, "current_version", lambda: "0.4.0")
    with pytest.raises(update.UpdateError, match="old one is back"):
        update.update(folder)
    assert attempts == ["new", "old"]
    assert (folder / "pyproject.toml").read_text(encoding="utf-8") == "old"
    assert not (tmp_path / "stenny (before 0.4.0).noindex").exists()


def test_bundled_tools_win_over_the_path(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "_windows", lambda: False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert paths.tool("no-such-tool") == "no-such-tool"
    program = tmp_path / "Applications/Stenny.app/Contents/MacOS/no-such-tool"
    program.parent.mkdir(parents=True)
    program.write_text("", encoding="utf-8")
    program.chmod(0o755)
    assert paths.tool("no-such-tool") == str(program)


def test_on_windows_the_tools_are_next_to_stenny_exe(tmp_path, monkeypatch):
    """The setup .exe installs into %LOCALAPPDATA%\\Stenny for one user, the .msi into %ProgramFiles%\\Stenny."""
    monkeypatch.setattr(paths, "_windows", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    assert paths.tool("ffmpeg") == "ffmpeg"  # not installed: the one on the PATH
    for_everyone = tmp_path / "Program Files/Stenny/ffmpeg.exe"
    for_everyone.parent.mkdir(parents=True)
    for_everyone.write_text("", encoding="utf-8")
    assert paths.tool("ffmpeg") == str(for_everyone)
    own = tmp_path / "Local/Stenny/ffmpeg.exe"
    own.parent.mkdir(parents=True)
    own.write_text("", encoding="utf-8")
    assert paths.tool("ffmpeg") == str(own)  # the user's own install first
