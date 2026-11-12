"""`stenny app-server`: the engine behind Stenny's app (desktop/).

One JSON request per line on stdin, one reply per line on stdout:
    {"id": 1, "method": "library", "params": {}}  →  {"id": 1, "result": …}  or  {"id": 1, "error": "…"}
The app starts one server and keeps it running; anything the engine prints goes to stderr, so stdout carries
only replies. The methods are plain functions of (cfg, params), so the phone page can later serve them too.
"""

import json
import re
import subprocess
import sys
import threading
from importlib.metadata import version as package_version
from pathlib import Path

from . import app_live, i18n, settings, share
from . import config as config_mod
from .config import ConfigError
from .i18n import tr
from .textproc import fmt_hms


class AppError(Exception):
    """A message for the person using the app (shown as it is)."""


def _with_live(entries: list[dict]) -> list[dict]:
    """The running recording's entry, while it records: its length from the session itself, as the menu bar's timer
    shows it (pauses left out; the placeholder's `duration` is only written now and then), and `live`, what the
    window needs between two looks: whether the length is counting up, whether the text so far is being
    transcribed, and a warning (low battery or memory, a full disk) while one applies."""
    if not any(e["status"] == "live" for e in entries):
        return entries
    try:
        st = app_live.control.send("status", timeout=3)
    except (app_live.control.NoSession, OSError, ValueError):
        return entries
    if st.get("state") not in ("starting", "recording", "paused") or not st.get("transcript"):
        return entries
    running = Path(st["transcript"])
    for e in entries:
        if e["status"] == "live" and _same_file(Path(e["transcript"]), running):
            length = max(0.0, float(st.get("elapsed_s") or 0) - float(st.get("paused_s") or 0))
            e["duration"] = fmt_hms(length)
            e["live"] = {"length_s": round(length, 1), "counting": st["state"] == "recording" and not st.get("input_lost"),
                         "draft": bool(st.get("draft")), "transcribing": bool(st.get("transcribing")),
                         "warning": st.get("warning") or ""}
    return entries


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a == b or a.resolve() == b.resolve()
    except OSError:
        return False


def _entry(cfg, params: dict) -> dict:
    try:
        return _with_live([share.find(cfg, params["course"], params["folder"], event=bool(params.get("event")))])[0]
    except KeyError as e:
        raise AppError(tr("Missing '{name}'.", name=e.args[0])) from None
    except share.ShareError as e:
        raise AppError(str(e)) from None


def library(cfg, params: dict) -> dict:
    """Every lecture and talk (oldest first within a course) and the courses in the config. A lecture whose final
    pass runs has `final_pass`: how far it is (progress.read), or None before it reports anything; the recording
    that runs now has `live` (_with_live)."""
    entries = _with_live(share.library(cfg))
    for entry in entries:
        if entry["status"] == "finalizing":
            entry["final_pass"] = final_pass(cfg, entry)
    return {"entries": entries,
            "courses": [{"name": c.name, "hidden": c.hidden} for c in cfg.courses.values()]}


def _data_base(cfg, entry: dict) -> Path | None:
    """Where a lecture's files in the data place start (`<data>/<Course>/<folder>`), None for an unknown course."""
    course = cfg.event(entry["course"]) if entry["kind"] == "event" else cfg.courses.get(entry["course"])
    return cfg.data_for(course) / entry["folder"] if course is not None else None


def final_pass(cfg, entry: dict) -> dict | None:
    from .progress import progress_path, read
    base = _data_base(cfg, entry)
    return read(progress_path(base.parent, base.name)) if base else None


SUMMARY_NOTE = re.compile(r"^> AI-generated summary .*?(?=\n\n|\Z)", re.S)


