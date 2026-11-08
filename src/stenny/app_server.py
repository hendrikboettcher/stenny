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


class AppError(Exception):
    """A message for the person using the app (shown as it is)."""


def _entry(cfg, params: dict) -> dict:
    try:
        return share.find(cfg, params["course"], params["folder"], event=bool(params.get("event")))
    except KeyError as e:
        raise AppError(tr("Missing '{name}'.", name=e.args[0])) from None
    except share.ShareError as e:
        raise AppError(str(e)) from None


def library(cfg, params: dict) -> dict:
    """Every lecture and talk (oldest first within a course) and the courses in the config."""
    return {"entries": share.library(cfg),
            "courses": [{"name": c.name, "hidden": c.hidden} for c in cfg.courses.values()]}


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

    course = cfg.event(entry["course"]) if entry["kind"] == "event" else cfg.courses.get(entry["course"])
    if course is None:
        return None
    base = cfg.data_for(course) / entry["folder"]
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
    """A clean copy to share (md, docx or html) in ~/Lectures Data/_Shared; returns its path."""
    entry = _entry(cfg, params)
    try:
        path = share.share(entry, with_summary=bool(params.get("with_summary")), fmt=params.get("format", "md"))
    except share.ShareError as e:
        raise AppError(str(e)) from None
    return {"path": str(path)}


def finish_now(cfg, params: dict) -> dict:
    """Starts a waiting lecture's final pass in its own process (`stenny queue run --lecture`), so the server stays
    free; the library shows "finalizing" once it runs and "final" when it's done."""
    from .cli import lecture_job
    entry = _entry(cfg, params)
    event = entry["kind"] == "event"
    if not lecture_job(cfg, entry["course"], entry["folder"], event=event):
        raise AppError(tr("This lecture has no final transcript waiting."))
    args = [sys.executable, "-m", "stenny", "queue", "run", "--lecture", entry["course"], entry["folder"]]
    subprocess.Popen(args + (["--event"] if event else []), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
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
    """A terminal window with `claude auth login`."""
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
    and the real state of each step, checked now: courses, the AI step (ai_status), the connector. Nothing is
    checked once the card is gone for good."""
    if not getattr(cfg, "setup_checklist", True):
        return {"show": False}
    from . import claude_setup
    return {"show": True,
            "courses": sum(1 for c in cfg.courses.values() if not c.hidden),
            "ai": claude_setup.ai_status(cfg),
            "connector": claude_setup.connector_status(cfg)}


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
           "setup_status": setup_status, **app_live.METHODS}


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
    without restarting. Anything else printed goes to stderr, so stdout carries only replies."""
    lock = threading.Lock()
    sys.stdout = sys.stderr

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
