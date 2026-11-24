"""Transcripts someone sent (#138): every format read word for word (transcripts.py), and added to a course as it is
through the window's ready entry (ready.py) or `stenny add` (pipeline.add_transcript)."""

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest
from pdfgen import make_pdf

from stenny import app_server, cli, connector, jobs, manage, pipeline, share, transcripts, word
from stenny.config import Config, Course
from stenny.render import Lecture, parse_frontmatter, summary_md, transcript_md
from stenny.vault import LectureFiles

PARAGRAPHS = [{"start": 12.0, "text": "Good morning, today we continue with trees."},
              {"start": 95.0, "text": "A decision tree splits the data by one feature at a time, and each split is "
                                      "chosen to make the groups purer than before, which we measure with the Gini index."},
              {"start": 640.0, "text": "Pruning keeps the tree from fitting noise."}]
SECTIONS = [{"at": 0, "title": "Decision trees"}, {"at": 2, "title": "Pruning"}]
SUMMARY = "## Overview\nTrees split data. [0:01:35]\n\n## Key points\n- Gini index [0:01:35]\n- Pruning [0:10:40]"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def stenny_lecture(tmp_path):
    """A lecture as Stenny writes it on another computer: own title, AI headings, a summary."""
    lec = Lecture(course="Data Mining", date="2026-10-20", start_time="10:15", duration=5460, languages=["en"],
                  language_note="en", model="m", source="rec.m4a", number=2, name="Decision trees",
                  raw_file="~/x (raw).md", structure="ai (x)",
                  names={"transcript": "Data Mining 2026-10-20.md", "summary": "Data Mining 2026-10-20 (summary).md"})
    folder = tmp_path / "theirs" / "Data Mining" / "2026-10-20"
    folder.mkdir(parents=True)
    t = folder / "Data Mining 2026-10-20.md"
    t.write_text(transcript_md(lec, PARAGRAPHS, SECTIONS), encoding="utf-8")
    s = folder / "Data Mining 2026-10-20 (summary).md"
    s.write_text(summary_md(lec, SUMMARY, "Claude"), encoding="utf-8")
    return {"kind": "lecture", "course": "Data Mining", "folder": "2026-10-20", "number": 2, "title": "Decision trees",
            "transcript": str(t), "summary": str(s)}


def came_back(t: transcripts.Transcript, summary: bool) -> None:
    assert t.by == "Stenny" and t.date == "2026-10-20" and t.start == "10:15" and t.seconds == 5460
    assert t.title == "Decision trees"
    assert [(p["start"], p["text"]) for p in t.paragraphs] == [(p["start"], p["text"]) for p in PARAGRAPHS]
    assert t.sections == SECTIONS
    if summary:
        assert t.summary.splitlines()[0] == "## Overview" and "## Key points" in t.summary
        assert "Trees split data. [0:01:35]" in t.summary and "Gini index [0:01:35]" in t.summary
    else:
        assert t.summary is None


def test_a_stenny_lecture_file_comes_back_whole(stenny_lecture):
    came_back(transcripts.read(Path(stenny_lecture["transcript"])), summary=False)


@pytest.mark.parametrize("fmt", ["md", "html"])
@pytest.mark.parametrize("with_summary", [False, True])
def test_a_stenny_copy_comes_back_whole(stenny_lecture, tmp_path, fmt, with_summary):
    copy = share.share(stenny_lecture, with_summary=with_summary, fmt=fmt, out_dir=tmp_path / "copies")
    came_back(transcripts.read(copy), summary=with_summary)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS's textutil makes the Word copy on a Mac")
@pytest.mark.parametrize("with_summary", [False, True])
def test_a_stenny_word_copy_from_a_mac(stenny_lecture, tmp_path, with_summary):
    copy = share.share(stenny_lecture, with_summary=with_summary, fmt="docx", out_dir=tmp_path / "copies")
    came_back(transcripts.read(copy), summary=with_summary)


@pytest.mark.parametrize("with_summary", [False, True])
def test_a_stenny_word_copy_from_windows(stenny_lecture, tmp_path, with_summary):
    """Stenny's own Word writer (word.py, Windows): headings as Word's Heading styles."""
    text = share.shared_text(stenny_lecture, with_summary)
    copy = word.write(tmp_path / "copy.docx", share.blocks(text), share._inline, "copy") or tmp_path / "copy.docx"
    came_back(transcripts.read(copy), summary=with_summary)


