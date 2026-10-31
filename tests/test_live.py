"""Live session logic with a fake microphone and a simulated clock (no audio hardware, no model)."""

import json
import threading
import time
from pathlib import Path

import numpy as np

from stenny import resources
from stenny.config import Config, Course
from stenny.live import session as live_session
from stenny.live.session import LiveSession
from stenny.pipeline import LiveInfo
from stenny.textproc import SAMPLE_RATE

BLOCK = SAMPLE_RATE // 10


class FakeMic:
    """Writes speech-like noise to the recording file like ffmpeg does, with a 0.5 s pause every 4 s.
    A background clock advances simulated time (0.1 s per ~1 ms) whether or not it is recording."""

    name, clock_rate, restartable = "fake mic", 1.0, True
    microphone = "Alex’s iPhone Microphone"

    def __init__(self, startup_blocks=0, dropout=None, zeros=None):
        self.t, self.running, self.rng, self.out = 0.0, False, np.random.default_rng(0), None
        self.startup_blocks, self.warming = startup_blocks, 0
        self.dropout = dropout  # (start, end): simulated time in which the device delivers nothing
        self.zeros = zeros      # (start, end): the device delivers exact digital silence (iPhone locked)
        self.finished = False
        self.twins = []         # FakeBackup microphones hearing the same room on the same clock
        self.lock = threading.Lock()
        threading.Thread(target=self._clock, daemon=True).start()

    def _clock(self):
        while True:
            time.sleep(0.001)
            with self.lock:
                self.t += 0.1
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

    def stop(self):
        with self.lock:
            self.running = False
            if self.out:
                self.out.close()
                self.out = None

    def error(self):
        return ""


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

    def quick(self, audio, language, prompt=None):
        return f"vorläufig{len(audio) // BLOCK}", -0.2


def wait_until(cond, timeout=10):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        time.sleep(0.005)


def test_pause_resume_timeline(tmp_path, monkeypatch):
    (tmp_path / "vault" / "Areas").mkdir(parents=True)
    cfg = Config(vault=tmp_path / "vault", lectures_dir="Areas/Lectures", data_dir=tmp_path / "audio")
    mic = FakeMic(startup_blocks=30)  # 3 s until the first audio, on start and on every resume
    s = LiveSession(cfg, Course("Calculus", language="fr"), mic, FakeEngine())
    monkeypatch.setattr(s, "now", lambda: mic.t)  # lecture clock = simulated time
    monkeypatch.setattr(live_session, "POLL", 0.002)

    def script():
        wait_until(lambda: mic.t >= 10)
        s.command("toggle")            # pause at ~10 s
        wait_until(lambda: mic.t >= 15)
        s.command("toggle")            # resume at ~15 s
        wait_until(lambda: mic.t >= 20)
        s.command("stop")

    threading.Thread(target=script, daemon=True).start()
    s.run()

    level = s.command("level")
    assert level["ok"] and set(level) == {"ok", "state", "input_lost", "level_db"}
    (p_start, p_end, reason), = s.pauses
    assert reason == "paused"
    # the test thread reacts a little after the simulated clock passes each mark
    assert 9.9 <= p_start <= 11 and 14.9 <= p_end <= 16
    # each span starts when audio actually arrived: 3 s after start / resume
    assert s.spans[0][0] == 0 and 2.9 <= s.spans[0][1] <= 3.3
    assert 2.9 <= s.spans[1][1] - p_end <= 3.3
    recorded = s.recorded / SAMPLE_RATE
    audible = (p_start - s.spans[0][1]) + (s.ended_at - s.spans[1][1])
    assert abs(recorded - audible) < 0.3                       # paused (and not yet started) audio is absent
    assert s.pcm_path.stat().st_size == s.recorded * 2         # everything recorded is on disk
    assert s.read_pos == s.recorded                            # and all of it reached the live draft
    # segments: nothing inside the pause, and together they cover the recorded audio
    assert not any(p_start + 0.05 < seg["start"] < s.spans[1][1] - 0.05 for seg in s.segments)
    assert min(seg["start"] for seg in s.segments) >= s.spans[0][1] - 0.01
    covered = sum(seg["end"] - seg["start"] for seg in s.segments)
    assert abs(covered - recorded) < 0.5
    # chunks are cut at the quiet stretches, so none is longer than the max
    assert all(seg["end"] - seg["start"] <= live_session.MAX_CHUNK + 0.2 for seg in s.segments)

    draft = s.transcript_path.read_text()
    marker = f"*[Recording paused 0:00:{int(p_start):02d}–0:00:{int(p_end):02d}]*"
    assert 'status: "live"' in draft and marker in draft
    assert 'microphone: "Alex’s iPhone Microphone"' in draft

    info = LiveInfo.load(s.session_path)
    assert info.folder == s.folder.name and len(info.spans) == 2 and len(info.pauses) == 1
    assert info.microphone == "Alex’s iPhone Microphone"           # the final pass keeps it


