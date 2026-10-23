"""Markdown + YAML frontmatter for a lecture's transcript, raw Whisper output and summary
(file names: vault.LectureFiles)."""

import json
import re
from dataclasses import dataclass, field

from .textproc import fmt_hms
from .vault import TOPICS_DIR, safe_name

LANG_NAMES = {"fr": "French", "de": "German", "en": "English", "it": "Italian", "es": "Spanish"}
TIME_SECTION_SECONDS = 600


@dataclass
class Lecture:
    course: str
    date: str                  # YYYY-MM-DD
    start_time: str            # HH:MM
    duration: float            # seconds of audio
    languages: list[str]
    language_note: str
    model: str
    source: str
    tags: list[str] = field(default_factory=list)
    audio: str | None = None
    status: str = "final"      # live | draft | final
    structure: str = "time"    # "time" or e.g. "ai (claude-code haiku)"
    pauses: list = field(default_factory=list)  # (start, end) lecture-time intervals not recorded
    microphone: str | None = None  # live: input device; file: recording device/app from its metadata
    backup_microphone: str | None = None  # live: second microphone, used where the main one had gaps
    slides: int = 0            # pages of the slides PDF in the lecture folder (0 = none)
    number: int = 0            # 1 = the course's first lecture (0 = unknown)
    names: dict = field(default_factory=dict)  # file names of the other files, for links (vault.LectureFiles.names)
    topics: list = field(default_factory=list)
    kind: str = "lecture"      # "lecture" | "event" (a talk or event: no course, no lecture number)
    raw_file: str | None = None  # where the raw output is (outside the vault, "~/…"); None: old lectures, linked in the vault  # main topics (AI step, checked by the guard); notes in _Topics/

    @property
    def who(self) -> dict:
        """The frontmatter's first property: the course, or for a talk or event its title."""
        return {"event": self.course} if self.kind == "event" else {"course": self.course}

    @property
    def base_tag(self) -> str:
        return "recording" if self.kind == "event" else "lecture"

    @property
    def title(self) -> str:
        return f"{self.course} – Lecture {self.number} ({self.date})" if self.number else f"{self.course} – {self.date}"

    def link(self, kind: str) -> str:
        """Obsidian link to one of this lecture's files ("raw", "transcript", "summary")."""
        name = self.names.get(kind) or {"raw": "Raw Transcript.md", "transcript": "Transcript.md",
                                        "summary": "Summary.md"}[kind]
        return f"[[{name.removesuffix('.md')}]]"

    @property
    def slides_name(self) -> str:
        return self.names.get("slides") or "Slides.pdf"


def yaml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(yaml_value(x) for x in v) + "]"
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v  # unquoted so Obsidian treats it as a date property
    return json.dumps(str(v), ensure_ascii=False)  # JSON strings are valid YAML


def frontmatter(fields: dict) -> str:
    lines = ["---"] + [f"{k}: {yaml_value(v)}" for k, v in fields.items() if v is not None] + ["---", ""]
    return "\n".join(lines)


def parse_frontmatter(text: str) -> dict:
    """Read back the frontmatter written by `frontmatter` (JSON strings/lists, bare dates and numbers)."""
    if not text.startswith("---\n"):
        return {}
    out = {}
    for line in text[4:].split("\n---", 1)[0].splitlines():
        key, sep, value = line.partition(": ")
        if not sep:
            continue
        try:
            out[key] = json.loads(value)
        except json.JSONDecodeError:
            out[key] = value
    return out


def parse_hms(value: str) -> float:
    h, m, s = (int(x) for x in str(value).split(":"))
    return h * 3600 + m * 60 + s


def topic_link(topic: str) -> str:
    """Link to a topic note, by path so it can't resolve to another note of the same name in the vault."""
    return f"[[{TOPICS_DIR}/{safe_name(topic)}|{topic}]]"


def slide_link(pages, slides_name: str = "Slides.pdf") -> str:
    """Obsidian link that opens the slides PDF at the first page of a section's slide range."""
    a, b = pages
    label = f"Slide {a}" if a == b else f"Slides {a}–{b}"
    return f"[[{slides_name}#page={a}|{label}]]"


