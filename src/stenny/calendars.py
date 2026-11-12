"""The calendar, as far as the engine sees it: calendars by link (iCal) and what the app's Calendar section shows.

A student's lectures are often in a calendar the computer doesn't know: Google Calendar or Outlook in the browser, a
university timetable. Such a calendar has an iCal link; pasted into Settings ▸ Calendar, Stenny fetches it itself, on
a Mac and on Windows. The events on now (or within 10 minutes) come out in the same shape the macOS helper sends for
the Mac's own calendars (`{title, start, end, range, id}`), so the menu's suggestion, the "Record this lecture?"
notification and the window's activity bar work the same whatever the source (desktop/src-tauri/src/menubar.rs).

- **A link is a secret** (anyone with it can read the calendar). It lives in the system's password store
  (keychain.py), one entry per link; `calendars.json` in the data place only knows its id, its host and the
  calendar's name. A link never goes into a log, a message or a command line (curl reads it on stdin).
- **Fetched every few minutes** (`REFRESH`), by curl like everything else Stenny downloads. The last good copy stays in
  `Calendars/<id>.ics` in the data place, so being offline for a while changes nothing. A link that stopped working
  (deleted, reset) is "broken"; one that can't be reached for longer than `PATIENCE` is "unreachable".
- **Repeating events** (a lecture every Monday) are expanded by `recurring-ical-events`.
- **Only titles and times are read**, nothing is changed, and nothing leaves the computer: a link is only fetched from
  where it points.

On a Mac the helper (app/Sources/stenny-agent) writes what macOS knows into `calendar-mac.json`: whether Stenny may
read the calendar and send notifications, and the Mac's accounts with this term's event titles. `status` reads it.
`calendar-titles.json` (this term's titles, for setup's course suggestions) is put together here from both.
"""

import hashlib
import json
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from . import keychain, paths
from .config import course_for_title
from .i18n import tr

LEAD = 10 * 60             # a lecture is offered this long before it starts, and until it ends (as the Mac helper)
MIN_LENGTH = 20 * 60       # shorter events (deadlines, reminders) are never lectures
TERM = (-28, 56)           # "this term": four weeks back, eight ahead (days)
REFRESH = 5 * 60           # a link is fetched again when its copy is older
RETRY = 60                 # … or this long after a fetch that failed
PATIENCE = 3600            # offline for less than this is no reason to say anything
TIMEOUT = 15               # seconds per fetch
MAX_BYTES = 20_000_000
MAX_LINK = 1000            # characters; Windows' Credential Manager holds 1280 per entry
MAX_LINKS = 10

LINKS_FILE = "calendars.json"
MAC_FILE = "calendar-mac.json"
TITLES_FILE = "calendar-titles.json"
COPIES = "Calendars"

PROVIDERS = (("google", ("calendar.google.com", "www.google.com")),
             ("outlook", ("outlook.live.com", "outlook.office365.com", "outlook.office.com")),
             ("icloud", ("icloud.com",)))

_lock = threading.RLock()            # one fetch, one change of calendars.json at a time
_parsed: dict[str, tuple] = {}       # id → (digest, the parsed calendar)
_near: dict[str, tuple] = {}         # id → (digest, from, until, the occurrences in that span)
_term: dict[str, tuple] = {}         # id → (digest, day, {title: count})


class CalendarError(Exception):
    """A message for the person using the app."""


# ---------------------------------------------------------------- the list of links

def _file():
    return paths.DATA_DIR / LINKS_FILE


def _copy(link_id: str):
    return paths.DATA_DIR / COPIES / f"{link_id}.ics"


def _account(link_id: str) -> str:
    return f"calendar-link-{link_id}"


def _load() -> list[dict]:
    try:
        links = json.loads(_file().read_text(encoding="utf-8"))["links"]
        return [x for x in links if isinstance(x, dict) and isinstance(x.get("id"), str)]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _save(links: list[dict]) -> None:
    _write(_file(), json.dumps({"links": links}, ensure_ascii=False, indent=2) + "\n")


