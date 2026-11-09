"""Per-course glossary: Stenny's list of correct spellings for a course, kept as a readable Markdown note in the
Stenny folder. Nobody has to maintain it; it fills itself from what costs the student nothing:

    ## Terms
    - Bolzano-Weierstrass

    ## Terms (added automatically)
    - théorème des valeurs intermédiaires

    ## Corrections
    - Bolzano Weierstrass → Bolzano-Weierstrass

    ## Corrections (added automatically)
    - Rimane → Riemann

Terms bias Whisper (as its prompt) and tell the AI step which spellings are correct. Corrections are applied word
for word to the clean transcript (never to the raw one), the student's own first.

Who writes what:
- "Terms" and "Corrections" belong to the student: written by hand, or when they answer "Always write it like
  this?" after fixing a word in Stenny (`add_correction`). Stenny only ever adds to them, never changes or removes.
- "(added automatically)": Claude through the connector (`with_auto_terms`, `with_auto_corrections`); the
  automatic terms are re-ranked by use after each lecture (`rank_auto_terms`). The student's entries always win:
  an automatic one that contradicts them is dropped.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ARROW = re.compile(r"\s*(?:→|->|=>)\s*")
AUTO_HEADING = "## Terms (added automatically)"
AUTO_NOTE = ("Added by Stenny, for example when Claude suggests a term through Stenny's connector. Most used in recent "
             "lectures first. Delete a wrong one, or move it up to Terms to keep it first.")
AUTO_CORRECTIONS_HEADING = "## Corrections (added automatically)"
AUTO_CORRECTIONS_NOTE = ("Added by Claude through Stenny's connector and applied word for word to new transcripts, "
                         "like your own. Delete a wrong one; a correction of yours always wins.")
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

Stenny's list of correct spellings for this course. You don't have to keep it: when you fix a misheard name in
Stenny, it asks whether to always write it that way, and Claude can add terms and corrections through Stenny's
connector. Terms help Whisper spell names and jargon correctly; corrections (`wrong → right`) are applied word
for word to new transcripts (never to the raw output). You can also add your own below.

## Terms

## Corrections
"""


@dataclass
class Glossary:
    terms: list[str] = field(default_factory=list)       # all terms: the student's first, then the automatic ones
    corrections: list[tuple[str, str]] = field(default_factory=list)  # all corrections, the student's first
    auto_terms: list[str] = field(default_factory=list)  # the automatic ones alone
    auto_corrections: list[tuple[str, str]] = field(default_factory=list)

    @property
    def own_terms(self) -> list[str]:
        return [t for t in self.terms if t not in self.auto_terms]

    @property
    def own_corrections(self) -> list[tuple[str, str]]:
        return [c for c in self.corrections if c not in self.auto_corrections]

    @classmethod
    def parse(cls, text: str) -> "Glossary":
        g, section, manual, own_fixes = cls(), None, [], []
        for line in text.splitlines():
            if line.startswith("#"):
                section = _kind(line)
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
            elif (pair := _pair(item)) and section == "corrections":
                own_fixes.append(pair)
            elif pair:
                g.auto_corrections.append(pair)
        known = {t.casefold() for t in manual}
        mine = {w for w, _ in own_fixes} | {r for _, r in own_fixes}
        g.auto_corrections = [c for c in dict.fromkeys(g.auto_corrections)
                              if c[0] not in mine and c[0].casefold() not in known]
        wrong = {w.casefold() for w, _ in [*own_fixes, *g.auto_corrections]}
        g.auto_terms = [t for t in dict.fromkeys(g.auto_terms) if t.casefold() not in known | wrong]
        g.terms = manual + g.auto_terms
        g.corrections = own_fixes + g.auto_corrections
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


def _kind(heading: str) -> str | None:
    """Which part of the note a heading starts: "terms", "auto", "corrections", "auto_corrections" or None."""
    title = heading.lstrip("#").strip().casefold()
    if title.startswith("term"):
        return "auto" if title.startswith("terms (added") else "terms"
    if title.startswith("correction"):
        return "auto_corrections" if title.startswith("corrections (added") else "corrections"
    return None


