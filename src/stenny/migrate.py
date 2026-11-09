"""`stenny migrate`: move an older install into the layout of paths.py and vault.py, once. Safe to run again (it
skips what's done) and with --dry-run (only lists what it would do). Refuses while a recording or a final pass runs.
Stenny's app runs it at start (`on_start`), so nobody has to.

From `~/Lectures Data/` (the layout until 0.4):
1. course data:  ~/Lectures Data/<Course>/, _Recordings/<Title>/  → <data>/Recordings/<Course>/, Recordings/Talks/<Title>/
2. talks:        <vault>/Recordings/<Title>/ ([recordings] folder) → <Stenny folder>/Talks/<Title>/
3. paths inside segments.json, session files, queue jobs, raw outputs and transcripts follow (1) and (2)
4. queue:        _Queue/   → <data>/Queue/
5. config:       _System/config.toml → the config place; `vault` + `lectures_dir` become `folder` (edited as text,
                 comments stay); `data_dir` stays only if it pointed elsewhere (then Recordings/ goes inside it)
6. the rest of _System/: consent.json → the config place, logs → the logs place, the calendar titles → data;
                 _Shared/ → the cache place
7. the queue's launchd agent writes its log to the new place
8. ~/Lectures Data is deleted once it's empty. Anything Stenny doesn't know stays there, and it says so.
On the same disk every move is a rename; across disks it's copy, check, then delete.

And at every start, whatever the layout (`drop_course_copies`): the `(all lectures)` and `(summaries)` copies that
earlier versions wrote into `_Course Files/` go to the Trash (the Recycle Bin on Windows).

And from the layout before that (~/.config/lecture/, ~/Lectures Audio/, ~/.local/share/lecture/, raw output and
glossaries named differently in the vault), first into ~/Lectures Data/ as it was, then on as above.
"""

import errno
import json
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path

from . import courses, jobs, paths
from .config import EXAMPLE, ConfigError, folder_of, load
from .live import control
from .settings import _table, set_line
from .render import parse_frontmatter
from .vault import COURSE_FILES_DIR, GLOSSARY_DIR, TALKS_DIR, LectureFiles, Vault, safe_name


class MigrateError(Exception):
    pass


def _tilde(path: Path) -> str:
    return str(path).replace(str(Path.home()), "~", 1)


def _rewrite(text: str, old: Path, new: Path) -> str:
    """Replace the old folder in paths (absolute and "~/" forms; the trailing "/" keeps "… (Test)" apart)."""
    for a, b in ((f"{old}/", f"{new}/"), (f"{_tilde(old)}/", f"{_tilde(new)}/")):
        text = text.replace(a, b)
    return text


def _expand(value: str) -> Path:
    return Path(os.path.expanduser(value))


def _visible(d: Path) -> list[Path]:
    return [p for p in d.iterdir() if p.name != ".DS_Store"] if d.is_dir() else []


class Mover:
    """Counts and logs every step; does nothing on a dry run."""

    def __init__(self, dry_run: bool, log):
        self.dry_run, self.log, self.actions, self.kept = dry_run, log, 0, []

    def act(self, message: str, fn=None) -> None:
        self.actions += 1
        self.log(("would: " if self.dry_run else "") + message)
        if fn and not self.dry_run:
            fn()

    def move(self, src: Path, dst: Path) -> None:
        """A file or folder to its new place; a folder that's already there gets what it doesn't have yet."""
        if dst.is_dir() and src.is_dir():
            for child in sorted(_visible(src)):
                self.move(child, dst / child.name)
            self.rmdir_empty(src)
        elif dst.exists():
            self.log(f"skipped, already there: {_tilde(dst)}")
            self.kept.append(src)
        else:
            self.act(f"move {_tilde(src)} → {_tilde(dst)}", lambda: _move(src, dst))

    def rmdir_empty(self, d: Path) -> None:
        if d.is_dir() and not _visible(d):
            self.act(f"remove empty folder {_tilde(d)}", lambda: shutil.rmtree(d))


def _files(p: Path) -> dict[str, int]:
    if p.is_file():
        return {"": p.stat().st_size}
    return {str(f.relative_to(p)): f.stat().st_size for f in p.rglob("*") if f.is_file()}


