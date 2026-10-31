"""Live mode: record from a source, transcribe short chunks into a live draft in the vault,
pause/resume on request, and hand the full recording to the offline pipeline afterwards.

Timekeeping: the recording only contains non-paused audio. `spans` maps it back to lecture
time: each span is (first recorded sample, lecture time in seconds when it started).
A digital-silence gap (locked iPhone) stays in the recording as zeros and is only marked.

A backup microphone (the Mac's own, when an iPhone is the main one) records into a second file
with its own spans. It is not transcribed live; the final pass fills the main one's gaps from it.
"""

import faulthandler
import json
import os
import queue
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np

from .. import language, resources
from ..config import Config, Course
from ..pipeline import ensure_glossary, lecture_time, log
from ..render import Lecture, time_sections, transcript_md
from ..textproc import SAMPLE_RATE, mark_dropped, to_paragraphs
from ..vault import Vault

# Chunk length for the live draft. Shorter chunks show text sooner, but give Whisper too little
# context with a distant microphone (tested with an iPhone at 3-5 m: 3-12 s chunks produced
# gibberish and loops, 6-15 s coherent text).
MIN_CHUNK = 6.0    # seconds of audio before we look for a pause to cut at
MAX_CHUNK = 15.0   # cut at the quietest moment if nobody pauses for this long
MIN_CONFIDENCE = -1.0  # live draft only: hide segments Whisper itself is unsure about (final pass keeps them)
# Provisional line: while a chunk is still being recorded, its text so far is shown at the end of the draft
# (in italics) and replaced by the real transcription once the chunk is done, so the draft moves every
# ~2 s instead of every ~10 s. Only when the transcriber has nothing else to do.
PROVISIONAL_MIN = 3.0    # seconds of chunk audio before the first provisional text
PROVISIONAL_EVERY = 2.0  # seconds of new audio between provisional updates
QUIET_TAIL = 4     # 100 ms blocks that must be quiet to count as a pause
BLOCK = SAMPLE_RATE // 10  # 100 ms
POLL = 0.1         # seconds between reads of the recording file
STALL = 5.0        # seconds without audio after which a microphone counts as disconnected
STARTUP_STALL = 15.0  # ... and right after (re)starting, before any audio arrived (permission prompt etc.)
SILENT_GAP = 2.0   # seconds of exact digital silence (iPhone locked/asleep) that count as "no input"
DRIFT = 1.0        # re-anchor the timeline when audio arrives this many seconds behind the clock
RETRY = 3.0        # seconds between reconnect attempts


BACKUP_RETRY = 30.0  # seconds between restarts of a backup recorder that stopped
RESOURCE_CHECK = 10.0  # seconds between looks at free disk and memory
DRAFT_RESUME = 120.0   # seconds of normal memory before a draft paused for memory goes on


def rms(block: np.ndarray) -> float:
    return float(np.sqrt(np.mean(block ** 2))) if len(block) else 0.0


class BackupTrack:
    """A second microphone recording alongside the main one. The session only tracks how its
    samples map to lecture time (same rules as the main recording); it runs through the main
    microphone's gaps, which is the point, and stops only when you pause or stop."""

    def __init__(self, source, path: Path, now):
        self.source, self.path, self.now = source, path, now
        self.written = 0
        self.spans: list[tuple[int, float]] = []
        self.waiting = False   # started, no audio yet: the span's time comes from its first audio
        self.running = False
        self.retry_at = 0.0

    def start(self) -> None:
        t = self.now()
        if self.waiting and self.spans and self.spans[-1][0] == self.written:
            self.spans[-1] = (self.written, t)  # the previous start never delivered audio
        else:
            self.spans.append((self.written, t))
        self.waiting, self.running = True, True
        self.source.start(self.path)

    def stop(self) -> None:
        if self.running:
            self.source.stop()
            self.running = False
        self.poll()

    def poll(self) -> None:
        size = self.path.stat().st_size // 2 if self.path.exists() else 0
        new = size - self.written
        if new > 0:
            arrived = self.now() - new / SAMPLE_RATE
            if self.waiting:
                self.waiting = False
                self.spans[-1] = (self.spans[-1][0], max(self.spans[-1][1], arrived))
            elif arrived - lecture_time(self.spans, self.written) > DRIFT:
                self.spans.append((self.written, arrived))
            self.written = size
        elif self.running and self.source.finished and time.monotonic() >= self.retry_at:
            err = self.source.error()
            log(f"Backup microphone {self.source.device} stopped{f' ({err})' if err else ''}; restarting it")
            self.source.stop()
            self.start()
            self.retry_at = time.monotonic() + BACKUP_RETRY

    def info(self) -> dict:
        return {"recording": str(self.path), "microphone": self.source.device, "spans": self.spans,
                "recorded_samples": self.written}


