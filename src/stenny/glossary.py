"""Per-course glossary, stored as an editable Markdown note in the vault:

    ## Terms
    - Bolzano-Weierstrass
    - suite de Cauchy

    ## Terms (added automatically)
    - théorème des valeurs intermédiaires

    ## Corrections
    - Bolzano Weierstrass → Bolzano-Weierstrass

Terms bias Whisper (as its prompt) and tell the LLM which spellings are correct.
Corrections are applied deterministically to the clean transcript (never to the raw one).
"Terms (added automatically)" is written by the tool after each lecture (see `with_auto_terms`); the other
sections are only ever edited by people (or a Claude project), never by the tool.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ARROW = re.compile(r"\s*(?:→|->|=>)\s*")
AUTO_HEADING = "## Terms (added automatically)"
AUTO_NOTE = ("Added after each lecture from its transcript and slides: only terms that are on the slides or were "
             "transcribed the same way at least twice. Most used in recent lectures first. Delete a wrong one "
             "(it comes back only if it keeps appearing), or move it up to Terms to keep it first.")
MAX_AUTO_TERMS = 150
AUTO_IN_PROMPT = 15  # automatic terms before the slide terms in Whisper's prompt
# Whisper keeps only the last 223 prompt tokens, so an overlong prompt would lose the glossary terms
# at its start. Stay below that, with some margin.
PROMPT_MAX_TOKENS = 200
PROMPT_MAX_CHARS = 600  # fallback estimate when Whisper's tokenizer isn't available


@lru_cache(maxsize=1)
def _tokenizer():
    try:
        from mlx_whisper.tokenizer import get_tokenizer
        return get_tokenizer(multilingual=True, num_languages=100)  # large-v3(-turbo); ships with mlx-whisper
    except Exception:
        return None


def prompt_fits(text: str) -> bool:
    tok = _tokenizer()
    if tok is None:
        return len(text) <= PROMPT_MAX_CHARS
    return len(tok.encode(" " + text)) <= PROMPT_MAX_TOKENS

TEMPLATE = """---
course: "{course}"
---
# Glossary: {course}

Terms help Whisper spell names and jargon correctly, and the cleanup step uses them to fix misheard terms.
Corrections (`wrong → right`) are applied word-for-word to the clean transcript (never to the raw output).
The course's Claude project keeps this note up to date (see `stenny project-instructions`).

## Terms

## Corrections
"""


@dataclass
class Glossary:
    terms: list[str] = field(default_factory=list)       # all terms: the hand-made ones first, then the automatic ones
    corrections: list[tuple[str, str]] = field(default_factory=list)
    auto_terms: list[str] = field(default_factory=list)  # the automatic ones alone

    @classmethod
    def parse(cls, text: str) -> "Glossary":
        g, section, manual = cls(), None, []
        for line in text.splitlines():
            if line.startswith("#"):
                title = line.lstrip("#").strip().casefold()
                section = ("auto" if title.startswith("terms (added") else "terms" if title.startswith("term")
                           else "corrections" if title.startswith("correction") else None)
                continue
            item = line.strip()
            if not item.startswith(("- ", "* ")) or section is None:
                continue
            item = item[2:].strip()
            if not item:
                continue
            if section == "terms":
                manual.append(item)
            elif section == "auto":
                g.auto_terms.append(item)
            elif len(parts := ARROW.split(item, maxsplit=1)) == 2 and parts[0] and parts[1]:
                g.corrections.append((parts[0], parts[1]))
        known = {t.casefold() for t in manual}
        g.auto_terms = [t for t in dict.fromkeys(g.auto_terms) if t.casefold() not in known]
        g.terms = manual + g.auto_terms
        return g

    @classmethod
    def load(cls, path: Path) -> "Glossary":
        return cls.parse(path.read_text(encoding="utf-8")) if path.exists() else cls()

    def prompt(self, extra: list[str] = ()) -> str | None:
        """Comma-separated terms for Whisper's initial prompt, cut to fit its window: the hand-made glossary
        terms first, then the most used automatic ones, then `extra` (terms from this lecture's slides), then
        the other automatic ones, as long as there is room."""
        manual = [t for t in self.terms if t not in self.auto_terms]
        ordered = [*manual, *self.auto_terms[:AUTO_IN_PROMPT]]
        rest = [*extra, *self.auto_terms[AUTO_IN_PROMPT:]]
        out, seen = "", set()
        for i, term in enumerate([*ordered, *rest]):
            if term.casefold() in seen:
                continue
            nxt = f"{out}, {term}" if out else term
            if not prompt_fits(f"{nxt}."):
                if i < len(ordered):
                    break  # glossary terms keep their order; don't skip ahead
                continue
            out = nxt
            seen.add(term.casefold())
        return f"{out}." if out else None

    def apply_corrections(self, text: str) -> tuple[str, int]:
        count = 0
        for wrong, right in self.corrections:
            pattern = re.compile(rf"(?<!\w){re.escape(wrong)}(?!\w)")
            text, n = pattern.subn(right, text)
            count += n
        return text, count

    def knows(self, word: str) -> bool:
        w = word.casefold()
        return any(w == t.casefold() or w in t.casefold().split() for t in self.terms)


def with_auto_terms(text: str, terms: list[str]) -> str:
    """The glossary note with its automatic section replaced by `terms` (inserted before Corrections, or at
    the end). Everything else in the note stays exactly as it was."""
    lines = text.rstrip("\n").split("\n")
    start = next((i for i, l in enumerate(lines) if l.strip().casefold() == AUTO_HEADING.casefold()), None)
    if start is not None:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("#")), len(lines))
        del lines[start:end]
        while start < len(lines) and start > 0 and not lines[start - 1].strip() and not lines[start].strip():
            del lines[start]  # don't leave a double blank line behind
    section = [AUTO_HEADING, AUTO_NOTE, *[f"- {t}" for t in terms], ""] if terms else []
    at = start if start is not None else next(
        (i for i, l in enumerate(lines) if l.lstrip("#").strip().casefold().startswith("correction") and l.startswith("#")),
        len(lines))
    if at == len(lines) and section and lines and lines[-1].strip():
        section = ["", *section]
    lines[at:at] = section
    return "\n".join(lines).rstrip("\n") + "\n"


def occurrences(term: str, text: str) -> int:
    """How often `term` occurs in `text` as whole words, ignoring case."""
    return len(re.findall(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.I))


def rank_auto_terms(glossary: Glossary, new: list[str], recent: list[str]) -> list[str]:
    """The automatic terms after a lecture: the old ones plus `new`, without those a person already listed
    (or corrects away), sorted by in how many of the `recent` transcripts they occur (then: older first, so
    the order is stable). At most MAX_AUTO_TERMS: Whisper only ever sees the first few dozen anyway."""
    manual = {t.casefold() for t in glossary.terms if t not in glossary.auto_terms}
    wrong = {w.casefold() for w, _ in glossary.corrections}
    out, seen = [], set()
    for t in [*glossary.auto_terms, *new]:
        key = t.casefold()
        if key in seen or key in manual or key in wrong:
            continue
        seen.add(key)
        out.append(t)
    score = {t: sum(1 for text in recent if occurrences(t, text)) for t in out}
    order = {t: i for i, t in enumerate(out)}
    return sorted(out, key=lambda t: (-score[t], order[t]))[:MAX_AUTO_TERMS]
