"""Recordings added from a file in Stenny's window (#136): ready to transcribe, placed by date, nothing transcribed
before Transcribe, the original file only read (ready.py, through the app server)."""

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from stenny import app_server, audio, cli, jobs, pipeline, ready, resources
from stenny.config import Config, Course
from stenny.render import parse_frontmatter
from stenny.vault import LectureFiles

RATE = 16000


class FakeEngine:
    def __init__(self):
        self.languages = []

    def transcribe(self, clip, language, prompt=None, fast=False):
        self.languages.append(language)
        filler = " more" * int(len(clip) / RATE * 2)
        return [{"start": 0.0, "end": len(clip) / RATE, "text": f"Welcome to the lecture.{filler}",
                 "no_speech_prob": 0.0, "avg_logprob": -0.2}]

    def language_probs(self, clip, allowed):
        return {lang: 1.0 if lang == "en" else 0.0 for lang in allowed}


def recording(path: Path, seconds: int = 20, when: str | None = None, seed: int = 1) -> Path:
    """Speech-like noise as an audio file, with its recording time inside like a phone's (creation_time)."""
    noise = (np.random.default_rng(seed).normal(0, 0.1, seconds * RATE) * 32767).astype(np.int16)
    meta = ["-metadata", f"creation_time={when}"] if when else []
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "-",
                    *meta, str(path)], input=noise.tobytes(), check=True)
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "QUEUE_DIR", tmp_path / "Queue")
    monkeypatch.setattr(jobs, "recording_running", lambda: False)
    monkeypatch.setattr(jobs, "on_ac_power", lambda: True)
    monkeypatch.setattr(resources, "enough_memory_for_final_pass", lambda: True)
    started = []
    monkeypatch.setattr(ready, "_start", started.append)
    cfg = Config(folder=tmp_path / "Stenny", data_dir=tmp_path / "data", llm_backend="none",
                 courses={"Calculus": Course("Calculus", language="en", languages=["en"])})
    cfg.folder.mkdir()
    cfg.started = started
    for day in (15, 29):  # two lectures already transcribed
        pipeline.process(recording(tmp_path / f"oct{day}.wav", seed=day), cfg, cfg.courses["Calculus"], FakeEngine(),
                         when=datetime(2026, 10, day, 10, 0), use_llm=False)
    return cfg


def call(cfg, method, **params):
    reply = app_server.handle(cfg, {"id": 1, "method": method, "params": params})
    if "error" in reply:
        raise AssertionError(reply["error"])
    return reply["result"]


def calculus(cfg):
    return [e for e in call(cfg, "library")["entries"] if e["course"] == "Calculus"]


def test_a_file_becomes_a_lecture_at_its_date_and_nothing_is_transcribed(cfg, tmp_path):
    meeting = recording(tmp_path / "meeting.m4a", when="2026-10-22T08:30:00Z")
    before = digest(meeting)
    entry = call(cfg, "add_recording", course="Calculus", path=str(meeting))
    assert entry["status"] == "ready" and entry["folder"] == "2026-10-22" and entry["ready"]["name"] == "meeting.m4a"
    assert entry["duration"] == "0:00:20" and entry["language"] == "en" and entry["waiting"] is None
    lectures = calculus(cfg)
    assert [(e["folder"], e["number"], e["status"]) for e in lectures] == [
        ("2026-10-15", 1, "final"), ("2026-10-22", 2, "ready"), ("2026-10-29", 3, "final")]
    # nothing in the Stenny folder or the queue yet, and the file itself untouched
    assert not (cfg.folder / "Calculus" / "2026-10-22").exists() and jobs.pending() == []
    assert digest(meeting) == before
    # the files in the Stenny folder keep their numbers until it's transcribed
    later = LectureFiles(cfg.folder / "Calculus" / "2026-10-29", "Calculus").existing("transcript")
    assert parse_frontmatter(later.read_text(encoding="utf-8"))["lecture"] == 2
    # the same file again is the same entry
    again = call(cfg, "add_recording", course="Calculus", path=str(meeting))
    assert again["ready"]["id"] == entry["ready"]["id"] and len(calculus(cfg)) == 3


def test_what_isnt_audio_or_a_transcript_is_refused(cfg, tmp_path):
    note = tmp_path / "notes.zip"
    note.write_bytes(b"PK\x03\x04 not a recording")
    reply = app_server.handle(cfg, {"id": 1, "method": "add_recording", "params": {"course": "Calculus", "path": str(note)}})
    assert "notes.zip" in reply["error"] and "isn't a recording or a transcript" in reply["error"]
    assert len(calculus(cfg)) == 2


