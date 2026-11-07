import json
import shutil
import tomllib

import pytest

from stenny import app_live, app_server, jobs, manage, paths, share
from stenny import config as config_mod
from stenny.pipeline import home_relative
from stenny.render import Lecture, parse_frontmatter, raw_md, summary_md, transcript_md

PARAGRAPHS = [{"start": 4.0, "text": "Welcome to Data Analytics 2026 edition."}]
SECTIONS = [{"at": 0, "title": "Intro"}]
NAME = "Data Analytics"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A vault with three lectures of one course (the second one queued), its data folder, a queue job, the
    lecturer's OK; the Trash is a folder."""
    vault, data, system = tmp_path / "vault", tmp_path / "data", tmp_path / "data" / "_System"
    system.mkdir(parents=True)
    (system / "config.toml").write_text(f'vault = "{vault}"\nlectures_dir = "Lectures"\ndata_dir = "{data}"\n\n'
                                        f'[courses."{NAME}"]  # mine\nlanguage = "en"\naliases = ["da"]\n\n'
                                        '[courses.Other]\nlanguage = "fr"\n', encoding="utf-8")
    monkeypatch.setattr(paths, "SYSTEM_DIR", system)
    monkeypatch.setattr(jobs, "DATA_DIR", system)
    monkeypatch.setattr(jobs, "QUEUE_DIR", data / "_Queue")
    monkeypatch.setattr(manage, "_recording", lambda: None)
    trashed = tmp_path / "Trash"
    trashed.mkdir()
    monkeypatch.setattr(manage, "trash", lambda items: [shutil.move(p, trashed / p.name) for p in items if p.exists()])
    root, course_data = vault / "Lectures", data / NAME
    course_data.mkdir(parents=True)
    dates = ["2026-10-20", "2026-10-27", "2026-10-29"]
    for n, date in enumerate(dates, 1):
        folder = root / NAME / date
        folder.mkdir(parents=True)
        names = {"transcript": f"{NAME} {date}.md", "summary": f"{NAME} {date} (summary).md"}
        lec = Lecture(course=NAME, date=date, start_time="10:00", duration=60, languages=["en"], language_note="en",
                      model="m", source=f"live {date}", number=n, names=names, status="queued" if n == 2 else "final",
                      audio=f"{home_relative(course_data)}/{date}.opus",
                      raw_file=f"{home_relative(course_data)}/{NAME} {date} (raw).md")
        (folder / names["transcript"]).write_text(transcript_md(lec, PARAGRAPHS, SECTIONS), encoding="utf-8")
        if n != 2:
            (folder / names["summary"]).write_text(summary_md(lec, "About it.", "x"), encoding="utf-8")
        (course_data / f"{date}.opus").write_bytes(b"x")
        (course_data / f"{NAME} {date} (raw).md").write_text(raw_md(lec, []), encoding="utf-8")
        (course_data / f"{date}.session.json").write_text(json.dumps(
            {"course": NAME, "recording": str(course_data / f"{date}.live.pcm"), "folder": date}), encoding="utf-8")
        (course_data / f"{date}.segments.json").write_text(json.dumps(
            {"meta": {"folder": str(folder), "lecture": {"course": NAME}}, "segments": []}), encoding="utf-8")
    (root / "_Glossaries").mkdir()
    (root / "_Glossaries" / f"{NAME} (glossary).md").write_text(
        f'---\ncourse: "{NAME}"\n---\n# Glossary: {NAME}\n\n## Terms\n- kNN\n', encoding="utf-8")
    jobs.QUEUE_DIR.mkdir()
    (jobs.QUEUE_DIR / "j.json").write_text(json.dumps(
        {"file": str(course_data / "2026-10-27.live.pcm"), "course": NAME, "live": str(course_data / "2026-10-27.session.json")}))
    (system / "consent.json").write_text(json.dumps([f"record/course/{NAME}", "share/course/Other"]))
    cfg = config_mod.load(system / "config.toml")
    from stenny import course_files
    from stenny.vault import Vault
    course_files.rebuild(Vault(cfg.lectures_root), NAME, log=lambda *_: None)
    return cfg, trashed