def _pair(item: str) -> tuple[str, str] | None:
    parts = ARROW.split(item, maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 and parts[0] and parts[1] else None


def _lines(text: str) -> list[str]:
    return text.rstrip("\n").split("\n")


def _find(lines: list[str], kind: str) -> tuple[int, int] | None:
    """(heading line, end) of the first section of this kind."""
    start = next((i for i, l in enumerate(lines) if l.startswith("#") and _kind(l) == kind), None)
    if start is None:
        return None
    return start, next((i for i in range(start + 1, len(lines)) if lines[i].startswith("#")), len(lines))


def _replace_section(text: str, kind: str, heading: str, note: str, items: list[str], before: str | None) -> str:
    """The note with one automatic section replaced by `items` (or removed if there are none): where it was, else
    before the section of kind `before`, else at the end. Everything else stays exactly as it was."""
    lines = _lines(text)
    start = None
    if found := _find(lines, kind):
        start, end = found
        del lines[start:end]
        while start < len(lines) and start > 0 and not lines[start - 1].strip() and not lines[start].strip():
            del lines[start]  # don't leave a double blank line behind
    section = [heading, note, *[f"- {i}" for i in items], ""] if items else []
    at = start if start is not None else (_find(lines, before) or (len(lines),))[0] if before else len(lines)
    if at == len(lines) and section and lines and lines[-1].strip():
        section = ["", *section]
    lines[at:at] = section
    return "\n".join(lines).rstrip("\n") + "\n"


def with_auto_terms(text: str, terms: list[str]) -> str:
    """The glossary note with its automatic terms replaced by `terms` (inserted before Corrections, or at the end)."""
    return _replace_section(text, "auto", AUTO_HEADING, AUTO_NOTE, terms, before="corrections")


def with_auto_corrections(text: str, corrections: list[tuple[str, str]]) -> str:
    """The glossary note with its automatic corrections replaced (a section of their own, at the end)."""
    return _replace_section(text, "auto_corrections", AUTO_CORRECTIONS_HEADING, AUTO_CORRECTIONS_NOTE,
                            [f"{w} → {r}" for w, r in corrections], before=None)


def _add_item(lines: list[str], kind: str, heading: str, item: str, replace=None) -> list[str]:
    """`- item` in the student's section of this kind: in place of the first item `replace` matches, else after its
    last line. A missing section is added at the end."""
    found = _find(lines, kind)
    if found is None:
        return [*lines, *([""] if lines and lines[-1].strip() else []), heading, f"- {item}"]
    start, end = found
    for i in range(start + 1, end):
        current = lines[i].strip()
        if current.startswith(("- ", "* ")) and replace and replace(current[2:].strip()):
            return [*lines[:i], f"- {item}", *lines[i + 1:]]
    last = max(i for i in range(start, end) if lines[i].strip())
    return [*lines[:last + 1], f"- {item}", *lines[last + 1:]]


def add_correction(text: str, wrong: str, right: str) -> str:
    """The note after the student chose "Always write it like this": `wrong → right` in Corrections (in place of
    their own earlier one for `wrong`) and `right` in Terms, if it isn't there yet. Automatic entries that now
    contradict the student's are dropped; nothing else of theirs changes."""
    g = Glossary.parse(text)
    lines = _add_item(_lines(text), "corrections", "## Corrections", f"{wrong} → {right}",
                      replace=lambda item: (_pair(item) or ("",))[0] == wrong)
    if right.casefold() not in {t.casefold() for t in g.own_terms}:
        lines = _add_item(lines, "terms", "## Terms", right)
    text = "\n".join(lines).rstrip("\n") + "\n"
    g = Glossary.parse(text)  # parse drops what contradicts the student; write the automatic sections that way
    return with_auto_corrections(with_auto_terms(text, g.auto_terms), g.auto_corrections)


def occurrences(term: str, text: str) -> int:
    """How often `term` occurs in `text` as whole words, ignoring case."""
    return len(re.findall(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.I))


def rank_auto_terms(glossary: Glossary, new: list[str], recent: list[str]) -> list[str]:
    """The automatic terms: the old ones plus `new`, without those the student already listed (or a correction
    corrects away), sorted by in how many of the `recent` transcripts they occur (then: older first, so the order
    is stable). A term in `new` counts as used in all of them, so Whisper gets it until the next lectures show
    how much it's used. At most MAX_AUTO_TERMS: Whisper only ever sees the first few dozen anyway."""
    manual = {t.casefold() for t in glossary.own_terms}
    wrong = {w.casefold() for w, _ in glossary.corrections}
    out, seen = [], set()
    for t in [*glossary.auto_terms, *new]:
        key = t.casefold()
        if key in seen or key in manual or key in wrong:
            continue
        seen.add(key)
        out.append(t)
    fresh = {t.casefold() for t in new}
    score = {t: len(recent) if t.casefold() in fresh else sum(1 for text in recent if occurrences(t, text)) for t in out}
    order = {t: i for i, t in enumerate(out)}
    return sorted(out, key=lambda t: (-score[t], order[t]))[:MAX_AUTO_TERMS]