def clean_link(text: str) -> str:
    """The pasted link as curl gets it: `webcal://` is https, and it must be a plain http(s) address."""
    link = (text or "").strip()
    if link.lower().startswith("webcal://"):
        link = "https://" + link[len("webcal://"):]
    parts = urlsplit(link)
    if (parts.scheme not in ("http", "https") or not parts.hostname or len(link) > MAX_LINK
            or re.search(r"[\s\"\\<>\x00-\x1f]", link)):
        raise CalendarError(tr("That isn't a calendar link. It starts with https:// (or webcal://) and usually ends in .ics."))
    return link


def provider(host: str) -> str | None:
    host = host.lower()
    return next((name for name, hosts in PROVIDERS if any(host == h or host.endswith("." + h) for h in hosts)), None)


# ---------------------------------------------------------------- fetching

def _download(link: str) -> tuple[int, bytes]:
    """(HTTP status, body); status 0: the server couldn't be reached. The link goes to curl on stdin, so it never
    shows in the list of running programs. curl, not urllib: uv's Python doesn't reliably find macOS's certificates
    (update.py); Windows 10 and 11 come with a curl.exe of their own."""
    curl = "curl.exe" if sys.platform == "win32" else "/usr/bin/curl"
    try:
        p = subprocess.run([curl, "-sS", "-L", "--max-time", str(TIMEOUT), "--max-filesize", str(MAX_BYTES),
                            "--proto", "=http,https", "--proto-redir", "=http,https", "-A", "Stenny",
                            "-w", "\n%{http_code}", "--config", "-"],
                           input=f'url = "{link}"\n'.encode(), capture_output=True, timeout=TIMEOUT + 10)
    except (OSError, subprocess.SubprocessError):
        return 0, b""
    body, _, code = p.stdout.rpartition(b"\n")
    if p.returncode != 0 or not code.strip().isdigit():
        return 0, b""
    return int(code), body


def _digest(body: bytes) -> str:
    """The same for the same calendar: Google stamps every event with the time of the download (DTSTAMP)."""
    return hashlib.sha1(re.sub(rb"(?m)^DTSTAMP[:;].*\r?\n", b"", body)).hexdigest()


def _parse(body: bytes):
    """The calendar in an iCal file; CalendarError if it isn't one."""
    try:
        from icalendar import Calendar
    except ImportError:
        raise CalendarError(tr("Stenny's engine is missing a part for calendar links. Update Stenny, then try again.")) from None
    if b"BEGIN:VCALENDAR" not in body[:4096].upper():
        raise CalendarError(tr("This link doesn't lead to a calendar. Copy the address in iCal format (it usually ends in .ics), not the calendar's web page."))
    try:
        return Calendar.from_ical(body)
    except Exception:  # noqa: BLE001 — whatever the parser makes of a broken file
        raise CalendarError(tr("The calendar behind this link couldn't be read.")) from None


def _fetch(link: str) -> tuple[bytes | None, str | None]:
    """(the calendar, None), or (None, what's wrong): "gone" (the link doesn't exist any more), "not_calendar",
    "unreachable" (no connection, or the server has a problem right now)."""
    code, body = _download(link)
    if code in (401, 403, 404, 410):
        return None, "gone"
    if code != 200:
        return None, "unreachable"
    try:
        _parse(body)
    except CalendarError:
        return None, "not_calendar"
    return body, None


def _calendar(link_id: str, digest: str):
    cached = _parsed.get(link_id)
    if cached and cached[0] == digest:
        return cached[1]
    try:
        cal = _parse(_copy(link_id).read_bytes())
    except (OSError, CalendarError):
        return None
    _parsed[link_id] = (digest, cal)
    return cal


