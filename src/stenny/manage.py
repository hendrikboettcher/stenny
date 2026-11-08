"""Renaming and deleting from Stenny's window: a lecture's own title, a course's (or talk's) name, a lecture.

Nothing is deleted for good: deleted files go to the Trash on macOS (with Finder's Put Back) and the Recycle Bin
on Windows (with Restore). A course rename moves
everything that carries its name, so Obsidian, Claude and the window keep agreeing:
- the vault: the course folder, each lecture's files ("<Course> <date>.md" …), the names and links inside them
  (frontmatter, titles, [[links]], the path of the raw output), the glossary; the course files are made again;
- ~/Lectures Data: the course's folder and its raw outputs, the paths inside session and segments files;
- config.toml (the old name stays a calendar word, as the timetable still uses it), the lecturer-OK answers and
  queued final passes.
Both hold the queue lock, so no final pass starts halfway through; while one runs, they wait for it to finish.
"""

import json
import os
import re
import subprocess
import sys
import tomllib
import unicodedata
from pathlib import Path

from . import course_files, courses, jobs, paths
from .config import Config, ConfigError
from .i18n import tr, trash_name
from .live import control
from .pipeline import home_relative
from .render import parse_frontmatter
from .vault import COURSE_FILES_DIR, GLOSSARY_DIR, TOPICS_DIR, LectureFiles, Vault, _visible, safe_name


class ManageError(Exception):
    """A message for the person using the app."""


# --------------------------------------------------------------------------- the Trash

TRASH_JS = """
function run(argv) {
  ObjC.import("Foundation");
  var fm = $.NSFileManager.defaultManager, failed = [];
  argv.forEach(function (p) {
    var err = $();
    if (!fm.trashItemAtURLResultingItemURLError($.NSURL.fileURLWithPath(p), null, err))
      failed.push(p + ": " + ObjC.unwrap(err.localizedDescription));
  });
  return failed.join("\\n");
}
"""


