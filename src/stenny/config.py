"""Configuration: one TOML file, config.toml in Stenny's config place (paths.py), or $STENNY_CONFIG."""

import os
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import paths
from . import i18n as _i18n
from .vault import TALKS_DIR, Vault, safe_name
from .i18n import tr as _tr  # `tr` is the transcription table in load()

DEFAULT_PATH = paths.CONFIG_PATH
EXAMPLE = Path(__file__).resolve().parents[2] / "config.example.toml"
LANGUAGE_MODES = ("auto", "mixed")  # anything else is a fixed Whisper language code
DEFAULT_PAUSE_SHORTCUT = "cmd+shift+ü"  # Ü is the key right of P on German keyboards
MODIFIERS = {"cmd": "cmd", "command": "cmd", "shift": "shift", "ctrl": "ctrl", "control": "ctrl",
             "opt": "opt", "option": "opt", "alt": "opt"}
NAMED_KEYS = {"space", *(f"f{i}" for i in range(1, 21))}


class ConfigError(Exception):
    pass


@dataclass
class Course:
    name: str
    language: str = "fr"            # "fr", "de", … (fixed) | "auto" (one language per lecture) | "mixed"
    languages: list[str] = field(default_factory=lambda: ["fr"])  # allowed set for auto/mixed
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    calendar: list[str] = field(default_factory=list)  # extra words that identify it in calendar event titles
    kind: str = "lecture"           # "lecture" (a configured course) | "event" (a talk or event, see Config.event)
    hidden: bool = False            # out of the menu and calendar matching; everything else still works

    @property
    def is_event(self) -> bool:
        return self.kind == "event"

    @property
    def fixed_language(self) -> str | None:
        return None if self.language in LANGUAGE_MODES else self.language

    def with_language(self, lang: str | None, default_languages: list[str]) -> "Course":
        """The course as `--lang` makes it: "auto" or "mixed" switch the mode, detected among the course's
        languages (or `default_languages` if it has only one); a code (or None) leaves the course as it is
        and is applied as an override."""
        if lang not in LANGUAGE_MODES:
            return self
        languages = self.languages if len(self.languages) > 1 else default_languages
        return replace(self, language=lang, languages=list(languages))


@dataclass
class Config:
    folder: Path                              # the Stenny folder: everything for reading (vault.py)
    data_dir: Path = paths.DATA_DIR           # holds Recordings/ (audio, segments, raw output); paths.py
    keep_audio: bool = True
    model: str = "mlx-community/whisper-large-v3-turbo"
    engine: str = "auto"                      # auto (mlx on a Mac, faster-whisper elsewhere) | mlx | faster-whisper
    llm_backend: str = "claude-code"          # claude-code | api | none
    llm_model: str = "claude-haiku-5-5"       # model for `claude -p --model`
    api_model: str = "claude-haiku-5-5"
    summary: bool = True
    priority: str = "low"                     # normal | low | background
    require_ac_for_queue: bool = True
    night_only: bool = False                  # queued jobs run only between night_start and night_end
    night_start: str = "01:00"
    night_end: str = "07:00"
    live_device: str | None = None            # microphone name for live mode; None = system input device
    live_draft: bool = True                   # transcribe during the recording when plugged in (False: record only)
    live_recorder: str = "native"             # native (bundled stenny-rec, CoreAudio) | ffmpeg
    live_backup: str = "auto"                 # second microphone for the main one's gaps: auto | off | device name
    courses: dict[str, Course] = field(default_factory=dict)
    event_languages: list[str] = field(default_factory=lambda: ["en", "de", "fr"])
    pause_shortcut: str = DEFAULT_PAUSE_SHORTCUT  # Stenny's global pause/resume shortcut, see parse_shortcut
    app_language: str = "system"              # the app's own language: system | en | de (i18n.py)
    setup_checklist: bool = True              # the library window's "Finish setting up Stenny" card (False: gone)
    reminders: bool = True                    # "Record this lecture?" when a lecture in the calendar starts
    path: Path | None = None

    @property
    def visible_courses(self) -> dict[str, Course]:
        return {name: c for name, c in self.courses.items() if not c.hidden}

    @property
    def events_root(self) -> Path:
        """Talks and events: their own folder inside the Stenny folder."""
        return self.folder / TALKS_DIR

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / paths.RECORDINGS

    def event(self, title: str) -> Course:
        """A talk or event: not a course, so no glossary, course files or lecture numbers. Its files go to
        `<events_root>/<title>/<date>/`; a speaker may switch language, so it's checked for each ~25 s."""
        title = re.sub(r"\s*:\s*", " – ", title.strip())  # "Guest talk: Müller" → "Guest talk – Müller"
        return Course(name=safe_name(title), language="mixed", languages=self.event_languages,
                      tags=["talk"], kind="event")

    def root_for(self, course: Course) -> Path:
        """The folder a recording's readable files go to."""
        return self.events_root if course.is_event else self.folder

    def vault_for(self, course: Course | None = None) -> Vault:
        """Writes into the Stenny folder: a course's files (course None: glossaries, course files, topics) or a talk's."""
        return Vault(self.root_for(course) if course else self.folder, top=self.folder)

    def data_for(self, course: Course) -> Path:
        """Audio, segments and raw output: `Recordings/<Course>/`, or `Recordings/Talks/<title>/` for a talk."""
        base = self.recordings_dir / TALKS_DIR if course.is_event else self.recordings_dir
        return base / course.name

    def course(self, name: str) -> Course:
        # macOS can hand over arguments decomposed ("o" + combining ¨), while config.toml has "ö"
        def fold(s: str) -> str:
            return unicodedata.normalize("NFC", s).casefold()
        key = fold(name)
        for course in self.courses.values():
            if key == fold(course.name) or key in (fold(a) for a in course.aliases):
                return course
        known = ", ".join(self.courses) or _tr("none")
        raise ConfigError(_tr("Unknown course '{name}'. Configured courses: {known}. "
                              "Add it to {path} or pass --lang to use it ad hoc.", name=name, known=known, path=self.path))


ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}
STOPWORDS = {"and", "of", "the", "for", "und", "der", "die", "das", "des", "de", "et", "la", "le", "les", "du",
             "in", "to", "a", "an", "lecture", "vorlesung", "vl", "cours", "übung", "ubung", "tutorial", "seminar"}


