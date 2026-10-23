"""Slides: text extraction, Whisper prompt terms, slide-based term fixes and slide references."""

import pytest
from pdfgen import make_pdf

from stenny import glossary as glossary_mod
from stenny import slides as slides_mod
from stenny.audio import device_from_tags
from stenny.glossary import Glossary
from stenny.llm import guard, tasks
from stenny.render import Lecture, parse_frontmatter, transcript_md
from stenny.slides import Slides

PAGES = [["Intégrale de Riemann", "Le critère de Cauchy et le théorème de Bolzano-Weierstrass"],
         ["Exemple: la fonction de Dirichlet", "Lemme de Heine-Borel (cas compact)"],
         ["Séries de Fourier", "Convergence au sens de Dirichlet"]]


@pytest.fixture
def pdf(tmp_path):
    return make_pdf(tmp_path / "deck.pdf", PAGES)


def test_extract_pages_with_pypdf(pdf, monkeypatch):
    monkeypatch.setattr(slides_mod.shutil, "which", lambda _: None)  # no poppler: pypdf path
    s = Slides.load(pdf)
    assert s.count == 3 and s.has_text
    assert s.pages[0] == "Intégrale de Riemann\nLe critère de Cauchy et le théorème de Bolzano-Weierstrass"
    assert s.contains("Heine-Borel") and s.contains("théorème de Bolzano-Weierstrass")
    assert not s.contains("Riemannienne") and not s.contains("riemann") and s.contains("riemann", ignore_case=True)


@pytest.mark.skipif(not slides_mod.shutil.which("pdftotext"), reason="poppler not installed")
def test_extract_pages_with_pdftotext(pdf):
    assert Slides.load(pdf).pages[2] == "Séries de Fourier\nConvergence au sens de Dirichlet"


def test_clean_joins_hyphenated_line_breaks_and_ligatures():
    assert slides_mod.clean("la conver-\ngence de Bolzano-\nWeierstrass, ﬁni") == \
        "la convergence de Bolzano-Weierstrass, fini"


def test_unreadable_or_empty_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(slides_mod.shutil, "which", lambda _: None)
    (tmp_path / "Slides.pdf").write_bytes(b"not a pdf")
    messages = []
    assert slides_mod.load_from(tmp_path / "Slides.pdf", messages.append) is None and "Slides skipped" in messages[0]
    make_pdf(tmp_path / "Slides.pdf", [[], []])  # scanned deck: pages without text
    assert slides_mod.load_from(tmp_path / "Slides.pdf", messages.append) is None and "no text layer" in messages[1]
    assert slides_mod.load_from(tmp_path / "nothing-here") is None


def test_terms_are_names_and_compounds_most_widespread_first():
    s = Slides(["\n".join(p) for p in PAGES])
    # line-initial capitals ("Intégrale", "Exemple", "Séries") are ordinary words, not terms
    assert s.terms() == ["Dirichlet", "Riemann", "Cauchy", "Bolzano-Weierstrass", "Heine-Borel", "Fourier"]
    assert s.terms(skip=["Riemann", "suite de Cauchy"]) == ["Dirichlet", "Bolzano-Weierstrass", "Heine-Borel", "Fourier"]


def test_terms_skip_header_and_footer_on_every_page():
    pages = [f"Université de Genève, Monsieur Martin\nexercice {i}" for i in range(6)]
    pages[0] += ": le théorème de Tychonoff"
    assert Slides(pages).terms() == ["Tychonoff"]


def test_prompt_puts_glossary_first_and_respects_whisper_token_limit():
    g = Glossary(terms=["Bolzano-Weierstrass", "Riemann"])
    assert g.prompt(["riemann", "Dirichlet"]) == "Bolzano-Weierstrass, Riemann, Dirichlet."
    many = [f"Mathématicien{i}-Lebesgue" for i in range(200)]
    prompt = g.prompt(many)
    assert prompt.startswith("Bolzano-Weierstrass, Riemann, Mathématicien0-Lebesgue")
    assert glossary_mod.prompt_fits(prompt) and not glossary_mod.prompt_fits(prompt + " " + many[-1] * 5)
    assert len(glossary_mod._tokenizer().encode(" " + prompt)) <= glossary_mod.PROMPT_MAX_TOKENS < 223


# --- guard ---------------------------------------------------------------------

SLIDES = Slides(["Lemme de Heine-Borel, fermeture", "La fonction de Dirichlet est partout discontinue. Elle converge."])
GLOSSARY = Glossary(terms=["Riemann"])
PARAS = [{"start": 0, "text": "Le lemme de Heine Borel dit que tout compact est fermé.", "unsure": []},
         {"start": 60, "text": "Prenons la fonction de Dirichelet. Elle diverge.",
          "unsure": ["Prenons la fonction de Dirichelet."]}]


def test_guard_accepts_fix_toward_slide_text():
    fixes = [{"p": 0, "from": "Heine Borel", "to": "Heine-Borel"}, {"p": 1, "from": "Dirichelet", "to": "Dirichlet"}]
    out, applied, rejected = guard.apply_fixes(PARAS, fixes, GLOSSARY, SLIDES)
    assert not rejected and [f["basis"] for f in applied] == ["slides", "slides"]
    assert out[0]["text"].startswith("Le lemme de Heine-Borel") and "fonction de Dirichlet." in out[1]["text"]
    assert guard.verify(PARAS, out, [(f["from"], f["to"]) for f in applied]) == []


