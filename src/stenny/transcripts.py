"""Transcripts someone sent you (#138): read into paragraphs, word for word, to be added to a course as they are.

Stenny's own files come back with what they knew: a lecture file from another Stenny folder, or a copy made with
Share… (Markdown, Word, HTML, PDF), with or without its summary at the top. They're recognised by their title line
("Calculus – Lecture 3 (2026-10-20)"), the note under it ("Automatic transcript (English) of a lecture recorded on
…") or the frontmatter; date, start, length, own title, section headings, every [h:mm:ss] and the summary come along.

Other apps' transcripts: WebVTT and SRT (Zoom, Teams, MacWhisper and many more: each cue's start time, short cues of
one speaker joined into paragraphs), text and Markdown (Otter, MacWhisper …: "Speaker 1  0:04" headers and
timestamps written in the text, like 00:12:34, [12:34] or 0:12:34, are picked up), Word and PDF (the text; line
breaks inside paragraphs, page numbers and running headers are cleaned up).

Nothing is invented: the text is taken as it is. Only what holds it together is dropped (a Stenny copy's own note,
title and credit line, which Stenny writes again; page numbers; repeated speaker labels within one paragraph).
Word files are read with Python's own zip and XML tools, PDFs with pypdf, so it's the same on macOS and Windows.
A PDF without real text (a scan) can't be read without OCR: that's said, nothing is added.
"""

import html
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from .i18n import tr
from .render import parse_frontmatter
from .textproc import fmt_hms

EXTS = {".md", ".markdown", ".txt", ".vtt", ".srt", ".docx", ".html", ".htm", ".pdf"}
PARAGRAPH_CHARS = 700   # cues of one speaker are joined into a paragraph up to about this long …
CUE_GAP_S = 2.5         # … unless the speaker pauses this long
SCAN_CHARS = 40         # a PDF with less text than this per page is a scan


class TranscriptError(Exception):
    """A message for the person using the app."""


@dataclass
class Transcript:
    paragraphs: list[dict]                       # {"start": seconds or None, "text": …}
    sections: list[dict] = field(default_factory=list)  # Stenny's headings: {"at": paragraph index, "title": … or None}
    summary: str | None = None                   # a Stenny copy's summary, as Markdown (its headings from ##)
    by: str | None = None                        # "Stenny", "Otter", "Zoom", "Teams", "MacWhisper", "Word" or None
    date: str | None = None                      # YYYY-MM-DD, when the file says
    start: str | None = None                     # HH:MM, when the file says
    seconds: float | None = None                 # the length, when the file says or its times show
    title: str | None = None                     # a Stenny lecture's own title ("Decision trees")

    @property
    def timestamps(self) -> bool:
        return any(p["start"] is not None for p in self.paragraphs)

    @property
    def words(self) -> int:
        return sum(len(p["text"].split()) for p in self.paragraphs)


def is_transcript(path: Path) -> bool:
    return path.suffix.lower() in EXTS


# ---------------------------------------------------------------- times

TS = r"(?:(\d{1,2}):)?(\d{1,3}):(\d{2})(?:[.,]\d{1,3})?"


def seconds(h: str | None, m: str, s: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s)


def _ts(text: str) -> float | None:
    m = re.fullmatch(TS, text.strip())
    return seconds(*m.groups()) if m else None


# ---------------------------------------------------------------- the file as text

def decode(data: bytes) -> str:
    """UTF-8 (with or without BOM), UTF-16 with BOM, otherwise Windows-1252."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _clean(text: str) -> str:
    return re.sub(r"[ \t ]+", " ", text).strip()


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_blocks(path: Path) -> list[str]:
    """A Word file's paragraphs as Markdown blocks: headings by their style ("Heading 2", Stenny's own on Windows)
    or, as macOS's textutil writes them, by their size; bullets as "- "."""
    try:
        with zipfile.ZipFile(path) as z:
            root = ElementTree.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as e:
        raise TranscriptError(tr("“{name}” couldn't be read: {error}", name=path.name, error=str(e))) from None
    out = []
    for p in root.iter(f"{W}p"):
        text = "".join((t.text or "") if t.tag == f"{W}t" else "\t" if t.tag == f"{W}tab" else "\n" if t.tag in (
            f"{W}br", f"{W}cr") else "" for t in p.iter())
        text = _clean(text.replace("\n", " "))
        if not text:
            continue
        style = p.find(f"{W}pPr/{W}pStyle")
        name = (style.get(f"{W}val") or "") if style is not None else ""
        level = 0
        if m := re.fullmatch(r"(?i)heading ?(\d)|berschrift(\d)", name):
            level = int(m.group(1) or m.group(2))
        elif name.lower() == "title":
            level = 1
        else:
            sizes = [int(s.get(f"{W}val")) for s in p.iter(f"{W}sz") if (s.get(f"{W}val") or "").isdigit()]
            bold = p.find(f".//{W}b") is not None
            size = max(sizes, default=0)
            level = 1 if size >= 30 else 2 if size >= 25 and bold else 3 if size >= 23 and bold else 0
        bullet = p.find(f"{W}pPr/{W}numPr") is not None or text.startswith("•")
        if bullet:
            text = "- " + text.lstrip("•").strip()
        out.append(("#" * level + " " + text) if level else text)
    return out


