"""Live session logic with a fake microphone and a simulated clock (no audio hardware, no model)."""

import json
import re
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from stenny import jobs, pipeline, resources
from stenny.config import Config, Course
from stenny.live import session as live_session
from stenny.live.session import LiveSession
from stenny.pipeline import ChunkCache, LiveInfo
from stenny.textproc import SAMPLE_RATE

BLOCK = SAMPLE_RATE // 10
EPS = 1e-6  # lecture times come from sample counts: 7.9 s may be 7.8999999999999995


TICK_S = 0.002  # real seconds per simulated block (0.1 s): slow enough that a busy CI runner keeps up


class FakeMic:
    """Writes speech-like noise to the recording file like ffmpeg does, with a 0.5 s pause every 4 s.
    A background clock advances simulated time (0.1 s per ~2 ms) whether or not it is recording, from the moment the
    session first starts it: how long setting up the session took in real time (long on a busy CI runner) never
    shows up as lecture time. It counts whole blocks, so 33 blocks are exactly 3.3 s (adding 0.1 up gives
    3.3000000000000016)."""

    name, clock_rate, restartable = "fake mic", 1.0, True
    microphone = "Alex’s iPhone Microphone"
    made = []  # every FakeMic of the running test: their clocks stop when it ends (stop_clocks)

    def __init__(self, startup_blocks=0, dropout=None, zeros=None):
        self.ticks, self.running, self.rng, self.out = 0, False, np.random.default_rng(0), None
        self.scheduled = []     # (tick, action, result box, done): run by the clock's thread (see at())
        self.startup_blocks, self.warming = startup_blocks, 0
        self.dropout = dropout  # (start, end): simulated time in which the device delivers nothing
        self.zeros = zeros      # (start, end): the device delivers exact digital silence (iPhone locked)
        self.finished = False
        self.twins = []         # FakeBackup microphones hearing the same room on the same clock
        self.lock = threading.Lock()
        self.started = threading.Event()  # the clock stands at 0 until the session starts the microphone
        self.closed = False
        FakeMic.made.append(self)
        threading.Thread(target=self._clock, daemon=True).start()

    @property
    def t(self) -> float:
        return self.ticks / 10

    def at(self, t, action):
        """Runs `action` (a command to the session) when the simulated clock reaches t. The clock's own thread runs
        it, between two ticks: it happens at t however late the test's thread would get to run (a busy CI runner,
        Windows' scheduler), and the clock doesn't stand still waiting for it, which would look like a lost
        microphone. Returns what the action returned."""
        done, box = threading.Event(), {}
        with self.lock:
            self.scheduled.append((round(t * 10), action, box, done))
        if not done.wait(30):
            raise TimeoutError(f"the clock never reached {t} s")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def _run_due(self):
        with self.lock:
            due = [job for job in self.scheduled if job[0] <= self.ticks]
            self.scheduled = [job for job in self.scheduled if job[0] > self.ticks]
        for _, action, box, done in due:  # outside the lock: the session stops and starts the microphone
            try:
                box["result"] = action()
            except Exception as e:  # noqa: BLE001 — raised again in the test's thread
                box["error"] = e
            done.set()

    def _clock(self):
        self.started.wait()
        while not self.closed:
            time.sleep(TICK_S)
            self._run_due()
            with self.lock:
                self.ticks += 1
                for twin in self.twins:  # the backup keeps delivering through the main mic's gaps
                    if twin.out:
                        twin.out.write((self.rng.normal(0, 0.05, BLOCK) * 32767).astype(np.int16).tobytes())
                        twin.out.flush()
                if not self.running or (self.dropout and self.dropout[0] <= self.t < self.dropout[1]):
                    continue
                if self.warming:  # device not delivering audio yet (e.g. permission prompt)
                    self.warming -= 1
                    continue
                if self.zeros and self.zeros[0] <= self.t < self.zeros[1]:
                    block = np.zeros(BLOCK, np.int16)
                else:
                    quiet = (self.t % 4.0) > 3.5
                    block = (self.rng.normal(0, 0.001 if quiet else 0.1, BLOCK) * 32767).astype(np.int16)
                self.out.write(block.tobytes())
                self.out.flush()

    def start(self, path):
        with self.lock:
            self.out = open(path, "ab")
            self.running, self.warming = True, self.startup_blocks
        self.started.set()

    def stop(self):
        with self.lock:
            self.running = False
            if self.out:
                self.out.close()
                self.out = None

    def error(self):
        return ""