@pytest.mark.parametrize("fix, reason", [
    # the replacement must be in the slides (verbatim, same case)
    ({"p": 1, "from": "Dirichelet", "to": "Dirichlet-Jordan"}, "not a glossary term or in the slides"),
    ({"p": 0, "from": "Heine Borel", "to": "heine-borel"}, "not a glossary term or in the slides"),
    ({"p": 1, "from": "Prenons", "to": "Dirichlet"}, "not a near spelling of the original"),
    # Whisper was confident about these words, and the spellings are only somewhat alike
    ({"p": 1, "from": "diverge", "to": "converge"}, "Whisper was confident here and the spellings differ too much"),
    ({"p": 0, "from": "fermé", "to": "fermeture"}, "Whisper was confident here and the spellings differ too much"),
])
def test_guard_rejects_slide_fixes(fix, reason):
    _, applied, rejected = guard.apply_fixes(PARAS, [fix], GLOSSARY, SLIDES)
    assert not applied and rejected[0]["reason"] == reason


def test_guard_allows_looser_slide_fix_where_whisper_was_unsure():
    paras = [{**PARAS[0], "unsure": ["Le lemme de Heine Borel dit que tout compact est fermé."]}]
    _, applied, _ = guard.apply_fixes(paras, [{"p": 0, "from": "fermé", "to": "fermeture"}], GLOSSARY, SLIDES)
    assert applied == [{"p": 0, "from": "fermé", "to": "fermeture", "basis": "slides"}]


def test_guard_rejects_original_that_also_appears_in_slides():
    paras = [{"start": 0, "text": "Elle converge vers zéro.", "unsure": ["Elle converge vers zéro."]}]
    slides = Slides(["Elle converge. Elle diverge."])
    _, applied, rejected = guard.apply_fixes(paras, [{"p": 0, "from": "converge", "to": "diverge"}], GLOSSARY, slides)
    assert not applied and rejected[0]["reason"] == "original words also appear in the slides"


def test_guard_without_slides_still_needs_glossary_term():
    _, applied, rejected = guard.apply_fixes(PARAS, [{"p": 1, "from": "Dirichelet", "to": "Dirichlet"}], GLOSSARY)
    assert not applied and rejected[0]["reason"] == "not a glossary term"


def test_slide_ranges_are_checked_against_page_count():
    proposed = [{"at": 0, "title": "Heine-Borel", "slides": [1, 1]}, {"at": 1, "title": "Dirichlet", "slides": "2–3"},
                {"at": 2, "title": "Fourier", "slides": [3, 9]}, {"at": 3, "title": "Fin", "slides": [2, 1]}]
    out = guard.validate_sections(proposed, n_paragraphs=4, n_pages=3)
    assert out == [{"at": 0, "title": "Heine-Borel", "slides": (1, 1)}, {"at": 1, "title": "Dirichlet", "slides": (2, 3)},
                   {"at": 2, "title": "Fourier"}, {"at": 3, "title": "Fin"}]
    assert guard.validate_sections(proposed, n_paragraphs=4) == [{k: v for k, v in s.items() if k != "slides"}
                                                                for s in proposed]


def test_prompt_marks_unsure_paragraphs_and_includes_slides():
    seen = []

    class LLM:
        def complete(self, system, prompt):
            seen.append((system, prompt))
            return '{"sections": [], "term_fixes": []}', tasks.Usage()

    tasks.propose_edits(PARAS, GLOSSARY, ["fr"], LLM(), SLIDES)
    system, prompt = seen[0]
    assert '"slides": [<first page>, <last page>]' in system
    assert "[Slide 2] La fonction de Dirichlet" in prompt and "Slides (2 pages)" in prompt
    assert "P1 [0:01:00] (unsure) Prenons" in prompt and "P0 [0:00:00] Le lemme" in prompt
    tasks.propose_edits(PARAS, GLOSSARY, ["fr"], LLM())
    assert "Slide" not in seen[1][1] and '"slides"' not in seen[1][0]


def test_heading_links_to_slide_pages_and_frontmatter():
    lec = Lecture(course="Calculus", date="2026-10-15", start_time="09:15", duration=120.0, languages=["fr"],
                  language_note="fr", model="whisper", source="a.m4a", microphone="Alex’s iPhone Microphone",
                  slides=3, structure="ai (Fake)")
    md = transcript_md(lec, PARAS, [{"at": 0, "title": "Heine-Borel", "slides": (1, 1)},
                                    {"at": 1, "title": "Dirichlet", "slides": (2, 3)}])
    assert "## [0:00:00] Heine-Borel ([[Slides.pdf#page=1|Slide 1]])" in md
    assert "## [0:01:00] Dirichlet ([[Slides.pdf#page=2|Slides 2–3]])" in md
    fm = parse_frontmatter(md)
    assert fm["microphone"] == "Alex’s iPhone Microphone" and fm["slides"] == "[[Slides.pdf]]"
    assert fm["date"] == "2026-10-15" and fm["languages"] == ["fr"]


def test_recording_device_from_tags():
    assert device_from_tags({"encoder": "com.apple.VoiceMemos (iPhone Version 18.1 (Build 22B83))"}) == \
        "Voice Memos (iPhone)"
    assert device_from_tags({"encoder": "com.apple.VoiceMemos (iPhone Version 18.1 (Build 22B83))",
                             "com.apple.quicktime.make": "Apple",
                             "com.apple.quicktime.model": "iPhone 15 Pro"}) == "iPhone 15 Pro (Voice Memos)"
    assert device_from_tags({"encoder": "Lavf61.7.100", "major_brand": "M4A "}) is None
    assert device_from_tags({"com.apple.quicktime.model": "iPhone 15", "com.apple.quicktime.software": "18.1"}) == \
        "iPhone 15"
    assert device_from_tags({"encoder": "Zoom H1n"}) == "Zoom H1n"
