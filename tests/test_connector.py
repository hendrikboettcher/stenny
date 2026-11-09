"""The connector for Claude (`stenny mcp`): its tools against a small made-up vault, and the MCP server itself."""

import asyncio

import pytest
from pdfgen import make_pdf

from stenny import config as config_mod
from stenny import connector
from stenny.connector import ConnectorError
from stenny.render import Lecture, summary_md, transcript_md

GLOSSARY = """---
course: "Calculus"
---
# Glossary: Calculus

Some words a person wrote.

## Terms
- epsilon-delta

## Corrections
- Koshi → Cauchy
"""


def lecture(**kw) -> Lecture:
    base = dict(course="Calculus", date="2026-10-20", start_time="10:00", duration=3600, languages=["en"],
                language_note="en", model="some-model", source="live 2026-10-20 10:00:00", number=1,
                audio="~/Lectures Data/Calculus/2026-10-20.opus", raw_file="~/Lectures Data/Calculus/x (raw).md",
                microphone="Alex's iPhone Microphone", backup_microphone="Alex's MacBook Microphone",
                structure="ai (claude-code haiku)", topics=["Limits"])
    return Lecture(**{**base, **kw})


def write_lecture(root, lec: Lecture, paragraphs, sections, summary: str | None = None):
    folder = root / lec.course / lec.date
    folder.mkdir(parents=True)
    stem = f"{lec.course} {lec.date}"
    lec.names = {"transcript": f"{stem}.md", "slides": f"{stem} (slides).pdf"}
    (folder / f"{stem}.md").write_text(transcript_md(lec, paragraphs, sections), encoding="utf-8")
    if summary:
        (folder / f"{stem} (summary).md").write_text(summary_md(lec, summary, "Claude Code (haiku)"), encoding="utf-8")
    return folder


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    lectures = tmp_path / "Vault" / "Lectures"
    write_lecture(lectures, lecture(),
                  [{"start": 4.0, "text": "Welcome. Today: limits and the Bolzano-Weierstrass theorem."},
                   {"start": 700.0, "text": "Every bounded sequence has a convergent subsequence, says Bolzano-Weierstrass."},
                   {"start": 1500.0, "text": "A Cauchy sequence, see the slide, gets closer and closer."}],
                  [{"at": 0, "title": "Intro"}, {"at": 1, "title": "Bounded sequences"}],
                  summary="## Overview\nAbout [[_Topics/Limits|limits]]. [0:00:04]")
    second = write_lecture(lectures, lecture(date="2026-10-27", number=2, slides=2, topics=["Continuity"]),
                           [{"start": 10.0, "text": "Continuity, epsilon-delta: as you can see here."},
                            {"start": 3700.0, "text": "K-means is not part of this course. Hilbert appears once."}],
                           [{"at": 0, "title": "Continuity"}])
    make_pdf(second / "Calculus 2026-10-27 (slides).pdf",
             [["Continuity", "The Intermediate Value Theorem by Bolzano"], ["Uniform continuity on compact sets"]])
    write_lecture(lectures, lecture(date="2026-11-03", number=3, status="live"),
                  [{"start": 5.0, "text": "A draft about derivatives."}], [])
    (lectures / "_Glossaries").mkdir()
    (lectures / "_Glossaries" / "Calculus (glossary).md").write_text(GLOSSARY, encoding="utf-8")
    write_lecture(lectures / "Talks",
                  lecture(course="Some Talk", kind="event", number=0, date="2026-10-22", topics=[]),
                  [{"start": 3.0, "text": "A talk about limits of AI."}], [])
    toml = tmp_path / "config.toml"
    toml.write_text(f'folder = "{(lectures).as_posix()}"\n\n[courses.Calculus]\n'
                    'language = "en"\naliases = ["Calc"]\n\n[courses."Linear Algebra"]\nlanguage = "en"\n',
                    encoding="utf-8")
    monkeypatch.setenv("STENNY_CONFIG", str(toml))
    return config_mod.load()


PRIVATE = ["Lectures Data", "opus", "some-model", "iPhone", "MacBook", "[[", "---\n", "Every segment"]