@pytest.fixture(autouse=True)
def stop_clocks():
    """A clock left ticking every 2 ms after its test would compete with the next tests' sessions for Python's
    lock (14 of them by the end of this file), enough on a Windows runner to delay a session's loop past what
    the assertions allow."""
    yield
    for mic in FakeMic.made:
        mic.closed = True
        mic.started.set()  # a clock still waiting for its start ends too
    FakeMic.made.clear()


class FakeBackup:
    """A second microphone driven by a FakeMic's clock."""

    finished = False

    def __init__(self, main, device="MacBook Air Microphone"):
        self.main, self.device, self.out = main, device, None
        main.twins.append(self)

    def start(self, path):
        with self.main.lock:
            self.out = open(path, "ab")

    def stop(self):
        with self.main.lock:
            if self.out:
                self.out.close()
                self.out = None

    def error(self):
        return ""


class FakeEngine:
    def transcribe(self, audio, language, prompt=None, fast=False):
        return [{"start": 0.0, "end": len(audio) / SAMPLE_RATE, "text": f"mot{len(audio) // BLOCK}",
                 "no_speech_prob": 0.0, "avg_logprob": -0.1}]

    def language_probs(self, clip, allowed):
        return {a: 1.0 / len(allowed) for a in allowed}


class SkippingEngine(FakeEngine):
    """Whisper on a bad day: in a long clip it writes the first second, skips 6 s and returns "..." for the rest,
    so the second look fills the gap and transcribes the near-empty rest again in short pieces."""

    def transcribe(self, audio, language, prompt=None):
        dur = len(audio) / SAMPLE_RATE
        if dur >= 12:
            return [{"start": 0.0, "end": 1.0, "text": "Bonjour", "avg_logprob": -0.1},
                    {"start": 7.0, "end": dur, "text": "...", "avg_logprob": -0.1}]
        return [{"start": 0.0, "end": dur, "text": " mot" * int(dur * 3), "avg_logprob": -0.1}]


def short_chunks(s, monkeypatch, on_power=True, lengths=(5.0, 3.0)):
    """Chunks of 3-5 s instead of 18-28 s, so a recording of a few simulated seconds has some. The session takes
    the final pass's chunk lengths, so the final pass in assert_final_pass_reuses gets the same."""
    s.max_len, s.min_len = lengths
    monkeypatch.setattr(pipeline, "chunk_lengths", lambda course, lang=None: lengths)
    s.on_power = on_power
    monkeypatch.setattr(jobs, "on_ac_power", lambda: s.on_power_now)
    s.on_power_now = on_power


def assert_final_pass_reuses(s):
    """Every chunk transcribed during the recording is one the final pass makes (same samples, same language),
    so it takes it as it is: the final transcript is the same as transcribing everything after the lecture."""
    info = LiveInfo.load(s.session_path)
    audio = np.fromfile(s.pcm_path, np.int16).astype(np.float32) / 32768.0
    cache = ChunkCache(info.chunks)
    made = {(*k, "chunk") for k in cache.chunks} | set(cache.others)
    assert cache.chunks, "nothing was transcribed during the recording"
    asked = []
    get = cache.get
    cache.get = lambda a, b, lang, kind="chunk": (asked.append((a, b, lang, kind)), get(a, b, lang, kind))[1]
    gaps = [(p[0], p[1]) for p in info.pauses if p[2] == "no input"]
    pipeline.transcribe(audio, s.course, type(s.engine)(), s.prompt, None, info.spans, skip=gaps, cache=cache)
    assert made <= set(asked), sorted(made - set(asked))
    return made


