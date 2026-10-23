"""`stenny migrate`: move everything into the layout of paths.py, once. Safe to run again (it skips what's
done) and with --dry-run (only lists what it would do). Refuses while a live session is running.

1. config:        ~/.config/lecture/*.toml           → ~/Lectures Data/_System/
2. course data:   ~/Lectures Audio/<Course>/*         → ~/Lectures Data/<Course>/   (paths inside the json files too)
3. queue, logs:   ~/.local/share/lecture/             → ~/Lectures Data/_Queue/, _System/
4. raw output:    vault <Course>/<date>/… (raw).md    → ~/Lectures Data/<Course>/   (the transcript's note says where)
5. glossaries:    vault _Glossaries/<Course>.md       → _Glossaries/<Course> (glossary).md
6. course files rebuilt (the overview links the renamed glossary)
"""

import json
import os
import re
import shutil
from pathlib import Path

from . import course_files, paths
from .config import load
from .live import control
from .vault import GLOSSARY_DIR, LectureFiles, Vault, safe_name


class MigrateError(Exception):
    pass


def _tilde(path: Path) -> str:
    return str(path).replace(str(Path.home()), "~", 1)


def _rewrite(text: str, old: Path, new: Path) -> str:
    """Replace the old folder in paths (absolute and "~/" forms; the trailing "/" keeps "… (Test)" apart)."""
    for a, b in ((f"{old}/", f"{new}/"), (f"{_tilde(old)}/", f"{_tilde(new)}/")):
        text = text.replace(a, b)
    return text


def migrate(dry_run: bool = False, log=print) -> int:
    if control.is_running() or control.is_running(paths.LEGACY_STATE_DIR / "live.sock"):
        if not dry_run:
            raise MigrateError("A live session is running. Stop it (and let its final pass finish) first.")
        log("A live session is running: the real migration has to wait until it's stopped and its final pass is done.")
    actions = 0

    def act(message: str, fn=None) -> None:
        nonlocal actions
        actions += 1
        log(("would: " if dry_run else "") + message)
        if fn and not dry_run:
            fn()

    def move(src: Path, dst: Path, rewrite: tuple[Path, Path] | None = None) -> None:
        if dst.exists():
            log(f"skipped, already there: {dst}")
            return
        def do():
            dst.parent.mkdir(parents=True, exist_ok=True)
            if rewrite and src.suffix in (".json", ".toml"):
                dst.write_text(_rewrite(src.read_text(encoding="utf-8"), *rewrite), encoding="utf-8")
                src.unlink()
            else:
                shutil.move(src, dst)
        act(f"move {_tilde(src)} → {_tilde(dst)}", do)

    def rmdir_empty(d: Path) -> None:
        if d.is_dir() and not [p for p in d.iterdir() if p.name != ".DS_Store"]:
            act(f"remove empty folder {_tilde(d)}", lambda: shutil.rmtree(d))

    # 1. config (first: everything else reads it)
    old_audio = paths.LEGACY_AUDIO_DIR
    if paths.LEGACY_CONFIG_DIR.is_dir():
        for f in sorted(paths.LEGACY_CONFIG_DIR.glob("*.toml")):
            dst = paths.SYSTEM_DIR / f.name
            if dst.exists():
                log(f"skipped, already there: {dst}")
                continue
            text = f.read_text(encoding="utf-8")
            m = re.search(r'(?m)^audio_dir\s*=\s*"([^"]*)"\s*$', text)
            if m and Path(os.path.expanduser(m.group(1))) == old_audio:
                text = text.replace(m.group(0), "# audio_dir: no longer needed, everything outside the vault is in "
                                                 f"{_tilde(paths.HOME)} (see paths.py in the project)")
            def write(f=f, dst=dst, text=text):
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(text, encoding="utf-8")
                f.unlink()
            act(f"move {_tilde(f)} → {_tilde(dst)}", write)
        rmdir_empty(paths.LEGACY_CONFIG_DIR)
    cfg = load(paths.CONFIG_PATH) if paths.CONFIG_PATH.exists() else load()
    data = cfg.data_dir if not dry_run or paths.CONFIG_PATH.exists() else paths.HOME

    # 2. course data
    if old_audio.is_dir() and old_audio != data:
        for course_dir in sorted(p for p in old_audio.iterdir() if p.is_dir()):
            for f in sorted(p for p in course_dir.iterdir() if p.is_file() and p.name != ".DS_Store"):
                move(f, data / course_dir.name / f.name, (old_audio, data))
            rmdir_empty(course_dir)
        rmdir_empty(old_audio)

    # 3. queue and logs
    state = paths.LEGACY_STATE_DIR
    if state.is_dir():
        for f in sorted((state / "queue").glob("*.json")) if (state / "queue").is_dir() else []:
            move(f, paths.QUEUE_DIR / f.name, (old_audio, data))
        rmdir_empty(state / "queue")
        for f in sorted(p for p in state.iterdir() if not p.is_dir()):  # also the socket
            if f.name in ("live.sock", "queue.lock", ".DS_Store"):
                act(f"remove stale {_tilde(f)}", f.unlink)
            else:
                move(f, paths.SYSTEM_DIR / f.name)
        rmdir_empty(state)

    # 4. raw output out of the vault; 5. glossaries renamed
    vault = Vault(cfg.lectures_root)
    names = {safe_name(n): n for n in cfg.courses}
    for course_dir in vault.course_dirs():
        course = names.get(course_dir.name, course_dir.name)
        for folder in vault.lectures(course):
            files = LectureFiles(folder, course)
            raw = files.existing("raw")
            if not raw:
                continue
            dst = data / course / f"{safe_name(course)} {folder.name} (raw).md"
            transcript = files.existing("transcript")
            def out(raw=raw, dst=dst, transcript=transcript, folder=folder, course=course):
                vault.move_out(raw, dst)
                text = transcript.read_text(encoding="utf-8")
                new = text.replace(f"[[{raw.stem}]]", f"`{_tilde(dst)}` (outside the vault)")
                vault.write(transcript, _rewrite(new, old_audio, data))
                meta = data / course / f"{folder.name}.segments.json"
                if meta.exists():
                    m = json.loads(meta.read_text(encoding="utf-8"))
                    m["meta"].setdefault("lecture", {})["raw_file"] = _tilde(dst)
                    meta.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
            act(f"move {_tilde(raw)} → {_tilde(dst)}", out)
    gdir = vault.root / GLOSSARY_DIR
    for g in sorted(gdir.glob("*.md")) if gdir.is_dir() else []:
        if g.stem.endswith(" (glossary)") or (gdir / f"{g.stem} (glossary).md").exists():
            continue
        act(f"rename {g.name} → {g.stem} (glossary).md", lambda g=g: vault.rename(g, gdir / f"{g.stem} (glossary).md"))

    # 6. course files
    if not dry_run and actions:
        for course in cfg.courses:
            course_files.rebuild(vault, course, log)
    log(("Nothing to do: already migrated." if not actions else
         f"{actions} steps {'planned' if dry_run else 'done'}.") + f" Data folder: {_tilde(data)}")
    return actions
