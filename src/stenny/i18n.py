"""The engine's messages in the app's language (English or German).

Stenny's app (desktop/) keeps its own texts in desktop/src/lib/locales/*.json; the engine only translates what it
sends back to the app: error messages, the recording's warnings, the status of a draft. Each message is looked up
by its English text, so the code reads as before: `raise ManageError(tr("A name can't be empty."))`, with
placeholders filled in after the lookup: `tr("Added {name}.", name=name)`.

The language: `[app] language` in config.toml ("system", "en" or "de"). The app server sets it for each request
(app_server.py); a recording it starts gets it as $STENNY_LANGUAGE. The CLI stays in English. "system" is the first
of the user's preferred languages that Stenny has ($STENNY_SYSTEM_LANGUAGE, which the app passes on, else macOS's
or Windows' own list). Transcripts, summaries and file names in the vault are never translated.
"""

import functools
import os
import subprocess
import sys

SUPPORTED = ("en", "de")
SETTINGS = ("system", *SUPPORTED)

_language = os.environ.get("STENNY_LANGUAGE") if os.environ.get("STENNY_LANGUAGE") in SUPPORTED else "en"


def language() -> str:
    return _language


def set_language(lang: str | None) -> None:
    global _language
    _language = lang if lang in SUPPORTED else "en"


@functools.lru_cache(maxsize=1)
def _preferred() -> tuple[str, ...]:
    """The user's preferred languages, most preferred first ("de-AT", "en-US" …)."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True,
                                 timeout=3).stdout
            return tuple(part.strip().strip('"') for part in out.strip("()\n ").split(",") if part.strip())
        if sys.platform == "win32":
            import ctypes
            import locale
            return (locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), ""),)
    except (OSError, subprocess.SubprocessError, AttributeError, ValueError):
        pass
    return tuple(v for v in (os.environ.get("LANG", ""),) if v)


def system_language() -> str:
    """The first preferred language Stenny has, else English."""
    if os.environ.get("STENNY_SYSTEM_LANGUAGE") in SUPPORTED:
        return os.environ["STENNY_SYSTEM_LANGUAGE"]
    for tag in _preferred():
        code = tag.replace("_", "-").split("-")[0].lower()
        if code in SUPPORTED:
            return code
    return "en"


def resolve(setting: str | None) -> str:
    """The language the app shows for a setting: "en", "de", or for "system" (or nothing) the system's."""
    return setting if setting in SUPPORTED else system_language()


def tr(text: str, **values) -> str:
    """`text` in the current language, with {placeholders} filled in."""
    if _language == "de":
        text = DE.get(text, text)
    return text.format(**values) if values else text


def trash_name() -> str:
    """Where deleted things go: the Trash, the Recycle Bin on Windows (Papierkorb on both)."""
    return tr("Recycle Bin") if sys.platform == "win32" else tr("Trash")


