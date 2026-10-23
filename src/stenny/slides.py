"""Lecture slides (a PDF in the lecture folder): text per page, for three uses:

- terms (names, acronyms, compounds) that bias Whisper's spelling, after the glossary terms;
- reference text for the AI structuring step (section → slide pages);
- a second source of correct spellings for term fixes (see llm/guard.py).

Text comes from poppler's `pdftotext` when installed, else from pypdf. Scanned PDFs without a
text layer give empty pages; they are still stored but not used.
"""

import re
import shutil
import subprocess
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

TOKEN = re.compile(r"\w+(?:[-'’]\w+)*")
MIN_TERM_LETTERS = 3
BOILERPLATE_SHARE = 0.8   # a term on ≥80 % of pages (header/footer: university, lecturer) isn't useful
REFERENCE_MAX_CHARS = 30_000  # slide text sent with each structuring request (~8k tokens)


class SlidesError(Exception):
    pass


def clean(text: str) -> str:
    """NFKC (ligatures ﬁ → fi), no soft hyphens, words split at a line end joined again."""
    text = unicodedata.normalize("NFKC", text).replace("­", "")
    # "Weier-\nstrass" → "Weierstrass", but "Bolzano-\nWeierstrass" keeps its hyphen
    text = re.sub(r"(\w)-\n\s*(\w)", lambda m: m[1] + ("-" if m[2].isupper() else "") + m[2], text)
    return "\n".join(" ".join(line.split()) for line in text.splitlines())


def extract_pages(path: Path) -> list[str]:
    """Text of each page, in order."""
    if exe := shutil.which("pdftotext"):
        p = subprocess.run([exe, "-enc", "UTF-8", str(path), "-"], capture_output=True, text=True)
        if p.returncode == 0:
            pages = p.stdout.split("\f")
            return [clean(t) for t in (pages[:-1] if pages and not pages[-1].strip() else pages)]
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        raise SlidesError("Reading slides needs pypdf (`uv sync`) or poppler (`brew install poppler`).") from None
    try:
        return [clean(page.extract_text() or "") for page in PdfReader(path).pages]
    except (PdfReadError, OSError, ValueError) as e:
        raise SlidesError(f"Could not read {path.name}: {e}") from None


@dataclass
class Slides:
    pages: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Slides":
        return cls(extract_pages(path))

    @property
    def count(self) -> int:
        return len(self.pages)

    @property
    def has_text(self) -> bool:
        return any(p.strip() for p in self.pages)

    @property
    def flat(self) -> str:
        return " ".join(" ".join(p.split()) for p in self.pages)

    def contains(self, phrase: str, ignore_case: bool = False) -> bool:
        """Does `phrase` occur verbatim (as whole words) in the slide text?"""
        phrase = " ".join(phrase.split())
        if not phrase:
            return False
        return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", self.flat, re.I if ignore_case else 0) is not None

    def terms(self, skip: list[str] = ()) -> list[str]:
        """Names, acronyms and capitalised compounds from the slides, most widespread first.
        A capitalised word counts only if it also appears mid-sentence (so "Exemple" at the start
        of a line doesn't), unless it has a capital inside (CNRS, Bolzano-Weierstrass, LaTeX)."""
        known = {w.casefold() for t in skip for w in [t, *TOKEN.findall(t)]}
        pages_with, first_seen, mid_sentence = Counter(), {}, set()
        for page in self.pages:
            seen = set()
            for line in page.splitlines():
                for m in TOKEN.finditer(line):
                    word = m.group(0)
                    if sum(ch.isalpha() for ch in word) < MIN_TERM_LETTERS or not any(ch.isupper() for ch in word):
                        continue
                    before = line[:m.start()].rstrip()
                    if before and before[-1] not in ".!?:;•▪●◦–—-*>":
                        mid_sentence.add(word)
                    first_seen.setdefault(word, len(first_seen))
                    seen.add(word)
            pages_with.update(seen)
        many = self.count >= 5
        out, taken = [], set()
        for word in sorted(first_seen, key=lambda w: (-pages_with[w], first_seen[w])):
            inner_cap = any(ch.isupper() for ch in word[1:])
            if not (inner_cap or word in mid_sentence) or word.casefold() in known or word.casefold() in taken:
                continue
            if many and pages_with[word] >= BOILERPLATE_SHARE * self.count:
                continue
            out.append(word)
            taken.add(word.casefold())
        return out

    def reference(self, max_chars: int = REFERENCE_MAX_CHARS) -> str:
        """Slide text for the LLM, one block per page ("[Slide 3] …"), cut at `max_chars`."""
        out, size = [], 0
        for i, page in enumerate(self.pages, 1):
            text = " ".join(page.split()) or "(no text)"
            block = f"[Slide {i}] {text}"
            if size + len(block) > max_chars:
                out.append(f"[Slides {i}-{self.count} left out: too long]")
                break
            out.append(block)
            size += len(block)
        return "\n".join(out)


def load_from(path: Path | None, log=print) -> Slides | None:
    """The slides PDF of a lecture (vault.LectureFiles.slides), if present and readable (problems are
    logged, not raised: the transcript is complete without slides)."""
    if not path or not path.exists():
        return None
    try:
        slides = Slides.load(path)
    except SlidesError as e:
        log(f"Slides skipped: {e}")
        return None
    if not slides.has_text:
        log(f"{path.name} has no text layer (scanned?); it stays in the folder but isn't used.")
        return None
    return slides
