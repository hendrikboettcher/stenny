"""End-to-end pipeline test with a fake Whisper engine and a fake LLM (no models, no network)."""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from pdfgen import make_pdf

from stenny import pipeline
from stenny.config import Config, Course
from stenny.llm import Usage
from stenny.textproc import SAMPLE_RATE
from stenny.vault import LectureFiles

RATE = 16000


def lf(folder, kind="transcript"):
    """A lecture file under its new name, e.g. "Calculus 2026-10-15 (summary).md". The raw output is outside
    the vault, next to the audio: <data_dir>/Calculus/Calculus 2026-10-15 (raw).md."""
    from stenny.vault import LectureFiles
    folder = Path(folder)
    if kind == "raw":
        data_dir = folder.parents[3].parent / "audio"  # the `setup` fixture's data_dir (tmp/audio)
        return data_dir / "Calculus" / f"Calculus {folder.name} (raw).md"
    return LectureFiles(folder, "Calculus").new(kind)


class FakeEngine:
    """Returns one segment per call, saying which chunk and language it saw."""

    def __init__(self, probs, logprob=-0.2):
        self.probs, self.logprob = probs, logprob
        self.prompts = []
        self.calls = 0

    def transcribe(self, audio, language, prompt=None, fast=False):
        self.prompts.append(prompt)
        self.calls += 1
        filler = " suite" * int(len(audio) / RATE * 2)  # realistic text density (~12 letters/s)
        return [{"start": 0.0, "end": len(audio) / RATE, "text": f"Phrase {self.calls} en {language} avec Rimane.{filler}",
                 "no_speech_prob": 0.0, "avg_logprob": self.logprob}]

    def language_probs(self, clip, allowed):
        return {lang: self.probs.get(lang, 0.0) for lang in allowed}


class FakeLLM:
    name = "Fake LLM"

    def complete(self, system, prompt):
        if "JSON" in system:
            return json.dumps({"sections": [{"at": 0, "title": "Introduction"}],
                               "term_fixes": [{"p": 0, "from": "Rimane", "to": "Riemann"},
                                              {"p": 0, "from": "Phrase", "to": "Théorème inventé"}]}), Usage(100, 20)
        return "## Overview\nUne phrase. [0:00:00]", Usage(100, 10)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    (tmp_path / "vault" / "Areas").mkdir(parents=True)
    cfg = Config(vault=tmp_path / "vault", lectures_dir="Areas/Lectures", data_dir=tmp_path / "audio",
                 keep_audio=True, llm_backend="fake")
    monkeypatch.setattr(pipeline, "get_backend", lambda cfg: FakeLLM())
    wav = tmp_path / "rec.wav"
    noise = (np.random.default_rng(1).normal(0, 0.1, 70 * RATE) * 32767).astype(np.int16)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "-",
                    str(wav)], input=noise.tobytes(), check=True)
    return cfg, wav


def test_process_writes_raw_transcript_summary_and_audio(setup):
    cfg, wav = setup
    course = Course("Calculus", language="fr", languages=["fr"], tags=["maths"])
    gloss = cfg.lectures_root / "_Glossaries" / "Calculus.md"
    gloss.parent.mkdir(parents=True)
    gloss.write_text("## Terms\n- Riemann\n", encoding="utf-8")
    engine = FakeEngine({"fr": 1.0})

    from datetime import datetime
    info = pipeline.process(wav, cfg, course, engine, when=datetime(2026, 10, 15, 9, 15))

    folder = Path(info["folder"])
    assert folder == cfg.lectures_root / "Calculus" / "2026-10-15"
    assert sorted(p.name for p in folder.iterdir()) == ["Calculus 2026-10-15 (summary).md", "Calculus 2026-10-15.md"]  # the raw output is outside the vault
    assert all(p == "Riemann." for p in engine.prompts)  # glossary prompt on every chunk
    transcript = lf(folder).read_text()
    raw = lf(folder, "raw").read_text()
    assert "## [0:00:00] Introduction" in transcript
    assert "avec Riemann." in transcript and "Rimane" not in transcript      # glossary fix applied
    assert "Théorème inventé" not in transcript                                # invented change rejected
    assert "Rimane" in raw                                                     # raw stays untouched
    assert 'structure: "ai (Fake LLM)"' in transcript
    assert [f["reason"] for f in info["term_fixes_rejected"]] == ["not a glossary term"]
    assert (cfg.data_dir / "Calculus" / "2026-10-15.opus").exists()
    assert (cfg.data_dir / "Calculus" / "2026-10-15.segments.json").exists()
    from stenny.vault import Vault
    assert Vault(cfg.lectures_root).violations() == []