def _move(src: Path, dst: Path) -> None:
    """A rename on the same disk; otherwise copy, check every file arrived whole, then delete the original."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dst)
        return
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    if src.is_dir():
        shutil.copytree(src, dst, copy_function=shutil.copy2)
    else:
        shutil.copy2(src, dst)
    if _files(src) != _files(dst):
        shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
        raise MigrateError(f"Copying {src} to {dst} didn't work out; the original is untouched.")
    shutil.rmtree(src) if src.is_dir() else src.unlink()


class Paths:
    """Old folder → new folder, for paths inside files (absolute and "~/…"), all in one pass."""

    def __init__(self, moves: list[tuple[Path, Path]]):
        pairs = {}
        for old, new in moves:
            for a, b in ((str(old), str(new)), (_tilde(old), _tilde(new))):
                pairs[a.rstrip("/") + "/"] = b.rstrip("/") + "/"
        self.pairs = pairs
        alternatives = sorted(pairs, key=len, reverse=True)
        self.pattern = re.compile("|".join(map(re.escape, alternatives))) if alternatives else None

    def text(self, text: str) -> str:
        def swap(m):
            new = self.pairs[m.group(0)]
            # already the new path (when it is inside the old one, e.g. <data_dir>/Recordings/…): leave it
            return m.group(0) if m.string.startswith(new, m.start()) else new
        return self.pattern.sub(swap, text) if self.pattern else text

    def json(self, text: str) -> str:
        try:
            data = json.loads(text)
        except ValueError:
            return self.text(text)

        def walk(v):
            if isinstance(v, dict):
                return {k: walk(x) for k, x in v.items()}
            if isinstance(v, list):
                return [walk(x) for x in v]
            return self.text(v) if isinstance(v, str) else v
        new = walk(data)
        return text if new == data else json.dumps(new, ensure_ascii=False, indent=1)

    def file(self, path: Path, vault: Vault | None = None) -> bool:
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        new = self.json(text) if path.suffix in (".json", ".failed") else self.text(text)
        if new == text:
            return False
        if vault:
            vault.write(path, new)
        else:
            tmp = path.with_name(f".{path.name}.tmp")
            tmp.write_text(new, encoding="utf-8", errors="surrogateescape")
            os.replace(tmp, path)
        return True


# ---------------------------------------------------------------- checks

def _legacy_lock_held() -> bool:
    """A final pass of the version before holds ~/Lectures Data/_System/queue.lock while it runs."""
    lock = paths.LEGACY_SYSTEM_DIR / "queue.lock"
    if sys.platform == "win32" or not lock.exists():
        return False
    import fcntl
    with open(lock, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
    return False


def busy() -> str | None:
    """Why it can't move anything now: a recording or a final pass is running."""
    sockets = [paths.SOCKET, paths.LEGACY_SYSTEM_DIR / "live.sock", paths.LEGACY_STATE_DIR / "live.sock"]
    if any(control.is_running(s) for s in sockets):
        return "A recording is running. Stop it (and let its final pass finish) first."
    if _legacy_lock_held():
        return "A final pass is running. Wait until it's done."
    return None


def needed() -> bool:
    """Is there anything of an older layout left? (Quick: only looks whether its folders are there.)"""
    return any(p.exists() for p in (paths.LEGACY_HOME, paths.LEGACY_CONFIG_DIR, paths.LEGACY_AUDIO_DIR,
                                     paths.LEGACY_STATE_DIR))


# ---------------------------------------------------------------- the layout before ~/Lectures Data

