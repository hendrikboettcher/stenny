"""`stenny app-server`: the engine behind Stenny's app, one JSON request and one reply per line."""
import io
import json
from types import SimpleNamespace

import pytest

from stenny import app_server, config as config_mod
from stenny.render import Lecture, summary_md, transcript_md

PARAGRAPHS = [{"start": 4.0, "text": "Welcome."}]


@pytest.fixture
def cfg(tmp_path):
    lec = Lecture(course="Calculus", date="2026-10-20", start_time="10:00", duration=3600, languages=["en"],
                  language_note="en", model="m", source="file", number=1, names={"transcript": "Calculus 2026-10-20.md"})
    folder = tmp_path / "Lectures" / "Calculus" / "2026-10-20"
    folder.mkdir(parents=True)
    (folder / "Calculus 2026-10-20.md").write_text(transcript_md(lec, PARAGRAPHS, []), encoding="utf-8")
    (folder / "Calculus 2026-10-20 (summary).md").write_text(
        summary_md(lec, "## Overview\nAbout [[_Topics/Limits|limits]]. [0:00:04]", "Claude Code (haiku)"), encoding="utf-8")
    return SimpleNamespace(folder=tmp_path / "Lectures", events_root=tmp_path / "Lectures" / "Talks",
                           courses={"Calculus": SimpleNamespace(name="Calculus", hidden=False)})


def test_library_lists_lectures_and_courses(cfg):
    reply = app_server.handle(cfg, {"id": 7, "method": "library"})
    assert reply["id"] == 7 and [e["folder"] for e in reply["result"]["entries"]] == ["2026-10-20"]
    assert reply["result"]["courses"] == [{"name": "Calculus", "hidden": False}]


def test_a_lecture_comes_with_its_summary_as_plain_markdown(cfg):
    result = app_server.handle(cfg, {"id": 1, "method": "lecture",
                                     "params": {"course": "calculus", "folder": "2026-10-20"}})["result"]
    assert result["entry"]["number"] == 1
    assert "About limits. [0:00:04]" in result["summary"] and "[[" not in result["summary"]
    assert result["summary"].startswith("> Written by AI from the transcript.") and "haiku" not in result["summary"]


def test_errors_are_replies_not_crashes(cfg):
    assert app_server.handle(cfg, {"id": 2, "method": "nope"})["error"] == "Unknown method 'nope'."
    missing = app_server.handle(cfg, {"id": 3, "method": "lecture", "params": {"course": "Calculus"}})
    assert missing == {"id": 3, "error": "Missing 'folder'."}
    unknown = app_server.handle(cfg, {"id": 4, "method": "lecture", "params": {"course": "X", "folder": "2026-01-01"}})
    assert unknown["error"].startswith("No transcript for X")
    waiting = app_server.handle(cfg, {"id": 5, "method": "finish_now",
                                      "params": {"course": "Calculus", "folder": "2026-10-20"}})
    assert waiting["error"] == "This lecture has no final transcript waiting."


def test_serve_answers_line_by_line_and_keeps_stdout_clean(cfg, monkeypatch):
    monkeypatch.setattr(config_mod, "load", lambda: cfg)

    def noisy(cfg, params):
        print("engine chatter")
        return {"ok": True}

    monkeypatch.setitem(app_server.METHODS, "noisy", noisy)
    out = io.StringIO()
    app_server.serve(io.StringIO('{"id": 1, "method": "noisy"}\n\nnot json\n{"id": 2, "method": "ping"}\n'), out)
    replies = {r["id"]: r for r in map(json.loads, out.getvalue().splitlines())}  # in any order, by id
    assert len(replies) == 3 and replies[1] == {"id": 1, "result": {"ok": True}}
    assert replies[None] == {"id": None, "error": "Not JSON."} and replies[2]["result"]["version"]


def test_a_recording_without_transcript_shows_its_text_so_far(cfg, tmp_path):
    from stenny.config import Course
    from stenny.pipeline import ChunkCache

    lec = Lecture(course="Calculus", date="2026-11-03", start_time="10:00", duration=600, languages=["en"],
                  language_note="live", model="m", source="live", status="live", number=2,
                  names={"transcript": "Calculus 2026-11-03.md"})
    folder = cfg.folder / "Calculus" / "2026-11-03"
    folder.mkdir(parents=True)
    (folder / "Calculus 2026-11-03.md").write_text(transcript_md(lec, [], []), encoding="utf-8")
    data = tmp_path / "audio" / "Calculus"
    data.mkdir(parents=True)
    cfg.data_for = lambda course: data
    cfg.courses["Calculus"] = Course("Calculus")
    cache = ChunkCache(data / "2026-11-03.chunks.jsonl")
    cache.put(0, 16000 * 20, "en", [{"start": 1.0, "end": 5.0, "text": "Limits first."}], 0.0)
    cache.put(16000 * 20, 16000 * 45, "en", [{"start": 2.0, "end": 9.0, "text": "Then derivatives."}], 20.0)
    with open(data / "2026-11-03.chunks.jsonl", "a") as f:
        f.write('{"a": 7200')  # a line cut short by a crash

    result = app_server.handle(cfg, {"id": 1, "method": "lecture",
                                     "params": {"course": "Calculus", "folder": "2026-11-03"}})["result"]
    assert result["entry"]["status"] == "live"
    assert result["progress"] == "**[0:00:01]** Limits first. Then derivatives.\n"
    final = app_server.handle(cfg, {"id": 2, "method": "lecture",
                                    "params": {"course": "Calculus", "folder": "2026-10-20"}})["result"]
    assert final["progress"] is None


def test_setup_status_checks_each_step(cfg, monkeypatch):
    """The "Finish setting up Stenny" card: courses, the AI step and the connector, each as it really is now."""
    from stenny import claude_setup
    monkeypatch.setattr(claude_setup, "ai_status", lambda c: {"backend": "claude-code", "state": "signed_out"})
    monkeypatch.setattr(claude_setup, "connector_status",
                        lambda c: {"desktop": True, "installed": False, "code": {"available": True, "added": False}})
    cfg.courses["Old"] = SimpleNamespace(name="Old", hidden=True)
    result = app_server.handle(cfg, {"id": 1, "method": "setup_status"})["result"]
    assert result["show"] and result["courses"] == 1  # a removed course doesn't count
    assert result["ai"]["state"] == "signed_out" and result["connector"]["installed"] is False


def test_setup_status_checks_nothing_once_the_card_is_gone(cfg, monkeypatch):
    from stenny import claude_setup
    monkeypatch.setattr(claude_setup, "ai_status", lambda c: pytest.fail("checked"))
    cfg.setup_checklist = False
    assert app_server.handle(cfg, {"id": 1, "method": "setup_status"})["result"] == {"show": False}
