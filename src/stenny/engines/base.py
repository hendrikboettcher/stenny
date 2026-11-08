"""What every engine shares: the segment fields the pipeline reads, and bringing quiet audio up to speech level."""

import numpy as np

SEGMENT_KEYS = ("start", "end", "text", "no_speech_prob", "avg_logprob", "compression_ratio")
MAX_GAIN_DB = 30.0


def normalize(audio: np.ndarray) -> np.ndarray:
    """Bring quiet audio (a distant phone microphone) up to normal speech level. Whisper's input
    features depend on absolute loudness; at -50 dB it invents text in random languages."""
    peak = float(np.percentile(np.abs(audio), 99.9)) if len(audio) else 0.0
    gain = min(0.5 / max(peak, 1e-6), 10 ** (MAX_GAIN_DB / 20))
    return (audio * gain).astype(np.float32) if gain > 1.0 else audio


def restrict(probs: dict[str, float], allowed: list[str]) -> dict[str, float]:
    """Language probabilities among `allowed` only, adding up to 1."""
    scores = {lang: float(probs.get(lang, 0.0)) for lang in allowed}
    total = sum(scores.values()) or 1.0
    return {k: v / total for k, v in scores.items()}
