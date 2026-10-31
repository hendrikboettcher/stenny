"""Add and hide courses from the menu. config.toml is edited as text, so its comments stay."""

import json
import re
import tomllib
import unicodedata
from pathlib import Path

from .config import LANGUAGE_MODES, Config, ConfigError
from .vault import safe_name

HEADER = re.compile(r"^\[courses\.(.+?)\][ \t]*(?:#.*)?$", re.M)


def _section(text: str, name: str) -> tuple[int, int]:
    """Where the course's table starts (after its header line) and ends."""
    for m in HEADER.finditer(text):
        if name in tomllib.loads(f"{m.group(1)} = 1"):
            start = m.end() + 1 if m.end() < len(text) else m.end()
            following = re.compile(r"^[ \t]*\[", re.M).search(text, start)
            return start, following.start() if following else len(text)
    raise ConfigError(f"Course '{name}' isn't written as its own table in the config.")


def _write(path: Path, text: str) -> None:
    tomllib.loads(text)  # never replace a working config with a broken one
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _existing(cfg: Config, name: str):
    try:
        return cfg.course(name)
    except ConfigError:
        return None


def add(cfg: Config, name: str, language: str, languages: list[str] | None = None, alias: str | None = None) -> str:
    """Adds a course, or brings a hidden one back as it was. Returns what happened, in a sentence."""
    name = unicodedata.normalize("NFC", name).strip()
    alias = alias.strip() if alias else None
    if not name:
        raise ConfigError("A course needs a name.")
    if safe_name(name) != name:
        raise ConfigError(f"A course name can't contain these characters: / \\ : # ^ [ ] | * ? \" < >. Try '{safe_name(name)}'.")
    if language not in LANGUAGE_MODES and not re.fullmatch(r"[a-z]{2,3}", language):
        raise ConfigError(f"Unknown language '{language}': use a code such as en, de or fr, or mixed or auto.")
    text = cfg.path.read_text(encoding="utf-8")
    if existing := _existing(cfg, name):
        if not existing.hidden:
            raise ConfigError(f"'{existing.name}' is already one of your courses.")
        start, end = _section(text, existing.name)
        section = re.sub(r"^[ \t]*hidden[ \t]*=[ \t]*true.*\n?", "", text[start:end], flags=re.M)
        _write(cfg.path, text[:start] + section + text[end:])
        return f"{existing.name} is back, with its earlier settings."
    if alias and (clash := _existing(cfg, alias)):
        raise ConfigError(f"The short name '{alias}' is already used by {clash.name}.")
    table = [f"[courses.{json.dumps(name, ensure_ascii=False)}]", f"language = {json.dumps(language)}"]
    if languages:
        table.append(f"languages = {json.dumps(languages)}")
    if alias:
        table.append(f"aliases = {json.dumps([alias], ensure_ascii=False)}")
    _write(cfg.path, text.rstrip("\n") + "\n\n" + "\n".join(table) + "\n")
    return f"Added {name}."


def hide(cfg: Config, name: str) -> str:
    """Takes a course out of the menu and calendar matching. Nothing is deleted: transcripts, glossaries and
    settings stay, and adding the same name again brings it back."""
    course = cfg.course(name)
    if course.hidden:
        return f"{course.name} is already hidden."
    text = cfg.path.read_text(encoding="utf-8")
    start, _ = _section(text, course.name)
    _write(cfg.path, text[:start] + "hidden = true\n" + text[start:])
    return f"{course.name} is hidden. Its transcripts stay where they are."
