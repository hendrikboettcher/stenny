import numpy as np
import pytest

from stenny.glossary import Glossary
from stenny.llm import guard, tasks
from stenny.render import Lecture, frontmatter, raw_md, time_sections, transcript_md
from stenny.textproc import SAMPLE_RATE, mark_dropped, retry_sparse, split_at_pauses, to_paragraphs
from stenny.vault import Vault, VaultError

GLOSSARY_MD = """# Glossary
## Terms
- Bolzano-Weierstrass
- suite de Cauchy
- Riemann

## Corrections
- Bolzano Weierstrass → Bolzano-Weierstrass
- rieman -> Riemann
"""


def seg(start, text, **kw):
    return {"start": start, "end": start + 2, "text": text, **kw}


# --- textproc -----------------------------------------------------------------

def test_split_at_pauses_cuts_in_silence_and_respects_max_length():
    rng = np.random.default_rng(0)
    speech = lambda s: rng.normal(0, 0.1, int(s * SAMPLE_RATE)).astype(np.float32)
    silence = lambda s: np.zeros(int(s * SAMPLE_RATE), np.float32)
    audio = np.concatenate([speech(22), silence(1), speech(25), silence(1), speech(10)])
    chunks = split_at_pauses(audio)
    assert all((b - a) / SAMPLE_RATE <= 28.01 for a, b in chunks)
    assert chunks[0][0] == 0 and chunks[-1][1] == len(audio)
    first_cut = chunks[0][1] / SAMPLE_RATE
    assert 22 <= first_cut <= 23  # inside the first pause


def test_mark_dropped_keeps_everything_but_flags_junk():
    segs = [seg(0, "Bonjour."), seg(2, "D'accord."), seg(4, "D'accord."), seg(6, "D'accord."),
            seg(8, "bla", no_speech_prob=0.9, avg_logprob=-1.5), seg(10, "Bolzano-Weierstrass, suite de Cauchy, Riemann.")]
    out = mark_dropped(segs, prompt="Bolzano-Weierstrass, suite de Cauchy, Riemann.")
    assert len(out) == len(segs)
    assert [s["dropped"] for s in out] == [None, None, None, "repetition", "no speech", "prompt echo"]


def test_retry_sparse_replaces_dots_with_real_text_and_keeps_original_in_raw():
    audio = np.random.default_rng(2).normal(0, 0.1, 30 * SAMPLE_RATE).astype(np.float32)
    segs = [{"start": 0.0, "end": 27.0, "text": "...", "no_speech_prob": 0.0, "avg_logprob": -1.0},
            {"start": 27.0, "end": 29.0, "text": "D'accord.", "no_speech_prob": 0.0, "avg_logprob": -0.3}]
    calls = []

    def fake(clip, start):
        calls.append(start)
        return [{"start": 0.0, "end": len(clip) / SAMPLE_RATE, "text": "Bonjour à tous, aujourd'hui les suites."}]

    out = retry_sparse(segs, audio, fake)
    assert len(calls) >= 3 and all(c < 27 for c in calls)        # only the sparse stretch, in short pieces
    assert out[0]["dropped"] == "almost empty, re-transcribed"
    kept = [s for s in mark_dropped(out) if not s["dropped"]]
    assert kept[-1]["text"] == "D'accord." and all(s.get("retry") for s in kept[:-1])
    assert retry_sparse([segs[1]], audio, fake) == [segs[1]]      # normal segments untouched


def test_paragraphs_break_at_recording_pauses_and_long_silence():
    segs = [seg(0, "Un."), seg(3, "Deux."), seg(10, "Trois."), seg(60, "Quatre.")]
    paras = to_paragraphs(segs, breaks=[6.0])
    assert [p["text"] for p in paras] == ["Un. Deux.", "Trois.", "Quatre."]


def test_paragraphs_skip_dropped_segments():
    segs = mark_dropped([seg(0, "Un."), seg(2, "Un."), seg(4, "Un."), seg(6, "Deux.")])
    paras = to_paragraphs(segs)
    assert paras == [{"start": 0, "text": "Un. Un. Deux.", "unsure": []}]


# --- glossary -----------------------------------------------------------------