def test_several_files_each_by_its_own_date(cfg, tmp_path):
    a = recording(tmp_path / "a.m4a", when="2026-11-05T09:00:00Z", seed=2)
    b = recording(tmp_path / "b.m4a", when="2026-11-05T14:00:00Z", seed=3)
    c = recording(tmp_path / "c.m4a", when="2026-10-01T09:00:00Z", seed=4)
    folders = [call(cfg, "add_recording", course="Calculus", path=str(p))["folder"] for p in (a, b, c)]
    assert folders == ["2026-11-05", "2026-11-05 (2)", "2026-10-01"]
    assert [(e["folder"], e["number"]) for e in calculus(cfg)] == [
        ("2026-10-01", 1), ("2026-10-15", 2), ("2026-10-29", 3), ("2026-11-05", 4), ("2026-11-05 (2)", 5)]


def test_date_and_language_can_be_changed_before_and_after_transcribe(cfg, tmp_path, monkeypatch):
    entry = call(cfg, "add_recording", course="Calculus", path=str(recording(tmp_path / "m.m4a", when="2026-10-22T08:30:00Z")))
    rid = entry["ready"]["id"]
    moved = call(cfg, "set_ready", course="Calculus", id=rid, date="2026-10-15")
    assert moved["folder"] == "2026-10-15 (2)" and moved["date"] == "2026-10-15"  # next to the lecture that day
    assert [e["folder"] for e in calculus(cfg)] == ["2026-10-15", "2026-10-15 (2)", "2026-10-29"]
    assert call(cfg, "set_ready", course="Calculus", id=rid, language="de")["language"] == "de"
    # confirmed while on battery: it waits, and its job takes a later change too
    monkeypatch.setattr(jobs, "on_ac_power", lambda: False)
    queued = call(cfg, "transcribe_ready", course="Calculus", id=rid)
    assert queued["status"] == "queued" and queued["waiting"] == "power" and cfg.started == []
    call(cfg, "set_ready", course="Calculus", id=rid, date="2026-11-12")
    now = call(cfg, "transcribe_ready", course="Calculus", id=rid, now=True)  # Transcribe Now: on battery too
    assert now["status"] == "finalizing" and cfg.started == jobs.pending()
    job = json.loads(jobs.pending()[0].read_text(encoding="utf-8"))
    assert job["lang"] == "de" and job["ready"]["folder"] == "2026-11-12" and job["ready"]["when"] == "2026-11-12T" + entry["start"]


def test_transcribe_makes_a_lecture_like_any_other(cfg, tmp_path):
    meeting = recording(tmp_path / "meeting.m4a", when="2026-10-22T08:30:00Z")
    before = digest(meeting)
    rid = call(cfg, "add_recording", course="Calculus", path=str(meeting))["ready"]["id"]
    started = call(cfg, "transcribe_ready", course="Calculus", id=rid)
    assert started["status"] == "finalizing" and len(cfg.started) == 1  # its own process, at once
    assert call(cfg, "transcribe_ready", course="Calculus", id=rid)["status"] == "finalizing" and len(cfg.started) == 1
    # what that process does: `stenny queue run --job …`, the job as any other
    engine = FakeEngine()
    cli.run_job(cfg.started[0], cfg, engine)
    folder = cfg.folder / "Calculus" / "2026-10-22"
    fm = parse_frontmatter(LectureFiles(folder, "Calculus").existing("transcript").read_text(encoding="utf-8"))
    assert fm["lecture"] == 2 and fm["source"] == "meeting.m4a" and fm["start"] == datetime(2026, 10, 22, 8, 30).replace(
        tzinfo=__import__("datetime").timezone.utc).astimezone().strftime("%H:%M")
    # the later lecture moved up one number, in its file too
    later = LectureFiles(cfg.folder / "Calculus" / "2026-10-29", "Calculus").existing("transcript")
    assert parse_frontmatter(later.read_text(encoding="utf-8"))["lecture"] == 3
    data = cfg.data_for(cfg.courses["Calculus"])
    assert (data / "2026-10-22.segments.json").exists() and (data / "Calculus 2026-10-22 (raw).md").exists()
    assert [(e["folder"], e["status"], e.get("ready")) for e in calculus(cfg)] == [
        ("2026-10-15", "final", None), ("2026-10-22", "final", None), ("2026-10-29", "final", None)]
    assert jobs.pending() == [] and list(data.glob("*.ready.json")) == []
    assert digest(meeting) == before  # read, never changed
    # Stenny's own copy carries the recording's start, so a copy sent to a friend still knows its date
    assert audio.recorded_at(data / "2026-10-22.opus").strftime("%Y-%m-%d") == "2026-10-22"


def test_the_language_chosen_is_the_one_transcribed(cfg, tmp_path):
    rid = call(cfg, "add_recording", course="Calculus", path=str(recording(tmp_path / "m.m4a", when="2026-10-22T08:30:00Z")))["ready"]["id"]
    call(cfg, "set_ready", course="Calculus", id=rid, language="de")
    call(cfg, "transcribe_ready", course="Calculus", id=rid)
    engine = FakeEngine()
    cli.run_job(cfg.started[0], cfg, engine)
    assert set(engine.languages) == {"de"}


