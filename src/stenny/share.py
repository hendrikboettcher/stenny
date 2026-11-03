"""The library (every lecture and talk in the vault, for Stenny's window) and clean copies to share.

A shared copy is the transcript as a classmate should get it: no frontmatter (audio path, model, microphone,
source …), no pointer to the raw output on this Mac, Obsidian links turned into plain text. The disclaimer, the
"audio only" note, headings and timestamps stay. Copies go to `~/Lectures Data/_Shared/`, outside the vault.
"""

import re
from pathlib import Path

from . import paths
from .render import parse_frontmatter
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
                "status": fm.get("status") or "final",
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


def share_name(entry: dict) -> str:
    """'Calculus – Lecture 3 (2026-10-20).md', 'Some Talk (2026-10-20).md'."""
    number = f" – Lecture {entry['number']}" if entry["kind"] == "lecture" and entry.get("number") else ""
    return f"{safe_name(entry['course'])}{number} ({entry['folder']}).md"


def share(entry: dict, with_summary: bool = False, out_dir: Path | None = None) -> Path:
    """Writes the clean copy (replacing an earlier one of the same lecture) and returns its path."""
    out_dir = out_dir or SHARED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / share_name(entry)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(shared_text(entry, with_summary), encoding="utf-8")
    tmp.replace(path)
    return path
