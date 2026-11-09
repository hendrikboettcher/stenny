#!/usr/bin/env bash
# Builds stenny-rec.exe, the microphone recorder on Windows (desktop/stenny-rec, WASAPI), and puts it where Tauri's
# bundler expects it: src-tauri/binaries/stenny-rec-<target triple>.exe. On Windows, in Git Bash
# (`bash scripts/stenny-rec.sh`); on a Mac, scripts/helpers.sh builds the Swift recorder instead.
set -euo pipefail
cd "$(dirname "$0")/../stenny-rec"
TRIPLE="$(rustc -vV | sed -n 's/^host: //p')"
if [[ "$TRIPLE" != *windows* ]]; then
    echo "stenny-rec.exe is built on Windows ($TRIPLE here); on a Mac, scripts/helpers.sh builds the Swift one" >&2
    exit 1
fi
cargo build --release --locked
mkdir -p ../src-tauri/binaries
cp target/release/stenny-rec.exe "../src-tauri/binaries/stenny-rec-$TRIPLE.exe"
echo "stenny-rec.exe in desktop/src-tauri/binaries ($TRIPLE)"
