"""mlx-whisper (Metal GPU on Apple Silicon)."""

import numpy as np

SEGMENT_KEYS = ("start", "end", "text", "no_speech_prob", "avg_logprob", "compression_ratio")
MAX_GAIN_DB = 30.0


def normalize(audio: np.ndarray) -> np.ndarray:
    """Bring quiet audio (a distant phone microphone) up to normal speech level. Whisper's input
    features depend on absolute loudness; at -50 dB it invents text in random languages."""
    peak = float(np.percentile(np.abs(audio), 99.9)) if len(audio) else 0.0
    gain = min(0.5 / max(peak, 1e-6), 10 ** (MAX_GAIN_DB / 20))
    return (audio * gain).astype(np.float32) if gain > 1.0 else audio


class MlxEngine:
    name = "mlx-whisper"

    def __init__(self, model: str):
        self.model = model

    def transcribe(self, audio: np.ndarray, language: str, prompt: str | None = None) -> list[dict]:
        import mlx_whisper

        result = mlx_whisper.transcribe(
            normalize(audio),
            path_or_hf_repo=self.model,
            language=language,
            task="transcribe",
            initial_prompt=prompt,
            condition_on_previous_text=False,  # prevents hallucination loops in long recordings
            temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
            verbose=None,
        )
        return [{k: s[k] for k in SEGMENT_KEYS if k in s} | {"language": language} for s in result["segments"]]

    def release(self) -> None:
        """Hand the model's memory back; the next transcription loads it again."""
        import gc

        import mlx.core as mx
        from mlx_whisper.transcribe import ModelHolder

        ModelHolder.model = ModelHolder.model_path = None
        gc.collect()
        mx.clear_cache()

    def language_probs(self, clip: np.ndarray, allowed: list[str]) -> dict[str, float]:
        import mlx.core as mx
        from mlx_whisper.audio import N_FRAMES, log_mel_spectrogram, pad_or_trim
        from mlx_whisper.decoding import detect_language
        from mlx_whisper.transcribe import ModelHolder

        model = ModelHolder.get_model(self.model, mx.float16)
        mel = log_mel_spectrogram(normalize(clip), n_mels=model.dims.n_mels)
        mel = pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16)
        _, probs = detect_language(model, mel)
        scores = {lang: float(probs.get(lang, 0.0)) for lang in allowed}
        total = sum(scores.values()) or 1.0
        return {k: v / total for k, v in scores.items()}