def wait_until(cond, timeout=10):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        time.sleep(0.005)


def test_pause_resume_timeline(tmp_path, monkeypatch):
    (tmp_path / "vault" / "Areas").mkdir(parents=True)
    cfg = Config(folder=tmp_path / "vault" / "Areas/Lectures", data_dir=tmp_path / "audio")
    mic = FakeMic(startup_blocks=30)  # 3 s until the first audio, on start and on every resume
    s = LiveSession(cfg, Course("Calculus", language="fr"), mic, FakeEngine())
    short_chunks(s, monkeypatch)
    monkeypatch.setattr(s, "now", lambda: mic.t)  # lecture clock = simulated time
    monkeypatch.setattr(s, "wall", lambda: mic.ticks * TICK_S)  # stall timers on the same clock
    monkeypatch.setattr(live_session, "POLL", 0.002)

    def script():
        mic.at(14, lambda: s.command("toggle"))            # pause at 14 s
        mic.at(19, lambda: s.command("toggle"))            # resume at 19 s
        mic.at(36, lambda: s.command("stop"))

    threading.Thread(target=script, daemon=True).start()
    s.run()

    level = s.command("level")
    assert level["ok"] and set(level) == {"ok", "state", "input_lost", "level_db"}
    (p_start, p_end, reason), = s.pauses
    assert reason == "paused"
    # the test thread reacts a little after the simulated clock passes each mark
    assert 13.9 - EPS <= p_start <= 15 and 18.9 - EPS <= p_end <= 20
    # each span starts when audio actually arrived: 3 s after start / resume
    assert s.spans[0][0] == 0 and 2.9 - EPS <= s.spans[0][1] <= 3.3 + EPS
    assert 2.9 - EPS <= s.spans[1][1] - p_end <= 3.3 + EPS
    recorded = s.recorded / SAMPLE_RATE
    audible = (p_start - s.spans[0][1]) + (s.ended_at - s.spans[1][1])
    assert abs(recorded - audible) < 0.3                       # paused (and not yet started) audio is absent
    assert s.pcm_path.stat().st_size == s.recorded * 2         # everything recorded is on disk
    assert s.read_pos == s.recorded                            # and all of it was looked at (gaps, level)
    # transcribed during the recording: nothing inside the pause, both stretches, the final pass's chunks
    assert not any(p_start + 0.05 < seg["start"] < s.spans[1][1] - 0.05 for seg in s.segments)
    assert min(seg["start"] for seg in s.segments) >= s.spans[0][1] - 0.01
    assert any(seg["start"] > p_end for seg in s.segments)
    assert all(seg["end"] - seg["start"] <= 5.0 + 0.01 for seg in s.segments)
    assert_final_pass_reuses(s)

    # the vault only has a placeholder: details and status, no text (that's in Stenny's window)
    draft = s.transcript_path.read_text(encoding="utf-8")
    marker = f"*[Recording paused 0:00:{int(p_start):02d}–0:00:{int(p_end):02d}]*"
    assert 'status: "live"' in draft and marker in draft and "No transcript yet" in draft
    assert 'microphone: "Alex’s iPhone Microphone"' in draft
    assert "mot" not in draft and s.text_so_far()

    info = LiveInfo.load(s.session_path)
    assert info.folder == s.folder.name and len(info.spans) == 2 and len(info.pauses) == 1
    assert info.microphone == "Alex’s iPhone Microphone"           # the final pass keeps it