def test_microphone_dropout_keeps_session_and_marks_gap(tmp_path, monkeypatch):
    (tmp_path / "vault" / "Areas").mkdir(parents=True)
    cfg = Config(vault=tmp_path / "vault", lectures_dir="Areas/Lectures", data_dir=tmp_path / "audio")
    mic = FakeMic(dropout=(8.0, 14.0))
    s = LiveSession(cfg, Course("Calculus", language="fr"), mic, FakeEngine())
    monkeypatch.setattr(s, "now", lambda: mic.t)
    # real seconds: one simulated 0.1 s block takes ~1 ms here
    monkeypatch.setattr(live_session, "STALL", 0.05)
    monkeypatch.setattr(live_session, "RETRY", 0.01)
    monkeypatch.setattr(live_session, "POLL", 0.002)

    def script():
        wait_until(lambda: mic.t >= 20)
        s.command("stop")

    threading.Thread(target=script, daemon=True).start()
    s.run()

    gaps = [p for p in s.pauses if p[2] == "no input"]
    assert len(gaps) == 1, s.pauses
    start, end, _ = gaps[0]
    assert 7.9 <= start <= 8.2 and 13.9 <= end <= 14.6
    assert s.state == "stopping"                                    # the session did not end by itself
    assert any(seg["start"] >= 13.9 for seg in s.segments)          # transcription continued afterwards
    assert not any(start + 0.05 < seg["start"] < end - 0.05 for seg in s.segments)
    draft = s.transcript_path.read_text()
    assert "No audio from the microphone 0:00:08" in draft


def run_session(tmp_path, monkeypatch, mic, until=20.0, stall=0.05, backup=None, script=None, provisional=False):
    (tmp_path / "vault" / "Areas").mkdir(parents=True, exist_ok=True)
    cfg = Config(vault=tmp_path / "vault", lectures_dir="Areas/Lectures", data_dir=tmp_path / "audio")
    s = LiveSession(cfg, Course("Calculus", language="fr"), mic, FakeEngine(), backup=backup, provisional=provisional)
    monkeypatch.setattr(s, "now", lambda: mic.t)
    monkeypatch.setattr(live_session, "STALL", stall)
    monkeypatch.setattr(live_session, "RETRY", 0.01)
    monkeypatch.setattr(live_session, "POLL", 0.002)
    script = script or (lambda s: (wait_until(lambda: mic.t >= until), s.command("stop")))
    threading.Thread(target=script, args=(s,), daemon=True).start()
    s.run()
    return s


def test_digital_silence_is_a_gap_not_recorded(tmp_path, monkeypatch):
    mic = FakeMic(zeros=(8.0, 13.0))
    s = run_session(tmp_path, monkeypatch, mic)
    gaps = [p for p in s.pauses if p[2] == "no input"]
    assert len(gaps) == 1 and 7.9 <= gaps[0][0] <= 8.2 and 12.9 <= gaps[0][1] <= 13.3
    recorded = s.recorded / SAMPLE_RATE
    assert abs(recorded - s.ended_at) < 0.3                                 # zeros stay in the file: timeline intact
    later = [seg for seg in s.segments if seg["start"] > 10]
    assert later and min(seg["start"] for seg in later) >= 12.9            # text after the gap keeps clock time
    assert "No audio from the microphone 0:00:08" in s.transcript_path.read_text()


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
        wait_until(lambda: mic.t >= 20)
        s.command("pause")
        wait_until(lambda: mic.t >= 25)
        s.command("resume")
        wait_until(lambda: mic.t >= 30)
        s.command("stop")

    s = run_session(tmp_path, monkeypatch, mic, backup=backup, script=script)
    assert [p[2] for p in s.pauses] == ["no input", "paused"]
    b = s.backup
    assert len(b.spans) == 2 and b.spans[0][1] < 0.3                  # started with the main mic
    (p0, p1, _) = s.pauses[1]
    assert abs(b.spans[1][1] - p1) < 0.3                               # off while paused, back on resume
    recorded = b.written / SAMPLE_RATE
    assert abs(recorded - (s.ended_at - (p1 - p0))) < 0.5              # ran through the main mic's gap
    info = json.loads(s.session_path.read_text())["backup"]
    assert info["microphone"] == "MacBook Air Microphone" and info["recorded_samples"] == b.written
    assert Path(info["recording"]).stat().st_size == b.written * 2


def test_gap_at_the_end_is_kept_when_a_backup_heard_it(tmp_path, monkeypatch):
    mic = FakeMic(dropout=(12.0, 10**9))      # main microphone lost for the rest of the lecture
    s = run_session(tmp_path, monkeypatch, mic, backup=FakeBackup(mic))
    (start, end, reason), = s.pauses
    assert reason == "no input" and 11.9 <= start <= 12.2 and end == s.ended_at and s.ended_at >= 19.9
    assert s.backup.written / SAMPLE_RATE >= 19.5


