"""Whisper engines behind one small interface, so the backend stays swappable.

An engine provides:
    transcribe(audio, language, prompt) -> list of segments {start, end, text, no_speech_prob, avg_logprob}
    language_probs(clip, allowed) -> {lang: probability} restricted to `allowed`
"""

from .mlx_engine import MlxEngine


def get_engine(model: str) -> MlxEngine:
    return MlxEngine(model)
