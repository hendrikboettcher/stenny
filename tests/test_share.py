from types import SimpleNamespace

import pytest

from stenny import share
from stenny.render import Lecture, summary_md, transcript_md


def lecture(**kw) -> Lecture:
    base = dict(course="Calculus", date="2026-10-20", start_time="10:00", duration=3600, languages=["en"],
                language_note="en", model="some-model", source="live 2026-10-20 10:00:00", number=3,
                audio="~/Lectures Data/Calculus/2026-10-20.opus", raw_file="~/Lectures Data/Calculus/x (raw).md",
                microphone="Alex's iPhone Microphone", backup_microphone="Alex's MacBook Microphone",
                structure="ai (claude-code haiku)", topics=["Limits"],
                pauses=[(600.0, 660.0, "backup")], names={"transcript": "Calculus 2026-10-20.md"})
    return Lecture(**{**base, **kw})


PARAGRAPHS = [{"start": 4.0, "text": "Welcome."}, {"start": 700.0, "text": "Limits next."}]
SECTIONS = [{"at": 0, "title": "Intro"}, {"at": 1, "title": "Limits", "slides": (3, 5)}]


@pytest.fixture
def cfg(tmp_path):
    lec = lecture(slides=12, names={"transcript": "Calculus 2026-10-20.md", "slides": "Calculus 2026-10-20 (slides).pdf"})
    folder = tmp_path / "Lectures" / "Calculus" / "2026-10-20"
    folder.mkdir(parents=True)
    (folder / "Calculus 2026-10-20.md").write_text(transcript_md(lec, PARAGRAPHS, SECTIONS), encoding="utf-8")
    (folder / "Calculus 2026-10-20 (summary).md").write_text(
        summary_md(lec, "## Overview\nAbout [[_Topics/Limits|limits]]. [0:00:04]", "Claude Code (haiku)"),
        encoding="utf-8")
    talk = tmp_path / "Recordings" / "Some Talk" / "2026-10-22"
    talk.mkdir(parents=True)
    (talk / "Some Talk 2026-10-22.md").write_text(
        transcript_md(lecture(course="Some Talk", kind="event", number=0, date="2026-10-22", status="live"),
                      PARAGRAPHS, SECTIONS), encoding="utf-8")
    return SimpleNamespace(lectures_root=tmp_path / "Lectures", events_root=tmp_path / "Recordings")


def test_library_lists_lectures_and_talks(cfg):
    entries = share.library(cfg)
    assert [(e["kind"], e["course"], e["folder"], e["number"], e["status"]) for e in entries] == [
        ("lecture", "Calculus", "2026-10-20", 3, "final"), ("event", "Some Talk", "2026-10-22", 0, "live")]
    assert entries[0]["summary"].endswith("(summary).md") and entries[1]["summary"] is None


def test_shared_copy_keeps_the_text_and_drops_what_is_private(cfg, tmp_path):
    path = share.share(share.find(cfg, "calculus", "2026-10-20"), out_dir=tmp_path / "out")
    assert path.name == "Calculus – Lecture 3 (2026-10-20).md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Calculus – Lecture 3 (2026-10-20)")
    for kept in ["Automatic transcript", "**Audio only:**", "## [0:00:04] Intro", "**[0:11:40]** Limits next.",
                 "(Slides 3–5)", "this part comes from the backup microphone and may be quieter"]:
        assert kept in text
    for gone in ["---\n", "Lectures Data", "opus", "some-model", "iPhone", "MacBook", "[[", "Every segment"]:
        assert gone not in text


def test_summary_goes_on_top_as_a_section(cfg, tmp_path):
    text = share.shared_text(share.find(cfg, "Calculus", "2026-10-20"), with_summary=True)
    assert text.index("## Summary") < text.index("### Overview") < text.index("## [0:00:04] Intro")
    assert "AI-generated summary of the transcript below." in text
    assert "About limits. [0:00:04]" in text and "[[" not in text and "haiku" not in text


def test_a_talk_is_named_without_a_lecture_number(cfg, tmp_path):
    entry = share.find(cfg, "Some Talk", "2026-10-22", event=True)
    assert share.share(entry, out_dir=tmp_path).name == "Some Talk (2026-10-22).md"
    with pytest.raises(share.ShareError):
        share.find(cfg, "Some Talk", "2026-10-22")  # not a lecture


def test_html_keeps_the_structure_and_greys_timestamps():
    page = share.to_html("# Title\n\n> Note one\n>\n> **Audio only:** two\n\n*[Recording paused 0:00:03–0:07:30]*\n\n"
                         "## Part\n\n**[0:01:02]** Some *word* & more.\n\n- point [0:00:04]\n- other\n\n---\n", "Title")
    assert "<h1>Title</h1>" in page and "<h2>Part</h2>" in page
    assert "<blockquote><p>Note one</p><p><strong>Audio only:</strong> two</p></blockquote>" in page
    assert "<em>[Recording paused 0:00:03–0:07:30]</em>" in page
    assert '<p><span class="ts">[0:01:02]</span> Some <em>word</em> &amp; more.</p>' in page
    assert '<ul><li>point <span class="ts">[0:00:04]</span></li><li>other</li></ul>' in page
    assert "<hr>" in page


def test_share_writes_word_and_html(cfg, tmp_path):
    entry = share.find(cfg, "Calculus", "2026-10-20")
    docx = share.share(entry, with_summary=True, out_dir=tmp_path / "out", fmt="docx")
    assert docx.name == "Calculus – Lecture 3 (2026-10-20).docx" and docx.read_bytes()[:2] == b"PK"
    page = share.share(entry, with_summary=True, out_dir=tmp_path / "out", fmt="html").read_text(encoding="utf-8")
    assert "<h2>Summary</h2>" in page and "Lectures Data" not in page and "MacBook" not in page
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [
        "Calculus – Lecture 3 (2026-10-20).docx", "Calculus – Lecture 3 (2026-10-20).html"]
    with pytest.raises(share.ShareError):
        share.share(entry, fmt="pdf")
