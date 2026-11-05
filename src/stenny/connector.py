"""What the Claude connector (`stenny mcp`, see mcp_server.py) can read and do, as plain functions.

Everything returned is clean text, as a classmate would get it from `stenny share`: no frontmatter, no paths on
this Mac, no microphone names, Obsidian links as plain text. Lectures are found through `share.library` and
`vault.LectureFiles`, glossaries through `glossary.Glossary`, slides through `slides.Slides`, so the connector
reads exactly what Stenny's window shows.

The only write is `suggest_glossary_terms`: proposed terms pass the same guard as the automatic terms after each
lecture (`guard.validate_terms`: on the slides, or transcribed the same way at least twice) and go only into the
"Terms (added automatically)" section, through `pipeline.update_glossary` and `vault.py`.
"""

import re
import unicodedata
from pathlib import Path

from .config import ConfigError
from .glossary import Glossary
from .render import parse_frontmatter
from .share import _body, _unlink, clean_summary, clean_transcript, library
from .slides import Slides, SlidesError
from .vault import LectureFiles, Vault, safe_name

PART_CHARS = 40_000   # one part of a transcript (~10k tokens); a 1 h lecture is about 35-40k characters
SLIDES_CHARS = 40_000
SNIPPET_CHARS = 220   # context on each side of a search hit
MAX_RESULTS = 15
TIMESTAMP = re.compile(r"\[(\d+:\d{2}:\d{2})\]")
LATEST = {"latest", "last", "newest", "most recent", "recent"}


class ConnectorError(Exception):
    """A message for Claude (unknown course, no such lecture …), not a crash."""


# ---------------------------------------------------------------- finding courses and lectures