def test_glossary_parse_prompt_and_corrections():
    g = Glossary.parse(GLOSSARY_MD)
    assert g.terms == ["Bolzano-Weierstrass", "suite de Cauchy", "Riemann"]
    assert g.corrections == [("Bolzano Weierstrass", "Bolzano-Weierstrass"), ("rieman", "Riemann")]
    assert g.prompt() == "Bolzano-Weierstrass, suite de Cauchy, Riemann."
    text, n = g.apply_corrections("le théorème de Bolzano Weierstrass et l'intégrale de rieman, riemannienne")
    assert text == "le théorème de Bolzano-Weierstrass et l'intégrale de Riemann, riemannienne"
    assert n == 2


# --- guard --------------------------------------------------------------------

PARAS = [{"start": 0, "text": "Le théorème de Bolsano Weierstrass dit que toute suite bornée converge."},
         {"start": 600, "text": "Passons à l'intégrale de Rimane."}]


def test_guard_applies_near_spelling_and_glossary_fixes():
    g = Glossary.parse(GLOSSARY_MD)
    fixes = [{"p": 0, "from": "Bolsano Weierstrass", "to": "Bolzano-Weierstrass"},
             {"p": 1, "from": "Rimane", "to": "Riemann"}]
    out, applied, rejected = guard.apply_fixes(PARAS, fixes, g)
    assert len(applied) == 2 and not rejected
    assert out[0]["text"].startswith("Le théorème de Bolzano-Weierstrass")
    assert out[1]["text"] == "Passons à l'intégrale de Riemann."
    assert guard.verify(PARAS, out, [(f["from"], f["to"]) for f in applied]) == []


@pytest.mark.parametrize("fix, reason", [
    ({"p": 0, "from": "toute suite bornée converge", "to": "toute suite bornée admet une sous-suite convergente"},
     "too large"),
    ({"p": 0, "from": "converge", "to": "diverge"}, "not a glossary term"),
    ({"p": 0, "from": "suite", "to": "Riemann"}, "not a near spelling of the original"),
    ({"p": 1, "from": "Cauchy", "to": "Cauchy-Schwarz"}, "'from' not found in paragraph"),
    ({"p": 0, "from": "théorème", "to": "théorème"}, "empty or no-op"),
])
def test_guard_rejects_content_changes(fix, reason):
    _, applied, rejected = guard.apply_fixes(PARAS, [fix], Glossary.parse(GLOSSARY_MD))
    assert not applied and rejected[0]["reason"] == reason


def test_verify_detects_invented_words():
    tampered = [dict(PARAS[0]), {"start": 600, "text": "Passons maintenant à l'intégrale de Rimane, très importante."}]
    assert guard.verify(PARAS, tampered, []) != []


def test_validate_sections():
    assert guard.validate_sections([{"at": "P1", "title": "Deux"}], 2)[1] == {"at": 1, "title": "Deux"}
    out = guard.validate_sections([{"at": 1, "title": "## Intégrale"}, {"at": 1, "title": "dup"},
                                   {"at": 9, "title": "out of range"}, "junk"], n_paragraphs=2)
    assert out == [{"at": 0, "title": None}, {"at": 1, "title": "Intégrale"}]


def test_parse_json_and_chunks():
    assert tasks.parse_json('Sure! {"sections": [], "term_fixes": []} done') == {"sections": [], "term_fixes": []}
    paras = [{"start": i, "text": "x" * 100} for i in range(10)]
    parts = tasks.chunks(paras, limit=350)
    assert [o for o, _ in parts] == [0, 3, 6, 9] and sum(len(p) for _, p in parts) == 10


# --- render -------------------------------------------------------------------

LEC = Lecture(course="Test Calculus", date="2026-10-15", start_time="09:15", duration=1325.0, languages=["fr"],
              language_note="fr (fixed)", model="whisper", source="a.m4a", tags=["maths"])


def test_frontmatter_is_valid_yaml_like():
    fm = frontmatter({"date": "2026-10-15", "title": 'Un "test": oui', "tags": ["a", "b"], "n": 3, "x": None})
    assert fm == '---\ndate: 2026-10-15\ntitle: "Un \\"test\\": oui"\ntags: ["a", "b"]\nn: 3\n---\n'


def test_transcript_and_raw_markdown():
    paras = [{"start": 0, "text": "Bonjour."}, {"start": 650, "text": "Suite."}]
    md = transcript_md(LEC, paras, time_sections(paras))
    assert md.startswith("---\ncourse: \"Test Calculus\"\ndate: 2026-10-15\n")
    assert "## [0:00:00]\n\n**[0:00:00]** Bonjour." in md
    assert "## [0:10:00]" not in md and "## [0:10:50]\n\n**[0:10:50]** Suite." in md
    raw = raw_md(LEC, mark_dropped([seg(0, "Bonjour."), seg(3, "x", no_speech_prob=0.9, avg_logprob=-2)]))
    assert "[0:00:00–0:00:02] Bonjour." in raw and "[0:00:03–0:00:05] x ⚠ *no speech*" in raw


