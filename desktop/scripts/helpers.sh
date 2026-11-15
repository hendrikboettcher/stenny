#!/bin/zsh
# Builds the Swift helpers the app bundles (app/Package.swift) and puts them where Tauri's bundler expects
# them: stenny-rec (the microphone recorder `stenny live` uses) as src-tauri/binaries/<name>-<target triple>, and
# stenny-agent (pause shortcut, calendar, PDF for Share…) as a small app of its own, src-tauri/binaries/Stenny
# Agent.app, which goes into Stenny.app/Contents/Helpers (tauri.macos.conf.json); and Stenny.mcpb, the connector.
#
# Why its own app: a program in Contents/MacOS reads Stenny.app's Info.plist, which has no LSUIElement (so the Dock
# bounces Stenny while it opens), and an AppKit program there shows up in the Dock as a second Stenny (#52). Its own
# Info.plist (LSUIElement, its own bundle id) keeps it out. The app starts it as its child, so macOS still counts
# its calendar access as Stenny's.
set -euo pipefail
cd "$(dirname "$0")/../../app"
swift build -c release --product stenny-agent
swift build -c release --product stenny-rec
BIN="$(swift build -c release --show-bin-path)"
TRIPLE="$(rustc -vV 2>/dev/null | sed -n 's/^host: //p')"
TRIPLE="${TRIPLE:-aarch64-apple-darwin}"
OUT=../desktop/src-tauri/binaries
mkdir -p "$OUT"
cp "$BIN/stenny-rec" "$OUT/stenny-rec-$TRIPLE"
rm -f "$OUT/stenny-agent-$TRIPLE"   # where it was before it had its own app
AGENT="$OUT/Stenny Agent.app"
VERSION="$(sed -n 's/^ *"version": *"\([^"]*\)".*/\1/p' ../desktop/src-tauri/tauri.conf.json | head -1)"
ID="$(sed -n 's/^ *"identifier": *"\([^"]*\)".*/\1/p' ../desktop/src-tauri/tauri.conf.json | head -1)"
rm -rf "$AGENT"
mkdir -p "$AGENT/Contents/MacOS"
cp "$BIN/stenny-agent" "$AGENT/Contents/MacOS/stenny-agent"
cat > "$AGENT/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>$ID.agent</string>
  <key>CFBundleName</key><string>Stenny Agent</string>
  <key>CFBundleExecutable</key><string>stenny-agent</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>LSMinimumSystemVersion</key><string>14.4</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST
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
