"""Calendars by link and what Settings ▸ Calendar shows (calendars.py). No network: `_download` is replaced, and
the password store is a dict."""

import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

from stenny import app_server, calendars, config, keychain

LINK = "https://calendar.example.org/ical/someone/private-0123456789abcdef/basic.ics"


def ical(*events: str, name: str = "Timetable") -> bytes:
    body = "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", f"X-WR-CALNAME:{name}", *events, "END:VCALENDAR", ""])
    return body.encode()


def event(uid: str, title: str, start: datetime, minutes: int = 90, *extra: str) -> str:
    return "\r\n".join(["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{datetime.now():%Y%m%dT%H%M%S}Z",
                        f"DTSTART:{start:%Y%m%dT%H%M%S}", f"DTEND:{start + timedelta(minutes=minutes):%Y%m%dT%H%M%S}",
                        f"SUMMARY:{title}", *extra, "END:VEVENT"])


@pytest.fixture
def cal(places, monkeypatch, tmp_path):
    """A config with two courses, an empty password store and a web that serves what `web` holds."""
    store: dict[str, str] = {}
    monkeypatch.setattr(keychain, "get_secret", store.get)
    monkeypatch.setattr(keychain, "save_secret", lambda account, secret, label: store.__setitem__(account, secret))
    monkeypatch.setattr(keychain, "delete_secret", lambda account: store.pop(account, None))
    web: dict[str, tuple[int, bytes]] = {}
    asked: list[str] = []

    def download(link):
        asked.append(link)
        return web.get(link, (404, b""))

    monkeypatch.setattr(calendars, "_download", download)
    for cache in (calendars._parsed, calendars._near, calendars._term):
        cache.clear()
    calendars._mac_seen[0] = 0.0
    path = places.config_path if hasattr(places, "config_path") else tmp_path / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'folder = "{(tmp_path / "Stenny").as_posix()}"\n\n[courses."Statistics"]\nlanguage = "en"\n\n'
                    '[courses."Linear Algebra"]\nlanguage = "en"\n', encoding="utf-8")
    cfg = config.load(path)

    class Cal:
        pass

    c = Cal()
    c.cfg, c.store, c.web, c.asked = cfg, store, web, asked
    return c


def weekly(now: datetime, title="Statistics – Lecture", uid="stat", lead_minutes=5) -> str:
    """A lecture every week at this time of day, begun two weeks ago; today's starts in `lead_minutes`."""
    return event(uid, title, now + timedelta(minutes=lead_minutes) - timedelta(days=14), 90, "RRULE:FREQ=WEEKLY")


def test_a_pasted_link_is_checked_kept_secret_and_counted(cal):
    now = datetime.now().replace(microsecond=0)
    cal.web[LINK] = (200, ical(weekly(now), event("dentist", "Dentist", now + timedelta(days=1), 30)))
    status = calendars.add_link(cal.cfg, "  webcal://" + LINK[len("https://"):] + " ", now.timestamp())
    [link] = status["links"]
    assert link["state"] == "ok" and link["name"] == "Timetable" and link["host"] == "calendar.example.org"
    # this term (four weeks back to eight ahead): the two before, today's and seven more; the dentist is no course
    assert link["lectures"] == 10 and link["events"] == 11 and status["lectures"] == 10 and status["connected"]
    # the link itself is only in the password store: not in the list, not in the status
    assert list(cal.store.values()) == [LINK]
    from stenny import paths
    listed = (paths.DATA_DIR / calendars.LINKS_FILE).read_text(encoding="utf-8")
    assert "private-0123456789abcdef" not in listed + json.dumps(status)
    titles = json.loads((paths.DATA_DIR / calendars.TITLES_FILE).read_text(encoding="utf-8"))
    assert titles[0] == {"title": "Statistics – Lecture", "count": 10}


