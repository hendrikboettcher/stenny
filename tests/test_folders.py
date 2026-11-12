"""Stenny's folders (issue #2, roadmap item 1): the Stenny folder for everything you read, the system's app-data
places for the rest (paths.py), and `stenny migrate` from ~/Lectures Data."""

import errno
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from stenny import config as config_mod
from stenny import migrate as mig
from stenny import paths, share
from stenny.config import Config, ConfigError, Course
from stenny.render import Lecture, transcript_md
from stenny.vault import TALKS_DIR, Vault


# ---------------------------------------------------------------- the places on each system

@pytest.fixture
def system(monkeypatch, tmp_path):
    """Pretend to be on a Mac or on Windows, with a home folder in tmp_path."""
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("STENNY_HOME", raising=False)

    def on(platform: str):
        monkeypatch.setattr(sys, "platform", platform)
        if platform == "win32":
            monkeypatch.setenv("APPDATA", str(home / "AppData/Roaming"))
            monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData/Local"))
        return home
    return on


def test_on_a_mac_stennys_own_files_are_in_library(system):
    home = system("darwin")
    assert paths.config_dir() == home / "Library/Application Support/Stenny"
    assert paths.data_dir() == home / "Library/Application Support/Stenny"
    assert paths.logs_dir() == home / "Library/Logs/Stenny"
    assert paths.cache_dir() == home / "Library/Caches/Stenny"
    assert paths.models_dir() is None  # mlx-whisper's own Hugging Face cache
    assert paths.default_folder() == home / "Documents/Stenny"


def test_on_windows_config_roams_and_the_large_files_stay_local(system, monkeypatch):
    home = system("win32")
    monkeypatch.setattr(paths, "_windows_documents", lambda: str(home / "OneDrive/Documents"))
    assert paths.config_dir() == home / "AppData/Roaming/Stenny"
    assert paths.data_dir() == home / "AppData/Local/Stenny"
    assert paths.logs_dir() == home / "AppData/Local/Stenny/Logs"
    assert paths.cache_dir() == home / "AppData/Local/Stenny/Cache"
    assert paths.models_dir() == home / "AppData/Local/Stenny/Models"
    assert paths.default_folder() == home / "OneDrive/Documents/Stenny"  # the Documents known folder, wherever it is
    monkeypatch.setattr(paths, "_windows_documents", lambda: (_ for _ in ()).throw(OSError("no shell32")))
    assert paths.default_folder() == home / "Documents/Stenny"


def test_stenny_home_puts_everything_in_one_folder(system, monkeypatch, tmp_path):
    system("win32")
    monkeypatch.setenv("STENNY_HOME", str(tmp_path / "test copy"))
    assert paths.config_dir() == paths.data_dir() == tmp_path / "test copy"
    assert paths.logs_dir() == tmp_path / "test copy/Logs" and paths.cache_dir() == tmp_path / "test copy/Cache"
    assert paths.models_dir() == tmp_path / "test copy/Models"


def test_paths_in_the_config_are_written_from_the_home_folder(system):
    home = system("darwin")
    assert paths.tilde(home / "Documents/Stenny") == "~/Documents/Stenny"
    assert paths.tilde(Path("/Volumes/Disk/Stenny")) == "/Volumes/Disk/Stenny"


# ---------------------------------------------------------------- the Stenny folder