def test_microphone_dropout_keeps_session_and_marks_gap(tmp_path, monkeypatch):
    (tmp_path / "vault" / "Areas").mkdir(parents=True)
    cfg = Config(folder=tmp_path / "vault" / "Areas/Lectures", data_dir=tmp_path / "audio")
    mic = FakeMic(dropout=(8.0, 14.0))
    s = LiveSession(cfg, Course("Calculus", language="fr"), mic, FakeEngine())
    short_chunks(s, monkeypatch)
    monkeypatch.setattr(s, "now", lambda: mic.t)
    monkeypatch.setattr(s, "wall", lambda: mic.ticks * TICK_S)  # stall timers on the same clock
    # real seconds: one simulated 0.1 s block takes ~1 ms here
    monkeypatch.setattr(live_session, "STALL", 0.05)
    monkeypatch.setattr(live_session, "RETRY", 0.01)
    monkeypatch.setattr(live_session, "POLL", 0.002)

    def script():
        mic.at(30, lambda: s.command("stop"))

    threading.Thread(target=script, daemon=True).start()
    s.run()

    gaps = [p for p in s.pauses if p[2] == "no input"]
    assert len(gaps) == 1, s.pauses
    start, end, _ = gaps[0]
    assert 7.9 - EPS <= start <= 8.2 + EPS and 13.9 - EPS <= end <= 14.6 + EPS
    assert s.state == "stopping"                                    # the session did not end by itself
    assert any(seg["start"] >= 13.9 for seg in s.segments)          # transcription continued afterwards
    assert not any(start + 0.05 < seg["start"] < end - 0.05 for seg in s.segments)
    assert_final_pass_reuses(s)
    draft = s.transcript_path.read_text(encoding="utf-8")
    assert re.search(r"No audio from the microphone 0:00:0[78]–", draft)  # 7.9–8.2 s, printed whole


def run_session(tmp_path, monkeypatch, mic, until=30.0, stall=0.05, backup=None, script=None, on_power=True,
                engine=None, lengths=(5.0, 3.0)):
    (tmp_path / "vault" / "Areas").mkdir(parents=True, exist_ok=True)
    cfg = Config(folder=tmp_path / "vault" / "Areas/Lectures", data_dir=tmp_path / "audio")
    s = LiveSession(cfg, Course("Calculus", language="fr"), mic, engine or FakeEngine(), backup=backup)
    short_chunks(s, monkeypatch, on_power, lengths)
    monkeypatch.setattr(s, "now", lambda: mic.t)
    monkeypatch.setattr(s, "wall", lambda: mic.ticks * TICK_S)  # stall timers on the same clock
    monkeypatch.setattr(live_session, "STALL", stall)
    monkeypatch.setattr(live_session, "RETRY", 0.01)
    monkeypatch.setattr(live_session, "POLL", 0.002)
    script = script or (lambda s: mic.at(until, lambda: s.command("stop")))
    threading.Thread(target=script, args=(s,), daemon=True).start()
    s.run()
    return s


def test_digital_silence_is_a_gap_not_recorded(tmp_path, monkeypatch):
    mic = FakeMic(zeros=(8.0, 13.0))
    s = run_session(tmp_path, monkeypatch, mic)
    gaps = [p for p in s.pauses if p[2] == "no input"]
    assert len(gaps) == 1 and 7.9 - EPS <= gaps[0][0] <= 8.2 + EPS and 12.9 - EPS <= gaps[0][1] <= 13.3 + EPS
    recorded = s.recorded / SAMPLE_RATE
    assert abs(recorded - s.ended_at) < 0.5                                 # zeros stay in the file: timeline intact (± start-up time)
    later = [seg for seg in s.segments if seg["start"] > 10]
    assert later and min(seg["start"] for seg in later) >= 12.9            # text after the gap keeps clock time
    assert_final_pass_reuses(s)                                            # no chunk reached into the zeros
    assert re.search(r"No audio from the microphone 0:00:0[78]–", s.transcript_path.read_text(encoding="utf-8"))