def summary_markdown(text: str) -> str:
    """A summary file as the app shows it: no frontmatter, no title of its own, Obsidian links as plain text, and
    the file's technical note (model, file link) as one plain sentence."""
    lines = share._body(text).splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    note = tr("Written by AI from the transcript. It can leave things out or get them wrong; the transcript is the source.")
    body = SUMMARY_NOTE.sub(lambda _: "> " + note, "\n".join(lines).strip(), count=1)
    return share._unlink(body) + "\n"


def progress(cfg, entry: dict) -> str | None:
    """A recording without its transcript yet: the text transcribed so far (during the recording, or by a final
    pass that was interrupted) as Markdown, None if there is none. Raw Whisper text, shown only in the window."""
    from .pipeline import ChunkCache
    from .textproc import fmt_hms, to_paragraphs

    base = _data_base(cfg, entry)
    if base is None:
        return None
    cache = ChunkCache(Path(f"{base}.chunks.jsonl"))
    if not cache.chunks:
        return None
    try:
        breaks = [p[0] for p in json.loads(Path(f"{base}.session.json").read_text(encoding="utf-8"))["pauses"]]
    except (OSError, ValueError, KeyError):
        breaks = []
    paragraphs = to_paragraphs(cache.text_so_far(), breaks=breaks)
    return "\n\n".join(f"**[{fmt_hms(p['start'])}]** {p['text']}" for p in paragraphs) + "\n"


def lecture(cfg, params: dict) -> dict:
    """What the detail view shows besides the library entry: the summary as Markdown (None if there is none), and
    for a recording without its transcript yet the text so far (`progress`)."""
    entry = _entry(cfg, params)
    summary = Path(entry["summary"]).read_text(encoding="utf-8") if entry.get("summary") else None
    return {"entry": entry, "summary": summary_markdown(summary) if summary else None,
            "progress": progress(cfg, entry) if entry["status"] != "final" else None}


def make_copy(cfg, params: dict) -> dict:
    """A clean copy to share (md, docx or html) in Stenny's cache folder (paths.SHARED_DIR); returns its path."""
    entry = _entry(cfg, params)
    try:
        path = share.share(entry, with_summary=bool(params.get("with_summary")), fmt=params.get("format", "md"))
    except share.ShareError as e:
        raise AppError(str(e)) from None
    return {"path": str(path)}


