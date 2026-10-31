"""Configuration: one TOML file (default ~/Lectures Data/_System/config.toml, or $STENNY_CONFIG)."""

import os
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import paths

DEFAULT_PATH = paths.CONFIG_PATH
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
    vault: Path
    lectures_dir: str
    data_dir: Path                            # everything that isn't for reading (paths.py); per course
    keep_audio: bool = True
    model: str = "mlx-community/whisper-large-v3-turbo"
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
    live_draft: bool = True                   # transcribe while recording (False: record only)
    live_provisional: bool = False            # draft: show the sentence being spoken, replaced seconds later (costs battery)
    live_recorder: str = "native"             # native (bundled stenny-rec, CoreAudio) | ffmpeg
    live_backup: str = "auto"                 # second microphone for the main one's gaps: auto | off | device name
    courses: dict[str, Course] = field(default_factory=dict)
    events_dir: str | None = None             # vault folder for talks and events; None = "Recordings" next to lectures_dir
    event_languages: list[str] = field(default_factory=lambda: ["en", "de", "fr"])
    pause_shortcut: str = DEFAULT_PAUSE_SHORTCUT  # Stenny's global pause/resume shortcut, see parse_shortcut
    path: Path | None = None

    @property
    def visible_courses(self) -> dict[str, Course]:
        return {name: c for name, c in self.courses.items() if not c.hidden}

    @property
    def lectures_root(self) -> Path:
        return self.vault / self.lectures_dir

    @property
    def events_root(self) -> Path:
        return self.vault / (self.events_dir or str(Path(self.lectures_dir).parent / "Recordings"))

    def event(self, title: str) -> Course:
        """A talk or event: not a course, so no glossary, course files or lecture numbers. Its files go to
        `<events_root>/<title>/<date>/`; a speaker may switch language, so it's checked for each ~25 s."""
        from .vault import safe_name
        title = re.sub(r"\s*:\s*", " – ", title.strip())  # "Guest talk: Müller" → "Guest talk – Müller"
        return Course(name=safe_name(title), language="mixed", languages=self.event_languages,
                      tags=["talk"], kind="event")

    def root_for(self, course: Course) -> Path:
        """The vault folder a recording's files go to."""
        return self.events_root if course.is_event else self.lectures_root

    def data_for(self, course: Course) -> Path:
        """Audio, segments and raw output: `<data_dir>/<Course>/`, or `<data_dir>/_Recordings/<title>/` for events."""
        return self.data_dir / "_Recordings" / course.name if course.is_event else self.data_dir / course.name

    def course(self, name: str) -> Course:
        # macOS can hand over arguments decomposed ("o" + combining ¨), while config.toml has "ö"
        def fold(s: str) -> str:
            return unicodedata.normalize("NFC", s).casefold()
        key = fold(name)
        for course in self.courses.values():
            if key == fold(course.name) or key in (fold(a) for a in course.aliases):
                return course
        known = ", ".join(self.courses) or "none"
        raise ConfigError(f"Unknown course '{name}'. Configured courses: {known}. "
                          f"Add it to {self.path} or pass --lang to use it ad hoc.")


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
        raise ConfigError(f"Unknown modifier in pause_shortcut '{value}': use cmd, shift, ctrl, opt.")
    mods = sorted({MODIFIERS[m] for m in mods}, key=["cmd", "ctrl", "opt", "shift"].index)
    if not set(mods) & {"cmd", "ctrl", "opt"}:
        raise ConfigError(f"pause_shortcut '{value}' needs cmd, ctrl or opt, e.g. \"cmd+shift+p\".")
    if len(key) != 1 and key not in NAMED_KEYS:
        raise ConfigError(f"pause_shortcut '{value}': the key must be one character, space or f1–f20.")
    return {"modifiers": mods, "key": key}


def _path(value: str) -> Path:
    return Path(os.path.expanduser(value))


def config_path() -> Path:
    """$STENNY_CONFIG, else the config in ~/Lectures Data/_System/, else (before `stenny migrate`) the old one."""
    if os.environ.get("STENNY_CONFIG"):
        return _path(os.environ["STENNY_CONFIG"])
    legacy = paths.LEGACY_CONFIG_DIR / "config.toml"
    return legacy if not DEFAULT_PATH.exists() and legacy.exists() else DEFAULT_PATH


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        raise ConfigError(f"No config at {path}. Run `stenny init` to create one.")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if not data.get("vault") or "CHANGE-ME" in str(data.get("vault")):
        raise ConfigError(f"Set `vault` in {path} to the folder your transcripts go to (e.g. your Obsidian vault).")
    pause_shortcut = data.get("app", {}).get("pause_shortcut", DEFAULT_PAUSE_SHORTCUT)
    parse_shortcut(pause_shortcut)
    try:
        tr, llm, proc = data.get("transcription", {}), data.get("llm", {}), data.get("processing", {})
        courses = {}
        for name, c in data.get("courses", {}).items():
            language = c.get("language", "fr")
            languages = c.get("languages") or ([language] if language not in LANGUAGE_MODES else ["fr", "de", "en"])
            courses[name] = Course(name=name, language=language, languages=languages,
                                   tags=c.get("tags", []), aliases=c.get("aliases", []),
                                   calendar=c.get("calendar", []), hidden=c.get("hidden", False))
        return Config(
            vault=_path(data["vault"]),
            lectures_dir=data.get("lectures_dir", "Lectures"),
            # "audio_dir": the name before everything moved into one folder
            data_dir=_path(data.get("data_dir") or data.get("audio_dir") or str(paths.HOME)),
            keep_audio=data.get("keep_audio", True),
            model=tr.get("model", Config.model),
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
            live_provisional=data.get("live", {}).get("provisional", False),
            live_recorder=data.get("live", {}).get("recorder", "native"),
            live_backup=data.get("live", {}).get("backup", "auto"),
            courses=courses,
            events_dir=data.get("recordings", {}).get("folder"),
            event_languages=data.get("recordings", {}).get("languages", ["en", "de", "fr"]),
            pause_shortcut=pause_shortcut,
            path=path,
        )
    except KeyError as e:
        raise ConfigError(f"Missing required key {e} in {path}") from None
