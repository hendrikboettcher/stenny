"""Recordings and transcripts added from a file in Stenny's window (#136, #138), "ready" until the student confirms.

A transcript someone sent (transcripts.py: Stenny's own copies, other apps' .vtt, .srt, .txt, .md, .docx, .pdf) comes
in the same way and becomes a lecture at its date too, "Ready to add". It's read at once (so a scan or an empty file is
turned down right away, and the window can show where it comes from and how it begins) and Stenny's own copy of the
file is kept in the data place; Add puts it into the Stenny folder as it is (pipeline.add_transcript), with no queue,
Whisper or AI step. Everything below about Transcribe is about recordings.

A file added to a course (the file button next to Record, or dropped on a course) becomes a lecture at once, at its
place by recording date and numbered like the others, but nothing is transcribed before Transcribe: a file dropped
into the wrong course would otherwise cost a long pass for nothing. Until it has a transcript it is one small file
in the course's data place, `Recordings/<Course>/<id>.ready.json` (like a queued job), so nothing shows in the
Stenny folder before there is something to read there. It reserves the lecture folder it will get
(`2026-11-06`, or `2026-11-06 (2)` next to another lecture that day).

Stenny only reads the original file: it's never moved, changed or deleted. Removing a ready lecture removes only
this entry; the audio Stenny keeps is its own copy in Recordings/ (`keep_audio`), made by the transcription.

Transcribe queues an ordinary job (jobs.enqueue) with the folder, date and language as confirmed; pipeline.process
does the work as for `stenny add`, and cli.run_job removes the entry once the transcript is there. It starts at once
in its own process when the queue's rules allow it (power, the night hours, memory, no recording running, no other
pass), otherwise the queue's next run takes it, like a recording's final pass that waits.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import audio, jobs, resources, transcripts
from .config import ConfigError, Course
from .i18n import tr
from .progress import progress_path, read as read_progress
from .textproc import fmt_hms
from .vault import TALKS_DIR, LectureFiles, folder_key, safe_name

SUFFIX = ".ready.json"
STARTING_GRACE_S = 60  # Transcribe starts a process that needs a moment before it reports its progress


class ReadyError(Exception):
    """A message for the person using the app."""


def _course(cfg, name: str, event: bool) -> Course:
    if event:
        if not name.strip():
            raise ReadyError(tr("A talk needs a title."))
        return cfg.event(name)
    try:
        return cfg.course(name)
    except ConfigError as e:
        raise ReadyError(str(e)) from None


def _read(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("file") and d.get("folder") else None
    except (OSError, ValueError):
        return None


def _write(path: Path, d: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _ready_files(data_dir: Path) -> list[Path]:
    return sorted(data_dir.glob(f"*{SUFFIX}")) if data_dir.is_dir() else []


def _has_lecture(folder: Path) -> bool:
    return folder.is_dir() and any(not p.name.startswith(".") for p in folder.iterdir())


def _free_folder(cfg, course: Course, date: str, keep: Path | None = None) -> str:
    """The first lecture folder that day that neither a lecture in the Stenny folder nor another ready recording
    has: "2026-11-06", "2026-11-06 (2)" … (`keep`: this entry's own file, whose folder doesn't count)."""
    base = cfg.vault_for(course).course_dir(course.name)
    taken = {d["folder"] for p in _ready_files(cfg.data_for(course)) if p != keep and (d := _read(p))}
    n = 1
    while True:
        name = date if n == 1 else f"{date} ({n})"
        if name not in taken and not _has_lecture(base / name):
            return name
        n += 1


def _find(cfg, params: dict) -> tuple[Course, Path, dict]:
    try:
        course = _course(cfg, params["course"], bool(params.get("event")))
        path = cfg.data_for(course) / f"{safe_name(params['id'])}{SUFFIX}"
    except KeyError as e:
        raise ReadyError(tr("Missing '{name}'.", name=e.args[0])) from None
    d = _read(path)
    if d is None:
        raise ReadyError(tr("This recording isn't waiting to be transcribed any more."))
    return course, path, d


def _job(d: dict) -> Path | None:
    return Path(d["job"]) if d.get("job") else None


def state(cfg, d: dict, data_dir: Path) -> str:
    """ready (waiting for Transcribe), queued (confirmed, waiting for its turn), finalizing (being transcribed). A
    transcript is ready until it's added (which takes a moment, not a queue)."""
    if d.get("transcript"):
        return "ready"
    job = _job(d)
    if not job or not job.exists():
        return "ready"  # never confirmed, or its pass failed (`error` says so)
    if time.time() - float(d.get("started") or 0) < STARTING_GRACE_S:
        return "finalizing"
    if read_progress(progress_path(data_dir, d["folder"])) and jobs.queue_busy():
        return "finalizing"
    return "queued"


