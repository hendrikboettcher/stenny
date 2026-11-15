"""The engine an older setup installed with `uv tool install -e` goes once the app's engine runs (#92,
old_engine.py): only the one Stenny's own setup installed, never a developer's, never during a recording or a final
pass; the background job and Claude Code's connector are pointed at the app's engine, the old folder goes to the
Trash."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from stenny import claude_setup, jobs, launcher, old_engine, paths


class Done:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _setup(tmp_path, monkeypatch, places, *, source: Path | None = None, windows: bool = False):
    """The app's engine (as in test_bundle) and a uv whose receipt says Stenny was installed from `source`
    (default: the folder Stenny's setup used). Returns the list of uv calls."""
    monkeypatch.setattr(paths, "_windows", lambda: windows)
    monkeypatch.setattr(launcher, "_windows", lambda: windows)
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData/Local"))
    app = tmp_path / ("Stenny" if windows else "Applications/Stenny.app/Contents")
    engine = app / ("engine" if windows else "Resources/engine")
    engine.mkdir(parents=True)
    (engine / paths.ENGINE_INFO).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(engine))

    folder = old_engine.folder()
    folder.mkdir(parents=True)
    (folder / "pyproject.toml").write_text("", encoding="utf-8")
    tools = home / ".local/share/uv/tools"
    (tools / "stenny").mkdir(parents=True)
    src = (source or folder).as_posix()
    (tools / "stenny/uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "stenny", editable = "{src}" }}]\n', encoding="utf-8")
    old = home / ".local/bin/stenny"
    old.parent.mkdir(parents=True)
    old.write_text("#!/bin/sh\n# uv's\n", encoding="utf-8")

    calls = []

    def run(args, timeout=60):
        calls.append(args[1:])
        if args[1:] == ("tool", "dir"):
            return Done(str(tools) + "\n")
        if args[1:3] == ("tool", "uninstall"):
            old.unlink()
            return Done()
        if args[0] == "schtasks":
            return Done(returncode=1)
        raise AssertionError(args)

    monkeypatch.setattr(old_engine, "uv", lambda: "/fake/uv")
    monkeypatch.setattr(old_engine, "_run", lambda *a, timeout=60: run(a, timeout))
    monkeypatch.setattr(old_engine, "busy", lambda: None)
    monkeypatch.setattr(old_engine, "to_trash", lambda p: (p.rename(tmp_path / "trashed") or tmp_path / "trashed"))
    return calls


def test_the_old_engine_goes_and_the_launcher_takes_its_place(places, tmp_path, monkeypatch):
    calls = _setup(tmp_path, monkeypatch, places)
    monkeypatch.setattr(jobs, "install_agent", lambda: pytest.fail("no background job was installed"))
    assert old_engine.retire(log=lambda m: None) is True
    assert ("tool", "uninstall", "stenny") in calls
    assert launcher.ours(Path.home() / ".local/bin/stenny")  # the app's `stenny` now
    assert not old_engine.folder().exists() and (tmp_path / "trashed/pyproject.toml").exists()
    # done: the next start finds nothing to remove
    (Path.home() / ".local/share/uv/tools/stenny/uv-receipt.toml").unlink()
    assert old_engine.retire(log=lambda m: None) is False


def test_a_developers_engine_stays(places, tmp_path, monkeypatch):
    checkout = tmp_path / "code/stenny"
    (checkout / ".git").mkdir(parents=True)
    calls = _setup(tmp_path, monkeypatch, places, source=checkout)
    assert old_engine.retire(log=lambda m: None) is False
    assert not any(c[:2] == ("tool", "uninstall") for c in calls)
    assert old_engine.folder().exists()


def test_a_git_checkout_in_the_setups_folder_stays(places, tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, places)
    (old_engine.folder() / ".git").mkdir()
    assert old_engine.retire(log=lambda m: None) is False


def test_not_during_a_recording_or_a_final_pass(places, tmp_path, monkeypatch):
    calls = _setup(tmp_path, monkeypatch, places)
    monkeypatch.setattr(old_engine, "busy", lambda: "a recording is running")
    said = []
    assert old_engine.retire(log=said.append) is False
    assert not any(c[:2] == ("tool", "uninstall") for c in calls)
    assert "next start" in said[0]


def test_a_final_pass_holding_the_queue_counts_as_busy(places, monkeypatch):
    monkeypatch.setattr("stenny.live.control.is_running", lambda *a: False)
    assert old_engine.busy() is None
    with jobs.QueueLock():
        if sys.platform == "win32":
            pytest.skip("msvcrt's lock is per process: the same process may take it twice")
        assert old_engine.busy() == "a final pass is running"


def test_only_the_apps_engine_removes_it(places, tmp_path, monkeypatch):
    calls = _setup(tmp_path, monkeypatch, places)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "elsewhere"))
    assert old_engine.retire(log=lambda m: None) is False
    assert calls == []