def test_delivery_hiccup_keeps_timestamps_on_the_clock(tmp_path, monkeypatch):
    mic = FakeMic(dropout=(8.0, 11.0))  # 3 s without blocks, shorter than the stall limit
    s = run_session(tmp_path, monkeypatch, mic, stall=60)
    assert not s.pauses                                                    # no gap marker for a hiccup
    assert any(abs(t - 11.0) < 0.3 for _, t in s.spans[1:])               # timeline re-anchored at 11 s
    assert abs(s.ended_at - mic.t) < 0.5                                  # end time matches the clock


def test_recorder_that_never_delivers_is_restarted(tmp_path, monkeypatch):
    mic = FakeMic(startup_blocks=10**9)  # e.g. a recorder that exits/hangs right after starting
    starts = []
    real_start = mic.start
    def counting_start(path):
        starts.append(mic.t)
        real_start(path)
        if len(starts) >= 3:  # the third attempt works
            mic.warming = 0
    mic.start = counting_start
    monkeypatch.setattr(live_session, "STARTUP_STALL", 0.05)
    s = run_session(tmp_path, monkeypatch, mic, until=25.0)
    assert len(starts) >= 3                                   # it kept trying instead of waiting forever
    gaps = [p for p in s.pauses if p[2] == "no input"]
    assert len(gaps) == 1 and gaps[0][0] == 0.0               # the lost start is a marked gap
    assert s.recorded > 0 and any(seg["start"] >= gaps[0][1] - 0.1 for seg in s.segments)


def test_backup_microphone_records_through_main_gaps_but_not_pauses(tmp_path, monkeypatch):
    mic = FakeMic(dropout=(8.0, 14.0))
    backup = FakeBackup(mic)

    def script(s):
        mic.at(20, lambda: s.command("pause"))
        mic.at(25, lambda: s.command("resume"))
        mic.at(30, lambda: s.command("stop"))

    s = run_session(tmp_path, monkeypatch, mic, backup=backup, script=script)
    assert [p[2] for p in s.pauses] == ["no input", "paused"]
    b = s.backup
    # started with the main mic, long before its gap at 8 s; Windows' ~15 ms timer tick alone is ~0.8 s simulated
    assert len(b.spans) == 2 and b.spans[0][1] < 1.0
    (p0, p1, _) = s.pauses[1]
    assert abs(b.spans[1][1] - p1) < 0.3                               # off while paused, back on resume
    recorded = b.written / SAMPLE_RATE
    assert abs(recorded - (s.ended_at - (p1 - p0))) < 0.5              # ran through the main mic's gap
    info = json.loads(s.session_path.read_text(encoding="utf-8"))["backup"]
    assert info["microphone"] == "MacBook Air Microphone" and info["recorded_samples"] == b.written
    assert Path(info["recording"]).stat().st_size == b.written * 2


def test_gap_at_the_end_is_kept_when_a_backup_heard_it(tmp_path, monkeypatch):
    mic = FakeMic(dropout=(12.0, 10**9))      # main microphone lost for the rest of the lecture
    s = run_session(tmp_path, monkeypatch, mic, backup=FakeBackup(mic))
    (start, end, reason), = s.pauses
    assert reason == "no input" and 11.9 - EPS <= start <= 12.2 + EPS and end == s.ended_at and s.ended_at >= 19.9
    assert s.backup.written / SAMPLE_RATE >= 19.5


def test_the_second_look_is_done_during_the_recording_too(tmp_path, monkeypatch):
    mic = FakeMic()
    s = run_session(tmp_path, monkeypatch, mic, until=80.0, engine=SkippingEngine(), lengths=(14.0, 12.0))
    kinds = {k[3] for k in s.cache.others}
    assert kinds == {"gap", "retry"}, kinds          # skipped stretches filled, "..." transcribed again
    made = assert_final_pass_reuses(s)               # and the final pass takes all of it from there
    assert sum(1 for k in made if k[3] != "chunk") >= 4


