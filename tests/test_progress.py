"""The final pass's progress for Stenny's window (progress.py): what it reports while it transcribes, tidies and
saves, and that the lecture stays "finalizing" until all of it is done."""

import json
import time
from datetime import datetime

import pytest
from test_pipeline import RATE, FakeEngine, FakeLLM, lf, setup  # noqa: F401  (setup is a fixture)

from stenny import app_server, pipeline, progress
from stenny.config import Course
from stenny.progress import Progress, progress_path, read
from stenny.render import parse_frontmatter


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_bar_counts_every_chunk_and_time_left_only_whispers_own(tmp_path):
    """Chunks from the cache move the bar but not the time left, which waits for a few chunks Whisper did."""
    clock, path = Clock(), tmp_path / "x.progress.json"
    p = Progress(path, duration=400.0, clock=clock)
    second = 100 * RATE
    # two chunks transcribed during the recording, then three new ones
    p.plan([(0, second, True), (second, 2 * second, True), (2 * second, 3 * second, False),
            (3 * second, 4 * second, False), (4 * second, 5 * second, False)])
    assert read(path)["fraction"] == 0 and read(path)["left_s"] is None
    p.chunk(0, second, 100.0, new=False)
    p.chunk(second, 2 * second, 200.0, new=False)
    clock.now += 30  # the first new chunk also loads the model: not counted
    p.chunk(2 * second, 3 * second, 300.0, new=True)
    clock.now += 10
    p.chunk(3 * second, 4 * second, 400.0, new=True)
    assert p.left() is None  # two new chunks: too few to tell
    snap = p.snapshot()
    assert snap["fraction"] == pytest.approx(0.8 * 0.85)
    assert snap["at"] == 400.0 and snap["of"] == 400.0
    clock.now += 10
    p.chunk(4 * second, 5 * second, 500.0, new=True)
    assert p.left() == 0  # all done
    assert p.snapshot()["at"] == 400.0  # never past the lecture's end


def test_time_left_from_measured_speed(tmp_path):
    clock = Clock()
    p = Progress(None, duration=1000.0, clock=clock)
    n = RATE * 60
    p.plan([(i * n, (i + 1) * n, False) for i in range(10)])
    for i in range(4):
        clock.now += 20
        p.chunk(i * n, (i + 1) * n, (i + 1) * 60.0, new=True)
    # 3 chunks in 60 s after the first: 20 s each, 6 to go
    assert p.left() == pytest.approx(120)


def test_writes_are_throttled_but_steps_always_written(tmp_path):
    clock, path = Clock(), tmp_path / "x.progress.json"
    p = Progress(path, duration=100.0, clock=clock)
    p.plan([(i * RATE, (i + 1) * RATE, False) for i in range(100)])
    for i in range(50):  # a burst of cached chunks within a second: nothing written
        p.chunk(i * RATE, (i + 1) * RATE, i + 1.0, new=False)
    assert read(path)["fraction"] == 0
    clock.now += 5
    p.chunk(50 * RATE, 51 * RATE, 51.0, new=False)
    assert read(path)["fraction"] == pytest.approx(0.51 * 0.85)
    p.chunk(51 * RATE, 52 * RATE, 52.0, new=False)
    assert read(path)["fraction"] == pytest.approx(0.51 * 0.85)  # too soon again
    p.step("tidying", 0.5)  # a step is written at once
    assert read(path)["step"] == "tidying"
    assert read(path)["fraction"] == pytest.approx(0.85 + 0.12 * 0.5)


def test_read_drops_stale_or_broken_files(tmp_path):
    path = tmp_path / "x.progress.json"
    assert read(path) is None
    path.write_text("{not json", encoding="utf-8")
    assert read(path) is None
    path.write_text(json.dumps({"step": "transcribing", "fraction": 0.3, "at": 1, "of": 2, "left_s": None,
                                "updated": time.time() - progress.STALE_S - 5}), encoding="utf-8")
    assert read(path) is None  # its pass died
    path.write_text(json.dumps({"step": "nonsense", "fraction": 0.3, "updated": time.time()}), encoding="utf-8")
    assert read(path) is None


def test_a_new_pass_never_shows_an_old_ones_numbers(tmp_path):
    path = tmp_path / "x.progress.json"
    path.write_text(json.dumps({"step": "saving", "fraction": 0.99, "updated": time.time()}), encoding="utf-8")
    Progress(path, duration=10.0)
    assert not path.exists()