def refresh(now: float | None = None) -> list[dict]:
    """Fetches every link whose copy is older than REFRESH; the list as it is afterwards."""
    now = time.time() if now is None else now
    with _lock:
        links = _load()
        changed = False
        for link in links:
            last = max(link.get("last_ok") or 0, link.get("last_try") or 0)
            wait = RETRY if link.get("problem") == "unreachable" else REFRESH
            if now - last < wait:
                continue
            address = keychain.get_secret(_account(link["id"]))
            if not address:
                link.update(problem="gone", last_try=now)  # the password store lost it: it has to be pasted again
                changed = True
                continue
            body, problem = _fetch(address)
            link["last_try"] = now
            link["problem"] = problem
            if body is not None:
                digest = _digest(body)
                if digest != link.get("digest") or not _copy(link["id"]).exists():
                    path = _copy(link["id"])
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(body)
                    link["digest"] = digest
                    link["name"] = _name(_calendar(link["id"], digest)) or link.get("name")
                link["last_ok"] = now
            changed = True
        if changed:
            _save(links)
            write_titles(links)
        return links


def _name(cal) -> str | None:
    try:
        name = str(cal.get("X-WR-CALNAME") or "").strip() if cal is not None else ""
    except Exception:  # noqa: BLE001
        name = ""
    return name[:80] or None


# ---------------------------------------------------------------- events

def _occurrences(cal, start: datetime, end: datetime) -> list[dict]:
    """The timed events between two moments, repeating ones expanded: [{title, start, end, uid}], start and end in
    seconds since 1970. All-day and cancelled events are left out, like events too short for a lecture."""
    import recurring_ical_events
    found = []
    try:
        events = recurring_ical_events.of(cal, components=["VEVENT"]).between(start, end)
    except Exception:  # noqa: BLE001 — a calendar the library can't expand counts as empty
        return found
    for e in events:
        try:
            begin, finish = e.start, e.end
            title = str(e.get("SUMMARY") or "").strip()
            if not title or not isinstance(begin, datetime) or not isinstance(finish, datetime):
                continue  # no title, or a date without a time: an all-day event
            if str(e.get("STATUS") or "").upper() == "CANCELLED":
                continue
            # a time without a zone ("floating") is the computer's local time, which is what timestamp() takes it for
            a, b = begin.timestamp(), finish.timestamp()
        except Exception:  # noqa: BLE001 — one odd event doesn't hide the others
            continue
        if b - a >= MIN_LENGTH:
            found.append({"title": title, "start": a, "end": b, "uid": str(e.get("UID") or title)})
    return found


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts).astimezone()


def _near_occurrences(link: dict, now: float) -> list[dict]:
    """Today's occurrences of a link's calendar, expanded once per copy (and again before the span runs out), so the
    look every 30 seconds costs nothing."""
    digest = link.get("digest")
    if not digest:
        return []
    cached = _near.get(link["id"])
    if cached and cached[0] == digest and cached[1] <= now - 12 * 3600 and now + LEAD <= cached[2]:
        return cached[3]
    cal = _calendar(link["id"], digest)
    if cal is None:
        return []
    start, end = now - 18 * 3600, now + 24 * 3600
    found = _occurrences(cal, _local(start), _local(end))
    _near[link["id"]] = (digest, start, end, found)
    return found


def upcoming(now: float | None = None) -> list[dict]:
    """The events of all links on now or starting within LEAD, earliest first, as the Mac helper's "events" line."""
    now = time.time() if now is None else now
    rows, seen = [], set()
    links = refresh(now)
    _titles_after_mac(links, now)
    for link in links:
        if link.get("problem") in ("gone", "not_calendar"):
            continue  # a broken link's old copy isn't today's timetable any more
        for o in _near_occurrences(link, now):
            key = (o["title"], round(o["start"]))
            if o["end"] > now and o["start"] <= now + LEAD and key not in seen:
                seen.add(key)
                a, b = datetime.fromtimestamp(o["start"]), datetime.fromtimestamp(o["end"])
                rows.append({"title": o["title"], "start": o["start"], "end": o["end"],
                             "range": f"{a:%H:%M}–{b:%H:%M}", "id": f"link-{o['uid']}-{round(o['start'])}"})
    return sorted(rows, key=lambda r: r["start"])


