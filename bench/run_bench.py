"""Benchmark local Whisper engines on Apple Silicon: speed, memory, CPU and word error rate.

Each engine runs as a separate process under `/usr/bin/time -l`, so peak memory and CPU
time are measured by the OS. Engines live in their own environments:

  mlx          – mlx-whisper in this project's venv (Metal GPU)
  whispercpp   – Homebrew `whisper-cli` (Metal GPU), needs a ggml model file
  faster       – faster-whisper (CTranslate2, CPU only on macOS), needs a separate venv

Usage:
  uv run python bench/run_bench.py --data DIR --ggml PATH --fw-python PATH \
      --clip lecture_fr.wav:lecture_fr.txt --clip parure.wav:parure_ref.txt --engines mlx,whispercpp,faster
"""

import argparse
import difflib
import json
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

MLX_SCRIPT = """
import sys, mlx_whisper
wav, lang, out = sys.argv[1:4]
r = mlx_whisper.transcribe(wav, path_or_hf_repo="mlx-community/whisper-large-v3-turbo", language=lang,
                           condition_on_previous_text=False, verbose=None)
open(out, "w").write(" ".join(s["text"].strip() for s in r["segments"]))
"""

FASTER_SCRIPT = """
import sys, wave
import numpy as np
from faster_whisper import WhisperModel
wav, lang, out = sys.argv[1:4]
with wave.open(wav) as w:  # 16 kHz mono PCM; avoids faster-whisper's PyAV decoder
    audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
segments, _ = model.transcribe(audio, language=lang, beam_size=1, condition_on_previous_text=False)
open(out, "w").write(" ".join(s.text.strip() for s in segments))
"""


def normalize(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text.lower()).replace("’", "'").replace("œ", "oe")
    text = re.sub(r"\[.*?\]", " ", text)
    text = re.sub(r"[^\w']+", " ", text)
    return text.replace("'", "' ").split()


def wer(ref: list[str], hyp: list[str]) -> float:
    """Word error rate via Levenshtein distance on words."""
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / max(len(ref), 1)


def trim_to_reference(ref: list[str], hyp: list[str]) -> list[str]:
    """Drop audiobook intro/outro (e.g. the LibriVox disclaimer) that is not in the reference text."""
    blocks = [b for b in difflib.SequenceMatcher(None, ref, hyp, autojunk=False).get_matching_blocks() if b.size >= 3]
    if not blocks:
        return hyp
    first, last = blocks[0], blocks[-1]
    start = max(0, first.b - first.a)  # keep hyp words that correspond to unmatched ref words at the start
    end = last.b + last.size + (len(ref) - last.a - last.size)
    return hyp[start:end]


def timed(cmd: list[str], prefix: list[str]) -> dict:
    t0 = time.time()
    p = subprocess.run(["/usr/bin/time", "-l", *prefix, *cmd], capture_output=True, text=True)
    wall = time.time() - t0
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-2000:])
    err = p.stderr
    num = lambda pat: float(m.group(1)) if (m := re.search(pat, err)) else 0.0
    user, sys_ = num(r"([\d.]+) user"), num(r"([\d.]+) sys")
    return {
        "wall_s": wall,
        "cpu_pct": 100 * (user + sys_) / wall,
        "peak_mem_gb": num(r"(\d+)\s+peak memory footprint") / 1e9,
    }


def run_engine(engine: str, wav: Path, lang: str, out: Path, args, prefix: list[str]) -> dict:
    if engine == "mlx":
        cmd = [sys.executable, "-c", MLX_SCRIPT, str(wav), lang, str(out)]
    elif engine == "faster":
        cmd = [args.fw_python, "-I", "-c", FASTER_SCRIPT, str(wav), lang, str(out)]
    elif engine == "whispercpp":
        cmd = ["whisper-cli", "-m", args.ggml, "-l", lang, "-f", str(wav), "-otxt", "-of", str(out.with_suffix("")), "-np"]
    else:
        raise ValueError(engine)
    stats = timed(cmd, prefix)
    if engine == "whispercpp":
        out.write_text(out.with_suffix(".txt").read_text().replace("\n", " "))
    return stats


def duration(wav: Path) -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(wav)],
                       capture_output=True, text=True, check=True)
    return float(p.stdout)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--clip", action="append", required=True, help="audio.wav[:reference.txt]")
    ap.add_argument("--engines", default="mlx,whispercpp,faster")
    ap.add_argument("--lang", default="fr")
    ap.add_argument("--ggml", help="ggml model for whisper.cpp")
    ap.add_argument("--fw-python", help="python of the faster-whisper venv")
    ap.add_argument("--priority", default="normal", choices=["normal", "nice", "background"])
    ap.add_argument("--out", type=Path, default=Path("bench_results.jsonl"))
    args = ap.parse_args()

    prefix = {"normal": [], "nice": ["nice", "-n", "10", "taskpolicy", "-c", "utility"],
              "background": ["taskpolicy", "-b"]}[args.priority]
    power = subprocess.run(["pmset", "-g", "ps"], capture_output=True, text=True).stdout.split("'")[1]

    for clip in args.clip:
        wav_name, _, ref_name = clip.partition(":")
        wav = args.data / wav_name
        dur = duration(wav)
        ref = normalize((args.data / ref_name).read_text()) if ref_name else None
        for engine in args.engines.split(","):
            out = args.data / f"hyp_{engine}_{wav.stem}_{args.priority}.out"
            stats = run_engine(engine, wav, args.lang, out, args, prefix)
            row = {"engine": engine, "clip": wav.name, "audio_s": round(dur, 1), "priority": args.priority,
                   "power": power, **{k: round(v, 2) for k, v in stats.items()},
                   "x_realtime": round(dur / stats["wall_s"], 1)}
            if ref:
                hyp = normalize(out.read_text())
                row["wer_pct"] = round(100 * wer(ref, trim_to_reference(ref, hyp)), 2)
            print(json.dumps(row), flush=True)
            with args.out.open("a") as f:
                f.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
