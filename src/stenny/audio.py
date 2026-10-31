"""Audio I/O via ffmpeg: decode to 16 kHz mono float32, read recording time, archive as Opus."""

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np

from .paths import tool
from .textproc import SAMPLE_RATE

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".mp4", ".mov", ".ogg", ".opus", ".flac", ".webm", ".qta", ".aiff"}


RAW_INPUT = ["-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1"]  # live recordings: headerless 16 kHz PCM (.pcm)


def input_args(path: Path) -> list[str]:
    return [*RAW_INPUT, "-i", str(path)] if path.suffix == ".pcm" else ["-i", str(path)]


def load(path: Path) -> np.ndarray:
    cmd = [tool("ffmpeg"), "-nostdin", "-loglevel", "error", *input_args(path), "-f", "s16le", "-ac", "1",
           "-ar", str(SAMPLE_RATE), "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg could not read {path}: {p.stderr.decode()[-500:]}")
    return np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768.0


def recorded_at(path: Path) -> datetime:
    """Recording start: container creation_time (Voice Memos, most recorders), else file birth time."""
    p = subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries", "format_tags=creation_time", "-of", "json",
                        str(path)], capture_output=True, text=True)
    try:
        stamp = json.loads(p.stdout)["format"]["tags"]["creation_time"]
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except (KeyError, ValueError, json.JSONDecodeError):
        st = path.stat()
        return datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_mtime))


# encoders that name a codec library or converter, not the device/app that recorded
CONVERTERS = re.compile(r"^(lav[fc]|lame|lib|ffmpeg|itunes|handbrake|audacity)", re.I)


def device_from_tags(tags: dict) -> str | None:
    """Recording device/app from container tags, e.g. "iPhone 15 Pro (Voice Memos)", "Voice Memos (iPhone)"."""
    tags = {k.lower(): str(v).strip() for k, v in tags.items() if str(v).strip()}
    make = tags.get("com.apple.quicktime.make") or tags.get("make") or ""
    model = tags.get("com.apple.quicktime.model") or tags.get("model") or tags.get("device") or ""
    if make and model and not model.lower().startswith(make.lower()) and make.lower() != "apple":
        model = f"{make} {model}"
    encoder = tags.get("encoder", "")
    if m := re.match(r"com\.apple\.VoiceMemos\s*\((\w[\w ]*?)\s+Version", encoder):
        return f"{model} (Voice Memos)" if model else f"Voice Memos ({m[1]})"
    app = tags.get("com.apple.quicktime.software") or tags.get("software") or ""
    if not app and encoder and not CONVERTERS.match(encoder):
        app = encoder
    if model and app and not app[0].isdigit():  # "software" on iPhones is just the iOS version
        return f"{model} ({app})"
    return model or (app if app and not app[0].isdigit() else None)


def recording_device(path: Path) -> str | None:
    """Device/app that recorded `path`, from its metadata (None when the file doesn't say)."""
    p = subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries", "format_tags:stream_tags", "-of", "json",
                        str(path)], capture_output=True, text=True)
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return None
    tags = dict(data.get("format", {}).get("tags", {}))
    for stream in data.get("streams", []):
        for k, v in stream.get("tags", {}).items():
            tags.setdefault(k, v)
    return device_from_tags(tags)


def archive(src: Path, dest: Path, bitrate: str = "32k") -> Path:
    """Store a compact copy (Opus, mono) outside the vault. Opus at 32 kbit/s keeps speech
    intelligible for re-transcription at ~14 MB per hour."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and src.resolve() == dest.resolve():
        return dest  # re-processing the archived file itself
    if src.suffix.lower() == ".opus":
        shutil.copy2(src, dest)
        return dest
    tmp = dest.with_name(f".{dest.name}.tmp.opus")
    subprocess.run([tool("ffmpeg"), "-nostdin", "-loglevel", "error", "-y", *input_args(src), "-ac", "1",
                    "-c:a", "libopus", "-b:a", bitrate, "-application", "voip", str(tmp)], check=True)
    tmp.replace(dest)
    return dest
