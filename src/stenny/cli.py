"""`stenny` command line."""

import argparse
import contextlib
import json
import subprocess
import sys
import time
from datetime import datetime
from importlib.metadata import version as package_version
from pathlib import Path

from . import config as config_mod
from . import jobs, paths, resources
from .config import ConfigError, Course
from .i18n import tr
from .paths import tool
from .vault import TALKS_DIR

EXAMPLE = config_mod.EXAMPLE
ICLOUD_INBOX = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Lectures"


def load_config():
    try:
        return config_mod.load()
    except ConfigError as e:
        sys.exit(str(e))


def resolve_course(cfg, name: str, lang: str | None) -> Course:
    try:
        return cfg.course(name)
    except ConfigError as e:
        if lang:
            return Course(name=name, language=lang, languages=[lang])
        sys.exit(str(e))


def recording_target(cfg, args) -> Course:
    """`--course X` (a configured course) or `--event "Title"` (a talk or event, see Config.event)."""
    if getattr(args, "event", None):
        if not args.event.strip():
            sys.exit("--event needs a title, e.g. --event \"Guest talk Müller\".")
        course = cfg.event(args.event)
        if args.lang and args.lang not in config_mod.LANGUAGE_MODES:
            course.language, course.languages = args.lang, [args.lang]
    else:
        course = resolve_course(cfg, args.course, args.lang)
    return course.with_language(args.lang, cfg.event_languages)


def parse_when(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value if "T" in value or " " in value else f"{value}T00:00")
    except ValueError:
        sys.exit(f"Invalid --date '{value}', use YYYY-MM-DD or YYYY-MM-DDTHH:MM")