def assert_clean(text: str, cfg) -> None:
    for gone in [*PRIVATE, str(cfg.folder)]:
        assert gone not in text


def test_list_courses_and_lectures(cfg):
    text = connector.list_courses(cfg)
    assert "- Calculus: 3 lectures, 2026-10-20 to 2026-11-03, glossary with 1 terms" in text
    assert "Configured but no transcripts yet: Linear Algebra." in text
    assert "Talks and events:\n- Some Talk: 2026-10-22" in text
    text = connector.list_lectures(cfg, "calc")  # an alias, any case
    assert "## Lecture 1 (2026-10-20)" in text and "Topics: Limits" in text
    assert "Sections: [0:00:04] Intro; [0:11:40] Bounded sequences" in text
    assert "Has: transcript, summary\n" in text and "slide" not in text.casefold()  # slides aren't part of Stenny
    assert "## Lecture 3 (2026-11-03) [not transcribed yet" in text
    assert_clean(text, cfg)


@pytest.mark.parametrize("which,date", [("2", "2026-10-27"), ("Lecture 2", "2026-10-27"), ("2026-10-27", "2026-10-27"),
                                        ("latest", "2026-11-03"), (1, "2026-10-20")])
def test_lectures_by_number_date_or_latest(cfg, which, date):
    assert connector._lecture(cfg, "Calculus", which)["folder"] == date


def test_unknown_course_or_lecture_names_the_known_ones(cfg):
    with pytest.raises(ConnectorError, match="Known: Calculus, Some Talk"):
        connector.list_lectures(cfg, "Physics")
    with pytest.raises(ConnectorError, match=r"Lecture 1 \(2026-10-20\), Lecture 2"):
        connector.get_transcript(cfg, "Calculus", "9")


def test_transcript_is_clean_text(cfg):
    text = connector.get_transcript(cfg, "Calculus", "1")
    assert text.startswith("[Calculus, Lecture 1 (2026-10-20) · part 1 of 1 · 0:00:04–0:25:00]")
    assert "# Calculus – Lecture 1 (2026-10-20)" in text and "**[0:11:40]** Every bounded sequence" in text
    assert "**Audio only:**" in text
    assert_clean(text, cfg)


def test_long_transcripts_come_in_parts(cfg, monkeypatch):
    monkeypatch.setattr(connector, "PART_CHARS", 200)
    first = connector.get_transcript(cfg, "Calculus", "1")
    n = int(first.split(" of ", 1)[1].split(" ", 1)[0])
    assert n > 1 and "next: get_transcript with part=2" in first
    last = connector.get_transcript(cfg, "Calculus", "1", part=n)
    assert "next:" not in last and "Cauchy sequence" in last
    assert "Cauchy sequence" in connector.get_transcript(cfg, "Calculus", "1", at="0:25:10")
    with pytest.raises(ConnectorError, match="part 1 to"):
        connector.get_transcript(cfg, "Calculus", "1", part=n + 1)
    with pytest.raises(ConnectorError, match="timestamp"):
        connector.get_transcript(cfg, "Calculus", "1", at="noon")


def test_summary(cfg):
    text = connector.get_summary(cfg, "Calculus", "1")
    assert text.startswith("# Summary: Calculus, Lecture 1 (2026-10-20)")
    assert "About limits. [0:00:04]" in text
    assert_clean(text, cfg)
    with pytest.raises(ConnectorError, match="no summary"):
        connector.get_summary(cfg, "Calculus", "2")


def test_search_gives_lecture_date_timestamp_and_section(cfg):
    text = connector.search_lectures(cfg, "bolzano weierstrass")
    assert text.startswith("2 matches")
    assert "- Calculus, Lecture 1 (2026-10-20) [0:00:04] — section: Intro" in text
    assert "- Calculus, Lecture 1 (2026-10-20) [0:11:40] — section: Bounded sequences" in text
    assert_clean(text, cfg)