def time_sections(paragraphs: list[dict]) -> list[dict]:
    """Fallback structure without an LLM: a heading every 10 minutes of lecture."""
    sections, next_mark = [], 0.0
    for i, p in enumerate(paragraphs):
        if p["start"] >= next_mark:
            sections.append({"at": i, "title": None})
            next_mark = (p["start"] // TIME_SECTION_SECONDS + 1) * TIME_SECTION_SECONDS
    return sections


def reason(gap) -> str:
    return gap[2] if len(gap) > 2 else "paused"


def paragraph_breaks(pauses) -> list[float]:
    """Pauses and gaps start a new paragraph; a gap filled from the backup microphone also ends one."""
    return [p[0] for p in pauses] + [p[1] for p in pauses if reason(p) == "backup"]


def pause_marker(gap, backup_mic: str | None = None) -> str:
    s, e = gap[0], gap[1]
    if reason(gap) == "backup":
        return (f"*[{fmt_hms(s)}–{fmt_hms(e)}: no audio from the main microphone; this part comes from the "
                f"backup microphone{f' ({backup_mic})' if backup_mic else ''} and may be quieter]*")
    if reason(gap) == "no input":
        return f"*[No audio from the microphone {fmt_hms(s)}–{fmt_hms(e)}: connection lost]*"
    return f"*[Recording paused {fmt_hms(s)}–{fmt_hms(e)}]*"


AUDIO_ONLY = ("**Audio only:** this is what the microphone heard, not what was shown. Words like \"this\", "
              "\"here\" or \"as you can see\" often point to slides or the board, which are not in the text. The "
              "lecture may digress from the slides, and voices of people nearby or questions from the room can "
              "appear without being marked as such.")


def transcript_md(lec: Lecture, paragraphs: list[dict], sections: list[dict]) -> str:
    lang_label = " / ".join(LANG_NAMES.get(l, l) for l in lec.languages)
    ai = lec.structure.startswith("ai")
    not_filled = [p for p in lec.pauses if reason(p) != "backup"]
    fm = frontmatter({
        **lec.who, "lecture": lec.number or None, "date": lec.date, "start": lec.start_time,
        "duration": fmt_hms(lec.duration),
        "language": lec.languages[0] if len(lec.languages) == 1 else "mixed", "languages": lec.languages,
        "tags": [lec.base_tag, *lec.tags], "status": lec.status, "model": lec.model, "structure": lec.structure,
        "paused": fmt_hms(sum(p[1] - p[0] for p in not_filled)) if not_filled else None,
        "source": lec.source, "microphone": lec.microphone, "backup_microphone": lec.backup_microphone,
        "audio": lec.audio,
        "slides": f"[[{lec.slides_name}]]" if lec.slides else None,
        "topics": [topic_link(t) for t in lec.topics] if lec.topics and lec.status != "live" else None,
    })
    note = (f"> Automatic transcript ({lang_label}) of {"a talk" if lec.kind == "event" else "a lecture"} recorded on {lec.date} at {lec.start_time}, "
            f"length {fmt_hms(lec.duration)}. Transcribed locally with Whisper; it may contain recognition "
            f"errors, especially in names and technical terms. "
            + ("Section headings were added by an AI; the text itself is Whisper's output, changed only by "
               "glossary corrections and term fixes checked against the glossary"
               + (" or the slides" if lec.slides else "") + ". " if ai else "")
            + (f"Every segment, unfiltered, is in `{lec.raw_file}` (outside the vault)." if lec.raw_file
               else f"Every segment, unfiltered, is in {lec.link('raw')}.")
            + "\n>\n> " + AUDIO_ONLY
            + (" Slide links on the headings are an AI's best guess." if ai and lec.slides else ""))
    if lec.status == "live":
        note = ("> **Live draft** (fast, unpolished). After the recording it is replaced by the final transcript, "
                "made from the full recording.")
    out = [fm, f"# {lec.title}", "", note, ""]
    starts = {s["at"]: s for s in sections}
    pending = sorted(tuple(p) for p in lec.pauses if p[1] is not None)
    # a filled gap's marker goes before the text from the backup microphone, the others after the gap
    def due(gap, p) -> bool:
        if reason(gap) == "backup":
            return p.get("backup", False) or gap[0] <= p["start"]
        return gap[1] <= p["start"]
    for i, p in enumerate(paragraphs):
        while pending and due(pending[0], p):
            out += [pause_marker(pending.pop(0), lec.backup_microphone), ""]
        if i in starts:
            title, pages = starts[i]["title"], starts[i].get("slides")
            out += [f"## [{fmt_hms(p['start'])}]" + (f" {title}" if title else "")
                    + (f" ({slide_link(pages, lec.slides_name)})" if pages else ""), ""]
        out += [f"**[{fmt_hms(p['start'])}]** {p['text']}", ""]
    for gap in pending:
        out += [pause_marker(gap, lec.backup_microphone), ""]
    return "\n".join(out)


def raw_md(lec: Lecture, segments: list[dict]) -> str:
    show_lang = len(lec.languages) > 1
    fm = frontmatter({**lec.who, "lecture": lec.number or None, "date": lec.date,
                      "kind": "raw-whisper-output", "model": lec.model,
                      "language": lec.language_note, "source": lec.source, "microphone": lec.microphone,
                      "backup_microphone": lec.backup_microphone, "tags": [f"{lec.base_tag}-raw"]})
    out = [fm, f"# Raw Whisper output: {lec.title}", "",
           "> Every segment exactly as Whisper produced it, with start–end times. "
           f"Segments marked ⚠ were left out of the transcript `{lec.names.get('transcript', 'Transcript.md')}` in "
           "the vault, with the reason. For reading or as context for an AI, use that transcript instead: it is the "
           "same text, cleaned up. This file is kept outside the vault, next to the audio, for checking.", ""]
    for s in segments:
        line = f"[{fmt_hms(s['start'])}–{fmt_hms(s['end'])}]"
        if show_lang:
            line += f" ({s.get('language', '?')})"
        line += f" {s.get('raw_text', s['text']).strip()}"
        if s.get("backup"):
            line += " *(backup microphone"
            if s.get("overlap"):
                line += f"; left out as a repeat of the main microphone: “{s['overlap']}”"
            line += ")*"
        if s.get("dropped"):
            line += f" ⚠ *{s['dropped']}*"
        out.append(line)
    return "\n".join(out) + "\n"


def summary_md(lec: Lecture, body: str, generator: str) -> str:
    fm = frontmatter({**lec.who, "lecture": lec.number or None, "date": lec.date, "kind": "summary",
                      "generated_by": generator,
                      "tags": [f"{lec.base_tag}-summary", *lec.tags]})
    return "\n".join([fm, f"# Summary: {lec.title}", "",
                      f"> AI-generated summary ({generator}) of {lec.link('transcript')}. It can omit or misstate things; "
                      "the transcript is authoritative. Timestamps point into the transcript.", "",
                      body.strip(), ""])
