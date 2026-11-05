"""The library (every lecture and talk in the vault, for Stenny's window) and clean copies to share.

A shared copy is the transcript as a classmate should get it: no frontmatter (audio path, model, microphone,
source …), no pointer to the raw output on this Mac, Obsidian links turned into plain text. The disclaimer, the
"audio only" note, headings and timestamps stay. Copies go to `~/Lectures Data/_Shared/`, outside the vault.

Formats: Markdown as it is, Word (.docx, through macOS's textutil) and HTML. Word and HTML (so the PDF too) end
with a small "Transcribed with Stenny · stenny.eu" line; Markdown doesn't, as it gets pasted into chats. Stenny's window turns the HTML
into a PDF (paginated by macOS's text system); there is no PDF converter on a Mac without the app.
"""

import html
import os
import re
import subprocess
import tempfile
from pathlib import Path

from . import paths
from .render import DRAFT_STATES, draft_status, parse_frontmatter
from .vault import LectureFiles, Vault, folder_key, safe_name

SHARED_DIR = paths.HOME / "_Shared"
HEAD_BYTES = 16384  # the frontmatter is at the top; transcripts themselves run to 100 KB and more


class ShareError(Exception):
    pass


def _head(path: Path) -> dict:
    with path.open(encoding="utf-8", errors="replace") as f:
        return parse_frontmatter(f.read(HEAD_BYTES))


def _entries(root: Path, kind: str) -> list[dict]:
    out = []
    if not root.is_dir():
        return out
    for course_dir in Vault(root).course_dirs():
        for folder in sorted((d for d in course_dir.iterdir() if d.is_dir() and not d.name.startswith(".")),
                             key=lambda d: folder_key(d.name)):
            files = LectureFiles(folder, course_dir.name)
            transcript = files.existing("transcript")
            if not transcript:
                continue
            fm = _head(transcript)
            summary = files.existing("summary")
            out.append({
                "kind": kind,
                "course": fm.get("course") or fm.get("event") or course_dir.name,
                "folder": folder.name,
                "number": fm.get("lecture") or 0,
                "date": str(fm.get("date") or folder.name[:10]),
                "start": fm.get("start"),
                "duration": fm.get("duration"),
                "language": fm.get("language"),
                "status": fm.get("status") or "final",  # live | queued | finalizing: still the live draft
                "draft_note": draft_status(fm["status"], fm.get("waiting")) if fm.get("status") in DRAFT_STATES else None,
                "transcript": str(transcript),
                "summary": str(summary) if summary else None,
            })
    return out


def library(cfg) -> list[dict]:
    """Every lecture and talk with a transcript, oldest first within each course."""
    events = _entries(cfg.events_root, "event") if cfg.events_root != cfg.lectures_root else []
    return _entries(cfg.lectures_root, "lecture") + events


def find(cfg, course: str, folder: str, event: bool = False) -> dict:
    """The library entry for one lecture folder ("2026-10-15" or "2026-10-15 (2)")."""
    for entry in library(cfg):
        if (entry["kind"] == "event") == event and entry["folder"] == folder and \
                safe_name(entry["course"]).casefold() == safe_name(course).casefold():
            return entry
    raise ShareError(f"No transcript for {course} on {folder}.")


def _body(text: str) -> str:
    """The text after the frontmatter."""
    if text.startswith("---\n"):
        _, sep, rest = text[4:].partition("\n---\n")
        if sep:
            return rest.lstrip("\n")
    return text


def _unlink(text: str) -> str:
    """Obsidian links to plain text: [[target|label]] → label, [[target]] → the target's name."""
    def plain(m: re.Match) -> str:
        target, _, label = m.group(1).partition("|")
        return label or Path(target.split("#", 1)[0]).name.removesuffix(".md")
    return re.sub(r"\[\[([^\]]+)\]\]", plain, text)


RAW_POINTER = re.compile(r" ?Every segment, unfiltered, is in (`[^`]*`|\[\[[^\]]*\]\])( \(outside the vault\))?\.")
BACKUP_DEVICE = re.compile(r"(backup microphone) \([^)]*\)")  # the device's name can carry its owner's name


def clean_transcript(text: str) -> str:
    body = RAW_POINTER.sub("", _body(text))
    return _unlink(BACKUP_DEVICE.sub(r"\1", body)).rstrip() + "\n"


def clean_summary(text: str) -> str:
    """The summary as a section: its own title dropped, its headings one level down."""
    lines = _body(text).splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    lines = ["#" + l if l.startswith("#") else l for l in lines]
    body = "\n".join(lines).strip()
    body = body.replace("Timestamps point into the transcript.", "Timestamps point into the transcript below.")
    return _unlink(re.sub(r"AI-generated summary \(.*?\) of \[\[[^\]]*\]\]",
                          "AI-generated summary of the transcript below", body))


def shared_text(entry: dict, with_summary: bool = False) -> str:
    transcript = clean_transcript(Path(entry["transcript"]).read_text(encoding="utf-8"))
    if not (with_summary and entry.get("summary")):
        return transcript
    summary = clean_summary(Path(entry["summary"]).read_text(encoding="utf-8"))
    # after the title and the disclaimer, before the first section
    head, sep, rest = transcript.partition("\n## ")
    return f"{head.rstrip()}\n\n## Summary\n\n{summary}\n\n---\n{sep}{rest}" if sep else \
        f"{transcript.rstrip()}\n\n## Summary\n\n{summary}\n"


FORMATS = ("md", "docx", "html")

