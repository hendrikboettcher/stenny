"""Validate and apply the LLM's edit list. The LLM never rewrites text; it may only propose
section headings (optionally with slide pages) and small term fixes. A fix is applied only if it
replaces a misheard spelling by a term from the course glossary, or by words that appear verbatim
in the lecture's slides; everything else is rejected (and logged in segments.json)."""

import difflib
import re

from ..glossary import Glossary

MAX_TITLE_CHARS = 90
MAX_FIX_WORDS = 5
MIN_SIMILARITY = 0.5
# A slide-based fix where Whisper was confident must be a much closer spelling: confident segments are
# usually right, and slide text is full of ordinary words (converge/diverge are 0.67 apart).
SURE_SIMILARITY = 0.75


def _words(text: str) -> list[str]:
    return re.findall(r"\w+(?:[-'’]\w+)*", text)


def _occurs(needle: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", text) is not None


def paragraph_index(value) -> int:
    """Accept 12, "12" or "P12" (models often echo the paragraph labels they were shown)."""
    return int(str(value).strip().lstrip("Pp"))


def slide_range(value, n_pages: int) -> tuple[int, int] | None:
    """[12, 15], [12], 12, "12-15" or "12–15" → (12, 15), if within 1..n_pages; else None."""
    if isinstance(value, (int, str)):
        value = re.split(r"\s*[-–—]\s*", str(value).strip())
    if not isinstance(value, list) or not 1 <= len(value) <= 2:
        return None
    try:
        a, b = int(value[0]), int(value[-1])
    except (TypeError, ValueError):
        return None
    return (a, b) if 1 <= a <= b <= n_pages else None


def validate_sections(proposed, n_paragraphs: int, n_pages: int = 0) -> list[dict]:
    """Keep well-formed headings in order. Slide pages are kept only if they exist in Slides.pdf."""
    sections, last = [], -1
    for s in proposed if isinstance(proposed, list) else []:
        try:
            at, title = paragraph_index(s["at"]), str(s.get("title") or "").strip()
        except (KeyError, TypeError, ValueError):
            continue
        title = re.sub(r"[#*_`\[\]\n]", "", title)[:MAX_TITLE_CHARS].strip()
        if not 0 <= at < n_paragraphs or at <= last or not title:
            continue
        pages = slide_range(s.get("slides"), n_pages) if n_pages and s.get("slides") is not None else None
        sections.append({"at": at, "title": title} | ({"slides": pages} if pages else {}))
        last = at
    if not sections or sections[0]["at"] != 0:
        sections.insert(0, {"at": 0, "title": None})
    return sections


def fix_basis(fix, paragraph: str, glossary: Glossary, slides=None, unsure: list[str] = ()) -> tuple[str | None, str | None]:
    """Check one proposed fix. Returns (basis, rejection reason): basis is "glossary" or "slides"
    when the fix is acceptable (reason None), else None with the reason.

    Slide-based fixes (replacement not in the glossary) also need:
    - the replacement verbatim in the slide text (exact case, whole words);
    - Whisper's words NOT in the slides (if the slides use them too, they are probably right);
    - a closer spelling when Whisper was confident about those words (see SURE_SIMILARITY)."""
    try:
        old, new = str(fix["from"]).strip(), str(fix["to"]).strip()
    except (KeyError, TypeError):
        return None, "malformed"
    if not old or not new or old == new:
        return None, "empty or no-op"
    if not _occurs(old, paragraph):
        return None, "'from' not found in paragraph"
    ow, nw = _words(old), _words(new)
    if len(ow) > MAX_FIX_WORDS or len(nw) > MAX_FIX_WORDS or abs(len(ow) - len(nw)) > 2:
        return None, "too large"
    similarity = difflib.SequenceMatcher(None, old.casefold(), new.casefold()).ratio()
    # Only toward a known spelling: near spellings alone can flip meaning (converge → diverge).
    if any(new.casefold() == t.casefold() or _occurs(new.casefold(), t.casefold()) for t in glossary.terms):
        return ("glossary", None) if similarity >= MIN_SIMILARITY else (None, "not a near spelling of the original")
    if slides is None:
        return None, "not a glossary term"
    if not slides.contains(new):
        return None, "not a glossary term or in the slides"
    if slides.contains(old, ignore_case=True):
        return None, "original words also appear in the slides"
    if similarity < MIN_SIMILARITY:
        return None, "not a near spelling of the original"
    if similarity < SURE_SIMILARITY and not any(_occurs(old, u) for u in unsure):
        return None, "Whisper was confident here and the spellings differ too much"
    return "slides", None


def apply_fixes(paragraphs: list[dict], fixes, glossary: Glossary, slides=None) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (new paragraphs, applied fixes, rejected fixes with reasons). `slides` (slides.Slides)
    allows fixes toward words from the slide text; see fix_basis."""
    out = [dict(p) for p in paragraphs]
    applied, rejected = [], []
    for fix in fixes if isinstance(fixes, list) else []:
        try:
            i = paragraph_index(fix["p"])
            text = out[i]["text"]
        except (KeyError, TypeError, ValueError, IndexError):
            rejected.append({"fix": fix, "reason": "bad paragraph number"})
            continue
        basis, reason = fix_basis(fix, text, glossary, slides, out[i].get("unsure", []))
        if reason:
            rejected.append({"fix": fix, "reason": reason})
            continue
        old, new = str(fix["from"]).strip(), str(fix["to"]).strip()
        out[i]["text"] = re.sub(rf"(?<!\w){re.escape(old)}(?!\w)", lambda _: new, text)  # every occurrence
        applied.append({"p": i, "from": old, "to": new, "basis": basis})
    return out, applied, rejected


def word_changes(before: str, after: str) -> list[tuple[str, str]]:
    """Word-level differences between two texts, as (old, new) pairs."""
    a, b = _words(before), _words(after)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return [(" ".join(a[i1:i2]), " ".join(b[j1:j2])) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]


def verify(before: list[dict], after: list[dict], allowed: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Every word-level change between the texts must come from an allowed (old, new) pair.
    Returns the unexplained changes (empty list = OK)."""
    if len(before) != len(after):
        return [("<paragraph count>", f"{len(before)} → {len(after)}")]
    allowed_words = {(" ".join(_words(o)), " ".join(_words(n))) for o, n in allowed}
    unexplained = []
    for p, q in zip(before, after):
        for old, new in word_changes(p["text"], q["text"]):
            if (old, new) not in allowed_words and not any(old in o and new in n for o, n in allowed_words):
                unexplained.append((old, new))
    return unexplained


# --- topics and glossary terms: they never change the text, but they need evidence in it -------------

MAX_TOPICS = 12
MAX_TOPIC_CHARS = 60
MAX_TOPIC_WORDS = 6
MAX_TERM_WORDS = 5
MAX_TERM_CHARS = 50


def _forms(word: str) -> set[str]:
    """A word and its singular candidates ("suites" → suites, suite, suit), so plural and singular match."""
    w = word.casefold().replace("’", "'").removesuffix("'s").rstrip("'")
    forms = {w}
    if len(w) > 3 and w.endswith("s"):
        forms.add(w[:-1])
    if len(w) > 4 and w.endswith("es"):
        forms.add(w[:-2])
    if len(w) > 4 and w.endswith("ies"):
        forms.add(w[:-3] + "y")
    return forms


def clean_topic(value) -> str:
    """A topic name usable as a note name and in links: no [ ] # | ^ : / \\ * ? " < >, single spaces."""
    text = re.sub(r'[\[\]#|^:/\\*?"<>`]', " ", str(value or ""))
    return " ".join(text.split()).strip(" .,;-")[:MAX_TOPIC_CHARS].strip()


def validate_topics(proposed, text: str, slides=None, existing: list[str] = (), reserved: list[str] = ()) -> list[str]:
    """Keep the proposed topics that have evidence: each of their words (3+ letters; plural or singular)
    occurs in the transcript or on the slides. Names already used for a topic keep their spelling.
    `reserved`: names that must not become topics (the course names; their notes already exist).
    Most often mentioned first, at most MAX_TOPICS."""
    seen_words = set().union(*(_forms(w) for w in _words(text + " " + (slides.flat if slides else ""))))
    canonical = {t.casefold(): t for t in existing}
    banned = {r.casefold() for r in reserved}
    out = []
    for value in proposed if isinstance(proposed, list) else []:
        if not isinstance(value, str):
            continue
        topic = clean_topic(value)
        words, content = _words(topic), [w for w in _words(topic) if len(w) >= 3]
        if not content or len(words) > MAX_TOPIC_WORDS or topic.casefold() in banned:
            continue
        if not all(_forms(w) & seen_words for w in content):
            continue
        topic = canonical.get(topic.casefold(), topic)
        if topic.casefold() not in {t.casefold() for t in out}:
            out.append(topic)
    count = {t: len(re.findall(rf"(?<!\w){re.escape(t)}(?!\w)", text, re.I)) for t in out}
    return sorted(out, key=lambda t: -count[t])[:MAX_TOPICS]


# --- glossary entries from Claude (connector) and from the student's edits ------------------------------------
# Claude's terms and corrections are taken as they are (the student decided so): no evidence in the transcripts is
# needed. They only have to be the shape of a spelling, so a correction stays a word-for-word fix, never a rewrite.

MAX_ENTRY_CHARS = 60
MARKUP = re.compile(r"[\[\]#|`*\n<>{}]|→|->|=>")


def clean_entry(value) -> str:
    return " ".join(str(value or "").split()).strip(" .,;:")


def check_term(term: str) -> str | None:
    """Why `term` can't be a glossary term, or None if it can: 1-5 words, no Markdown, not a plain common word."""
    words = _words(term)
    if not words or len(words) > MAX_TERM_WORDS or len(term) > MAX_TERM_CHARS or MARKUP.search(term):
        return "not a name or term (1-5 words, no Markdown)"
    if len(words) == 1 and term.islower() and term.isalpha() and len(term) < 6:
        return "a common word, not worth a place in Whisper's prompt"
    return None


def check_correction(wrong: str, right: str) -> str | None:
    """Why `wrong → right` can't be a glossary correction, or None if it can: a different spelling of at most
    MAX_FIX_WORDS words on each side (applied word for word wherever `wrong` occurs, so never a short common word
    like "the", which would change ordinary text everywhere)."""
    if not wrong or not right or wrong == right:
        return "empty or no change"
    ow, nw = _words(wrong), _words(right)
    if (not ow or not nw or len(ow) > MAX_FIX_WORDS or len(nw) > MAX_FIX_WORDS or abs(len(ow) - len(nw)) > 2
            or max(len(wrong), len(right)) > MAX_ENTRY_CHARS):
        return "too long for a spelling fix (at most 5 words on each side)"
    if MARKUP.search(wrong) or MARKUP.search(right):
        return "no Markdown or arrows inside"
    if len(ow) == 1 and wrong.islower() and wrong.isalpha() and len(wrong) <= 3:
        return f"'{wrong}' is too common a word to replace everywhere"
    return None


def _spans(text: str) -> list[re.Match]:
    return list(re.finditer(r"\w+(?:[-'’]\w+)*", text))


def _looks_like_a_name(old: str, new: str, sentence_start: bool) -> bool:
    if old.casefold() == new.casefold():  # only the case changed: a name ("bayes" → "Bayes"), unless a sentence begins
        return not sentence_start and len(new) >= 4 and any(c.isupper() for c in new)
    return (any(c.isupper() or c.isdigit() for c in new) or "-" in new
            or any(len(w) >= 9 for w in _words(new)))


def spelling_changes(before: str, after: str, limit: int = 3) -> list[tuple[str, str]]:
    """What the student's edit of a passage changed that looks like a misheard name or term, as (wrong, right),
    for "Always write it like this?": replaced words (not added or deleted ones) of the shape check_correction
    accepts, where the new spelling has a capital, digit or hyphen, or is a long word. Fixing ordinary words or
    rewording asks nothing."""
    a, b = _spans(before), _spans(after)
    sm = difflib.SequenceMatcher(None, [m.group() for m in a], [m.group() for m in b], autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != "replace":
            continue
        old, new = before[a[i1].start():a[i2 - 1].end()], after[b[j1].start():b[j2 - 1].end()]
        sentence_start = not before[:a[i1].start()].strip() or before[:a[i1].start()].rstrip()[-1] in ".!?:"
        if check_correction(old, new) is None and _looks_like_a_name(old, new, sentence_start) \
                and (old, new) not in out:
            out.append((old, new))
    return out[:limit]
