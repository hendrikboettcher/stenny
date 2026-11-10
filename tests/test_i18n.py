"""The app in English and German: the engine's messages (stenny/i18n.py) and the app's own texts
(desktop/src/lib/locales/*.json) stay complete and consistent."""

import ast
import json
import re
from pathlib import Path

import pytest

from stenny import app_server, i18n, settings
from stenny.i18n import DE, tr

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "stenny"
LOCALES = ROOT / "desktop" / "src" / "lib" / "locales"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


@pytest.fixture
def german():
    i18n.set_language("de")
    yield
    i18n.set_language("en")


def _literal_messages() -> list[tuple[str, str]]:
    """Every tr("…") / _tr("…") with a plain string in the engine's code (f-strings are checked below)."""
    found = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) in ("tr", "_tr") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.append((path.name, arg.value))
    return found


def test_every_message_has_german():
    missing = [(f, m) for f, m in _literal_messages() if m not in DE]
    assert not missing, missing


@pytest.mark.parametrize("text", [
    # built with an f-string around tr(): every value it can take
    "This recording is still running. To rename it, stop it first, or use Cancel Recording… in Stenny's menu.",
    "This recording is still running. To delete it, stop it first, or use Cancel Recording… in Stenny's menu.",
    "There's already a talk called '{name}'.", "There's already a course called '{name}'.",
    "There's no talk called '{name}'.", "There's no course called '{name}'.",
    "final pass queued (as requested)", "final pass queued (memory is low)", "final pass queued (on battery)",
    "The recording is finished; its transcript is made once the Mac is plugged in.",
    "The recording is finished; its transcript comes later.",
    "The recording is finished and its transcript is being made now.",
    "Stenny is recording. The full transcript is made once the recording ends.",
])
def test_built_messages_have_german(text):
    assert text in DE


def test_draft_states_are_translated():
    from stenny.render import draft_status
    for status, waiting in [("queued", "battery"), ("queued", None), ("finalizing", None), ("live", None)]:
        assert draft_status(status, waiting) in DE


def test_german_keeps_the_placeholders():
    for en, de in DE.items():
        assert set(PLACEHOLDER.findall(en)) == set(PLACEHOLDER.findall(de)), en


def test_tr(german):
    assert tr("Added {name}.", name="Statistik") == "Statistik hinzugefügt."
    assert i18n.trash_name() == "Papierkorb"
    i18n.set_language("en")
    assert tr("Added {name}.", name="Statistics") == "Added Statistics."
    i18n.set_language("fr")  # not one Stenny has: English
    assert i18n.language() == "en"


def test_system_language(monkeypatch):
    monkeypatch.setenv("STENNY_SYSTEM_LANGUAGE", "de")
    assert i18n.system_language() == "de"
    assert i18n.resolve("system") == "de" and i18n.resolve("en") == "en" and i18n.resolve(None) == "de"
    monkeypatch.delenv("STENNY_SYSTEM_LANGUAGE")
    monkeypatch.setattr(i18n, "_preferred", lambda: ("fr-FR", "de-AT", "en-US"))
    assert i18n.system_language() == "de"  # the first preferred language Stenny has
    monkeypatch.setattr(i18n, "_preferred", lambda: ("it-IT",))
    assert i18n.system_language() == "en"


def test_language_setting(tmp_path, monkeypatch):
    from stenny import config as config_mod
    monkeypatch.setenv("STENNY_SYSTEM_LANGUAGE", "en")
    path = tmp_path / "config.toml"
    path.write_text(f'vault = "{tmp_path.as_posix()}"\n\n[app]\npause_shortcut = "cmd+shift+p"\n', encoding="utf-8")
    cfg = config_mod.load(path)
    assert cfg.app_language == "system"
    shown = settings.get(cfg)
    assert shown["app"]["language"] == "system" and shown["ui_language"] == "en"
    shown = settings.change(cfg, "app.language", "de")
    assert shown["app"]["language"] == "de" and shown["ui_language"] == "de"
    assert 'language = "de"' in path.read_text(encoding="utf-8")
    with pytest.raises(config_mod.ConfigError):
        settings.change(config_mod.load(path), "app.language", "fr")
    path.write_text(path.read_text(encoding="utf-8").replace('"de"', '"fr"'), encoding="utf-8")
    with pytest.raises(config_mod.ConfigError):
        config_mod.load(path)