def _earliest(m: Mover) -> None:
    """~/.config/lecture, ~/Lectures Audio, ~/.local/share/lecture and the vault's raw outputs and glossary names →
    ~/Lectures Data as it was (the next step takes it on from there)."""
    home, system = paths.LEGACY_HOME, paths.LEGACY_SYSTEM_DIR
    old_audio = paths.LEGACY_AUDIO_DIR

    def move_rewriting(src: Path, dst: Path, old: Path, new: Path) -> None:
        if dst.exists():
            m.log(f"skipped, already there: {dst}")
            return

        def do():
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.suffix in (".json", ".toml"):
                dst.write_text(_rewrite(src.read_text(encoding="utf-8"), old, new), encoding="utf-8")
                src.unlink()
            else:
                shutil.move(src, dst)
        m.act(f"move {_tilde(src)} → {_tilde(dst)}", do)

    # config (first: everything else reads it)
    if paths.LEGACY_CONFIG_DIR.is_dir():
        for f in sorted(paths.LEGACY_CONFIG_DIR.glob("*.toml")):
            dst = system / f.name
            if dst.exists() or paths.CONFIG_PATH.exists():
                m.log(f"skipped, already there: {dst}")
                continue
            text = f.read_text(encoding="utf-8")
            found = re.search(r'(?m)^audio_dir\s*=\s*"([^"]*)"\s*$', text)
            if found and _expand(found.group(1)) == old_audio:
                text = text.replace(found.group(0), "# audio_dir: no longer needed (paths.py in the project)")

            def write(f=f, dst=dst, text=text):
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(text, encoding="utf-8")
                f.unlink()
            m.act(f"move {_tilde(f)} → {_tilde(dst)}", write)
        m.rmdir_empty(paths.LEGACY_CONFIG_DIR)
    config = next((p for p in (system / "config.toml", paths.CONFIG_PATH) if p.exists()), None)
    if config is None:
        return
    try:
        cfg = load(config)
    except ConfigError as e:
        m.log(f"skipped the vault: {e}")
        return
    raw = tomllib.loads(config.read_text(encoding="utf-8"))
    data = cfg.data_dir if raw.get("data_dir") else home if config.parent == system else cfg.recordings_dir

    # course data
    if old_audio.is_dir() and old_audio != data:
        for course_dir in sorted(p for p in old_audio.iterdir() if p.is_dir()):
            for f in sorted(p for p in course_dir.iterdir() if p.is_file() and p.name != ".DS_Store"):
                move_rewriting(f, data / course_dir.name / f.name, old_audio, data)
            m.rmdir_empty(course_dir)
        m.rmdir_empty(old_audio)

    # queue and logs
    state = paths.LEGACY_STATE_DIR
    if state.is_dir():
        for f in sorted((state / "queue").glob("*.json")) if (state / "queue").is_dir() else []:
            move_rewriting(f, home / "_Queue" / f.name, old_audio, data)
        m.rmdir_empty(state / "queue")
        for f in sorted(p for p in state.iterdir() if not p.is_dir()):  # also the socket
            if f.name in ("live.sock", "queue.lock", ".DS_Store"):
                m.act(f"remove stale {_tilde(f)}", f.unlink)
            else:
                move_rewriting(f, system / f.name, old_audio, data)
        m.rmdir_empty(state)

    # raw output out of the vault; glossaries renamed
    vault = cfg.vault_for()
    names = {safe_name(n): n for n in cfg.courses}
    for course_dir in vault.course_dirs():
        course = names.get(course_dir.name, course_dir.name)
        for folder in vault.lectures(course):
            files = LectureFiles(folder, course)
            raw_file = files.existing("raw")
            if not raw_file:
                continue
            dst = data / course / f"{safe_name(course)} {folder.name} (raw).md"
            transcript = files.existing("transcript")

            def out(raw_file=raw_file, dst=dst, transcript=transcript, folder=folder, course=course):
                vault.move_out(raw_file, dst)
                text = transcript.read_text(encoding="utf-8")
                new = text.replace(f"[[{raw_file.stem}]]", f"`{_tilde(dst)}` (outside the vault)")
                vault.write(transcript, _rewrite(new, old_audio, data))
                meta = data / course / f"{folder.name}.segments.json"
                if meta.exists():
                    d = json.loads(meta.read_text(encoding="utf-8"))
                    d["meta"].setdefault("lecture", {})["raw_file"] = _tilde(dst)
                    meta.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            m.act(f"move {_tilde(raw_file)} → {_tilde(dst)}", out)
    gdir = vault.root / GLOSSARY_DIR
    for g in sorted(gdir.glob("*.md")) if gdir.is_dir() else []:
        if g.stem.endswith(" (glossary)") or (gdir / f"{g.stem} (glossary).md").exists():
            continue
        m.act(f"rename {g.name} → {g.stem} (glossary).md", lambda g=g: vault.rename(g, gdir / f"{g.stem} (glossary).md"))