def test_a_weekly_lecture_is_offered_from_ten_minutes_before_until_it_ends(cal):
    now = datetime.now().replace(microsecond=0)
    cal.web[LINK] = (200, ical(
        weekly(now),
        event("short", "Statistics deadline", now, 10),                       # too short for a lecture
        "\r\n".join(["BEGIN:VEVENT", "UID:day", f"DTSTART;VALUE=DATE:{now:%Y%m%d}",
                     f"DTEND;VALUE=DATE:{now + timedelta(days=1):%Y%m%d}", "SUMMARY:Statistics day", "END:VEVENT"]),
        event("off", "Statistics (cancelled)", now, 90, "STATUS:CANCELLED"),
        event("later", "Linear Algebra", now + timedelta(minutes=30))))
    t = now.timestamp()
    calendars.add_link(cal.cfg, LINK, t)
    start = now + timedelta(minutes=5)
    [offered] = calendars.upcoming(t)
    assert offered["title"] == "Statistics – Lecture" and offered["start"] == start.timestamp()
    assert offered["range"] == f"{start:%H:%M}–{start + timedelta(minutes=90):%H:%M}"
    assert calendars.upcoming(t - 6 * 60) == []                                # 11 minutes before: not yet
    assert [e["title"] for e in calendars.upcoming(t + 25 * 60)] == ["Statistics – Lecture", "Linear Algebra"]
    assert [e["title"] for e in calendars.upcoming(t + 96 * 60)] == ["Linear Algebra"]  # the first one is over


def test_times_with_a_zone_and_utc_come_out_as_the_same_moment(cal):
    now = datetime.now().replace(microsecond=0)
    start = datetime.fromtimestamp(now.timestamp() + 300)
    utc = datetime.fromtimestamp(start.timestamp(), tz=timezone.utc)
    zoned = "\r\n".join(["BEGIN:VEVENT", "UID:z", f"DTSTART:{utc:%Y%m%dT%H%M%S}Z",
                         f"DTEND:{utc + timedelta(minutes=45):%Y%m%dT%H%M%S}Z", "SUMMARY:Linear Algebra", "END:VEVENT"])
    cal.web[LINK] = (200, ical(zoned))
    calendars.add_link(cal.cfg, LINK, now.timestamp())
    [offered] = calendars.upcoming(now.timestamp())
    assert offered["start"] == start.timestamp() and offered["range"].startswith(f"{start:%H:%M}")


@pytest.mark.parametrize("pasted, answer, said", [
    ("my calendar", None, "isn't a calendar link"),
    ("ftp://calendar.example.org/a.ics", None, "isn't a calendar link"),
    (LINK, (404, b""), "doesn't exist"),
    (LINK, (200, b"<html>Sign in</html>"), "doesn't lead to a calendar"),
    (LINK, (0, b""), "couldn't reach calendar.example.org"),
    ("https://calendar.google.com/calendar/ical/x/public/basic.ics", (404, b""), "Secret address"),
])
def test_what_isnt_a_working_calendar_link_is_refused_and_nothing_is_kept(cal, pasted, answer, said):
    if answer:
        cal.web[pasted] = answer
    with pytest.raises(calendars.CalendarError, match=said):
        calendars.add_link(cal.cfg, pasted)
    assert cal.store == {} and calendars.status(cal.cfg)["links"] == []


def test_the_same_link_twice_is_refused(cal):
    cal.web[LINK] = (200, ical())
    calendars.add_link(cal.cfg, LINK)
    with pytest.raises(calendars.CalendarError, match="already has"):
        calendars.add_link(cal.cfg, LINK)