def write_config(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_one_folder_setting_and_the_talks_inside_it(tmp_path):
    cfg = config_mod.load(write_config(tmp_path / "c.toml", f'folder = "{tmp_path.as_posix()}/S"\n'))
    assert cfg.folder == tmp_path / "S" and cfg.events_root == tmp_path / "S" / TALKS_DIR
    assert cfg.data_dir == paths.DATA_DIR and cfg.recordings_dir == paths.DATA_DIR / "Recordings"
    assert cfg.data_for(Course("Calculus")) == paths.DATA_DIR / "Recordings/Calculus"
    assert cfg.data_for(cfg.event("Guest talk")) == paths.DATA_DIR / "Recordings/Talks/Guest talk"
    moved = config_mod.load(write_config(tmp_path / "d.toml", f'folder = "{tmp_path.as_posix()}/S"\n'
                                                              f'data_dir = "{tmp_path.as_posix()}/Disk"\n'))
    assert moved.data_for(Course("Calculus")) == tmp_path / "Disk/Recordings/Calculus"
    # a config from before `stenny migrate` still loads
    old = config_mod.load(write_config(tmp_path / "e.toml", f'vault = "{tmp_path.as_posix()}/V"\nlectures_dir = "L"\n'))
    assert old.folder == tmp_path / "V/L"


def test_a_course_cant_be_called_talks(tmp_path):
    from stenny import courses
    with pytest.raises(ConfigError, match="Talks"):
        config_mod.load(write_config(tmp_path / "c.toml", 'folder = "~/S"\n\n[courses.talks]\nlanguage = "en"\n'))
    cfg = config_mod.load(write_config(tmp_path / "d.toml", 'folder = "~/S"\n'))
    with pytest.raises(ConfigError, match="Talks"):
        courses.add(cfg, "Talks", "en")


def test_the_talks_folder_is_no_course_and_new_folders_may_be_made(tmp_path):
    cfg = Config(folder=tmp_path / "Documents" / "Stenny", data_dir=tmp_path / "data",
                 courses={"Calculus": Course("Calculus")})
    (tmp_path / "Documents").mkdir()
    talk = cfg.event("Guest talk")
    vault = cfg.vault_for(talk)
    folder = vault.lecture_dir(talk.name, "2026-11-08")
    lec = Lecture(course=talk.name, date="2026-11-08", start_time="18:00", duration=60, languages=["en"],
                  language_note="en", model="m", source="file", number=0, kind="event")
    vault.write(vault.files(talk.name, folder).new("transcript"), transcript_md(lec, [], []))  # makes Stenny/Talks/…
    lecture = cfg.vault_for(Course("Calculus")).lecture_dir("Calculus", "2026-11-03")
    cfg.vault_for().write(lecture / "Calculus 2026-11-03.md", "x")
    assert [d.name for d in cfg.vault_for().course_dirs()] == ["Calculus"]
    assert [d.name for d in vault.course_dirs()] == ["Guest talk"]
    assert [(e["kind"], e["course"]) for e in share.library(cfg)] == [("lecture", "Calculus"), ("event", "Guest talk")]
    assert cfg.vault_for().violations() == []
    with pytest.raises(Exception, match="outside"):
        vault.write(tmp_path / "Documents" / "elsewhere.md", "no")


def test_stenny_where_says_where_everything_is(tmp_path):
    config = write_config(tmp_path / "home" / "config.toml", f'folder = "{(tmp_path / "S").as_posix()}"\n')
    env = {**os.environ, "STENNY_HOME": str(tmp_path / "home")}
    out = subprocess.run([sys.executable, "-m", "stenny", "where", "--json"], capture_output=True, text=True,
                         encoding="utf-8", env=env, check=True).stdout
    where = json.loads(out)
    assert where["config"] == str(config) and where["folder"] == str(tmp_path / "S")
    assert where["talks"] == str(tmp_path / "S" / "Talks") and where["recordings"] == str(tmp_path / "home/Recordings")
    assert where["logs"] == str(tmp_path / "home/Logs") and where["error"] is None
    broken = json.loads(subprocess.run([sys.executable, "-m", "stenny", "where", "--json"], capture_output=True,
                                       text=True, encoding="utf-8", check=True,
                                       env={**env, "STENNY_HOME": str(tmp_path / "empty")}).stdout)
    assert broken["folder"] is None and broken["config"] == str(tmp_path / "empty/config.toml") and broken["error"]


def test_init_suggests_documents_stenny(places, monkeypatch, capsys):
    from argparse import Namespace

    from stenny import cli
    cli.cmd_init(Namespace(folder=None, vault=None, lectures_dir=None))
    cfg = config_mod.load()
    assert cfg.path == places.config_path and cfg.folder == places.home / "Documents/Stenny" and cfg.folder.is_dir()
    assert places.config_path.read_text(encoding="utf-8").startswith("# Stenny configuration")


def test_the_app_starts_a_config_without_example_courses(places):
    mig.on_start(log=lambda m: None)
    cfg = config_mod.load()
    assert cfg.folder == places.home / "Documents/Stenny" and cfg.courses == {}
    assert "One table per course" in places.config_path.read_text(encoding="utf-8")  # the explanation stays
    mig.on_start(log=lambda m: None)  # and it's left alone afterwards
    assert config_mod.load().courses == {}



def test_the_per_course_copies_go_to_the_trash_at_start(places, monkeypatch):
    """Earlier versions wrote every transcript and summary of a course again into _Course Files/. At start those
    copies go to the Trash, only Stenny's own (by their frontmatter); lectures, the overview and lookalikes stay."""
    from stenny import manage
    mig.on_start(log=lambda m: None)
    folder = config_mod.load().folder
    files = folder / "_Course Files"
    files.mkdir(parents=True)
    copy = '---\ncourse: "Calculus"\nkind: "course-file"\ncontent: "{}"\n---\n# Calculus – {}\n'
    (files / "Calculus (all lectures).md").write_text(copy.format("all lectures", "all lectures"), encoding="utf-8")
    (files / "Calculus (summaries).md").write_text(copy.format("summaries", "summaries"), encoding="utf-8")
    (files / "Calculus (overview).md").write_text('---\nkind: "course-overview"\n---\n', encoding="utf-8")
    (files / "Notes (summaries).md").write_text("# my own summaries\n", encoding="utf-8")  # the user's, no frontmatter
    lecture = folder / "Calculus" / "2026-10-15"
    lecture.mkdir(parents=True)
    (lecture / "Calculus 2026-10-15 (summary).md").write_text("x", encoding="utf-8")
    trashed = []
    monkeypatch.setattr(manage, "trash", lambda items: trashed.extend(items) or [p.unlink() for p in items])
    said = []

    assert mig.drop_course_copies(dry_run=True, log=said.append) == 2 and not trashed  # `stenny migrate --dry-run`
    mig.on_start(log=said.append)
    assert sorted(p.name for p in trashed) == ["Calculus (all lectures).md", "Calculus (summaries).md"]
    assert sorted(p.name for p in files.iterdir()) == ["Calculus (overview).md", "Notes (summaries).md"]
    assert (lecture / "Calculus 2026-10-15 (summary).md").exists()
    assert any("Calculus (summaries).md" in m and m.startswith("migrate: ") for m in said)
    trashed.clear()
    mig.on_start(log=lambda m: None)  # nothing left to do: the Trash isn't asked again
    assert not trashed

    def broken(items):
        raise manage.ManageError("no Trash")
    (files / "Calculus (summaries).md").write_text(copy.format("summaries", "summaries"), encoding="utf-8")
    monkeypatch.setattr(manage, "trash", broken)
    said.clear()
    mig.on_start(log=said.append)  # a Trash that fails doesn't stop the start; it tries again next time
    assert "migrate: no Trash" in said and (files / "Calculus (summaries).md").exists()

# ---------------------------------------------------------------- `stenny migrate` from ~/Lectures Data

# the older layouts were a Mac's only: Windows never had ~/Lectures Data
mac_only = pytest.mark.skipif(sys.platform == "win32", reason="the layouts before were macOS only")

OLD_CONFIG = '''# Stenny configuration. `stenny init --vault <folder>` copies it to ~/Lectures Data/_System/config.toml.

vault = "~/Vault"                            # the folder transcripts go to, e.g. your Obsidian vault
lectures_dir = "Studies/Lectures"            # inside the vault; the tool never writes elsewhere
keep_audio = true                            # false = don't archive audio

[llm]
backend = "claude-code"   # mine

[recordings]
folder = "Studies/Recordings"
languages = ["en", "de"]

[courses."Calculus"]
language = "fr"
'''


def lecture_md(course: str, date: str, audio: str, raw: str, kind: str = "lecture") -> str:
    key = "event" if kind == "event" else "course"
    return (f'---\n{key}: "{course}"\ndate: {date}\naudio: "{audio}"\nstatus: "final"\n---\n# {course}\n\n'
            f"> Every segment, unfiltered, is in `{raw}` (outside the vault).\n\nText.\n")


@pytest.fixture
def old_install(places):
    """An install as 0.4 left it: ~/Lectures Data with a course, a talk, the queue, logs, consent; the talks in
    the vault's Studies/Recordings next to Studies/Lectures."""
    home, data = places.home, places.legacy_home
    system = places.legacy_system_dir
    write_config(system / "config.toml", OLD_CONFIG)
    (system / "consent.json").write_text('["record/course/Calculus"]')
    (system / "stenny.log").write_text("app log")
    (system / "live.log").write_text("live log")
    (system / "calendar-titles.json").write_text('["Calculus I"]')
    (system / "live.sock").write_text("")
    (system / "notes.txt").write_text("mine")  # not Stenny's: stays
    calc = data / "Calculus"
    calc.mkdir(parents=True)
    (calc / "2026-10-20.opus").write_bytes(b"ogg")
    (calc / "Calculus 2026-10-20 (raw).md").write_text("---\naudio: \"~/Lectures Data/Calculus/2026-10-20.opus\"\n---\n")
    (calc / "2026-10-20.session.json").write_text(json.dumps(
        {"course": "Calculus", "recording": f"{calc}/2026-10-20.live.pcm"}))  # ASCII-escaped, as json.dumps writes it
    (calc / "2026-10-20.segments.json").write_text(json.dumps({"meta": {"lecture": {
        "audio": "~/Lectures Data/Calculus/2026-10-20.opus", "raw_file": "~/Lectures Data/Calculus/Calculus 2026-10-20 (raw).md",
        "transcript": f"{home}/Vault/Studies/Lectures/Calculus/2026-10-20/Calculus 2026-10-20.md"}}, "segments": []}))
    talk_data = data / "_Recordings" / "Guest talk – Müller"
    talk_data.mkdir(parents=True)
    (talk_data / "2026-10-22.opus").write_bytes(b"ogg2")
    (talk_data / "2026-10-22.segments.json").write_text(json.dumps({"meta": {"lecture": {
        "audio": f"{talk_data}/2026-10-22.opus",
        "transcript": f"{home}/Vault/Studies/Recordings/Guest talk – Müller/2026-10-22/Guest talk – Müller 2026-10-22.md"}}},
        ensure_ascii=False))
    (data / "_Queue").mkdir()
    (data / "_Queue" / "job.json").write_text(json.dumps({"file": f"{calc}/2026-10-27.live.pcm", "course": "Calculus",
                                                           "live": f"{calc}/2026-10-27.session.json"}))
    (data / "_Shared").mkdir()
    (data / "_Shared" / "Calculus (2026-10-20).md").write_text("copy")
    lectures = home / "Vault" / "Studies" / "Lectures"
    (lectures / "Calculus" / "2026-10-20").mkdir(parents=True)
    (lectures / "Calculus" / "2026-10-20" / "Calculus 2026-10-20.md").write_text(lecture_md(
        "Calculus", "2026-10-20", "~/Lectures Data/Calculus/2026-10-20.opus",
        "~/Lectures Data/Calculus/Calculus 2026-10-20 (raw).md"))
    talks = home / "Vault" / "Studies" / "Recordings"
    (talks / "Guest talk – Müller" / "2026-10-22").mkdir(parents=True)
    (talks / "Guest talk – Müller" / "2026-10-22" / "Guest talk – Müller 2026-10-22.md").write_text(lecture_md(
        "Guest talk – Müller", "2026-10-22", "~/Lectures Data/_Recordings/Guest talk – Müller/2026-10-22.opus",
        "~/Lectures Data/_Recordings/Guest talk – Müller/Guest talk – Müller 2026-10-22 (raw).md", kind="event"))
    (talks / "Holiday photos").mkdir()  # not a talk of Stenny's: stays
    (talks / "Holiday photos" / "beach.jpg").write_bytes(b"jpg")
    return places


@mac_only
def test_migrate_moves_lectures_data_into_the_app_data_places(old_install):
    p = old_install
    home, rec = p.home, p.data_dir / "Recordings"
    lectures, talks = home / "Vault/Studies/Lectures", home / "Vault/Studies/Lectures/Talks"
    said = []
    assert mig.needed()
    assert mig.migrate(dry_run=True, log=said.append) > 0
    assert p.legacy_home.exists() and not p.config_path.exists() and not rec.exists()  # a dry run changes nothing

    said.clear()
    mig.migrate(log=said.append)
    # the config: vault + lectures_dir → folder, the talks' folder out, everything else and its comments as it was
    text = p.config_path.read_text(encoding="utf-8")
    assert text.splitlines()[2].startswith('folder = "~/Vault/Studies/Lectures"  # ')
    assert "lectures_dir" not in text and 'vault = ' not in text and 'folder = "Studies/Recordings"' not in text
    assert 'backend = "claude-code"   # mine' in text and 'languages = ["en", "de"]' in text
    cfg = config_mod.load()
    assert cfg.folder == lectures and cfg.data_dir == p.data_dir
    # data
    calc = rec / "Calculus"
    assert (calc / "2026-10-20.opus").read_bytes() == b"ogg"
    assert json.loads((calc / "2026-10-20.session.json").read_text(encoding="utf-8"))["recording"] == f"{calc}/2026-10-20.live.pcm"
    meta = json.loads((calc / "2026-10-20.segments.json").read_text(encoding="utf-8"))["meta"]["lecture"]
    assert meta["audio"] == f"{calc}/2026-10-20.opus"
    assert meta["transcript"] == f"{lectures}/Calculus/2026-10-20/Calculus 2026-10-20.md"  # unchanged: same folder
    assert f"{calc}/2026-10-20.opus" in (calc / "Calculus 2026-10-20 (raw).md").read_text(encoding="utf-8")
    talk_data = rec / "Talks" / "Guest talk – Müller"
    meta = json.loads((talk_data / "2026-10-22.segments.json").read_text(encoding="utf-8"))["meta"]["lecture"]
    assert meta["audio"] == f"{talk_data}/2026-10-22.opus"
    assert meta["transcript"] == f"{talks}/Guest talk – Müller/2026-10-22/Guest talk – Müller 2026-10-22.md"
    job = json.loads((p.queue_dir / "job.json").read_text(encoding="utf-8"))
    assert job["file"] == f"{calc}/2026-10-27.live.pcm" and job["live"] == f"{calc}/2026-10-27.session.json"
    # talks into the Stenny folder, with their paths; what isn't Stenny's stays
    talk = (talks / "Guest talk – Müller/2026-10-22/Guest talk – Müller 2026-10-22.md").read_text(encoding="utf-8")
    assert f"{talk_data}/Guest talk – Müller 2026-10-22 (raw).md" in talk
    assert "Lectures Data" not in talk
    assert f"{calc}/2026-10-20.opus" in (
        lectures / "Calculus/2026-10-20/Calculus 2026-10-20.md").read_text(encoding="utf-8")
    assert (home / "Vault/Studies/Recordings/Holiday photos/beach.jpg").exists()
    assert [e["course"] for e in share.library(cfg)] == ["Calculus", "Guest talk – Müller"]
    assert Vault(lectures).violations() == []
    # the rest of _System and _Shared
    assert (p.config_dir / "consent.json").read_text(encoding="utf-8") == '["record/course/Calculus"]'
    assert (p.logs_dir / "stenny.log").exists() and (p.logs_dir / "live.log").exists()
    assert (p.data_dir / "calendar-titles.json").exists() and (p.shared_dir / "Calculus (2026-10-20).md").exists()
    # ~/Lectures Data keeps only what Stenny doesn't know, and says so
    assert sorted(f.relative_to(p.legacy_home).as_posix() for f in p.legacy_home.rglob("*")) == [
        "_System", "_System/notes.txt"]
    assert any("notes.txt" in s for s in said) and any("Holiday photos" in s for s in said)
    assert mig.migrate(log=lambda m: None) == 0  # done once
    (p.legacy_system_dir / "notes.txt").unlink()
    mig.migrate(log=lambda m: None)
    assert not p.legacy_home.exists() and not mig.needed()


@mac_only
def test_a_data_dir_of_ones_own_stays_and_gets_recordings_inside(places):
    disk = places.home / "Disk"
    write_config(places.legacy_system_dir / "config.toml",
                 'vault = "~/Vault"\nlectures_dir = "Lectures"\ndata_dir = "~/Disk"\n')
    (disk / "Calculus").mkdir(parents=True)
    (disk / "Calculus" / "2026-10-20.session.json").write_text(json.dumps({"recording": f"{disk}/Calculus/a.pcm"}))
    mig.migrate(log=lambda m: None)
    assert 'data_dir = "~/Disk"' in places.config_path.read_text(encoding="utf-8")
    session = disk / "Recordings" / "Calculus" / "2026-10-20.session.json"
    assert json.loads(session.read_text(encoding="utf-8"))["recording"] == f"{disk}/Recordings/Calculus/a.pcm"
    assert config_mod.load().data_for(Course("Calculus")) == disk / "Recordings/Calculus"
    (disk / "Physics").mkdir()  # another round (say, after a partly failed one) moves only what's new
    (disk / "Physics" / "x.json").write_text(json.dumps({"a": f"{disk}/Physics/x"}))
    write_config(places.legacy_system_dir / "config.toml", 'vault = "~/Vault"\ndata_dir = "~/Disk"\n')
    places.config_path.unlink()
    mig.migrate(log=lambda m: None)
    assert json.loads(session.read_text(encoding="utf-8"))["recording"] == f"{disk}/Recordings/Calculus/a.pcm"  # not twice
    assert json.loads((disk / "Recordings/Physics/x.json").read_text(encoding="utf-8"))["a"] == f"{disk}/Recordings/Physics/x"


@mac_only
def test_data_dir_set_to_lectures_data_itself_is_dropped(places):
    write_config(places.legacy_system_dir / "config.toml",
                 'vault = "~/Vault"\nlectures_dir = "Lectures"\ndata_dir = "~/Lectures Data"   # explicit\n')
    (places.legacy_home / "Calculus").mkdir()
    (places.legacy_home / "Calculus" / "a.opus").write_bytes(b"x")
    mig.migrate(log=lambda m: None)
    assert "data_dir" not in places.config_path.read_text(encoding="utf-8")
    assert (places.data_dir / "Recordings/Calculus/a.opus").exists() and not places.legacy_home.exists()


@mac_only
def test_the_old_audio_dir_note_goes_and_ones_own_comments_stay(places):
    write_config(places.legacy_system_dir / "config.toml",
                 '# lecture configuration\n\nvault = "~/Vault"\nlectures_dir = "Lectures"\n'
                 '# audio_dir: no longer needed, everything outside the vault is in ~/Lectures Data (see paths.py)\n'
                 '# my own note about ~/Lectures Data\nkeep_audio = true\n')
    mig.migrate(log=lambda m: None)
    text = places.config_path.read_text(encoding="utf-8")
    assert "audio_dir" not in text
    assert "# my own note about ~/Lectures Data\nkeep_audio = true" in text
    assert text.startswith("# lecture configuration\n\nfolder = ")


def test_across_disks_it_copies_checks_and_then_deletes(old_install, monkeypatch):
    real = os.rename

    def other_disk(src, dst):
        if "Lectures Data" in str(src):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real(src, dst)
    monkeypatch.setattr(os, "rename", other_disk)
    mig.migrate(log=lambda m: None)
    assert (old_install.data_dir / "Recordings/Calculus/2026-10-20.opus").read_bytes() == b"ogg"
    assert not (old_install.legacy_home / "Calculus").exists()


def test_a_copy_that_doesnt_arrive_whole_leaves_the_original(tmp_path, monkeypatch):
    import shutil
    src = tmp_path / "a" / "Calculus"
    src.mkdir(parents=True)
    (src / "x.opus").write_bytes(b"12345")
    monkeypatch.setattr(os, "rename", lambda a, b: (_ for _ in ()).throw(OSError(errno.EXDEV, "cross-device")))
    monkeypatch.setattr(shutil, "copy2", lambda a, b, **k: Path(b).write_bytes(b"12"))
    with pytest.raises(mig.MigrateError):
        mig._move(src, tmp_path / "b" / "Calculus")
    assert (src / "x.opus").read_bytes() == b"12345" and not (tmp_path / "b" / "Calculus").exists()


@mac_only
def test_migrate_waits_while_a_recording_runs(old_install, monkeypatch):
    monkeypatch.setattr(mig.control, "is_running", lambda path=None: True)
    with pytest.raises(mig.MigrateError, match="recording"):
        mig.migrate(log=lambda m: None)
    said = []
    mig.on_start(log=said.append)
    assert old_install.legacy_home.exists() and any("next start" in s for s in said)


@mac_only
def test_migrate_waits_while_an_old_final_pass_runs(old_install):
    import fcntl
    with open(old_install.legacy_system_dir / "queue.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert "final pass" in mig.busy()
    assert mig.busy() is None


@mac_only
def test_the_queues_background_job_logs_to_the_new_place(old_install, monkeypatch):
    import plistlib
    from stenny import jobs
    jobs.AGENT_PATH.parent.mkdir(parents=True)
    jobs.AGENT_PATH.write_bytes(plistlib.dumps({"ProgramArguments": ["/x/stenny", "queue", "run"],
                                                "StandardOutPath": str(old_install.legacy_system_dir / "queue.log")}))
    installed = []
    monkeypatch.setattr(jobs, "install_agent", lambda exe=None: installed.append(exe))
    monkeypatch.setattr(sys, "platform", "darwin")
    mig.migrate(log=lambda m: None)
    assert installed == ["/x/stenny"]
