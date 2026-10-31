from pathlib import Path

import pytest

from stenny import paths, update


def test_newer_versions_compare_as_numbers():
    assert update.parse("v0.10.0") > update.parse("0.9.3")
    assert update.parse("0.4.0") == update.parse("v0.4.0")


def test_backup_never_overwrites_an_earlier_one(tmp_path):
    folder = tmp_path / "stenny"
    assert update.backup_path(folder, "0.4.0").name == "stenny (before 0.4.0)"
    (tmp_path / "stenny (before 0.4.0)").mkdir()
    assert update.backup_path(folder, "0.4.0").name == "stenny (before 0.4.0, 2)"


def test_a_git_checkout_is_left_alone(tmp_path):
    (tmp_path / ".git").mkdir()
    with pytest.raises(update.UpdateError, match="git checkout"):
        update.update(tmp_path)


def test_failed_install_brings_the_old_folder_back(tmp_path, monkeypatch):
    folder = tmp_path / "stenny"
    folder.mkdir()
    (folder / "pyproject.toml").write_text("old")

    def fake_curl(*args):
        new = tmp_path / "zip" / "stenny"
        (new / "app/build.noindex/Stenny.app").mkdir(parents=True)
        (new / "app/install.sh").write_text("")
        (new / "pyproject.toml").write_text("new")
        import shutil
        shutil.make_archive(str(tmp_path / "made"), "zip", tmp_path / "zip")
        shutil.move(str(tmp_path / "made.zip"), args[1])

    attempts = []

    def fake_install(path):
        attempts.append((path / "pyproject.toml").read_text())
        if len(attempts) == 1:
            raise update.UpdateError("no network")

    monkeypatch.setattr(update, "_curl", fake_curl)
    monkeypatch.setattr(update, "install_tool", fake_install)
    monkeypatch.setattr(update, "current_version", lambda: "0.4.0")
    with pytest.raises(update.UpdateError, match="old one is back"):
        update.update(folder)
    assert attempts == ["new", "old"]
    assert (folder / "pyproject.toml").read_text() == "old"
    assert not (tmp_path / "stenny (before 0.4.0)").exists()


def test_bundled_tools_win_over_the_path(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert paths.tool("no-such-tool") == "no-such-tool"
    program = tmp_path / "Applications/Stenny.app/Contents/MacOS/no-such-tool"
    program.parent.mkdir(parents=True)
    program.write_text("")
    program.chmod(0o755)
    assert paths.tool("no-such-tool") == str(program)
