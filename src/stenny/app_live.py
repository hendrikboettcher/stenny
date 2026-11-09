"""Recording, for Stenny's app (through `stenny app-server`, see app_server.py).

The recording itself is `stenny live` in its own process (it outlives the app if the app quits or crashes, and
`stenny recover` picks it up if it dies). The app starts it here and then only talks to it through its control
socket (live/control.py): status, pause, resume, stop, microphone.
"""

import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from . import i18n, paths
from .i18n import tr
from .live import control

_started: dict = {}  # the `stenny live` this server started: {"process": Popen, "course": …}


class LiveError(Exception):
    """A message for the person using the app."""


def status(cfg, params: dict) -> dict:
    """The running recording's status (state, course, elapsed_s, paused_s, words, microphone, warning …), or
    {"state": "starting"} while a start is under way, or {"state": "idle"} (with "error" if a start failed)."""
    try:
        return control.send("status", timeout=3)
    except (control.NoSession, OSError, ValueError):
        pass
    proc = _started.get("process")
    if proc is not None:
        if proc.poll() is None:
            return {"ok": True, "state": "starting", "course": _started["course"]}
        _started.clear()
        if proc.returncode:  # it ended before its socket came up: say why
            return {"ok": True, "state": "idle", "error": last_log_line() or f"exit status {proc.returncode}"}
    return {"ok": True, "state": "idle", **waiting()}


def waiting() -> dict:
    """Final passes in the queue, and whether the Mac is plugged in (they run only then): for the menu's
    "Ready · …" line after a recording."""
    from . import jobs
    try:
        queued = len(jobs.pending())
    except OSError:
        queued = 0
    return {"queued": queued, "on_power": jobs.on_ac_power()} if queued else {"queued": 0}


def last_log_line() -> str | None:
    try:
        lines = [l.strip() for l in (paths.LOGS_DIR / "live.log").read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return None
    return lines[-1] if lines else None


def start(cfg, params: dict) -> dict:
    """Starts `stenny live` for a course (`course`) or a talk (`event`: its title). `device`: the microphone
    (default: the system's); `lang`: this recording's language (en, de, fr or mixed; default: the course's, and
    mixed for a talk); `draft`: live text while recording (default on); `file`/`speed`: play an audio file
    instead of the microphone (for testing). Its output goes to live.log, like the Swift app's."""
    if status(cfg, {})["state"] not in ("idle",):
        raise LiveError(tr("A recording is already running."))
    course, event = params.get("course"), params.get("event")
    if bool(course) == bool(event):
        raise LiveError(tr("Start either a course or a talk."))
    args = [sys.executable, "-m", "stenny", "live"] + (["--event", event] if event else ["--course", course])
    if params.get("device"):
        args += ["--device", params["device"]]
    if params.get("lang"):
        if params["lang"] not in ("en", "de", "fr", "mixed"):
            raise LiveError(tr("Stenny is made for English, German and French so far."))
        args += ["--lang", params["lang"]]
    if params.get("draft") is False:
        args.append("--no-draft")
    if params.get("llm") is False:
        args.append("--no-llm")
    if params.get("file"):
        args += ["--file", str(params["file"]), "--speed", str(params.get("speed", 1.0))]
    paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log = open(paths.LOGS_DIR / "live.log", "w", encoding="utf-8")
    # its warnings and notes show in the app's menu: in the app's language
    env = {**os.environ, "STENNY_LANGUAGE": i18n.language()}
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, env=env)
    log.close()
    _started.update(process=proc, course=event or course)
    return {"ok": True, "state": "starting", "course": event or course}


def command(cfg, params: dict) -> dict:
    """pause, resume, toggle, stop or cancel (no transcript, files to the Trash) the running recording."""
    cmd = params.get("cmd")
    if cmd not in ("pause", "resume", "toggle", "stop", "cancel"):
        raise LiveError(tr("Unknown command '{cmd}'.", cmd=cmd))
    try:
        return control.send(cmd)
    except control.NoSession:
        raise LiveError(tr("No recording is running.")) from None


def microphone(cfg, params: dict) -> dict:
    """Switches the running recording to another microphone."""
    try:
        reply = control.send("microphone", device=params.get("device"))
    except control.NoSession:
        raise LiveError(tr("No recording is running.")) from None
    if not reply.get("ok"):
        raise LiveError(reply.get("error") or tr("Switching the microphone didn't work."))
    return reply


def devices(cfg, params: dict) -> dict:
    from .live.sources import default_input_device, input_devices
    return {"devices": input_devices(), "default": default_input_device()}


def settings(cfg, params: dict) -> dict:
    """What the app reads at launch: the pause shortcut (as text, like "cmd+shift+ü") and the courses to start."""
    return {"pause_shortcut": cfg.pause_shortcut, "data_dir": str(paths.DATA_DIR), "logs_dir": str(paths.LOGS_DIR),
            "language": i18n.resolve(cfg.app_language),  # the app's own language: "en" or "de"
            "courses": [c.name for c in cfg.courses.values() if not c.hidden]}


def match_courses(cfg, params: dict) -> dict:
    """Which course each calendar event title belongs to (None: none), by the same rules as everywhere
    (config.course_for_title): {"Calculus I – Lecture": "Calculus", "Dentist": None}."""
    from .config import course_for_title
    return {t: (c := course_for_title(cfg.visible_courses, t)) and c.name for t in params.get("titles", [])}