def test_app_server_answers_in_the_setting(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(f'vault = "{tmp_path.as_posix()}"\n\n[app]\nlanguage = "de"\n', encoding="utf-8")
    monkeypatch.setenv("STENNY_CONFIG", str(path))
    reply = app_server._answer(json.dumps({"id": 1, "method": "nope"}))
    assert reply["error"] == "Unbekannte Methode „nope“."
    path.write_text(f'vault = "{tmp_path.as_posix()}"\n\n[app]\nlanguage = "en"\n', encoding="utf-8")
    reply = app_server._answer(json.dumps({"id": 2, "method": "nope"}))
    assert reply["error"] == "Unknown method 'nope'."
    i18n.set_language("en")


def test_summary_note(german):
    text = "---\ncourse: X\n---\n# X\n\n> AI-generated summary (claude), see [[X]].\n\n## Gist\n\nText.\n"
    note = DE["Written by AI from the transcript. It can leave things out or get them wrong; the transcript is the source."]
    assert "> " + note in app_server.summary_markdown(text)


# ---------------------------------------------------------------- the app's own texts (Svelte and Rust share them)

def _locale(lang: str) -> dict[str, str]:
    return json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))


def test_locales_have_the_same_keys_and_placeholders():
    en, de = _locale("en"), _locale("de")
    assert en.keys() == de.keys(), set(en) ^ set(de)
    for key in en:
        assert set(PLACEHOLDER.findall(en[key])) == set(PLACEHOLDER.findall(de[key])), key


def test_every_key_the_app_uses_exists():
    """t("…") in the Svelte code and tr("…") in the Rust code name keys that are in en.json."""
    keys = set(_locale("en"))
    used = set()
    for path in [*(ROOT / "desktop" / "src").rglob("*.svelte"), *(ROOT / "desktop" / "src").rglob("*.ts")]:
        used |= set(re.findall(r'\bt\("([\w.]+)"', path.read_text(encoding="utf-8")))
    for path in (ROOT / "desktop" / "src-tauri" / "src").glob("*.rs"):
        used |= set(re.findall(r'\btr!?\(\s*"([\w.]+)"', path.read_text(encoding="utf-8")))
    assert used, "no keys found"
    assert not used - keys, used - keys


GERMAN_TERMS = {"lecture": "Vorlesung", "course": "Kurs", "talk": "Vortrag", "transcript": "Transkript",
                "summary": "Zusammenfassung", "recording": "Aufnahme"}


def test_german_uses_du_not_sie():
    """The German addresses the user as "du" everywhere."""
    texts = list(_locale("de").values()) + list(DE.values())
    # a capital Sie/Ihr in mid-sentence is the formal "you" (at a sentence's start it's "they"/"their")
    formal = [t for t in texts if re.search(r"(?<![.;:!?]\s)(?<!^)\b(Sie|Ihnen|Ihr|Ihre|Ihren|Ihrem|Ihrer)\b", t)]
    assert not formal, formal
    assert any(re.search(r"\b(du|dein\w*|dich|dir)\b", t) for t in texts)


# word-for-word renderings the German once had, and the established word for each (desktop/README.md, "Language")
NOT_GERMAN = {
    "Tastenkürzel": "Kurzbefehl (as in macOS)",
    "Im Finder zeigen": "Im Finder anzeigen",
    "Im Explorer zeigen": "Im Explorer anzeigen",
    "am Strom": "am Netzteil",
    "Damit löschst du": "say what is deleted: „Der Kurs … wird gelöscht“",
    "Pausiert seit": "a duration, not a time of day: „Pausiert · 0:05:12“",
    "Mikro aus": "Mikrofon aus",
    "Zurückholen": "Wiederherstellen",
    "Stoppen": "Beenden",
}


def test_german_uses_the_established_words():
    texts = list(_locale("de").values()) + list(DE.values())
    found = [(bad, better, t) for t in texts for bad, better in NOT_GERMAN.items() if bad in t]
    assert not found, found