def test_a_link_is_fetched_again_every_few_minutes_and_a_broken_one_says_so(cal):
    now = datetime.now().replace(microsecond=0)
    t = now.timestamp()
    cal.web[LINK] = (200, ical(weekly(now)))
    calendars.add_link(cal.cfg, LINK, t)
    calendars.upcoming(t + 60)
    assert len(cal.asked) == 1                      # its copy is fresh
    calendars.upcoming(t + calendars.REFRESH + 1)
    assert len(cal.asked) == 2
    # offline for a while: nothing to say, and the copy still knows today's lecture
    cal.web[LINK] = (0, b"")
    t2 = t + 2 * calendars.REFRESH + 2
    assert [e["title"] for e in calendars.upcoming(t2)] == ["Statistics – Lecture"]
    assert calendars.status(cal.cfg, t2)["links"][0]["state"] == "ok"
    # … tried again after a minute, and after an hour it shows, with when it last worked
    calendars.upcoming(t2 + calendars.RETRY + 1)
    assert len(cal.asked) == 4
    late = t2 + calendars.PATIENCE + calendars.REFRESH
    calendars.refresh(late)
    link = calendars.status(cal.cfg, late)["links"][0]
    assert link["state"] == "unreachable" and link["last_ok"] == t + calendars.REFRESH + 1
    # the link was reset: broken, its old copy offers nothing any more, and Stenny counts as not connected
    cal.web[LINK] = (404, b"")
    calendars.refresh(late + calendars.RETRY + 1)
    status = calendars.status(cal.cfg, late + calendars.RETRY + 2)
    assert status["links"][0]["state"] == "broken" and status["lectures"] == 0
    assert calendars.upcoming(late + calendars.RETRY + 2) == []
    if sys.platform != "darwin":
        assert not status["connected"]


def test_removing_a_link_forgets_it_everywhere(cal):
    from stenny import paths
    cal.web[LINK] = (200, ical())
    link = calendars.add_link(cal.cfg, LINK)["links"][0]
    assert (paths.DATA_DIR / calendars.COPIES / f"{link['id']}.ics").exists()
    status = calendars.remove_link(cal.cfg, link["id"])
    assert status["links"] == [] and cal.store == {}
    assert not (paths.DATA_DIR / calendars.COPIES / f"{link['id']}.ics").exists()
    with pytest.raises(calendars.CalendarError):
        calendars.remove_link(cal.cfg, link["id"])


def test_the_macs_accounts_show_with_their_matching_lectures(cal, monkeypatch):
    from stenny import paths
    monkeypatch.setattr(sys, "platform", "darwin")
    assert calendars.status(cal.cfg)["access"] == "unknown"     # no helper has written its note
    note = {"access": "allowed", "sources": [
        {"name": "Google", "calendars": 3, "titles": [{"title": "Statistics – Lecture", "count": 12},
                                                       {"title": "Football", "count": 8}]},
        {"name": "iCloud", "calendars": 1, "titles": []}]}
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (paths.DATA_DIR / calendars.MAC_FILE).write_text(json.dumps(note), encoding="utf-8")
    now = datetime.now().replace(microsecond=0)
    cal.web[LINK] = (200, ical(weekly(now), weekly(now, "Linear Algebra", "la")))
    status = calendars.add_link(cal.cfg, LINK, now.timestamp())
    assert status["access"] == "allowed" and status["connected"]
    assert status["notifications"] is None   # the app's to tell (macos_notifications.rs), not the helper's
    assert status["sources"] == [{"name": "Google", "calendars": 3, "lectures": 12},
                                 {"name": "iCloud", "calendars": 1, "lectures": 0}]
    # the statistics lecture is in the Mac's Google account and behind the link: counted once
    assert status["links"][0]["lectures"] == 20 and status["lectures"] == 22
    titles = {r["title"]: r["count"] for r in json.loads((paths.DATA_DIR / calendars.TITLES_FILE).read_text("utf-8"))}
    assert titles == {"Statistics – Lecture": 12, "Linear Algebra": 10, "Football": 8}
    # access taken away in System Settings: the Mac's calendars are gone from the list, the link stays
    (paths.DATA_DIR / calendars.MAC_FILE).write_text(json.dumps({**note, "access": "denied"}), encoding="utf-8")
    status = calendars.status(cal.cfg, now.timestamp())
    assert status["access"] == "denied" and status["sources"] == [] and status["connected"]