# Natural German, addressing the user as "du"; the same terms as the app (desktop/README.md, "Language").
DE = {
    # general
    "Trash": "Papierkorb",
    "Recycle Bin": "Papierkorb",
    "Missing '{name}'.": "„{name}“ fehlt.",
    "Unknown method '{name}'.": "Unbekannte Methode „{name}“.",
    "Not JSON.": "Kein JSON.",
    "Something went wrong ({error}).": "Etwas ist schiefgelaufen ({error}).",
    "Stenny's settings couldn't be read: {error}": "Stennys Einstellungen konnten nicht gelesen werden: {error}",
    "Stenny is made for English, German and French so far.":
        "Stenny ist bisher für Englisch, Deutsch und Französisch gemacht.",

    # the library window
    "Written by AI from the transcript. It can leave things out or get them wrong; the transcript is the source.":
        "Von einer KI aus dem Transkript geschrieben. Sie kann Dinge auslassen oder falsch wiedergeben; "
        "maßgeblich ist das Transkript.",
    "This lecture has no final transcript waiting.": "Für diese Vorlesung wartet kein Transkript.",
    "The recording is finished; its transcript is made once the Mac is plugged in.":
        "Die Aufnahme ist fertig; das Transkript entsteht, sobald der Mac am Strom hängt.",
    "The recording is finished; its transcript comes later.":
        "Die Aufnahme ist fertig; das Transkript kommt später.",
    "The recording is finished and its transcript is being made now.":
        "Die Aufnahme ist fertig, und das Transkript wird gerade erstellt.",
    "Stenny is recording. The transcript appears here when it's done; Stenny's window shows the text so far.":
        "Stenny nimmt auf. Das Transkript erscheint, sobald es fertig ist; bis dahin zeigt Stennys Fenster den "
        "bisherigen Text.",
    "No transcript for {course} on {folder}.": "Kein Transkript für {course} vom {folder}.",
    "The Word file couldn't be made: {error}": "Die Word-Datei konnte nicht erstellt werden: {error}",
    "Unknown format '{fmt}': use {formats}.": "Unbekanntes Format „{fmt}“: Nimm {formats}.",

    # renaming, deleting, the course list
    "Couldn't move to the {trash}: {error}": "Konnte nicht in den {trash} gelegt werden: {error}",
    "A transcript is being made right now. Try again when it's done.":
        "Gerade wird ein Transkript erstellt. Versuch es noch mal, wenn es fertig ist.",
    "A name can't be empty.": "Der Name darf nicht leer sein.",
    "A name can't contain these characters: / \\ : # ^ [ ] | * ? \" < >. Try '{name}'.":
        "Ein Name darf diese Zeichen nicht enthalten: / \\ : # ^ [ ] | * ? \" < >. Wie wäre es mit „{name}“?",
    "No recording of {course} on {folder}.": "Keine Aufnahme von {course} vom {folder}.",
    "This recording is still running. To rename it, stop it first, or use Cancel Recording… in Stenny's menu.":
        "Diese Aufnahme läuft noch. Um sie umzubenennen, stopp sie zuerst oder wähle „Aufnahme abbrechen …“ in "
        "Stennys Menü.",
    "This recording is still running. To delete it, stop it first, or use Cancel Recording… in Stenny's menu.":
        "Diese Aufnahme läuft noch. Um sie zu löschen, stopp sie zuerst oder wähle „Aufnahme abbrechen …“ in "
        "Stennys Menü.",
    "Its transcript is being made right now. Try again when it's done.":
        "Das Transkript wird gerade erstellt. Versuch es noch mal, wenn es fertig ist.",
    "That title is too long (120 characters at most).": "Der Titel ist zu lang (höchstens 120 Zeichen).",
    "Moved to the {trash}: {name}, {folder}.": "In den {trash} gelegt: {name}, {folder}.",
    "'{name}' isn't written as its own table in the config.":
        "„{name}“ steht in der Konfiguration nicht als eigene Tabelle.",
    "There's already a talk called '{name}'.": "Es gibt schon einen Vortrag namens „{name}“.",
    "There's already a course called '{name}'.": "Es gibt schon einen Kurs namens „{name}“.",
    "{name} is recording right now. Rename it when the recording is done.":
        "{name} wird gerade aufgenommen. Umbenennen kannst du, sobald die Aufnahme fertig ist.",
    "{name} is recording right now. Delete it when the recording is done.":
        "{name} wird gerade aufgenommen. Löschen kannst du, sobald die Aufnahme fertig ist.",
    "There's no talk called '{name}'.": "Es gibt keinen Vortrag namens „{name}“.",
    "There's no course called '{name}'.": "Es gibt keinen Kurs namens „{name}“.",
    "Moved {name} to the {trash}.": "{name} in den {trash} gelegt.",
    "Course '{name}' isn't written as its own table in the config.":
        "Der Kurs „{name}“ steht in der Konfiguration nicht als eigene Tabelle.",
    "A course needs a name.": "Ein Kurs braucht einen Namen.",
    "A course name can't contain these characters: / \\ : # ^ [ ] | * ? \" < >. Try '{name}'.":
        "Ein Kursname darf diese Zeichen nicht enthalten: / \\ : # ^ [ ] | * ? \" < >. Wie wäre es mit „{name}“?",
    "Unknown language '{language}': use a code such as en, de or fr, or mixed or auto.":
        "Unbekannte Sprache „{language}“: Nimm einen Code wie en, de oder fr, oder mixed bzw. auto.",
    "'{name}' is already one of your courses.": "„{name}“ ist schon einer deiner Kurse.",
    "{name} is back, with its earlier settings.": "{name} ist zurück, mit den früheren Einstellungen.",
    "The short name '{alias}' is already used by {name}.": "Den Kurznamen „{alias}“ hat schon {name}.",
    "Added {name}.": "{name} hinzugefügt.",
    "{name} is already hidden.": "{name} ist schon ausgeblendet.",
    "{name} is hidden. Its transcripts stay where they are.":
        "{name} ist ausgeblendet. Die Transkripte bleiben, wo sie sind.",
    "Unknown course '{name}'. Configured courses: {known}. Add it to {path} or pass --lang to use it ad hoc.":
        "Unbekannter Kurs „{name}“. Eingerichtete Kurse: {known}. Trag ihn in {path} ein oder nimm --lang, "
        "um ihn spontan zu verwenden.",
    "none": "keine",

    # Settings
    "Expected on or off.": "Erwartet wird an oder aus.",
    "Use one of: {options}.": "Nimm eins davon: {options}.",
    "Use a time such as 01:00.": "Nimm eine Uhrzeit wie 01:00.",
    "Pick a microphone, Automatic or Off.": "Wähle ein Mikrofon, „Automatisch“ oder „Aus“.",
    "'{key}' can't be changed here.": "„{key}“ lässt sich hier nicht ändern.",
    "Stenny is made for English, German and French so far (or mixed, or one per lecture).":
        "Stenny ist bisher für Englisch, Deutsch und Französisch gemacht (oder gemischt, oder eine pro Vorlesung).",
    "Unknown modifier in pause_shortcut '{value}': use cmd, shift, ctrl, opt.":
        "Unbekannte Sondertaste in pause_shortcut „{value}“: Nimm cmd, shift, ctrl oder opt.",
    "pause_shortcut '{value}' needs cmd, ctrl or opt, e.g. \"cmd+shift+p\".":
        "pause_shortcut „{value}“ braucht cmd, ctrl oder opt, z. B. \"cmd+shift+p\".",
    "pause_shortcut '{value}': the key must be one character, space or f1–f20.":
        "pause_shortcut „{value}“: Die Taste muss ein einzelnes Zeichen sein, space oder f1–f20.",
    "No config at {path}. Run `stenny init` to create one.":
        "Keine Konfiguration unter {path}. Leg mit `stenny init` eine an.",
    "Set `vault` in {path} to the folder your transcripts go to (e.g. your Obsidian vault).":
        "Trag in {path} bei `vault` den Ordner ein, in den deine Transkripte sollen (z. B. deinen Obsidian-Vault).",
    "Missing required key {key} in {path}": "Pflichtfeld {key} fehlt in {path}",
    "[app] language '{value}': use system, en or de.":
        "[app] language „{value}“: Nimm system, en oder de.",

    # recording
    "A recording is already running.": "Es läuft bereits eine Aufnahme.",
    "Start either a course or a talk.": "Starte entweder einen Kurs oder einen Vortrag.",
    "Unknown command '{cmd}'.": "Unbekannter Befehl „{cmd}“.",
    "No recording is running.": "Gerade läuft keine Aufnahme.",
    "Switching the microphone didn't work.": "Das Mikrofon ließ sich nicht wechseln.",
    "Consent needs 'use' (record or share) and 'course'.":
        "Die Zustimmung braucht „use“ (record oder share) und „course“.",
    "Disk almost full": "Speicherplatz fast voll",
    "Memory is low: recording goes on, transcription paused":
        "Wenig Arbeitsspeicher: Die Aufnahme läuft weiter, das Transkribieren pausiert",
    "stopped early: the disk is full": "vorzeitig gestoppt: Der Speicherplatz ist voll",
    "final pass queued (as requested)": "Transkript wartet (wie gewünscht)",
    "final pass queued (memory is low)": "Transkript wartet (wenig Arbeitsspeicher)",
    "final pass queued (on battery)": "Transkript wartet (Akkubetrieb)",
    "final pass queued (another one is running)": "Transkript wartet (ein anderes läuft gerade)",
    "final pass queued (unplugged)": "Transkript wartet (Netzteil getrennt)",

    # feedback and updates
    "Write a few words first.": "Schreib erst ein paar Worte.",
    "The feedback didn't go through.": "Das Feedback ist nicht angekommen.",
    "Couldn't reach stenny.eu. Check your internet connection and try again.":
        "stenny.eu war nicht erreichbar. Prüf deine Internetverbindung und versuch es noch mal.",
    "Couldn't check for updates. Check your internet connection and try again.":
        "Updates konnten nicht geprüft werden. Prüf deine Internetverbindung und versuch es noch mal.",
}