def test_rename_lecture_sets_a_title_the_library_and_shared_copies_show(env):
    cfg, _ = env
    manage.rename_lecture(cfg, NAME, "2026-10-29", "  Decision   trees ")
    entry = share.find(cfg, NAME, "2026-10-29")
    assert entry["title"] == "Decision trees" and entry["number"] == 3
    assert "# Data Analytics – Lecture 3: Decision trees (2026-10-29)" in share.shared_text(entry)
    assert share.share_name(entry, "pdf") == "Data Analytics – Lecture 3 – Decision trees (2026-10-29).pdf"
    manage.rename_lecture(cfg, NAME, "2026-10-29", "")
    assert share.find(cfg, NAME, "2026-10-29")["title"] is None


def test_the_final_pass_keeps_the_title(env):
    from stenny.pipeline import own_title
    from stenny.vault import LectureFiles
    cfg, _ = env
    manage.rename_lecture(cfg, NAME, "2026-10-27", "Trees")
    assert own_title(LectureFiles(cfg.lectures_root / NAME / "2026-10-27", NAME)) == "Trees"
    lec = Lecture(course=NAME, date="2026-10-27", start_time="10:00", duration=60, languages=["en"], language_note="",
                  model="m", source="s", number=2, name="Trees")
    assert parse_frontmatter(transcript_md(lec, PARAGRAPHS, SECTIONS))["title"] == "Trees"


def test_delete_lecture_trashes_everything_and_renumbers(env):
    cfg, trashed = env
    manage.delete_lecture(cfg, NAME, "2026-10-27")
    assert not (cfg.lectures_root / NAME / "2026-10-27").exists()
    assert {p.name for p in trashed.iterdir()} == {"2026-10-27", "2026-10-27.opus", "2026-10-27.session.json",
                                                   "2026-10-27.segments.json",
                                                   f"{NAME} 2026-10-27 (raw).md", "j.json"}
    assert (cfg.data_dir / NAME / "2026-10-20.opus").exists()
    third = parse_frontmatter((cfg.lectures_root / NAME / "2026-10-29" / f"{NAME} 2026-10-29.md").read_text())
    assert third["lecture"] == 2 and third["previous"] == f"[[{NAME} 2026-10-20]]"


def test_deleting_the_last_lecture_removes_the_course_folder(env):
    cfg, _ = env
    for d in ["2026-10-20", "2026-10-27", "2026-10-29"]:
        manage.delete_lecture(cfg, NAME, d)
    assert not (cfg.lectures_root / NAME).exists()


def test_a_running_recording_is_neither_renamed_nor_deleted(env):
    cfg, _ = env
    path = cfg.lectures_root / NAME / "2026-10-29" / f"{NAME} 2026-10-29.md"
    path.write_text(path.read_text().replace('status: "final"', 'status: "live"'))
    for call in (lambda: manage.delete_lecture(cfg, NAME, "2026-10-29"),
                 lambda: manage.rename_lecture(cfg, NAME, "2026-10-29", "x")):
        with pytest.raises(manage.ManageError, match="still running"):
            call()