def _tokens(text: str) -> list[str]:
    """Lower-case words without accents; "&" = "and"; Roman numerals as digits (II = 2)."""
    import unicodedata
    text = unicodedata.normalize("NFKD", text.replace("&", " and ")).encode("ascii", "ignore").decode().lower()
    return [ROMAN.get(w, w) for w in re.findall(r"[a-z0-9]+", text)]


def _numbers_fit(name: list[str], words: list[str]) -> bool:
    """A number in the course name must follow the same word in the title ("Processing 1"), and a number
    right after the name's last word in the title must be the course's ("Processing 2" is another course).
    Other numbers in the title (rooms, times) don't matter."""
    for i, w in enumerate(name):
        if w.isdigit() and i and not any(a == name[i - 1] and b == w for a, b in zip(words, words[1:])):
            return False
    last = next((w for w in reversed(name) if not w.isdigit() and w not in STOPWORDS), None)
    if last and not (name and name[-1].isdigit()):
        return not any(a == last and b.isdigit() for a, b in zip(words, words[1:]))
    return True


def course_for_title(courses: dict[str, "Course"], title: str) -> "Course | None":
    """The course a calendar event belongs to, judged by its title: all significant words of the course
    name (numbers must match exactly: "Programming II" is not "Programming I"), or one of its
    aliases/calendar words (3+ letters) as a whole word or phrase. The most specific match wins."""
    words = _tokens(title)
    text = " " + " ".join(words) + " "
    best, best_score = None, 0
    for c in courses.values():
        name = [w for w in _tokens(c.name) if w not in STOPWORDS and (len(w) > 2 or w.isdigit())]
        score = 0
        if name and all(w in words for w in name) and _numbers_fit(_tokens(c.name), words):
            score = len(name) * 10
        for key in [*c.calendar, *c.aliases]:
            k = " ".join(_tokens(key))
            if len(k.replace(" ", "")) >= 3 and f" {k} " in text:
                score = max(score, 5 + len(k))
        if score > best_score:
            best, best_score = c, score
    return best


def parse_shortcut(value: str) -> dict:
    """"cmd+shift+ü" → {"modifiers": ["cmd", "shift"], "key": "ü"}. Modifiers: cmd, shift, ctrl, opt (alt);
    at least one of cmd/ctrl/opt, so the shortcut doesn't swallow normal typing. Key: one character
    (as printed on the user's keyboard), "space" or f1–f20."""
    import unicodedata
    parts = [p.strip().lower() for p in unicodedata.normalize("NFC", value).split("+")]
    if value.strip().endswith("+"):            # "cmd+shift++": the key is "+" itself
        parts = [p for p in parts if p] + ["+"]
    *mods, key = parts or [""]
    if any(m not in MODIFIERS for m in mods):
        raise ConfigError(_tr("Unknown modifier in pause_shortcut '{value}': use cmd, shift, ctrl, opt.", value=value))
    mods = sorted({MODIFIERS[m] for m in mods}, key=["cmd", "ctrl", "opt", "shift"].index)
    if not set(mods) & {"cmd", "ctrl", "opt"}:
        raise ConfigError(_tr("pause_shortcut '{value}' needs cmd, ctrl or opt, e.g. \"cmd+shift+p\".", value=value))
    if len(key) != 1 and key not in NAMED_KEYS:
        raise ConfigError(_tr("pause_shortcut '{value}': the key must be one character, space or f1–f20.", value=value))
    return {"modifiers": mods, "key": key}