def test_a_stenny_pdf_copy(tmp_path):
    """The lines pypdf reads from Stenny's PDF (made on a Mac by stenny-agent from the HTML copy)."""
    lines = ["Data Mining – Lecture 2: Decision trees (2026-10-20)",
             "Automatic transcript (English) of a lecture recorded on 2026-10-20 at 10:15, length 1:31:00. Transcribed locally",
             "with Whisper; it may contain recognition errors, especially in names and technical terms. Section headings were",
             "added by an AI.",
             "Audio only: this is what the microphone heard, not what was shown. Words like \"this\", \"here\" or \"as you can",
             "see\" often point to something shown in the room.",
             "Summary",
             "AI-generated summary of the transcript below. It can omit or misstate things; the transcript is authoritative.",
             "Timestamps point into the transcript below.",
             "Overview", "Trees split data. [0:01:35]", "Key points", "• Gini index [0:01:35]", "• Pruning [0:10:40]",
             "[0:00:12] Decision trees", "[0:00:12] Good morning, today we continue with trees.",
             "[0:01:35] A decision tree splits the data by one feature at a time, and each split is chosen to make",
             "the groups purer than before, which we measure with the Gini index.",
             "[0:10:40] Pruning", "[0:10:40] Pruning keeps the tree from fitting noise.",
             "Transcribed with Stenny · stenny.eu"]
    t = transcripts.read(make_pdf(tmp_path / "copy.pdf", [lines]))
    came_back(t, summary=True)
    assert t.summary == "## Overview\n\nTrees split data. [0:01:35]\n\n## Key points\n\n- Gini index [0:01:35]\n\n- Pruning [0:10:40]"


TEAMS_VTT = """WEBVTT

0b4ac1f2-9c3e-4a51-8d0e-1f2a3b4c5d6e/12-0
00:00:04.120 --> 00:00:07.880
<v Anna Berger>Okay, let's get started with clustering.</v>

0b4ac1f2-9c3e-4a51-8d0e-1f2a3b4c5d6e/13-0
00:00:08.000 --> 00:00:12.500
<v Anna Berger>K-means needs the number of clusters up front.</v>

0b4ac1f2-9c3e-4a51-8d0e-1f2a3b4c5d6e/14-0
00:00:13.000 --> 00:00:15.000
<v Ben Ito>Can it find that by itself?</v>
"""


def test_a_vtt_from_teams(tmp_path):
    path = tmp_path / "Data Mining week 6.vtt"
    path.write_text(TEAMS_VTT, encoding="utf-8")
    t = transcripts.read(path)
    assert t.by == "Teams" and t.seconds == 15 and t.date is None
    assert t.paragraphs == [
        {"start": 4.0, "text": "Anna Berger: Okay, let's get started with clustering. K-means needs the number of clusters up front."},
        {"start": 13.0, "text": "Ben Ito: Can it find that by itself?"}]


def test_an_srt_and_a_zoom_vtt(tmp_path):
    srt = tmp_path / "lecture.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nWelcome back.\n\n2\n00:01:10,500 --> 00:01:14,000\n"
                   "Today: hierarchical clustering.\n", encoding="utf-8")
    t = transcripts.read(srt)
    assert [(p["start"], p["text"]) for p in t.paragraphs] == [(1.0, "Welcome back."), (70.0, "Today: hierarchical clustering.")]
    zoom = tmp_path / "GMT20261106-091500_Recording.transcript.vtt"
    zoom.write_text("WEBVTT\n\n1\n00:00:02.000 --> 00:00:05.000\nAnna Berger: Hello everyone.\n\n2\n"
                    "00:00:05.500 --> 00:00:08.000\nAnna Berger: Let's begin.\n", encoding="utf-8")
    t = transcripts.read(zoom)
    assert t.by == "Zoom" and t.date == "2026-11-06"
    assert t.paragraphs == [{"start": 2.0, "text": "Anna Berger: Hello everyone. Let's begin."}]