def _fold(text: str) -> str:
    """For comparing and searching: no case, no accents ("Französisch" ~ "franzosisch")."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()


def _courses(cfg) -> dict[str, list[dict]]:
    """Course (or talk) name → its library entries, oldest first."""
    out: dict[str, list[dict]] = {}
    for entry in library(cfg):
        out.setdefault(entry["course"], []).append(entry)
    return out


def _course(cfg, name: str) -> tuple[str, list[dict]]:
    courses = _courses(cfg)
    key = _fold(safe_name(name.strip()))
    for course, entries in courses.items():
        if _fold(safe_name(course)) == key:
            return course, entries
    try:  # an alias from the config ("DA" for "Data Analytics")
        configured = cfg.course(name).name
        if configured in courses:
            return configured, courses[configured]
    except (ConfigError, AttributeError):
        pass
    partial = [c for c in courses if key and key in _fold(c)]
    if len(partial) == 1:
        return partial[0], courses[partial[0]]
    known = ", ".join(courses) or "none yet"
    raise ConnectorError(f"No course or talk called '{name}'. Known: {known}.")


def _lecture_number(entry: dict, index: int) -> int:
    return entry["number"] if isinstance(entry.get("number"), int) and entry["number"] else index + 1


def _lecture(cfg, course: str, lecture: str | int) -> dict:
    """One lecture of a course, by number ("3", "Lecture 3"), date ("2026-10-20", "2026-10-20 (2)") or "latest"."""
    name, entries = _course(cfg, course)
    wanted = str(lecture).strip()
    folded = _fold(wanted)
    if folded in LATEST and entries:
        return entries[-1]
    if m := re.fullmatch(r"(?:lecture|vorlesung|cours|talk|part|nr\.?|no\.?|#)?\s*(\d{1,3})", folded):
        n = int(m.group(1))
        for i, entry in enumerate(entries):
            if _lecture_number(entry, i) == n:
                return entry
    for entry in entries:
        if entry["folder"] == wanted:
            return entry
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", wanted):  # a date with two recordings: the first; "(2)" for the next
        same_day = [e for e in entries if e["folder"][:10] == wanted]
        if same_day:
            return same_day[0]
    listed = ", ".join(_label(e, i) for i, e in enumerate(entries))
    raise ConnectorError(f"{name} has no lecture '{lecture}'. Its lectures: {listed}.")


def _label(entry: dict, index: int) -> str:
    if entry["kind"] == "event":
        return entry["folder"]
    return f"Lecture {_lecture_number(entry, index)} ({entry['folder']})"


def _cite(entry: dict, index: int, time: str | None, with_course: bool = False) -> str:
    """How to cite a passage, ready to copy: "Lecture 3 [0:02:05]". The full h:mm:ss, since a shortened
    "0:02" reads as two seconds."""
    where = entry["folder"] if entry["kind"] == "event" else f"Lecture {_lecture_number(entry, index)}"
    if with_course or entry["kind"] == "event":
        where = f"{entry['course']}, {where}"
    return where + (f" [{time}]" if time else "")


def _title(entry: dict, entries: list[dict]) -> str:
    return f"{entry['course']}, {_label(entry, entries.index(entry))}"


def _status_note(entry: dict) -> str:
    if entry["status"] == "final":
        return ""
    return f" [live draft, rough text. {entry.get('draft_note') or 'The final transcript replaces it later.'}]"


# ---------------------------------------------------------------- transcripts as paragraphs

def _blocks(text: str) -> list[str]:
    return [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]


def _paragraphs(text: str) -> list[dict]:
    """The clean transcript as paragraphs with their timestamp and section title."""
    out, section, last_time = [], "", None
    for block in _blocks(text):
        if block.startswith("## "):
            m = TIMESTAMP.match(block[3:])
            section = TIMESTAMP.sub("", block[3:], count=1).strip()
            last_time = m.group(1) if m else last_time
            continue
        if block.startswith(("# ", ">")):
            continue
        m = TIMESTAMP.search(block[:20])
        if m:
            last_time = m.group(1)
        text_only = re.sub(r"^\*\*\[\d+:\d{2}:\d{2}\]\*\*\s*", "", block)
        out.append({"time": last_time, "section": section, "text": text_only})
    return out


def _clean(entry: dict) -> str:
    return clean_transcript(Path(entry["transcript"]).read_text(encoding="utf-8"))


def _parts(text: str, size: int = PART_CHARS) -> list[str]:
    """The transcript cut into parts of about `size` characters, at paragraph breaks."""
    parts, current = [], ""
    for block in _blocks(text):
        if current and len(current) + len(block) > size:
            parts.append(current)
            current = ""
        current = f"{current}\n\n{block}" if current else block
    if current:
        parts.append(current)
    return parts or [""]


def _seconds(hms: str) -> int:
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


def _span(part: str) -> str:
    times = TIMESTAMP.findall(part)
    return f"{times[0]}–{times[-1]}" if times else "no timestamps"


# ---------------------------------------------------------------- the tools

def list_courses(cfg) -> str:
    courses = _courses(cfg)
    lectures = {c: e for c, e in courses.items() if e[0]["kind"] == "lecture"}
    talks = {c: e for c, e in courses.items() if e[0]["kind"] == "event"}
    vault = Vault(cfg.lectures_root)
    lines = ["Courses (lectures transcribed by Stenny):"]
    for course, entries in lectures.items():
        glossary = Glossary.load(vault.glossary_path(course))
        dates = (f"{entries[0]['folder'][:10]} to {entries[-1]['folder'][:10]}" if len(entries) > 1
                 else f"on {entries[0]['folder'][:10]}")
        lines.append(f"- {course}: {len(entries)} lecture{'s' * (len(entries) != 1)}, {dates}"
                     + (f", glossary with {len(glossary.terms)} terms" if glossary.terms else ""))
    empty = [c.name for c in getattr(cfg, "visible_courses", {}).values() if c.name not in lectures]
    if empty:
        lines.append(f"Configured but no transcripts yet: {', '.join(empty)}.")
    if not lectures:
        lines.append("- none yet")
    if talks:
        lines += ["", "Talks and events:"]
        lines += [f"- {course}: {', '.join(e['folder'] for e in entries)}" for course, entries in talks.items()]
    return "\n".join(lines)


def list_lectures(cfg, course: str) -> str:
    name, entries = _course(cfg, course)
    files = [LectureFiles(Path(e["transcript"]).parent, Path(e["transcript"]).parent.parent.name) for e in entries]
    lines = [f"{name}: {len(entries)} recording{'s' * (len(entries) != 1)}, oldest first."]
    for i, (entry, lf) in enumerate(zip(entries, files)):
        head = parse_frontmatter(Path(entry["transcript"]).read_text(encoding="utf-8")[:16384])
        topics = [_unlink(t) for t in head.get("topics") or []]
        sections = [p for p in _section_titles(entry)]
        details = ", ".join(x for x in [entry.get("start") and f"started {entry['start']}",
                                         entry.get("duration") and f"length {entry['duration']}",
                                         entry.get("language") and f"language {entry['language']}"] if x)
        lines.append(f"\n## {_label(entry, i)}{_status_note(entry)}")
        lines.append(f"Date {entry['date']}" + (f", {details}" if details else ""))
        if topics:
            lines.append(f"Topics: {', '.join(topics)}")
        if sections:
            lines.append(f"Sections: {'; '.join(sections)}")
        lines.append("Has: transcript" + (", summary" if entry.get("summary") else "")
                     + (", slides" if lf.slides() else ""))
    return "\n".join(lines)


def _section_titles(entry: dict) -> list[str]:
    out = []
    for line in Path(entry["transcript"]).read_text(encoding="utf-8").splitlines():
        if line.startswith("## [") and "] " in line:
            title = re.sub(r"\s*\(\[\[.*$", "", line.split("] ", 1)[1]).strip()
            if title:
                out.append(f"[{TIMESTAMP.match(line[3:]).group(1)}] {title}")
    return out


def get_transcript(cfg, course: str, lecture: str | int, part: int = 1, at: str | None = None) -> str:
    entry = _lecture(cfg, course, lecture)
    name, entries = _course(cfg, course)
    parts = _parts(_clean(entry), PART_CHARS)
    if at:
        if not re.fullmatch(r"\d+:\d{2}:\d{2}", at.strip()):
            raise ConnectorError(f"'at' must be a timestamp like 1:05:30, not '{at}'.")
        target = _seconds(at.strip())
        part = 1
        for i, p in enumerate(parts, 1):
            times = TIMESTAMP.findall(p)
            if times and _seconds(times[0]) <= target:
                part = i
    if not 1 <= part <= len(parts):
        raise ConnectorError(f"{_title(entry, entries)} has {len(parts)} part(s); ask for part 1 to {len(parts)}.")
    head = f"[{_title(entry, entries)}{_status_note(entry)} · part {part} of {len(parts)} · {_span(parts[part - 1])}"
    head += f" · next: get_transcript with part={part + 1}]" if part < len(parts) else "]"
    times = TIMESTAMP.findall(parts[part - 1])
    if times:
        head += (f"\n[Name this lecture once in your answer ({_cite(entry, entries.index(entry), None)}), then cite a "
                 f"passage by its paragraph's time only, e.g. [{times[0]}]: at most once per point, never "
                 f"shortened to h:mm.]")
    return f"{head}\n\n{parts[part - 1]}\n"


def get_summary(cfg, course: str, lecture: str | int) -> str:
    entry = _lecture(cfg, course, lecture)
    _, entries = _course(cfg, course)
    if not entry.get("summary"):
        raise ConnectorError(f"{_title(entry, entries)} has no summary (yet). Read the transcript instead.")
    text = clean_summary(Path(entry["summary"]).read_text(encoding="utf-8"))
    text = text.replace("of the transcript below", "of the transcript (get_transcript)")
    text = text.replace("Timestamps point into the transcript below.", "Timestamps point into the transcript.")
    return f"# Summary: {_title(entry, entries)}\n\n{text}\n"


def _snippet(text: str, at: int, length: int) -> str:
    start, end = max(0, at - SNIPPET_CHARS), min(len(text), at + length + SNIPPET_CHARS)
    while start > 0 and text[start - 1] not in " \n":
        start -= 1
    while end < len(text) and text[end] not in " \n":
        end += 1
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def _term_pattern(term: str) -> str:
    """One query word as a pattern on folded text: at the start of a word ("cluster" finds "clustering", "ward"
    doesn't find "forward"), its parts in order with any punctuation or space between ("k-means" finds
    "k-means", "k means" and "kmeans")."""
    return r"(?<!\w)" + r"[\W_]*".join(re.escape(t) for t in re.findall(r"\w+", term))


def _matches(paragraphs: list[tuple], terms: list[str]) -> list[tuple[int, tuple, int]]:
    """(0 for the whole phrase, 1 for all terms anywhere; the paragraph; where the first term is)."""
    each = [re.compile(_term_pattern(t)) for t in terms]
    phrase = re.compile(r"[\W_]+".join(_term_pattern(t) for t in terms))
    out = []
    for item in paragraphs:
        folded = item[-1]
        if not all(rx.search(folded) for rx in each):
            continue
        exact = phrase.search(folded)
        out.append((0 if exact else 1, item, (exact or each[0].search(folded)).start()))
    return out


def _similar(terms: list[str], paragraphs: list[tuple]) -> dict[str, str]:
    """For query words that occur nowhere: the closest spelling in the transcripts ("dendrogram" →
    "dendogram"), since Whisper often writes a term slightly differently."""
    import difflib
    from collections import Counter
    vocab = Counter(w for item in paragraphs for w in re.findall(r"\w+", item[-1]) if len(w) > 3)
    out = {}
    for term in terms:
        if len(term) < 5 or not re.fullmatch(r"\w+", term) or term in vocab:
            continue
        close = difflib.get_close_matches(term, vocab, n=5, cutoff=0.8)
        if close:
            out[term] = max(close, key=lambda w: (difflib.SequenceMatcher(None, term, w).ratio(), vocab[w]))
    return out


def search_lectures(cfg, query: str, course: str | None = None, max_results: int = MAX_RESULTS) -> str:
    """Paragraphs that contain the whole phrase first, then those with all of its words (word starts count:
    "cluster" finds "clustering"). Case and accents don't matter. If a word occurs nowhere, its closest
    spelling in the transcripts is searched instead, and the result says so."""
    terms = [t for t in _fold(query).split() if re.search(r"\w", t)]
    if not terms:
        raise ConnectorError("Search for at least one word.")
    if course:
        name, entries = _course(cfg, course)
        scope = {name: entries}
    else:
        scope = _courses(cfg)
    paragraphs = [(entry, i, p, _fold(p["text"]))
                  for entries in scope.values() for i, entry in enumerate(entries) for p in _paragraphs(_clean(entry))]
    hits, note = _matches(paragraphs, terms), ""
    if not hits and (similar := _similar(terms, paragraphs)):
        hits = _matches(paragraphs, [similar.get(t, t) for t in terms])
        if hits:
            note = ("No exact matches. Showing the closest spelling in the transcripts: "
                    + ", ".join(f"'{a}' → '{b}'" for a, b in similar.items()) + ".\n")
    if not hits:
        where = f" in {next(iter(scope))}" if course else ""
        alone = [f"'{t}' {len(_matches(paragraphs, [t]))}×" for t in terms] if len(terms) > 1 else []
        return (f"No matches for '{query}'{where}."
                + (f" Each word alone (paragraphs): {', '.join(alone)}." if alone else "")
                + " These are automatic transcripts: try another spelling or a shorter query (one distinctive "
                "word), the course's glossary (get_glossary) for how terms are spelled, or the summaries.")
    hits.sort(key=lambda h: h[0])  # stable: within each kind, courses and lectures stay in order
    shown = hits[:max(1, min(max_results, 50))]
    lines = [f"{note}{len(hits)} match{'es' * (len(hits) != 1)} for '{query}'"
             + (f", showing the first {len(shown)}" if len(shown) < len(hits) else "") + ":"]
    for _, (entry, i, p, _), pos in shown:
        where = f"{entry['course']}, {_label(entry, i)}" + (f" [{p['time']}]" if p["time"] else "")
        section = f" — section: {p['section']}" if p["section"] else ""
        lines.append(f"\n- {where}{section}{_status_note(entry)}\n  {_snippet(p['text'], pos, len(terms[0]))}"
                     f"\n  cite as: {_cite(entry, i, p['time'], with_course=not course)}")
    lines.append("\nA hit's \"cite as\" line says which lecture and time it is. In your answer, name each lecture "
                 "once and then give only the time in brackets, at most once per point, with the full h:mm:ss. "
                 "Read more around a hit with get_transcript(course, lecture, at=\"h:mm:ss\").")
    return "\n".join(lines)


def get_glossary(cfg, course: str) -> str:
    name, _ = _course(cfg, course)
    path = Vault(cfg.lectures_root).glossary_path(name)
    if not path.exists():
        return f"{name} has no glossary yet."
    g = Glossary.load(path)
    manual = [t for t in g.terms if t not in g.auto_terms]
    lines = [f"# Glossary: {name}", "",
             "Correct spellings of names and technical terms in this course. The transcripts may misspell them.", "",
             "## Terms (kept by the student)", *(f"- {t}" for t in manual or ["(none)"]), "",
             "## Terms (added automatically from transcripts and slides)", *(f"- {t}" for t in g.auto_terms or ["(none)"]),
             "", "## Corrections (applied to new transcripts)",
             *(f"- {w} → {r}" for w, r in g.corrections or []), *(["(none)"] if not g.corrections else [])]
    return "\n".join(lines)


def _slides(entry: dict) -> tuple[Path | None, Slides | None]:
    folder = Path(entry["transcript"]).parent
    path = LectureFiles(folder, folder.parent.name).slides()
    if not path:
        return None, None
    try:
        return path, Slides.load(path)
    except SlidesError as e:
        raise ConnectorError(str(e)) from None


def get_slides_text(cfg, course: str, lecture: str | int, first_page: int = 1, last_page: int | None = None) -> str:
    entry = _lecture(cfg, course, lecture)
    _, entries = _course(cfg, course)
    title = _title(entry, entries)
    path, slides = _slides(entry)
    if not slides:
        return f"{title} has no slides. Stenny only has what the microphone heard."
    if not slides.has_text:
        return f"The slides of {title} ({slides.count} pages) have no text layer (scanned?), so there is nothing to read."
    last = min(last_page or slides.count, slides.count)
    if not 1 <= first_page <= last:
        raise ConnectorError(f"The slides of {title} have pages 1 to {slides.count}.")
    out, size, i = [], 0, first_page
    for i in range(first_page, last + 1):
        block = f"[Slide {i}] {' '.join(slides.pages[i - 1].split()) or '(no text)'}"
        if out and size + len(block) > SLIDES_CHARS:
            out.append(f"[Slides {i}–{last} not shown: ask with first_page={i}]")
            break
        out.append(block)
        size += len(block)
    return f"[Slides of {title}: {slides.count} pages, text only]\n\n" + "\n\n".join(out) + "\n"


def suggest_glossary_terms(cfg, course: str, terms: list[str]) -> str:
    """Add terms to the glossary's automatic section, if the course's transcripts or slides back them up."""
    from .llm import guard
    from .pipeline import update_glossary

    name, entries = _course(cfg, course)
    if entries[0]["kind"] != "lecture":
        raise ConnectorError(f"'{name}' is a talk, not a course: talks have no glossary.")
    vault = Vault(cfg.lectures_root)
    path = vault.glossary_path(name)
    if not path.exists():
        raise ConnectorError(f"{name} has no glossary note yet; Stenny makes one with the course's first lecture.")
    glossary = Glossary.load(path)
    finals = [e for e in entries if e["status"] == "final"]
    texts = [_body(Path(e["transcript"]).read_text(encoding="utf-8")) for e in finals]
    pages = [page for e in finals for page in ((_slides(e)[1] or Slides()).pages)]
    known = [*glossary.terms, *(w for w, _ in glossary.corrections)]
    known_keys = {k.casefold() for k in known}
    text, slides = "\n\n".join(texts), Slides(pages) if pages else None
    accepted, already, rejected = [], [], []
    for term in dict.fromkeys(t for t in terms[:50] if isinstance(t, str) and t.strip()):
        if " ".join(term.split()).strip(" .,;:").casefold() in known_keys:
            already.append(term)
        elif ok := guard.validate_terms([term], text, slides, [*known, *accepted]):
            accepted += ok  # may carry the transcript's spelling instead of the proposed one
        else:
            rejected.append(term)
    added = []
    if accepted:
        latest = Path(finals[-1]["transcript"])
        added = update_glossary(vault, name, latest.parent, glossary, accepted, texts[-1])
    lines = [f"Added to {name}'s glossary (Terms added automatically): {', '.join(added)}" if added
             else f"Nothing added to {name}'s glossary."]
    if accepted and not added:
        lines.append(f"Accepted but already there or ranked out: {', '.join(accepted)}.")
    if already:
        lines.append(f"Already in the glossary: {', '.join(already)}.")
    if rejected:
        lines.append(f"Not added (not on the slides, and not transcribed this way at least twice): "
                     f"{', '.join(rejected)}. If one is a misheard spelling, ask the student to add a correction "
                     "'wrong → right' to the glossary note.")
    return "\n".join(lines)
