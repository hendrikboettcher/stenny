#!/bin/zsh
# Builds the Swift helpers the app bundles (app/Package.swift) and puts them where Tauri's bundler expects
# them (src-tauri/binaries/<name>-<target triple>): stenny-agent (pause shortcut, calendar, lecture
# notifications) and stenny-rec (the microphone recorder `stenny live` uses).
set -euo pipefail
cd "$(dirname "$0")/../../app"
swift build -c release --product stenny-agent
swift build -c release --product stenny-rec
BIN="$(swift build -c release --show-bin-path)"
TRIPLE="$(rustc -vV 2>/dev/null | sed -n 's/^host: //p')"
TRIPLE="${TRIPLE:-aarch64-apple-darwin}"
OUT=../desktop/src-tauri/binaries
mkdir -p "$OUT"
for helper in stenny-agent stenny-rec; do
    cp "$BIN/$helper" "$OUT/$helper-$TRIPLE"
done
# the LGPL ffmpeg/ffprobe (app/build-ffmpeg.sh), so Stenny needs no Homebrew; from an earlier Stenny.app if not built
for tool in ffmpeg ffprobe; do
    for from in build.noindex/tools/$tool /Applications/Stenny.app/Contents/MacOS/$tool; do
        [[ -x "$from" ]] && { cp "$from" "$OUT/$tool-$TRIPLE"; break; }
    done
done
echo "✅ helpers in desktop/src-tauri/binaries ($TRIPLE)"