def test_windows_has_no_calendar_access_to_give(cal, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    status = calendars.status(cal.cfg)
    assert status["platform"] == "windows" and status["access"] is None and status["notifications"] is None
    assert not status["connected"] and status["reminders"]


def test_the_app_servers_methods(cal):
    now = datetime.now().replace(microsecond=0)
    cal.web[LINK] = (200, ical(weekly(now)))
    added = app_server.handle(cal.cfg, {"id": 1, "method": "add_calendar_link", "params": {"link": LINK}})
    assert added["result"]["links"][0]["lectures"] == 10
    events = app_server.handle(cal.cfg, {"id": 2, "method": "calendar_events"})["result"]["events"]
    assert [e["title"] for e in events] == ["Statistics – Lecture"]
    assert set(events[0]) == {"title", "start", "end", "range", "id"}       # as the Mac helper's "events" line
    refused = app_server.handle(cal.cfg, {"id": 3, "method": "add_calendar_link", "params": {"link": "nope"}})
    assert "isn't a calendar link" in refused["error"]
    assert app_server.handle(cal.cfg, {"id": 4, "method": "setup_status"})["result"]["calendar"]["connected"]
    link_id = added["result"]["links"][0]["id"]
    gone = app_server.handle(cal.cfg, {"id": 5, "method": "remove_calendar_link", "params": {"id": link_id}})
    assert gone["result"]["links"] == []


def test_reminders_can_be_switched_off(cal):
    from stenny import settings
    assert settings.get(cal.cfg)["app"]["reminders"] is True
    assert settings.change(cal.cfg, "app.reminders", False)["app"]["reminders"] is False
    assert calendars.status(config.load(cal.cfg.path))["reminders"] is False


# ---------------------------------------------------------------- the system's own tools (no stand-ins)

@pytest.fixture
def local_web():
    """A small web server on this computer: /timetable.ics is a calendar with a weekly lecture in Berlin time,
    anything else is 404. Yields its address."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    start = datetime.now(timezone.utc).replace(microsecond=0, second=0) - timedelta(days=14)
    body = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "X-WR-CALNAME:Timetable",
        "BEGIN:VEVENT", "UID:stat", f"DTSTAMP:{start:%Y%m%dT%H%M%S}Z",
        f"DTSTART;TZID=Europe/Berlin:{start.astimezone(__import__('zoneinfo').ZoneInfo('Europe/Berlin')):%Y%m%dT%H%M%S}",
        "DURATION:PT1H30M", "RRULE:FREQ=WEEKLY", "SUMMARY:Statistics – Lecture", "END:VEVENT",
        "END:VCALENDAR", ""]).encode()

    class Serve(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            found = self.path == "/timetable.ics"
            self.send_response(200 if found else 404)
            self.send_header("Content-Type", "text/calendar")
            self.end_headers()
            self.wfile.write(body if found else b"not here")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Serve)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", start
    server.shutdown()


def test_a_link_is_fetched_by_the_systems_own_curl(local_web):
    """The real `_download`: /usr/bin/curl on a Mac, Windows' own curl.exe on Windows (CI runs this on both). A
    repeating lecture in another time zone comes out at the right moment, which on Windows needs the tzdata package
    (Windows has no time zone database of its own)."""
    base, start = local_web
    code, body = calendars._download(f"{base}/timetable.ics")
    assert code == 200 and b"BEGIN:VCALENDAR" in body
    assert calendars._download(f"{base}/gone.ics")[0] == 404
    assert calendars._download("http://127.0.0.1:9/nothing.ics") == (0, b"")   # nobody listens there

    week_later = start + timedelta(days=14)
    found = calendars._occurrences(calendars._parse(body), week_later - timedelta(minutes=1), week_later + timedelta(minutes=1))
    assert [e["title"] for e in found] == ["Statistics – Lecture"]
    assert found[0]["start"] == week_later.timestamp() and found[0]["end"] - found[0]["start"] == 90 * 60


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows Credential Manager (a Mac's Keychain isn't touched)")
def test_windows_keeps_a_link_in_credential_manager(monkeypatch):
    import secrets
    monkeypatch.setattr(keychain, "SERVICE", f"Stenny test {secrets.token_hex(4)}")
    link = "https://calendar.google.com/calendar/ical/" + "x" * 300 + "/private-0123456789abcdef/basic.ics"
    assert keychain.get_secret("calendar-link-test") is None
    keychain.save_secret("calendar-link-test", link, "Stenny: calendar link")
    try:
        assert keychain.get_secret("calendar-link-test") == link
    finally:
        keychain.delete_secret("calendar-link-test")
    assert keychain.get_secret("calendar-link-test") is None