class _Html(HTMLParser):
    """An HTML page as Markdown blocks: headings, paragraphs, list items, quotes."""

    BLOCKS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "br", "tr", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.current: list[str] = []
        self.stack: list[str] = []
        self.skip = 0

    def flush(self):
        text = _clean("".join(self.current))
        self.current = []
        if not text:
            return
        tag = next((t for t in reversed(self.stack) if t in self.BLOCKS), "p")
        quote = "blockquote" in self.stack
        if tag[0] == "h" and tag[1:].isdigit():
            text = "#" * int(tag[1]) + " " + text
        elif tag == "li":
            text = "- " + text
        self.blocks.append(("> " if quote else "") + text)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
        if tag in self.BLOCKS:
            self.flush()
        if tag == "hr":
            self.blocks.append("---")
        if tag not in ("br", "hr", "meta", "link", "img"):
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self.skip = max(0, self.skip - 1)
        if tag in self.BLOCKS:
            self.flush()
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass

    def handle_data(self, data):
        if not self.skip:
            self.current.append(data)


def html_blocks(text: str) -> list[str]:
    parser = _Html()
    parser.feed(text)
    parser.flush()
    return parser.blocks


PAGE_NUMBER = re.compile(r"(?i)(page|seite)?\s*\d{1,4}(\s*(/|of|von)\s*\d{1,4})?|[-–]\s*\d{1,4}\s*[-–]")


def pdf_pages(path: Path) -> list[list[str]]:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:  # pragma: no cover - a dependency of Stenny's
        raise TranscriptError("Reading PDFs needs pypdf (`uv sync`).") from None
    try:
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except (PdfReadError, OSError, ValueError, KeyError) as e:
        raise TranscriptError(tr("“{name}” couldn't be read: {error}", name=path.name, error=str(e))) from None
    if sum(len(re.sub(r"\s", "", p)) for p in pages) < SCAN_CHARS * max(1, len(pages)):
        raise TranscriptError(tr("“{name}” has no text Stenny can read, probably a scan. Nothing was added.",
                                 name=path.name))
    return [[_clean(l) for l in p.splitlines() if _clean(l)] for p in pages]


def pdf_lines(pages: list[list[str]]) -> list[str]:
    """Every page's lines without page numbers and the running header and footer (a line, its digits aside, that
    starts or ends most pages)."""
    def key(line: str) -> str:
        return re.sub(r"\d+", "#", line.casefold())
    edges: dict[str, int] = {}
    for lines in pages:
        for line in {*lines[:2], *lines[-2:]}:
            edges[key(line)] = edges.get(key(line), 0) + 1
    running = {k for k, n in edges.items() if len(pages) >= 3 and n >= len(pages) * 0.6}
    out = []
    for lines in pages:
        for i, line in enumerate(lines):
            at_edge = i < 2 or i >= len(lines) - 2
            if at_edge and (PAGE_NUMBER.fullmatch(line) or key(line) in running):
                continue
            out.append(line)
    return out


STENNY_STARTS = ("Automatic transcript (", "Audio only:", "AI-generated summary", "Timestamps point into the transcript")


def join_lines(lines: list[str]) -> list[str]:
    """Lines of a PDF as paragraphs: a paragraph ends where a line ends a sentence and is shorter than the lines
    around it; a timestamp, a speaker's header or one of Stenny's own lines starts a new one. A word broken at a
    hyphen is joined again, hyphen kept."""
    if not lines:
        return []
    full = sorted(len(l) for l in lines)[int(len(lines) * 0.8)]
    blocks: list[str] = []
    last = ""  # the line before, as it was on the page
    for line in lines:
        starts_new = (not blocks or re.match(r"\[\d+:\d{2}:\d{2}\]", line) or HEADER.fullmatch(line)
                      or line.startswith(STENNY_STARTS) or line == "Summary" or line.startswith(("• ", "- ")))
        ended = bool(re.search(r"[.!?:…\"”)\]]$", last)) and len(last) < full * 0.85
        alone = last == "Summary" or re.fullmatch(r"\[\d+:\d{2}:\d{2}\]", last)
        if starts_new or ended or alone:
            blocks.append(line)
        elif last.endswith("-") and line[:1].islower():
            blocks[-1] += line
        else:
            blocks[-1] += " " + line
        last = line
    return blocks


