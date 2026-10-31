"""Offline pipeline: audio file → raw Whisper output (next to the audio) → clean transcript (→ summary) in the vault,
then the course's generated files (course_files.py). File names: vault.LectureFiles."""

import json
import os
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

import numpy as np

from . import audio as audio_io
from . import course_files
from . import language
from . import slides as slides_mod
from .config import Config, Course
from .glossary import TEMPLATE as GLOSSARY_TEMPLATE
from .glossary import Glossary, rank_auto_terms, with_auto_terms
from .llm import LLMError, Usage, get_backend
from .llm import guard, tasks
from .render import (Lecture, paragraph_breaks, parse_frontmatter, parse_hms, raw_md, summary_md, time_sections,
                     transcript_md)
from .slides import Slides, SlidesError
from .textproc import (SAMPLE_RATE, fill_gaps, fmt_ts, mark_dropped, retry_sparse, split_at_pauses, to_paragraphs,
                       trim_edge_overlap)
from .vault import LectureFiles, Vault, safe_name


MAX_SLIDE_TERMS = 80  # candidates for Whisper's prompt; the token budget decides how many fit


class LectureError(Exception):
    pass


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


@dataclass
class LiveInfo:
    """What the final pass needs to know about a live recording (see live/session.py)."""
    started_at: datetime
    folder: str                                   # lecture folder name the live draft was written to
    spans: list[tuple[int, float]] = field(default_factory=list)   # (first recorded sample, lecture time in s)
    pauses: list[tuple] = field(default_factory=list)  # (start, end, reason): lecture time not recorded
    source: str = "live recording"
    microphone: str | None = None                 # input device the live session recorded from
    backup: dict | None = None                    # second microphone: {recording, microphone, spans}

    @classmethod
    def load(cls, path: Path) -> "LiveInfo":
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(started_at=datetime.fromisoformat(d["started_at"]), folder=d["folder"],
                   spans=[tuple(s) for s in d["spans"]], pauses=[(p[0], p[1], p[2] if len(p) > 2 else "paused") for p in d["pauses"] if p[1] is not None],
                   source=d.get("source", "live recording"), microphone=d.get("microphone"),
                   backup=d.get("backup"))


BREAK_JUMP = 3.0  # a jump in lecture time this large between spans is a real break (pause, lost microphone)


def lecture_time(spans: list[tuple[int, float]], sample: float) -> float:
    for start, t in reversed(spans):
        if sample >= start:
            return t + (sample - start) / SAMPLE_RATE
    return spans[0][1]


def span_groups(spans: list[tuple[int, float]], n_samples: int) -> list[tuple[int, int]]:
    """Contiguous stretches of recorded audio to transcribe as one piece. Small timeline re-syncs
    (a device delivering a bit less audio than time passed) don't split; real breaks do, so no
    chunk straddles a pause. A 57 min lecture had 415 re-syncs of ~1 s: split at each, it became
    416 mini-pieces, slower and with less context per chunk."""
    groups = [[spans[0][0], n_samples]]
    for (ps, pt), (s, t) in zip(spans, spans[1:]):
        if t - (pt + (s - ps) / SAMPLE_RATE) > BREAK_JUMP:
            groups[-1][1] = s
            groups.append([s, n_samples])
    return [(a, b) for a, b in groups if b > a]


def time_ranges(spans: list[tuple[int, float]], n_samples: int, t0: float, t1: float) -> list[tuple[int, int, float]]:
    """The parts of a recording that cover lecture time [t0, t1]: (first sample, end sample, lecture time)."""
    out = []
    for k, (s, t) in enumerate(spans):
        e = spans[k + 1][0] if k + 1 < len(spans) else n_samples
        lo, hi = max(t0, t), min(t1, t + (e - s) / SAMPLE_RATE)
        if hi > lo:
            a, b = s + int((lo - t) * SAMPLE_RATE), min(e, s + int((hi - t) * SAMPLE_RATE))
            if b > a:
                out.append((a, b, t + (a - s) / SAMPLE_RATE))
    return out