def waits_for(cfg) -> str | None:
    """Why a confirmed recording would wait now rather than start: "recording" (one is on), "power" (Stenny's
    queue waits for the computer to be plugged in), "night" (it runs only at night), "memory"; None: it can start."""
    if jobs.recording_running():
        return "recording"
    if cfg.require_ac_for_queue and not jobs.on_ac_power():
        return "power"
    if cfg.night_only and not jobs.in_window(cfg.night_start, cfg.night_end):
        return "night"
    if not resources.enough_memory_for_final_pass():
        return "memory"
    return None


def _entry(cfg, course: Course, path: Path, d: dict, waiting: str | None) -> dict:
    """The library entry, as share.library's look (app_server.library numbers it among the course's lectures)."""
    data_dir = cfg.data_for(course)
    status = state(cfg, d, data_dir)
    src = Path(d["file"])
    error = None
    if status == "ready":
        if not src.is_file():
            error = tr("The file isn't there any more: {path}", path=str(src))
        elif _job(d) and _job(d).with_suffix(".failed").exists():
            error = tr("The transcript couldn't be made. The file is still there; try again, or use Send Feedback.")
    entry = {
        "kind": "event" if course.is_event else "lecture",
        "course": course.name,
        "folder": d["folder"],
        "number": 0,
        "title": None,
        "date": d["date"],
        "start": d.get("start"),
        "duration": fmt_hms(float(d.get("seconds") or 0)),
        "language": d.get("language") or (course.language if not course.is_event else "mixed"),
        "status": status,
        "draft_note": None,
        "waiting": waiting if status in ("ready", "queued") else None,
        "transcript": None,
        "summary": None,
        "ready": {"id": path.name.removesuffix(SUFFIX), "file": str(src), "name": src.name, "error": error,
                  "night_start": cfg.night_start},
    }
    if d.get("transcript"):  # nothing to transcribe: where it comes from, what comes along, how it begins
        entry.update(language=None, waiting=None, duration=fmt_hms(float(d["seconds"])) if d.get("seconds") else None)
        entry["ready"]["error"] = None if Path(d.get("copy") or "").is_file() else \
            tr("The file isn't there any more: {path}", path=str(src))
        entry["ready"]["transcript"] = {"by": d.get("by"), "summary": bool(d.get("summary")),
                                        "timestamps": bool(d.get("timestamps")), "preview": d.get("preview") or []}
    if status == "finalizing":
        entry["final_pass"] = read_progress(progress_path(data_dir, d["folder"]))
    return entry


def entries(cfg) -> list[dict]:
    """Every recording added in the window that has no transcript yet, in every course (past ones too) and talk."""
    found: list[tuple[Course, Path]] = []
    for course in cfg.courses.values():
        found += [(course, p) for p in _ready_files(cfg.data_for(course))]
    talks = cfg.recordings_dir / TALKS_DIR
    if talks.is_dir():
        for d in sorted(talks.iterdir()):
            if d.is_dir():
                found += [(cfg.event(d.name), p) for p in _ready_files(d)]
    if not found:
        return []
    waiting = waits_for(cfg)
    out = []
    for course, path in found:
        d = _read(path)
        if d is None:
            continue
        folder = cfg.vault_for(course).course_dir(course.name) / d["folder"]
        if LectureFiles(folder, course.name).existing("transcript"):
            if state(cfg, d, cfg.data_for(course)) != "ready":
                continue  # its transcript is being written: the Stenny folder's entry shows it
            # a recording took the folder meanwhile (one started the same day): this one moves on to the next
            d["folder"] = _free_folder(cfg, course, d["date"], keep=path)
            _write(path, d)
        out.append(_entry(cfg, course, path, d, waiting))
    return out


