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
    for name in ("CONFIG_DIR", "LOGS_DIR"):
        monkeypatch.setattr(paths, name, tmp_path)
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
    assert app_live.status(None, {}) == {"ok": True, "state": "starting", "course": "Calculus", "kind": "lecture"}
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
    app_live.start(None, {"event": "Gastvortrag", "lang": "de"})
    assert FakeProcess.last.args[2:] == ["stenny", "live", "--event", "Gastvortrag", "--lang", "de"]
    app_live._started.clear()
    with pytest.raises(app_live.LiveError, match="English, German and French"):
        app_live.start(None, {"event": "Charla", "lang": "es"})
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


def test_plugged_in_starts_the_queues_scheduled_run_only_with_passes_waiting(home, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    FakeProcess.last = None
    assert app_live.plugged_in(None, {}) == {"started": False}
    assert FakeProcess.last is None
    (home / "_Queue").mkdir()
    (home / "_Queue" / "20261112-134200-lecture.json").write_text("{}", encoding="utf-8")
    assert app_live.plugged_in(None, {}) == {"started": True}
    # the background job's own run: it checks power, the night hours and the lock itself
    assert FakeProcess.last.args[1:] == ["-m", "stenny", "queue", "run", "--scheduled"]


def test_live_log_keeps_the_recording_before_and_feedback_sends_the_end_of_every_log(home, monkeypatch):
    """A recording that died must still be in live.log after the next one starts (that one recovers it), and
    Send Feedback's details carry the newest lines of each log, within what stenny.eu keeps."""
    (home / "live.log").write_text("[02:46] the recording before\n", encoding="utf-8")
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    app_live.start(None, {"course": "Calculus"})
    live = (home / "live.log").read_text(encoding="utf-8")
    assert live.startswith("[02:46] the recording before\n--- ") and live.rstrip().endswith("--course Calculus")

    (home / "queue.log").write_text("".join(f"line {i}\n" for i in range(5000)), encoding="utf-8")
    logs = app_live.recent_logs()
    assert len(logs) <= app_live.FEEDBACK_LOG_CHARS
    assert "== live.log\n[02:46] the recording before" in logs and logs.endswith("line 4999")
    assert "\nline " in logs and "== stenny.log" not in logs  # no empty headings

    sent = []
    monkeypatch.setattr(app_live, "system_details", lambda: "a computer")
    monkeypatch.setattr(subprocess, "run", lambda args, input=None, **kw: sent.append(json.loads(input))
                        or SimpleNamespace(returncode=0, stdout=""))
    app_live.send_feedback(None, {"message": "Stuck", "details": True})
    assert sent[0]["log"] == logs


def test_a_log_that_grew_too_long_keeps_its_newer_half(home):
    (home / "queue.log").write_bytes(b"old line\n" * (app_live.LOG_KEEP // 9 + 10) + b"newest\n")
    with app_live.open_log("queue.log") as log:
        log.write("added\n")
    text = (home / "queue.log").read_text(encoding="utf-8")
    assert len(text) <= app_live.LOG_KEEP // 2 + 10 and text.startswith("old line\n") and text.endswith("newest\nadded\n")
