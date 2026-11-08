"""Compare the pipeline's chunked transcription (as `stenny add` runs it) against a reference text.

Usage: uv run python bench/chunked_wer.py audio.wav reference.txt [--lang fr] [--prompt "terms…"] [--engine mlx]
faster-whisper on a Mac: uv run --with faster-whisper python bench/chunked_wer.py … --engine faster-whisper
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_bench import normalize, trim_to_reference, wer  # noqa: E402

from stenny import audio as audio_io  # noqa: E402
from stenny.config import Course  # noqa: E402
from stenny.engines import get_engine  # noqa: E402
from stenny.pipeline import transcribe  # noqa: E402
from stenny.textproc import mark_dropped, to_paragraphs  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("audio", type=Path)
ap.add_argument("reference", type=Path)
ap.add_argument("--lang", default="fr")
ap.add_argument("--prompt")
ap.add_argument("--engine", default="mlx", help="mlx | faster-whisper (stenny.engines)")
args = ap.parse_args()

audio = audio_io.load(args.audio)
t0 = time.time()
segments, _, _ = transcribe(audio, Course("bench", language=args.lang, languages=[args.lang]),
                            get_engine("mlx-community/whisper-large-v3-turbo", args.engine), args.prompt)
wall = time.time() - t0
text = " ".join(p["text"] for p in to_paragraphs(mark_dropped(segments, args.prompt)))
args.audio.with_suffix(f".{args.engine}.chunked.out").write_text(text)
ref = normalize(args.reference.read_text())
print(json.dumps({"engine": f"{args.engine}-chunked", "clip": args.audio.name, "wall_s": round(wall, 1),
                  "x_realtime": round(len(audio) / 16000 / wall, 1),
                  "wer_pct": round(100 * wer(ref, trim_to_reference(ref, normalize(text))), 2)}))
