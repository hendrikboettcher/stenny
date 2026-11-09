"""Whisper engines behind one small interface, so the backend stays swappable.

An engine provides:
    transcribe(audio, language, prompt) -> list of segments {start, end, text, no_speech_prob, avg_logprob}
    language_probs(clip, allowed) -> {lang: probability} restricted to `allowed`
    release() -> hands the model's memory back

mlx-whisper on a Mac (Metal, Apple Silicon); faster-whisper everywhere else (Windows). `[transcription] engine`
picks one by name, e.g. faster-whisper on a Mac to compare the two.
"""

import sys

ENGINES = ("auto", "mlx", "faster-whisper")


def resolve(engine: str = "auto") -> str:
    """The engine `auto` stands for on this computer."""
    if engine == "auto":
        return "mlx" if sys.platform == "darwin" else "faster-whisper"
    return engine


def get_engine(model: str, engine: str = "auto"):
    engine = resolve(engine)
    if engine == "mlx":
        from .mlx_engine import MlxEngine

        return MlxEngine(model)
    if engine == "faster-whisper":
        from .faster_engine import FasterWhisperEngine

        return FasterWhisperEngine(model)
    raise ValueError(f"Unknown engine '{engine}': use {', '.join(ENGINES)}.")