# --- vault --------------------------------------------------------------------

def test_vault_folder_rule_and_boundaries(tmp_path):
    root = tmp_path / "Areas" / "Lectures"
    (tmp_path / "Areas").mkdir()
    v = Vault(root)
    d = v.lecture_dir("Calculus", "2026-10-15")
    v.write(d / "Transcript.md", 'source: "a.m4a"\n')
    v.write(v.glossary_path("Calculus"), "# g\n")
    assert v.violations() == []
    # a file next to course folders would break the rule
    with pytest.raises(VaultError, match="Folder rule"):
        v.write(root / "notes.md", "x")
    # a folder inside a lecture folder (which holds files) would break it too
    with pytest.raises(VaultError, match="Folder rule"):
        v.ensure_dir(d / "sub")
    with pytest.raises(VaultError, match="outside"):
        v.write(tmp_path / "Areas" / "evil.md", "x")
    with pytest.raises(VaultError, match="outside"):
        v.write(root / ".." / "evil.md", "x")
    # same source reuses the folder; a different lecture that day gets "(2)"
    assert v.lecture_dir("Calculus", "2026-10-15", source="a.m4a") == d
    assert v.lecture_dir("Calculus", "2026-10-15", source="b.m4a").name == "2026-10-15 (2)"


def test_span_groups_ignore_small_resyncs_but_split_at_real_breaks():
    from stenny.pipeline import lecture_time, span_groups
    sr = SAMPLE_RATE
    # 0-8 s recorded, then a 1 s re-sync, then a 60 s pause after 16 s of lecture, then 1 s re-sync
    spans = [(0, 0.0), (8 * sr, 9.0), (15 * sr, 76.0), (20 * sr, 82.0)]
    assert span_groups(spans, 30 * sr) == [(0, 15 * sr), (15 * sr, 30 * sr)]
    assert lecture_time(spans, 4 * sr) == 4.0 and lecture_time(spans, 10 * sr) == 11.0
    assert lecture_time(spans, 25 * sr) == 87.0


def test_backup_text_drops_words_the_main_microphone_already_has():
    from stenny.textproc import trim_edge_overlap
    main = [{"start": 57.7, "end": 62.7, "text": " And I have 95 patients."},
            {"start": 62.7, "end": 64.1, "text": " This is nice."},
            {"start": 94.6, "end": 99.6, "text": " The classifier just assigns all observations to the zero group."}]
    backup = [{"start": 63.1, "end": 65.6, "text": " So this is nice, yeah."},
              {"start": 65.6, "end": 69.5, "text": " And now I have a learning method."},
              {"start": 86.0, "end": 95.6, "text": " observation to the larger class. So this classifier just"}]
    out = trim_edge_overlap(main, backup, 64.1, 94.6)
    assert [s["text"] for s in out] == [" Yeah.", " And now I have a learning method.",
                                         " observation to the larger class. So"]
    assert out[0]["raw_text"] == " So this is nice, yeah." and out[0]["overlap"] == "So this is nice"
    assert out[2]["overlap"] == "this classifier just" and "raw_text" not in out[1]
    # nothing repeated: unchanged; a segment that only repeats is dropped, not deleted
    assert trim_edge_overlap(main, backup[1:2], 64.1, 94.6) == backup[1:2]
    only = trim_edge_overlap(main, [{"start": 63.5, "end": 64.5, "text": " This is nice."}], 64.1, 94.6)
    assert only[0]["dropped"] == "repeats the main microphone" and only[0]["raw_text"] == " This is nice."


