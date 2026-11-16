"""Tauri's updater (#92): what an update has to wait for (app_live.update_blocked, asked by
desktop/src-tauri/src/updater.rs) and the latest.json that releases publish (desktop/scripts/latest-json.py)."""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from stenny import app_live, jobs
from stenny.live import control

SCRIPT = Path(__file__).resolve().parents[1] / "desktop/scripts/latest-json.py"
spec = importlib.util.spec_from_file_location("latest_json", SCRIPT)
latest_json = importlib.util.module_from_spec(spec)
spec.loader.exec_module(latest_json)


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "QUEUE_DIR", tmp_path / "Queue")
    monkeypatch.setattr(control, "send", lambda *a, **k: (_ for _ in ()).throw(control.NoSession("none")))
    app_live._started.clear()
    yield tmp_path
    app_live._started.clear()


def test_nothing_running_lets_the_update_go(queue):
    assert app_live.update_blocked(None, {}) == {"recording": False, "final_pass": False}


def test_a_recording_makes_the_update_wait(queue, monkeypatch):
    for state in ("recording", "paused"):
        monkeypatch.setattr(control, "send", lambda *a, state=state, **k: {"ok": True, "state": state})
        assert app_live.update_blocked(None, {})["recording"] is True


def test_a_final_pass_makes_the_update_wait(queue):
    """A running final pass holds the queue's lock (a scheduled one, or the one right after a recording)."""
    with jobs.QueueLock() as held:
        assert held is not None
        assert app_live.update_blocked(None, {}) == {"recording": False, "final_pass": True}
    assert app_live.update_blocked(None, {})["final_pass"] is False  # and the check took no lock of its own


def test_latest_json_collects_both_systems_for_one_version():
    now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    mac = latest_json.add({}, "0.5.12", "darwin-aarch64", "https://x/Stenny.app.tar.gz", "sig-mac\n", "notes", now)
    both = latest_json.add(mac, "0.5.12", "windows-x86_64", "https://x/setup.exe", "sig-win", None, now)
    assert both == {"version": "0.5.12", "pub_date": "2026-11-14T12:00:00Z", "notes": "notes", "platforms": {
        "darwin-aarch64": {"signature": "sig-mac", "url": "https://x/Stenny.app.tar.gz"},
        "windows-x86_64": {"signature": "sig-win", "url": "https://x/setup.exe"}}}


def test_an_older_versions_file_is_started_over():
    """CI adds Windows to the release's latest.json; one left from another version must not lend it a Mac entry."""
    old = latest_json.add({}, "0.5.11", "darwin-aarch64", "https://x/old.tar.gz", "sig", None)
    new = latest_json.add(old, "0.5.12", "windows-x86_64", "https://x/setup.exe", "sig")
    assert new["version"] == "0.5.12" and list(new["platforms"]) == ["windows-x86_64"]


@pytest.mark.parametrize("platform, url, signature", [
    ("linux-x86_64", "https://x/a", "sig"), ("darwin-aarch64", "http://x/a", "sig"), ("darwin-aarch64", "https://x", " ")])
def test_latest_json_refuses_what_the_updater_couldnt_use(platform, url, signature):
    with pytest.raises(ValueError):
        latest_json.add({}, "0.5.12", platform, url, signature)


def test_the_command_line_writes_the_file(tmp_path):
    sig = tmp_path / "Stenny.app.tar.gz.sig"
    sig.write_text("c2ln\n", encoding="utf-8")
    out = tmp_path / "latest.json"
    assert latest_json.main([str(out), "--version", "v0.5.12", "--platform", "darwin-aarch64",
                             "--url", "https://x/Stenny.app.tar.gz", "--signature-file", str(sig)]) == 0
    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.12" and manifest["platforms"]["darwin-aarch64"]["signature"] == "c2ln"


def test_the_app_asks_stenny_eu_for_its_updates():
    """Invite-only (closed alpha): stenny.eu hands out the newest release's latest.json to invited users (the
    stenny-website repo, api/update.js); the app sends the invite along (updater.rs)."""
    conf = json.loads((SCRIPT.parents[1] / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    updater = conf["plugins"]["updater"]
    assert updater["endpoints"] == ["https://stenny.eu/api/update?target={{target}}&arch={{arch}}&current={{current_version}}"]
    assert updater["pubkey"]
    # the update archives are made only for releases (release.sh, CI's installer job), never by every build
    assert not conf["bundle"].get("createUpdaterArtifacts")
