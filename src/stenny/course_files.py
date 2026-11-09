"""Generated files, rebuilt after every final pass and refresh.

In `<lectures>/_Course Files/`: `<Course> (overview).md`, one line per lecture with links to its files and its
topics (the course's hub). It holds no lecture text: each lecture's transcript and summary exist once, in its own
folder. (Earlier versions also wrote `(all lectures)` and `(summaries)` copies there; `migrate.drop_course_copies` moves
them to the Trash.)

In `<lectures>/_Topics/`: one note per topic, listing the lectures (of every course) that cover it. Lectures
link to their topics from the `topics` property, so Obsidian's graph connects lectures on the same subject.

Final transcripts also get `previous` / `next` properties linking the course's lectures in order.
Live drafts are left out until their final pass. Only files the tool generated are ever rewritten.
"""

import re
from pathlib import Path

from .render import frontmatter, parse_frontmatter, yaml_value
from .vault import GLOSSARY_DIR, Vault, safe_name


def renumber(vault: Vault, path: Path, number: int) -> str:
    """Correct the lecture number in a lecture file (after an older lecture was added later): only the
    `lecture:` property and the title change."""
    text = path.read_text(encoding="utf-8")
    new = re.sub(r"(?m)^lecture: \d+$", f"lecture: {number}", text, count=1)
    new = re.sub(r"(?m)^(# .*? – Lecture )\d+( \()", rf"\g<1>{number}\2", new, count=1)
    if new != text:
        vault.write(path, new)
    return new


def set_properties(vault: Vault, path: Path, values: dict, text: str | None = None) -> str:
    """Set (or, with None, remove) frontmatter properties of a lecture file; the rest stays as it is."""
    text = path.read_text(encoding="utf-8") if text is None else text
    if not text.startswith("---\n") or "\n---\n" not in text[3:]:
        return text
    head, _, rest = text[4:].partition("\n---\n")
    lines = head.split("\n")
    for key, value in values.items():
        at = next((i for i, l in enumerate(lines) if l.startswith(f"{key}: ")), None)
        line = f"{key}: {yaml_value(value)}" if value is not None else None
        if at is None and line:
            lines.append(line)
        elif at is not None and line:
            lines[at] = line
        elif at is not None:
            del lines[at]
    new = "---\n" + "\n".join(lines) + "\n---\n" + rest
    if new != text:
        vault.write(path, new)
    return new


def topic_names(fm: dict) -> list[str]:
    """Topic names from a transcript's `topics` property ("[[_Topics/X|X]]" links)."""
    out = []
    for value in fm.get("topics") or []:
        m = re.fullmatch(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", str(value).strip())
        out.append(m.group(1) if m else str(value))
    return out


def overview(vault: Vault, course: str, rows: list[dict]) -> Path:
    """`<Course> (overview).md`: the course's hub, linking every lecture (drafts included)."""
    glossary = vault.glossary_path(course)
    out = [frontmatter({"course": course, "kind": "course-overview", "lectures": len(rows),
                        "tags": ["lecture-course-file"]}),
           f"# {course} – overview", "",
           "> Generated automatically (don't edit: this file is rewritten after every lecture). One line per "
           "lecture with links to its transcript and summary. The topics were chosen by an AI and checked against "
           "the transcript; each has a note in `_Topics/` listing every lecture, in any course, that covers it.", ""]
    if glossary.exists():
        out.append(f"- Glossary: [[{GLOSSARY_DIR}/{glossary.stem}|{glossary.stem}]]")
    out += ["", "## Lectures", ""]
    for r in rows:
        links = [f"[[{r['stem']}|transcript]]"] + ([f"[[{r['summary']}|summary]]"] if r["summary"] else [])
        status = "" if r["fm"].get("status") == "final" else " *(live draft, final transcript pending)*"
        out.append(f"- **Lecture {r['number']}** – {r['fm'].get('date', r['folder'].name)}"
                   + (f" · {r['fm']['duration']}" if r["fm"].get("duration") else "")
                   + " · " + " · ".join(links) + status)
        if r["topics"]:
            out.append(f"  Topics: {', '.join(r['topics'])}")
    return vault.write(vault.course_overview(course), "\n".join(out).rstrip() + "\n")


def rebuild_topics(vault: Vault, log=print) -> int:
    """Rewrite every topic note from the `topics` of all final transcripts, in every course. A topic note
    that no lecture names any more stays, saying so (generated notes are rewritten, never deleted)."""
    covered: dict[str, list[dict]] = {}
    for course_dir in vault.course_dirs():
        for number, folder in enumerate(vault.lectures(course_dir.name), 1):
            path = vault.files(course_dir.name, folder).existing("transcript")
            fm = parse_frontmatter(path.read_text(encoding="utf-8"))
            if fm.get("status") != "final":
                continue
            for topic in topic_names(fm):
                covered.setdefault(safe_name(topic), []).append(
                    {"topic": topic, "course": str(fm.get("course", course_dir.name)), "number": number,
                     "date": str(fm.get("date", folder.name)), "stem": path.stem})
    stale = [n for n in vault.topic_names() if n not in covered
             and parse_frontmatter(vault.topic_path(n).read_text(encoding="utf-8")).get("kind") == "topic"]
    for key in [*covered, *stale]:
        entries = covered.get(key, [])
        name = entries[0]["topic"] if entries else key
        courses = list(dict.fromkeys(e["course"] for e in entries))
        out = [frontmatter({"kind": "topic", "topic": name, "courses": courses, "lectures": len(entries),
                            "tags": ["lecture-topic"]}),
               f"# {name}", "",
               "> Generated automatically (don't edit: this note is rewritten after every lecture). The lectures "
               f"whose main topics include {name}, as chosen by an AI and checked against the transcript text.", ""]
        if not entries:
            out.append("No lecture covers this topic at the moment.")
        for course in courses:
            out += [f"## {course}", ""]
            out += [f"- [[{e['stem']}|Lecture {e['number']} ({e['date']})]]" for e in entries if e["course"] == course]
            out.append("")
        path = vault.topic_path(name)
        text = "\n".join(out).rstrip() + "\n"
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            vault.write(path, text)
    if covered:
        log(f"Topic notes updated: {len(covered)} topics")
    return len(covered)


def rebuild(vault: Vault, course: str, log=print) -> list[Path]:
    """Number the course's lectures in date order, link the final ones to each other (`previous` / `next`), and
    write the overview and the topic notes."""
    rows = []
    folders = vault.lectures(course)
    stems = [vault.files(course, f).existing("transcript").stem for f in folders]
    for number, folder in enumerate(folders, 1):
        files = vault.files(course, folder)
        for kind in ("raw", "summary"):
            if files.existing(kind):
                renumber(vault, files.existing(kind), number)
        text = renumber(vault, files.existing("transcript"), number)
        fm = parse_frontmatter(text)
        summary = files.existing("summary")
        rows.append({"number": number, "folder": folder, "fm": fm, "stem": stems[number - 1],
                     "summary": summary.stem if summary else None, "topics": topic_names(fm)})
        if fm.get("status") == "final":
            set_properties(vault, files.existing("transcript"), {
                "previous": f"[[{stems[number - 2]}]]" if number > 1 else None,
                "next": f"[[{stems[number]}]]" if number < len(stems) else None}, text)
    if not rows:
        return []
    written = [overview(vault, course, rows)]
    log(f"Course files updated: {', '.join(p.name for p in written)}")
    if any(r["fm"].get("status") == "final" for r in rows):
        rebuild_topics(vault, log)
    return written