def finish_now(cfg, params: dict) -> dict:
    """Starts a waiting lecture's final pass in its own process (`stenny queue run --lecture`), so the server stays
    free. The draft says "finalizing" before this returns: the process needs a few seconds to start and load the
    model, and the window shouldn't show the button again meanwhile. If the pass can't start after all, the
    process puts it back to "queued" (cli.cmd_queue)."""
    from . import jobs
    from .cli import lecture_job, mark_job_draft
    entry = _entry(cfg, params)
    event = entry["kind"] == "event"
    job = lecture_job(cfg, entry["course"], entry["folder"], event=event)
    if not job:
        raise AppError(tr("This lecture has no final transcript waiting."))
    with jobs.QueueLock() as free:  # another final pass running: say so now, the new process would only give up
        if free is None:
            raise AppError(tr("A transcript is being made right now. Try again when it's done."))
    mark_job_draft(cfg, job, "finalizing")
    args = [sys.executable, "-m", "stenny", "queue", "run", "--lecture", entry["course"], entry["folder"]]
    try:
        subprocess.Popen(args + (["--event"] if event else []), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        mark_job_draft(cfg, job, "queued")
        raise
    return {"started": True}


def _managed(fn, *args, **kw):
    from .manage import ManageError
    try:
        return fn(*args, **kw)
    except (ManageError, ConfigError) as e:
        raise AppError(str(e)) from None
    except KeyError as e:
        raise AppError(tr("Missing '{name}'.", name=e.args[0])) from None


def rename_lecture(cfg, params: dict) -> dict:
    """A lecture's own title (empty: "Lecture N" again)."""
    from . import manage
    title = _managed(manage.rename_lecture, cfg, params["course"], params["folder"], params.get("title") or "",
                     event=bool(params.get("event")))
    return {"title": title or None}


def delete_lecture(cfg, params: dict) -> dict:
    """A lecture to the Trash."""
    from . import manage
    return {"message": _managed(manage.delete_lecture, cfg, params["course"], params["folder"],
                                event=bool(params.get("event")))}


def rename_course(cfg, params: dict) -> dict:
    """A course (or with `event`, a talk) renamed everywhere."""
    from . import manage
    return {"name": _managed(manage.rename_course, cfg, params["course"], params["name"],
                             event=bool(params.get("event")))}


def delete_course(cfg, params: dict) -> dict:
    """A whole course (or with `event`, a talk) and all its recordings to the Trash."""
    from . import manage
    return {"message": _managed(manage.delete_course, cfg, params["course"], event=bool(params.get("event")))}


def add_course(cfg, params: dict) -> dict:
    """A new course (`name`, `language`, optional `alias`), or a removed one back with its settings."""
    from . import courses
    return {"message": _managed(courses.add, cfg, params["name"], params.get("language") or "auto",
                                None, params.get("alias") or None)}


def remove_course(cfg, params: dict) -> dict:
    """A course out of the menu and calendar matching; its recordings stay, under Past courses in Settings."""
    from . import courses
    return {"message": _managed(courses.hide, cfg, params["course"])}


def preferences(cfg, params: dict) -> dict:
    """The Settings window: everything it shows (settings.py)."""
    return settings.get(cfg)


def set_preference(cfg, params: dict) -> dict:
    """One setting changed in the Settings window, e.g. {"key": "llm.summary", "value": false}; all of them again."""
    return _managed(settings.change, cfg, params["key"], params.get("value"))


def set_course_language(cfg, params: dict) -> dict:
    return _managed(settings.course_language, cfg, params["course"], params["language"], params.get("languages"))


def _setup(fn, *args):
    from .claude_setup import SetupError
    try:
        return fn(*args)
    except SetupError as e:
        raise AppError(str(e)) from None


def ai_status(cfg, params: dict) -> dict:
    """Whether the chosen option of the AI step works right now (claude_setup.py); spends no tokens."""
    from . import claude_setup
    return claude_setup.ai_status(cfg)


def set_api_key(cfg, params: dict) -> dict:
    """An Anthropic API key, checked and saved in the system keychain; ai_status again."""
    from . import claude_setup
    return _setup(claude_setup.set_api_key, cfg, params.get("key") or "")


def remove_api_key(cfg, params: dict) -> dict:
    from . import claude_setup
    return _setup(claude_setup.remove_api_key, cfg)


def claude_sign_in(cfg, params: dict) -> dict:
    """`claude auth login` in the background: the browser opens, no terminal window."""
    from . import claude_setup
    return _setup(claude_setup.claude_sign_in, cfg)


def connector_status(cfg, params: dict) -> dict:
    """The Stenny connector: in Claude Desktop or not, in Claude Code or not."""
    from . import claude_setup
    return claude_setup.connector_status(cfg)


def install_connector(cfg, params: dict) -> dict:
    """Stenny.mcpb opened with Claude Desktop, which shows its own Install dialog."""
    from . import claude_setup
    return _setup(claude_setup.install_connector, cfg)


def add_to_claude_code(cfg, params: dict) -> dict:
    from . import claude_setup
    return _setup(claude_setup.add_to_claude_code, cfg)


def open_claude(cfg, params: dict) -> dict:
    """The Claude desktop app, where the connector is removed again."""
    from . import claude_setup
    return _setup(claude_setup.open_claude, cfg)


def remove_from_claude_code(cfg, params: dict) -> dict:
    from . import claude_setup
    return _setup(claude_setup.remove_from_claude_code, cfg)


def setup_status(cfg, params: dict) -> dict:
    """The library window's "Finish setting up Stenny" card: whether it's still wanted (`[app] setup_checklist`),
    and the real state of each step, checked now: courses, the AI step (ai_status), the connector, the calendar
    (calendars.status). Nothing is checked once the card is gone for good."""
    if not getattr(cfg, "setup_checklist", True):
        return {"show": False}
    from . import calendars, claude_setup
    return {"show": True,
            "courses": sum(1 for c in cfg.courses.values() if not c.hidden),
            "ai": claude_setup.ai_status(cfg),
            "connector": claude_setup.connector_status(cfg),
            "calendar": calendars.status(cfg)}


def _calendar(fn, *args):
    from .calendars import CalendarError
    try:
        return fn(*args)
    except CalendarError as e:
        raise AppError(str(e)) from None


def calendar_status(cfg, params: dict) -> dict:
    """Settings ▸ Calendar: whether Stenny may read the Mac's calendar, the calendars it sees (the Mac's accounts,
    the pasted links) with how many lectures match the courses, and the notifications (calendars.py)."""
    from . import calendars
    return calendars.status(cfg)


def add_calendar_link(cfg, params: dict) -> dict:
    """A pasted iCal link (`link`), checked and kept in the system's password store; calendar_status again."""
    from . import calendars
    return _calendar(calendars.add_link, cfg, params.get("link") or "")


def remove_calendar_link(cfg, params: dict) -> dict:
    from . import calendars
    return _calendar(calendars.remove_link, cfg, params.get("id") or "")


def calendar_events(cfg, params: dict) -> dict:
    """The linked calendars' events on now or within 10 minutes, as the Mac helper's "events" line; the app asks
    every 30 seconds, and a link is fetched again when its copy is a few minutes old."""
    from . import calendars
    return {"events": calendars.upcoming()}


def _glossary_course(cfg, params: dict) -> str | None:
    """The course whose glossary a lecture's edit can teach, or None for a talk (talks have no glossary)."""
    if params.get("event"):
        return None
    try:
        course = cfg.course(params["course"])
        return None if course.is_event else course.name
    except KeyError as e:
        raise AppError(tr("Missing '{name}'.", name=e.args[0])) from None
    except ConfigError:
        return params["course"]  # a past course, no longer in the config: its glossary note is still there


def spelling_suggestions(cfg, params: dict) -> dict:
    """After the student changed a passage of a transcript (`before` → `after`): the changes that look like a misheard
    name or term (guard.spelling_changes), for "Always write it like this?". Fixing ordinary words or rewording asks
    nothing, and neither does a spelling the course's glossary already corrects."""
    from .glossary import Glossary
    from .llm.guard import spelling_changes

    course = _glossary_course(cfg, params)
    if not course:
        return {"suggestions": []}
    glossary = Glossary.load(cfg.vault_for().glossary_path(course))
    known = set(glossary.own_corrections)
    changes = spelling_changes(str(params.get("before") or ""), str(params.get("after") or ""))
    return {"suggestions": [{"wrong": w, "right": r} for w, r in changes if (w, r) not in known]}


def always_spell(cfg, params: dict) -> dict:
    """The student's answer "Yes" to "Always write it like this?": `wrong → right` in the course glossary's own
    Corrections and `right` in its Terms (glossary.add_correction), so every lecture transcribed from now on (Whisper's
    prompt and the AI step) gets it right. Makes the glossary note if the course has none yet."""
    from .glossary import TEMPLATE, add_correction
    from .llm.guard import check_correction, clean_entry

    course = _glossary_course(cfg, params)
    if not course:
        raise AppError(tr("Talks have no glossary."))
    try:
        wrong, right = clean_entry(params["wrong"]), clean_entry(params["right"])
    except KeyError as e:
        raise AppError(tr("Missing '{name}'.", name=e.args[0])) from None
    if check_correction(wrong, right):
        raise AppError(tr("Stenny can only remember the spelling of a name or term, a few words long."))
    vault = cfg.vault_for()
    path = vault.glossary_path(course)
    old = path.read_text(encoding="utf-8") if path.exists() else TEMPLATE.format(course=course)
    vault.write(path, add_correction(old, wrong, right))
    return {"course": course, "wrong": wrong, "right": right}


def ping(cfg, params: dict) -> dict:
    return {"version": package_version("stenny")}


METHODS = {"ping": ping, "library": library, "lecture": lecture, "make_copy": make_copy, "finish_now": finish_now,
           "rename_lecture": rename_lecture, "delete_lecture": delete_lecture, "rename_course": rename_course, "delete_course": delete_course,
           "add_course": add_course, "remove_course": remove_course, "preferences": preferences,
           "set_preference": set_preference, "set_course_language": set_course_language,
           "ai_status": ai_status, "set_api_key": set_api_key, "remove_api_key": remove_api_key,
           "claude_sign_in": claude_sign_in, "connector_status": connector_status,
           "install_connector": install_connector, "add_to_claude_code": add_to_claude_code,
           "open_claude": open_claude, "remove_from_claude_code": remove_from_claude_code,
           "setup_status": setup_status, "calendar_status": calendar_status, "add_calendar_link": add_calendar_link,
           "remove_calendar_link": remove_calendar_link, "calendar_events": calendar_events,
           "spelling_suggestions": spelling_suggestions, "always_spell": always_spell,
           **app_live.METHODS}


def handle(cfg, request: dict) -> dict:
    """One request → one reply. Unknown methods and AppErrors become {"error": …}; other exceptions too, with
    their type, so the app never waits for a reply that doesn't come."""
    rid = request.get("id")
    method = METHODS.get(request.get("method"))
    if not method:
        return {"id": rid, "error": tr("Unknown method '{name}'.", name=request.get("method"))}
    try:
        return {"id": rid, "result": method(cfg, request.get("params") or {})}
    except (AppError, app_live.LiveError) as e:
        return {"id": rid, "error": str(e)}
    except Exception as e:  # noqa: BLE001 — reported to the app, logged to stderr
        print(f"app-server: {request.get('method')} failed: {e!r}", file=sys.stderr)
        return {"id": rid, "error": tr("Something went wrong ({error}).", error=f"{type(e).__name__}: {e}")}


def _answer(line: str) -> dict:
    try:
        request = json.loads(line)
    except ValueError:
        return {"id": None, "error": tr("Not JSON.")}
    i18n.set_language(i18n.system_language())  # until the config says otherwise
    try:
        cfg = config_mod.load()
    except ConfigError as e:
        return {"id": request.get("id"), "error": tr("Stenny's settings couldn't be read: {error}", error=e)}
    # every reply in the app's language (Settings ▸ General); the config is read again for each request
    i18n.set_language(i18n.resolve(getattr(cfg, "app_language", None)))
    return handle(cfg, request)


def serve(stdin=sys.stdin, stdout=sys.stdout) -> None:
    """Reads requests until stdin closes (the app quit), each in its own thread: a slow one (the microphone
    list, a share copy) mustn't hold up the recording's status. Replies carry their request's id, so their order
    doesn't matter. The config is read again for every request, so a course added in the config shows up
    without restarting. Anything else printed goes to stderr, so stdout carries only replies.
    First it moves an older install into today's places, or starts a config if there is none (migrate.on_start)."""
    lock = threading.Lock()
    sys.stdout = sys.stderr
    from .migrate import on_start
    on_start()

    def reply(line: str) -> None:
        answer = _answer(line)
        with lock:
            stdout.write(json.dumps(answer, ensure_ascii=False) + "\n")
            stdout.flush()

    workers = []
    try:
        for line in stdin:
            if line.strip():
                t = threading.Thread(target=reply, args=(line,), daemon=True)
                t.start()
                workers.append(t)
        for t in workers:
            t.join(timeout=30)
    finally:
        sys.stdout = sys.__stdout__