def add(cfg, params: dict) -> dict:
    """A file (`path`) into a course (`course`) or a talk (`event`, its title; with `language`), ready to
    transcribe: its length and date read from the file, its place among the lectures by that date. The same file
    added twice to one course is one entry."""
    event = bool(params.get("event"))
    try:
        course = _course(cfg, params["event"] if event else params["course"], event)
        src = Path(params["path"]).expanduser()
    except KeyError as e:
        raise ReadyError(tr("Missing '{name}'.", name=e.args[0])) from None
    if not src.is_file():
        raise ReadyError(tr("The file isn't there any more: {path}", path=str(src)))
    data_dir = cfg.data_for(course)
    for p in _ready_files(data_dir):
        d = _read(p)
        if d and Path(d["file"]) == src.resolve():
            return _entry(cfg, course, p, d, waits_for(cfg))
    if transcripts.is_transcript(src):
        return _add_transcript(cfg, course, src)
    info = audio.probe(src)
    if not info or not info["audio"]:
        raise ReadyError(tr("“{name}” isn't a recording or a transcript Stenny can read.", name=src.name))
    when = audio.recorded_at(src)
    language = params.get("language") or None
    if event and language not in (None, "mixed", *cfg.event_languages):
        raise ReadyError(tr("Stenny is made for English, German and French so far."))
    rid = uuid.uuid4().hex[:12]
    path = data_dir / f"{rid}{SUFFIX}"
    d = {"file": str(src.resolve()), "course": course.name, "kind": course.kind, "date": f"{when:%Y-%m-%d}",
         "start": f"{when:%H:%M}", "seconds": round(info["seconds"], 1), "language": language,
         "folder": _free_folder(cfg, course, f"{when:%Y-%m-%d}"), "added": datetime.now().isoformat(timespec="seconds")}
    _write(path, d)
    return _entry(cfg, course, path, d, waits_for(cfg))


def _file_day(src: Path) -> str:
    """The day a file was made (a transcript that doesn't say its date: often the day it arrived; it can be
    changed in the window)."""
    st = src.stat()
    return f"{datetime.fromtimestamp(getattr(st, 'st_birthtime', st.st_mtime)):%Y-%m-%d}"


def _read_transcript(src: Path) -> transcripts.Transcript:
    try:
        return transcripts.read(src)
    except transcripts.TranscriptError as e:
        raise ReadyError(str(e)) from None


def _add_transcript(cfg, course: Course, src: Path) -> dict:
    """A transcript, ready to add: read now (a scan is turned down at once), Stenny's own copy of the file kept."""
    t = _read_transcript(src)
    data_dir = cfg.data_for(course)
    data_dir.mkdir(parents=True, exist_ok=True)
    rid = uuid.uuid4().hex[:12]
    copy = data_dir / f"{rid}.original{src.suffix.lower()}"
    shutil.copy2(src, copy)
    date = t.date or _file_day(src)
    path = data_dir / f"{rid}{SUFFIX}"
    d = {"file": str(src.resolve()), "course": course.name, "kind": course.kind, "transcript": True,
         "copy": str(copy), "by": t.by, "summary": bool(t.summary), "timestamps": t.timestamps,
         "preview": transcripts.preview(t), "date": date, "start": t.start, "seconds": t.seconds,
         "folder": _free_folder(cfg, course, date), "added": datetime.now().isoformat(timespec="seconds")}
    _write(path, d)
    return _entry(cfg, course, path, d, None)


def import_file(cfg, course: Course, src: Path, date: str | None = None) -> dict:
    """`stenny add` with a transcript: added at once, as it is (no ready step in a terminal)."""
    t = _read_transcript(src)
    data_dir = cfg.data_for(course)
    data_dir.mkdir(parents=True, exist_ok=True)
    copy = data_dir / f".{uuid.uuid4().hex[:12]}.original{src.suffix.lower()}"
    shutil.copy2(src, copy)
    day = date or t.date or _file_day(src)
    from .pipeline import add_transcript
    return add_transcript(cfg, course, t, copy, src.name, date=day, start=t.start,
                          folder_name=_free_folder(cfg, course, day))


