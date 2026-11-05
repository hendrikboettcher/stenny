"""Recording through Stenny's app: `stenny app-server`'s start/status/command and the consent answers."""
import json
import plistlib
import subprocess
from types import SimpleNamespace

import pytest

from stenny import app_live, paths
from stenny.live import control


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SYSTEM_DIR", tmp_path)
    monkeypatch.setattr("stenny.jobs.QUEUE_DIR", tmp_path / "_Queue")
    monkeypatch.setattr(control, "send", lambda *a, **k: (_ for _ in ()).throw(control.NoSession("none")))
    app_live._started.clear()
    yield tmp_path
    app_live._started.clear()


class FakeProcess:
    def __init__(self, args, **kw):
        self.args, self.returncode = args, None
        FakeProcess.last = self

    def poll(self):
        return self.returncode


def test_start_runs_stenny_live_and_reports_starting_then_why_it_failed(home, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    reply = app_live.start(None, {"course": "Calculus", "device": "USB Mic", "draft": False, "llm": False})
    assert reply["state"] == "starting"
    assert FakeProcess.last.args[2:] == ["stenny", "live", "--course", "Calculus", "--device", "USB Mic",
                                          "--no-draft", "--no-llm"]
    assert app_live.status(None, {}) == {"ok": True, "state": "starting", "course": "Calculus"}
    with pytest.raises(app_live.LiveError, match="already running"):
        app_live.start(None, {"course": "Calculus"})

    (home / "live.log").write_text("Loading …\nThe microphone 'USB Mic' isn't connected.\n", encoding="utf-8")
    FakeProcess.last.returncode = 1
    assert app_live.status(None, {})["error"] == "The microphone 'USB Mic' isn't connected."
    assert app_live.status(None, {}) == {"ok": True, "state": "idle", "queued": 0}  # said once


def test_a_talk_starts_with_its_title_and_one_target_is_required(home, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    app_live.start(None, {"event": "Guest talk"})
    assert FakeProcess.last.args[2:] == ["stenny", "live", "--event", "Guest talk"]
    app_live._started.clear()
    with pytest.raises(app_live.LiveError, match="either a course or a talk"):
        app_live.start(None, {"course": "A", "event": "B"})


def test_commands_need_a_running_recording(home):
    with pytest.raises(app_live.LiveError, match="No recording is running"):
        app_live.command(None, {"cmd": "pause"})
    with pytest.raises(app_live.LiveError, match="Unknown command"):
        app_live.command(None, {"cmd": "explode"})


def test_consent_takes_over_the_swift_apps_answers_once(home, monkeypatch):
    earlier = plistlib.dumps({"lecturerAgreed": ["record/course/Calculus", "share/event/Guest talk"]})
    calls = []

    def defaults(args, **kw):
        calls.append(args)
        return SimpleNamespace(stdout=earlier)

    monkeypatch.setattr(subprocess, "run", defaults)
    assert app_live.consent(None, {"use": "record", "course": "Calculus"}) == {"given": True}
    assert app_live.consent(None, {"use": "share", "course": "Guest talk", "event": True}) == {"given": True}
    assert app_live.consent(None, {"use": "share", "course": "Calculus"}) == {"given": False}
    app_live.give_consent(None, {"use": "share", "course": "Calculus"})
    assert app_live.consent(None, {"use": "share", "course": "Calculus"}) == {"given": True}
    assert len(calls) == 1  # the Swift app's defaults are read only the first time
    assert "share/course/Calculus" in json.loads((home / "consent.json").read_text(encoding="utf-8"))


def test_feedback_needs_words_and_says_when_stenny_eu_is_unreachable(home, monkeypatch):
    with pytest.raises(app_live.LiveError, match="Write a few words"):
        app_live.send_feedback(None, {"message": "  "})
    sent = []

    def curl(args, input=None, **kw):
        sent.append(json.loads(input))
        return SimpleNamespace(returncode=6, stdout="")  # 6: couldn't resolve the host

    monkeypatch.setattr(subprocess, "run", curl)
    with pytest.raises(app_live.LiveError, match="Couldn't reach stenny.eu"):
        app_live.send_feedback(None, {"message": "The clock froze", "contact": "a@b.c"})
    assert sent == [{"source": "app", "message": "The clock froze", "contact": "a@b.c"}]  # no details unless ticked
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=22, stdout='{"error": "Too long."}'))
    with pytest.raises(app_live.LiveError, match="Too long."):
        app_live.send_feedback(None, {"message": "x"})