def recover(cfg, params: dict) -> dict:
    """Recordings whose process died: their recorder is stopped and their final pass queued."""
    from .cli import recover_sessions
    return {"recovered": recover_sessions(cfg)}


# ---------------------------------------------------------------- feedback and updates

FEEDBACK_URL = "https://stenny.eu/api/feedback"  # the same endpoint serves the website's form


def system_details() -> str:
    """The computer's model, memory and system version, for feedback: "Mac16,12, 16 GB, macOS 15.5" or
    "LENOVO 21HD, 16 GB, Windows 11 (10.0.22631)"."""
    import platform
    if sys.platform == "win32":
        from . import winsys
        model = platform.machine()
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS") as key:
                model = " ".join(str(winreg.QueryValueEx(key, name)[0]).strip()
                                 for name in ("SystemManufacturer", "SystemProductName")) or model
        except (OSError, ImportError):
            pass
        status = winsys.memory()
        release, build = platform.win32_ver()[:2]
        return f"{model}, {(status.ullTotalPhys if status else 0) >> 30} GB, Windows {release} ({build})"
    model = subprocess.run(["sysctl", "-n", "hw.model"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    memory = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout or 0) >> 30
    return f"{model}, {memory} GB, macOS {platform.mac_ver()[0]}"


def send_feedback(cfg, params: dict) -> dict:
    """Posts the message (and, if the user ticked the box, Stenny's version, the computer's model and the last lines
    of Stenny's log) to stenny.eu, like the Swift app did."""
    from importlib.metadata import version

    message = (params.get("message") or "").strip()
    if not message:
        raise LiveError(tr("Write a few words first."))
    body = {"source": "app", "message": message, "contact": (params.get("contact") or "").strip()}
    if params.get("details"):
        try:
            log = (paths.LOGS_DIR / "stenny.log").read_text(encoding="utf-8").splitlines()[-40:]
        except OSError:
            log = []
        body.update(version=version("stenny"), system=system_details(), log="\n".join(log))
    # curl, not urllib: uv's Python doesn't reliably find macOS's certificates (see update.py); Windows 10 and 11
    # come with a curl.exe of their own
    curl = "curl.exe" if sys.platform == "win32" else "/usr/bin/curl"
    try:
        done = subprocess.run([curl, "-sS", "--max-time", "20", "--fail-with-body", "-X", "POST",
                               "-H", "Content-Type: application/json", "--data-binary", "@-", FEEDBACK_URL],
                              input=json.dumps(body), capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        done = None
    if done is not None and done.returncode == 0:
        return {"sent": True}
    if done is not None and done.returncode == 22:  # the server answered with an error
        try:
            reason = json.loads(done.stdout or "{}").get("error")
        except ValueError:
            reason = None
        raise LiveError(reason or tr("The feedback didn't go through."))
    raise LiveError(tr("Couldn't reach stenny.eu. Check your internet connection and try again."))


def check_update(cfg, params: dict) -> dict:
    """{"current", "latest", "update_available"} from GitHub's releases (update.check)."""
    from . import update
    try:
        return update.check()
    except Exception as e:  # noqa: BLE001 — no internet, GitHub down
        raise LiveError(tr("Couldn't check for updates. Check your internet connection and try again.")) from e


# ---------------------------------------------------------------- "Did the lecturer agree?"

CONSENT = "consent.json"


def _consent_path() -> Path:
    return paths.CONFIG_DIR / CONSENT


def _consent() -> set[str]:
    """What the user confirmed, as "record/course/<name>" and "share/event/<title>". On first use it takes over
    the answers given in the Swift app (its defaults, `lecturerAgreed`)."""
    try:
        return set(json.loads(_consent_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    try:
        out = subprocess.run(["defaults", "export", "app.stenny", "-"], capture_output=True, timeout=5).stdout
        earlier = set(plistlib.loads(out).get("lecturerAgreed", [])) if out else set()
    except (OSError, ValueError, subprocess.SubprocessError, plistlib.InvalidFileException):
        earlier = set()
    _save_consent(earlier)
    return earlier


def _save_consent(given: set[str]) -> None:
    paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _consent_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(given), ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_consent_path())


def _consent_id(params: dict) -> str:
    use = params.get("use")
    if use not in ("record", "share") or not params.get("course"):
        raise LiveError(tr("Consent needs 'use' (record or share) and 'course'."))
    return f"{use}/{'event' if params.get('event') else 'course'}/{params['course']}"


def consent(cfg, params: dict) -> dict:
    """Whether the user already confirmed the lecturer (or speaker) agreed, for this use and course."""
    return {"given": _consent_id(params) in _consent()}


def give_consent(cfg, params: dict) -> dict:
    given = _consent()
    given.add(_consent_id(params))
    _save_consent(given)
    return {"given": True}


METHODS = {"status": status, "start": start, "command": command, "microphone": microphone, "devices": devices,
           "settings": settings, "match_courses": match_courses, "recover": recover, "consent": consent,
           "give_consent": give_consent, "send_feedback": send_feedback, "check_update": check_update}