class LiveSession:
    def __init__(self, cfg: Config, course: Course, source, engine, lang: str | None = None, draft: bool = True,
                 backup=None, provisional: bool = False):
        self.cfg, self.course, self.source, self.engine, self.lang_override = cfg, course, source, engine, lang
        self.draft = draft  # False: record only (no live transcription), e.g. to save battery
        # provisional line needs an engine that can decode a short clip quickly (MlxEngine.quick)
        self.provisional_on = draft and provisional and hasattr(engine, "quick")
        self.provisional: tuple[float, str] | None = None  # (lecture time, text) of the chunk in progress
        self.provisional_at = 0                             # recorded sample up to which it was made
        self.started_at = datetime.now().replace(microsecond=0)
        self.t0 = time.monotonic()
        self.vault = Vault(cfg.root_for(course))
        self.glossary = ensure_glossary(self.vault, course)
        self.prompt = self.glossary.prompt()
        self.source_label = f"live {self.started_at:%Y-%m-%d %H:%M:%S}"
        self.folder = self.vault.lecture_dir(course.name, f"{self.started_at:%Y-%m-%d}", source=self.source_label)
        self.files = self.vault.files(course.name, self.folder)
        self.transcript_path = self.files.target("transcript")
        self.number = 0 if course.is_event else self.vault.lecture_number(course.name, self.folder)
        base = cfg.data_for(course) / self.folder.name
        base.parent.mkdir(parents=True, exist_ok=True)
        self.pcm_path = Path(f"{base}.live.pcm")
        self.session_path = Path(f"{base}.session.json")
        self.backup = BackupTrack(backup, Path(f"{base}.backup.pcm"), lambda: self.now()) if backup else None

        self.state = "starting"           # starting → recording ⇄ paused → stopping → stopped (→ finalizing → done)
        self.written = 0                  # samples ffmpeg has written to the recording file
        self.read_pos = 0                 # samples fed to the live chunker
        self.partial = np.zeros(0, np.float32)
        self.last_growth = time.monotonic()
        self.retry_at = 0.0
        self.spans: list[tuple[int, float]] = [(0, 0.0)]
        self.pauses: list[list] = []      # [start, end, reason] in lecture time; end None while ongoing;
                                          # reason "paused" (by you) or "no input" (microphone lost)
        self.ended_at: float | None = None
        # The microphone needs a moment (seconds, if macOS asks for permission) before audio arrives,
        # so a span's lecture time is taken from its first block, not from the button press.
        self.span_waiting = False
        self.stalled = False              # device delivered nothing for STALL seconds; reconnecting
        self.silent = False               # device delivers exact zeros (iPhone locked)
        self.zero_start: int | None = None
        self.zeros_held: list[np.ndarray] = []  # zeros held back until we know whether it's a gap
        self.level_db = -120.0            # loudness of the latest block, for the level display
        self.buffer: list[np.ndarray] = []
        self.buffer_start = 0             # recorded-sample index of the first buffered sample
        self.levels: deque = deque(maxlen=300)  # last 30 s of block loudness → adaptive noise floor
        self.segments: list[dict] = []
        self.current_lang: str | None = None
        self.work: queue.Queue = queue.Queue()
        self.lock = threading.RLock()
        self.render_lock = threading.Lock()  # the worker and the control thread both re-render the draft
        self.final_note = ""
        self.warning = ""                 # shown in the menu while it applies: disk or memory running out
        self.next_resource_check = time.monotonic() + RESOURCE_CHECK
        self.disk_low = False             # the backup microphone stays off from here on
        self.draft_paused = False         # memory is low: record only, the final pass covers the gap
        self.memory_normal_since: float | None = None
        self.model_released = False

    # --- time ----------------------------------------------------------------

    def now(self) -> float:
        """Lecture time in seconds (sped up when a test file is played faster than real time)."""
        return (time.monotonic() - self.t0) * self.source.clock_rate

    def lecture_time(self, sample: int) -> float:
        for start, t in reversed(self.spans):
            if sample >= start:
                return t + (sample - start) / SAMPLE_RATE
        return 0.0

    # --- recording loop (main thread) -----------------------------------------
    # ffmpeg appends to the recording file by itself; this loop only reads what arrived.

    @property
    def recorded(self) -> int:
        return self.written

    def run(self) -> None:
        self.pcm_path.touch()
        self.reader = open(self.pcm_path, "rb")
        worker = threading.Thread(target=self._transcribe_loop, daemon=True)
        worker.start()
        with self.lock:
            self.source.start(self.pcm_path)
            if self.backup:
                self.backup.start()
            self.state = "recording"
            self.span_waiting = True
        self.save()
        self.render()
        log(f"Recording {self.course.name} from {self.source.name} → {self.transcript_path}"
            + (f" (backup: {self.backup.source.device})" if self.backup else ""))
        try:
            while self.state in ("recording", "paused"):
                time.sleep(POLL)
                with self.lock:
                    if self.state not in ("recording", "paused"):
                        break
                    self._poll()
                    if self.state != "recording":
                        continue
                    if self.backup and not self.disk_low:
                        self.backup.poll()
                    self._watch_resources()
                    if self.state != "recording":
                        continue
                    if self.source.finished and not self.source.restartable:
                        log("Input ended." + (f" ({self.source.error()})" if self.source.error() else ""))
                        self._stop_locked()
                    elif self.source.restartable:
                        self._watch_input()
        except KeyboardInterrupt:
            log("Ctrl+C: stopping …")
            with self.lock:
                self._stop_locked()
        log(f"Transcribing the last {self.work.qsize()} chunk(s) of the draft …")
        faulthandler.dump_traceback_later(60, exit=False, file=sys.stdout)  # stack dump if this ever hangs
        worker.join()
        faulthandler.cancel_dump_traceback_later()
        log("Live draft complete.")
        self.reader.close()
        self.save()

    def _poll(self) -> bool:
        """Read whatever ffmpeg appended since the last poll. Returns True if audio arrived."""
        st = self.pcm_path.stat()
        if st.st_ino != os.fstat(self.reader.fileno()).st_ino:  # the file was replaced: follow it
            log("Recording file was replaced on disk; re-opening it")
            self.reader.close()
            self.reader = open(self.pcm_path, "rb")
        size = st.st_size // 2
        new = size - self.written
        if new <= 0:
            return False
        self.reader.seek(self.written * 2)
        data = np.frombuffer(self.reader.read(new * 2), np.int16).astype(np.float32) / 32768.0
        first = self.written
        self.written = size
        self.last_growth = time.monotonic()
        arrived_at = self.now() - new / SAMPLE_RATE  # lecture time at which this audio started
        if self.span_waiting:
            # a span's time comes from its first audio, not from the button press: the device needs
            # a moment (seconds, if macOS asks for permission) before it delivers anything
            self.span_waiting = False
            start = max(self.spans[-1][1], arrived_at)
            self.spans[-1] = (self.spans[-1][0], start)
            if self.stalled:
                self.stalled = False
                self.pauses[-1][1] = start
                log(f"Microphone back at {self._fmt(start)}")
        elif arrived_at - self.lecture_time(first) > DRIFT:
            # the device delivered less audio than time passed (hiccups): keep timestamps on the clock
            self.spans.append((first, arrived_at))
        self.partial = np.concatenate([self.partial, data]) if len(self.partial) else data
        while len(self.partial) >= BLOCK:
            block, self.partial = self.partial[:BLOCK], self.partial[BLOCK:]
            self._feed(block, self.read_pos)
            self.read_pos += BLOCK
        return True

    def _flush_partial(self) -> None:
        if len(self.partial):
            self._feed(self.partial, self.read_pos)
            self.read_pos += len(self.partial)
            self.partial = np.zeros(0, np.float32)

    def _feed(self, block: np.ndarray, index: int) -> None:
        """One 100 ms block of recorded audio. Digital silence (a locked iPhone sends exact zeros)
        becomes a marked gap and is not transcribed; everything else goes to the chunker."""
        if not np.any(block):
            self.level_db = -120.0
            if self.zero_start is None:
                self.zero_start = index
            if not self.silent:
                self.zeros_held.append(block)
                if (index + len(block) - self.zero_start) / SAMPLE_RATE >= SILENT_GAP:
                    self.silent = True
                    self.zeros_held = []
                    self._emit()
                    t = self.lecture_time(self.zero_start)
                    self.pauses.append([t, None, "no input"])
                    log(f"Only digital silence from {self.source.name} since {self._fmt(t)} (phone locked?) …")
                    self.save()
            return
        if self.silent:
            self.silent = False
            t = self.lecture_time(index)
            self.pauses[-1][1] = t
            self.buffer = []
            log(f"Audio back at {self._fmt(t)}")
            self.save()
        elif self.zeros_held:  # a short stretch of zeros inside real audio: keep it
            for k, z in enumerate(self.zeros_held):
                self._buffer(z, self.zero_start + k * BLOCK)
        self.zero_start, self.zeros_held = None, []
        self._buffer(block, index)

    def _buffer(self, block: np.ndarray, index: int) -> None:
        if not self.buffer:
            self.buffer_start = index
        self.level_db = 20 * float(np.log10(max(rms(block), 1e-6)))
        self.buffer.append(block)
        self.levels.append(rms(block))
        self._maybe_cut()

    def _watch_input(self) -> None:
        """A microphone that stops delivering (iPhone out of range) is reconnected; the session goes on."""
        idle = time.monotonic() - self.last_growth
        # also catch a recorder that exited or never delivered anything since it was started
        limit = STARTUP_STALL if self.span_waiting else STALL
        if not self.stalled and (self.source.finished or idle > limit):
            t = self.lecture_time(self.written)
            err = self.source.error()
            log(f"No audio from {self.source.name} since {self._fmt(t)}{f' ({err})' if err else ''}; "
                f"reconnecting every {RETRY:.0f} s …")
            self._flush_partial()
            self._emit()
            if self.silent:  # the digital-silence gap turns into a lost-connection gap
                self.silent = False
                self.pauses[-1][1] = t
            self.stalled = True
            self.pauses.append([t, None, "no input"])
            self.source.stop()
            self.retry_at = 0.0
            self.save()
        if self.stalled and time.monotonic() >= self.retry_at:  # (re)try; success is noticed in _poll
            self.source.stop()
            if self.span_waiting and self.spans[-1][0] == self.written:
                self.spans[-1] = (self.written, self.now())   # replace the last failed attempt
            else:
                self.spans.append((self.written, self.now()))
            self.span_waiting = True
            self.source.start(self.pcm_path)
            self.retry_at = time.monotonic() + RETRY
            self.last_growth = time.monotonic()

    def _watch_resources(self) -> None:
        """Whatever runs out, the recording comes first: memory costs the draft, disk costs the backup
        microphone, and a really full disk ends the recording while it can still be saved."""
        if time.monotonic() < self.next_resource_check:
            return
        self.next_resource_check = time.monotonic() + RESOURCE_CHECK
        free = resources.free_disk(self.pcm_path.parent)
        if free < resources.DISK_FULL:
            log(f"Only {free // resources.MB} MB free: stopping, so what was recorded can be saved")
            self.warning = ""
            self.final_note = "stopped early: the disk is full"
            self._stop_locked()
            return
        if free < resources.DISK_LOW and not self.disk_low:
            log(f"Only {free // resources.MB} MB free: backup microphone off")
            self.disk_low = True
            if self.backup:
                self.backup.stop()
        pressure = resources.memory_pressure()
        if pressure > resources.PRESSURE_NORMAL:
            self.memory_normal_since = None
            if self.draft and not self.draft_paused:
                log("Memory is low: live draft paused, the recording goes on and the final pass covers it")
                self.draft_paused = True
                self.provisional = None
        elif self.draft_paused:
            self.memory_normal_since = self.memory_normal_since or time.monotonic()
            if time.monotonic() - self.memory_normal_since >= DRAFT_RESUME:
                log("Memory is back: live draft goes on")
                self.draft_paused = False
        self.warning = "Disk almost full" if self.disk_low else \
            "Memory is low: recording goes on, live text paused" if self.draft_paused else ""

    def _maybe_cut(self) -> None:
        length = sum(len(b) for b in self.buffer) / SAMPLE_RATE
        if length < MIN_CHUNK:
            return
        floor = float(np.percentile(self.levels, 20)) if len(self.levels) >= 10 else 0.005
        threshold = max(floor * 2.5, 0.002)
        tail = [rms(b) for b in self.buffer[-QUIET_TAIL:]]
        if len(tail) == QUIET_TAIL and max(tail) < threshold:
            self._emit()
        elif length >= MAX_CHUNK:
            audio = np.concatenate(self.buffer)
            search_from = max(0, len(audio) - 5 * SAMPLE_RATE)
            levels = [rms(audio[i:i + BLOCK]) for i in range(search_from, len(audio) - BLOCK + 1, BLOCK)]
            cut = search_from + int(np.argmin(levels)) * BLOCK + BLOCK // 2 if levels else len(audio)
            self._emit(cut)

    def _emit(self, n: int | None = None) -> None:
        """Send the first `n` buffered samples (all if None) to the transcriber."""
        if not self.buffer:
            return
        audio = np.concatenate(self.buffer)
        n = len(audio) if n is None else n
        if n > SAMPLE_RATE * 0.3 and self.draft and not self.draft_paused:
            self.work.put((audio[:n], self.lecture_time(self.buffer_start)))
        self.buffer_start += n
        self.buffer = [audio[n:]] if n < len(audio) else []

    # --- transcription (worker thread) ----------------------------------------

    def _transcribe_loop(self) -> None:
        while True:
            try:
                item = self.work.get(timeout=POLL)
            except queue.Empty:
                self._release_model_if_paused()
                self._update_provisional()
                continue
            if item is None:
                break
            clip, t = item
            if self.draft_paused:  # chunks from just before memory ran low
                continue
            self.model_released = False
            try:
                lang = language.pick(clip, self.course, self.engine, self.current_lang, self.lang_override)
                self.current_lang = lang
                segs = self.engine.transcribe(clip, lang, self.prompt, fast=True)
                segs = [s for s in segs if s.get("avg_logprob", 0) >= MIN_CONFIDENCE]
            except Exception as e:  # keep recording even if one chunk fails; the final pass redoes everything
                log(f"Live transcription error (final pass will cover it): {e}")
                continue
            dur = len(clip) / SAMPLE_RATE
            with self.lock:
                self.segments += [{**s, "start": s["start"] + t, "end": min(s["end"], dur) + t} for s in segs]
                if self.provisional and self.provisional[0] <= t + 0.05:  # its chunk now has the real text
                    self.provisional = None
            self.render()
        with self.lock:
            self.provisional = None
        self.render()

    def _release_model_if_paused(self) -> None:
        if self.draft_paused and not self.model_released and hasattr(self.engine, "release"):
            self.engine.release()
            self.model_released = True
            log("Whisper model unloaded until the draft goes on")

    def _update_provisional(self) -> None:
        """Quick text of the chunk being recorded (see PROVISIONAL_MIN), when there is enough new audio."""
        if not self.provisional_on or self.draft_paused:
            return
        with self.lock:
            if self.state != "recording" or not self.buffer or self.work.qsize():
                return
            end = self.buffer_start + sum(len(b) for b in self.buffer)
            if (end - self.buffer_start) / SAMPLE_RATE < PROVISIONAL_MIN:
                return
            same_chunk = self.provisional_at > self.buffer_start
            if same_chunk and (end - self.provisional_at) / SAMPLE_RATE < PROVISIONAL_EVERY:
                return
            audio, start = np.concatenate(self.buffer), self.lecture_time(self.buffer_start)
            lang = self.current_lang or language.fixed_override(self.lang_override) or self.course.fixed_language or self.course.languages[0]
            self.provisional_at = end
        try:
            text, logprob = self.engine.quick(audio, lang, self.prompt)
        except Exception as e:  # the draft works without it
            log(f"Provisional text failed, turned off for this session: {e}")
            self.provisional_on = False
            return
        with self.lock:
            if self.segments and self.segments[-1]["start"] > start:  # its chunk was done meanwhile
                return
            self.provisional = (start, text) if text and logprob >= MIN_CONFIDENCE else self.provisional
        self.render()

    # --- control (called from the socket thread) -------------------------------

    def command(self, cmd: str, device: str | None = None) -> dict:
        if cmd == "level":  # polled ~20×/s by Stenny's level meter while its menu is open: keep it cheap
            return {"ok": True, "state": self.state, "input_lost": self.stalled or self.silent,
                    "level_db": round(self.level_db, 1)}
        if cmd != "status":
            log(f"Command: {cmd} (state {self.state})")
        if cmd == "toggle":
            cmd = "pause" if self.state == "recording" else "resume"
        if cmd == "pause":
            self.pause()
        elif cmd == "resume":
            self.resume()
        elif cmd == "stop":
            self.stop()
        elif cmd == "microphone":
            if error := self.switch_microphone(device):
                return {"ok": False, "error": error}
        elif cmd != "status":
            return {"ok": False, "error": f"unknown command {cmd}"}
        return {"ok": True, **self.status()}

    def pause(self) -> None:
        with self.lock:
            if self.state != "recording":
                return
            self.state = "paused"
            t = self.now()
            self.source.stop()
            if self.backup:
                self.backup.stop()
            self._poll()            # audio written before the pause still belongs to the draft
            self._flush_partial()
            self._emit()
            self._close_gap(t)
            self.span_waiting = False
            self.pauses.append([t, None, "paused"])
        log(f"Paused at {self._fmt(t)} (microphone off, nothing is recorded)")
        self.save()
        self.render()

    def resume(self) -> None:
        with self.lock:
            if self.state != "paused":
                return
            t = self.now()
            self.pauses[-1][1] = t
            self.spans.append((self.written, t))
            self.span_waiting = True
            self.last_growth = time.monotonic()
            self.source.start(self.pcm_path)
            if self.backup and not self.disk_low:
                self.backup.start()
            self.state = "recording"
        log(f"Resumed at {self._fmt(t)}")
        self.save()
        self.render()

    def switch_microphone(self, device: str | None) -> str | None:
        """Record from another microphone from now on (e.g. the AirPods dropped out, or the iPhone is closer).
        The recording goes on in the same file; the switch costs well under a second of audio. Returns an
        error message, or None."""
        if not device:
            return "no microphone given"
        if not hasattr(self.source, "device"):  # a file played back for testing
            return "this recording doesn't come from a microphone"
        with self.lock:
            if self.state not in ("recording", "paused"):
                return "no recording is running"
            src = self.source
            if device == src.device and not self.stalled:
                return None
            old = src.device
            recording = self.state == "recording"
            if recording:
                t = self.now()
                src.stop()
                self._poll()            # what the old microphone delivered still belongs to the draft
                self._flush_partial()
                self._emit()
                self._close_gap(t)      # a lost microphone's gap ends here
            src.device, src.name, src.microphone = device, f"mic ({device})", device
            if recording:
                if self.span_waiting and self.spans[-1][0] == self.written:
                    self.spans[-1] = (self.written, self.now())   # the old one never delivered after its last start
                else:
                    self.spans.append((self.written, self.now()))
                self.span_waiting = True
                self.last_growth = time.monotonic()
                src.start(self.pcm_path)
        log(f"Microphone switched: {old} → {device}")
        self.save()
        self.render()
        return None

    def _close_gap(self, t: float) -> None:
        """End an open "no input" gap at t (pause or stop while the microphone was lost)."""
        if (self.silent or self.stalled) and self.pauses and self.pauses[-1][1] is None:
            self.pauses[-1][1] = t
        self.silent = self.stalled = False
        self.zero_start, self.zeros_held = None, []

    def stop(self) -> None:
        with self.lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        if self.state not in ("recording", "paused"):
            return
        if self.state == "paused":
            self.ended_at = self.pauses.pop()[0]  # a pause at the very end isn't part of the lecture
        else:
            self.source.stop()
            if self.backup:
                self.backup.stop()
            self._poll()
            self._flush_partial()
            if (self.silent or self.stalled) and self.pauses and self.pauses[-1][1] is None:
                if self.backup:  # the backup microphone heard the end: it stays part of the lecture
                    self.ended_at = self.pauses[-1][1] = self.now()
                else:
                    self.ended_at = self.pauses.pop()[0]  # likewise a gap at the very end
            else:
                self.ended_at = max(self.lecture_time(self.written), 0.0)
            self.silent = self.stalled = False
        if self.span_waiting and len(self.spans) > 1 and self.spans[-1][0] == self.written:
            self.spans.pop()  # a reconnect/resume that never delivered audio
        self._emit()
        self.state = "stopping"
        self.work.put(None)
        log(f"Stopped at {self._fmt(self.ended_at)}")

    # --- output ------------------------------------------------------------------

    def status(self) -> dict:
        with self.lock:
            # with a backup microphone, the main one's gaps are still recorded (the counter keeps going)
            paused = sum((p[1] if p[1] is not None else self.now()) - p[0] for p in self.pauses
                         if not (self.backup and p[2] == "no input"))
            words = sum(len(s["text"].split()) for s in self.segments)
            return {
                "state": self.state, "course": self.course.name, "source": self.source.name,
                "started_at": self.started_at.isoformat(),
                "elapsed_s": round(self.ended_at if self.ended_at is not None else self.now(), 1),
                "recorded_s": round(self.recorded / SAMPLE_RATE, 1), "paused_s": round(paused, 1),
                "pending_chunks": self.work.qsize(), "words": words, "language": self.current_lang,
                "draft": self.draft, "level_db": round(self.level_db, 1),
                "current_pause_s": round(self.now() - self.pauses[-1][0], 1)
                if self.state == "paused" and self.pauses else 0.0, "input_lost": self.stalled or self.silent,
                "transcript": str(self.transcript_path), "note": self.final_note, "warning": self.warning,
                "kind": self.course.kind,
                "backup": self.backup.source.device if self.backup else None,
                "microphone": getattr(self.source, "device", None),
            }

    def save(self) -> None:
        """session.json next to the recording: enough to redo the final pass after a crash."""
        with self.lock:
            data = {"course": self.course.name, "kind": self.course.kind, "started_at": self.started_at.isoformat(), "folder": self.folder.name,
                    "source": self.source_label, "microphone": getattr(self.source, "microphone", None),
                    "recording": str(self.pcm_path), "state": self.state,
                    "spans": self.spans, "pauses": self.pauses, "lang": self.lang_override,
                    "recorded_samples": self.written, "live_draft": self.draft,
                    "backup": self.backup.info() if self.backup else None}
        tmp = self.session_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(self.session_path)

    def render(self) -> None:
        with self.lock:
            segments = list(self.segments)
            pauses = [(p[0], p[1] if p[1] is not None else self.now(), p[2]) for p in self.pauses]
            duration = self.ended_at if self.ended_at is not None else self.now()
            provisional = self.provisional
        paragraphs = to_paragraphs(mark_dropped(segments, self.prompt), breaks=[p[0] for p in pauses])
        if provisional:  # still being said: italic, at the end, replaced when its chunk is transcribed
            start, text = provisional
            if paragraphs and start - paragraphs[-1]["start"] < 60 and not any(p[0] > paragraphs[-1]["start"] for p in pauses):
                paragraphs[-1] = {**paragraphs[-1], "text": f"{paragraphs[-1]['text']} *{text} …*"}
            else:
                paragraphs.append({"start": start, "text": f"*{text} …*", "unsure": []})
        with self.render_lock:
            self._write_draft(paragraphs, pauses, duration)

    def _write_draft(self, paragraphs, pauses, duration) -> None:
        lec = Lecture(course=self.course.name, date=f"{self.started_at:%Y-%m-%d}",
                      start_time=f"{self.started_at:%H:%M}", duration=duration,
                      languages=[self.current_lang or self.course.languages[0]], language_note="live",
                      model=self.cfg.model, source=self.source_label, tags=self.course.tags,
                      status="live", pauses=pauses, microphone=getattr(self.source, "microphone", None),
                      number=self.number, names=self.files.names(), kind=self.course.kind)
        self.vault.write(self.transcript_path, transcript_md(lec, paragraphs, time_sections(paragraphs)))

    @staticmethod
    def _fmt(t: float) -> str:
        s = int(t)
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
