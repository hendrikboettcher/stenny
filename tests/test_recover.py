import json
import subprocess
import sys

import pytest

from stenny import cli, jobs, resources
from stenny.config import Config


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "QUEUE_DIR", tmp_path / "data/_Queue")
    monkeypatch.setattr("stenny.live.control.is_running", lambda: False)
    return Config(vault=tmp_path / "vault", lectures_dir="Lectures", data_dir=tmp_path / "data")


def crashed_session(cfg, samples=16000, name="Calculus", folder=None):
    base = (folder or cfg.data_dir / name) / "2026-10-31"
    base.parent.mkdir(parents=True, exist_ok=True)
    pcm = base.with_name("2026-10-31.live.pcm")
    pcm.write_bytes(b"\0" * samples * 2)
    session = base.with_name("2026-10-31.session.json")
    session.write_text(json.dumps({"course": name, "started_at": "2026-10-31T10:00:00", "folder": "2026-10-31",
                                   "recording": str(pcm), "state": "recording", "spans": [[0, 0.0]],
                                   "pauses": [[1.0, 2.0, "paused"], [3.0, None, "no input"]], "recorded_samples": 5}))
    return pcm, session


def test_a_died_session_gets_its_final_pass_with_all_its_audio(cfg):
    pcm, session = crashed_session(cfg)
    assert cli.recover_sessions(cfg) == [{"course": "Calculus", "kind": "lecture"}]
    job = json.loads(jobs.pending()[0].read_text())
    assert job["file"] == str(pcm.resolve()) and job["live"] == str(session)
    d = json.loads(session.read_text())
    assert d["recorded_samples"] == 16000 and d["pauses"] == [[1.0, 2.0, "paused"]]


def test_recovering_twice_queues_once(cfg):
    crashed_session(cfg)
    cli.recover_sessions(cfg)
    assert cli.recover_sessions(cfg) == [] and len(jobs.pending()) == 1


def test_a_failed_final_pass_isnt_queued_again(cfg):
    crashed_session(cfg)
    cli.recover_sessions(cfg)
    job = jobs.pending()[0]
    job.rename(job.with_suffix(".failed"))
    assert cli.recover_sessions(cfg) == []


def test_finished_and_empty_recordings_are_left_alone(cfg):
    pcm, _ = crashed_session(cfg)
    pcm.unlink()  # the final pass removes the .pcm
    crashed_session(cfg, samples=0, name="Economics")
    assert cli.recover_sessions(cfg) == [] and not jobs.pending()


def test_nothing_happens_while_a_session_runs(cfg, monkeypatch):
    crashed_session(cfg)
    monkeypatch.setattr("stenny.live.control.is_running", lambda: True)
    assert cli.recover_sessions(cfg) == [] and not jobs.pending()


def test_a_talk_stays_a_talk(cfg):
    crashed_session(cfg, name="Guest talk", folder=cfg.data_dir / "_Recordings/Guest talk")
    assert cli.recover_sessions(cfg) == [{"course": "Guest talk", "kind": "event"}]


def test_orphan_recorder_is_stopped(tmp_path):
    fake = tmp_path / "stenny-rec"
    fake.write_text("#!/bin/sh\nexec sleep 60\n")
    fake.chmod(0o755)
    # a process whose command line looks like the recorder's, as `ps` shows it
    out = tmp_path / "a recording.pcm"
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "stenny-rec", "--out", str(out)])
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "stenny-rec", "--out", str(out) + "x2"])
    try:
        # "x2" contains the first path: only an exact path may match
        cli.stop_orphan_recorders(tmp_path / "nothing.pcm")
        assert proc.poll() is None
        cli.stop_orphan_recorders(out)
        assert proc.wait(timeout=5) is not None
        assert other.poll() is None
    finally:
        other.kill()


def test_memory_check_needs_normal_pressure_and_room(monkeypatch):
    monkeypatch.setattr(resources, "memory_pressure", lambda: 1)
    monkeypatch.setattr(resources, "available_memory", lambda: 4 * resources.GB)
    assert resources.enough_memory_for_final_pass()
    monkeypatch.setattr(resources, "available_memory", lambda: 1 * resources.GB)
    assert not resources.enough_memory_for_final_pass()
    monkeypatch.setattr(resources, "available_memory", lambda: 4 * resources.GB)
    monkeypatch.setattr(resources, "memory_pressure", lambda: 2)
    assert not resources.enough_memory_for_final_pass()


def test_the_real_readings_make_sense():
    assert resources.memory_pressure() in (1, 2, 4)
    assert 0 < resources.available_memory() < 1024 * resources.GB