OTTER = """Speaker 1  0:04
Okay, let's get started with clustering. K-means needs
the number of clusters up front.

Speaker 2  1:10
Can it find that by itself?

Transcribed by https://otter.ai
"""


def test_otter_text_with_speakers_and_times(tmp_path):
    path = tmp_path / "Lecture.txt"
    path.write_text(OTTER, encoding="utf-8")
    t = transcripts.read(path)
    assert t.by == "Otter"
    assert t.paragraphs == [
        {"start": 4.0, "text": "Speaker 1: Okay, let's get started with clustering. K-means needs the number of clusters up front."},
        {"start": 70.0, "text": "Speaker 2: Can it find that by itself?"},
        {"start": None, "text": "Transcribed by https://otter.ai"}]


@pytest.mark.parametrize("text, starts", [
    ("[12:34] First point.\n\n[1:02:03] Second point.", [754.0, 3723.0]),
    ("00:12:34 First point.\n\n00:13:00 Second point.", [754.0, 780.0]),
    ("0:12:34\nFirst point.\n\n0:13:00\nSecond point.", [754.0, 780.0]),
    ("We meet at 10:30 in room 2.\n\nBring the slides.", [None, None]),  # a time in a sentence is text
])
def test_timestamps_in_different_spellings(tmp_path, text, starts):
    path = tmp_path / "notes.md"
    path.write_text(text, encoding="utf-8")
    t = transcripts.read(path)
    assert [p["start"] for p in t.paragraphs] == starts
    assert t.paragraphs[0]["text"] in ("First point.", "We meet at 10:30 in room 2.")


@pytest.mark.parametrize("encoding, bom", [("utf-8", b""), ("utf-8", b"\xef\xbb\xbf"), ("utf-16", b""), ("cp1252", b"")])
def test_text_encodings(tmp_path, encoding, bom):
    path = tmp_path / "Vorlesung.txt"
    path.write_bytes(bom + "Grüße aus der Übung – „Clustering“ für 5 €.".encode(encoding))
    assert transcripts.read(path).paragraphs[0]["text"] == "Grüße aus der Übung – „Clustering“ für 5 €."


def docx(path: Path, paragraphs: list[str]) -> Path:
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in paragraphs)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/'
                                        f'2006/main"><w:body>{body}</w:body></w:document>')
    return path


def test_a_word_file_from_otter(tmp_path):
    t = transcripts.read(docx(tmp_path / "Lecture.docx", ["Speaker 1  0:04", "Okay, let's get started.",
                                                          "Speaker 2  1:10", "Can it find that by itself?",
                                                          "Transcribed by https://otter.ai"]))
    assert t.by == "Otter" and t.paragraphs[:2] == [
        {"start": 4.0, "text": "Speaker 1: Okay, let's get started."},
        {"start": 70.0, "text": "Speaker 2: Can it find that by itself?"}]


def test_a_pdf_without_page_numbers_and_running_headers(tmp_path):
    pages = [["Data Mining – Lecture notes", "Okay, let's get started with clustering. K-means needs the number",
              "of clusters up front.", "Can it find that by it-",
              "self? Not directly.", "1"],
             ["Data Mining – Lecture notes", "Hierarchical clustering builds a tree of clusters from the bottom up,",
              "merging the two closest groups at each step.", "2"],
             ["Data Mining – Lecture notes", "The silhouette score says how well each point fits its own cluster",
              "compared with the next one.", "Page 3 of 3"]]
    t = transcripts.read(make_pdf(tmp_path / "notes.pdf", pages))
    # a paragraph ends where a sentence ends on a line clearly shorter than the full ones
    assert t.paragraphs[0]["text"] == "Okay, let's get started with clustering. K-means needs the number of clusters up front."
    assert t.paragraphs[1]["text"] == "Can it find that by it-self? Not directly."
    assert len(t.paragraphs) == 4
    assert not any("Lecture notes" in p["text"] or p["text"].isdigit() for p in t.paragraphs)
    assert t.timestamps is False


def test_a_scanned_pdf_is_turned_down(tmp_path):
    with pytest.raises(transcripts.TranscriptError, match="probably a scan"):
        transcripts.read(make_pdf(tmp_path / "Week 6.pdf", [[], []]))


# ---------------------------------------------------------------- added to a course