def test_on_battery_it_only_records_and_catches_up_when_plugged_in(tmp_path, monkeypatch):
    mic = FakeMic()

    def script(s):
        wait_until(lambda: mic.t >= 20)
        s.seen = len(s.cache.chunks), s.status()["transcribing"]
        s.on_power_now = True                         # plugged in
        monkeypatch.setattr(live_session, "POWER_CHECK", 0.0)
        mic.at(40, lambda: s.command("stop"))

    s = run_session(tmp_path, monkeypatch, mic, script=script, on_power=False)
    assert s.seen == (0, False)                       # on battery: nothing transcribed, the recording goes on
    assert min(seg["start"] for seg in s.segments) < 1.0   # plugged in: it caught up from the start
    assert_final_pass_reuses(s)


class SwitchableMic(FakeMic):
    """AirPods that drop out for good at 8 s; the MacBook microphone works. Switching changes `device`."""

    def __init__(self):
        super().__init__(dropout=(8.0, 1e9))
        self._device = "AirPods"
        self.name, self.microphone = "mic (AirPods)", "AirPods"

    @property
    def device(self):
        return self._device

    @device.setter
    def device(self, value):
        self._device = value
        self.dropout = (8.0, 1e9) if value == "AirPods" else None


def test_switching_the_microphone_ends_the_gap_and_keeps_recording(tmp_path, monkeypatch):
    mic = SwitchableMic()
    replies = []

    def script(s):
        mic.at(12, lambda: replies.append(s.command("microphone", "MacBook Air Microphone")))
        replies.append(s.command("microphone", None))
        mic.at(30, lambda: s.command("stop"))

    s = run_session(tmp_path, monkeypatch, mic, script=script)
    assert replies[0]["ok"] and replies[0]["microphone"] == "MacBook Air Microphone"
    assert not replies[1]["ok"]                                     # a switch needs a name
    gaps = [p for p in s.pauses if p[2] == "no input"]
    assert len(gaps) == 1 and 7.9 - EPS <= gaps[0][0] <= 8.2 + EPS and 11.9 - EPS <= gaps[0][1] <= 12.6 + EPS
    assert any(seg["start"] >= 12.5 for seg in s.segments)          # transcribed from the new microphone
    assert_final_pass_reuses(s)
    assert LiveInfo.load(s.session_path).microphone == "MacBook Air Microphone"


def fake_resources(monkeypatch, disk=10 * resources.GB, pressure=resources.PRESSURE_NORMAL):
    state = {"disk": disk, "pressure": pressure}
    monkeypatch.setattr(resources, "free_disk", lambda path: state["disk"])
    monkeypatch.setattr(resources, "memory_pressure", lambda: state["pressure"])
    monkeypatch.setattr(live_session, "RESOURCE_CHECK", 0.0)
    return state


def test_low_memory_pauses_transcription_but_never_the_recording(tmp_path, monkeypatch):
    state = fake_resources(monkeypatch)
    monkeypatch.setattr(live_session, "DRAFT_RESUME", 0.0)
    mic = FakeMic()
    released = []

    def script(s):
        wait_until(lambda: mic.t >= 10 and s.cache.chunks)  # the model is loaded
        state["pressure"] = 2
        wait_until(lambda: s.draft_paused and s.warning)
        s.engine.release = lambda: released.append(True)
        before = len(s.segments)
        # the session reads what the clock wrote a little later (much later on a busy runner): wait for both
        wait_until(lambda: mic.t >= 25 and s.recorded >= 24 * SAMPLE_RATE)
        s.seen = before, len(s.segments), s.recorded
        state["pressure"] = 1
        wait_until(lambda: not s.draft_paused)
        mic.at(45, lambda: s.command("stop"))

    s = run_session(tmp_path, monkeypatch, mic, script=script)
    before, during, recorded = s.seen
    assert during - before <= 2                      # at most what was on its way when memory ran low
    assert recorded / SAMPLE_RATE >= 24              # the recording went on
    assert s.recorded / SAMPLE_RATE >= 44
    assert len(s.segments) > during                  # and transcription came back
    assert released == [True]
    assert not s.warning