def test_lang_auto_or_mixed_switches_the_mode_instead_of_forcing_a_language():
    from argparse import Namespace
    from pathlib import Path
    from stenny import language
    from stenny.cli import recording_target
    from stenny.config import Config, Course
    cfg = Config(vault=Path("/v"), lectures_dir="L", data_dir=Path("/d"),
                 courses={"Calculus": Course("Calculus", language="fr", languages=["fr"])})
    def target(**kw):
        return recording_target(cfg, Namespace(**{"course": None, "event": None, "lang": None, **kw}))
    mixed = target(course="Calculus", lang="mixed")
    assert (mixed.language, mixed.languages) == ("mixed", ["en", "de", "fr"])  # one language: the defaults
    assert cfg.courses["Calculus"].language == "fr"                           # the configured course is unchanged
    assert target(course="Calculus", lang="de").language == "fr"              # a code stays an override
    talk = target(event="Guest talk", lang="mixed")
    assert (talk.language, talk.languages) == ("mixed", ["en", "de", "fr"])
    assert target(event="Guest talk", lang="de").languages == ["de"]
    adhoc = target(course="Unknown", lang="auto")
    assert (adhoc.language, adhoc.languages) == ("auto", ["en", "de", "fr"])
    assert language.fixed_override("mixed") is None and language.fixed_override("de") == "de"


def test_pause_shortcut_is_parsed_and_checked():
    import pytest
    from stenny.config import ConfigError, parse_shortcut
    assert parse_shortcut("cmd+shift+ü") == {"modifiers": ["cmd", "shift"], "key": "ü"}
    assert parse_shortcut("Shift + Command + U\u0308") == {"modifiers": ["cmd", "shift"], "key": "ü"}  # NFC
    assert parse_shortcut("alt+ctrl+space") == {"modifiers": ["ctrl", "opt"], "key": "space"}
    assert parse_shortcut("cmd+shift++") == {"modifiers": ["cmd", "shift"], "key": "+"}
    for bad in ["shift+p", "cmd+shift+pp", "hyper+p", "p"]:
        with pytest.raises(ConfigError):
            parse_shortcut(bad)


def test_calendar_titles_find_their_course():
    from stenny.config import Course, course_for_title
    cs = {n: Course(n, aliases=a, calendar=k) for n, a, k in [
        ("Microeconomics", ["mi"], []), ("Programming I", ["prog"], []),
        ("Principles and Methods of Software Engineering", ["pmse"], []),
        ("Business Communication 2 – French", ["bc"], ["BizCom"])]}
    expect = {
        "VL Microeconomics (H 1.01)": "Microeconomics",             # room numbers don't matter
        "Programming 1 Übung 3": "Programming I",
        "Programming II": None,                                     # another course
        "Programming II (Raum 1)": None,
        "Principles & Methods of Software Engineering": "Principles and Methods of Software Engineering",
        "PMSE Übung": "Principles and Methods of Software Engineering",   # alias, 3+ letters
        "Business Communication II: French": "Business Communication 2 – French",
        "BizCom Gruppe B": "Business Communication 2 – French",           # calendar word
        "Business Communication 3 French": None,
        "mi casa party": None,                                      # 2-letter aliases are ignored
        "Zahnarzt": None,
    }
    assert {t: (c.name if (c := course_for_title(cs, t)) else None) for t in expect} == expect


def test_trim_repeats_cuts_whisper_loops_only():
    from stenny.textproc import trim_repeats
    assert trim_repeats("And five of them are can. And five of them are can. And five") == "And five of them are can."
    assert trim_repeats("divide by 3, then we divide by x3 again, divide by x3 again, divide by x3") == \
        "divide by 3, then we divide by x3 again,"
    assert trim_repeats("Right, because we started at zero and then evidence art. Right, because we sta") == \
        "Right, because we started at zero and then evidence art."
    assert trim_repeats("very very good, so we have the mean and the variance") == \
        "very very good, so we have the mean and the variance"


def test_glossary_auto_section_ranking_and_prompt_order():
    from stenny.glossary import AUTO_HEADING, Glossary, rank_auto_terms, with_auto_terms
    note = "# Glossary\n\n## Terms\n- Riemann\n\n## Corrections\n- Rimane → Riemann\n"
    g = Glossary.parse(with_auto_terms(note, ["Cauchy", "riemann"]))
    assert g.terms == ["Riemann", "Cauchy"] and g.auto_terms == ["Cauchy"]  # manual wins over a duplicate
    # rewriting replaces the section, never the rest
    again = with_auto_terms(with_auto_terms(note, ["Cauchy"]), ["Lebesgue"])
    assert again.count(AUTO_HEADING) == 1 and "- Cauchy" not in again and "- Rimane → Riemann" in again
    assert with_auto_terms(with_auto_terms(note, ["Cauchy"]), []) == note
    # ranking: used in more recent lectures first; manual terms and corrected spellings never come in
    g = Glossary.parse(with_auto_terms(note, ["Cauchy", "Lebesgue"]))
    assert rank_auto_terms(g, ["Fubini", "Rimane", "riemann"], ["Fubini et Lebesgue", "Lebesgue"]) == \
        ["Lebesgue", "Fubini", "Cauchy"]
    # prompt: hand-made terms first, then automatic ones, slide terms after the first AUTO_IN_PROMPT
    assert Glossary.parse(with_auto_terms(note, ["Cauchy"])).prompt(["Slide"]) == "Riemann, Cauchy, Slide."


