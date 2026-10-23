"""Generated files, rebuilt after every final pass and refresh.

In `<lectures>/_Course Files/`:
- `<Course> (overview).md`: one line per lecture with links to its files and its topics (the course's hub);
- `<Course> (all lectures).md`: every final transcript of the course in order, with a table of contents;
- `<Course> (summaries).md`: every lecture summary.

In `<lectures>/_Topics/`: one note per topic, listing the lectures (of every course) that cover it. Lectures
link to their topics from the `topics` property, so Obsidian's graph connects lectures on the same subject.

Final transcripts also get `previous` / `next` properties linking the course's lectures in order.

The all-lectures and summaries files name the lectures without linking them (the overview is the one hub in
Obsidian's graph). They are for handing a whole course to an AI (or reading it in one go). The
text is the same as in the lecture folders, so a reader should use one or the other. Live drafts are left out
until their final pass. Only files the tool generated are ever rewritten.
"""

import re
from datetime import date
from pathlib import Path

from .render import AUDIO_ONLY, frontmatter, parse_frontmatter, parse_hms, yaml_value
from .textproc import fmt_hms
from .vault import GLOSSARY_DIR, Vault, safe_name


def body(text: str) -> list[str]:
    """A lecture file without its frontmatter, title and opening note; headings one level deeper."""
    if text.startswith("---\n"):
        text = text[4:].split("\n---\n", 1)[-1]
    lines = text.strip("\n").splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    while lines and (not lines[0].strip() or lines[0].startswith(">")):
        lines = lines[1:]
    return ["#" + l if l.startswith("#") else l for l in lines]


def unlink(text: str) -> str:
    """Obsidian links as plain text ("[[a|b]]" → "b", "[[a]]" → "a"). The all-lectures and summaries files
    name the lectures without linking them, so only the overview links every lecture: otherwise the graph
    gets three hubs with the same lines."""
    return re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)


def section_titles(lines: list[str]) -> list[str]:
    """Titles of the lecture's sections (AI headings), without their timestamps and slide links."""
    out = []
    for l in lines:
        if l.startswith("### [") and "] " in l:
            title = l.split("] ", 1)[1].split(" ([[", 1)[0].strip()
            if title:
                out.append(title)
    return out


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
    extra = [f"[[{vault.course_file(course, k).stem}]]" for k in ("all lectures", "summaries")
             if vault.course_file(course, k).exists()]
    out = [frontmatter({"course": course, "kind": "course-overview", "lectures": len(rows),
                        "tags": ["lecture-course-file"]}),
           f"# {course} – overview", "",
           "> Generated automatically (don't edit: this file is rewritten after every lecture). One line per "
           "lecture with links to its transcript and summary. The topics were chosen by an AI and checked against "
           "the transcript; each has a note in `_Topics/` listing every lecture, in any course, that covers it.", ""]
    if glossary.exists():
        out.append(f"- Glossary: [[{GLOSSARY_DIR}/{glossary.stem}|{glossary.stem}]]")
    if extra:
        out.append("- Whole course in one file: " + " · ".join(extra))
    out += ["", "## Lectures", ""]
    for r in rows:
        links = [f"[[{r['stem']}|transcript]]"] + ([f"[[{r['summary']}|summary]]"] if r["summary"] else [])
        status = "" if r["fm"].get("status") == "final" else " *(live draft, final transcript pending)*"
        out.append(f"- **Lecture {r['number']}** – {r['fm'].get('date', r['folder'].name)}"
                   + (f" · {r['fm']['duration']}" if r["fm"].get("duration") else "")
                   + " · " + " · ".join(links) + status)
        if r["topics"]:
            out.append(f"  Topics: {', '.join(r['topics'])}")
    return vault.write(vault.course_file(course, "overview"), "\n".join(out).rstrip() + "\n")


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
    lectures, rows = [], []
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
        if fm.get("status") != "final":
            continue
        text = set_properties(vault, files.existing("transcript"), {
            "previous": f"[[{stems[number - 2]}]]" if number > 1 else None,
            "next": f"[[{stems[number]}]]" if number < len(stems) else None}, text)
        lectures.append({"number": number, "folder": folder, "fm": fm, "body": body(text),
                         "file": files.existing("transcript").name,
                         "summary": body(summary.read_text(encoding="utf-8")) if summary else None})
    if not rows:
        return []
    written = []
    if not lectures:
        written.append(overview(vault, course, rows))
        log(f"Course files updated: {', '.join(p.name for p in written)}")
        return written

    def heading(lec: dict) -> str:
        return f"## Lecture {lec['number']} – {lec['fm'].get('date', lec['folder'].name)}"

    def info(lec: dict) -> str:
        fm, parts = lec["fm"], [str(lec["fm"].get("duration", ""))]
        langs = fm.get("languages") or [fm.get("language")]
        parts.append("/".join(str(l) for l in langs if l))
        parts.append(f"transcript `{lec['file']}`")
        if fm.get("slides"):
            parts.append(f"slides `{unlink(str(fm['slides']))}`")
        return "*" + " · ".join(p for p in parts if p) + "*"

    total = sum(parse_hms(l["fm"]["duration"]) for l in lectures if l["fm"].get("duration"))
    meta = {"course": course, "kind": "course-file", "lectures": len(lectures), "duration": fmt_hms(total),
            "updated": date.today().isoformat(), "tags": ["lecture-course-file"]}

    out = [frontmatter({**meta, "content": "all lectures"}), f"# {course} – all lectures", "",
           f"> Generated automatically from the {len(lectures)} final lecture transcripts in the folder "
           f"`{vault.course_dir(course).name}/` (don't edit: this file is rewritten after every lecture). It is the "
           "same text as the individual transcripts, so use one or the other. Automatic transcripts: they may "
           "contain recognition errors, especially in names and technical terms. Timestamps are lecture time.",
           ">", f"> {AUDIO_ONLY}", "",
           "## Contents", ""]
    for lec in lectures:
        titles = section_titles(lec["body"])
        out.append(f"- **Lecture {lec['number']}** ({lec['fm'].get('date')}, {lec['fm'].get('duration')})"
                   + (": " + "; ".join(titles) if titles else ""))
    out.append("")
    for lec in lectures:
        out += [heading(lec), "", info(lec), "", *map(unlink, lec["body"]), ""]
    written.append(vault.write(vault.course_file(course, "all lectures"), "\n".join(out).rstrip() + "\n"))

    with_summary = [l for l in lectures if l["summary"]]
    if with_summary:
        out = [frontmatter({**meta, "content": "summaries", "lectures": len(with_summary)}),
               f"# {course} – summaries", "",
               f"> Generated automatically from the AI summaries of {len(with_summary)} lectures (don't edit: this "
               "file is rewritten after every lecture). Summaries can omit or misstate things; the transcripts "
               f"(see `{vault.course_file(course, 'all lectures').stem}`) are authoritative.", ""]
        for lec in with_summary:
            out += [heading(lec), "", info(lec), "", *map(unlink, lec["summary"]), ""]
        written.append(vault.write(vault.course_file(course, "summaries"), "\n".join(out).rstrip() + "\n"))
    written.append(overview(vault, course, rows))  # last: it links the files above
    log(f"Course files updated: {', '.join(p.name for p in written)}")
    rebuild_topics(vault, log)
    return written