def cmd_init(args) -> None:
    dest = config_mod.config_path()
    if dest.exists():
        sys.exit(f"Config already exists: {dest}")
    if args.vault:  # the options before `folder`
        args.folder = str(Path(args.vault) / (args.lectures_dir or "Lectures"))
    folder = Path(args.folder).expanduser() if args.folder else paths.default_folder()
    text = EXAMPLE.read_text(encoding="utf-8")
    text = text.replace('folder = "~/Documents/Stenny"', f"folder = {json.dumps(paths.tilde(folder), ensure_ascii=False)}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    folder.mkdir(parents=True, exist_ok=True)
    print(f"Wrote {dest} (Stenny folder: {folder}). Edit it to set your courses.")


def cmd_where(args) -> None:
    """Where Stenny keeps everything (paths.py), for people and for the connector and the app (--json)."""
    places = paths.places()
    try:
        cfg = config_mod.load()
        places.update(folder=str(cfg.folder), talks=str(cfg.events_root), recordings=str(cfg.recordings_dir),
                      config=str(cfg.path), error=None)
    except ConfigError as e:
        places.update(config=str(config_mod.config_path()), folder=None, talks=None, recordings=None, error=str(e))
    if args.json:
        print(json.dumps(places, ensure_ascii=False))
        return
    for key in ("config", "folder", "talks", "recordings", "queue", "logs", "shared", "models"):
        if places.get(key):
            print(f"{key + ':':12}{places[key]}")
    if places["error"]:
        print(places["error"])


def cmd_download_model(args) -> None:
    """Download the Whisper model now (with progress), instead of silently during the first transcription."""
    from .config import Config
    from .engines import resolve
    try:
        cfg = config_mod.load()
        model, engine = cfg.model, cfg.engine
    except ConfigError:
        model, engine = Config.model, Config.engine
    if Path(model).expanduser().exists():
        print(f"The model is a local folder, nothing to download: {model}")
        return
    if resolve(engine) == "faster-whisper":  # Windows: CTranslate2's version of the model, in Stenny's Models folder
        from .engines import faster_engine
        where = paths.models_dir() or Path("~/.cache/huggingface")
        print(f"Downloading Whisper {faster_engine.model_name(model)} (about 1.6 GB for large-v3-turbo) to "
              f"{where} …")
        print(f"Done: {faster_engine.download(model)}")
        return
    from huggingface_hub import snapshot_download
    print(f"Downloading {model} (about 1.6 GB for large-v3-turbo) to ~/.cache/huggingface …")
    path = snapshot_download(repo_id=model)
    print(f"Done: {path}")


def cmd_app_settings(args) -> None:
    """Settings Stenny (the menu bar app) reads at launch, as JSON. Moves an older install first (migrate.py)."""
    from .config import DEFAULT_PAUSE_SHORTCUT, parse_shortcut
    from .migrate import on_start
    on_start()
    try:
        shortcut = config_mod.load().pause_shortcut
        error = None
    except ConfigError as e:  # no or broken config: Stenny still gets its default shortcut
        shortcut, error = DEFAULT_PAUSE_SHORTCUT, str(e)
    print(json.dumps({"pause_shortcut": parse_shortcut(shortcut), "pause_shortcut_text": shortcut, "error": error,
                      "data_dir": str(paths.DATA_DIR), "logs_dir": str(paths.LOGS_DIR), "socket": str(paths.SOCKET)},
                     ensure_ascii=False))


def cmd_migrate(args) -> None:
    from .migrate import MigrateError, migrate
    try:
        migrate(dry_run=args.dry_run)
    except MigrateError as e:
        sys.exit(str(e))


def cmd_courses(args) -> None:
    cfg = load_config()
    if args.json:  # for Stenny
        print(json.dumps([{"name": c.name, "language": c.language, "aliases": c.aliases}
                          for c in cfg.visible_courses.values()], ensure_ascii=False))
        return
    print(f"Config: {cfg.path}\nLectures: {cfg.folder}\n")
    for c in cfg.courses.values():
        langs = c.language if c.fixed_language else f"{c.language} {c.languages}"
        aliases = f"  aliases: {', '.join(c.aliases)}" if c.aliases else ""
        print(f"- {c.name}: {langs}{aliases}{'  (hidden)' if c.hidden else ''}")


def cmd_course(args) -> None:
    from .courses import add, hide

    cfg = load_config()
    try:
        if args.action == "add":
            languages = args.languages.split(",") if args.languages else None
            print(add(cfg, args.name, args.language, languages, args.alias))
        else:
            print(hide(cfg, args.name))
    except ConfigError as e:
        sys.exit(str(e))


def cmd_library(args) -> None:
    from .share import library
    entries = library(load_config())
    if args.json:  # for Stenny's window
        print(json.dumps(entries, ensure_ascii=False))
        return
    for e in entries:
        number = f"Lecture {e['number']}, " if e["number"] else ""
        print(f"{e['course']}: {number}{e['folder']}" + ("" if e["status"] == "final" else f"  ({e['status']})"))


def cmd_share(args) -> None:
    from .share import ShareError, find, share
    try:
        entry = find(load_config(), args.course, args.date, event=args.event)
    except ShareError as e:
        sys.exit(str(e))
    try:
        path = share(entry, with_summary=args.with_summary, fmt=args.format)
    except ShareError as e:
        sys.exit(str(e))
    if args.json:
        print(json.dumps({"path": str(path), "status": entry["status"]}, ensure_ascii=False))
    else:
        print(path)


def check_slides(path: Path | None) -> None:
    if path and not path.is_file():
        sys.exit(f"Slides not found: {path}")
    if path and path.suffix.lower() != ".pdf":
        sys.exit(f"Slides must be a PDF: {path}")


def cmd_add(args) -> None:
    cfg = load_config()
    course = recording_target(cfg, args)
    for f in args.files:
        if not f.exists():
            sys.exit(f"File not found: {f}")
    check_slides(args.slides)
    if args.slides and len(args.files) > 1:
        sys.exit("--slides belongs to one lecture: add one file at a time.")
    if args.later:
        for f in args.files:
            print(f"Queued: {jobs.enqueue(f, course.name, args.lang, not args.no_llm, slides=args.slides, kind=course.kind)}")
        print("Runs with `stenny queue run` (or automatically if `stenny schedule install` was set up).")
        return
    jobs.apply_priority("normal" if args.now else args.priority or cfg.priority)

    from .engines import get_engine
    from .pipeline import LectureError, process

    engine = get_engine(cfg.model, cfg.engine)
    for f in args.files:
        try:
            process(f, cfg, course, engine, lang=args.lang, when=parse_when(args.date) if len(args.files) == 1 else None,
                    use_llm=not args.no_llm, summary=False if args.no_summary else None, slides_src=args.slides)
        except LectureError as e:
            sys.exit(str(e))


def cmd_refresh(args) -> None:
    cfg = load_config()
    try:
        course = cfg.event(args.course) if args.event else cfg.course(args.course)
    except ConfigError as e:
        # an ad-hoc course (`stenny add --lang …`): its folder in the vault is enough, refresh needs no language
        root = cfg.folder
        found = [p.name for p in root.iterdir() if p.is_dir() and p.name.casefold() == args.course.casefold()] \
            if root.is_dir() else []
        if not found:
            sys.exit(str(e))
        course = Course(name=found[0])
    check_slides(args.slides)
    from .pipeline import LectureError, refresh

    try:
        refresh(cfg, course, args.date, use_llm=not args.no_llm, summary=False if args.no_summary else None,
                slides_src=args.slides)
    except LectureError as e:
        sys.exit(str(e))


def cmd_match_course(args) -> None:
    """Which configured course a calendar event title belongs to (used by Stenny)."""
    from .config import course_for_title

    cfg = load_config()
    out = [{"title": t, "course": (c := course_for_title(cfg.visible_courses, t)) and c.name} for t in args.titles]
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
        return
    for m in out:
        print(f"{m['title']} → {m['course'] or '(no course)'}")


PROJECT_TEXT = """Lecture transcripts for this course are on my computer, readable with the Filesystem connector, in
{root}

- Overview (every lecture with links to its transcript and summary, and its topics): _Course Files/{course} (overview).md
- Each lecture: {course}/<date>/{course} <date>.md, its AI summary next to it as {course} <date> (summary).md

Start with the overview and the summaries; the transcripts are the source for details. (With Stenny's own
connector installed, use its tools instead: they read the same files.)

Read them whenever a question is explicitly about what was said in the lectures, and cite as "Lecture N [h:mm:ss]".
They are automatic transcripts: names and technical terms may be misheard, so prefer the spellings in the
project files. Don't change any of these files, with one exception:

The glossary _Glossaries/{course} (glossary).md makes future transcripts more accurate. Keep it up to date:
whenever you notice a name or technical term of this course that is missing, add it under
"## Terms (added automatically)" as "- term"; for a misheard spelling you see in a transcript, add
"- wrong → right" under "## Corrections (added automatically)" (start that section at the end of the note if it
isn't there). Never change "## Terms" or "## Corrections": they are mine. Tell me briefly when you changed it."""


def cmd_project_instructions(args) -> None:
    """Text to paste into each course's Claude project instructions (once)."""
    cfg = load_config()
    from .vault import safe_name

    names = [resolve_course(cfg, args.course, None).name] if args.course else list(cfg.courses)
    for i, name in enumerate(names):
        if len(names) > 1:
            print(f"{'' if i == 0 else chr(10)}===== {name} =====")
        print(PROJECT_TEXT.format(root=cfg.folder, course=safe_name(name)))


def cmd_mcp(args) -> None:
    """The connector for Claude: an MCP server on stdin/stdout, started by Claude Desktop."""
    if args.desktop_config:
        from .claude_setup import stenny_command
        command, *rest = stenny_command()
        server = {"command": command, "args": [*rest, "mcp"]}
        print(json.dumps({"mcpServers": {"stenny": server}}, indent=2, ensure_ascii=False))
        return
    from .mcp_server import main as serve
    serve()


def cmd_api_key(args) -> None:
    """The Anthropic API key for `[llm] backend = "api"`, in the system keychain (never in config.toml)."""
    import getpass

    from . import claude_setup, keychain
    if args.action == "remove":
        keychain.delete_key()
        print("Removed the API key from the keychain.")
        return
    if args.action == "status":
        status = claude_setup.api_status()
        source = " (from $ANTHROPIC_API_KEY)" if status.get("source") == "environment" else ""
        print({"no_key": "No API key saved.", "ready": f"The key {status.get('hint')} works{source}.",
               "bad_key": f"Anthropic doesn't accept the key {status.get('hint')}{source}.",
               "unchecked": f"Couldn't check the key: {status.get('detail')}"}[status["state"]])
        return
    key = getpass.getpass("Anthropic API key (from platform.claude.com, not shown as you paste): ")
    try:
        claude_setup.set_api_key(load_config(), key)
    except claude_setup.SetupError as e:
        sys.exit(str(e))
    print("Saved in the keychain. Set [llm] backend = \"api\" to use it (Settings ▸ Transcripts & AI).")


def cmd_course_files(args) -> None:
    """Rebuild the generated course files: overview, previous/next links, topic notes (normally done after
    every final pass)."""
    cfg = load_config()
    from .course_files import rebuild
    from .vault import Vault

    vault = cfg.vault_for()
    names = [resolve_course(cfg, args.course, None).name] if args.course else list(cfg.courses)
    for name in names:
        if not rebuild(vault, name):
            print(f"{name}: no final lectures yet")


def lecture_job(cfg, course: str, folder: str, event: bool = False) -> Path | None:
    """The queued final pass of one live recording (course, lecture folder), if it is still waiting."""
    from .vault import safe_name
    for p in jobs.pending():
        try:
            job = json.loads(p.read_text(encoding="utf-8"))
            if not job.get("live") or (job.get("kind") == "event") != event:
                continue
            if json.loads(Path(job["live"]).read_text(encoding="utf-8"))["folder"] == folder and \
                    safe_name(job["course"]).casefold() == safe_name(course).casefold():
                return p
        except (OSError, ValueError, KeyError):
            continue
    return None


def cmd_queue(args) -> None:
    if getattr(args, "scheduled", False):
        jobs.run_without_console()  # Windows' task starts pythonw.exe; does nothing anywhere else
    cfg = load_config()
    items = jobs.pending()
    lecture = getattr(args, "lecture", None)
    if lecture:  # "Make Final Transcript Now" in Stenny's window: this one, now, also on battery
        job = lecture_job(cfg, *lecture, event=args.event)
        if not job:
            sys.exit("This lecture has no final transcript waiting.")
        items = [job]
    if args.action == "list":
        for p in items:
            print(p.name)
        print(f"{len(items)} job(s) in {jobs.QUEUE_DIR}")
        return
    if not items:
        return
    if not lecture:
        return run_queue(cfg, args, items)
    # "being made" at once, also when started from a terminal (the app's finish_now marks it before starting this);
    # anything that stops the pass before it begins (another one running, the model not loading) makes it wait again
    mark_job_draft(cfg, items[0], "finalizing")
    try:
        run_queue(cfg, args, items, lecture=True)
    except BaseException:
        mark_job_draft(cfg, items[0], "queued")  # no-op once the job is done or failed (its file is gone then)
        raise


def run_queue(cfg, args, items: list[Path], lecture: bool = False) -> None:
    """cmd_queue's work: `items` (just the one lecture's job), or with `lecture` False every pending job."""
    if args.scheduled:
        if cfg.require_ac_for_queue and not jobs.on_ac_power():
            return
        if cfg.night_only and not jobs.in_window(cfg.night_start, cfg.night_end):
            return
    recover_sessions(cfg)
    jobs.apply_priority(cfg.priority if not args.scheduled else "background")
    with jobs.QueueLock() as lock:
        if lock is None:
            if lecture:
                sys.exit("Another final transcript is being made right now. Try again when it's done.")
            print("Another queue run is active.")
            return
        jobs.keep_awake()
        from .engines import get_engine
        from .pipeline import Interrupted, log

        engine = get_engine(cfg.model, cfg.engine)
        unplugged = jobs.unplugged_check() if args.scheduled and cfg.require_ac_for_queue else None
        for p in items if lecture else jobs.pending():
            if unplugged and unplugged():
                log("Unplugged; stopping, remaining jobs wait.")
                break
            if args.scheduled and not resources.enough_memory_for_final_pass():
                log("Not enough free memory now; remaining jobs wait.")
                break
            try:
                run_job(p, cfg, engine, stop=unplugged)
            except Interrupted as e:  # the Mac is on battery now: give it back, the job waits for power
                log(f"Unplugged; final pass {e}, it starts over on power. Remaining jobs wait.")
                break
            except Exception as e:  # keep the job, move on to the next one
                log(f"Job {p.name} failed: {e}")
                p.rename(p.with_suffix(".failed"))
                if lecture:
                    sys.exit("The final transcript couldn't be made. The recording is kept; please use Send Feedback.")


def draft_path(cfg, course, folder: str) -> Path | None:
    """A live recording's transcript file (its live draft until the final pass replaces it)."""
    from .vault import LectureFiles, Vault
    return LectureFiles(cfg.vault_for(course).course_dir(course.name) / folder, course.name).existing("transcript")


def mark_draft(path: Path | None, status: str, waiting: str | None = None) -> None:
    """Tells the reader of a live draft where its final transcript is (render.set_draft_state). Does nothing to a
    final transcript or a missing file, and never stops the work around it."""
    from .render import set_draft_state
    try:
        if not path or not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        new = set_draft_state(text, status, waiting)
        if new and new != text:
            tmp = path.with_name(f".{path.name}.tmp")
            tmp.write_text(new, encoding="utf-8")
            tmp.replace(path)
    except OSError:
        pass


def mark_job_draft(cfg, job_path: Path, status: str, waiting: str | None = None) -> None:
    """mark_draft for a queued job, if it is a live recording's."""
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
        if not job.get("live"):
            return
        folder = json.loads(Path(job["live"]).read_text(encoding="utf-8"))["folder"]
        course = cfg.event(job["course"]) if job.get("kind") == "event" else resolve_course(cfg, job["course"], job.get("lang"))
    except (OSError, ValueError, KeyError, SystemExit, ConfigError):
        return
    mark_draft(draft_path(cfg, course, folder), status, waiting)


def run_job(job_path: Path, cfg, engine, stop=None) -> dict:
    """Process one queued job; on success remove it (and a live recording's temporary .pcm).
    `stop`: see pipeline.transcribe(); an interrupted job stays queued."""
    from .pipeline import Interrupted, LiveInfo, process

    job = json.loads(job_path.read_text(encoding="utf-8"))
    course = cfg.event(job["course"]) if job.get("kind") == "event" else resolve_course(cfg, job["course"], job.get("lang"))
    course = course.with_language(job.get("lang"), cfg.event_languages)
    live = LiveInfo.load(Path(job["live"])) if job.get("live") else None
    src = Path(job["file"])
    slides = Path(job["slides"]) if job.get("slides") else None
    draft = draft_path(cfg, course, live.folder) if live else None
    mark_draft(draft, "finalizing")
    try:
        info = process(src, cfg, course, engine, lang=job.get("lang"), use_llm=job.get("llm", True), live=live,
                       slides_src=slides if slides and slides.exists() else None, stop=stop)
    except Interrupted:
        mark_draft(draft, "queued", "battery")
        raise
    except Exception:
        mark_draft(draft, "queued")
        raise
    job_path.unlink()
    if live and src.suffix == ".pcm":
        src.unlink(missing_ok=True)  # archived as .opus by process() (or keep_audio = false)
        if live.chunks:  # in the final transcript now
            live.chunks.unlink(missing_ok=True)
        if live.backup:  # archived by process() if it filled a gap, else not needed
            Path(live.backup["recording"]).unlink(missing_ok=True)
    return info


def cmd_live(args) -> None:
    cfg = load_config()
    course = recording_target(cfg, args)
    from .engines import get_engine
    from .live.control import ControlServer, is_running
    from .live.session import LiveSession
    from .live.sources import FileSource, audio_devices, make_backup, make_mic
    from .pipeline import log

    if is_running():
        sys.exit("A live session is already running. Use `stenny status` / `stenny stop`.")
    # The microphone comes first: nothing here asks the system about its devices (system_profiler takes a third
    # of a second or more, each time). The system's input device records as "default" and is named afterwards,
    # and the backup microphone joins a moment after the main one (after_start).
    source = FileSource(args.file, args.speed) if args.file else make_mic(args.device or cfg.live_device, cfg.live_recorder)

    def after_start(session) -> None:
        devices = audio_devices()
        session.name_microphone(devices["default"])
        if backup := make_backup(args.backup or cfg.live_backup, source, devices):
            session.attach_backup(backup)

    recover_sessions(cfg)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    if (free := resources.free_disk(cfg.data_dir)) < resources.DISK_TO_START:
        computer = "PC" if sys.platform == "win32" else "Mac"
        sys.exit(f"Only {free / resources.GB:.1f} GB free on this {computer}. A lecture needs about "
                 f"{resources.DISK_TO_START / resources.GB:.1f} GB, so free some space first.")
    engine = get_engine(cfg.model, cfg.engine)
    session = LiveSession(cfg, course, source, engine, lang=args.lang, draft=cfg.live_draft and not args.no_draft)
    server = ControlServer(session)
    jobs.keep_awake()  # on battery macOS idle-sleeps after a few minutes; closing the lid still sleeps
    try:
        log("Controls: `stenny pause` / `stenny resume` / `stenny toggle` / `stenny stop` (or Stenny, ⌘⇧Ü)")
        session.run(after_start=None if args.file else after_start)
        if session.cancelled:
            discard(session, cfg)
        else:
            finalize(session, cfg, args.final, not args.no_llm, engine)
    finally:
        server.close()


def stop_orphan_recorders(*recordings: Path) -> None:
    """Recorders still writing to these files although the session that started them is gone."""
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    for line in out.splitlines():
        pid, _, command = line.strip().partition(" ")
        if "stenny-rec" in command and any(f"--out {r} " in f"{command} " for r in recordings):
            subprocess.run(["kill", pid])
            for _ in range(30):
                if subprocess.run(["kill", "-0", pid], capture_output=True).returncode:
                    break
                time.sleep(0.1)


def recording_sessions(cfg) -> list[Path]:
    """Every recording's session.json: courses' and talks' (Recordings/<Course>/, Recordings/Talks/<Title>/)."""
    return [*cfg.recordings_dir.glob("*/*.session.json"), *(cfg.recordings_dir / TALKS_DIR).glob("*/*.session.json")]


def recover_sessions(cfg) -> list[dict]:
    """A recording whose process died (macOS killed it, a crash, a restart of the Mac) still has its audio
    file, but no final pass waiting. Queue one: the transcript comes later, the lecture isn't lost."""
    from .live.control import is_running
    from .pipeline import log

    if is_running():
        return []
    known = set()
    for job in [*jobs.QUEUE_DIR.glob("*.json"), *jobs.QUEUE_DIR.glob("*.failed")]:
        try:
            known.add(json.loads(job.read_text(encoding="utf-8"))["file"])
        except (OSError, ValueError, KeyError):
            continue
    recovered = []
    for path in recording_sessions(cfg):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            pcm = Path(d["recording"])
        except (OSError, ValueError, KeyError):
            continue
        if not pcm.exists() or str(pcm.resolve()) in known:
            continue
        backup = d.get("backup")
        stop_orphan_recorders(pcm, *([Path(backup["recording"])] if backup else []))
        size = pcm.stat().st_size // 2
        if not size:
            continue
        d.update(recorded_samples=size, state="stopped", pauses=[p for p in d["pauses"] if p[1] is not None])
        if backup and Path(backup["recording"]).exists():
            backup["recorded_samples"] = Path(backup["recording"]).stat().st_size // 2
        path.write_text(json.dumps(d, indent=1), encoding="utf-8")
        kind = d.get("kind") or ("event" if path.parent.parent == cfg.recordings_dir / TALKS_DIR else "lecture")
        job = jobs.enqueue(pcm, d["course"], d.get("lang"), True, live=path, kind=kind)
        mark_job_draft(cfg, job, "queued")
        log(f"Recovered the recording {pcm.name} of {d['course']}: final pass queued")
        recovered.append({"course": d["course"], "kind": kind})
    return recovered


def cmd_recover(args) -> None:
    with contextlib.redirect_stdout(sys.stderr):  # the log lines mustn't get into the JSON
        recovered = recover_sessions(load_config())
    if args.json:  # for Stenny
        print(json.dumps(recovered, ensure_ascii=False))


def discard(session, cfg) -> None:
    """A cancelled recording: its audio, session files and placeholder go to the Trash."""
    from .manage import TRASH_NAME, ManageError, discard_recording
    from .pipeline import log

    try:
        discard_recording(cfg, session.course, session.folder.name)
        log(f"Recording cancelled: {session.course.name} {session.folder.name} moved to the {TRASH_NAME}")
    except (ManageError, OSError) as e:
        log(f"Recording cancelled, but its files couldn't go to the {TRASH_NAME}: {e}")


def finalize(session, cfg, policy: str, use_llm: bool, engine) -> None:
    """After a live session: queue the full recording for the offline pipeline, and run it now
    unless we're on battery (policy auto) or told to wait (policy later)."""
    from .pipeline import log

    if session.recorded == 0:
        log("Nothing was recorded.")
        session.pcm_path.unlink(missing_ok=True)
        if session.backup:
            session.backup.path.unlink(missing_ok=True)
        return
    job = jobs.enqueue(session.pcm_path, session.course.name, session.lang_override, use_llm, live=session.session_path,
                       kind=session.course.kind)
    if hasattr(engine, "release"):  # the recording's model would count against the memory check
        engine.release()
    on_power = jobs.on_ac_power()
    run_now = policy == "now" or (policy == "auto" and on_power and resources.enough_memory_for_final_pass())
    if not run_now:
        why = "as requested" if policy != "auto" else "memory is low" if on_power else "on battery"
        session.final_note = session.final_note or tr(f"final pass queued ({why})")  # the app's menu shows it
        mark_draft(session.transcript_path, "queued", "battery" if policy == "auto" and not on_power else None)
        log(f"Final pass queued {why}: {job.name} — runs with `stenny queue run` "
            "or the schedule (`stenny schedule install`).")
        return
    from .pipeline import Interrupted

    with jobs.QueueLock() as lock:
        if lock is None:  # a scheduled run is busy; a later one picks this job up
            session.final_note = session.final_note or tr("final pass queued (another one is running)")
            mark_draft(session.transcript_path, "queued")
            log(f"Another final pass is running; this one waits in the queue: {job.name}")
            return
        session.state = "finalizing"
        jobs.lower_own_priority(cfg.priority)
        log("Final pass on the full recording (low priority) …")
        try:
            # "auto" ran because the Mac was plugged in, so it gives way when unplugged; "now" was asked for
            run_job(job, cfg, engine, stop=jobs.unplugged_check() if policy == "auto" else None)
        except Interrupted as e:
            session.state = "stopped"
            session.final_note = session.final_note or tr("final pass queued (unplugged)")
            log(f"Unplugged; final pass {e}. The job waits in the queue and goes on from there: {job.name}")
            return
    session.state = "done"


def cmd_check(args) -> None:
    """How complete is a live recording? Compares lecture time with the audio actually delivered."""
    cfg = load_config()
    from .pipeline import lecture_time, time_ranges
    from .textproc import SAMPLE_RATE, fmt_hms

    sessions = recording_sessions(cfg)
    path = args.session or max(sessions, key=lambda p: p.stat().st_mtime, default=None)
    if not path or not path.exists():
        sys.exit("No live session found.")
    d = json.loads(path.read_text(encoding="utf-8"))
    pcm = Path(d["recording"])
    samples = d.get("recorded_samples") or (pcm.stat().st_size // 2 if pcm.exists() else 0)
    opus = pcm.with_name(pcm.name.replace(".live.pcm", ".opus"))
    if not samples and opus.exists():  # after the final pass only the archived .opus remains
        probe = subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(opus)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        samples = int(float(probe.stdout or 0) * SAMPLE_RATE)
    spans = [tuple(s) for s in d["spans"]]
    end = lecture_time(spans, samples)
    gaps = [p for p in d["pauses"] if p[1] is not None]
    paused = sum(p[1] - p[0] for p in gaps if (p[2] if len(p) > 2 else "paused") == "paused")
    no_input = sum(p[1] - p[0] for p in gaps if len(p) > 2 and p[2] == "no input")
    jumps = [t - (pt + (s - ps) / SAMPLE_RATE) for (ps, pt), (s, t) in zip(spans, spans[1:])]
    small = [j for j in jumps if 0 < j <= 3.0]
    listened = max(end - paused - spans[0][1], 1e-9)
    print(f"{d['course']} · {d['folder']} · {d.get('microphone') or d.get('source')}"
          + ("" if d.get("live_draft", True) else " · record only"))
    print(f"lecture time {fmt_hms(end)}, recorded audio {fmt_hms(samples / SAMPLE_RATE)}, paused {fmt_hms(paused)}")
    print(f"marked gaps (phone locked / connection lost): {fmt_hms(no_input)}")
    print(f"audio the device silently didn't deliver: {sum(small):.1f} s in {len(small)} re-syncs "
          f"= {100 * sum(small) / listened:.1f} % of the listened time")
    if b := d.get("backup"):
        b_spans = [tuple(s) for s in b["spans"]]
        b_samples = b.get("recorded_samples", 0)
        no_input_gaps = [p for p in gaps if len(p) > 2 and p[2] == "no input"]
        covered = sum((b1 - a) / SAMPLE_RATE for g in no_input_gaps
                      for a, b1, _ in time_ranges(b_spans, b_samples, g[0], g[1]))
        print(f"backup {b['microphone']}: {fmt_hms(b_samples / SAMPLE_RATE)} recorded, covers {covered:.0f} s "
              f"of the {sum(g[1] - g[0] for g in no_input_gaps):.0f} s of gaps")
    rec_log = pcm.with_suffix(".rec.log")
    backup_log = pcm.with_name(pcm.name.replace(".live.pcm", ".backup.rec.log"))
    for log_path in (pcm.with_suffix(".ffmpeg.log"), rec_log, backup_log):
        if not log_path.exists():
            continue
        lines = [l for l in log_path.read_text(errors="replace").splitlines()
                 if l and not l.startswith("---") and "stenny-rec: recording" not in l and "stopped:" not in l]
        print(f"{'backup ' if log_path == backup_log else ''}{log_path.suffixes[-2][1:]} warnings: {len(lines)}" + (f" — last: {lines[-1][:150]}" if lines else ""))


def cmd_devices(args) -> None:
    from .live.sources import audio_devices, input_devices

    known = audio_devices()
    devices, default = input_devices(known), known["default"]
    if args.json:  # for Stenny
        print(json.dumps({"devices": devices, "default": default}, ensure_ascii=False))
        return
    for d in devices:
        print(f"- {d}" + ("  (system default)" if d == default else ""))


def cmd_control(args) -> None:
    from .live.control import NoSession, send

    try:
        reply = send(args.cmd, device=args.device) if args.cmd == "microphone" else send(args.cmd)
    except NoSession as e:
        if args.json:
            print(json.dumps({"ok": False, "state": "idle", "error": str(e)}))
            return
        sys.exit(str(e))
    if args.json:
        print(json.dumps(reply, ensure_ascii=False))
        return
    if not reply.get("ok"):
        sys.exit(reply.get("error", "error"))
    e = int(reply["elapsed_s"])
    level = "no audio – reconnecting" if reply.get("input_lost") else f"level {reply.get('level_db', -120):.0f} dB"
    print(f"{reply['state']} · {reply['course']} · {e // 3600}:{e % 3600 // 60:02d}:{e % 60:02d} · "
          f"{reply['words']} words · paused {int(reply['paused_s'])} s · {level} · {reply['source']}")
    print(reply["transcript"])


def cmd_schedule(args) -> None:
    if sys.platform == "win32":
        schedule_task(args.action)
        return
    if args.action == "install":
        path = jobs.install_agent()
        print(f"Installed {path}: checks the queue every 15 min (runs only on AC power"
              f"{' and at night' if load_config().night_only else ''}). Log: {jobs.LOGS_DIR / 'queue.log'}")
    else:
        print("Removed." if jobs.uninstall_agent() else "No schedule installed.")


def schedule_task(action: str) -> None:
    """`stenny schedule` on Windows: the task in Task Scheduler (jobs.install_task)."""
    try:
        if action == "uninstall":
            print("Removed." if jobs.uninstall_agent() else "No schedule installed.")
            return
        cfg = load_config()
        only = [w for w, on in (("on AC power", cfg.require_ac_for_queue), ("at night", cfg.night_only)) if on]
        task = jobs.install_agent()
        print(f'Added "{task}" to Task Scheduler: checks the queue every {jobs.QUEUE_INTERVAL_S // 60} min'
              f"{' (runs only ' + ' and '.join(only) + ')' if only else ''}. Log: {jobs.LOGS_DIR / 'queue.log'}")
    except OSError as e:
        sys.exit(f"Task Scheduler: {e}")


def cmd_update(args) -> None:
    from .live.control import is_running
    from . import update

    try:
        if args.check:
            print(json.dumps(update.check()))
            return
        update.supported()
        if is_running():
            sys.exit("A recording or its final transcript is running. Update when it's finished.")
        result = update.check()
        if not result["update_available"]:
            print(f"Stenny {result['current']} is the newest version.")
            return
        backup = update.update()
    except update.UpdateError as e:
        sys.exit(str(e))
    print(f"Updated to Stenny {result['latest']}. The old folder is kept at {backup}.")


def cmd_watch(args) -> None:
    """v0 workflow: watch a folder (default iCloud Drive/Lectures) and queue/process new recordings."""
    cfg = load_config()
    from . import audio as audio_io
    from .pipeline import log

    seen_file = paths.DATA_DIR / "watch-seen.txt"
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    seen = set(seen_file.read_text().splitlines()) if seen_file.exists() else set()
    sizes: dict[Path, int] = {}
    log(f"Watching {args.inbox} (Ctrl+C to stop)")
    while True:
        for p in sorted(args.inbox.iterdir()) if args.inbox.exists() else []:
            if p.name.startswith(".") and p.name.endswith(".icloud"):
                subprocess.run(["brctl", "download", str(p)], capture_output=True)  # fetch iCloud placeholder
                continue
            if p.suffix.lower() not in audio_io.AUDIO_EXTS or str(p) in seen:
                continue
            size = p.stat().st_size
            if sizes.get(p) != size:  # wait until the file stopped growing (sync/copy finished)
                sizes[p] = size
                continue
            course = next((c for c in cfg.courses.values()
                           if any(n.casefold() in p.stem.casefold() for n in [c.name, *c.aliases])), None)
            if course is None and not args.course:
                log(f"Skipping {p.name}: no course name/alias in the file name and no --course given")
            else:
                job = jobs.enqueue(p, course.name if course else args.course, None, True)
                log(f"Queued {p.name} → {job.name}")
            seen.add(str(p))
            seen_file.write_text("\n".join(sorted(seen)))
        time.sleep(args.interval)


def main() -> None:
    ap = argparse.ArgumentParser(prog="stenny", description="Transcribe lectures locally into Markdown files (your Stenny folder).")
    ap.add_argument("--version", action="version", version=f"stenny {package_version('stenny')}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="transcribe audio file(s) into the Stenny folder")
    p.add_argument("files", nargs="+", type=Path)
    t = p.add_mutually_exclusive_group(required=True)
    t.add_argument("--course", "-c", help="course name or alias from the config")
    t.add_argument("--event", "-e", metavar="TITLE", help="a talk or event (not a course): its own folder in "
                                                           "Talks, e.g. --event \"Guest talk Müller\"")
    p.add_argument("--lang", "-l", help="language for this file, overriding the course setting: a Whisper code (e.g. fr), "
                   "auto (one language, detected) or mixed (detected throughout)")
    p.add_argument("--date", help="lecture date/time if the file's own timestamp is wrong (YYYY-MM-DD[THH:MM])")
    p.add_argument("--no-llm", action="store_true", help="skip AI structuring and summary (fully offline)")
    p.add_argument("--no-summary", action="store_true", help="structure with AI but don't write the summary")
    p.add_argument("--slides", type=Path, help=argparse.SUPPRESS)  # dormant: Stenny doesn't use slides any more
    g = p.add_mutually_exclusive_group()
    g.add_argument("--now", action="store_true", help="full speed (normal priority)")
    g.add_argument("--later", action="store_true", help="queue it; runs on AC power / at night")
    g.add_argument("--priority", choices=list(jobs.PRIORITY_PREFIX), help="override the configured priority")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("refresh", help="redo only the AI step (structure, term fixes, summary) of a lecture, "
                                       "e.g. after editing the glossary")
    p.add_argument("course", help="course name or alias (or a talk's title, with --event)")
    p.add_argument("--event", action="store_true", help="COURSE is the title of a talk or event")
    p.add_argument("date", help="lecture folder name, e.g. 2026-10-15 or \"2026-10-15 (2)\"")
    p.add_argument("--slides", type=Path, help=argparse.SUPPRESS)  # dormant, as for add
    p.add_argument("--no-llm", action="store_true", help="rebuild the transcript without AI (time sections)")
    p.add_argument("--no-summary", action="store_true", help="don't rewrite the summary")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("project-instructions", help="text to paste into each course's Claude project instructions, "
                       "so its chats read the transcripts (Filesystem connector) and keep the glossary up to date")
    p.add_argument("--course", help="only this course (default: all)")
    p.set_defaults(func=cmd_project_instructions)

    p = sub.add_parser("mcp", help="the connector for Claude: lets Claude Desktop read your lectures, summaries, "
                       "and glossaries (a local MCP server on stdin/stdout)")
    p.add_argument("--desktop-config", action="store_true",
                   help="print the entry for Claude Desktop's claude_desktop_config.json instead of serving")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("api-key", help="save the Anthropic API key for the AI step in the system keychain")
    p.add_argument("action", nargs="?", choices=["set", "remove", "status"], default="set",
                   help="set (default, asks for the key), remove, or status (checks it, costs nothing)")
    p.set_defaults(func=cmd_api_key)

    p = sub.add_parser("match-course", help="which course a calendar event title belongs to")
    p.add_argument("titles", nargs="+")
    p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_match_course)

    p = sub.add_parser("course-files", help="rebuild each course's overview in _Course Files/, the lectures' "
                                         "previous/next links and the topic notes")
    p.add_argument("--course", help="only this course (default: all configured courses)")
    p.set_defaults(func=cmd_course_files)

    p = sub.add_parser("app-server", help=argparse.SUPPRESS)  # Stenny's app talks to the engine through this
    p.set_defaults(func=lambda args: __import__("stenny.app_server", fromlist=["serve"]).serve())

    p = sub.add_parser("library", help="list every lecture and talk with a transcript")
    p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_library)

    p = sub.add_parser("share", help="clean copy of a transcript to send to someone (in Stenny's cache folder, see `stenny where`)")
    p.add_argument("course", help="course name (or a talk's title, with --event)")
    p.add_argument("date", help="lecture folder name, e.g. 2026-10-15 or \"2026-10-15 (2)\"")
    p.add_argument("--event", action="store_true", help="COURSE is the title of a talk or event")
    p.add_argument("--with-summary", action="store_true", help="put the summary at the top")
    p.add_argument("--format", choices=["md", "docx", "html"], default="md",
                   help="Markdown (default), Word or HTML; Stenny's window also makes PDFs")
    p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_share)

    p = sub.add_parser("courses", help="list configured courses")
    p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_courses)

    p = sub.add_parser("live", help="record a lecture or talk live into the Stenny folder (draft now, final transcript after)")
    t = p.add_mutually_exclusive_group(required=True)
    t.add_argument("--course", "-c", help="course name or alias from the config")
    t.add_argument("--event", "-e", metavar="TITLE", help="a talk or event (not a course), e.g. --event \"Guest talk\"")
    p.add_argument("--lang", "-l", help="a Whisper language code (e.g. fr), auto or mixed")
    p.add_argument("--device", help="microphone name (default: the system's input device)")
    p.add_argument("--file", type=Path, help="play this audio file instead of the microphone (for testing)")
    p.add_argument("--speed", type=float, default=1.0, help="playback speed for --file")
    p.add_argument("--final", choices=["auto", "now", "later"], default="auto",
                   help="final pass after stop: auto = now on AC power, queued on battery")
    p.add_argument("--no-llm", action="store_true", help="final pass without AI structuring/summary")
    p.add_argument("--no-draft", action="store_true", help="record only: no transcription during the recording, even when plugged in")
    p.add_argument("--backup", metavar="DEVICE", help="second microphone that fills the main one's gaps in the final "
                   "pass: a device name, 'auto' (the Mac's own) or 'off' (default: [live] backup in the config)")
    p.set_defaults(func=cmd_live)

    p = sub.add_parser("check", help="health check of a live recording: missing audio, gaps, device warnings")
    p.add_argument("session", nargs="?", type=Path, help="session.json (default: the most recent one)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("devices", help="list microphones usable for live mode")
    p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_devices)

    for name, text in [("status", "show the running live session"), ("pause", "pause recording (mic off)"),
                       ("resume", "resume recording"), ("toggle", "pause or resume"),
                       ("stop", "stop recording and run the final pass")]:
        p = sub.add_parser(name, help=text)
        p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
        p.set_defaults(func=cmd_control, cmd=name)
    p = sub.add_parser("mic", help="switch the running recording to another microphone (see `stenny devices`)")
    p.add_argument("device", help="microphone name, e.g. \"MacBook Air Microphone\"")
    p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_control, cmd="microphone")

    p = sub.add_parser("init", help="create Stenny's config.toml from the example (see `stenny where`)")
    p.add_argument("--folder", help="the Stenny folder, where transcripts go (default: ~/Documents/Stenny); "
                   "a folder in an Obsidian vault works too")
    p.add_argument("--vault", help=argparse.SUPPRESS)          # before `--folder`: --vault V --lectures-dir L
    p.add_argument("--lectures-dir", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("where", help="where Stenny keeps your transcripts, its config, recordings and logs")
    p.add_argument("--json", action="store_true", help="as JSON (for the app and the connector)")
    p.set_defaults(func=cmd_where)

    p = sub.add_parser("download-model", help="download the Whisper model now (about 1.6 GB, once)")
    p.set_defaults(func=cmd_download_model)

    p = sub.add_parser("app-settings", help="settings the Stenny app reads at launch (JSON)")
    p.set_defaults(func=cmd_app_settings)

    p = sub.add_parser("migrate", help="move an older install (~/Lectures Data) into today's places (once; the app does it too)")
    p.add_argument("--dry-run", action="store_true", help="only list what would be moved")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("queue", help="list or process queued jobs")
    p.add_argument("action", choices=["list", "run"])
    p.add_argument("--scheduled", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--lecture", nargs=2, metavar=("COURSE", "FOLDER"),
                   help="run only this live recording's final pass, now (also on battery)")
    p.add_argument("--event", action="store_true", help="--lecture names a talk")
    p.set_defaults(func=cmd_queue)

    scheduler = "Task Scheduler task" if sys.platform == "win32" else "launchd agent"
    p = sub.add_parser("schedule", help=f"install/remove the {scheduler} that runs the queue")
    p.add_argument("action", choices=["install", "uninstall"])
    p.set_defaults(func=cmd_schedule)

    p = sub.add_parser("course", help="add a course, or hide one from the menu and calendar matching")
    actions = p.add_subparsers(dest="action", required=True)
    a = actions.add_parser("add", help="add a course (or bring a hidden one back)")
    a.add_argument("name")
    a.add_argument("--language", "-l", default="auto", help="a code (en, de, fr, …), mixed or auto")
    a.add_argument("--languages", help="for mixed or auto: the languages to detect among, e.g. fr,de")
    a.add_argument("--alias", help="a short name for the terminal and calendar matching")
    h = actions.add_parser("hide", help="hide a course; its transcripts, glossary and settings stay")
    h.add_argument("name")
    p.set_defaults(func=cmd_course)

    p = sub.add_parser("recover", help="queue the final pass of recordings whose process died")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_recover)

    p = sub.add_parser("update", help="install the newest release of Stenny (your courses and transcripts stay)")
    p.add_argument("--check", action="store_true", help="only print whether a newer version exists, as JSON")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("watch", help="watch a folder (default: iCloud Drive/Lectures) and queue new recordings")
    p.add_argument("--inbox", type=Path, default=ICLOUD_INBOX)
    p.add_argument("--course", help="course for files whose name doesn't contain a course name/alias")
    p.add_argument("--interval", type=int, default=30)
    p.set_defaults(func=cmd_watch)

    args = ap.parse_args()
    args.func(args)