def test_removing_a_ready_lecture_leaves_the_file(cfg, tmp_path, monkeypatch):
    meeting = recording(tmp_path / "meeting.m4a", when="2026-10-22T08:30:00Z")
    before = digest(meeting)
    rid = call(cfg, "add_recording", course="Calculus", path=str(meeting))["ready"]["id"]
    monkeypatch.setattr(jobs, "on_ac_power", lambda: False)
    call(cfg, "transcribe_ready", course="Calculus", id=rid)  # waits for power
    assert call(cfg, "remove_ready", course="Calculus", id=rid) == {"removed": True}
    assert [e["folder"] for e in calculus(cfg)] == ["2026-10-15", "2026-10-29"]
    assert jobs.pending() == [] and meeting.exists() and digest(meeting) == before
    reply = app_server.handle(cfg, {"id": 1, "method": "remove_ready", "params": {"course": "Calculus", "id": rid}})
    assert "isn't waiting" in reply["error"]


def test_during_a_recording_a_confirmed_file_waits_for_it(cfg, tmp_path, monkeypatch):
    rid = call(cfg, "add_recording", course="Calculus", path=str(recording(tmp_path / "m.m4a", when="2026-10-22T08:30:00Z")))["ready"]["id"]
    monkeypatch.setattr(jobs, "recording_running", lambda: True)
    assert calculus(cfg)[1]["waiting"] == "recording"  # the card says so before Transcribe
    queued = call(cfg, "transcribe_ready", course="Calculus", id=rid)
    assert queued["status"] == "queued" and queued["waiting"] == "recording" and cfg.started == []
    # the queue's scheduled run leaves it alone while the recording runs …
    ran = []
    monkeypatch.setattr(cli, "run_job", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(cli, "recover_sessions", lambda cfg: [])
    args = type("Args", (), {"scheduled": True})()
    cli.run_queue(cfg, args, jobs.pending())
    assert ran == []


def test_a_failed_pass_can_be_tried_again(cfg, tmp_path):
    meeting = recording(tmp_path / "m.m4a", when="2026-10-22T08:30:00Z")
    rid = call(cfg, "add_recording", course="Calculus", path=str(meeting))["ready"]["id"]
    call(cfg, "transcribe_ready", course="Calculus", id=rid)
    job = cfg.started[0]
    job.rename(job.with_suffix(".failed"))  # what run_queue does with a job that fails
    entry = next(e for e in calculus(cfg) if e.get("ready"))
    assert entry["status"] == "ready" and "couldn't be made" in entry["ready"]["error"]
    call(cfg, "transcribe_ready", course="Calculus", id=rid)
    assert len(cfg.started) == 2 and not job.with_suffix(".failed").exists()


def test_a_recording_that_takes_its_folder_moves_it_on(cfg, tmp_path):
    entry = call(cfg, "add_recording", course="Calculus", path=str(recording(tmp_path / "m.m4a", when="2026-11-24T08:30:00Z")))
    assert entry["folder"] == "2026-11-24"
    # a lecture recorded live the same day gets that folder (the live session doesn't know about ready ones)
    vault = cfg.vault_for(cfg.courses["Calculus"])
    vault.write(LectureFiles(cfg.folder / "Calculus" / "2026-11-24", "Calculus").new("transcript"),
                '---\ncourse: "Calculus"\nlecture: 3\nstatus: "live"\n---\n# draft\n')
    assert [(e["folder"], e["status"]) for e in calculus(cfg)][-2:] == [("2026-11-24", "live"), ("2026-11-24 (2)", "ready")]


def test_a_talk_from_a_file(cfg, tmp_path):
    entry = call(cfg, "add_recording", event="Guest talk", language="en",
                 path=str(recording(tmp_path / "talk.mp3", when="2026-11-03T17:00:00Z")))
    assert entry["kind"] == "event" and entry["course"] == "Guest talk" and entry["number"] == 0
    assert entry["language"] == "en"
    talks = [e for e in call(cfg, "library")["entries"] if e["kind"] == "event"]
    assert [(e["course"], e["folder"], e["status"]) for e in talks] == [("Guest talk", "2026-11-03", "ready")]
    call(cfg, "transcribe_ready", event=True, course="Guest talk", id=entry["ready"]["id"])
    cli.run_job(cfg.started[0], cfg, FakeEngine())
    assert LectureFiles(cfg.events_root / "Guest talk" / "2026-11-03", "Guest talk").existing("transcript")


def test_stennys_own_audio_dates_by_its_name(tmp_path):
    sent = tmp_path / "2026-10-20 (2).opus"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", "-c:a", "libopus", str(sent)], check=True)
    assert audio.recorded_at(sent).strftime("%Y-%m-%d") == "2026-10-20"  # not the day it arrived
    kept = audio.archive(sent, tmp_path / "out" / "2026-10-20.opus", when=datetime(2026, 10, 20, 10, 15))
    assert audio.recorded_at(kept) == datetime(2026, 10, 20, 10, 15)
    assert audio.probe(kept)["audio"] and audio.probe(tmp_path / "out") is None