@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "QUEUE_DIR", tmp_path / "Queue")
    monkeypatch.setattr(jobs, "recording_running", lambda: False)
    cfg = Config(folder=tmp_path / "Stenny", data_dir=tmp_path / "data", llm_backend="none",
                 courses={"Data Mining": Course("Data Mining", language="en", languages=["en"])})
    cfg.folder.mkdir()
    return cfg


def call(cfg, method, **params):
    reply = app_server.handle(cfg, {"id": 1, "method": method, "params": params})
    if "error" in reply:
        raise AssertionError(reply["error"])
    return reply["result"]


def lectures(cfg):
    return [e for e in call(cfg, "library")["entries"] if e["course"] == "Data Mining"]


def test_a_vtt_is_ready_to_add_then_added_as_it_is(cfg, tmp_path):
    sent = tmp_path / "Downloads" / "Data Mining 2026-11-06.vtt"
    sent.parent.mkdir()
    sent.write_text(TEAMS_VTT, encoding="utf-8")
    before = digest(sent)
    entry = call(cfg, "add_recording", course="Data Mining", path=str(sent))
    info = entry["ready"]["transcript"]
    assert entry["status"] == "ready" and entry["folder"] == "2026-11-06" and entry["duration"] == "0:00:15"
    assert info["by"] == "Teams" and info["timestamps"] and not info["summary"]
    assert info["preview"][0] == {"start": "0:00:04", "text": "Anna Berger: Okay, let's get started with clustering. "
                                                             "K-means needs the number of clusters up front."}
    assert not (cfg.folder / "Data Mining").exists()  # nothing in the Stenny folder before Add
    added = call(cfg, "transcribe_ready", course="Data Mining", id=entry["ready"]["id"])
    assert added == {"added": True, "folder": "2026-11-06"}
    path = LectureFiles(cfg.folder / "Data Mining" / "2026-11-06", "Data Mining").existing("transcript")
    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    assert fm["source"] == "imported" and fm["imported_from"] == sent.name and fm["transcribed_by"] == "Teams"
    assert fm["lecture"] == 1 and "model" not in fm and "audio" not in fm and "timestamps" not in fm
    assert "# Data Mining – Lecture 1 (2026-11-06)" in text
    assert f"Transcript imported from `{sent.name}` (made with Teams). Stenny didn't hear this recording" in text
    assert "**[0:00:04]** Anna Berger: Okay, let's get started with clustering." in text
    data = cfg.data_for(cfg.courses["Data Mining"])
    kept = data / "2026-11-06.original.vtt"
    assert kept.read_bytes() == sent.read_bytes() and digest(sent) == before  # a copy kept; theirs untouched
    meta = json.loads((data / "2026-11-06.segments.json").read_text(encoding="utf-8"))
    assert meta["meta"]["imported"] and all(s["imported"] for s in meta["segments"])
    [e] = lectures(cfg)
    assert e["status"] == "final" and e["imported"] == {"from": sent.name, "by": "Teams", "timestamps": True}
    assert list(data.glob("*.ready.json")) == []
    # no AI step to run again, and deleting it takes Stenny's copy along
    with pytest.raises(pipeline.LectureError, match="added as it is"):
        pipeline.refresh(cfg, cfg.courses["Data Mining"], "2026-11-06")
    assert kept in manage.lecture_paths(cfg, "Data Mining", "2026-11-06")