def _term_titles(link: dict, now: float) -> dict[str, int]:
    """This term's event titles of a link's calendar, with how often each comes up."""
    digest = link.get("digest")
    if not digest:
        return {}
    day = datetime.fromtimestamp(now).date().isoformat()
    cached = _term.get(link["id"])
    if cached and cached[:2] == (digest, day):
        return cached[2]
    cal = _calendar(link["id"], digest)
    counts: dict[str, int] = {}
    if cal is not None:
        here = _local(now)
        for o in _occurrences(cal, here + timedelta(days=TERM[0]), here + timedelta(days=TERM[1])):
            counts[o["title"]] = counts.get(o["title"], 0) + 1
    _term[link["id"]] = (digest, day, counts)
    return counts


# ---------------------------------------------------------------- what the Mac knows (written by stenny-agent)

STATES = ("allowed", "denied", "ask")


def _mac() -> dict | None:
    """The helper's note, or None on Windows. "unknown": no helper has written one (a development build)."""
    if sys.platform != "darwin":
        return None
    try:
        note = json.loads((paths.DATA_DIR / MAC_FILE).read_text(encoding="utf-8"))
        sources = [s for s in note.get("sources", []) if isinstance(s, dict) and s.get("name")]
    except (OSError, ValueError, AttributeError):
        note, sources = {}, []
    state = lambda key: note.get(key) if note.get(key) in STATES else "unknown"  # noqa: E731
    return {"access": state("access"), "notifications": state("notifications"), "sources": sources}


def _source_titles(source: dict) -> dict[str, int]:
    titles = {}
    for row in source.get("titles") or []:
        if isinstance(row, dict) and isinstance(row.get("title"), str):
            titles[row["title"]] = int(row.get("count") or 0)
    return titles


_mac_seen = [0.0]  # the helper's note as of the last calendar-titles.json (its modification time)


def _titles_after_mac(links: list[dict], now: float) -> None:
    """calendar-titles.json again once the Mac's helper has written a new note (Stenny opened, access was given, an
    account was added), so setup finds the titles without anyone opening Settings."""
    try:
        changed = (paths.DATA_DIR / MAC_FILE).stat().st_mtime
    except OSError:
        return
    if changed != _mac_seen[0]:
        _mac_seen[0] = changed
        write_titles(links, now)


def write_titles(links: list[dict] | None = None, now: float | None = None) -> None:
    """calendar-titles.json: this term's event titles from every calendar Stenny sees, most frequent first, so setup
    can suggest the courses. A lecture that is in two of them (the Mac's Google account and its link) counts once."""
    now = time.time() if now is None else now
    counts: dict[str, int] = {}
    mac = _mac()
    groups = [_source_titles(s) for s in (mac["sources"] if mac and mac["access"] == "allowed" else [])]
    groups += [_term_titles(link, now) for link in (_load() if links is None else links)]
    for titles in groups:
        for title, n in titles.items():
            counts[title] = max(counts.get(title, 0), n)
    rows = [{"title": t, "count": n} for t, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]
    text = json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    path = paths.DATA_DIR / TITLES_FILE
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        if not rows:
            return  # nothing to say and nothing to replace
    _write(path, text)


# ---------------------------------------------------------------- the Calendar section

def _lectures(cfg, titles: dict[str, int]) -> int:
    return sum(n for title, n in titles.items() if course_for_title(cfg.visible_courses, title))


