"""The engine inside the app (#92, desktop/scripts/bundle-engine.sh): it knows it runs from there, finds the app's
own helpers and connector, tells other programs to start it as the app's Python, gets a `stenny` for the terminal
that never replaces anyone else's, and doesn't try to swap a folder of code it doesn't have."""
import plistlib
import sys
from pathlib import Path

import pytest

from stenny import claude_setup, config, jobs, launcher, paths, update


def _app(tmp_path, monkeypatch, windows: bool) -> Path:
    """A fake installed app whose engine this test's Python pretends to be; returns the app's folder (Contents on a
    Mac, the folder with Stenny.exe on Windows)."""
    monkeypatch.setattr(paths, "_windows", lambda: windows)
    monkeypatch.setattr(launcher, "_windows", lambda: windows)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    app = tmp_path / ("Stenny" if windows else "Applications/Stenny.app/Contents")
    engine = app / ("engine" if windows else "Resources/engine")
    engine.mkdir(parents=True)
    (engine / paths.ENGINE_INFO).write_text('{"version": "0.0.0"}', encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(engine))
    return app


def test_a_development_engine_is_not_the_apps():
    assert paths.in_app() is False
    assert paths.app_dir() is None and paths.engine_command() is None


def test_the_apps_engine_finds_its_own_helpers_first(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, windows=False)
    assert paths.in_app() and paths.app_dir() == app.resolve()
    assert paths.engine_command() == [sys.executable, "-m", "stenny"]
    # another Stenny.app in ~/Applications is not this engine's
    other = tmp_path / "home/Applications/Stenny.app/Contents/MacOS/stenny-rec"
    other.parent.mkdir(parents=True)
    other.write_text("", encoding="utf-8")
    other.chmod(0o755)
    own = app / "MacOS/stenny-rec"
    own.parent.mkdir(parents=True)
    own.write_text("", encoding="utf-8")
    own.chmod(0o755)
    assert paths.bundled("stenny-rec") == str(own.resolve())
    (app / "Resources/Stenny.mcpb").write_text("", encoding="utf-8")
    monkeypatch.delenv("STENNY_CONNECTOR", raising=False)
    assert claude_setup.connector_file() == (app / "Resources/Stenny.mcpb").resolve()


def test_on_windows_the_helpers_are_next_to_stenny_exe(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, windows=True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    (app / "ffmpeg.exe").write_text("", encoding="utf-8")
    (app / "Stenny.mcpb").write_text("", encoding="utf-8")
    assert paths.tool("ffmpeg") == str((app / "ffmpeg.exe").resolve())
    assert paths.app_file("Stenny.mcpb") == (app / "Stenny.mcpb").resolve()


def test_other_programs_start_the_apps_python(tmp_path, monkeypatch):
    """Claude Code's connector and the queue's background job: the app's Python, whose path stays the same across
    updates, never another `stenny` that happens to be on the PATH."""
    _app(tmp_path, monkeypatch, windows=False)
    assert claude_setup.stenny_command() == [sys.executable, "-m", "stenny"]


@pytest.mark.skipif(sys.platform == "win32", reason="the launchd agent is macOS's; Windows' task: test_windows_system")
def test_the_background_job_runs_the_apps_engine(places, tmp_path, monkeypatch):
    _app(tmp_path, monkeypatch, windows=False)
    monkeypatch.setattr(jobs.shutil, "which", lambda name: "/somewhere/else/stenny")
    monkeypatch.setattr(jobs.subprocess, "run", lambda *a, **k: None)
    jobs.install_agent()
    plist = plistlib.loads(jobs.AGENT_PATH.read_bytes())
    assert plist["ProgramArguments"] == [sys.executable, "-m", "stenny", "queue", "run", "--scheduled"]


def test_the_launcher_runs_the_apps_engine(tmp_path, monkeypatch):
    _app(tmp_path, monkeypatch, windows=False)
    target = launcher.install(log=lambda m: None)
    assert target == tmp_path / "home/.local/bin/stenny"
    text = target.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n") and launcher.MARK in text and "-m stenny \"$@\"" in text
    assert sys.platform == "win32" or target.stat().st_mode & 0o111
    before = target.stat().st_mtime_ns
    assert launcher.install(log=lambda m: None) == target and target.stat().st_mtime_ns == before  # unchanged
    assert launcher.remove() and not target.exists()


def test_the_launcher_never_replaces_another_stenny(tmp_path, monkeypatch):
    """A developer's `uv tool install -e .` (a link to uv's copy), or the engine an older setup installed."""
    _app(tmp_path, monkeypatch, windows=False)
    bin_dir = tmp_path / "home/.local/bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "stenny").write_text("#!/usr/bin/env python\n# uv's\n", encoding="utf-8")
    assert launcher.install(log=lambda m: None) is None
    assert "uv's" in (bin_dir / "stenny").read_text(encoding="utf-8")
    assert launcher.remove() is False and (bin_dir / "stenny").exists()


def test_without_the_apps_engine_there_is_no_launcher(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert launcher.install(log=lambda m: None) is None
    assert not (tmp_path / ".local/bin").exists()


def test_on_windows_the_launcher_is_a_cmd_file_beside_no_stenny_exe(tmp_path, monkeypatch):
    _app(tmp_path, monkeypatch, windows=True)
    target = launcher.install(log=lambda m: None)
    assert target.name == "stenny.cmd"
    raw = target.read_bytes()
    assert raw.startswith(b"@echo off\r\n") and b'-m stenny %*\r\n' in raw
    before = target.stat().st_mtime_ns
    launcher.install(log=lambda m: None)
    assert target.stat().st_mtime_ns == before  # CRLF compared as written, so not rewritten at every start
    target.unlink()
    (target.parent / "stenny.exe").write_text("", encoding="utf-8")  # uv's: it would win on the PATH anyway
    assert launcher.install(log=lambda m: None) is None and not target.exists()


def test_stenny_update_says_the_app_is_what_gets_updated(tmp_path, monkeypatch):
    _app(tmp_path, monkeypatch, windows=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert update.how().startswith("In Stenny: Check for Updates")


def test_the_config_example_is_found():
    """`stenny init` and a first config read it: at the checkout's top here, next to the code in the app's engine."""
    assert config.EXAMPLE.is_file()