def test_a_stenny_copy_brings_its_summary_and_numbers_the_course(cfg, stenny_lecture, tmp_path):
    later = tmp_path / "2026-11-17 notes.md"  # a lecture after the copy's
    later.write_text("[0:00:05] A later lecture.", encoding="utf-8")
    call(cfg, "transcribe_ready", course="Data Mining",
         id=call(cfg, "add_recording", course="Data Mining", path=str(later))["ready"]["id"])
    copy = share.share(stenny_lecture, with_summary=True, fmt="html", out_dir=tmp_path / "copies")
    entry = call(cfg, "add_recording", course="Data Mining", path=str(copy))
    assert entry["ready"]["transcript"]["by"] == "Stenny" and entry["ready"]["transcript"]["summary"]
    assert [(e["folder"], e["number"], e["status"]) for e in lectures(cfg)][0] == ("2026-10-20", 1, "ready")
    call(cfg, "transcribe_ready", course="Data Mining", id=entry["ready"]["id"])
    folder = cfg.folder / "Data Mining" / "2026-10-20"
    files = LectureFiles(folder, "Data Mining")
    fm = parse_frontmatter(files.existing("transcript").read_text(encoding="utf-8"))
    assert fm["lecture"] == 1 and fm["title"] == "Decision trees" and fm["start"] == "10:15" and fm["duration"] == "1:31:00"
    assert "## [0:10:40] Pruning" in files.existing("transcript").read_text(encoding="utf-8")
    summary = files.existing("summary").read_text(encoding="utf-8")
    assert "## Overview" in summary and "Trees split data. [0:01:35]" in summary
    assert [e["number"] for e in lectures(cfg)] == [1, 2]  # the later one moved up


def test_without_timestamps_nothing_is_invented(cfg, tmp_path, monkeypatch):
    sent = tmp_path / "notes.txt"
    sent.write_text("Okay, let's get started with clustering.\n\nK-means needs the number of clusters.", encoding="utf-8")
    entry = call(cfg, "add_recording", course="Data Mining", path=str(sent))
    assert entry["ready"]["transcript"]["timestamps"] is False and entry["duration"] is None
    call(cfg, "transcribe_ready", course="Data Mining", id=entry["ready"]["id"])
    path = LectureFiles(cfg.folder / "Data Mining" / entry["folder"], "Data Mining").existing("transcript")
    text = path.read_text(encoding="utf-8")
    assert "[0:" not in text and "It has no timestamps." in text and parse_frontmatter(text)["timestamps"] is False
    assert text.rstrip().endswith("Okay, let's get started with clustering.\n\nK-means needs the number of clusters.")
    monkeypatch.setattr(connector, "_courses", lambda cfg: {"Data Mining": share.library(cfg)})
    read = connector.get_transcript(cfg, "Data Mining", 1)
    assert "Imported transcript, made with another app" in read and "cite it by quoting, not by time" in read
    assert "Name this lecture once" not in read  # no times to cite by


def test_the_same_date_twice_and_removing(cfg, tmp_path):
    a, b = tmp_path / "2026-11-06 mine.srt", tmp_path / "2026-11-06 friend.srt"
    for p in (a, b):
        p.write_text("1\n00:00:01,000 --> 00:00:03,000\nWelcome back.\n", encoding="utf-8")
    first = call(cfg, "add_recording", course="Data Mining", path=str(a))
    second = call(cfg, "add_recording", course="Data Mining", path=str(b))
    assert (first["folder"], second["folder"]) == ("2026-11-06", "2026-11-06 (2)")
    data = cfg.data_for(cfg.courses["Data Mining"])
    assert len(list(data.glob("*.original.srt"))) == 2
    call(cfg, "remove_ready", course="Data Mining", id=second["ready"]["id"])
    assert len(list(data.glob("*.original.srt"))) == 1 and b.exists()


def test_bad_files_are_turned_down_at_once(cfg, tmp_path):
    scan = make_pdf(tmp_path / "Week 6.pdf", [[]])
    reply = app_server.handle(cfg, {"id": 1, "method": "add_recording", "params": {"course": "Data Mining", "path": str(scan)}})
    assert "probably a scan" in reply["error"]
    empty = tmp_path / "empty.md"
    empty.write_text("\n\n", encoding="utf-8")
    reply = app_server.handle(cfg, {"id": 1, "method": "add_recording", "params": {"course": "Data Mining", "path": str(empty)}})
    assert "no transcript text" in reply["error"]
    assert lectures(cfg) == []


def test_stenny_add_takes_a_transcript(cfg, tmp_path, monkeypatch, capsys):
    sent = tmp_path / "Lecture.txt"
    sent.write_text(OTTER, encoding="utf-8")
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(sys, "argv", ["stenny", "add", str(sent), "--course", "Data Mining", "--date", "2026-11-03"])
    cli.main()
    assert "Adding a transcript, not transcribing" in capsys.readouterr().out
    assert LectureFiles(cfg.folder / "Data Mining" / "2026-11-03", "Data Mining").existing("transcript")
