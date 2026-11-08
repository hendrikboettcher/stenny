"""The Whisper engines behind one interface: which one runs where, and faster-whisper's answers in the shape the
pipeline reads (without the real library or a model download)."""
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from stenny import engines
from stenny.engines import faster_engine
from stenny.engines.faster_engine import FasterWhisperEngine, model_name


def test_auto_is_mlx_on_a_mac_and_faster_whisper_elsewhere(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert engines.get_engine("m").name == "faster-whisper"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert engines.get_engine("m").name == "mlx-whisper"
    assert engines.get_engine("m", "faster-whisper").name == "faster-whisper"
    with pytest.raises(ValueError):
        engines.get_engine("m", "whisper.cpp")


@pytest.mark.parametrize("configured,expected", [
    ("mlx-community/whisper-large-v3-turbo", "large-v3-turbo"),
    ("mlx-community/whisper-small-mlx", "small"),
    ("large-v3", "large-v3"),
    ("deepdml/faster-whisper-large-v3-turbo-ct2", "deepdml/faster-whisper-large-v3-turbo-ct2"),
])
def test_the_configured_model_as_faster_whisper_knows_it(configured, expected):
    assert model_name(configured) == expected


class FakeWhisper:
    """faster_whisper.WhisperModel: segments come lazily, the language info at once."""
    made = []

    def __init__(self, model, device, compute_type, download_root=None):
        self.calls = []
        FakeWhisper.made.append((model, device, compute_type))
        FakeWhisper.root = download_root

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        segs = (SimpleNamespace(id=1, start=0.0, end=2.5, text=" Bonjour.", avg_logprob=-0.2, no_speech_prob=0.01,
                                compression_ratio=1.1, tokens=[1, 2]) for _ in range(1))
        info = SimpleNamespace(language="fr", all_language_probs=[("fr", 0.6), ("en", 0.3), ("de", 0.1)])
        return segs, info


@pytest.fixture
def fake(monkeypatch):
    FakeWhisper.made = []
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeWhisper))
    monkeypatch.setattr(faster_engine, "_cuda", lambda: False)
    return FakeWhisper


def test_segments_have_the_fields_the_pipeline_reads(fake):
    engine = FasterWhisperEngine("mlx-community/whisper-large-v3-turbo")
    segments = engine.transcribe(np.zeros(16000, np.float32), "fr", prompt="Glossar: Ableitung")
    assert segments == [{"start": 0.0, "end": 2.5, "text": " Bonjour.", "no_speech_prob": 0.01, "avg_logprob": -0.2,
                         "compression_ratio": 1.1, "language": "fr"}]
    assert fake.made == [("large-v3-turbo", "cpu", "int8")]
    kw = engine._whisper.calls[0]
    # the same decoding as mlx: greedy with temperature fallback, the prompt, no conditioning, no VAD
    assert kw["beam_size"] == 1 and kw["initial_prompt"] == "Glossar: Ableitung"
    assert kw["condition_on_previous_text"] is False and kw["vad_filter"] is False and kw["temperature"][0] == 0.0


def test_language_probs_only_among_the_allowed(fake):
    probs = FasterWhisperEngine("large-v3-turbo").language_probs(np.zeros(16000, np.float32), ["fr", "de"])
    assert probs == pytest.approx({"fr": 0.6 / 0.7, "de": 0.1 / 0.7})


def test_a_gpu_without_its_libraries_falls_back_to_the_cpu(fake, monkeypatch):
    def picky(model, device, compute_type, **kw):
        if device == "cuda":
            raise RuntimeError("Library cublas64_12.dll is not found")
        return FakeWhisper(model, device, compute_type, **kw)
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=picky))
    monkeypatch.setattr(faster_engine, "_cuda", lambda: True)
    engine = FasterWhisperEngine("large-v3-turbo")
    engine.transcribe(np.zeros(16000, np.float32), "en")
    assert fake.made[-1] == ("large-v3-turbo", "cpu", "int8")
    engine.release()
    assert engine._whisper is None


def test_the_engine_setting(tmp_path):
    from stenny import config as config_mod
    path = tmp_path / "config.toml"
    path.write_text(f'vault = "{tmp_path.as_posix()}"\n[transcription]\nengine = "faster-whisper"\n', encoding="utf-8")
    assert config_mod.load(path).engine == "faster-whisper"
    path.write_text(f'vault = "{tmp_path.as_posix()}"\n', encoding="utf-8")
    assert config_mod.load(path).engine == "auto"
    path.write_text(f'vault = "{tmp_path.as_posix()}"\n[transcription]\nengine = "cpu"\n', encoding="utf-8")
    with pytest.raises(config_mod.ConfigError):
        config_mod.load(path)


def test_the_model_goes_to_stennys_models_folder(fake, monkeypatch, tmp_path):
    from stenny import paths
    monkeypatch.setattr(paths, "models_dir", lambda: tmp_path / "Models")
    FasterWhisperEngine("large-v3-turbo").transcribe(np.zeros(16000, np.float32), "en")
    assert fake.root == str(tmp_path / "Models")
    monkeypatch.setattr(paths, "models_dir", lambda: None)  # a Mac: the Hugging Face cache
    FasterWhisperEngine("large-v3-turbo").transcribe(np.zeros(16000, np.float32), "en")
    assert fake.root is None
