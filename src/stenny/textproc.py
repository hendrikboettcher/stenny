"""Cleanup of raw Whisper segments: chunking, hallucination filters, gap filling, paragraphs."""

import re

import numpy as np

SAMPLE_RATE = 16000


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_hms(seconds: float) -> str:
    """Always h:mm:ss, so timestamps sort and read consistently in long lectures."""
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def frame_rms(audio: np.ndarray, frame: int) -> np.ndarray:
    n = len(audio) // frame
    if n == 0:
        return np.zeros(0)
    return np.sqrt((audio[: n * frame].reshape(n, frame) ** 2).mean(axis=1))


def has_speech(clip: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bool:
    """Rough energy check: is a meaningful share of this clip louder than room noise?"""
    rms = frame_rms(clip, sample_rate // 2)
    if len(rms) == 0:
        return False
    return (rms > 0.01).mean() > 0.3


def split_at_pauses(audio: np.ndarray, max_len: float = 28.0, min_len: float = 18.0,
                    sample_rate: int = SAMPLE_RATE) -> list[tuple[int, int]]:
    """Cut audio into chunks of at most `max_len` seconds, each ending at the quietest
    moment between `min_len` and `max_len`, so cuts land in pauses rather than mid-word.

    Chunks let every Whisper call get the course glossary as prompt (mlx-whisper drops the
    initial prompt after the first 30 s window) and allow per-chunk language detection.
    """
    frame = sample_rate // 50  # 20 ms
    rms = frame_rms(audio, frame)
    # smooth over 200 ms so a single quiet frame inside a word doesn't count as a pause
    rms = np.convolve(rms, np.ones(10) / 10, mode="same") if len(rms) >= 10 else rms
    total = len(audio)
    chunks, start = [], 0
    while total - start > max_len * sample_rate:
        lo = (start + int(min_len * sample_rate)) // frame
        hi = (start + int(max_len * sample_rate)) // frame
        cut = (lo + int(np.argmin(rms[lo:hi]))) * frame if hi > lo else hi * frame
        chunks.append((start, cut))
        start = cut
    if total > start:
        chunks.append((start, total))
    return [(a, b) for a, b in chunks if b - a > sample_rate // 10]


def is_hallucination(seg: dict) -> bool:
    """Whisper's standard 'probably no speech here' signal."""
    return seg.get("no_speech_prob", 0) > 0.6 and seg.get("avg_logprob", 0) < -1.0


# Credits of the TV subtitles Whisper was trained on. On near-silence it writes them as if they were said
# ("Untertitelung des ZDF für funk, 2017"). They never occur in a lecture, so a short segment that is one
# of them is dropped (and kept, marked, in the raw output like every dropped segment).
SUBTITLE_CREDITS = re.compile(
    r"amara\.org|untertitel(ung)?\b.*\b(zdf|ard|wdr|swr|ndr|br|funk|im auftrag)|copyright\s+(wdr|swr|ndr|zdf)"
    r"|sous-titr(es|age)\b.*\b(amara|radio-canada|st' ?501|soci[ée]t[ée])|subt[ií]tulos realizados"
    r"|subtitles by\b|transcription by castingwords",
    re.IGNORECASE)


def is_subtitle_credit(text: str) -> bool:
    return len(text.split()) <= 15 and bool(SUBTITLE_CREDITS.search(text))


def is_prompt_echo(text: str, prompt: str | None) -> bool:
    """On near-silent audio Whisper sometimes just repeats its prompt (the glossary)."""
    if not prompt or len(text) < 12:
        return False
    norm = lambda s: " ".join(s.lower().replace(",", " ").split())
    return norm(text) in norm(prompt)


def drop_reason(seg: dict, previous: list[dict], prompt: str | None) -> str | None:
    """Why a raw segment should not appear in the clean transcript (None = keep)."""
    text = seg["text"].strip()
    if not text:
        return "empty"
    if is_hallucination(seg):
        return "no speech"
    if seg.get("compression_ratio", 0) > 2.4:  # Whisper's own threshold for "stuck in a loop"
        return "repetition loop"
    if is_prompt_echo(text, prompt):
        return "prompt echo"
    if is_subtitle_credit(text):
        return "subtitle credit"
    if len(previous) >= 2 and all(p["text"].strip() == text for p in previous[-2:]):
        return "repetition"
    return None


def mark_dropped(segments: list[dict], prompt: str | None = None) -> list[dict]:
    """Annotate each segment with `dropped` (reason or None). Nothing is deleted, so the raw
    transcript can still show every segment Whisper produced."""
    kept: list[dict] = []
    out = []
    for seg in segments:
        reason = seg.get("dropped") or drop_reason(seg, kept, prompt)
        out.append({**seg, "dropped": reason})
        if reason is None:
            kept.append(seg)
    return out


def fill_gaps(segments: list[dict], audio, transcribe_clip, sample_rate: int = SAMPLE_RATE,
              min_gap: float = 5.0) -> list[dict]:
    """Re-transcribe stretches Whisper skipped although someone was talking.

    Whisper sometimes jumps to the end of its 30s window and silently drops what was
    said there (e.g. right after small talk before the lecture starts).
    """
    bounds = [0.0] + [t for s in segments for t in (s["start"], s["end"])] + [len(audio) / sample_rate]
    gaps = [(bounds[i], bounds[i + 1]) for i in range(0, len(bounds), 2) if bounds[i + 1] - bounds[i] >= min_gap]
    filled = list(segments)
    for start, end in gaps:
        clip = audio[int(start * sample_rate) : int(end * sample_rate)]
        if not has_speech(clip, sample_rate):
            continue
        for seg in transcribe_clip(clip, start):
            filled.append({**seg, "start": seg["start"] + start, "end": seg["end"] + start, "gap_fill": True})
    return sorted(filled, key=lambda s: s["start"])


def letters(text: str) -> int:
    return sum(ch.isalpha() for ch in text)


def is_sparse(seg: dict, min_seconds: float = 5.0, min_letters_per_s: float = 2.0) -> bool:
    """A long segment with (almost) no text, e.g. "..." over 27 s. Normal speech has ~12-15 letters/s."""
    dur = seg["end"] - seg["start"]
    return dur >= min_seconds and letters(seg["text"]) < dur * min_letters_per_s


def retry_sparse(segments: list[dict], audio, transcribe_clip, sample_rate: int = SAMPLE_RATE) -> list[dict]:
    """Whisper sometimes returns "..." for a whole chunk although someone talks. Re-transcribe such
    stretches in short pieces (without prompt) and keep whichever version has more text. The sparse
    original stays in the list, marked as dropped, so the raw transcript still shows it."""
    out = []
    for seg in segments:
        if not is_sparse(seg) or seg.get("retry"):
            out.append(seg)
            continue
        a, b = int(seg["start"] * sample_rate), int(seg["end"] * sample_rate)
        clip, redo = audio[a:b], []
        for x, y in split_at_pauses(clip, max_len=10, min_len=4, sample_rate=sample_rate):
            for s in transcribe_clip(clip[x:y], seg["start"] + x / sample_rate):
                offset = seg["start"] + x / sample_rate
                redo.append({**s, "start": s["start"] + offset, "end": min(s["end"] + offset, seg["end"]), "retry": True})
        if sum(letters(s["text"]) for s in redo) > letters(seg["text"]):
            out.append({**seg, "dropped": "almost empty, re-transcribed"})
            out.extend(redo)
        else:
            out.append(seg)
    return sorted(out, key=lambda s: s["start"])


LONG_SILENCE = 20.0
UNSURE_LOGPROB = -0.6  # segments below this average log-probability: Whisper wasn't sure what it heard


def to_paragraphs(segments: list[dict], breaks: list[float] | None = None) -> list[dict]:
    """Merge kept segments into readable paragraphs, breaking at pauses or length. `breaks` are
    times (e.g. where a live recording was paused) that always start a new paragraph.
    Each paragraph also lists the texts of its `unsure` segments (low avg_logprob). Text from the backup
    microphone (live mode) gets paragraphs of its own, marked `backup`."""
    breaks = sorted(breaks or [])
    paragraphs, current, unsure, start, last_end, backup = [], [], [], None, 0.0, False
    for seg in segments:
        if seg.get("dropped"):
            continue
        text = seg["text"].strip()
        pause = seg["start"] - last_end
        length = sum(len(t) for t in current)
        forced = (pause > LONG_SILENCE or any(last_end - 0.5 <= b <= seg["start"] + 0.5 for b in breaks)
                  or seg.get("backup", False) != backup)
        if current and (forced or (pause > 1.5 and length > 300) or length > 900):
            paragraphs.append({"start": start, "text": " ".join(current), "unsure": unsure, **({"backup": True} if backup else {})})
            current, unsure, start = [], [], None
        if start is None:
            start, backup = seg["start"], seg.get("backup", False)
        current.append(text)
        if seg.get("avg_logprob", 0.0) < UNSURE_LOGPROB:
            unsure.append(text)
        last_end = seg["end"]
    if current:
        paragraphs.append({"start": start, "text": " ".join(current), "unsure": unsure, **({"backup": True} if backup else {})})
    return paragraphs



WORD = re.compile(r"[\w']+")
EDGE_WORDS = 12   # words compared on each side of a gap edge
EDGE_SLACK = 3    # unmatched words allowed around the repeated run (a misheard "So", "The")


def _words(segs: list[dict]) -> list[tuple[int, re.Match]]:
    """(segment index, match) for every word, in order."""
    return [(i, m) for i, s in enumerate(segs) for m in WORD.finditer(s["text"])]


def _norm(words) -> list[str]:
    return [m.group().lower() for _, m in words]


def head_repeat(before: list[str], head: list[str]) -> int:
    """How many words at the start of `head` repeat the end of `before` (0 if none): the longest run
    of >= 2 words ending `before` (up to EDGE_SLACK words from its end) found near the start of `head`."""
    for k in range(min(len(before), len(head)), 1, -1):
        for i in range(min(EDGE_SLACK, len(before) - k) + 1):
            run = before[len(before) - i - k:len(before) - i]
            for j in range(min(EDGE_SLACK, len(head) - k) + 1):
                if head[j:j + k] == run:
                    return min(len(head), j + k + i)
    return 0


def tail_repeat(after: list[str], tail: list[str]) -> int:
    """How many words at the end of `tail` repeat the start of `after` (mirror of head_repeat)."""
    return head_repeat(after[::-1], tail[::-1])


def trim_edge_overlap(main: list[dict], backup: list[dict], g0: float, g1: float) -> list[dict]:
    """Backup text for a gap is transcribed with a little margin, so its first and last words can repeat
    what the main microphone still caught at the gap's edges. That repeat is removed from the backup text;
    the main microphone's text stays as it is. A trimmed segment keeps Whisper's text in `raw_text`;
    one trimmed away entirely is marked dropped."""
    if not backup:
        return backup
    out = [dict(s) for s in backup]
    words = _words(out)
    kept = {i: [0, len(s["text"])] for i, s in enumerate(out)}  # character range kept per segment
    before = _words([s for s in main if s["start"] < g0 and s["end"] > g0 - 8 and not s.get("dropped")])
    n = head_repeat(_norm(before[-EDGE_WORDS:]), _norm(words[:EDGE_WORDS]))
    for i, m in words[:n]:
        kept[i][0] = max(kept[i][0], m.end())
    after = _words([s for s in main if s["start"] < g1 + 8 and s["end"] > g1 and not s.get("dropped")])
    n = tail_repeat(_norm(after[:EDGE_WORDS]), _norm(words[-EDGE_WORDS:]))
    for i, m in words[len(words) - n:] if n else []:
        kept[i][1] = min(kept[i][1], m.start())
    for i, (a, b) in kept.items():
        seg, text = out[i], out[i]["text"]
        if (a, b) == (0, len(text)):
            continue
        rest = text[a:b].strip(" ,;") if b > a else ""
        seg["raw_text"] = text
        seg["overlap"] = " … ".join(x.strip() for x in (text[:a], text[max(a, b):]) if x.strip())
        if WORD.search(rest):
            seg["text"] = " " + (rest[0].upper() + rest[1:] if a else rest)
        else:
            seg["text"], seg["dropped"] = "", "repeats the main microphone"
    return out


def trim_repeats(text: str, max_n: int = 10, gram: int = 4) -> str:
    """Cut a Whisper loop, typical on very short audio: where a run of words repeats right after itself
    ("divide by 3 again, divide by 3 again, …" → "divide by 3 again,"), or where any 4 words come back
    or starts over ("Right, because we started… Right, because we sta" → "Right, because we started…"). For provisional
    text only: within a few seconds of speech, a repeated 4-word run is almost always a loop."""
    words = text.split()
    norm = [re.sub(r"[^\w']", "", w).lower() for w in words]
    seen: dict[tuple, int] = {}
    for i in range(len(words)):
        for n in range(2, max_n + 1):
            if i + 2 * n <= len(words) and norm[i:i + n] == norm[i + n:i + 2 * n]:
                return " ".join(words[:i + n])
        if i >= 3 and norm[i:i + 3] == norm[:3]:  # starts over from its beginning
            return " ".join(words[:i])
        key = tuple(norm[i:i + gram])
        if len(key) == gram:
            if key in seen and seen[key] + gram <= i:
                return " ".join(words[:i])
            seen.setdefault(key, i)
    return text