# ---------------------------------------------------------------- ~/Lectures Data → the app-data places

SYSTEM_FILES = {"consent.json": "config", "calendar-titles.json": "data", "watch-seen.txt": "data"}
STALE = {"live.sock", "queue.lock", "config.check.toml", "config.tmp"}


# what the migrations before wrote in place of `audio_dir` ("# audio_dir: no longer needed, everything outside the
# vault is in ~/Lectures Data (…)", later "# audio_dir: no longer needed (paths.py in the project)"): Stenny's own line
AUDIO_DIR_NOTE = re.compile(r"(?m)^[ \t]*# audio_dir: no longer needed\b.*(?:\r?\n|\Z)")


def convert_config(text: str, folder: Path, drop_data_dir: bool) -> str:
    """`vault` + `lectures_dir` → `folder` (where `vault` was), `[recordings] folder` out (talks are in the folder
    now), and `data_dir` out if it only named ~/Lectures Data. Everything else, comments too, stays, except the
    note an earlier migration left where `audio_dir` was: it named ~/Lectures Data, which is gone now."""
    value = json.dumps(paths.tilde(folder), ensure_ascii=False)
    line = f"folder = {value}  # everything you or Claude read: courses, Talks, glossaries; Stenny writes nowhere else"
    start, end = _table(text, None)
    top = text[start:end]
    vault = re.compile(r"(?m)^[ \t]*vault[ \t]*=.*$").search(top)
    if vault:
        top = top[:vault.start()] + line + top[vault.end():]
    elif not re.search(r"(?m)^[ \t]*folder[ \t]*=", top):
        top = line + "\n" + top
    text = text[:start] + top + text[end:]
    for key in ("lectures_dir", *(("data_dir", "audio_dir") if drop_data_dir else ())):
        text = set_line(text, _table(text, None), None, key, None)
    text = AUDIO_DIR_NOTE.sub("", text)
    if _table(text, "recordings") is not None:
        text = set_line(text, _table(text, "recordings"), "recordings", "folder", None)
    return text