def test_low_disk_stops_the_backup_and_a_full_disk_ends_cleanly(tmp_path, monkeypatch):
    state = fake_resources(monkeypatch)
    mic = FakeMic()
    backup = FakeBackup(mic)

    def script(s):
        wait_until(lambda: mic.t >= 8)
        state["disk"] = resources.DISK_LOW - 1
        wait_until(lambda: s.disk_low)
        assert s.warning == "Disk almost full" and not s.backup.running
        # the session reads what the clock wrote a little later (much later on a busy runner): wait for both
        wait_until(lambda: mic.t >= 14 and s.recorded >= 13 * SAMPLE_RATE)
        state["disk"] = resources.DISK_FULL - 1

    s = run_session(tmp_path, monkeypatch, mic, backup=backup, script=script)
    assert s.state == "stopping" and "disk is full" in s.final_note
    assert s.recorded / SAMPLE_RATE >= 13
    assert s.session_path.exists()


def test_virtual_devices_are_not_offered_as_microphones(monkeypatch):
    from types import SimpleNamespace

    from stenny.live import sources

    profile = """Audio:

    Devices:

        Alex's iPhone Microphone:

          Input Channels: 1
          Transport: Unknown

        Built-in Microphone:

          Default Input Device: Yes
          Input Channels: 1
          Transport: Built-in

        Microsoft Teams Audio:

          Input Channels: 1
          Transport: Virtual
"""
    listing = ("AVFoundation audio devices:\n[x] [0] Microsoft Teams Audio\n"
               "[x] [1] Alex's iPhone Microphone\n[x] [2] Built-in Microphone\n")
    monkeypatch.setattr(sources.subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        stdout=profile if cmd[0] == "system_profiler" else "", stderr=listing))
    assert sources.input_devices() == ["Alex's iPhone Microphone", "Built-in Microphone"]


def test_cancel_ends_without_a_transcript_and_trashes_the_recording(tmp_path, monkeypatch):
    import shutil
    from types import SimpleNamespace

    from stenny import cli, manage
    (tmp_path / "vault" / "Areas").mkdir(parents=True)
    cfg = Config(folder=tmp_path / "vault" / "Areas/Lectures", data_dir=tmp_path / "audio")
    mic = FakeMic()
    s = LiveSession(cfg, Course("Calculus", language="fr"), mic, FakeEngine())
    short_chunks(s, monkeypatch)
    monkeypatch.setattr(s, "now", lambda: mic.t)
    monkeypatch.setattr(s, "wall", lambda: mic.ticks * TICK_S)  # stall timers on the same clock
    monkeypatch.setattr(live_session, "POLL", 0.002)
    trashed = tmp_path / "Trash"
    trashed.mkdir()
    monkeypatch.setattr(manage, "trash", lambda items: [shutil.move(p, trashed / p.name) for p in items if p.exists()])

    def script():
        mic.at(8, lambda: s.command("cancel"))

    threading.Thread(target=script, daemon=True).start()
    s.run()
    assert s.cancelled and s.state == "cancelling" and s.recorded > 0
    queued = []
    monkeypatch.setattr(jobs, "enqueue", lambda *a, **k: queued.append(a))
    cli.discard(s, cfg)
    assert not queued
    assert not (cfg.folder / "Calculus").exists()       # the placeholder (and its now empty course)
    assert not list((tmp_path / "audio" / "Recordings" / "Calculus").iterdir())
    names = {p.name for p in trashed.iterdir()}
    assert {s.folder.name, s.pcm_path.name, s.session_path.name} <= names
