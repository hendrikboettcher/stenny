"""Which Whisper language to use for which part of a recording.

- fixed  ("fr")    : the course always uses one language.
- auto             : one language per lecture, voted from samples spread over the recording
                     (Whisper alone only looks at the first 30 s, often chatter before the start).
- mixed            : language detected per short chunk (4-12 s, cut at pauses) within the allowed set.
                     Needed because a fixed language makes Whisper *translate* other-language
                     passages instead of transcribing them.
"""

import numpy as np

from .config import LANGUAGE_MODES, Course
from .textproc import SAMPLE_RATE

STICKY = 0.65  # in mixed mode, switch language only when the new one is this likely


def fixed_override(override: str | None) -> str | None:
    """`--lang`: a language code fixes the language; "auto" and "mixed" only switch the mode (see
    Course.with_language), so they are no override for Whisper."""
    return None if override in LANGUAGE_MODES else override


def vote_language(audio: np.ndarray, engine, allowed: list[str]) -> tuple[str, dict[str, float]]:
    window = 30 * SAMPLE_RATE
    positions = [0.1, 0.3, 0.5, 0.7, 0.9] if len(audio) > 2 * window else [0.0]
    scores = dict.fromkeys(allowed, 0.0)
    for p in positions:
        start = int(len(audio) * p)
        for lang, prob in engine.language_probs(audio[start : start + window], allowed).items():
            scores[lang] += prob / len(positions)
    return max(scores, key=scores.get), scores


def plan(audio: np.ndarray, chunks: list[tuple[int, int]], course: Course, engine,
         override: str | None = None) -> tuple[list[str], str]:
    """Return one language per chunk and a short human-readable note."""
    fixed = fixed_override(override) or course.fixed_language
    if fixed:
        return [fixed] * len(chunks), f"{fixed} (fixed)"
    if course.language == "auto" or len(course.languages) == 1:
        lang, scores = vote_language(audio, engine, course.languages)
        return [lang] * len(chunks), f"{lang} (auto: " + ", ".join(f"{k} {v:.0%}" for k, v in scores.items()) + ")"
    langs, current = [], None
    for a, b in chunks:
        probs = engine.language_probs(audio[a:b], course.languages)
        best = max(probs, key=probs.get)
        if current is None or (best != current and probs[best] >= STICKY):
            current = best
        langs.append(current)
    share = {lang: langs.count(lang) / len(langs) for lang in course.languages if lang in langs}
    return langs, "mixed (" + ", ".join(f"{k} {v:.0%}" for k, v in share.items()) + ")"


def pick(clip: np.ndarray, course: Course, engine, current: str | None, override: str | None = None) -> str:
    """Language for one live chunk: fixed if configured, otherwise detected (sticky, like mixed mode)."""
    fixed = fixed_override(override) or course.fixed_language
    if fixed:
        return fixed
    probs = engine.language_probs(clip, course.languages)
    best = max(probs, key=probs.get)
    return best if current is None or (best != current and probs[best] >= STICKY) else current