# Arial: on every Mac and Windows PC, so a Word file looks the same on a friend's computer. Sizes are in px
# because macOS's HTML import (textutil, and the window's PDF) turns 1px into 1pt, while 1pt would grow to 1.33pt.
CSS = """
body { font-family: Arial, Helvetica, sans-serif; font-size: 11px; line-height: 1.35; color: #1d1b18; }
h1 { font-size: 16px; font-weight: bold; margin: 0 0 8px; }
h2 { font-size: 13px; font-weight: bold; margin: 14px 0 5px; }
h3 { font-size: 11.5px; font-weight: bold; margin: 11px 0 4px; }
h4, h5, h6 { font-size: 11px; font-weight: bold; margin: 9px 0 3px; }
p { margin: 0 0 6px; }
ul { margin: 0 0 6px; padding-left: 14px; }
li { margin: 0 0 2px; }
p.li { margin: 0 0 3px 10px; }
blockquote { margin: 0 0 8px; padding-left: 8px; border-left: 2px solid #ddd6cc; color: #5f5a52; font-size: 9.5px; }
blockquote p { margin: 0 0 4px; }
hr { border: none; border-top: 1px solid #ddd6cc; margin: 12px 0; }
.ts { font-size: 8.5px; color: #8a847b; }
p.credit { margin-top: 18px; font-size: 8.5px; color: #8a847b; }
p.credit a { color: #8a847b; }
"""

# The last line of a shared PDF or Word file. Not in Markdown, which gets pasted into chats and notes.
CREDIT = '<p class="credit">Transcribed with Stenny · <a href="https://stenny.eu">stenny.eu</a></p>'


TIMESTAMP = re.compile(r"(\*\*)?\[(\d+:\d{2}:\d{2})\](\*\*)?")


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = TIMESTAMP.sub(r'<span class="ts">[\2]</span>', out)  # small and grey, so the sentence reads first
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"(?<![*\w])\*(?![\s*])(.+?)(?<![\s*])\*(?![*\w])", r"<em>\1</em>", out)


def to_html(markdown: str, title: str, bullet_lines: bool = False, credit: bool = False) -> str:
    """The Markdown Stenny writes (headings, paragraphs, quotes, bullets, bold, italics, rules) as a page.
    `bullet_lines`: bullets as "• …" paragraphs instead of a list, for textutil, whose Word lists come out with
    the text pushed far right of the bullet. `credit`: end with "Transcribed with Stenny · stenny.eu"."""
    out: list[str] = []
    para: list[str] = []
    quote: list[list[str]] = []
    items: list[str] = []

    def flush() -> None:
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()
        if quote:
            out.append("<blockquote>" + "".join(f"<p>{_inline(' '.join(q))}</p>" for q in quote if q) + "</blockquote>")
            quote.clear()
        if items:
            out.append("".join(f'<p class="li">•&nbsp;&nbsp;{_inline(i)}</p>' for i in items) if bullet_lines
                       else "<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for raw in markdown.splitlines():
        line = raw.strip()
        if line.startswith(">"):
            if para or items:
                flush()
            text = line[1:].strip()
            if not quote:
                quote.append([])
            if text:
                quote[-1].append(text)
            elif quote[-1]:
                quote.append([])
            continue
        if quote:
            flush()
        if not line:
            flush()
        elif m := re.match(r"(#{1,6})\s+(.*)", line):
            flush()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
        elif re.fullmatch(r"-{3,}|\*{3,}", line):
            flush()
            out.append("<hr>")
        elif line.startswith(("- ", "* ")):
            if para:
                flush()
            items.append(line[2:])
        elif items and raw.startswith((" ", "\t")):
            items[-1] += " " + line  # a wrapped bullet
        else:
            if items:
                flush()
            para.append(line)
    flush()
    if credit:
        out.append(CREDIT)
    return (f'<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
            f"<style>{CSS}</style></head>\n<body>\n" + "\n".join(out) + "\n</body></html>\n")


def share_name(entry: dict, fmt: str = "md") -> str:
    """'Calculus – Lecture 3 (2026-10-20).md', 'Some Talk (2026-10-20).docx'."""
    number = f" – Lecture {entry['number']}" if entry["kind"] == "lecture" and entry.get("number") else ""
    return f"{safe_name(entry['course'])}{number} ({entry['folder']}).{fmt}"


def _docx(page: str, path: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "copy.html"
        source.write_text(page, encoding="utf-8")
        done = subprocess.run(["textutil", "-convert", "docx", "-format", "html", "-inputencoding", "UTF-8",
                               "-output", str(path), str(source)], capture_output=True, text=True)
    if done.returncode or not path.exists():
        raise ShareError(f"The Word file couldn't be made: {done.stderr.strip() or 'textutil failed'}")


def share(entry: dict, with_summary: bool = False, out_dir: Path | None = None, fmt: str = "md") -> Path:
    """Writes the clean copy in `fmt` (md, docx or html), replacing an earlier one of the same lecture, and
    returns its path."""
    if fmt not in FORMATS:
        raise ShareError(f"Unknown format '{fmt}': use {', '.join(FORMATS)}.")
    out_dir = out_dir or SHARED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / share_name(entry, fmt)
    text = shared_text(entry, with_summary)
    tmp = path.with_name(f".{path.stem}.tmp.{fmt}")  # textutil wants the extension
    try:
        if fmt == "md":
            tmp.write_text(text, encoding="utf-8")
        else:
            page = to_html(text, path.stem, bullet_lines=fmt == "docx", credit=True)
            if fmt == "html":
                tmp.write_text(page, encoding="utf-8")
            else:
                _docx(page, tmp)
        tmp.replace(path)
    finally:
        if tmp.exists():
            os.unlink(tmp)
    return path
