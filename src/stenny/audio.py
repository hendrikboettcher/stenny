"""Audio I/O via ffmpeg: decode to 16 kHz mono float32, read recording time, archive as Opus."""

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .paths import tool
from .textproc import SAMPLE_RATE

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".mp4", ".mov", ".m4v", ".mkv", ".ogg", ".opus", ".flac", ".webm",
              ".qta", ".aiff"}
# Stenny's own stored audio (`Recordings/<Course>/2026-10-20 (2).opus`): the lecture's date is in its name
OWN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})(?: \(\d+\))?(?:\.backup)?\.opus", re.I)


RAW_INPUT = ["-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1"]  # live recordings: headerless 16 kHz PCM (.pcm)


def input_args(path: Path) -> list[str]:
    return [*RAW_INPUT, "-i", str(path)] if path.suffix == ".pcm" else ["-i", str(path)]


def pcm_seconds(path: Path) -> float:
    """How long a live recording (.pcm) is, from its size: no need to read it."""
    try:
        return path.stat().st_size // 2 / SAMPLE_RATE
    except OSError:
        return 0.0


def load(path: Path) -> np.ndarray:
    if path.suffix == ".pcm":  # already what Whisper takes: read as it is, no ffmpeg (a 3-hour lecture is 330 MB)
        return np.fromfile(path, dtype="<i2", count=path.stat().st_size // 2).astype(np.float32) / 32768.0
    cmd = [tool("ffmpeg"), "-nostdin", "-loglevel", "error", *input_args(path), "-f", "s16le", "-ac", "1",
           "-ar", str(SAMPLE_RATE), "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg could not read {path}: {p.stderr.decode(errors="replace")[-500:]}")
    return np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768.0


def _tags(path: Path) -> dict:
    """The container's tags and its streams' (Ogg keeps them on the stream), the container's first."""
    p = subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries", "format_tags:stream_tags", "-of", "json",
                        str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return {}
    tags = dict(data.get("format", {}).get("tags", {}))
    for stream in data.get("streams", []):
        for k, v in stream.get("tags", {}).items():
            tags.setdefault(k, v)
    return tags


def recorded_at(path: Path) -> datetime:
    """Recording start: the file's creation_time (Voice Memos, most recorders, and Stenny's own stored audio since
    archive() writes it), else for Stenny's own audio the date in its name ("2026-10-20.opus", a copy someone sent:
    the file itself is from the day it arrived), else the file's birth time."""
    stamp = next((v for k, v in _tags(path).items() if k.lower() == "creation_time"), None)
    if stamp:
        try:
            return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        except ValueError:
            pass
    st = path.stat()
    born = datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_mtime))
    if m := OWN_NAME.fullmatch(path.name):
        day = datetime.strptime(m[1], "%Y-%m-%d")
        # made here that day: its clock time is the recording's end, close enough; otherwise the time isn't known
        return born if born.date() == day.date() else day
    return born


def probe(path: Path) -> dict | None:
    """What a file is, without reading it all: {"seconds": its length, "audio": whether it has sound}; None for
    anything ffmpeg can't read (a document, a picture)."""
    p = subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of",
                        "json", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return None
    if p.returncode or not data.get("format"):
        return None
    try:
        seconds = float(data["format"].get("duration") or 0)
    except ValueError:
        seconds = 0.0
    return {"seconds": seconds, "audio": any(s.get("codec_type") == "audio" for s in data.get("streams", []))}


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
    return device_from_tags(_tags(path))


def _codec(path: Path) -> str | None:
    """The first audio stream's codec ("opus", "aac" …)."""
    p = subprocess.run([tool("ffprobe"), "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.stdout.strip() or None


def _stamp(when: datetime) -> str:
    """A local clock time as the UTC creation_time containers carry ("2026-10-20T08:15:00.000000Z")."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def archive(src: Path, dest: Path, bitrate: str = "32k", when: datetime | None = None) -> Path:
    """Store a compact copy (Opus, mono) outside the vault. Opus at 32 kbit/s keeps speech
    intelligible for re-transcription at ~14 MB per hour. `when`: the recording's start, written into the copy
    (creation_time), so a copy someone is sent still knows its lecture's date. An Opus file is kept as it is (only
    its container written anew when `when` is given), never encoded again."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and src.resolve() == dest.resolve():
        return dest  # re-processing the archived file itself
    stamp = ["-metadata:s:a:0", f"creation_time={_stamp(when)}"] if when else []
    tmp = dest.with_name(f".{dest.name}.tmp.opus")
    if src.suffix.lower() == ".opus" or (src.suffix.lower() in (".ogg", ".oga") and _codec(src) == "opus"):
        if not stamp and src.suffix.lower() == ".opus":
            shutil.copy2(src, dest)
            return dest
        done = subprocess.run([tool("ffmpeg"), "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:a",
                               "-c", "copy", *stamp, str(tmp)])
        if done.returncode:  # an Opus file ffmpeg can't copy: kept exactly as it is
            tmp.unlink(missing_ok=True)
            shutil.copy2(src, dest)
            return dest
    else:
        subprocess.run([tool("ffmpeg"), "-nostdin", "-loglevel", "error", "-y", *input_args(src), "-vn", "-ac", "1",
                        "-c:a", "libopus", "-b:a", bitrate, "-application", "voip", *stamp, str(tmp)], check=True)
    tmp.replace(dest)
    return dest