def _from_lectures_data(m: Mover) -> None:
    home, system = paths.LEGACY_HOME, paths.LEGACY_SYSTEM_DIR
    legacy_config = system / "config.toml"
    explicit_config = os.environ.get("STENNY_CONFIG")
    if explicit_config and Path(explicit_config).expanduser().exists():
        source = Path(explicit_config).expanduser()
    else:
        source = next((p for p in (legacy_config, paths.CONFIG_PATH) if p.exists()), None)
    raw = tomllib.loads(source.read_text(encoding="utf-8")) if source else {}
    folder_value = folder_of(raw)
    folder = _expand(folder_value) if folder_value and "CHANGE-ME" not in folder_value else None
    explicit = raw.get("data_dir") or raw.get("audio_dir")
    old_data = _expand(explicit) if explicit else home
    keep_data_dir = bool(explicit) and old_data.resolve() != home.resolve()
    data = old_data if keep_data_dir else paths.DATA_DIR
    recordings = data / paths.RECORDINGS
    old_talks = None
    if raw.get("vault") and folder:
        set_folder = raw.get("recordings", {}).get("folder")
        lectures_dir = raw.get("lectures_dir", "Lectures")
        old_talks = _expand(raw["vault"]) / (set_folder or str(Path(lectures_dir).parent / "Recordings"))
    new_talks = folder / TALKS_DIR if folder else None

    moves = [(old_data / "_Recordings", recordings / TALKS_DIR), (home / "_Queue", paths.QUEUE_DIR)]
    if old_talks and new_talks and old_talks != new_talks:
        moves.append((old_talks, new_talks))
    moves.append((old_data, recordings))  # the courses' folders (the longer ones above go first)
    rewrite = Paths(moves)
    before = m.actions

    # 1. course data and talks' data
    if old_data.is_dir():
        for d in sorted(p for p in _visible(old_data) if p.is_dir()):
            if d.name == "_Recordings":
                for talk in sorted(p for p in _visible(d) if p.is_dir()):
                    m.move(talk, recordings / TALKS_DIR / talk.name)
                m.rmdir_empty(d)
            elif not d.name.startswith("_") and not (keep_data_dir and d.name == paths.RECORDINGS):
                m.move(d, recordings / d.name)

    # 2. talks into the Stenny folder (only folders that hold Stenny's talks)
    if old_talks and new_talks and old_talks.is_dir() and old_talks != new_talks:
        talks_vault = Vault(new_talks, top=folder)
        for talk in sorted(p for p in _visible(old_talks) if p.is_dir()):
            if Vault(old_talks).lectures(talk.name):
                if not m.dry_run:
                    talks_vault.ensure_dir(new_talks)
                m.move(talk, new_talks / talk.name)
            else:
                m.kept.append(talk)
        m.rmdir_empty(old_talks)

    # 4. queue (before 3: its jobs are rewritten there)
    if (home / "_Queue").is_dir():
        for f in sorted(_visible(home / "_Queue")):
            if f.name in STALE:
                m.act(f"remove stale {_tilde(f)}", f.unlink)
            else:
                m.move(f, paths.QUEUE_DIR / f.name)
        m.rmdir_empty(home / "_Queue")

    # 3. paths inside the files that moved, and in the transcripts that point at them
    def fix_paths():
        for base, vault in ((recordings, None), (paths.QUEUE_DIR, None), (folder, Vault(folder) if folder else None)):
            if not base or not base.is_dir():
                continue
            for f in base.rglob("*"):
                if f.is_file() and f.suffix in (".json", ".failed", ".md") and not f.name.startswith("."):
                    rewrite.file(f, vault if vault and f.suffix == ".md" else None)
    if m.actions > before:
        m.act("rewrite the paths inside segments, sessions, queue jobs and transcripts", fix_paths)

    # 5. config
    if source is not None and folder is not None and (source == legacy_config or raw.get("vault")):
        dst = source if source != legacy_config else paths.CONFIG_PATH
        if dst != source and dst.exists():
            m.log(f"skipped, already there: {_tilde(dst)}")
            m.kept.append(source)
        else:
            text = convert_config(source.read_text(encoding="utf-8"), folder, drop_data_dir=not keep_data_dir)

            def write(source=source, dst=dst, text=text):
                dst.parent.mkdir(parents=True, exist_ok=True)
                courses._write(dst, text)
                if dst != source:
                    source.unlink()
            m.act(f"move {_tilde(source)} → {_tilde(dst)} (vault + lectures_dir → folder)", write)

    # 6. the rest of _System and _Shared
    places = {"config": paths.CONFIG_DIR, "data": paths.DATA_DIR}
    if system.is_dir():
        for f in sorted(_visible(system)):
            if f.name == "config.toml":
                continue
            if f.name in STALE:
                m.act(f"remove stale {_tilde(f)}", f.unlink)
            elif f.name in SYSTEM_FILES:
                m.move(f, places[SYSTEM_FILES[f.name]] / f.name)
            elif f.suffix == ".log" and f.is_file():
                m.move(f, paths.LOGS_DIR / f.name)
            else:
                m.kept.append(f)
        m.rmdir_empty(system)
    if (home / "_Shared").is_dir():
        m.move(home / "_Shared", paths.SHARED_DIR)

    # 7. the queue's background job logs to the new place
    if jobs.AGENT_PATH.exists() and sys.platform == "darwin" and not os.environ.get("STENNY_HOME"):  # not a test copy
        import plistlib
        try:
            plist = plistlib.loads(jobs.AGENT_PATH.read_bytes())
        except (OSError, ValueError, plistlib.InvalidFileException):
            plist = {}
        if str(plist.get("StandardOutPath", "")).startswith(str(home)):
            exe = (plist.get("ProgramArguments") or [None])[0]
            m.act("point the queue's background job at the new log place", lambda: jobs.install_agent(exe=exe))

    # 8. ~/Lectures Data, once it's empty
    if home.is_dir():
        for d in sorted(p for p in _visible(home) if p.is_dir()):
            m.rmdir_empty(d)
        m.rmdir_empty(home)
        if not m.dry_run:
            m.kept += [p for p in _visible(home) if p != system]
    if keep_data_dir:
        for d in sorted(p for p in _visible(old_data) if p.is_dir() and p.name == "_Recordings"):
            m.rmdir_empty(d)


