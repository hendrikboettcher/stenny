"""Live mode: record from a source, pause/resume on request, and transcribe the recording while it grows, in the
very chunks the final pass would make (pipeline.transcribe), so the final transcript is ready minutes after Stop.

Transcription during the lecture runs only on power and with enough memory; otherwise the session only records and
the final pass catches up later (the chunks done so far are kept in a ChunkCache next to session.json, so nothing
is transcribed twice). The vault only gets a placeholder until the final transcript replaces it: the text so far is
shown in Stenny's window (app_server.lecture), never written where Claude or Obsidian would take it as the transcript.

Timekeeping: the recording only contains non-paused audio. `spans` maps it back to lecture
time: each span is (first recorded sample, lecture time in seconds when it started).
A digital-silence gap (locked iPhone) stays in the recording as zeros and is only marked.

A backup microphone (the Mac's own, when an iPhone is the main one) records into a second file
with its own spans. It is not transcribed live; the final pass fills the main one's gaps from it.
"""

import faulthandler
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from .. import jobs, language, resources
from ..config import Config, Course
from ..i18n import tr
from ..pipeline import (ChunkCache, chunk_bounds, chunk_lengths, chunks_path, ensure_glossary, lecture_time, log,
                        second_look, whisper_prompt, whisper_with)
from ..render import Lecture, transcript_md
from ..textproc import SAMPLE_RATE, chunk_end, to_paragraphs
from ..vault import Vault

BLOCK = SAMPLE_RATE // 10  # 100 ms
POLL = 0.1         # seconds between reads of the recording file
STALL = 5.0        # seconds without audio after which a microphone counts as disconnected
STARTUP_STALL = 15.0  # ... and right after (re)starting, before any audio arrived (permission prompt etc.)
SILENT_GAP = 2.0   # seconds of exact digital silence (iPhone locked/asleep) that count as "no input"
DRIFT = 1.0        # re-anchor the timeline when audio arrives this many seconds behind the clock
RETRY = 3.0        # seconds between reconnect attempts


BACKUP_RETRY = 30.0  # seconds between restarts of a backup recorder that stopped
RESOURCE_CHECK = 10.0  # seconds between looks at free disk and memory
DRAFT_RESUME = 120.0   # seconds of normal memory before transcription paused for memory goes on
# Audio this close to the recording's head may still become a gap (SILENT_GAP of zeros is noticed only after
# 2 s); chunks are cut only behind it, so they are the ones the final pass will cut.
LAG = SILENT_GAP + 1.5
POWER_CHECK = 20.0     # seconds between looks at whether the Mac is plugged in
PROMPT_CHECK = 300.0   # seconds between looks for slides dropped into the lecture's folder (terms for the prompt)


class _Stopping(Exception):
    """The second look stops: the recording ended, or transcription has to wait (battery, memory)."""


