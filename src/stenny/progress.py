"""How far a final pass is, for Stenny's window: `<folder>.progress.json` next to the lecture's segments.json (in
the data place, outside the Stenny folder). pipeline.process writes it while it runs; app_server.library reads it
for a lecture that is "finalizing", so the banner, the list's row and the activity bar show the same; it is
removed when the pass ends, however it ends.

    {"step": "transcribing" | "tidying" | "saving", "fraction": 0…1 of the whole pass,
     "at": lecture seconds transcribed so far, "of": the lecture's length, "left_s": transcribing left (s) or null,
     "updated": Unix time}

Cheap on purpose: one small file, written at most every few seconds and at each new step; a write that fails is
skipped, never an error that could stop the final pass.
"""

import json
import os
import time
from pathlib import Path

# each step's share of the bar: Whisper is nearly all of the time; tidying is the text steps and the optional AI
# step (a call or two to Claude), saving the audio archive and the course files
STEPS = {"transcribing": (0.0, 0.85), "tidying": (0.85, 0.97), "saving": (0.97, 1.0)}
MIN_GAP_S = 3.0      # between writes within a step
ETA_AFTER = 3        # chunks Whisper really transcribed before there's a time left to show
STALE_S = 15 * 60    # a file not written for this long belongs to a pass that died: no bar rather than a stuck one


def progress_path(data_dir: Path, folder: str) -> Path:
    return data_dir / f"{folder}.progress.json"


class Progress:
    """The final pass reports here; `path` None reports nowhere (refresh, the backup microphone's own pass)."""

    def __init__(self, path: Path | None, duration: float, clock=time.monotonic):
        self.path, self.duration, self.clock = path, duration, clock
        self.name, self.part, self.at = "transcribing", 0.0, 0.0
        self.total = self.done = 0                 # samples in all chunks / in those done
        self.new_total = self.new_done = 0         # the same, only chunks Whisper transcribes (not from the cache)
        self.new_chunks = 0
        self.rate_from: tuple[float, int] | None = None  # (clock, new_done) once the first new chunk is done
        self.last: float | None = None
        self.dirty = False                         # something changed since the last write
        self.clear()  # an older pass's numbers (one that died) never show

    def plan(self, chunks: list[tuple[int, int, bool]]) -> None:
        """The chunks to go through, as (first sample, end sample, already in the cache)."""
        self.total = sum(b - a for a, b, _ in chunks)
        self.new_total = sum(b - a for a, b, cached in chunks if not cached)
        self.step("transcribing")

    def chunk(self, a: int, b: int, lecture_t: float, new: bool) -> None:
        """One chunk done; `lecture_t`: where it ends in the lecture, `new`: Whisper transcribed it just now."""
        self.done += b - a
        self.at = lecture_t
        if new:
            self.new_done += b - a
            self.new_chunks += 1
            if self.rate_from is None:  # the first one also loads the model: the speed is measured from its end
                self.rate_from = (self.clock(), self.new_done)
        self.part = self.done / self.total if self.total else 1.0
        self.dirty = True
        self._write()

    def before_whisper(self) -> None:
        """Whisper is about to take a while: what is done so far shows now (after a run of chunks from the cache,
        a resumed pass's bar starts where it was, not where the last write left it)."""
        if self.dirty:
            self._write(force=True)

    def left(self) -> float | None:
        """Seconds of transcribing left, from the speed of the chunks Whisper did so far; None until it knows."""
        if self.rate_from is None or self.new_chunks < ETA_AFTER:
            return None
        t0, s0 = self.rate_from
        if self.new_done <= s0:
            return None
        per_sample = (self.clock() - t0) / (self.new_done - s0)
        return max(0.0, (self.new_total - self.new_done) * per_sample)

    def step(self, name: str, part: float = 0.0) -> None:
        """A new step, or how far into it (0…1)."""
        self.name, self.part = name, max(0.0, min(1.0, part))
        self._write(force=True)

    def snapshot(self) -> dict:
        lo, hi = STEPS[self.name]
        left = self.left() if self.name == "transcribing" else None
        return {"step": self.name, "fraction": round(lo + (hi - lo) * self.part, 4),
                "at": round(min(self.at, self.duration), 1), "of": round(self.duration, 1),
                "left_s": round(left) if left is not None else None, "updated": round(time.time())}

    def _write(self, force: bool = False) -> None:
        if not self.path:
            return
        now = self.clock()
        if not force and self.last is not None and now - self.last < MIN_GAP_S:
            return
        self.last, self.dirty = now, False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.tmp")
            tmp.write_text(json.dumps(self.snapshot()), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def clear(self) -> None:
        if not self.path:
            return
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


def read(path: Path) -> dict | None:
    """What the window shows of a final pass, or None (none running, or its file is stale or unreadable)."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if d["step"] not in STEPS or time.time() - float(d["updated"]) > STALE_S:
            return None
        left = d.get("left_s")
        return {"step": d["step"], "fraction": max(0.0, min(1.0, float(d["fraction"]))),
                "at": float(d.get("at") or 0), "of": float(d.get("of") or 0),
                "left_s": float(left) if left is not None else None}
    except (OSError, ValueError, KeyError, TypeError):
        return None