def test_if_uv_refuses_everything_stays(places, tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, places)
    monkeypatch.setattr(old_engine, "_run", lambda *a, timeout=60: (
        Done(str(Path.home() / ".local/share/uv/tools") + "\n") if a[1:] == ("tool", "dir")
        else Done(returncode=2, stderr="stenny.exe is in use")))
    said = []
    assert old_engine.retire(log=said.append) is False
    assert old_engine.folder().exists() and "in use" in said[0]


def test_the_background_job_and_claude_code_follow(places, tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, places)
    jobs.AGENT_PATH.parent.mkdir(parents=True)
    jobs.AGENT_PATH.write_text("", encoding="utf-8")
    (Path.home() / ".claude.json").write_text(json.dumps(
        {"mcpServers": {"stenny": {"command": str(Path.home() / ".local/bin/stenny"), "args": ["mcp"]}}}),
        encoding="utf-8")
    done = []
    monkeypatch.setattr(jobs, "install_agent", lambda: done.append("agent"))
    monkeypatch.setattr(claude_setup, "remove_from_claude_code", lambda cfg: done.append("remove"))
    monkeypatch.setattr(claude_setup, "add_to_claude_code", lambda cfg: done.append("add"))
    assert old_engine.retire(log=lambda m: None) is True
    assert done == ["agent", "remove", "add"]


def test_claude_code_already_on_the_apps_engine_is_left_alone(places, tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, places)
    (Path.home() / ".claude.json").write_text(json.dumps(
        {"mcpServers": {"stenny": {"command": sys.executable, "args": ["-m", "stenny", "mcp"]}}}), encoding="utf-8")
    monkeypatch.setattr(claude_setup, "remove_from_claude_code", lambda cfg: pytest.fail("re-added"))
    assert old_engine.retire(log=lambda m: None) is True


def test_on_windows_the_setups_folder_is_under_localappdata(places, tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, places, windows=True)
    assert old_engine.folder() == tmp_path / "home/AppData/Local/Programs/stenny"
    assert old_engine.retire(log=lambda m: None) is True
    assert launcher.ours(Path.home() / ".local/bin/stenny.cmd")


def test_the_receipt_is_read_as_uv_writes_it(tmp_path, monkeypatch):
    receipt = tmp_path / "stenny/uv-receipt.toml"
    receipt.parent.mkdir()
    receipt.write_text('[tool]\nrequirements = [{ name = "stenny", editable = "/x/stenny.noindex" }]\npython = "3.12"\n'
                       'entrypoints = [\n    { name = "stenny", install-path = "/x/bin/stenny", from = "stenny" },\n]\n',
                       encoding="utf-8")
    monkeypatch.setattr(old_engine, "_run", lambda *a, timeout=60: Done(str(tmp_path) + "\n"))
    assert old_engine.installed_from("uv") == Path("/x/stenny.noindex")
    receipt.unlink()
    assert old_engine.installed_from("uv") is None


@pytest.mark.skipif(sys.platform == "win32", reason="the Mac's Trash; Windows' Recycle Bin goes through PowerShell")
def test_the_old_folder_goes_to_the_trash_under_a_free_name(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "_windows", lambda: False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for _ in range(2):
        old = tmp_path / "Applications/stenny.noindex"
        old.mkdir(parents=True)
        went = old_engine.to_trash(old)
        assert went.parent == tmp_path / ".Trash" and not old.exists()
    assert len(list((tmp_path / ".Trash").iterdir())) == 2
