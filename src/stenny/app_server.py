"""`stenny app-server`: the engine behind Stenny's app (desktop/).

One JSON request per line on stdin, one reply per line on stdout:
    {"id": 1, "method": "library", "params": {}}  →  {"id": 1, "result": …}  or  {"id": 1, "error": "…"}
The app starts one server and keeps it running; anything the engine prints goes to stderr, so stdout carries
only replies. The methods are plain functions of (cfg, params), so the phone page can later serve them too.
"""

import contextlib
import json
import re
import subprocess
import sys
from importlib.metadata import version as package_version
from pathlib import Path

from . import app_live, share
from . import config as config_mod
from .config import ConfigError


class AppError(Exception):
    """A message for the person using the app (shown as it is)."""


def _entry(cfg, params: dict) -> dict:
    try:
        return share.find(cfg, params["course"], params["folder"], event=bool(params.get("event")))
    except KeyError as e:
        raise AppError(f"Missing '{e.args[0]}'.") from None
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
    body = SUMMARY_NOTE.sub("> Written by AI from the transcript. It can leave things out or get them wrong; "
                            "the transcript is the source.", "\n".join(lines).strip(), count=1)
    return share._unlink(body) + "\n"


def lecture(cfg, params: dict) -> dict:
    """What the detail view shows besides the library entry: the summary as Markdown (None if there is none)."""
    entry = _entry(cfg, params)
    summary = Path(entry["summary"]).read_text(encoding="utf-8") if entry.get("summary") else None
    return {"entry": entry, "summary": summary_markdown(summary) if summary else None}


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
        raise AppError("This lecture has no final transcript waiting.")
    args = [sys.executable, "-m", "stenny", "queue", "run", "--lecture", entry["course"], entry["folder"]]
    subprocess.Popen(args + (["--event"] if event else []), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return {"started": True}


def ping(cfg, params: dict) -> dict:
    return {"version": package_version("stenny")}


METHODS = {"ping": ping, "library": library, "lecture": lecture, "make_copy": make_copy, "finish_now": finish_now,
           **app_live.METHODS}


def handle(cfg, request: dict) -> dict:
    """One request → one reply. Unknown methods and AppErrors become {"error": …}; other exceptions too, with
    their type, so the app never waits for a reply that doesn't come."""
    rid = request.get("id")
    method = METHODS.get(request.get("method"))
    if not method:
        return {"id": rid, "error": f"Unknown method '{request.get('method')}'."}
    try:
        return {"id": rid, "result": method(cfg, request.get("params") or {})}
    except (AppError, app_live.LiveError) as e:
        return {"id": rid, "error": str(e)}
    except Exception as e:  # noqa: BLE001 — reported to the app, logged to stderr
        print(f"app-server: {request.get('method')} failed: {e!r}", file=sys.stderr)
        return {"id": rid, "error": f"Something went wrong ({type(e).__name__}: {e})."}


def serve(stdin=sys.stdin, stdout=sys.stdout) -> None:
    """Reads requests until stdin closes (the app quit). The config is read again for every request, so a course
    added in the config shows up without restarting."""
    for line in stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except ValueError:
            reply = {"id": None, "error": "Not JSON."}
        else:
            with contextlib.redirect_stdout(sys.stderr):
                try:
                    cfg = config_mod.load()
                except ConfigError as e:
                    reply = {"id": request.get("id"), "error": f"Stenny's settings couldn't be read: {e}"}
                else:
                    reply = handle(cfg, request)
        stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        stdout.flush()
