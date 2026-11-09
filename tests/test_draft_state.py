"""A recording without its transcript yet says where the transcript is: waiting (for power, if the Mac is on battery),
or being made. Its file in the vault is a placeholder without text (Stenny 0.4's live drafts had a rough text)."""
import json
import sys
from types import SimpleNamespace

import pytest

from stenny import cli, jobs, pipeline, resources, share
from stenny.config import Config, Course
from stenny.connector import _status_note
from stenny.render import Lecture, set_draft_state, transcript_md

PARAGRAPHS = [{"start": 4.0, "text": "Bonjour."}]


def draft(status="live", paragraphs=PARAGRAPHS) -> str:
    lec = Lecture(course="Calculus", date="2026-10-31", start_time="10:00", duration=600, languages=["fr"],
                  language_note="live", model="m", source="live", status=status, number=2,
                  names={"transcript": "Calculus 2026-10-31.md"})
    return transcript_md(lec, paragraphs, [])


def test_the_placeholder_says_its_transcript_waits_for_power():
    queued = set_draft_state(draft(paragraphs=[]), "queued", "battery")
    assert 'status: "queued"\nwaiting: "battery"\n' in queued
    assert "> **No transcript yet.** The recording is finished; its transcript is made once the Mac is plugged in." \
        in queued
    assert queued.count("No transcript yet") == 1

    making = set_draft_state(queued, "finalizing")
    assert 'status: "finalizing"\n' in making and "waiting:" not in making
    assert "its transcript is being made now" in making and "plugged in" not in making


def test_a_live_draft_of_stenny_0_4_gets_the_new_note():
    old = draft().replace("> **No transcript yet.** ", "> **Live draft** (fast, unpolished). ")
    queued = set_draft_state(old, "queued")
    assert "> **No transcript yet.** The recording is finished; its transcript comes later." in queued
    assert "Live draft" not in queued and "**[0:00:04]** Bonjour." in queued


def test_a_final_transcript_is_never_touched():
    assert set_draft_state(draft("final"), "queued", "battery") is None
    assert set_draft_state("no frontmatter", "queued", "battery") is None


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "QUEUE_DIR", tmp_path / "data/_Queue")
    monkeypatch.setattr(jobs, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(jobs, "lower_own_priority", lambda mode: None)
    monkeypatch.setattr(resources, "enough_memory_for_final_pass", lambda: True)
    return Config(folder=tmp_path / "vault" / "Lectures", data_dir=tmp_path / "data",
                  courses={"Calculus": Course("Calculus", language="fr", languages=["fr"])})


class StoppedSession:
    def __init__(self, cfg):
        folder = cfg.recordings_dir / "Calculus"
        folder.mkdir(parents=True)
        self.pcm_path, self.session_path = folder / "2026-10-31.live.pcm", folder / "2026-10-31.session.json"
        self.pcm_path.write_bytes(b"\0" * 32000)
        self.session_path.write_text(json.dumps({
            "course": "Calculus", "started_at": "2026-10-31T10:00:00", "folder": "2026-10-31", "spans": [[0, 0.0]],
            "pauses": [], "recording": str(self.pcm_path), "state": "stopped", "recorded_samples": 16000}), encoding="utf-8")
        self.transcript_path = cfg.folder / "Calculus/2026-10-31/Calculus 2026-10-31.md"
        self.transcript_path.parent.mkdir(parents=True)
        self.transcript_path.write_text(draft(), encoding="utf-8")
        self.course, self.lang_override, self.backup = Course(name="Calculus"), None, None
        self.recorded, self.final_note, self.state = 16000, "", "stopped"

    def text(self) -> str:
        return self.transcript_path.read_text(encoding="utf-8")


def test_stopping_on_battery_says_so_in_the_draft(cfg, monkeypatch):
    monkeypatch.setattr(jobs, "on_ac_power", lambda: False)
    session = StoppedSession(cfg)
    cli.finalize(session, cfg, "auto", True, object())
    assert 'waiting: "battery"' in session.text() and len(jobs.pending()) == 1


def test_any_other_wait_gives_no_reason(cfg, monkeypatch):
    monkeypatch.setattr(jobs, "on_ac_power", lambda: True)
    session = StoppedSession(cfg)
    with jobs.QueueLock():  # another final pass is running
        cli.finalize(session, cfg, "auto", True, object())
    assert 'status: "queued"\n' in session.text() and "waiting:" not in session.text()
    assert "its transcript comes later." in session.text()


def test_the_final_pass_marks_the_draft_and_an_unplugged_mac_puts_it_back(cfg, monkeypatch):
    monkeypatch.setattr(jobs, "on_ac_power", lambda: False)
    session = StoppedSession(cfg)
    cli.finalize(session, cfg, "auto", True, object())
    seen = []

    def unplugged(*a, **kw):
        seen.append(session.text())
        raise pipeline.Interrupted("stopped")

    monkeypatch.setattr(pipeline, "process", unplugged)
    with pytest.raises(pipeline.Interrupted):
        cli.run_job(jobs.pending()[0], cfg, object())
    assert 'status: "finalizing"' in seen[0]
    assert 'waiting: "battery"' in session.text() and len(jobs.pending()) == 1


def test_the_window_and_claude_get_the_same_sentence(cfg, monkeypatch):
    StoppedSession(cfg)
    cli.mark_draft(cfg.folder / "Calculus/2026-10-31/Calculus 2026-10-31.md", "queued", "battery")
    [entry] = share.library(cfg)
    computer = "PC" if sys.platform == "win32" else "Mac"
    assert entry["status"] == "queued" and f"once the {computer} is plugged in" in entry["draft_note"]
    assert _status_note(entry) == f" [not transcribed yet. {entry['draft_note']}]"


def test_the_window_can_start_one_waiting_final_pass_now_also_on_battery(cfg, monkeypatch):
    monkeypatch.setattr(jobs, "on_ac_power", lambda: False)
    monkeypatch.setattr(jobs, "apply_priority", lambda mode: None)
    monkeypatch.setattr(jobs, "keep_awake", lambda: None)
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "recover_sessions", lambda cfg: [])
    monkeypatch.setattr("stenny.engines.get_engine", lambda model, engine="auto": object())
    session = StoppedSession(cfg)
    cli.finalize(session, cfg, "auto", True, object())
    [job] = jobs.pending()
    assert cli.lecture_job(cfg, "calculus", "2026-10-31") == job
    assert cli.lecture_job(cfg, "Calculus", "2026-10-30") is None
    assert cli.lecture_job(cfg, "Calculus", "2026-10-31", event=True) is None

    ran = []
    monkeypatch.setattr(cli, "run_job", lambda p, cfg, engine, stop=None: ran.append((p, stop)))
    args = SimpleNamespace(action="run", scheduled=False, lecture=["Calculus", "2026-10-31"], event=False)
    cli.cmd_queue(args)
    assert ran == [(job, None)]  # not stopped for being on battery

    with pytest.raises(SystemExit, match="no final transcript waiting"):
        cli.cmd_queue(SimpleNamespace(**{**vars(args), "lecture": ["Calculus", "2026-10-30"]}))
    with jobs.QueueLock(), pytest.raises(SystemExit, match="Another final transcript is being made"):
        cli.cmd_queue(args)