def test_final_pass_reports_each_step_and_turns_final_at_the_end(setup, monkeypatch):
    """While Claude tidies up, the transcript is on disk but the lecture is still "finalizing" with the window's
    bar in the tidying step; after the audio is saved it is final and the progress file is gone."""
    cfg, wav = setup
    course = Course("Calculus", language="fr", languages=["fr"])
    cfg.courses = {"Calculus": course}
    folder = cfg.folder / "Calculus" / "2026-10-15"
    seen = {"engine": [], "llm": [], "saving": None}
    path = progress_path(cfg.data_for(course), "2026-10-15")

    class WatchingEngine(FakeEngine):
        def transcribe(self, audio, language, prompt=None, fast=False):
            seen["engine"].append(read(path))
            return super().transcribe(audio, language, prompt, fast)

    class WatchingLLM(FakeLLM):
        def complete(self, system, prompt):
            fm = parse_frontmatter(lf(folder).read_text(encoding="utf-8"))
            entry = next(e for e in app_server.library(cfg, {})["entries"] if e["folder"] == "2026-10-15")
            seen["llm"].append((fm["status"], entry["status"], entry["final_pass"]))
            return super().complete(system, prompt)

    real_archive = pipeline.audio_io.archive

    def archive(src, dest):
        seen["saving"] = read(path)
        return real_archive(src, dest)

    monkeypatch.setattr(pipeline, "get_backend", lambda cfg: WatchingLLM())
    monkeypatch.setattr(pipeline.audio_io, "archive", archive)
    pipeline.process(wav, cfg, course, WatchingEngine({"fr": 1.0}), when=datetime(2026, 10, 15, 9, 15))

    assert all(s and s["step"] == "transcribing" for s in seen["engine"])
    fractions = [s["fraction"] for s in seen["engine"]]
    assert fractions == sorted(fractions) and fractions[-1] < 0.85
    assert len(seen["llm"]) == 2  # the structure, then the summary
    for status, entry_status, bar in seen["llm"]:
        assert status == "finalizing" and entry_status == "finalizing"
        assert bar["step"] == "tidying" and 0.85 <= bar["fraction"] <= 0.97
    assert seen["llm"][0][2]["fraction"] < seen["llm"][1][2]["fraction"]
    assert seen["saving"]["step"] == "saving"

    text = lf(folder).read_text(encoding="utf-8")
    assert parse_frontmatter(text)["status"] == "final"
    assert "No transcript yet" not in text and "## [0:00:00] Introduction" in text
    assert not path.exists()
    meta = json.loads((cfg.data_for(course) / "2026-10-15.segments.json").read_text(encoding="utf-8"))["meta"]
    assert meta["lecture"]["status"] == "final"
    entry = next(e for e in app_server.library(cfg, {})["entries"] if e["folder"] == "2026-10-15")
    assert entry["status"] == "final" and "final_pass" not in entry


def test_a_failure_after_the_transcript_leaves_it_final(setup, monkeypatch):
    """Saving the audio fails: the transcript written so far is the lecture's, never one that looks unfinished."""
    cfg, wav = setup
    course = Course("Calculus", language="fr", languages=["fr"])

    def broken(src, dest):
        raise OSError("disk full")
    monkeypatch.setattr(pipeline.audio_io, "archive", broken)
    with pytest.raises(OSError):
        pipeline.process(wav, cfg, course, FakeEngine({"fr": 1.0}), when=datetime(2026, 10, 15, 9, 15))
    folder = cfg.folder / "Calculus" / "2026-10-15"
    assert parse_frontmatter(lf(folder).read_text(encoding="utf-8"))["status"] == "final"
    assert not progress_path(cfg.data_for(course), "2026-10-15").exists()


def test_a_resumed_pass_starts_its_bar_where_it_was(setup):
    """The charger was pulled after two chunks; next time those come from the cache, so Whisper's first new chunk
    already sees the bar past them, not at 0."""
    cfg, wav = setup
    cfg.keep_audio = False
    course = Course("Calculus", language="fr", languages=["fr"])
    live = pipeline.LiveInfo(started_at=datetime(2026, 10, 15, 9, 15), folder="2026-10-15", spans=[(0, 0.0)],
                             chunks=cfg.data_for(course) / "2026-10-15.chunks.jsonl")
    live.chunks.parent.mkdir(parents=True, exist_ok=True)
    path = progress_path(cfg.data_for(course), "2026-10-15")
    first = FakeEngine({"fr": 1.0})
    with pytest.raises(pipeline.Interrupted):
        pipeline.process(wav, cfg, course, first, use_llm=False, live=live, stop=lambda: first.calls >= 2)
    assert first.calls == 2 and not path.exists()

    seen = []

    class WatchingEngine(FakeEngine):
        def transcribe(self, audio, language, prompt=None, fast=False):
            seen.append(read(path))
            return super().transcribe(audio, language, prompt, fast)
    pipeline.process(wav, cfg, course, WatchingEngine({"fr": 1.0}), use_llm=False, live=live)
    assert seen[0]["fraction"] > 0.2  # two of the three chunks were done before
    assert not path.exists()


def test_an_interrupted_pass_leaves_no_progress(setup):
    cfg, wav = setup
    course = Course("Calculus", language="fr", languages=["fr"])
    with pytest.raises(pipeline.Interrupted):
        pipeline.process(wav, cfg, course, FakeEngine({"fr": 1.0}), when=datetime(2026, 10, 15, 9, 15),
                         stop=lambda: True)
    assert not progress_path(cfg.data_for(course), "2026-10-15").exists()
    assert not (cfg.folder / "Calculus" / "2026-10-15").exists() or not lf(cfg.folder / "Calculus" / "2026-10-15").exists()
