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

    def transcribe(self, audio: np.ndarray, language: str, prompt: str | None = None, fast: bool = False) -> list[dict]:
        """fast: single greedy pass without Whisper's temperature fallback (live drafts: ~30 % faster,
        and bad audio can't make it retry over and over)."""
        import mlx_whisper

        result = mlx_whisper.transcribe(
            normalize(audio),
            path_or_hf_repo=self.model,
            language=language,
            task="transcribe",
            initial_prompt=prompt,
            condition_on_previous_text=False,  # prevents hallucination loops in long recordings
            temperature=(0.0,) if fast else (0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
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

    def quick(self, audio: np.ndarray, language: str, prompt: str | None = None) -> tuple[str, float]:
        """Rough text of a short clip, fast: for the live draft's provisional line, replaced by the real
        transcription seconds later. Whisper always encodes a 30 s window (~2.5 s on an M4 Air, however short
        the clip); here the window is cut to the clip, as whisper.cpp's streaming mode does (~0.5-1 s).
        Short windows make Whisper repeat itself, so the length is capped and loops are cut off.
        Returns (text, average log probability)."""
        import mlx.core as mx
        from mlx_whisper.audio import log_mel_spectrogram
        from mlx_whisper.decoding import DecodingOptions, decode
        from mlx_whisper.transcribe import ModelHolder

        from ..textproc import SAMPLE_RATE, trim_repeats

        _allow_short_window()
        model = ModelHolder.get_model(self.model, mx.float16)
        seconds = len(audio) / SAMPLE_RATE
        padded = np.concatenate([normalize(audio), np.zeros(SAMPLE_RATE // 2, np.float32)])
        mel = log_mel_spectrogram(mx.array(padded), n_mels=model.dims.n_mels)
        mel = mel[: min(mel.shape[0] // 2 * 2, 2 * model.dims.n_audio_ctx)]
        result = decode(model, mel, DecodingOptions(language=language, prompt=prompt, without_timestamps=True,
                                                    fp16=True, sample_len=int(seconds * 4.5) + 6))
        return trim_repeats(result.text.strip()), float(result.avg_logprob)

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


_patched = False


def _allow_short_window() -> None:
    """Let mlx-whisper's audio encoder take less than 30 s: its positional embedding is cut to the input.
    For a full 30 s window (everything else in this tool) it computes exactly what the original does."""
    global _patched
    if _patched:
        return
    import mlx.nn as nn
    from mlx_whisper.whisper import AudioEncoder

    def call(self, x):
        x = nn.gelu(self.conv1(x))
        x = nn.gelu(self.conv2(x))
        assert x.shape[1] <= self._positional_embedding.shape[0], "audio longer than 30 s"
        x = x + self._positional_embedding[: x.shape[1]]
        for block in self.blocks:
            x, _, _ = block(x)
        return self.ln_post(x)

    AudioEncoder.__call__ = call
    _patched = True