# ---------------------------------------------------------------- the per-course copies

COPIES = ("all lectures", "summaries")  # `content` of the copies earlier versions wrote; the overview stays


def course_copies(folder: Path) -> list[Path]:
    """`_Course Files/<Course> (all lectures).md` and `(summaries).md`: every transcript or summary of a course a
    second time. Only files Stenny generated (frontmatter `kind: course-file` with one of COPIES as `content`),
    never one the user made or renamed that way."""
    found = []
    for kind in COPIES:
        for f in sorted((folder / COURSE_FILES_DIR).glob(f"* ({kind}).md")):
            try:
                fm = parse_frontmatter(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if f.is_file() and fm.get("kind") == "course-file" and fm.get("content") == kind:
                found.append(f)
    return found


def drop_course_copies(dry_run: bool = False, log=print) -> int:
    """The copies to the Trash (the Recycle Bin on Windows): each lecture's own transcript and summary are the
    one place of its text. Quick when there are none, so it runs at every start."""
    from .config import config_path
    try:
        folder = load(config_path()).folder if config_path().exists() else None
    except (ConfigError, OSError, ValueError):
        folder = None
    copies = course_copies(folder) if folder and (folder / COURSE_FILES_DIR).is_dir() else []
    if not copies:
        return 0
    bin_name = "Recycle Bin" if sys.platform == "win32" else "Trash"
    for f in copies:
        log(f"{'would: ' if dry_run else ''}move {_tilde(f)} to the {bin_name} (a copy of the course's transcripts or "
            "summaries; each lecture keeps its own)")
    if not dry_run:
        from .manage import trash
        trash(copies)
    return len(copies)


def migrate(dry_run: bool = False, log=print) -> int:
    if reason := busy():
        if not dry_run:
            raise MigrateError(reason)
        log(reason + " The real migration has to wait.")
    m = Mover(dry_run, log)
    lock = jobs.QueueLock()
    if not dry_run and lock.__enter__() is None:
        raise MigrateError("A final pass is running. Wait until it's done.")
    try:
        _earliest(m)
        _from_lectures_data(m)
        m.actions += drop_course_copies(dry_run, log)
    finally:
        if not dry_run:
            lock.__exit__(None, None, None)
    for p in dict.fromkeys(m.kept):
        if p.exists():
            log(f"left where it is (Stenny doesn't know it, or it's there twice): {_tilde(p)}")
    log(("Nothing to do: already migrated." if not m.actions else
         f"{m.actions} steps {'planned' if dry_run else 'done'}.") +
        f" Stenny's files: {_tilde(paths.DATA_DIR)}")
    return m.actions


EMPTY_COURSES = re.compile(r"(?ms)^\[courses\.[^\n]*\n.*\Z")


def default_config(path: Path) -> Path:
    """A new config with the default Stenny folder (~/Documents/Stenny) and no courses yet: what the app starts
    with when there's no config at all. `stenny init` writes the same, with example courses."""
    text = EXAMPLE.read_text(encoding="utf-8")
    text = text.replace('folder = "~/Documents/Stenny"', f"folder = {json.dumps(paths.tilde(paths.default_folder()), ensure_ascii=False)}")
    text = EMPTY_COURSES.sub("", text).rstrip("\n") + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    courses._write(path, text)
    return path


def on_start(log=lambda message: print(message, file=sys.stderr)) -> None:
    """When Stenny's app starts: move an older install (unless a recording or final pass is running: then at the
    next start), drop the per-course copies earlier versions wrote, and with no config at all, start one with the
    default folder."""
    from .config import config_path
    if needed():
        try:
            if busy():
                log("migrate: " + busy() + " Trying again at the next start.")
            else:
                migrate(log=lambda message: log("migrate: " + message))
        except (MigrateError, OSError, ValueError) as e:
            log(f"migrate: {e}")
    try:
        drop_course_copies(log=lambda message: log("migrate: " + message))
    except Exception as e:  # never in the way of starting (ManageError, OSError …): it tries again next time
        log(f"migrate: {e}")
    if not config_path().exists():
        try:
            default_config(config_path())
            log(f"Started a config at {config_path()}")
        except (OSError, ValueError) as e:
            log(f"Couldn't start a config: {e}")