# Windows: the Recycle Bin, as Explorer's Delete does (so Restore works). The paths come in an environment variable
# as JSON, so no quoting can go wrong; errors come back one per line, in UTF-8.
RECYCLE_PS = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName Microsoft.VisualBasic
$failed = @()
foreach ($p in @($env:STENNY_TRASH | ConvertFrom-Json)) {
  try {
    if (Test-Path -LiteralPath $p -PathType Container) {
      [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($p, 'OnlyErrorDialogs', 'SendToRecycleBin')
    } else {
      [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p, 'OnlyErrorDialogs', 'SendToRecycleBin')
    }
  } catch { $failed += "${p}: $($_.Exception.Message)" }
}
$failed -join "`n"
"""

TRASH_NAME = "Recycle Bin" if sys.platform == "win32" else "Trash"


def trash(items: list[Path]) -> None:
    """Moves files and folders to the Trash on macOS (as Finder does, so Put Back works) or the Recycle Bin on
    Windows (as Explorer does, so Restore works)."""
    items = [p for p in items if p.exists()]
    if not items:
        return
    if sys.platform == "win32":
        out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-Command", RECYCLE_PS], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", env={**os.environ, "STENNY_TRASH": json.dumps([str(p) for p in items])},
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        out = subprocess.run(["osascript", "-l", "JavaScript", "-e", TRASH_JS, *map(str, items)],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode or out.stdout.strip():
        raise ManageError(tr("Couldn't move to the {trash}: {error}", trash=trash_name(),
                              error=(out.stdout or out.stderr).strip()))


# --------------------------------------------------------------------------- shared checks

class _Quiet:
    """The queue lock, or a message that a transcript is being made right now."""

    def __enter__(self):
        self.lock = jobs.QueueLock()
        if self.lock.__enter__() is None:
            self.lock.__exit__(None, None, None)
            raise ManageError(tr("A transcript is being made right now. Try again when it's done."))
        return self

    def __exit__(self, *exc):
        self.lock.__exit__(*exc)


def _recording() -> dict | None:
    try:
        reply = control.send("status", timeout=3)
    except (control.NoSession, OSError, ValueError):
        return None
    return reply if reply.get("state") not in (None, "idle") else None


def _target(cfg: Config, name: str, event: bool):
    if event:
        return cfg.event(name)
    try:
        return cfg.course(name)
    except ConfigError:
        # a past course: only its recordings are left (no table in the config)
        from .config import Course
        return Course(name=name)


def _queue_jobs() -> list[Path]:
    return [*jobs.QUEUE_DIR.glob("*.json"), *jobs.QUEUE_DIR.glob("*.failed")] if jobs.QUEUE_DIR.exists() else []


def _clean_name(name: str) -> str:
    name = unicodedata.normalize("NFC", name).strip()
    if not name:
        raise ManageError(tr("A name can't be empty."))
    if safe_name(name) != name:
        raise ManageError(tr("A name can't contain these characters: / \\ : # ^ [ ] | * ? \" < >. Try '{name}'.",
                             name=safe_name(name)))
    return name


# --------------------------------------------------------------------------- one lecture

def _lecture(cfg: Config, course: str, folder: str, event: bool):
    target = _target(cfg, course, event)
    vault = Vault(cfg.root_for(target))
    lecture_dir = vault.course_dir(target.name) / folder
    files = LectureFiles(lecture_dir, target.name)
    transcript = files.existing("transcript")
    if not transcript:
        raise ManageError(tr("No recording of {course} on {folder}.", course=course, folder=folder))
    return target, vault, lecture_dir, files, transcript


def _busy_lecture(status: str | None, what: str) -> None:
    if status == "live":
        raise ManageError(tr(f"This recording is still running. To {what} it, stop it first, "
                             "or use Cancel Recording… in Stenny's menu."))
    if status == "finalizing":
        raise ManageError(tr("Its transcript is being made right now. Try again when it's done."))


def rename_lecture(cfg: Config, course: str, folder: str, title: str, event: bool = False) -> str:
    """A lecture's own title ("Decision trees"), shown instead of "Lecture 3"; empty brings the number back.
    It's the transcript's `title` property, so the final pass keeps it."""
    _, vault, _, _, transcript = _lecture(cfg, course, folder, event)
    fm = parse_frontmatter(transcript.read_text(encoding="utf-8"))
    _busy_lecture(fm.get("status"), "rename")
    title = " ".join(unicodedata.normalize("NFC", title).split())
    number = fm.get("lecture")
    if not event and number and title.casefold() == f"lecture {number}".casefold():
        title = ""  # renamed back to what it was called anyway: no title of its own
    if len(title) > 120:
        raise ManageError(tr("That title is too long (120 characters at most)."))
    course_files.set_properties(vault, transcript, {"title": title or None})
    return title


def lecture_paths(cfg: Config, course: str, folder: str, event: bool = False) -> list[Path]:
    """Everything a lecture has: its vault folder (transcript, summary, slides), and in ~/Lectures Data its
    audio, raw output, session and segment files, and queued final passes."""
    target, _, lecture_dir, _, _ = _lecture(cfg, course, folder, event)
    data = cfg.data_for(target)
    items = [lecture_dir]
    if data.is_dir():
        items += [p for p in data.iterdir() if p.name.startswith(f"{folder}.")]  # "2026-10-20.opus", ….session.json
        raw = data / f"{safe_name(target.name)} {folder} (raw).md"
        items += [raw] if raw.exists() else []
    inside = {str(p.resolve()) for p in items[1:]}
    for job in _queue_jobs():
        try:
            d = json.loads(job.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("file") in inside or d.get("live") in inside:
            items.append(job)
    return items


def delete_lecture(cfg: Config, course: str, folder: str, event: bool = False) -> str:
    """Moves a lecture to the Trash (see lecture_paths); later lectures of the course move up a number."""
    target, vault, lecture_dir, _, transcript = _lecture(cfg, course, folder, event)
    _busy_lecture(parse_frontmatter(transcript.read_text(encoding="utf-8")).get("status"), "delete")
    with _Quiet():
        trash(lecture_paths(cfg, course, folder, event))
        course_dir = lecture_dir.parent
        if course_dir.is_dir() and not _visible(course_dir):
            for p in course_dir.iterdir():  # .DS_Store
                p.unlink()
            course_dir.rmdir()
        if not target.is_event:
            course_files.rebuild(vault, target.name, log=lambda *_: None)
            course_files.rebuild_topics(vault, log=lambda *_: None)
    return tr("Moved to the {trash}: {name}, {folder}.", trash=trash_name(), name=target.name, folder=folder)


def discard_recording(cfg: Config, course, folder: str) -> None:
    """A cancelled recording (live/session.py): its placeholder in the vault and its files in ~/Lectures Data
    (audio, session, chunks, logs) go to the Trash."""
    vault = Vault(cfg.root_for(course))
    lecture_dir = vault.course_dir(course.name) / folder
    data = cfg.data_for(course)
    files = [p for p in data.iterdir() if p.name.startswith(f"{folder}.")] if data.is_dir() else []
    trash([lecture_dir, *files])
    course_dir = lecture_dir.parent
    if course_dir.is_dir() and not _visible(course_dir):
        for p in course_dir.iterdir():
            p.unlink()
        course_dir.rmdir()
    if not course.is_event and course_dir.is_dir():
        course_files.rebuild(vault, course.name, log=lambda *_: None)


# --------------------------------------------------------------------------- a course or talk

def _replace_names(text: str, old: str, new: str, old_data: str, new_data: str, key: str) -> str:
    """The old name where it is a name: file stems ("Old 2026-10-20 (summary)", links too), the frontmatter's
    course/event, titles, paths into ~/Lectures Data. The words in the text stay."""
    text = re.sub(re.escape(old) + r"(?= \d{4}-\d{2}-\d{2})", lambda _: new, text)
    text = re.sub(rf'(?m)^{key}: {re.escape(json.dumps(old, ensure_ascii=False))}$',
                  lambda _: f"{key}: {json.dumps(new, ensure_ascii=False)}", text)
    text = re.sub(rf"(?m)^(# (?:Summary: |Raw Whisper output: |Glossary: )?){re.escape(old)}(?= –|$)",
                  lambda m: m.group(1) + new, text)
    return text.replace(old_data, new_data)


def _rewrite(path: Path, change, vault: Vault | None = None) -> None:
    text = path.read_text(encoding="utf-8")
    new = change(text)
    if new != text:
        if vault:
            vault.write(path, new)
        else:
            tmp = path.with_name(f".{path.name}.tmp")
            tmp.write_text(new, encoding="utf-8")
            tmp.replace(path)


def _rename_in_config(cfg: Config, old: str, new: str) -> None:
    """The course's table gets the new name; the old one becomes a calendar word (the timetable still says it)."""
    text = cfg.path.read_text(encoding="utf-8")
    for m in courses.HEADER.finditer(text):
        if old in tomllib.loads(f"{m.group(1)} = 1"):
            break
    else:
        raise ManageError(tr("'{name}' isn't written as its own table in the config.", name=old))
    start, end = courses._section(text, old)
    section = text[start:end]
    calendar = tomllib.loads(section).get("calendar", [])
    if old not in calendar:
        line = f"calendar = {json.dumps([*calendar, old], ensure_ascii=False)}\n"
        if re.search(r"(?m)^[ \t]*calendar[ \t]*=", section):
            section = re.sub(r"(?m)^[ \t]*calendar[ \t]*=.*\n?", lambda _: line, section, count=1)
        else:
            section = line + section
    header = f"[courses.{json.dumps(new, ensure_ascii=False)}]"
    line = header + m.group(0)[m.group(0).index("]", len("[courses.") + len(m.group(1))) + 1:]  # its comment stays
    courses._write(cfg.path, text[:m.start()] + line + text[m.end():start] + section + text[end:])


def rename_course(cfg: Config, old: str, new: str, event: bool = False) -> str:
    """Renames a course (or a talk) everywhere, see the module's note. Returns the new name."""
    new = _clean_name(new)
    target = _target(cfg, old, event)
    old = target.name
    if new == old:
        return new
    same = new.casefold() == old.casefold()  # only the case changes: the same folder on macOS
    if not same:
        taken = (new.casefold() in [g.name.casefold() for g in Vault(cfg.root_for(target)).course_dirs()]
                 or (not event and any(new.casefold() == c.casefold() for c in cfg.courses))
                 or (not event and any(new.casefold() in map(str.casefold, c.aliases) for c in cfg.courses.values())))
        if taken:
            raise ManageError(tr(f"There's already {'a talk' if event else 'a course'} called '{{name}}'.", name=new))
    if (rec := _recording()) and rec.get("course") == old:
        raise ManageError(tr("{name} is recording right now. Rename it when the recording is done.", name=old))

    vault = Vault(cfg.root_for(target))
    renamed = _target(cfg, new, event) if event else type(target)(**{**target.__dict__, "name": new})
    old_data, new_data = cfg.data_for(target), cfg.data_for(renamed)
    key = "event" if event else "course"
    swap = lambda text: _replace_names(text, old, new, home_relative(old_data) + "/", home_relative(new_data) + "/", key)

    with _Quiet():
        # 1. the vault: course folder, lecture files and what's in them
        old_dir, new_dir = vault.course_dir(old), vault.course_dir(new)
        if old_dir.is_dir():
            old_dir.rename(new_dir)
            for folder in [d for d in _visible(new_dir) if d.is_dir()]:
                for p in _visible(folder):
                    if p.name.startswith(f"{old} "):
                        p = p.rename(p.with_name(new + p.name[len(old):]))
                    if p.suffix == ".md":
                        _rewrite(p, swap, vault)
        if not event:
            glossary = vault.glossary_path(old)
            if glossary.exists():
                glossary = glossary.rename(vault.root / GLOSSARY_DIR / f"{new} (glossary).md")
                _rewrite(glossary, swap, vault)
            trash([p for p in (vault.root / COURSE_FILES_DIR).glob(f"{old} (*).md")] if not same else [])
            for p in (vault.root / TOPICS_DIR).glob("*.md") if (vault.root / TOPICS_DIR).is_dir() else []:
                _rewrite(p, swap, vault)

        # 2. ~/Lectures Data: the folder, raw outputs, paths in its files
        if old_data.is_dir():
            old_data.rename(new_data)
            for p in list(new_data.iterdir()):
                if p.name.startswith(f"{old} "):
                    p = p.rename(p.with_name(new + p.name[len(old):]))
                if p.suffix == ".json":
                    _rewrite(p, lambda t: _replace_json(t, old, new, [(old_data, new_data), (old_dir, new_dir)], swap))
                elif p.suffix == ".md":
                    _rewrite(p, swap)

        # 3. the config, the lecturer's OK, queued final passes
        if not event and old in cfg.courses:
            _rename_in_config(cfg, old, new)
        _rename_consent(old, new, key)
        for job in _queue_jobs():
            _rewrite(job, lambda t: _replace_job(t, old, new, old_data, new_data))

        if not event:
            course_files.rebuild(vault, new, log=lambda *_: None)
            course_files.rebuild_topics(vault, log=lambda *_: None)
    return new


def _replace_json(text: str, old: str, new: str, moves: list[tuple[Path, Path]], swap) -> str:
    """session.json / segments.json: the course's name, absolute paths (its data and vault folders), and file
    names in them."""
    try:
        data = json.loads(text)
    except ValueError:
        return text

    def walk(v, key=None):
        if isinstance(v, dict):
            return {k: walk(x, k) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, str):
            if key in ("course", "event") and v == old:
                return new
            for a, b in moves:
                v = v.replace(str(a) + "/", str(b) + "/")
            return swap(v)
        return v

    changed = walk(data)
    return text if changed == data else json.dumps(changed, ensure_ascii=False, indent=1)


def _replace_job(text: str, old: str, new: str, old_data: Path, new_data: Path) -> str:
    try:
        job = json.loads(text)
    except ValueError:
        return text
    if job.get("course") != old:
        return text
    job["course"] = new
    for k in ("file", "live"):
        if job.get(k) and job[k].startswith(str(old_data) + "/"):
            job[k] = str(new_data) + job[k][len(str(old_data)):]
    return json.dumps(job, indent=1)


def _rename_consent(old: str, new: str, key: str) -> None:
    from . import app_live
    given = app_live._consent()
    kind = "event" if key == "event" else "course"
    moved = {f"{use}/{kind}/{new}" if c == f"{use}/{kind}/{old}" else c
             for c in given for use in [c.split("/", 1)[0]]}
    if moved != given:
        app_live._save_consent(moved)


def delete_course(cfg: Config, name: str, event: bool = False) -> str:
    """A whole course (or talk) to the Trash: every lecture in the vault and in ~/Lectures Data (audio, raw
    output, session files), its glossary and course files, queued final passes; a course leaves the config."""
    target = _target(cfg, name, event)
    name = target.name
    if (rec := _recording()) and rec.get("course") == name:
        raise ManageError(tr("{name} is recording right now. Delete it when the recording is done.", name=name))
    vault = Vault(cfg.root_for(target))
    data = cfg.data_for(target)
    if not (vault.course_dir(name).exists() or data.exists() or (not event and name in cfg.courses)):
        raise ManageError(tr(f"There's no {'talk' if event else 'course'} called '{{name}}'.", name=name))
    with _Quiet():
        items = [vault.course_dir(name), data]
        if not event:
            items += [vault.glossary_path(name), *(vault.root / COURSE_FILES_DIR).glob(f"{safe_name(name)} (*).md")]
        prefix = str(data) + "/"
        for job in _queue_jobs():
            try:
                d = json.loads(job.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if d.get("course") == name or str(d.get("file", "")).startswith(prefix):
                items.append(job)
        trash(items)
        if not event and name in cfg.courses:
            _remove_from_config(cfg, name)
        from . import app_live
        kind = "event" if event else "course"
        given = app_live._consent()
        kept = {c for c in given if c.split("/", 1)[-1] != f"{kind}/{name}"}
        if kept != given:
            app_live._save_consent(kept)
        if not event:
            course_files.rebuild_topics(vault, log=lambda *_: None)
    return tr("Moved {name} to the {trash}.", name=name, trash=trash_name())


def _remove_from_config(cfg: Config, name: str) -> None:
    """The course's table goes from config.toml, header and settings (comments elsewhere stay)."""
    text = cfg.path.read_text(encoding="utf-8")
    for m in courses.HEADER.finditer(text):
        if name in tomllib.loads(f"{m.group(1)} = 1"):
            break
    else:
        raise ManageError(tr("'{name}' isn't written as its own table in the config.", name=name))
    _, end = courses._section(text, name)
    rest = text[end:]
    new = text[:m.start()].rstrip("\n") + ("\n\n" + rest.lstrip("\n") if rest.strip() else "\n")
    courses._write(cfg.path, new)
