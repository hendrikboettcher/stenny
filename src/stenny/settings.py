"""The app's Settings window: what it shows and the one setting it changes at a time. config.toml is edited as text
(like courses.py), so its comments and order stay; a change that wouldn't load is never written."""

import json
import re
import tomllib
from pathlib import Path

from . import config as config_mod
from . import i18n
from .i18n import tr
from .config import LANGUAGE_MODES, Config, ConfigError, parse_shortcut
from .courses import _section, _write

TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
SUPPORTED = ("en", "de", "fr")  # the languages Stenny is built and tested for; the window offers only these


def _bool(v) -> bool:
    if not isinstance(v, bool):
        raise ConfigError(tr("Expected on or off."))
    return v


def _choice(*options):
    def check(v):
        if v not in options:
            raise ConfigError(tr("Use one of: {options}.", options=", ".join(options)))
        return v
    return check


def _time(v) -> str:
    if not isinstance(v, str) or not TIME.fullmatch(v):
        raise ConfigError(tr("Use a time such as 01:00."))
    return v


def _shortcut(v) -> str:
    parse_shortcut(v)
    return v


def _languages(v) -> list[str]:
    if not isinstance(v, list) or not v or not all(x in SUPPORTED for x in v):
        raise ConfigError(tr("Stenny is made for English, German and French so far."))
    return list(dict.fromkeys(v))


def _backup(v) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ConfigError(tr("Pick a microphone, Automatic or Off."))
    return v.strip()


# what the window may change: "table.key" (or "key" at the top) → check
SETTINGS = {
    "keep_audio": _bool,
    "live.draft": _bool,
    "live.backup": _backup,
    "llm.backend": _choice("claude-code", "api", "none"),
    "llm.summary": _bool,
    "processing.priority": _choice("normal", "low", "background"),
    "processing.require_ac_for_queue": _bool,
    "processing.night_only": _bool,
    "processing.night_start": _time,
    "processing.night_end": _time,
    "app.pause_shortcut": _shortcut,
    "app.language": _choice(*i18n.SETTINGS),
    "app.setup_checklist": _bool,
    "recordings.languages": _languages,
}


def get(cfg: Config) -> dict:
    """Everything the window shows, read from the loaded config."""
    return {
        "keep_audio": cfg.keep_audio,
        "live": {"draft": cfg.live_draft, "backup": cfg.live_backup},
        "llm": {"backend": cfg.llm_backend, "summary": cfg.summary},
        "processing": {"priority": cfg.priority, "require_ac_for_queue": cfg.require_ac_for_queue,
                       "night_only": cfg.night_only, "night_start": cfg.night_start, "night_end": cfg.night_end},
        "app": {"pause_shortcut": cfg.pause_shortcut, "language": cfg.app_language,
                "setup_checklist": cfg.setup_checklist},
        # Stenny's own language: what "system" comes to, and the system's (for "System default (Deutsch)")
        "ui_language": i18n.resolve(cfg.app_language),
        "system_language": i18n.system_language(),
        "recordings": {"languages": cfg.event_languages, "folder": str(cfg.events_root)},
        "paths": {"lectures": str(cfg.folder), "data": str(cfg.data_dir), "config": str(cfg.path)},
        "model": cfg.model,
        "courses": [{"name": c.name, "language": c.language, "languages": c.languages}
                    for c in cfg.courses.values() if not c.hidden],
    }


def _table(text: str, table: str | None) -> tuple[int, int] | None:
    """Where a table's lines are: the top of the file up to the first table for None; None if it's not there."""
    if table is None:
        first = re.compile(r"^[ \t]*\[", re.M).search(text)
        return 0, first.start() if first else len(text)
    header = re.compile(rf"^\[{re.escape(table)}\][ \t]*(?:#.*)?$", re.M).search(text)
    if not header:
        return None
    start = header.end() + 1 if header.end() < len(text) else header.end()
    following = re.compile(r"^[ \t]*\[", re.M).search(text, start)
    return start, following.start() if following else len(text)


def set_line(text: str, span: tuple[int, int] | None, table: str | None, key: str, value) -> str:
    """The text with `key = value` in that table: its line replaced (a comment after it kept), else added after the
    table's last setting, else the table added at the end. value None takes the line out."""
    line = None if value is None else f"{key} = {json.dumps(value, ensure_ascii=False)}"
    if span is not None:
        start, end = span
        body = text[start:end]
        existing = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=.*$", re.M).search(body)
        if existing:
            old = existing.group(0)
            comment = re.search(r"[ \t]+#[^\"']*$", old)  # a trailing comment without quotes in it
            new = "" if line is None else line + (comment.group(0) if comment else "")
            a, b = start + existing.start(), start + existing.end()
            if line is None and b < len(text) and text[b] == "\n":
                b += 1
            return text[:a] + new + text[b:]
        if line is None:
            return text
        settings = list(re.compile(r"^[ \t]*[^#\s\[][^\n]*$", re.M).finditer(body))
        at = start + (settings[-1].end() + 1 if settings else 0)
        at = min(at, len(text))
        prefix = "" if at == 0 or text[at - 1] == "\n" else "\n"
        return text[:at] + prefix + line + "\n" + text[at:]
    if line is None:
        return text
    return text.rstrip("\n") + f"\n\n[{table}]\n{line}\n"


def change(cfg: Config, key: str, value) -> dict:
    """Sets one setting (see SETTINGS) and returns all of them again."""
    if key not in SETTINGS:
        raise ConfigError(tr("'{key}' can't be changed here.", key=key))
    value = SETTINGS[key](value)
    table, _, name = key.rpartition(".")
    table = table or None
    text = cfg.path.read_text(encoding="utf-8")
    text = set_line(text, _table(text, table), table, name, value)
    return _save(cfg.path, text)


def course_language(cfg: Config, course: str, language: str, languages: list[str] | None) -> dict:
    """A course's language: a fixed code, or "auto"/"mixed" among `languages`."""
    c = cfg.course(course)
    if language not in LANGUAGE_MODES and language not in SUPPORTED:
        raise ConfigError(tr("Stenny is made for English, German and French so far (or mixed, or one per lecture)."))
    text = cfg.path.read_text(encoding="utf-8")
    text = set_line(text, _section(text, c.name), None, "language", language)
    keep = _languages(languages) if language in LANGUAGE_MODES and languages else None
    text = set_line(text, _section(text, c.name), None, "languages", keep)
    return _save(cfg.path, text)


def _save(path: Path, text: str) -> dict:
    tomllib.loads(text)
    tmp = path.with_suffix(".check.toml")
    tmp.write_text(text, encoding="utf-8")
    try:
        cfg = config_mod.load(tmp)  # the whole config still loads, or nothing is written
    finally:
        tmp.unlink(missing_ok=True)
    _write(path, text)
    cfg.path = path
    return get(cfg)