class _Recorded:
    """A stretch of the recording file as the array second_look expects: only what it looks at is read.
    `length`: what it counts as the stretch's length (where its look ends); `avail`: what may be read."""

    def __init__(self, read, s0: int, length: int, avail: int):
        self.read, self.s0, self.length, self.avail = read, s0, length, avail

    def __len__(self) -> int:
        return self.length

    def __getitem__(self, sl: slice) -> np.ndarray:
        a, b = min(sl.start or 0, self.avail), min(self.avail if sl.stop is None else sl.stop, self.avail)
        return self.read(self.s0 + a, self.s0 + max(a, b))


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
                 backup=None):
        self.cfg, self.course, self.source, self.engine, self.lang_override = cfg, course, source, engine, lang
        self.draft = draft  # transcribe during the recording when plugged in (False: record only)
        self.started_at = datetime.now().replace(microsecond=0)
        self.t0 = time.monotonic()
        self.vault = cfg.vault_for(course)
        self.glossary = ensure_glossary(self.vault, course)
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
        chunks = chunks_path(self.session_path)
        chunks.unlink(missing_ok=True)  # a leftover of an earlier recording in this folder
        self.cache = ChunkCache(chunks)
        self.prompt, _, _ = whisper_prompt(self.vault, self.folder, self.glossary, course.name)
        self.prompt_checked = time.monotonic()
        self.max_len, self.min_len = chunk_lengths(course, lang)
        self.chunk_pos: dict[int, int] = {}  # per stretch (its first sample): where its next chunk starts
        self.looked: dict[int, tuple] = {}   # per stretch: what the second look last saw (chunks, complete)
        self.look_state = None               # what _second_look last saw of the session as a whole
        self.done_to = 0.0                   # lecture time transcribed up to (for the status)

        self.state = "starting"           # starting → recording ⇄ paused → stopping → stopped (→ finalizing → done)
        self.written = 0                  # samples ffmpeg has written to the recording file
        self.read_pos = 0                 # samples looked at for digital silence and the level
        self.partial = np.zeros(0, np.float32)
        self.last_growth = self.wall()
        self.retry_at = 0.0
        self.spans: list[tuple[int, float]] = [(0, 0.0)]
        self.pauses: list[list] = []      # [start, end, reason] in lecture time; end None while ongoing;
                                          # reason "paused" (by you) or "no input" (microphone lost)
        self.ended_at: float | None = None
        self.cancelled = False            # Cancel Recording: no transcript, its files go to the Trash
        # The microphone needs a moment (seconds, if macOS asks for permission) before audio arrives,
        # so a span's lecture time is taken from its first block, not from the button press.
        self.span_waiting = False
        self.stalled = False              # device delivered nothing for STALL seconds; reconnecting
        self.silent = False               # device delivers exact zeros (iPhone locked)
        self.zero_start: int | None = None
        self.zeros_held: list[np.ndarray] = []  # zeros held back until we know whether it's a gap
        self.level_db = -120.0            # loudness of the latest block, for the level display
        self.current_lang: str | None = None
        self.lock = threading.RLock()
        self.stopped = threading.Event()  # wakes the transcription thread at Stop
        self.render_lock = threading.Lock()  # the main and the control thread both write the placeholder
        self.final_note = ""
        self.warning = ""                 # shown in the menu while it applies: disk or memory running out
        self.next_resource_check = time.monotonic() + RESOURCE_CHECK
        self.disk_low = False             # the backup microphone stays off from here on
        self.draft_paused = False         # memory is low: record only, transcription catches up later
        self.memory_normal_since: float | None = None
        self.on_power = False             # looked at by the transcription thread (pmset takes a moment)
        self.power_checked = float("-inf")
        self.model_released = True

    # --- time ----------------------------------------------------------------

    def now(self) -> float:
        """Lecture time in seconds (sped up when a test file is played faster than real time)."""
        return (time.monotonic() - self.t0) * self.source.clock_rate

    def wall(self) -> float:
        """Seconds for the microphone's stall and retry timers: real time (the tests put their simulated clock here,
        so a slow machine running them doesn't look like a lost microphone)."""
        return time.monotonic()

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
        with self.lock:
            self.last_growth = self.wall()  # the microphone's stall timer starts with the recording
            self.source.start(self.pcm_path)
            if self.backup:
                self.backup.start()
            self.state = "recording"
            self.span_waiting = True
        self.save()
        self.render()
        worker.start()
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
        faulthandler.dump_traceback_later(60, exit=False, file=sys.stdout)  # stack dump if this ever hangs
        worker.join()  # it finishes the chunk it is on
        faulthandler.cancel_dump_traceback_later()
        log(f"{len(self.cache.chunks)} chunks transcribed during the recording, up to {self._fmt(self.done_to)}")
        self.reader.close()
        self.save()
        self.render()  # its final length

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
        self.last_growth = self.wall()
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
                self.save()
                self.render()
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
                    t = self.lecture_time(self.zero_start)
                    self.pauses.append([t, None, "no input"])
                    log(f"Only digital silence from {self.source.name} since {self._fmt(t)} (phone locked?) …")
                    self.save()
                    self.render()
            return
        if self.silent:
            self.silent = False
            t = self.lecture_time(index)
            self.pauses[-1][1] = t
            log(f"Audio back at {self._fmt(t)}")
            self.save()
            self.render()
        self.zero_start, self.zeros_held = None, []  # a short stretch of zeros inside real audio stays part of it
        self.level_db = 20 * float(np.log10(max(rms(block), 1e-6)))

    def _watch_input(self) -> None:
        """A microphone that stops delivering (iPhone out of range) is reconnected; the session goes on."""
        idle = self.wall() - self.last_growth
        # also catch a recorder that exited or never delivered anything since it was started
        limit = STARTUP_STALL if self.span_waiting else STALL
        if not self.stalled and (self.source.finished or idle > limit):
            t = self.lecture_time(self.written)
            err = self.source.error()
            log(f"No audio from {self.source.name} since {self._fmt(t)}{f' ({err})' if err else ''}; "
                f"reconnecting every {RETRY:.0f} s …")
            self._flush_partial()
            if self.silent:  # the digital-silence gap turns into a lost-connection gap
                self.silent = False
                self.pauses[-1][1] = t
            self.stalled = True
            self.pauses.append([t, None, "no input"])
            self.source.stop()
            self.retry_at = 0.0
            self.save()
            self.render()
        if self.stalled and self.wall() >= self.retry_at:  # (re)try; success is noticed in _poll
            self.source.stop()
            if self.span_waiting and self.spans[-1][0] == self.written:
                self.spans[-1] = (self.written, self.now())   # replace the last failed attempt
            else:
                self.spans.append((self.written, self.now()))
            self.span_waiting = True
            self.source.start(self.pcm_path)
            self.retry_at = self.wall() + RETRY
            self.last_growth = self.wall()

    def _watch_resources(self) -> None:
        """Whatever runs out, the recording comes first: memory costs the transcription, disk costs the backup
        microphone, and a really full disk ends the recording while it can still be saved."""
        if time.monotonic() < self.next_resource_check:
            return
        self.next_resource_check = time.monotonic() + RESOURCE_CHECK
        free = resources.free_disk(self.pcm_path.parent)
        if free < resources.DISK_FULL:
            log(f"Only {free // resources.MB} MB free: stopping, so what was recorded can be saved")
            self.warning = ""
            self.final_note = tr("stopped early: the disk is full")
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
                log("Memory is low: transcription paused, the recording goes on and catches up later")
                self.draft_paused = True
        elif self.draft_paused:
            self.memory_normal_since = self.memory_normal_since or time.monotonic()
            if time.monotonic() - self.memory_normal_since >= DRAFT_RESUME:
                log("Memory is back: transcription goes on")
                self.draft_paused = False
        # shown in the app's menu, in its language ($STENNY_LANGUAGE)
        self.warning = tr("Disk almost full") if self.disk_low else \
            tr("Memory is low: recording goes on, transcription paused") if self.draft_paused else ""

    # --- transcription (worker thread) ----------------------------------------
    # The final pass's chunks (pipeline.transcribe), cut as the recording grows (textproc.chunk_end), in order.

    def _may_transcribe(self) -> bool:
        if time.monotonic() - self.power_checked >= POWER_CHECK:
            on_power = jobs.on_ac_power()
            if on_power != self.on_power and self.draft and self.power_checked > float("-inf"):
                log("Plugged in: transcribing during the recording" if on_power else
                    "On battery: recording only, the transcript catches up when the "
                    f"{'PC' if sys.platform == 'win32' else 'Mac'} is plugged in")
            self.on_power, self.power_checked = on_power, time.monotonic()
        return self.draft and self.on_power and not self.draft_paused

    def _next_chunk(self) -> tuple[int, int, int] | None:
        """(stretch start, chunk start, chunk end) in recorded samples of the next chunk to transcribe, or None."""
        with self.lock:
            spans, n, state = list(self.spans), self.written, self.state
            gaps = [(p[0], p[1]) for p in self.pauses if p[2] == "no input"]
        bounds = chunk_bounds(spans, n, gaps)
        growing = state in ("starting", "recording", "paused")
        for k, (s0, s1) in enumerate(bounds):
            last = k == len(bounds) - 1
            closed = not (growing and last)  # the last stretch may go on (a pause under 3 s isn't a break)
            avail = s1 - s0 if closed or state != "recording" else max(0, n - int(LAG * SAMPLE_RATE) - s0)
            pos = self.chunk_pos.get(s0, 0)
            while pos < s1 - s0:
                end = chunk_end(lambda a, b: self._read(s0 + a, s0 + b), pos, avail, closed, self.max_len, self.min_len)
                if end is None:
                    break
                if end - pos > SAMPLE_RATE // 10:  # split_at_pauses drops shorter ones
                    return s0, s0 + pos, s0 + end
                pos = self.chunk_pos[s0] = end
        return None

    def _read(self, a: int, b: int) -> np.ndarray:
        """Recorded samples a..b, as the final pass reads them (audio.load of the .pcm)."""
        with open(self.pcm_path, "rb") as f:
            f.seek(a * 2)
            return np.frombuffer(f.read((b - a) * 2), np.int16).astype(np.float32) / 32768.0

    def _language(self, clip: np.ndarray) -> str:
        """As language.plan picks it for this chunk: fixed, voted over the recording so far (the final pass votes
        over all of it, and transcribes again where it votes otherwise), or per chunk, sticky."""
        fixed = language.fixed_override(self.lang_override) or self.course.fixed_language
        if fixed:
            return fixed
        if self.course.language == "auto" or len(self.course.languages) == 1:
            if self.current_lang is None:
                n = self.written
                lang, _ = language.vote_language(self._read(0, n), self.engine, self.course.languages)
                self.current_lang = lang
            return self.current_lang
        self.current_lang = language.pick(clip, self.course, self.engine, self.current_lang)
        return self.current_lang

    def _transcribe_loop(self) -> None:
        while self.state in ("starting", "recording", "paused"):
            may = self._may_transcribe()
            chunk = self._next_chunk() if may else None
            if chunk is None:
                if may and self._second_look():
                    continue
                self._release_model_if_idle()
                self.stopped.wait(POLL * 5)
                continue
            s0, a, b = chunk
            if time.monotonic() - self.prompt_checked >= PROMPT_CHECK:  # slides dropped in meanwhile
                prompt, slides, _ = whisper_prompt(self.vault, self.folder, self.glossary, self.course.name, quiet=True)
                if prompt != self.prompt:
                    log(f"Slides found ({slides.count if slides else 0} pages): their terms help Whisper from now on")
                self.prompt, self.prompt_checked = prompt, time.monotonic()
            clip = self._read(a, b)
            self.model_released = False
            try:
                lang = self._language(clip)
                segs = self.engine.transcribe(clip, lang, self.prompt)
            except Exception as e:  # the recording goes on; the final pass transcribes this chunk
                log(f"Transcription error at {self._fmt(lecture_time(self.spans, a))} (the final pass redoes it): {e}")
                segs, lang = None, None
            with self.lock:
                t = lecture_time(self.spans, a)
                if segs is not None:
                    self.cache.put(a, b, lang, segs, t)
                    self.done_to = lecture_time(self.spans, b)
                self.chunk_pos[s0] = b - s0

    def _second_look(self) -> bool:
        """Whisper's second look (pipeline.second_look) at each stretch's chunks transcribed so far, as far as it is
        settled: up to their last segment, or to the stretch's end once all its chunks are done. The same code as
        in the final pass, on the same segments, so it asks Whisper the same questions; the answers go to the
        ChunkCache. Returns True if it looked at anything anew."""
        with self.lock:
            spans, n, state = list(self.spans), self.written, self.state
            gaps = [(p[0], p[1]) for p in self.pauses if p[2] == "no input"]
            chunks = sorted(self.cache.chunks.values(), key=lambda d: d["a"])
            seen = (len(chunks), state, len(spans), str(gaps), sum(self.chunk_pos.values()))
        if seen == self.look_state:
            return False
        bounds = chunk_bounds(spans, n, gaps)
        growing = state in ("starting", "recording", "paused")
        whisper = whisper_with(self.cache, self.engine, self.prompt, spans, before=self._still_transcribing)
        looked = False
        for k, (s0, s1) in enumerate(bounds):
            span_chunks, local, pos = [], [], s0
            for d in chunks:
                if not s0 <= d["a"] < s1:
                    continue
                if d["a"] != pos:  # a chunk failed here: the final pass looks at the rest
                    break
                off = (d["a"] - s0) / SAMPLE_RATE
                local += [{**seg, "start": seg["start"] + off, "end": min(seg["end"] + off, (d["b"] - s0) / SAMPLE_RATE)}
                          for seg in d["segments"]]
                span_chunks.append((d["a"], d["b"], d["lang"]))
                pos = d["b"]
            closed = not (growing and k == len(bounds) - 1)
            complete = closed and self.chunk_pos.get(s0) == s1 - s0 and s1 - pos <= SAMPLE_RATE // 10
            if not local or self.looked.get(s0) == (len(span_chunks), complete):
                continue
            # an unfinished stretch ends at its last segment for now: what comes after it isn't known yet
            length = s1 - s0 if complete else int(np.ceil(local[-1]["end"] * SAMPLE_RATE))
            try:
                second_look(local, _Recorded(self._read, s0, length, pos - s0), s0, span_chunks, whisper)
            except _Stopping:
                return looked
            self.looked[s0] = (len(span_chunks), complete)
            looked = True
        self.look_state = seen
        return looked

    def _still_transcribing(self) -> None:
        if self.state not in ("starting", "recording", "paused") or not self._may_transcribe():
            raise _Stopping
        self.model_released = False

    def _release_model_if_idle(self) -> None:
        """On battery or low memory Whisper's model (GBs) is unloaded until transcription goes on."""
        if not self.model_released and not self._may_transcribe() and hasattr(self.engine, "release"):
            self.engine.release()
            self.model_released = True
            log("Whisper model unloaded until transcription goes on")

    @property
    def segments(self) -> list[dict]:
        """Whisper's segments so far, in lecture time."""
        with self.lock:
            return self.cache.text_so_far()

    def text_so_far(self) -> list[dict]:
        """The transcribed paragraphs so far (lecture time), for Stenny's window."""
        with self.lock:
            segments = self.cache.text_so_far()
            pauses = [p[0] for p in self.pauses]
        return to_paragraphs(segments, breaks=pauses)

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
        elif cmd == "cancel":
            self.cancel()
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
            self._poll()            # audio written before the pause still belongs to the recording
            self._flush_partial()
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
            self.last_growth = self.wall()
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
                self._poll()            # what the old microphone delivered still belongs to the recording
                self._flush_partial()
                self._close_gap(t)      # a lost microphone's gap ends here
            src.device, src.name, src.microphone = device, f"mic ({device})", device
            if recording:
                if self.span_waiting and self.spans[-1][0] == self.written:
                    self.spans[-1] = (self.written, self.now())   # the old one never delivered after its last start
                else:
                    self.spans.append((self.written, self.now()))
                self.span_waiting = True
                self.last_growth = self.wall()
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

    def cancel(self) -> None:
        """Stop without a transcript: the recording and its placeholder go to the Trash (cli.cmd_live)."""
        with self.lock:
            if self.state not in ("recording", "paused"):
                return
            self.cancelled = True
            self._stop_locked()
            self.state = "cancelling"

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
        self.state = "stopping"
        self.stopped.set()
        log(f"Stopped at {self._fmt(self.ended_at)}")

    # --- output ------------------------------------------------------------------

    def status(self) -> dict:
        with self.lock:
            # with a backup microphone, the main one's gaps are still recorded (the counter keeps going)
            paused = sum((p[1] if p[1] is not None else self.now()) - p[0] for p in self.pauses
                         if not (self.backup and p[2] == "no input"))
            words = sum(len(seg["text"].split()) for d in self.cache.chunks.values() for seg in d["segments"])
            return {
                "state": self.state, "course": self.course.name, "source": self.source.name,
                "started_at": self.started_at.isoformat(),
                "elapsed_s": round(self.ended_at if self.ended_at is not None else self.now(), 1),
                "recorded_s": round(self.recorded / SAMPLE_RATE, 1), "paused_s": round(paused, 1),
                "words": words, "transcribed_s": round(self.done_to, 1), "language": self.current_lang,
                "draft": self.draft, "transcribing": self.draft and self.on_power and not self.draft_paused, "level_db": round(self.level_db, 1),
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
        """The placeholder in the vault: the lecture's details and status, no text (see the module's note)."""
        with self.lock:
            pauses = [(p[0], p[1] if p[1] is not None else self.now(), p[2]) for p in self.pauses]
            duration = self.ended_at if self.ended_at is not None else self.now()
            lec = Lecture(course=self.course.name, date=f"{self.started_at:%Y-%m-%d}",
                          start_time=f"{self.started_at:%H:%M}", duration=duration,
                          languages=[self.current_lang or self.course.languages[0]], language_note="live",
                          model=self.cfg.model, source=self.source_label, tags=self.course.tags,
                          status="live", pauses=pauses, microphone=getattr(self.source, "microphone", None),
                          number=self.number, names=self.files.names(), kind=self.course.kind)
        with self.render_lock:
            self.vault.write(self.transcript_path, transcript_md(lec, [], []))

    @staticmethod
    def _fmt(t: float) -> str:
        s = int(t)
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