def test_provisional_text_shows_the_chunk_in_progress_and_is_replaced(tmp_path, monkeypatch):
    mic = FakeMic()
    seen = []

    def script(s):
        while mic.t < 20:
            with s.lock:
                if s.provisional:
                    seen.append((s.provisional, s.buffer_start, len(s.segments)))
            time.sleep(0.001)
        s.command("stop")

    monkeypatch.setattr(live_session, "MIN_CHUNK", 6.0)
    s = run_session(tmp_path, monkeypatch, mic, script=script, provisional=True)
    assert seen, "no provisional text appeared"
    blocks = [int(text.removeprefix("vorläufig")) for (_, text), _, _ in seen]
    assert min(blocks) >= live_session.PROVISIONAL_MIN * 10                 # not before 3 s of chunk audio
    assert len(set(blocks)) >= 2                                            # it moved within a chunk
    starts = {t for (t, _), _, _ in seen}
    assert len(starts) >= 2                                                 # and followed the next chunk
    assert s.provisional is None                                            # gone once all is transcribed
    draft = s.transcript_path.read_text()
    assert "vorläufig" not in draft and "…*" not in draft


def test_provisional_line_is_rendered_in_italics(tmp_path, monkeypatch):
    mic = FakeMic()
    s = run_session(tmp_path, monkeypatch, mic, until=8.0)
    s.provisional = (8.0, "what is being said")
    s.render()
    assert "*what is being said …*" in s.transcript_path.read_text()


def test_provisional_line_is_off_by_default(tmp_path, monkeypatch):
    mic = FakeMic()
    s = run_session(tmp_path, monkeypatch, mic, until=12.0)
    assert not s.provisional_on and "vorläufig" not in s.transcript_path.read_text()


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
        wait_until(lambda: mic.t >= 12)
        replies.append(s.command("microphone", "MacBook Air Microphone"))
        replies.append(s.command("microphone", None))
        wait_until(lambda: mic.t >= 20)
        s.command("stop")

    s = run_session(tmp_path, monkeypatch, mic, script=script)
    assert replies[0]["ok"] and replies[0]["microphone"] == "MacBook Air Microphone"
    assert not replies[1]["ok"]                                     # a switch needs a name
    gaps = [p for p in s.pauses if p[2] == "no input"]
    assert len(gaps) == 1 and 7.9 <= gaps[0][0] <= 8.2 and 11.9 <= gaps[0][1] <= 12.6
    assert any(seg["start"] >= 12.5 for seg in s.segments)          # transcribed from the new microphone
    assert LiveInfo.load(s.session_path).microphone == "MacBook Air Microphone"


def fake_resources(monkeypatch, disk=10 * resources.GB, pressure=resources.PRESSURE_NORMAL):
    state = {"disk": disk, "pressure": pressure}
    monkeypatch.setattr(resources, "free_disk", lambda path: state["disk"])
    monkeypatch.setattr(resources, "memory_pressure", lambda: state["pressure"])
    monkeypatch.setattr(live_session, "RESOURCE_CHECK", 0.0)
    return state


def test_low_memory_pauses_the_draft_but_never_the_recording(tmp_path, monkeypatch):
    state = fake_resources(monkeypatch)
    monkeypatch.setattr(live_session, "DRAFT_RESUME", 0.0)
    mic = FakeMic()
    released = []

    def script(s):
        wait_until(lambda: mic.t >= 10)
        state["pressure"] = 2
        wait_until(lambda: s.draft_paused and s.warning)
        s.engine.release = lambda: released.append(True)
        before = len(s.segments)
        wait_until(lambda: mic.t >= 25)
        s.seen = before, len(s.segments), s.recorded
        state["pressure"] = 1
        wait_until(lambda: not s.draft_paused)
        wait_until(lambda: mic.t >= 45)
        s.command("stop")

    s = run_session(tmp_path, monkeypatch, mic, script=script)
    before, during, recorded = s.seen
    assert during - before <= 2                      # at most what was on its way when memory ran low
    assert recorded / SAMPLE_RATE >= 24              # the recording went on
    assert s.recorded / SAMPLE_RATE >= 44
    assert len(s.segments) > during                  # and the draft came back
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
        wait_until(lambda: mic.t >= 14)
        state["disk"] = resources.DISK_FULL - 1

    s = run_session(tmp_path, monkeypatch, mic, backup=backup, script=script)
    assert s.state == "stopping" and "disk is full" in s.final_note
    assert s.recorded / SAMPLE_RATE >= 13
    assert s.session_path.exists()