def test_guard_topics_and_terms_need_evidence():
    from stenny.llm import guard
    text = "Les suites de Cauchy convergent. Le critère de Cauchy, une suite de Cauchy. Naive Bayes classifier."
    assert guard.validate_topics(["suite de Cauchy", "Naive Bayes classifiers", "Théorème de Rolle", "Calculus",
                                  "intro: [a|b]", 3], text, existing=["Suite de Cauchy"], reserved=["Calculus"]) == \
        ["Suite de Cauchy", "Naive Bayes classifiers"]  # plural/singular match; existing spelling kept
    assert guard.clean_topic("a/b: [x] #y") == "a b x y"
    # terms: twice in the text (case-insensitive, text's spelling wins) or on the slides; no short common words
    assert guard.validate_terms(["critère de cauchy", "Cauchy", "Rolle", "suite", "naive bayes classifier"],
                                text, known=["Cauchy"]) == []
    assert guard.validate_terms(["suites de cauchy", "SUITE DE CAUCHY"], text + " suite de Cauchy") == ["suite de Cauchy"]

    class Slides:
        flat = "Théorème de Rolle"

        def contains(self, phrase, ignore_case=False):
            return phrase.casefold() in self.flat.casefold() if ignore_case else phrase in self.flat
    assert guard.validate_terms(["Théorème de Rolle"], text, Slides()) == ["Théorème de Rolle"]
    assert guard.validate_topics(["Théorème de Rolle"], text, Slides()) == ["Théorème de Rolle"]


def test_migrate_moves_everything_outside_the_vault_into_one_folder(tmp_path, monkeypatch):
    import json
    from stenny import migrate as mig
    from stenny import paths
    from stenny.vault import Vault
    home = tmp_path / "Lectures Data"
    old_cfg, old_state, old_audio = tmp_path / ".config/lecture", tmp_path / ".local/share/lecture", tmp_path / "Lectures Audio"
    for name, value in {"HOME": home, "SYSTEM_DIR": home / "_System", "QUEUE_DIR": home / "_Queue",
                        "CONFIG_PATH": home / "_System/config.toml", "LEGACY_CONFIG_DIR": old_cfg,
                        "LEGACY_STATE_DIR": old_state, "LEGACY_AUDIO_DIR": old_audio}.items():
        monkeypatch.setattr(paths, name, value)
    vault = tmp_path / "vault"
    (old_cfg).mkdir(parents=True)
    (old_cfg / "config.toml").write_text(f'vault = "{vault}"\nlectures_dir = "L"\naudio_dir = "{old_audio}"\n'
                                         '[courses."Calculus"]\nlanguage = "fr"\n')
    (old_audio / "Calculus").mkdir(parents=True)
    (old_audio / "Calculus" / "2026-10-15.opus").write_bytes(b"ogg")
    (old_audio / "Calculus" / "2026-10-15.segments.json").write_text(
        json.dumps({"meta": {"lecture": {"audio": f"{old_audio}/Calculus/2026-10-15.opus"}}, "segments": []}))
    (old_state / "queue").mkdir(parents=True)
    (old_state / "queue" / "job.json").write_text(json.dumps({"file": f"{old_audio}/Calculus/x.pcm"}))
    (old_state / "live.log").write_text("log")
    (old_state / "queue.lock").write_text("")
    lecture = vault / "L" / "Calculus" / "2026-10-15"
    lecture.mkdir(parents=True)
    (lecture / "Calculus 2026-10-15.md").write_text(
        f'---\naudio: "{old_audio}/Calculus/2026-10-15.opus"\nstatus: "final"\n---\n# T\n\n> Every segment, unfiltered, '
        "is in [[Calculus 2026-10-15 (raw)]].\n")
    (lecture / "Calculus 2026-10-15 (raw).md").write_text("raw")
    (vault / "L" / "_Glossaries").mkdir()
    (vault / "L" / "_Glossaries" / "Calculus.md").write_text("## Terms\n- Riemann\n")

    assert mig.migrate(dry_run=True, log=lambda m: None) > 0
    assert (old_audio / "Calculus" / "2026-10-15.opus").exists() and not home.exists()  # a dry run changes nothing

    mig.migrate(log=lambda m: None)
    assert not old_cfg.exists() and not old_state.exists() and not old_audio.exists()
    assert "audio_dir" not in (home / "_System/config.toml").read_text().split("#")[0]
    assert (home / "Calculus" / "2026-10-15.opus").read_bytes() == b"ogg"
    raw = home / "Calculus" / "Calculus 2026-10-15 (raw).md"
    assert raw.read_text() == "raw" and not (lecture / "Calculus 2026-10-15 (raw).md").exists()
    t = (lecture / "Calculus 2026-10-15.md").read_text()
    assert "(outside the vault)" in t and "[[Calculus 2026-10-15 (raw)]]" not in t and str(old_audio) not in t
    meta = json.loads((home / "Calculus" / "2026-10-15.segments.json").read_text())["meta"]["lecture"]
    assert meta["audio"].startswith(str(home)) and meta["raw_file"].endswith("(raw).md")
    assert json.loads((home / "_Queue" / "job.json").read_text())["file"].startswith(str(home))
    assert (home / "_System" / "live.log").exists() and not (home / "_System" / "queue.lock").exists()
    assert (vault / "L" / "_Glossaries" / "Calculus (glossary).md").exists()
    assert Vault(vault / "L").violations() == []
    assert all(not (p.is_file() and any(q.is_dir() for q in p.parent.iterdir())) for p in home.rglob("*"))  # folder rule
    assert mig.migrate(log=lambda m: None) == 0  # done once, nothing left to do