def status(cfg, now: float | None = None) -> dict:
    """What Settings ▸ Calendar and the setup card show. Counts and names only, never a list of events:
    `sources` are the Mac's accounts, `links` the pasted ones, each with how many of this term's events belong to a
    course. `connected`: Stenny can read at least one calendar. Nothing is fetched here (the app does that every
    30 seconds through `upcoming`); a link's state is the one of its last fetch."""
    now = time.time() if now is None else now
    mac = _mac()
    links = _load()
    write_titles(links, now)
    seen: dict[str, int] = {}
    sources = []
    for s in (mac["sources"] if mac and mac["access"] == "allowed" else []):
        titles = _source_titles(s)
        sources.append({"name": str(s["name"]), "calendars": int(s.get("calendars") or 0),
                        "lectures": _lectures(cfg, titles)})
        for title, n in titles.items():
            seen[title] = max(seen.get(title, 0), n)
    rows = []
    for link in links:
        titles = _term_titles(link, now)
        problem = link.get("problem")
        last_ok = link.get("last_ok")
        if problem in ("gone", "not_calendar"):
            state = "broken"
        elif problem == "unreachable" and now - (last_ok or 0) > PATIENCE:
            state = "unreachable"
        else:
            state = "ok"
        rows.append({"id": link["id"], "name": link.get("name"), "host": link.get("host"),
                     "provider": link.get("provider"), "state": state, "last_ok": last_ok,
                     "events": sum(titles.values()), "lectures": _lectures(cfg, titles)})
        if state != "broken":
            for title, n in titles.items():
                seen[title] = max(seen.get(title, 0), n)
    working = any(r["state"] != "broken" for r in rows)
    return {"platform": "mac" if sys.platform == "darwin" else "windows" if sys.platform == "win32" else "other",
            "access": mac["access"] if mac else None,
            "notifications": mac["notifications"] if mac else None,  # Windows: the app fills it in itself
            "sources": sources, "links": rows,
            "lectures": _lectures(cfg, seen),
            "connected": working or bool(mac and mac["access"] == "allowed"),
            "reminders": bool(getattr(cfg, "reminders", True))}


def add_link(cfg, text: str, now: float | None = None) -> dict:
    """A pasted link: fetched once to see that it is a calendar, then kept. The section's status again."""
    now = time.time() if now is None else now
    link = clean_link(text)
    host = urlsplit(link).hostname or ""
    with _lock:
        links = _load()
        if any(keychain.get_secret(_account(x["id"])) == link for x in links):
            raise CalendarError(tr("Stenny already has this calendar link."))
        if len(links) >= MAX_LINKS:
            raise CalendarError(tr("That's more calendar links than Stenny keeps. Remove one first."))
        code, body = _download(link)
        if code in (401, 403, 404, 410):
            if provider(host) == "google":
                raise CalendarError(tr("Google says this calendar isn't there or isn't shared. Use the “Secret address in iCal format”, not the public one."))
            raise CalendarError(tr("The calendar's server says this link doesn't exist (any more). Copy it again from your calendar's settings."))
        if code != 200:
            raise CalendarError(tr("Stenny couldn't reach {host}. Check your internet connection and the link, then try again.", host=host))
        cal = _parse(body)
        link_id = secrets.token_hex(4)
        try:
            keychain.save_secret(_account(link_id), link, "Stenny: calendar link")
        except keychain.KeychainError as e:
            raise CalendarError(tr("The link couldn't be saved in your computer's password store: {error}", error=e)) from None
        path = _copy(link_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        digest = _digest(body)
        _parsed[link_id] = (digest, cal)
        links.append({"id": link_id, "host": host, "provider": provider(host), "name": _name(cal), "added": now,
                      "last_ok": now, "last_try": now, "problem": None, "digest": digest})
        _save(links)
    return status(cfg, now)


def remove_link(cfg, link_id: str) -> dict:
    """A link forgotten: its entry in the password store, its copy and its line in the list."""
    with _lock:
        links = _load()
        if not any(x["id"] == link_id for x in links):
            raise CalendarError(tr("Stenny doesn't have this calendar link any more."))
        try:
            keychain.delete_secret(_account(link_id))
        except keychain.KeychainError:
            pass  # without its line in the list it is never read again
        _copy(link_id).unlink(missing_ok=True)
        for cache in (_parsed, _near, _term):
            cache.pop(link_id, None)
        _save([x for x in links if x["id"] != link_id])
    return status(cfg)