def text_blocks(text: str) -> list[str]:
    return [b.strip() for b in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")) if b.strip()]


# ---------------------------------------------------------------- Stenny's own files

# "Calculus – Lecture 3 (2026-10-20)", "Calculus – Lecture 3: Decision trees (2026-10-20 (2))", a talk "Guest talk – 2026-10-20"
TITLE = re.compile(r"#?\s*(?P<course>.+?) – Lecture (?P<number>\d+)(?:: (?P<title>.+?))? \((?P<date>\d{4}-\d{2}-\d{2})[^)]*\)?\)")
TALK_TITLE = re.compile(r"#?\s*(?P<course>.+?) – (?P<date>\d{4}-\d{2}-\d{2})(?:: (?P<title>.+))?")
NOTE = re.compile(r"recorded on (?P<date>\d{4}-\d{2}-\d{2}) at (?P<start>\d{1,2}:\d{2}), length (?P<length>\d+:\d{2}:\d{2})")
STAMPED = re.compile(r"(?:\*\*)?\[(\d+:\d{2}:\d{2})\](?:\*\*)?\s*(.*)", re.S)
CREDIT = re.compile(r"Transcribed with Stenny · stenny\.eu")


def _stenny(blocks: list[str], fm: dict) -> Transcript | None:
    """A Stenny lecture file or shared copy, or None if it isn't one."""
    head = [b.lstrip("#> ").strip() for b in blocks[:6]]
    title = next((m for b in head[:2] if (m := TITLE.fullmatch(b) or TALK_TITLE.fullmatch(b))), None)
    note = next((NOTE.search(b) for b in head if b.startswith("Automatic transcript (") and NOTE.search(b)), None)
    if not (fm.get("model") or note):
        return None
    t = Transcript(paragraphs=[], by="Stenny")
    if note:
        t.date, t.start = note["date"], note["start"].zfill(5)
        t.seconds = _ts(note["length"])
    if title:
        t.date = t.date or title["date"]
        t.title = title.groupdict().get("title")
    if fm:
        t.date = str(fm.get("date") or t.date or "") or None
        t.start = str(fm.get("start") or t.start or "") or None
        t.seconds = _ts(str(fm["duration"])) if fm.get("duration") else t.seconds
        t.title = fm.get("title") or t.title
    summary: list[str] | None = None
    in_summary = False
    for raw in blocks:
        block = raw.strip()
        plain = block.lstrip("#> ").strip()
        if not t.paragraphs and not in_summary and (block.startswith("# ") or (title and plain == title.group(0).strip())):
            continue  # the title: Stenny writes it again
        if plain.startswith(STENNY_STARTS) or plain.startswith("Every segment, unfiltered") or CREDIT.search(plain):
            continue  # its notes and credit line: Stenny writes its own
        if re.fullmatch(r"#{1,3} Summary", block) or (block == "Summary" and not t.paragraphs):
            in_summary, summary = True, []
            continue
        if block == "---" or re.fullmatch(r"[-*_]{3,}", block):
            in_summary = False
            continue
        heading = re.fullmatch(r"(#{2,6}) (.*)", block, re.S)
        stamped = STAMPED.fullmatch(heading.group(2) if heading else block)
        if in_summary and not stamped:
            if heading:  # the copy put the summary's headings one level down
                block = "#" * max(2, len(heading.group(1)) - 1) + " " + heading.group(2)
            summary.append(block)
            continue
        in_summary = False
        if heading and stamped:  # "## [0:10:00] Decision trees"
            t.sections.append({"at": len(t.paragraphs), "title": stamped.group(2).strip() or None})
        elif stamped:
            t.paragraphs.append({"start": _ts(stamped.group(1)), "text": stamped.group(2).strip()})
        elif heading:
            t.paragraphs.append({"start": None, "text": heading.group(2).strip()})
        else:
            t.paragraphs.append({"start": None, "text": block})
    if not t.sections:  # a PDF: its headings are lines like any other
        _stenny_headings_from_lines(t)
    t.summary = _summary(summary) if summary else None
    return t


def _summary(blocks: list[str]) -> str | None:
    """A copy's summary as Markdown. A PDF has lost its headings and lists: there a short line without a full stop
    is a heading again, and "• " a bullet."""
    if not any(b.startswith("#") for b in blocks):
        blocks = ["- " + b[2:] if b.startswith("• ") else
                  "## " + b if len(b) < 60 and not re.search(r"[.!?…:)\]]$", b) and not b.startswith("- ") else b
                  for b in blocks]
    return "\n\n".join(blocks).strip() or None


def _stenny_headings_from_lines(t: Transcript) -> None:
    """A PDF has no headings, only lines: "[0:10:00] Decision trees" right before a paragraph of the same time is
    a section's heading, as is a bare "[0:10:00]"."""
    keep, sections = [], {s["at"]: s for s in t.sections}
    for i, p in enumerate(t.paragraphs):
        nxt = t.paragraphs[i + 1] if i + 1 < len(t.paragraphs) else None
        if (p["start"] is not None and nxt and nxt["start"] == p["start"] and len(p["text"]) < 100
                and not re.search(r"[.!?…]$", p["text"])) or (p["start"] is not None and not p["text"]):
            sections.setdefault(len(keep), {"at": len(keep), "title": p["text"] or None})
            continue
        keep.append(p)
    t.paragraphs = keep
    t.sections = sorted(sections.values(), key=lambda s: s["at"])


# ---------------------------------------------------------------- other apps' files

CUE_TIME = re.compile(rf"{TS}\s*-->\s*{TS}")
VOICE = re.compile(r"<v(?:\.[\w.]+)?\s+([^>]+)>")
SPEAKER = re.compile(r"([A-ZÄÖÜ][\w.'’-]*(?: [\w.'’-]+){0,3}|Speaker \d+|Sprecher \d+)\s*:\s+(.*)", re.S)
HEADER = re.compile(rf"(?P<name>[^\d\[\]():][^\[\]():]{{0,40}}?)?\s*[\[(]?{TS}[\])]?")
LEADING = re.compile(rf"[\[(]{TS}[\])]\s*[-–:]?\s*(?P<rest>.+)|{TS}\s+[-–:]?\s*(?P<rest2>\S.*)", re.S)


def cues(text: str) -> list[dict]:
    """WebVTT or SRT cues: {"start", "end", "text", "speaker"}."""
    out = []
    for block in text_blocks(text):
        lines = block.splitlines()
        at = next((i for i, l in enumerate(lines) if CUE_TIME.search(l)), None)
        if at is None:
            continue  # WEBVTT, NOTE, STYLE, a lone number
        m = CUE_TIME.search(lines[at])
        start, end = seconds(*m.groups()[:3]), seconds(*m.groups()[3:])
        body = " ".join(lines[at + 1:]).strip()
        speaker = None
        if v := VOICE.search(body):
            speaker = v.group(1).strip()
        body = html.unescape(re.sub(r"<[^>]+>", "", body)).strip()
        if speaker is None and (s := SPEAKER.fullmatch(body)):
            speaker, body = s.group(1), s.group(2).strip()
        if body:
            out.append({"start": start, "end": end, "text": _clean(body), "speaker": speaker})
    return out


def from_cues(found: list[dict]) -> list[dict]:
    """Cues of one speaker joined into paragraphs, the speaker's name once at the start of each."""
    named = sum(1 for c in found if c["speaker"]) >= len(found) * 0.3
    paragraphs: list[dict] = []
    last = None
    for c in found:
        same = last and c["speaker"] == last["speaker"] and c["start"] - last["end"] <= CUE_GAP_S \
            and len(paragraphs[-1]["text"]) < PARAGRAPH_CHARS
        if same:
            paragraphs[-1]["text"] += " " + c["text"]
        else:
            who = f"{c['speaker']}: " if named and c["speaker"] else ""
            paragraphs.append({"start": float(c["start"]), "text": who + c["text"]})
        last = c
    return paragraphs


def from_blocks(blocks: list[str]) -> list[dict]:
    """Paragraphs of text from another app. A block that is only a header ("Speaker 1  0:04", "[00:12:34]") gives
    the next one its time and speaker; a block starting with a time ("[12:34] …", "00:12:34 …") has its own. Times
    without brackets count only where most blocks have one (so "10:30 is when …" stays text)."""
    bare = sum(1 for b in blocks if LEADING.match(b) and not b.startswith(("[", "("))) >= len(blocks) * 0.5
    paragraphs: list[dict] = []
    pending: tuple[float, str | None] | None = None
    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        first = lines[0]
        h = HEADER.fullmatch(first)
        if h and len(lines) == 1 and (h.group("name") or first[:1] in "[(" or bare or _ts(first) is not None):
            pending = (seconds(*h.groups()[1:4]), (h.group("name") or "").strip() or None)
            continue
        start, speaker, text = None, None, " ".join(lines)
        if h and len(lines) > 1:
            start, speaker, text = seconds(*h.groups()[1:4]), (h.group("name") or "").strip() or None, " ".join(lines[1:])
        elif (m := LEADING.match(text)) and (text.startswith(("[", "(")) or bare):
            start = seconds(*(m.groups()[0:3] if m.group("rest") else m.groups()[4:7]))
            text = (m.group("rest") or m.group("rest2")).strip()
        elif pending:
            start, speaker = pending
        pending = None
        text = re.sub(r"^#{1,6}\s+", "", text)  # a heading of the file's own stays as text
        paragraphs.append({"start": start, "text": (f"{speaker}: " if speaker else "") + _clean(text)})
    return paragraphs


def app_of(name: str, text: str) -> str | None:
    """Which app made it, when the file says (None: another app)."""
    low = text[:4000].lower() + text[-600:].lower()
    if "otter.ai" in low:
        return "Otter"
    if re.search(r"GMT\d{8}-\d{6}", name) or "zoom" in name.lower():
        return "Zoom"
    if re.search(r"(?m)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/\d+-\d+$", text[:4000]):
        return "Teams"
    if "macwhisper" in low:
        return "MacWhisper"
    return None


def date_in_name(name: str) -> str | None:
    """A date in the file's name ("2026-11-06", "20261106", "06.11.2026"), as Zoom and many people name files."""
    for pattern, order in ((r"(20\d\d)-(\d\d)-(\d\d)", "ymd"), (r"(?<!\d)(20\d\d)(\d\d)(\d\d)(?!\d)", "ymd"),
                           (r"(?<!\d)(\d\d)\.(\d\d)\.(20\d\d)(?!\d)", "dmy")):
        if m := re.search(pattern, name):
            y, mo, d = (m.group(1), m.group(2), m.group(3)) if order == "ymd" else (m.group(3), m.group(2), m.group(1))
            try:
                return datetime(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------- all together

def read(path: Path) -> Transcript:
    """The transcript in `path`, or TranscriptError (a scan, an empty file, one that can't be read)."""
    suffix = path.suffix.lower()
    try:
        data = path.read_bytes() if suffix not in (".docx", ".pdf") else b""
    except OSError as e:
        raise TranscriptError(tr("“{name}” couldn't be read: {error}", name=path.name, error=str(e))) from None
    text, fm = "", {}
    if suffix in (".vtt", ".srt"):
        text = decode(data)
        found = cues(text)
        t = Transcript(paragraphs=from_cues(found), by=app_of(path.name, text),
                       seconds=float(found[-1]["end"]) if found else None)
    else:
        if suffix == ".docx":
            blocks = docx_blocks(path)
        elif suffix == ".pdf":
            blocks = join_lines(pdf_lines(pdf_pages(path)))
        elif suffix in (".html", ".htm"):
            blocks = html_blocks(decode(data))
        else:
            text = decode(data)
            fm = parse_frontmatter(text.replace("\r\n", "\n"))
            if fm:
                text = text.replace("\r\n", "\n").split("\n---\n", 1)[1]
            blocks = text_blocks(text)
        text = text or "\n\n".join(blocks)
        t = _stenny(blocks, fm) or Transcript(paragraphs=from_blocks(blocks), by=app_of(path.name, text))
        if t.by is None and suffix == ".docx" and blocks and blocks[0].lstrip("# ").strip() == "Transcript":
            t.by = "Word"  # Word's Transcribe starts its file with "Transcript"
    t.paragraphs = [p for p in t.paragraphs if p["text"].strip()]
    if not t.paragraphs:
        raise TranscriptError(tr("“{name}” has no transcript text in it.", name=path.name))
    t.date = t.date or date_in_name(path.name)
    if t.seconds is None and t.timestamps:
        t.seconds = max(p["start"] for p in t.paragraphs if p["start"] is not None)
    return t


def preview(t: Transcript, n: int = 3, chars: int = 220) -> list[dict]:
    """Its first paragraphs, shortened, for the window's "Beginning of the transcript"."""
    out = []
    for p in t.paragraphs[:n]:
        text = p["text"] if len(p["text"]) <= chars else p["text"][:chars].rsplit(" ", 1)[0] + " …"
        out.append({"start": fmt_hms(p["start"]) if p["start"] is not None else None, "text": text})
    return out