def test_whisper_subtitle_credits_are_dropped_but_real_speech_stays():
    from stenny.textproc import drop_reason
    seg = lambda text: {"text": text, "no_speech_prob": 0.1, "avg_logprob": -0.3}
    for credit in ["Untertitelung des ZDF für funk, 2017", "Untertitel im Auftrag des ZDF, 2020",
                   "Sous-titres réalisés par la communauté d'Amara.org", "Subtitles by the Amara.org community",
                   "Copyright WDR 2021"]:
        assert drop_reason(seg(credit), [], None) == "subtitle credit", credit
    for speech in ["Die Untertitel im Film waren schlecht, aber der Inhalt war gut.",
                   "Le ZDF est une chaîne publique allemande.", "Subtitles help when the audio is bad."]:
        assert drop_reason(seg(speech), [], None) is None, speech


def test_whisper_stock_phrases_are_dropped_or_cut():
    from stenny.textproc import drop_reason
    seg = lambda text, lang="fr": {"text": text, "no_speech_prob": 0.0, "avg_logprob": -0.6, "language": lang,
                                   "start": 0.0, "end": 1.0}
    for filler in ["Vielen Dank.", "Vielen Dank. Vielen Dank.", " Thank you.", "Merci.", "– Sous-titrage FR –",
                   "Thanks for watching!"]:
        assert drop_reason(seg(filler), [], None) == "stock phrase", filler
    for speech in ["Vielen Dank für die Frage, das ist wichtig.", "Thank you, that's a good point.",
                   "Merci pour vos réponses."]:
        assert drop_reason(seg(speech), [], None) is None, speech
    out = mark_dropped([seg("Vielen Dank. Le bonheur national."), seg("Thank you, that's it.", "en"),
                        seg("Vielen Dank, das war's.", "de")])
    assert [s["text"].strip() for s in out] == ["Le bonheur national.", "Thank you, that's it.",
                                                "Vielen Dank, das war's."]
    assert out[0]["raw_text"] == "Vielen Dank. Le bonheur national."  # the raw output keeps what Whisper wrote
    assert "raw_text" not in out[1] and "raw_text" not in out[2]


def test_nonsense_from_near_silence_is_dropped_but_misheard_speech_stays():
    from stenny.textproc import drop_reason

    assert drop_reason(seg(0, " Ladies keep krypton", avg_logprob=-7.4), [], None).startswith("nonsense")
    assert drop_reason(seg(0, " Do you want to try and change", avg_logprob=-2.56), [], None) is None
    assert drop_reason(seg(0, " You", avg_logprob=-0.97), [], None) == "stock phrase"
    assert drop_reason(seg(0, " You see the point?", avg_logprob=-0.2), [], None) is None
    assert not [s for s in mark_dropped([seg(0, " ayım", avg_logprob=-5.2)]) if not s["dropped"]]
