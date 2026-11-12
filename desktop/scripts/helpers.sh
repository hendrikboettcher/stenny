#!/bin/zsh
# Builds the Swift helpers the app bundles (app/Package.swift) and puts them where Tauri's bundler expects
# them (src-tauri/binaries/<name>-<target triple>): stenny-agent (pause shortcut, calendar, open at login,
# PDF for Share…) and stenny-rec (the microphone recorder `stenny live` uses); and Stenny.mcpb, the connector.
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
# the connector for Claude Desktop (connector/build.sh), bundled so Settings ▸ Use with Claude can always install it
../connector/build.sh >/dev/null
mkdir -p ../desktop/src-tauri/resources
cp ../dist/Stenny.mcpb ../desktop/src-tauri/resources/Stenny.mcpb
echo "✅ helpers in desktop/src-tauri/binaries ($TRIPLE), the connector in desktop/src-tauri/resources"