BACKUP_MARGIN = 1.0   # s of backup audio transcribed beyond each gap edge, for whole words at the edges
ALIGN_WINDOW = 20.0   # s of audio next to a gap compared to line the two microphones up
ALIGN_MAX_LAG = 2.0   # s the two timelines may disagree by (each is anchored to the clock when audio arrives)
ALIGN_MIN_CORR = 0.3  # weaker loudness correlation than this: keep the clock alignment


def _envelope(audio: np.ndarray) -> np.ndarray:
    """Loudness per 10 ms, normalized: speech rhythm, comparable across microphones."""
    frames = audio[: len(audio) // 160 * 160].reshape(-1, 160)
    env = np.log(np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-4)
    return (env - env.mean()) / (env.std() + 1e-9)


def backup_shift(main: np.ndarray, main_spans, backup: np.ndarray, backup_spans, g0: float, g1: float) -> float:
    """How far the backup's lecture times run ahead of the main recording's (s), measured on the
    audio just before the gap (or after it), where both microphones heard the lecture. 0 if unclear."""
    for t0, t1 in ((g0 - ALIGN_WINDOW, g0), (g1, g1 + ALIGN_WINDOW)):
        m = time_ranges(main_spans, len(main), t0, t1)
        b = time_ranges(backup_spans, len(backup), t0 - ALIGN_MAX_LAG, t1 + ALIGN_MAX_LAG)
        if t0 < 0 or len(m) != 1 or len(b) != 1:
            continue
        (ma, mb, _), (ba, bb, bt) = m[0], b[0]
        if abs(bt - (t0 - ALIGN_MAX_LAG)) > 0.05 or mb - ma < (t1 - t0 - 0.1) * SAMPLE_RATE:
            continue  # the window isn't fully covered by one stretch of recording
        em, eb = _envelope(main[ma:mb]), _envelope(backup[ba:bb])
        if len(eb) <= len(em) or not np.any(main[ma:mb]):
            continue
        corr = np.correlate(eb, em, mode="valid") / len(em)
        k = int(np.argmax(corr))
        if corr[k] >= ALIGN_MIN_CORR:
            return round(k / 100 - ALIGN_MAX_LAG, 2)
    return 0.0


def fill_from_backup(segments: list[dict], pauses: list[tuple], main: np.ndarray, main_spans,
                     backup: np.ndarray, backup_spans, course: Course, engine, prompt: str | None,
                     lang: str | None) -> tuple[list[dict], list[tuple], float]:
    """Transcribe the backup microphone where the main one delivered nothing ("no input" gaps).
    Filled gaps get the reason "backup". Returns (segments, pauses, seconds filled)."""
    out_pauses, extra, filled = [], [], 0.0
    for p in pauses:
        g0, g1, reason = p[0], p[1], p[2] if len(p) > 2 else "paused"
        covered = sum(b - a for a, b, _ in time_ranges(backup_spans, len(backup), g0, g1)) / SAMPLE_RATE
        if reason != "no input" or covered < 0.5 * (g1 - g0):
            out_pauses.append(p)
            continue
        shift = backup_shift(main, main_spans, backup, backup_spans, g0, g1)
        log(f"Filling {fmt_ts(g0)}–{fmt_ts(g1)} from the backup microphone"
            + (f" (timelines {shift:+.2f} s apart)" if shift else ""))
        here = []
        for a, b, t in time_ranges(backup_spans, len(backup), g0 - BACKUP_MARGIN + shift, g1 + BACKUP_MARGIN + shift):
            if b - a < SAMPLE_RATE // 2:
                continue
            segs, _, _ = transcribe(backup[a:b], course, engine, prompt, lang, [(0, t - shift)])
            here += [{**s, "backup": True} for s in segs if g0 <= (s["start"] + s["end"]) / 2 < g1]
        # backup text starts in the margin before the gap: keep it after the main microphone's last words
        extra += [(max(s["start"], g0), s) for s in trim_edge_overlap(segments, here, g0, g1)]
        out_pauses.append((g0, g1, "backup"))
        filled += g1 - g0
    merged = sorted([(s["start"], s) for s in segments] + extra, key=lambda x: x[0])
    return [s for _, s in merged], out_pauses, filled


def transcribe(audio: np.ndarray, course: Course, engine, prompt: str | None, lang_override: str | None = None,
               spans: list[tuple[int, float]] | None = None,
               skip: list[tuple[float, float]] = ()) -> tuple[list[dict], list[str], str]:
    """Chunked transcription. `spans` maps recorded audio to lecture time for live recordings; each
    stretch between real breaks is transcribed on its own, so no chunk straddles a pause.
    `skip`: lecture-time ranges left out (gaps where the microphone sent only digital silence).
    Returns (segments in lecture time, languages used by share, language note)."""
    spans = spans or [(0, 0.0)]
    bounds = span_groups(spans, len(audio))
    for t0, t1 in skip:
        for a, b, _ in time_ranges(spans, len(audio), t0, t1):
            bounds = [piece for s0, s1 in bounds for piece in ((s0, min(s1, a)), (max(s0, b), s1)) if piece[1] > piece[0]]
    # mixed-language courses use short chunks so a switch is caught at sentence level. Talks keep the normal
    # long chunks and only check each one's language: short chunks cost whole sentences and take ~6× as long
    # (measured), a price for lecturers who really switch back and forth, not for every talk.
    short = course.language == "mixed" and not course.is_event and not lang_override
    chunks = []  # (start sample, end sample, span index)
    for i, (s0, s1) in enumerate(bounds):
        piece = audio[s0:s1]
        cuts = split_at_pauses(piece, max_len=12, min_len=4) if short else split_at_pauses(piece)
        chunks += [(s0 + a, s0 + b, i) for a, b in cuts]
    langs, note = language.plan(audio, [(a, b) for a, b, _ in chunks], course, engine, lang_override)
    log(f"Language: {note}; {len(chunks)} chunks")

    segments, step, done = [], max(1, len(chunks) // 10), 0
    for i, (s0, s1) in enumerate(bounds):
        span_chunks = [(a, b, lang) for (a, b, k), lang in zip(chunks, langs) if k == i]
        local = []
        for a, b, lang in span_chunks:
            off = (a - s0) / SAMPLE_RATE
            for seg in engine.transcribe(audio[a:b], lang, prompt):
                local.append({**seg, "start": seg["start"] + off, "end": min(seg["end"] + off, (b - s0) / SAMPLE_RATE)})
            done += 1
            if done % step == 0 or done == len(chunks):
                log(f"  {done}/{len(chunks)} chunks ({fmt_ts(b / SAMPLE_RATE)} of {fmt_ts(len(audio) / SAMPLE_RATE)})")

        def lang_at(t: float, span_chunks=span_chunks, s0=s0) -> str:
            for a, b, lang in span_chunks:
                if (a - s0) / SAMPLE_RATE <= t < (b - s0) / SAMPLE_RATE:
                    return lang
            return span_chunks[-1][2] if span_chunks else langs[-1]

        if span_chunks:
            piece = audio[s0:s1]
            local = fill_gaps(local, piece, lambda clip, start: engine.transcribe(clip, lang_at(start), prompt))
            local = retry_sparse(local, piece, lambda clip, start: engine.transcribe(clip, lang_at(start), None))
        to_lecture = lambda t, s0=s0: lecture_time(spans, s0 + t * SAMPLE_RATE)
        segments += [{**seg, "start": to_lecture(seg["start"]), "end": to_lecture(seg["end"])} for seg in local]
    used = sorted(set(langs), key=langs.count, reverse=True) if langs else [lang_override or course.languages[0]]
    return segments, used, note


def ensure_glossary(vault: Vault, course: Course) -> Glossary:
    if course.is_event:  # talks and events have no glossary
        return Glossary.parse("")
    path = vault.glossary_path(course.name)
    if not path.exists():
        vault.write(path, GLOSSARY_TEMPLATE.format(course=course.name))
        log(f"Created glossary note {path.name} (add terms there for better spelling)")
    return Glossary.load(path)


def find_slides(vault: Vault, folder: Path, slides_src: Path | None, course: str) -> tuple[Slides | None, Path | None]:
    """Slides for this lecture: a PDF passed on the command line (copied into the folder later), else
    a PDF already in the folder. Returns (slides, file to copy into the folder)."""
    if slides_src:
        try:
            slides = Slides.load(slides_src)
        except SlidesError as e:
            raise LectureError(str(e)) from None
        if not slides.has_text:
            log(f"{slides_src.name} has no text layer (scanned?); it is stored but not used.")
        return (slides if slides.has_text else None), slides_src
    return slides_mod.load_from(vault.files(course, folder).slides(), log), None


def raw_path(cfg: Config, course: "Course | str", folder: Path) -> Path:
    """The raw Whisper output lives next to the audio, outside the vault: it's for checking, not reading."""
    name = course.name if isinstance(course, Course) else course
    base = cfg.data_for(course) if isinstance(course, Course) else cfg.data_dir / course
    return base / f"{safe_name(name)} {folder.name} (raw).md"


def home_relative(path: Path) -> str:
    return str(path).replace(str(Path.home()), "~", 1)


def write_file(path: Path, text: str) -> Path:
    """Atomic write outside the vault (the vault's writes go through vault.Vault)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


RECENT_LECTURES = 5  # automatic glossary terms are ranked by how many of the course's last lectures use them


def update_glossary(vault: Vault, course: str, folder: Path, glossary: Glossary, new: list[str], text: str) -> list[str]:
    """Add checked terms (guard.validate_terms) to the glossary's automatic section and re-rank it by use in
    the course's recent final transcripts. Only that section of the note changes. Returns the added terms."""
    path = vault.glossary_path(course)
    if not path.exists():
        return []
    recent = [text]
    for other in reversed(vault.lectures(course)):
        if len(recent) >= RECENT_LECTURES:
            break
        transcript = vault.files(course, other).existing("transcript")
        if other != folder and transcript:
            body = transcript.read_text(encoding="utf-8")
            if parse_frontmatter(body).get("status") == "final":
                recent.append(body)
    ranked = rank_auto_terms(glossary, new, recent)
    old = path.read_text(encoding="utf-8")
    updated = with_auto_terms(old, ranked)
    if updated != old:
        vault.write(path, updated)
    known = {t.casefold() for t in glossary.auto_terms}
    added = [t for t in ranked if t.casefold() not in known]
    if added:
        log(f"  Glossary: {len(added)} new terms ({', '.join(added[:8])}{' …' if len(added) > 8 else ''})")
    return added


def postprocess(lec: Lecture, folder: Path, segments: list[dict], glossary: Glossary, cfg: Config, vault: Vault, *,
                use_llm: bool = True, summary: bool | None = None, slides: Slides | None = None) -> dict:
    """Segments → transcript: paragraphs, glossary corrections, then the optional AI step (edit list,
    validated by the guard) and the summary. Shared by `process` and `refresh`. The raw output is not touched."""
    files = vault.files(lec.course, folder)
    plain = to_paragraphs(segments, breaks=paragraph_breaks(lec.pauses))
    corrected, n_corr = [], 0
    for p in plain:
        text, n = glossary.apply_corrections(p["text"])
        corrected.append({**p, "text": text, "unsure": [glossary.apply_corrections(u)[0] for u in p["unsure"]]})
        n_corr += n
    paragraphs, sections = corrected, time_sections(corrected)
    lec.structure = "time"
    vault.write(files.target("transcript"), transcript_md(lec, paragraphs, sections))

    usage, applied, rejected, generator, added = Usage(), [], [], None, []
    try:
        backend = get_backend(cfg) if use_llm else None
    except LLMError as e:
        log(f"LLM step skipped: {e} The transcript is complete without it.")
        backend = None
    if backend and corrected:
        try:
            log(f"Structuring with {backend.name}" + (f" (with {slides.count} slides)" if slides else ""))
            proposal, u = tasks.propose_edits(corrected, glossary, lec.languages, backend, slides, vault.topic_names())
            usage += u
            proposed_sections = proposal["sections"]
            fixed, applied, rejected = guard.apply_fixes(corrected, proposal["term_fixes"], glossary, slides)
            unexplained = guard.verify(corrected, fixed, [(f["from"], f["to"]) for f in applied])
            if unexplained:
                log(f"Guard: {len(unexplained)} unexplained changes, keeping text unchanged")
                fixed, applied = corrected, []
            paragraphs = fixed
            sections = guard.validate_sections(proposed_sections, len(fixed), slides.count if slides else 0)
            generator = backend.name
            lec.structure = f"ai ({backend.name})"
            full_text = "\n".join(p["text"] for p in paragraphs)
            lec.topics = [] if lec.kind == "event" else guard.validate_topics(
                proposal["topics"], full_text, slides, vault.topic_names(), reserved=list(cfg.courses))
            vault.write(files.target("transcript"), transcript_md(lec, paragraphs, sections))
            refs = sum(1 for s in sections if s.get("slides"))
            log(f"  {len(sections)} sections" + (f" ({refs} with slide pages)" if slides else "")
                + f", {len(applied)} term fixes applied, {len(rejected)} rejected, {len(lec.topics)} topics")
            new_terms = guard.validate_terms(proposal["terms"], full_text, slides, glossary.terms)
            if lec.kind != "event":
                added = update_glossary(vault, lec.course, folder, glossary, new_terms, full_text)
            if cfg.summary if summary is None else summary:
                log("Writing summary")
                body, u = tasks.summarize(paragraphs, lec.languages, backend)
                usage += u
                vault.write(files.target("summary"), summary_md(lec, body, backend.name))
        except LLMError as e:
            log(f"LLM step skipped: {e} The transcript is complete without it.")
    return {
        "words": sum(len(p["text"].split()) for p in paragraphs), "glossary_corrections": n_corr,
        "term_fixes_applied": applied, "term_fixes_rejected": rejected,
        "slides": slides.count if slides else 0, "topics": lec.topics, "glossary_terms_added": added,
        "llm": {"backend": generator, "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                "cost_usd": round(usage.cost_usd, 5), "billed": usage.billed} if generator else None,
        "_usage": usage,
    }


def log_llm(info: dict) -> None:
    usage = info.pop("_usage")
    if info["llm"]:
        cost = f"${usage.cost_usd:.4f}" + ("" if usage.billed else " at API prices; covered by your subscription")
        log(f"LLM: {usage.input_tokens} in / {usage.output_tokens} out tokens, {cost}")


def process(path: Path, cfg: Config, course: Course, engine, *, lang: str | None = None,
            when: datetime | None = None, use_llm: bool = True, summary: bool | None = None,
            live: LiveInfo | None = None, slides_src: Path | None = None) -> dict:
    """`slides_src`: lecture slides (PDF), copied into the folder as Slides.pdf. Without it, a Slides.pdf
    already in the folder (e.g. dropped there during a live lecture) is used."""
    t0 = time.time()
    vault = Vault(cfg.root_for(course))
    glossary = ensure_glossary(vault, course)

    log(f"Loading {path.name}")
    audio = audio_io.load(path)
    recorded = len(audio) / SAMPLE_RATE
    spans = live.spans if live else None
    pauses = live.pauses if live else []
    # lecture length: recorded audio plus the paused stretches
    duration = (spans[-1][1] + recorded - spans[-1][0] / SAMPLE_RATE) if spans else recorded
    duration = max([duration] + [p[1] for p in pauses])  # a gap at the end (filled from the backup microphone)
    when = when or (live.started_at if live else audio_io.recorded_at(path))
    microphone = live.microphone if live else audio_io.recording_device(path)
    log(f"Length {fmt_ts(duration)}" + (f" ({fmt_ts(recorded)} recorded)" if pauses else "")
        + f", recorded {when:%Y-%m-%d %H:%M}" + (f" with {microphone}" if microphone else ""))

    date = f"{when:%Y-%m-%d}"
    source = live.source if live else path.name
    folder = vault.course_dir(course.name) / live.folder if live else vault.lecture_dir(course.name, date, source)
    slides, slides_copy = find_slides(vault, folder, slides_src, course.name)
    slide_terms = slides.terms(skip=glossary.terms)[:MAX_SLIDE_TERMS] if slides else []
    prompt = glossary.prompt(slide_terms)
    if slides:
        used = len([t for t in slide_terms if prompt and t in prompt])
        log(f"Slides: {slides.count} pages, {used} slide terms in Whisper's prompt")

    gaps = [(p[0], p[1]) for p in pauses if p[2] == "no input"]  # only zeros there, or nothing at all
    segments, languages, lang_note = transcribe(audio, course, engine, prompt, lang, spans, skip=gaps)
    backup_path = Path(live.backup["recording"]) if live and live.backup else None
    backup_mic, filled = None, 0.0
    if backup_path and backup_path.exists() and backup_path.stat().st_size:
        backup_mic = live.backup.get("microphone")
        if gaps:
            backup_audio = audio_io.load(backup_path)
            segments, pauses, filled = fill_from_backup(
                segments, pauses, audio, spans, backup_audio, [tuple(s) for s in live.backup["spans"]],
                course, engine, prompt, lang)
    segments = mark_dropped(segments, prompt)
    t_whisper = time.time() - t0

    audio_dest = cfg.data_for(course) / f"{folder.name}.opus"
    lec = Lecture(course=course.name, date=date, start_time=f"{when:%H:%M}", duration=duration,
                  languages=languages, language_note=lang_note, model=cfg.model, source=source, pauses=pauses,
                  tags=course.tags, audio=str(audio_dest).replace(str(Path.home()), "~") if cfg.keep_audio else None,
                  microphone=microphone, backup_microphone=backup_mic,
                  number=0 if course.is_event else vault.lecture_number(course.name, folder), kind=course.kind)
    files = vault.files(course.name, folder)

    # 1. raw output first, so it exists no matter what happens later
    if slides_copy:
        vault.copy(slides_copy, files.slides() or files.target("slides"))
    lec.names = files.names()
    raw = raw_path(cfg, course, folder)
    write_file(raw, raw_md(lec, segments))
    lec.raw_file = home_relative(raw)
    lec.names = files.names()
    lec.slides = slides.count if slides else 0

    # 2.–3. clean transcript, optional AI structure and summary
    result = postprocess(lec, folder, segments, glossary, cfg, vault, use_llm=use_llm, summary=summary, slides=slides)

    # 4. archive audio + machine-readable data outside the vault
    meta_dir = cfg.data_for(course)
    meta_dir.mkdir(parents=True, exist_ok=True)
    if cfg.keep_audio:
        log(f"Archiving audio → {audio_dest}")
        audio_io.archive(path, audio_dest)
        if filled:  # otherwise the backup holds nothing the main recording doesn't
            audio_io.archive(backup_path, audio_dest.with_name(f"{folder.name}.backup.opus"))
    info = {
        "folder": str(folder), "source": str(path), "duration_s": round(duration, 1),
        "language": lang_note, "filled_from_backup_s": round(filled, 1), **result,
        "timing_s": {"whisper": round(t_whisper, 1), "total": round(time.time() - t0, 1)},
    }
    info.pop("_usage")
    write_meta(meta_dir / f"{folder.name}.segments.json", info, lec, segments)
    if not course.is_event:
        course_files.rebuild(vault, course.name, log)
    speed = duration / max(t_whisper, 1e-6)
    log(f"Done in {fmt_ts(time.time() - t0)} (Whisper {fmt_ts(t_whisper)}, {speed:.1f}x realtime), "
        f"{info['words']} words → {folder}")
    log_llm(result)
    return info


def write_meta(path: Path, info: dict, lec: Lecture, segments: list[dict]) -> None:
    """segments.json: every segment plus what `refresh` needs to rebuild the transcript without the audio."""
    meta = {**info, "lecture": asdict(lec)}
    path.write_text(json.dumps({"meta": meta, "segments": segments}, ensure_ascii=False, indent=1), encoding="utf-8")


def lecture_from_files(folder: Path, meta: dict, session: Path, course: Course) -> Lecture:
    """Lecture metadata for `refresh`: from segments.json (newer runs), else from the frontmatter of the
    existing Transcript.md and, for live lectures, session.json (pause times, microphone)."""
    live = LiveInfo.load(session) if session.exists() else None
    if "lecture" in meta:
        known = {f.name for f in fields(Lecture)}
        lec = Lecture(**{k: v for k, v in meta["lecture"].items() if k in known})
        lec.pauses = [tuple(p) for p in lec.pauses]
    else:
        transcript = LectureFiles(folder, course.name).existing("transcript")
        if not transcript:
            raise LectureError(f"The transcript in {folder} is missing, so the lecture's details are unknown.")
        fm = parse_frontmatter(transcript.read_text(encoding="utf-8"))
        langs = fm.get("languages") or [fm.get("language", course.languages[0])]
        lec = Lecture(course=course.name, date=str(fm.get("date", folder.name[:10])), start_time=str(fm.get("start", "00:00")),
                      duration=meta.get("duration_s") or parse_hms(fm.get("duration", "0:00:00")),
                      languages=langs if isinstance(langs, list) else [langs], language_note=meta.get("language", ""),
                      model=str(fm.get("model", "")), source=str(fm.get("source", "")), tags=course.tags,
                      audio=fm.get("audio"), microphone=fm.get("microphone"),
                      pauses=live.pauses if live else [])
    if live and not lec.microphone:
        lec.microphone = live.microphone
    lec.status = "final"
    return lec


def refresh(cfg: Config, course: Course, folder_name: str, *, use_llm: bool = True, summary: bool | None = None,
            slides_src: Path | None = None) -> dict:
    """Redo only the text steps of a lecture (paragraphs, glossary corrections, AI structure, summary) from its
    segments.json, without re-transcribing. Picks up glossary edits and slides added to the folder later."""
    vault = Vault(cfg.root_for(course))
    folder = vault.course_dir(course.name) / folder_name
    if not folder.is_dir():
        others = sorted(p.name for p in vault.course_dir(course.name).glob(f"{folder_name[:10]}*") if p.is_dir())
        hint = f" Lectures that day: {', '.join(others)}." if others else ""
        raise LectureError(f"No lecture folder {folder}.{hint}")
    meta_path = cfg.data_for(course) / f"{folder.name}.segments.json"
    if not meta_path.exists():
        raise LectureError(f"{meta_path} is missing. A live lecture needs its final pass first (`stenny queue run`).")
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    meta, segments = data["meta"], data["segments"]
    lec = lecture_from_files(folder, meta, cfg.data_for(course) / f"{folder.name}.session.json", course)
    glossary = ensure_glossary(vault, course)
    files = vault.files(course.name, folder)
    if slides_src:
        vault.copy(slides_src, files.slides() or files.target("slides"))
    slides = slides_mod.load_from(files.slides(), log)
    lec.slides = slides.count if slides else 0
    lec.number = 0 if course.is_event else vault.lecture_number(course.name, folder)
    lec.names, lec.kind = files.names(), course.kind
    raw = raw_path(cfg, course, folder)
    lec.raw_file = home_relative(raw) if raw.exists() else None
    log(f"Refreshing {course.name} {folder.name}" + (f" with {slides.count} slides" if slides else "")
        + " (no re-transcription)")
    result = postprocess(lec, folder, segments, glossary, cfg, vault, use_llm=use_llm, summary=summary, slides=slides)
    info = {**meta, **{k: v for k, v in result.items() if k != "_usage"},
            "refreshed_at": datetime.now().isoformat(timespec="seconds")}
    info.pop("lecture", None)
    write_meta(meta_path, info, lec, segments)
    if not course.is_event:
        course_files.rebuild(vault, course.name, log)
    log(f"Done: {info['words']} words → {folder}")
    log_llm(result)
    return info