def change(cfg, params: dict) -> dict:
    """The date (`date`, YYYY-MM-DD) or the language (`language`) of a recording not yet being transcribed. A new
    date moves it to its place among the lectures; the clock time stays the file's."""
    course, path, d = _find(cfg, params)
    if state(cfg, d, cfg.data_for(course)) == "finalizing":
        raise ReadyError(tr("It's being transcribed right now."))
    if params.get("date"):
        try:
            date = datetime.strptime(str(params["date"]), "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            raise ReadyError(tr("Not a date: {date}", date=params["date"])) from None
        if date != d["date"]:
            d["date"], d["folder"] = date, _free_folder(cfg, course, date, keep=path)
    if "language" in params:
        d["language"] = params["language"] or None
    job = _job(d)
    if job and job.exists():  # confirmed, still waiting: its job takes the change too
        _write(job, {**json.loads(job.read_text(encoding="utf-8")), **_job_fields(course, path, d)})
    _write(path, d)
    return _entry(cfg, course, path, d, waits_for(cfg))


def _job_fields(course: Course, path: Path, d: dict) -> dict:
    language = d.get("language")
    return {"lang": None if language == course.language else language,
            "ready": {"entry": str(path), "folder": d["folder"], "when": f"{d['date']}T{d.get('start') or '00:00'}"}}


def transcribe(cfg, params: dict) -> dict:
    """Transcribe: an ordinary job in the queue, started at once in its own process when nothing makes it wait
    (waits_for), else the queue's next run takes it (after the recording, once plugged in, at night). Again with
    `now` while it waits: it starts now (Transcribe Now, like a recording's Make Final Transcript Now)."""
    course, path, d = _find(cfg, params)
    if d.get("transcript"):  # Add: into the Stenny folder now, as it is
        copy = Path(d.get("copy") or "")
        if not copy.is_file():
            raise ReadyError(tr("The file isn't there any more: {path}", path=d["file"]))
        from .pipeline import add_transcript
        info = add_transcript(cfg, course, _read_transcript(copy), copy, Path(d["file"]).name, date=d["date"],
                              start=d.get("start"), folder_name=d["folder"])
        path.unlink(missing_ok=True)
        return {"added": True, "folder": Path(info["folder"]).name}
    status = state(cfg, d, cfg.data_for(course))
    if status == "queued" and params.get("now"):  # Transcribe Now: this one, now, whatever it waited for
        if jobs.queue_busy():
            raise ReadyError(tr("A transcript is being made right now. Try again when it's done."))
        d["started"] = time.time()
        _write(path, d)
        _start(_job(d))
        return _entry(cfg, course, path, d, None)
    if status != "ready":
        return _entry(cfg, course, path, d, waits_for(cfg))
    src = Path(d["file"])
    if not src.is_file():
        raise ReadyError(tr("The file isn't there any more: {path}", path=str(src)))
    if (old := _job(d)) is not None:
        old.with_suffix(".failed").unlink(missing_ok=True)  # trying again
    fields = _job_fields(course, path, d)
    job = jobs.enqueue(src, course.name, fields["lang"], True, kind=course.kind, ready=fields["ready"])
    d["job"] = str(job)
    d.pop("started", None)
    waiting = waits_for(cfg)
    if waiting is None and not jobs.queue_busy():
        d["started"] = time.time()
        _write(path, d)
        _start(job)
    else:
        _write(path, d)
    return _entry(cfg, course, path, d, waiting)


def _start(job: Path) -> None:
    from .app_live import open_log
    args = [sys.executable, "-m", "stenny", "queue", "run", "--job", str(job)]
    with open_log("queue.log") as log:  # like the scheduled runs': a pass that fails must leave a trace
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def remove(cfg, params: dict) -> dict:
    """Out of Stenny again: the entry and its job if it waits. The original file stays where it is."""
    course, path, d = _find(cfg, params)
    if state(cfg, d, cfg.data_for(course)) == "finalizing":
        raise ReadyError(tr("It's being transcribed right now."))
    if (job := _job(d)) is not None:
        job.unlink(missing_ok=True)
        job.with_suffix(".failed").unlink(missing_ok=True)
    if d.get("copy"):  # Stenny's own copy of a transcript; the file it came from stays where it is
        Path(d["copy"]).unlink(missing_ok=True)
    path.unlink(missing_ok=True)
    return {"removed": True}


def numbered(cfg, entries: list[dict]) -> list[dict]:
    """The library with the ready recordings among the lectures: each course's oldest first, and where a course has
    one, every lecture numbered by its place, as they'll be numbered once it's transcribed (course_files.rebuild).
    The files in the Stenny folder keep their numbers until then."""
    order = sorted(entries, key=lambda e: (e["kind"] == "event", safe_name(e["course"]).casefold(),
                                           folder_key(e["folder"])))
    with_ready = {e["course"] for e in order if e.get("ready") and e["kind"] == "lecture"}
    count: dict[str, int] = {}
    for e in order:
        if e["kind"] == "lecture" and e["course"] in with_ready:
            count[e["course"]] = e["number"] = count.get(e["course"], 0) + 1
    return order