def test_mixed_language_course_switches_per_chunk(setup, monkeypatch):
    cfg, wav = setup
    cfg.keep_audio = False
    course = Course("Mixed", language="mixed", languages=["fr", "en"])
    engine = FakeEngine({"fr": 0.2, "en": 0.8})
    info = pipeline.process(wav, cfg, course, engine, use_llm=False)
    assert info["language"].startswith("mixed (en")
    transcript = LectureFiles(Path(info["folder"]), "Mixed").new("transcript").read_text()
    assert "en en avec" in transcript and 'language: "en"' in transcript


class SlideLLM:
    """Proposes a slide range, a slide-based fix, and a fix toward a slide word that isn't a near spelling."""
    name = "Slide LLM"

    def __init__(self):
        self.prompts = []

    def complete(self, system, prompt):
        self.prompts.append(prompt)
        if "JSON" in system:
            return json.dumps({"sections": [{"at": 0, "title": "Intégrales", "slides": [1, 2]}],
                               "term_fixes": [{"p": 0, "from": "Rimane", "to": "Riemann"},
                                              {"p": 0, "from": "Phrase", "to": "Darboux"}]}), Usage(100, 20)
        return "## Overview\nRésumé. [0:00:00]", Usage(100, 10)


def test_add_with_slides_and_microphone_from_metadata(setup, tmp_path):
    cfg, wav = setup
    cfg.keep_audio = False
    m4a = tmp_path / "memo.m4a"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(wav), "-c:a", "aac", "-metadata",
                    "com.apple.quicktime.model=iPhone 15 Pro", "-movflags", "use_metadata_tags", str(m4a)], check=True)
    deck = make_pdf(tmp_path / "Cours 3.pdf", [["Intégrale de Riemann", "Les sommes de Darboux"], ["Fin"]])
    course = Course("Calculus", language="fr", languages=["fr"])
    gloss = cfg.lectures_root / "_Glossaries" / "Calculus.md"
    gloss.parent.mkdir(parents=True)
    gloss.write_text("## Terms\n- Cauchy\n", encoding="utf-8")
    engine = FakeEngine({"fr": 1.0})
    info = pipeline.process(m4a, cfg, course, engine, use_llm=False, slides_src=deck)

    folder = Path(info["folder"])
    assert lf(folder, "slides").read_bytes() == deck.read_bytes()            # stored under the lecture's name
    assert set(engine.prompts) == {"Cauchy, Riemann, Darboux."}   # glossary first, then slide terms
    for kind in ("transcript", "raw"):
        assert 'microphone: "iPhone 15 Pro"' in lf(folder, kind).read_text()
    assert f'slides: "[[{lf(folder, "slides").name}]]"' in lf(folder).read_text()
    from stenny.vault import Vault
    assert Vault(cfg.lectures_root).violations() == []