def test_rename_course_everywhere(env):
    cfg, _ = env
    new = "Data Analytics – Fall"
    manage.rename_course(cfg, "da", new)
    root, data = cfg.lectures_root, cfg.data_dir
    assert not (root / NAME).exists() and not (data / NAME).exists()
    transcript = root / new / "2026-10-29" / f"{new} 2026-10-29.md"
    text = transcript.read_text()
    fm = parse_frontmatter(text)
    assert fm["course"] == new and fm["previous"] == f"[[{new} 2026-10-27]]"
    assert f"# {new} – Lecture 3 (2026-10-29)" in text
    assert f"{home_relative(data / new)}/{new} 2026-10-29 (raw).md" in text and fm["audio"].startswith(home_relative(data / new))
    assert "Welcome to Data Analytics 2026 edition." in text  # the words of the lecture stay
    assert (root / new / "2026-10-29" / f"{new} 2026-10-29 (summary).md").exists()
    assert (root / "_Glossaries" / f"{new} (glossary).md").read_text().count(new) == 2
    assert not list((root / "_Course Files").glob(f"{NAME} (*"))
    assert (root / "_Course Files" / f"{new} (overview).md").exists()
    assert (data / new / f"{new} 2026-10-20 (raw).md").exists()
    session = json.loads((data / new / "2026-10-20.session.json").read_text())
    assert session["course"] == new and session["recording"] == str(data / new / "2026-10-20.live.pcm")
    meta = json.loads((data / new / "2026-10-20.segments.json").read_text())["meta"]
    assert meta == {"folder": str(root / new / "2026-10-20"), "lecture": {"course": new}}
    job = json.loads((jobs.QUEUE_DIR / "j.json").read_text())
    assert job["course"] == new and job["live"] == str(data / new / "2026-10-27.session.json")
    assert json.loads((paths.SYSTEM_DIR / "consent.json").read_text()) == ["record/course/" + new, "share/course/Other"]
    text = cfg.path.read_text()
    assert "# mine" in text
    course = config_mod.load(cfg.path).course(new)
    assert course.aliases == ["da"] and course.calendar == [NAME] and course.language == "en"
    assert [e["course"] for e in share.library(config_mod.load(cfg.path))] == [new] * 3


def test_rename_course_refuses_a_taken_name(env):
    cfg, _ = env
    for name in ("Other", "other", "da"):
        with pytest.raises(manage.ManageError, match="already"):
            manage.rename_course(cfg, NAME, name)
    with pytest.raises(manage.ManageError, match="can't contain"):
        manage.rename_course(cfg, NAME, "A/B")
    assert (cfg.lectures_root / NAME).exists()


def test_rename_course_waits_for_a_running_final_pass(env):
    cfg, _ = env
    with jobs.QueueLock():
        with pytest.raises(manage.ManageError, match="being made"):
            manage.rename_course(cfg, NAME, "New")
    assert (cfg.lectures_root / NAME).exists()


def test_the_app_server_reports_problems_as_messages(env):
    cfg, _ = env
    assert app_server.handle(cfg, {"id": 1, "method": "rename_course", "params": {"course": NAME, "name": "Other"}}) \
        == {"id": 1, "error": "There's already a course called 'Other'."}
    reply = app_server.handle(cfg, {"id": 2, "method": "rename_lecture",
                                    "params": {"course": NAME, "folder": "2026-10-20", "title": "Intro"}})
    assert reply["result"] == {"title": "Intro"}
    reply = app_server.handle(cfg, {"id": 3, "method": "add_course", "params": {"name": "Stats", "language": "de"}})
    assert reply["result"]["message"] == "Added Stats."
    assert app_live._consent() == {f"record/course/{NAME}", "share/course/Other"}


def test_delete_course_trashes_it_all_and_leaves_the_config(env):
    cfg, trashed = env
    manage.delete_course(cfg, "da")
    root = cfg.lectures_root
    assert not (root / NAME).exists() and not (cfg.data_dir / NAME).exists()
    assert {p.name for p in trashed.iterdir()} >= {NAME, f"{NAME} (glossary).md", f"{NAME} (overview).md", "j.json"}
    left = config_mod.load(cfg.path)
    assert list(left.courses) == ["Other"] and "# mine" not in cfg.path.read_text()
    assert json.loads((paths.SYSTEM_DIR / "consent.json").read_text()) == ["share/course/Other"]
    assert share.library(left) == []


def test_delete_a_talk(env, tmp_path):
    cfg, trashed = env
    talk = cfg.events_root / "Guest Talk" / "2026-10-22"
    talk.mkdir(parents=True)
    (talk / "Guest Talk 2026-10-22.md").write_text('---\nevent: "Guest Talk"\n---\n# Guest Talk – 2026-10-22\n')
    (cfg.data_dir / "_Recordings" / "Guest Talk").mkdir(parents=True)
    manage.delete_course(cfg, "Guest Talk", event=True)
    assert not (cfg.events_root / "Guest Talk").exists() and not (cfg.data_dir / "_Recordings" / "Guest Talk").exists()
    assert list(config_mod.load(cfg.path).courses) == [NAME, "Other"]
