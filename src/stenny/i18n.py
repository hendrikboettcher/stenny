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
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True, encoding="utf-8", errors="replace",
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
        text = (DE_WINDOWS.get(text) if sys.platform == "win32" else None) or DE.get(text, text)
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
    "Talks have no glossary.": "Vorträge haben kein Glossar.",
    "Stenny can only remember the spelling of a name or term, a few words long.":
        "Stenny kann sich nur die Schreibweise eines Namens oder Begriffs merken, ein paar Wörter lang.",
    "Unknown method '{name}'.": "Unbekannte Methode „{name}“.",
    "Not JSON.": "Kein JSON.",
    "Something went wrong ({error}).": "Da ist etwas schiefgelaufen ({error}).",
    "Stenny's settings couldn't be read: {error}": "Stennys Einstellungen konnten nicht gelesen werden: {error}",
    "Stenny is made for English, German and French so far.":
        "Stenny unterstützt bisher Englisch, Deutsch und Französisch.",

    # the library window
    "Written by AI from the transcript. It can leave things out or get them wrong; the transcript is the source.":
        "Von einer KI aus dem Transkript erstellt. Sie kann Dinge weglassen oder falsch wiedergeben – maßgeblich ist das Transkript.",
    "This lecture has no final transcript waiting.": "Für diese Vorlesung steht kein Transkript aus.",
    "The recording is finished; its transcript is made once the Mac is plugged in.":
        "Aufnahme abgeschlossen. Das Transkript wird erstellt, sobald dein Mac am Netzteil ist.",
    "The recording is finished; its transcript is made once the PC is plugged in.":
        "Aufnahme abgeschlossen. Das Transkript wird erstellt, sobald dein PC am Netzteil ist.",
    "The recording is finished; its transcript comes later.":
        "Aufnahme abgeschlossen. Das Transkript folgt später.",
    "The recording is finished and its transcript is being made now.":
        "Aufnahme abgeschlossen. Das Transkript wird gerade erstellt.",
    "Stenny is recording. The full transcript is made once the recording ends.":
        "Stenny nimmt gerade auf. Das vollständige Transkript entsteht, sobald die Aufnahme endet.",
    "No transcript for {course} on {folder}.": "Zu {course} gibt es am {folder} kein Transkript.",
    "The Word file couldn't be made: {error}": "Die Word-Datei konnte nicht erstellt werden: {error}",
    "Unknown format '{fmt}': use {formats}.": "Unbekanntes Format „{fmt}“. Möglich sind: {formats}.",

    # renaming, deleting, the course list
    "Couldn't move to the {trash}: {error}": "Konnte nicht in den {trash} gelegt werden: {error}",
    "A transcript is being made right now. Try again when it's done.":
        "Gerade wird ein Transkript erstellt. Bitte versuch es noch einmal, wenn es fertig ist.",
    "A name can't be empty.": "Bitte gib einen Namen ein.",
    "A name can't contain these characters: / \\ : # ^ [ ] | * ? \" < >. Try '{name}'.":
        "Diese Zeichen sind in Namen nicht erlaubt: / \\ : # ^ [ ] | * ? \" < >. Vorschlag: „{name}“.",
    "No recording of {course} on {folder}.": "Zu {course} gibt es am {folder} keine Aufnahme.",
    "This recording is still running. To rename it, stop it first, or use Cancel Recording… in Stenny's menu.":
        "Diese Aufnahme läuft noch. Beende sie zuerst, um sie umzubenennen – oder wähle „Aufnahme abbrechen …“ im Stenny-Menü.",
    "This recording is still running. To delete it, stop it first, or use Cancel Recording… in Stenny's menu.":
        "Diese Aufnahme läuft noch. Beende sie zuerst, um sie zu löschen – oder wähle „Aufnahme abbrechen …“ im Stenny-Menü.",
    "Its transcript is being made right now. Try again when it's done.":
        "Das Transkript wird gerade erstellt. Bitte versuch es noch einmal, wenn es fertig ist.",
    "That title is too long (120 characters at most).": "Der Titel ist zu lang – höchstens 120 Zeichen.",
    "Moved to the {trash}: {name}, {folder}.": "In den {trash} gelegt: {name}, {folder}.",
    "'{name}' isn't written as its own table in the config.":
        "„{name}“ hat in der Konfiguration keinen eigenen Abschnitt.",
    "There's already a talk called '{name}'.": "Es gibt bereits einen Vortrag namens „{name}“.",
    "There's already a course called '{name}'.": "Es gibt bereits einen Kurs namens „{name}“.",
    "{name} is recording right now. Rename it when the recording is done.":
        "{name} wird gerade aufgenommen. Umbenennen geht, sobald die Aufnahme beendet ist.",
    "{name} is recording right now. Delete it when the recording is done.":
        "{name} wird gerade aufgenommen. Löschen geht, sobald die Aufnahme beendet ist.",
    "There's no talk called '{name}'.": "Es gibt keinen Vortrag namens „{name}“.",
    "There's no course called '{name}'.": "Es gibt keinen Kurs namens „{name}“.",
    "Moved {name} to the {trash}.": "{name} in den {trash} gelegt.",
    "Course '{name}' isn't written as its own table in the config.":
        "Der Kurs „{name}“ hat in der Konfiguration keinen eigenen Abschnitt.",
    "A course needs a name.": "Bitte gib dem Kurs einen Namen.",
    "A course name can't contain these characters: / \\ : # ^ [ ] | * ? \" < >. Try '{name}'.":
        "Diese Zeichen sind in Kursnamen nicht erlaubt: / \\ : # ^ [ ] | * ? \" < >. Vorschlag: „{name}“.",
    "Unknown language '{language}': use a code such as en, de or fr, or mixed or auto.":
        "Unbekannte Sprache „{language}“. Möglich sind Codes wie en, de oder fr sowie mixed oder auto.",
    "'{name}' is already one of your courses.": "„{name}“ ist bereits einer deiner Kurse.",
    "{name} is back, with its earlier settings.": "{name} ist wiederhergestellt – mit den bisherigen Einstellungen.",
    "The short name '{alias}' is already used by {name}.": "Der Kurzname „{alias}“ ist bereits für {name} vergeben.",
    "Added {name}.": "{name} hinzugefügt.",
    "{name} is already hidden.": "{name} ist bereits ausgeblendet.",
    "{name} is hidden. Its transcripts stay where they are.":
        "{name} ist ausgeblendet. Die Transkripte bleiben, wo sie sind.",
    "Unknown course '{name}'. Configured courses: {known}. Add it to {path} or pass --lang to use it ad hoc.":
        "Unbekannter Kurs „{name}“. Eingerichtete Kurse: {known}. Trag ihn in {path} ein oder gib --lang an, um ihn spontan zu verwenden.",
    "none": "keine",

    # Settings
    "Expected on or off.": "Erwartet wird „an“ oder „aus“.",
    "Use one of: {options}.": "Möglich sind: {options}.",
    "Use a time such as 01:00.": "Bitte gib eine Uhrzeit wie 01:00 an.",
    "Pick a microphone, Automatic or Off.": "Wähle ein Mikrofon, „Automatisch“ oder „Aus“.",
    "'{key}' can't be changed here.": "„{key}“ lässt sich hier nicht ändern.",
    "Stenny is made for English, German and French so far (or mixed, or one per lecture).":
        "Stenny unterstützt bisher Englisch, Deutsch und Französisch (auch gemischt oder eine Sprache pro Vorlesung).",
    "Unknown modifier in pause_shortcut '{value}': use cmd, shift, ctrl, opt.":
        "Unbekannte Sondertaste in pause_shortcut „{value}“. Möglich sind cmd, shift, ctrl und opt.",
    "pause_shortcut '{value}' needs cmd, ctrl or opt, e.g. \"cmd+shift+p\".":
        "pause_shortcut „{value}“ braucht cmd, ctrl oder opt, z. B. \"cmd+shift+p\".",
    "pause_shortcut '{value}': the key must be one character, space or f1–f20.":
        "pause_shortcut „{value}“: Die Taste muss ein einzelnes Zeichen, space oder f1–f20 sein.",
    "No config at {path}. Run `stenny init` to create one.":
        "Unter {path} gibt es keine Konfiguration. Mit `stenny init` legst du eine an.",
    "Set `folder` in {path} to the folder your transcripts go to (e.g. ~/Documents/Stenny).":
        "Trag in {path} bei `folder` den Ordner für deine Transkripte ein (z. B. ~/Documents/Stenny).",
    "A course can't be called \"{name}\": that's the folder for talks.":
        "Ein Kurs kann nicht „{name}“ heißen – so heißt der Ordner für Vorträge.",
    "Missing required key {key} in {path}": "In {path} fehlt der Pflichteintrag {key}",
    "[app] language '{value}': use system, en or de.":
        "[app] language „{value}“: Möglich sind system, en und de.",
    "[transcription] engine '{value}': use auto, mlx or faster-whisper.":
        "[transcription] engine „{value}“: Möglich sind auto, mlx und faster-whisper.",

    # recording
    "A recording is already running.": "Es läuft bereits eine Aufnahme.",
    "Start either a course or a talk.": "Starte entweder einen Kurs oder einen Vortrag.",
    "Unknown command '{cmd}'.": "Unbekannter Befehl „{cmd}“.",
    "No recording is running.": "Es läuft gerade keine Aufnahme.",
    "Switching the microphone didn't work.": "Das Mikrofon konnte nicht gewechselt werden.",
    "Consent needs 'use' (record or share) and 'course'.":
        "Die Zustimmung braucht „use“ (record oder share) und „course“.",
    "Disk almost full": "Fast kein Speicherplatz mehr",
    "Memory is low: recording goes on, transcription paused":
        "Wenig Arbeitsspeicher – Aufnahme läuft weiter, Transkription pausiert",
    "Battery is low: recording goes on, transcription paused":
        "Akku fast leer – Aufnahme läuft weiter, Transkription pausiert",
    "stopped early: the disk is full": "vorzeitig beendet – kein Speicherplatz mehr",
    "final pass queued (as requested)": "Transkript folgt später (wie gewünscht)",
    "final pass queued (memory is low)": "Transkript folgt später (wenig Arbeitsspeicher)",
    "final pass queued (on battery)": "Transkript folgt, sobald der Mac am Netzteil ist",
    "final pass queued (another one is running)": "Transkript folgt später (ein anderes läuft gerade)",
    "final pass queued (unplugged)": "Transkript folgt, sobald der Mac wieder am Netzteil ist",

    # feedback and updates
    "Write a few words first.": "Schreib bitte ein paar Worte.",
    "The feedback didn't go through.": "Das Feedback konnte nicht gesendet werden.",
    "Couldn't reach stenny.eu. Check your internet connection and try again.":
        "stenny.eu ist nicht erreichbar. Prüf bitte deine Internetverbindung und versuch es noch einmal.",
    "Couldn't check for updates. Check your internet connection and try again.":
        "Die Suche nach Updates ist fehlgeschlagen. Prüf bitte deine Internetverbindung und versuch es noch einmal.",

    # Settings ▸ Transcripts & AI and Use with Claude (claude_setup.py)
    "Anthropic couldn't be reached. Check your internet connection.":
        "Anthropic war nicht erreichbar. Prüf deine Internetverbindung.",
    "Paste your API key first.": "Füg zuerst deinen API-Schlüssel ein.",
    "That isn't an Anthropic API key: those start with sk-ant-.":
        "Das ist kein Anthropic-API-Schlüssel: Die beginnen mit sk-ant-.",
    "Anthropic didn't accept this key. Check that you copied all of it, and that it hasn't been deleted in the "
    "Claude Console.":
        "Anthropic hat diesen Schlüssel nicht angenommen. Prüf, ob du ihn vollständig kopiert hast und ob er in der "
        "Claude Console noch existiert.",
    "The key couldn't be saved: {error}": "Der Schlüssel konnte nicht gespeichert werden: {error}",
    "The key couldn't be removed: {error}": "Der Schlüssel konnte nicht entfernt werden: {error}",
    "Claude Code isn't installed yet.": "Claude Code ist noch nicht installiert.",
    "Stenny.mcpb isn't in Stenny's folder. Download Stenny again, or build it with connector/build.sh.":
        "Stenny.mcpb fehlt in Stennys Ordner. Lade Stenny neu herunter oder bau die Datei mit connector/build.sh.",
    "The Claude desktop app isn't installed. Get it at claude.ai/download, then try again.":
        "Die Claude-Desktop-App ist nicht installiert. Lade sie unter claude.ai/download herunter und versuch es dann "
        "noch mal.",
    "Claude couldn't open the connector: {error}": "Claude konnte den Connector nicht öffnen: {error}",
    "Claude Code couldn't add the connector: {error}": "Claude Code konnte den Connector nicht hinzufügen: {error}",
    "Claude Code couldn't remove the connector: {error}": "Claude Code konnte den Connector nicht entfernen: {error}",
    "Couldn't open Claude: {error}": "Claude ließ sich nicht öffnen: {error}",

    # the calendar (calendars.py)
    "That isn't a calendar link. It starts with https:// (or webcal://) and usually ends in .ics.":
        "Das ist kein Kalenderlink. Er beginnt mit https:// (oder webcal://) und endet meist auf .ics.",
    "Stenny's engine is missing a part for calendar links. Update Stenny, then try again.":
        "Stenny fehlt ein Baustein für Kalenderlinks. Aktualisiere Stenny und versuch es dann noch einmal.",
    "This link doesn't lead to a calendar. Copy the address in iCal format (it usually ends in .ics), not the calendar's web page.":
        "Dieser Link führt nicht zu einem Kalender. Kopiere die Adresse im iCal-Format (sie endet meist auf .ics), nicht die Webseite des Kalenders.",
    "The calendar behind this link couldn't be read.": "Der Kalender hinter diesem Link konnte nicht gelesen werden.",
    "Stenny already has this calendar link.": "Diesen Kalenderlink hat Stenny schon.",
    "That's more calendar links than Stenny keeps. Remove one first.":
        "Mehr Kalenderlinks kann Stenny nicht aufbewahren. Entferne zuerst einen.",
    "Google says this calendar isn't there or isn't shared. Use the “Secret address in iCal format”, not the public one.":
        "Google meldet, dass es diesen Kalender nicht gibt oder dass er nicht freigegeben ist. Nimm die „Privatadresse im iCal-Format“, nicht die öffentliche.",
    "The calendar's server says this link doesn't exist (any more). Copy it again from your calendar's settings.":
        "Der Server des Kalenders meldet, dass es diesen Link nicht (mehr) gibt. Kopiere ihn noch einmal aus den Einstellungen deines Kalenders.",
    "Stenny couldn't reach {host}. Check your internet connection and the link, then try again.":
        "Stenny konnte {host} nicht erreichen. Prüfe deine Internetverbindung und den Link und versuch es dann noch einmal.",
    "The link couldn't be saved in your computer's password store: {error}":
        "Der Link konnte nicht im Passwortspeicher deines Computers gesichert werden: {error}",
    "Stenny doesn't have this calendar link any more.": "Diesen Kalenderlink hat Stenny nicht mehr.",
}

# Where the German names the Mac and the English doesn't: the same message on Windows.
DE_WINDOWS = {
    "final pass queued (on battery)": "Transkript folgt, sobald der PC am Netzteil ist",
    "final pass queued (unplugged)": "Transkript folgt, sobald der PC wieder am Netzteil ist",
}