def _path(value: str) -> Path:
    return Path(os.path.expanduser(value))


def check_course_name(name: str) -> None:
    """A course can't be called like the talks' folder: both would be the same folder."""
    if safe_name(name).casefold() == TALKS_DIR.casefold():
        raise ConfigError(_tr("A course can't be called \"{name}\": that's the folder for talks.", name=TALKS_DIR))


def config_path() -> Path:
    """$STENNY_CONFIG, else the config in Stenny's config place, else (before `stenny migrate`) an older one."""
    if os.environ.get("STENNY_CONFIG"):
        return _path(os.environ["STENNY_CONFIG"])
    if DEFAULT_PATH.exists() or os.environ.get("STENNY_HOME"):
        return DEFAULT_PATH
    for legacy in (paths.LEGACY_SYSTEM_DIR / "config.toml", paths.LEGACY_CONFIG_DIR / "config.toml"):
        if legacy.exists():
            return legacy
    return DEFAULT_PATH


def folder_of(data: dict) -> str | None:
    """The Stenny folder as config.toml says it: `folder`, or before `stenny migrate` `vault` + `lectures_dir`."""
    if data.get("folder"):
        return data["folder"]
    if data.get("vault"):
        return str(Path(data["vault"]) / data.get("lectures_dir", "Lectures"))
    return None


def _engine(value: str) -> str:
    from .engines import ENGINES
    if value not in ENGINES:
        raise ConfigError(_tr("[transcription] engine '{value}': use auto, mlx or faster-whisper.", value=value))
    return value


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        raise ConfigError(_tr("No config at {path}. Run `stenny init` to create one.", path=path))
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    folder = folder_of(data)
    if not folder or "CHANGE-ME" in folder:
        raise ConfigError(_tr("Set `folder` in {path} to the folder your transcripts go to (e.g. ~/Documents/Stenny).",
                               path=path))
    pause_shortcut = data.get("app", {}).get("pause_shortcut", DEFAULT_PAUSE_SHORTCUT)
    parse_shortcut(pause_shortcut)
    app_language = data.get("app", {}).get("language", "system")
    if app_language not in _i18n.SETTINGS:
        raise ConfigError(_tr("[app] language '{value}': use system, en or de.", value=app_language))
    try:
        tr, llm, proc = data.get("transcription", {}), data.get("llm", {}), data.get("processing", {})
        courses = {}
        for name, c in data.get("courses", {}).items():
            check_course_name(name)
            language = c.get("language", "fr")
            languages = c.get("languages") or ([language] if language not in LANGUAGE_MODES else ["fr", "de", "en"])
            courses[name] = Course(name=name, language=language, languages=languages,
                                   tags=c.get("tags", []), aliases=c.get("aliases", []),
                                   calendar=c.get("calendar", []), hidden=c.get("hidden", False))
        return Config(
            folder=_path(folder),
            # "audio_dir": the name before `stenny migrate` (paths.py)
            data_dir=_path(data.get("data_dir") or data.get("audio_dir") or str(paths.DATA_DIR)),
            keep_audio=data.get("keep_audio", True),
            model=tr.get("model", Config.model),
            engine=_engine(tr.get("engine", "auto")),
            llm_backend=llm.get("backend", Config.llm_backend),
            llm_model=llm.get("model", Config.llm_model),
            api_model=llm.get("api_model", Config.api_model),
            summary=llm.get("summary", True),
            priority=proc.get("priority", Config.priority),
            require_ac_for_queue=proc.get("require_ac_for_queue", True),
            night_only=proc.get("night_only", False),
            night_start=proc.get("night_start", Config.night_start),
            night_end=proc.get("night_end", Config.night_end),
            live_device=data.get("live", {}).get("device") or None,
            live_draft=data.get("live", {}).get("draft", True),
            live_recorder=data.get("live", {}).get("recorder", "native"),
            live_backup=data.get("live", {}).get("backup", "auto"),
            courses=courses,
            event_languages=data.get("recordings", {}).get("languages", ["en", "de", "fr"]),
            pause_shortcut=pause_shortcut,
            app_language=app_language,
            setup_checklist=data.get("app", {}).get("setup_checklist", True),
            reminders=data.get("app", {}).get("reminders", True),
            path=path,
        )
    except KeyError as e:
        raise ConfigError(_tr("Missing required key {key} in {path}", key=e, path=path)) from None
