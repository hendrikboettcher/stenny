"""The two LLM tasks: (1) propose an edit list (sections + term fixes) plus the lecture's topics and glossary
terms, (2) write a summary."""

import json
import re

from ..glossary import Glossary
from ..render import LANG_NAMES
from ..textproc import fmt_hms
from . import LLMError, Usage

CHUNK_CHARS = 45_000  # ~12k tokens of French per request: reliable for a small model, cheap

STRUCTURE_SYSTEM = """You add structure to an automatic lecture transcript. You never rewrite, shorten, translate or add content.

You receive numbered paragraphs ("P<number> [h:mm:ss] text"; "(unsure)" after the time means Whisper was unsure about part of that paragraph). Reply with ONLY a JSON object, no prose:
{"sections": [{"at": <paragraph number>, "title": "<heading>"}],
 "term_fixes": [{"p": <paragraph number>, "from": "<exact words from that paragraph>", "to": "<corrected words>"}],
 "topics": ["<topic>"],
 "terms": ["<term>"]}

sections:
- Start a new section where the lecturer moves to a new topic (typically every 5-20 minutes).
- Titles: at most 8 words, written in the lecture language given below (never translate them to English unless the lecture is in English), naming the topic of the following paragraphs. Use only what is said.

term_fixes (optional, use sparingly):
- Only where a glossary term was clearly misheard (e.g. "Rimane" for the glossary term "Riemann"). "to" must be a glossary term.
- "from" must be copied exactly from the paragraph (1-4 words). "to" must be what the lecturer clearly said.
- Never fix grammar, style, filler words, repetitions or anything you are not sure about. When in doubt, leave it out.

topics: the 3-8 main subjects of these paragraphs, for linking lectures that cover the same thing.
- Specific concepts, methods, models, theorems or named things (e.g. "Naive Bayes", "cross-validation"), never generic words ("introduction", "example", "overview", "exercise").
- Name each the way it is usually written, in the lecture language, 1-5 words, singular. If one of the existing topics below means the same thing, use its name exactly.
- Only what these paragraphs actually discuss, not what is merely mentioned in passing.

terms: up to 15 technical terms and names from these paragraphs that a speech recognizer could misspell (jargon, method names, people, places), each written correctly. Only words the lecturer used; no common words, and none that are already in the glossary."""

SLIDES_RULES = """

Slides: the lecture's slides follow as reference text ("[Slide <page>] text"). Never copy slide text into titles beyond a term, and never add content from the slides.
- sections may also say which slide pages they cover: {"at": <paragraph number>, "title": "<heading>", "slides": [<first page>, <last page>]}. Only when the paragraphs clearly discuss those slides; otherwise leave "slides" out.
- term_fixes may also correct a misheard term to a spelling that appears exactly in the slide text ("to" copied verbatim from the slides), when the lecturer clearly said that term. Misheard terms are most likely in paragraphs marked (unsure)."""

SUMMARY_SYSTEM = """You write study summaries of university lectures from their transcripts.
Use only information that appears in the transcript. Do not add outside knowledge, examples or explanations.
If something in the transcript is unclear or seems misheard, say so instead of guessing."""

SUMMARY_PROMPT = """Write a summary of this lecture in {language}, as Markdown with exactly these sections:

## Overview
3-5 sentences: what the lecture covered.

## Key points
Bullets grouped by topic. End each bullet with the timestamp where it is discussed, like [0:12:30].

## Definitions, theorems & formulas
Only those stated in the lecture, with timestamps. Write "—" if none.

## Organisational
Exams, homework, deadlines, announcements, with timestamps. Write "—" if none.

Do not add a title. Transcript:

{transcript}"""


def numbered(paragraphs: list[dict], offset: int = 0) -> str:
    return "\n\n".join(f"P{offset + i} [{fmt_hms(p['start'])}]{' (unsure)' if p.get('unsure') else ''} {p['text']}"
                         for i, p in enumerate(paragraphs))


def chunks(paragraphs: list[dict], limit: int = CHUNK_CHARS) -> list[tuple[int, list[dict]]]:
    out, start, size = [], 0, 0
    for i, p in enumerate(paragraphs):
        if size and size + len(p["text"]) > limit:
            out.append((start, paragraphs[start:i]))
            start, size = i, 0
        size += len(p["text"])
    out.append((start, paragraphs[start:]))
    return out


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LLMError(f"No JSON in LLM reply: {text[:300]}")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise LLMError(f"Invalid JSON from LLM: {e}") from None


def propose_edits(paragraphs: list[dict], glossary: Glossary, languages: list[str], backend,
                  slides=None, topics: list[str] = ()) -> tuple[dict, Usage]:
    """Ask for sections, term fixes, topics and glossary terms chunk by chunk. Returns the raw (unvalidated)
    proposals as {"sections", "term_fixes", "topics", "terms"}. `slides` (slides.Slides): their text goes
    along as reference, for slide pages and spellings. `topics`: topic names already in use, to reuse."""
    lang = " / ".join(LANG_NAMES.get(l, l) for l in languages)
    terms = ", ".join(glossary.terms) or "(none)"
    existing = "; ".join(topics) or "(none yet)"
    system = STRUCTURE_SYSTEM + (SLIDES_RULES if slides else "")
    reference = f"Slides ({slides.count} pages):\n{slides.reference()}\n\n" if slides else ""
    sections, fixes, found_topics, found_terms, usage = [], [], [], [], Usage()
    for offset, part in chunks(paragraphs):
        prompt = (f"Lecture language: {lang} (write section titles in {lang})\nGlossary (correct spellings): {terms}\n"
                  f"Existing topics: {existing}\n"
                  f"{reference}Paragraphs P{offset}-P{offset + len(part) - 1}:\n\n{numbered(part, offset)}")
        text, u = backend.complete(system, prompt)
        usage += u
        data = parse_json(text)
        sections += data.get("sections") or []
        fixes += data.get("term_fixes") or []
        found_topics += [t for t in data.get("topics") or [] if isinstance(t, str)]
        found_terms += [t for t in data.get("terms") or [] if isinstance(t, str)]
    # drop a repeated heading where one chunk's last topic continues into the next chunk
    deduped = []
    for s in sections:
        if deduped and str(s.get("title", "")).casefold() == str(deduped[-1].get("title", "")).casefold():
            continue
        deduped.append(s)
    return {"sections": deduped, "term_fixes": fixes, "topics": found_topics, "terms": found_terms}, usage


def summarize(paragraphs: list[dict], languages: list[str], backend) -> tuple[str, Usage]:
    lang = LANG_NAMES.get(languages[0], languages[0])
    text, usage = backend.complete(SUMMARY_SYSTEM, SUMMARY_PROMPT.format(
        language=lang, transcript="\n\n".join(f"[{fmt_hms(p['start'])}] {p['text']}" for p in paragraphs)))
    return text, usage