def test_refresh_uses_slides_added_later_without_retranscribing(setup, monkeypatch):
    cfg, wav = setup
    cfg.keep_audio = False
    course = Course("Calculus", language="fr", languages=["fr"])
    engine = FakeEngine({"fr": 1.0}, logprob=-0.9)  # Whisper unsure everywhere
    from datetime import datetime
    info = pipeline.process(wav, cfg, course, engine, use_llm=False, when=datetime(2026, 10, 15, 9, 15))
    folder = Path(info["folder"])
    calls, raw = engine.calls, lf(folder, "raw").read_text()
    meta_path = cfg.data_dir / "Calculus" / "2026-10-15.segments.json"
    segments = json.loads(meta_path.read_text())["segments"]
    assert "Rimane" in lf(folder).read_text()

    make_pdf(folder / "Slides.pdf", [["Intégrale de Riemann"], ["Les sommes de Darboux"], ["Exercices"]])
    llm = SlideLLM()
    monkeypatch.setattr(pipeline, "get_backend", lambda cfg: llm)
    info = pipeline.refresh(cfg, course, "2026-10-15")

    assert engine.calls == calls                                     # nothing re-transcribed
    assert lf(folder, "raw").read_text() == raw         # raw transcript untouched
    assert "[Slide 2] Les sommes de Darboux" in llm.prompts[0]
    transcript = lf(folder).read_text()
    assert "## [0:00:00] Intégrales ([[Slides.pdf#page=1|Slides 1–2]])" in transcript
    assert "avec Riemann." in transcript and "Rimane" not in transcript and "Darboux" not in transcript
    assert 'structure: "ai (Slide LLM)"' in transcript and "start: \"09:15\"" in transcript
    assert info["term_fixes_applied"][0] == {"p": 0, "from": "Rimane", "to": "Riemann", "basis": "slides"}
    assert [r["reason"] for r in info["term_fixes_rejected"]] == ["not a near spelling of the original"]
    assert lf(folder, "summary").exists()
    data = json.loads(meta_path.read_text())
    assert data["segments"] == segments and data["meta"]["slides"] == 3 and data["meta"]["refreshed_at"]


def test_refresh_live_lecture_from_older_files(setup):
    """A live lecture processed before segments.json kept the lecture details: refresh reads them from
    Transcript.md's frontmatter and session.json (pauses, microphone) and rebuilds the same transcript."""
    cfg, wav = setup
    cfg.keep_audio = False
    course = Course("Calculus", language="fr", languages=["fr"])
    from datetime import datetime
    live = pipeline.LiveInfo(started_at=datetime(2026, 10, 15, 9, 15), folder="2026-10-15",
                             spans=[(0, 0.0), (30 * RATE, 45.0)], pauses=[(30.0, 45.0, "paused")],
                             source="live 2026-10-15 09:15", microphone="Alex’s iPhone Microphone")
    info = pipeline.process(wav, cfg, course, FakeEngine({"fr": 1.0}), use_llm=False, live=live)
    folder = Path(info["folder"])
    before = lf(folder).read_text()
    assert 'microphone: "Alex’s iPhone Microphone"' in before and "*[Recording paused 0:00:30–0:00:45]*" in before

    meta_path = cfg.data_dir / "Calculus" / "2026-10-15.segments.json"
    data = json.loads(meta_path.read_text())
    del data["meta"]["lecture"]                                       # the older segments.json format
    meta_path.write_text(json.dumps(data))
    (cfg.data_dir / "Calculus" / "2026-10-15.session.json").write_text(json.dumps({
        "course": "Calculus", "started_at": "2026-10-15T09:15:00", "folder": "2026-10-15", "source": live.source,
        "microphone": live.microphone, "spans": live.spans, "pauses": [list(p) for p in live.pauses]}))
    lf(folder).write_text(before.replace('microphone: "Alex’s iPhone Microphone"\n', ""))

    pipeline.refresh(cfg, course, "2026-10-15", use_llm=False)
    assert lf(folder).read_text() == before
    assert "lecture" in json.loads(meta_path.read_text())["meta"]     # upgraded for next time


