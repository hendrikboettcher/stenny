"""faster-whisper (CTranslate2): Stenny's engine on Windows. An NVIDIA GPU when CUDA is there, else the CPU.

Decoding matches mlx_engine.py (greedy with temperature fallback, no conditioning on the previous text), so a
lecture comes out the same on both; only the arithmetic differs (int8 on the CPU, float16 on a GPU)."""

import gc

import numpy as np

from .base import SEGMENT_KEYS, normalize, restrict

TEMPERATURES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


def model_name(model: str) -> str:
    """The configured model as faster-whisper knows it: "mlx-community/whisper-large-v3-turbo" (the config's
    default, an mlx repo) → "large-v3-turbo"; anything else (a size, a CTranslate2 repo, a folder) as it is."""
    if model.startswith("mlx-community/whisper-"):
        name = model.removeprefix("mlx-community/whisper-")
        return name.removesuffix("-mlx")
    return model


class FasterWhisperEngine:
    name = "faster-whisper"

    def __init__(self, model: str, device: str = "auto"):
        self.model = model_name(model)
        self.device = device
        self._whisper = None

    def _load(self):
        if self._whisper is None:
            from faster_whisper import WhisperModel

            from .. import paths

            root = paths.models_dir()  # %LOCALAPPDATA%\Stenny\Models on Windows; None: the Hugging Face cache
            kw = {"download_root": str(root)} if root else {}

            if self.device in ("auto", "cuda") and _cuda():
                try:
                    self._whisper = WhisperModel(self.model, device="cuda", compute_type="float16", **kw)
                    return self._whisper
                except (RuntimeError, ValueError, OSError):
                    if self.device == "cuda":
                        raise
                    # CUDA is there but its libraries (cuBLAS, cuDNN) aren't: the CPU does it
            self._whisper = WhisperModel(self.model, device="cpu", compute_type="int8", **kw)
        return self._whisper

    def transcribe(self, audio: np.ndarray, language: str, prompt: str | None = None) -> list[dict]:
        segments, _ = self._load().transcribe(
            normalize(audio),
            language=language,
            task="transcribe",
            beam_size=1,
            initial_prompt=prompt,
            condition_on_previous_text=False,  # prevents hallucination loops in long recordings
            temperature=list(TEMPERATURES),
            vad_filter=False,  # gaps and silence are textproc's job, as with mlx
        )
        return [{k: getattr(s, k) for k in SEGMENT_KEYS if hasattr(s, k)} | {"language": language} for s in segments]

    def release(self) -> None:
        """Hand the model's memory back; the next transcription loads it again."""
        self._whisper = None
        gc.collect()

    def language_probs(self, clip: np.ndarray, allowed: list[str]) -> dict[str, float]:
        # transcribe() detects the language before it returns; its segments are only decoded when read
        _, info = self._load().transcribe(normalize(clip), task="transcribe", beam_size=1)
        return restrict(dict(info.all_language_probs or []), allowed)


def download(model: str) -> str:
    """Fetches the model into the folder _load() reads it from (`stenny download-model`), and returns where it is."""
    from faster_whisper.utils import download_model

    from .. import paths

    root = paths.models_dir()
    return download_model(model_name(model), cache_dir=str(root) if root else None)


def _cuda() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except (ImportError, RuntimeError, OSError):
        return False