def test_hits_and_transcripts_say_how_to_cite_with_the_full_timestamp(cfg):
    # "0:02" for 0:02:05 reads as two seconds, so every hit carries a citation ready to copy
    everywhere = connector.search_lectures(cfg, "bolzano weierstrass")
    assert "cite as: Calculus, Lecture 1 [0:11:40]" in everywhere
    one_course = connector.search_lectures(cfg, "bolzano weierstrass", course="Calculus")
    assert "cite as: Lecture 1 [0:11:40]" in one_course
    talk = connector.search_lectures(cfg, "limits")
    assert "cite as: Some Talk, " in talk
    transcript = connector.get_transcript(cfg, "Calculus", "1")
    assert "Name this lecture once in your answer (Lecture 1)" in transcript and "e.g. [0:00:04]" in transcript


def test_search_matching(cfg):
    assert "1 match " in connector.search_lectures(cfg, "kmeans")          # "K-means" in the text
    assert "1 match " in connector.search_lectures(cfg, "continu", "Calc")  # start of a word
    assert "No matches" in connector.search_lectures(cfg, "inuity")        # but not the middle of one
    assert "2 matches" in connector.search_lectures(cfg, "Weierstraß")      # ß = ss
    # the whole phrase first, then paragraphs with all of its words
    text = connector.search_lectures(cfg, "limits")
    assert text.index("Calculus, Lecture 1") < text.index("Some Talk")
    one_course = connector.search_lectures(cfg, "limits", course="Calculus")
    assert "Some Talk" not in one_course
    assert "[not transcribed yet" in connector.search_lectures(cfg, "derivatives")


def test_search_falls_back_to_the_transcribed_spelling(cfg):
    text = connector.search_lectures(cfg, "Cauchi")
    assert "closest spelling" in text and "'cauchi' → 'cauchy'" in text and "1 match " in text
    text = connector.search_lectures(cfg, "bounded nothing")
    assert "No matches" in text and "'bounded' 1×" in text and "'nothing' 0×" in text


def test_glossary(cfg):
    text = connector.get_glossary(cfg, "Calculus")
    assert "## Terms (the student's)\n- epsilon-delta" in text
    assert "## Corrections (the student's)\n- Koshi → Cauchy" in text and "(none)" in text
    assert "slide" not in text.casefold()
    assert connector.get_glossary(cfg, "Some Talk") == "Some Talk has no glossary yet."


def test_slides_text(cfg):
    text = connector.get_slides_text(cfg, "Calculus", "2")
    assert text.startswith("[Slides of Calculus, Lecture 2 (2026-10-27): 2 pages, text only]")
    assert "[Slide 1] Continuity The Intermediate Value Theorem by Bolzano" in text
    assert "[Slide 2] Uniform continuity" in text
    assert "[Slide 1]" not in connector.get_slides_text(cfg, "Calculus", "2", first_page=2)
    assert "has no slides" in connector.get_slides_text(cfg, "Calculus", "1")


def test_suggested_terms_are_taken_as_given_into_the_automatic_section(cfg):
    path = cfg.folder / "_Glossaries" / "Calculus (glossary).md"
    text = connector.suggest_glossary_terms(
        cfg, "Calculus", ["Bolzano-Weierstrass", "Hilbert", "epsilon-delta", "Koshi", "mean", "[[Link]]"])
    assert "Added to Calculus's glossary (Terms added automatically): Bolzano-Weierstrass, Hilbert" in text
    assert "Already in the glossary (or corrected away by a correction): epsilon-delta, Koshi." in text
    assert "Not added: mean (a common word" in text and "[[Link]] (not a name or term" in text
    after = path.read_text(encoding="utf-8")
    assert after.startswith(GLOSSARY.split("## Corrections")[0])  # the student's part is untouched
    assert after.rstrip().endswith("## Corrections\n- Koshi → Cauchy")
    assert "## Terms (added automatically)" in after and "- Hilbert" in after  # no evidence needed any more
    again = connector.suggest_glossary_terms(cfg, "Calculus", ["Bolzano-Weierstrass"])
    assert "Already in the glossary" in again and path.read_text(encoding="utf-8") == after
    with pytest.raises(ConnectorError, match="talk"):
        connector.suggest_glossary_terms(cfg, "Some Talk", ["Something"])