def test_refresh_errors_are_clear(setup):
    cfg, _ = setup
    course = Course("Calculus", language="fr", languages=["fr"])
    with pytest.raises(pipeline.LectureError, match="No lecture folder"):
        pipeline.refresh(cfg, course, "2026-10-15")
    from stenny.vault import Vault
    Vault(cfg.lectures_root).write(cfg.lectures_root / "Calculus" / "2026-10-15" / "Transcript.md", "live draft")
    with pytest.raises(pipeline.LectureError, match="final pass first"):
        pipeline.refresh(cfg, course, "2026-10-15")


def speech_like(seconds: float, seed: int) -> np.ndarray:
    """Syllable-like bursts: a loudness rhythm two microphones in the same room share."""
    rng = np.random.default_rng(seed)
    env = np.repeat(rng.random(int(seconds * 5)) > 0.4, RATE // 5).astype(np.float32)
    return env * 0.3 + 0.002


def test_backup_microphone_fills_a_gap_of_the_main_one(setup, tmp_path):
    """The iPhone (main) sent only zeros 30-40 s; the Mac's microphone (backup) heard the lecture, but its
    clock-based timeline runs 0.4 s ahead. The final pass lines the two up and fills the gap from it."""
    cfg, _ = setup
    course = Course("Calculus", language="fr", languages=["fr"])
    rng = np.random.default_rng(3)
    env = speech_like(60, seed=5)
    main = env * rng.normal(0, 1, len(env))
    main[30 * RATE:40 * RATE] = 0                                    # phone in use: digital silence
    backup = 0.3 * env * rng.normal(0, 1, len(env))                  # quieter, different noise
    main_pcm, backup_pcm = tmp_path / "x.live.pcm", tmp_path / "x.backup.pcm"
    main_pcm.write_bytes((np.clip(main, -1, 1) * 32767).astype(np.int16).tobytes())
    backup_pcm.write_bytes((np.clip(backup, -1, 1) * 32767).astype(np.int16).tobytes())

    shift = pipeline.backup_shift(main, [(0, 0.0)], backup, [(0, 0.4)], 30.0, 40.0)
    assert abs(shift - 0.4) <= 0.02
    assert pipeline.backup_shift(main, [(0, 0.0)], rng.normal(0, 0.1, len(env)), [(0, 0.4)], 30.0, 40.0) == 0.0

    from datetime import datetime
    live = pipeline.LiveInfo(started_at=datetime(2026, 10, 15, 9, 15), folder="2026-10-15", spans=[(0, 0.0)],
                             pauses=[(30.0, 40.0, "no input")], source="live 2026-10-15 09:15",
                             microphone="Alex’s iPhone Microphone",
                             backup={"recording": str(backup_pcm), "microphone": "MacBook Air Microphone",
                                     "spans": [(0, 0.4)]})
    info = pipeline.process(main_pcm, cfg, course, FakeEngine({"fr": 1.0}), use_llm=False, live=live)
    assert info["filled_from_backup_s"] == 10.0
    data = json.loads((cfg.data_dir / "Calculus" / "2026-10-15.segments.json").read_text())
    segs = data["segments"]
    from_backup = [s for s in segs if s.get("backup")]
    assert from_backup and all(30 <= (s["start"] + s["end"]) / 2 < 40 for s in from_backup)
    assert abs(min(s["start"] for s in from_backup) - 29.0) < 0.05   # 1 s margin, on the main timeline
    assert not any(30 < s["start"] < 40 or 30 < s["end"] < 40 for s in segs if not s.get("backup"))
    first = next(i for i, s in enumerate(segs) if s.get("backup"))
    assert all(s["start"] < 30 for s in segs[:first])                  # main text before the gap comes first
    folder = Path(info["folder"])
    transcript = lf(folder).read_text()
    assert 'backup_microphone: "MacBook Air Microphone"' in transcript
    marker = "*[0:00:30–0:00:40: no audio from the main microphone; this part comes from the backup microphone"
    assert marker in transcript and "paused:" not in transcript
    assert transcript.index(marker) < transcript.index("**[0:00:29]**")   # marker before the backup text
    assert "*(backup microphone)*" in lf(folder, "raw").read_text()
    assert (cfg.data_dir / "Calculus" / "2026-10-15.backup.opus").exists()


def test_lecture_names_numbers_and_course_files(setup):
    """Files are named after course and date, numbered per course, and gathered in _Course Files/."""
    cfg, wav = setup
    cfg.keep_audio = False
    course = Course("Calculus", language="fr", languages=["fr"])
    from datetime import datetime
    second = pipeline.process(wav, cfg, course, FakeEngine({"fr": 1.0}), when=datetime(2026, 10, 22, 9, 15))
    first = pipeline.process(wav, cfg, course, FakeEngine({"fr": 1.0}), when=datetime(2026, 10, 15, 9, 15))
    from stenny.render import parse_frontmatter
    t1 = lf(first["folder"]).read_text()
    assert parse_frontmatter(t1)["lecture"] == 1 and "# Calculus – Lecture 1 (2026-10-15)" in t1
    assert f"`{str(lf(first['folder'], 'raw')).replace(str(Path.home()), '~')}` (outside the vault)" in t1
    assert "`Calculus 2026-10-15.md` in the vault" in lf(first["folder"], "raw").read_text()  # and back
    assert "[[Calculus 2026-10-15]]" in lf(first["folder"], "summary").read_text()
    # the 22nd was made before the 15th was added: rebuilding the course files (done by process) renumbered it
    t2 = LectureFiles(Path(second["folder"]), "Calculus").new("transcript").read_text()
    assert parse_frontmatter(t2)["lecture"] == 2 and "# Calculus – Lecture 2 (2026-10-22)" in t2
    assert "# Summary: Calculus – Lecture 2 (2026-10-22)" in LectureFiles(Path(second["folder"]), "Calculus").new("summary").read_text()
    # a live draft in progress is not part of the course files
    from stenny.vault import Vault
    vault = Vault(cfg.lectures_root)
    vault.write(LectureFiles(cfg.lectures_root / "Calculus" / "2026-10-29", "Calculus").new("transcript"),
                '---\nstatus: "live"\n---\n# draft\n')

    from stenny import course_files
    course_files.rebuild(vault, "Calculus")
    all_md = vault.course_file("Calculus", "all lectures").read_text()
    fm = parse_frontmatter(all_md)
    assert fm["kind"] == "course-file" and fm["lectures"] == 2 and fm["duration"] == "0:02:20"
    assert "- **Lecture 1** (2026-10-15, 0:01:10): Introduction" in all_md and "**Lecture 3**" not in all_md
    assert all_md.index("## Lecture 1 – 2026-10-15") < all_md.index("## Lecture 2 – 2026-10-22")
    assert "### [0:00:00] Introduction" in all_md and "\n# Calculus – Lecture" not in all_md  # one level deeper
    assert "transcript `Calculus 2026-10-15.md`" in all_md and all_md.count("\ncourse:") == 1     # one frontmatter
    assert "[[" not in all_md and "[[" not in vault.course_file("Calculus", "summaries").read_text()  # only the overview links
    assert "**[0:00:00]** Phrase" in all_md
    summaries = vault.course_file("Calculus", "summaries").read_text()
    assert summaries.count("### Overview") == 2 and "## Lecture 2 – 2026-10-22" in summaries
    assert vault.violations() == []


def test_older_lectures_keep_their_file_names(tmp_path):
    from stenny.vault import LectureFiles
    folder = tmp_path / "Calculus" / "2026-10-15"
    folder.mkdir(parents=True)
    (folder / "Transcript.md").write_text("old")
    (folder / "Slides.pdf").write_bytes(b"%PDF")
    files = LectureFiles(folder, "Calculus")
    assert files.target("transcript").name == "Transcript.md" and files.target("summary").name == "Summary.md"
    assert files.slides().name == "Slides.pdf"
    new = LectureFiles(tmp_path / "Calculus" / "2026-10-22 (2)", "Calculus")
    assert new.target("raw").name == "Calculus 2026-10-22 (2) (raw).md"


def test_transcripts_say_they_are_audio_only(setup):
    cfg, wav = setup
    cfg.keep_audio = False
    info = pipeline.process(wav, cfg, Course("Calculus", language="fr", languages=["fr"]), FakeEngine({"fr": 1.0}))
    from stenny.render import AUDIO_ONLY
    from stenny.vault import Vault
    transcript = lf(info["folder"]).read_text()
    assert f"> {AUDIO_ONLY}" in transcript
    all_md = Vault(cfg.lectures_root).course_file("Calculus", "all lectures").read_text()
    assert all_md.count("**Audio only:**") == 1                     # once at the top, not per lecture


class TopicLLM(FakeLLM):
    """Also proposes topics and glossary terms, some without evidence in the text."""

    def complete(self, system, prompt):
        if "JSON" in system:
            self.prompts = getattr(self, "prompts", []) + [prompt]
            return json.dumps({"sections": [{"at": 0, "title": "Introduction"}],
                               "term_fixes": [{"p": 0, "from": "Rimane", "to": "Riemann"}],
                               "topics": ["Riemann", "suites", "Théorème inventé", "Calculus", "a/b: [x]"],
                               "terms": ["Riemann", "Phrase", "suite", "Lebesgue"]}), Usage(100, 20)
        return super().complete(system, prompt)


def test_topics_glossary_and_links_between_lectures(setup, monkeypatch):
    from datetime import datetime

    from stenny.glossary import Glossary
    from stenny.render import parse_frontmatter
    from stenny.vault import Vault
    cfg, wav = setup
    cfg.keep_audio = False
    cfg.courses = {"Calculus": Course("Calculus", language="fr", languages=["fr"])}
    llm = TopicLLM()
    monkeypatch.setattr(pipeline, "get_backend", lambda cfg: llm)
    course = cfg.courses["Calculus"]
    gloss = cfg.lectures_root / "_Glossaries" / "Calculus (glossary).md"
    gloss.parent.mkdir(parents=True)
    gloss.write_text("## Terms\n- Riemann\n\n## Corrections\n- Rimann → Riemann\n", encoding="utf-8")

    first = Path(pipeline.process(wav, cfg, course, FakeEngine({"fr": 1.0}), when=datetime(2026, 10, 15, 9))["folder"])
    fm = parse_frontmatter(lf(first).read_text())
    # topics need evidence in the text; the course name and invented ones are dropped
    assert fm["topics"] == ["[[_Topics/Riemann|Riemann]]", "[[_Topics/suites|suites]]"]  # most mentioned first
    topic = (cfg.lectures_root / "_Topics" / "Riemann.md").read_text()
    assert "[[Calculus 2026-10-15|Lecture 1 (2026-10-15)]]" in topic and "## Calculus" in topic
    assert not (cfg.lectures_root / "_Topics" / "Théorème inventé.md").exists()

    # glossary: only "Phrase" has evidence (said in every chunk) and isn't known or a common word
    g = Glossary.load(gloss)
    assert g.auto_terms == ["Phrase"] and g.terms == ["Riemann", "Phrase"]
    text = gloss.read_text()
    assert text.index("## Terms (added automatically)") < text.index("## Corrections")
    assert "- Rimann → Riemann" in text and text.startswith("## Terms\n- Riemann\n")  # the rest is untouched

    second = Path(pipeline.process(wav, cfg, course, FakeEngine({"fr": 1.0}), when=datetime(2026, 10, 22, 9))["folder"])
    assert "Existing topics: Riemann; suites" in llm.prompts[-1]  # names are reused across lectures
    fm1, fm2 = parse_frontmatter(lf(first).read_text()), parse_frontmatter(lf(second).read_text())
    assert fm1["next"] == "[[Calculus 2026-10-22]]" and "previous" not in fm1
    assert fm2["previous"] == "[[Calculus 2026-10-15]]" and "next" not in fm2
    assert "Lecture 2 (2026-10-22)" in (cfg.lectures_root / "_Topics" / "Riemann.md").read_text()
    hub = (cfg.lectures_root / "_Course Files" / "Calculus (overview).md").read_text()
    assert "[[Calculus 2026-10-15|transcript]] · [[Calculus 2026-10-15 (summary)|summary]]" in hub
    assert "[[_Glossaries/Calculus (glossary)|Calculus (glossary)]]" in hub and "Topics: Riemann, suites" in hub
    assert "[[Calculus (all lectures)]]" in hub
    assert Glossary.load(gloss).auto_terms == ["Phrase"]  # not added twice
    assert Vault(cfg.lectures_root).violations() == []

    # a refresh without the AI keeps the topics it had
    pipeline.refresh(cfg, course, "2026-10-15", use_llm=False)
    assert parse_frontmatter(lf(first).read_text())["topics"] == fm["topics"]


def test_talk_goes_to_its_own_folder_without_course_files(setup):
    """A talk or event (`--event`): its own vault folder next to Lectures, no glossary, no course files,
    no lecture number, no topic links; audio and raw output under the data folder's _Recordings/."""
    cfg, wav = setup
    talk = cfg.event("Guest talk: Müller")
    assert talk.name == "Guest talk – Müller" and talk.is_event and talk.languages == ["en", "de", "fr"]
    from datetime import datetime
    info = pipeline.process(wav, cfg, talk, FakeEngine({"en": 1.0}), when=datetime(2026, 10, 22, 18, 0))

    folder = Path(info["folder"])
    assert folder == cfg.vault / "Areas" / "Recordings" / "Guest talk – Müller" / "2026-10-22"
    transcript = LectureFiles(folder, talk.name).new("transcript").read_text()
    assert 'event: "Guest talk – Müller"' in transcript and "course:" not in transcript
    assert "# Guest talk – Müller – 2026-10-22" in transcript and "Lecture 1" not in transcript
    assert "of a talk recorded on 2026-10-22" in transcript and "topics:" not in transcript
    assert not (cfg.vault / "Areas" / "Recordings" / "_Glossaries").exists()
    assert not (cfg.vault / "Areas" / "Recordings" / "_Course Files").exists()
    assert not cfg.lectures_root.exists()                              # nothing written next to the lectures
    data = cfg.data_dir / "_Recordings" / "Guest talk – Müller"
    assert (data / "2026-10-22.opus").exists() and (data / "Guest talk – Müller 2026-10-22 (raw).md").exists()


def test_talk_checks_each_long_chunk_for_its_language(tmp_path):
    from stenny.pipeline import transcribe

    class Engine:
        """English for the first minute, German after it; records the language each chunk was given."""
        def __init__(self):
            self.used, self.lengths = [], []

        def language_probs(self, clip, allowed):
            first = float(clip[0]) < 0.15
            return {lang: (0.9 if (lang == "en") == first and lang in ("en", "de") else 0.05) for lang in allowed}

        def transcribe(self, audio, language, prompt=None, fast=False):
            self.used.append(language)
            self.lengths.append(len(audio) / SAMPLE_RATE)
            return [{"start": 0.0, "end": 1.0, "text": " x", "avg_logprob": -0.2, "no_speech_prob": 0.0}]

    audio = np.concatenate([np.full(60 * SAMPLE_RATE, 0.1, np.float32), np.full(60 * SAMPLE_RATE, 0.2, np.float32)])
    cfg = Config(vault=tmp_path, lectures_dir="L", data_dir=tmp_path)
    engine = Engine()
    transcribe(audio, cfg.event("Guest talk"), engine, None)
    assert "en" in engine.used and "de" in engine.used and engine.used[0] == "en" and engine.used[-1] == "de"
    assert engine.lengths.count(18.0) >= 5  # the long chunks: short ones cost sentences on a talk