def test_suggested_corrections_are_taken_as_given_and_never_beat_the_students(cfg):
    from stenny.glossary import Glossary

    path = cfg.folder / "_Glossaries" / "Calculus (glossary).md"
    text = connector.suggest_glossary_corrections(cfg, "Calculus", [
        "Bolzano Weierstrass → Bolzano-Weierstrass", "Hilbird -> Hilbert", "Koshi → Kauchy", "Cauchy → Cauchie",
        "the → a", "no arrow here", "one two three four five six → seven"])
    assert "Added to Calculus's glossary: Bolzano Weierstrass → Bolzano-Weierstrass; Hilbird → Hilbert." in text
    assert "Koshi → Kauchy (the student corrects 'Koshi' to 'Cauchy' themselves)" in text
    assert "Cauchy → Cauchie (the student lists 'Cauchy' as a correct spelling)" in text
    assert "the → a ('the' is too common a word" in text and "no arrow here (write it as" in text
    assert "(too long for a spelling fix" in text
    after = path.read_text(encoding="utf-8")
    assert after.startswith(GLOSSARY.split("## Corrections")[0])  # the student's part is untouched
    assert "## Corrections\n- Koshi → Cauchy\n\n## Corrections (added automatically)" in after
    g = Glossary.load(path)
    assert g.own_corrections == [("Koshi", "Cauchy")]
    assert g.auto_corrections == [("Bolzano Weierstrass", "Bolzano-Weierstrass"), ("Hilbird", "Hilbert")]
    assert {"Bolzano-Weierstrass", "Hilbert"} <= set(g.auto_terms)  # the right spellings go into Whisper's prompt
    # applied word for word, the student's first
    assert g.apply_corrections("Koshi and Hilbird, Bolzano Weierstrass.")[0] == \
        "Cauchy and Hilbert, Bolzano-Weierstrass."
    # Claude may change its own correction, and says when nothing changed
    assert "Replaced an earlier automatic correction: Hilbird → Hilbertt." in \
        connector.suggest_glossary_corrections(cfg, "Calculus", ["Hilbird → Hilbertt"])
    assert "Already in the glossary: Hilbird → Hilbertt." in \
        connector.suggest_glossary_corrections(cfg, "Calculus", ["Hilbird → Hilbertt"])
    with pytest.raises(ConnectorError, match="talk"):
        connector.suggest_glossary_corrections(cfg, "Some Talk", ["a b → c"])


def test_mcp_server_serves_the_tools(cfg):
    from mcp import Client

    from stenny.mcp_server import build

    server = build()
    assert "slide" not in server.instructions.casefold()
    assert "suggest_glossary_corrections" in server.instructions
    assert "search_lectures" in server.instructions and "name the lecture once" in server.instructions \
        and "at most once per point" in server.instructions and "[1:58:41]" in server.instructions

    async def session():
        async with Client(server) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            hit = await client.call_tool("search_lectures", {"query": "Cauchy", "course": "Calculus"})
            bad = await client.call_tool("list_lectures", {"course": "Physics"})
            prompt = await client.get_prompt("study", {"course": "Calculus"})
            return tools, hit, bad, prompt

    tools, hit, bad, prompt = asyncio.run(session())
    assert set(tools) == {"list_courses", "list_lectures", "get_transcript", "get_summary", "search_lectures",
                          "get_glossary", "suggest_glossary_terms", "suggest_glossary_corrections"}
    writes = {"suggest_glossary_terms", "suggest_glossary_corrections"}
    assert all(t.annotations.read_only_hint for name, t in tools.items() if name not in writes)
    assert not any(tools[name].annotations.read_only_hint for name in writes)
    assert not any("slide" in (t.description or "").casefold() for t in tools.values())
    assert not hit.is_error and "Calculus, Lecture 1 (2026-10-20) [0:25:00]" in hit.content[0].text
    assert bad.is_error and "Known: Calculus" in bad.content[0].text
    assert "study Calculus" in prompt.messages[0].content.text
