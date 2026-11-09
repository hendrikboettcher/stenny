#!/usr/bin/env bash
# The ffmpeg and ffprobe that Stenny's Windows installer bundles, where Tauri's bundler expects them
# (src-tauri/binaries/ffmpeg-<target triple>.exe, ffprobe-…), and their licence notice (src-tauri/resources/
# ffmpeg-license.txt, installed next to Stenny.exe). The engine finds them there (src/stenny/paths.py, bundled()).
#
# The Mac builds its own small audio-only ffmpeg (app/build-ffmpeg.sh). For Windows this takes a pinned build from
# BtbN/FFmpeg-Builds instead: the "lgpl" variant (no GPL or nonfree parts; libopus included), FFmpeg 9.0.2 like the
# Mac's, from a month-end autobuild (BtbN keeps those for good), checked against its SHA-256. A new build means
# new RELEASE, BUILD and SHA256 below: `gh api repos/BtbN/FFmpeg-Builds/releases/tags/<release>` lists each
# file's digest.
#
# Runs in Git Bash (CI's Windows installer job) or on a Mac/Linux to prepare a Windows build. Downloads ~170 MB.
#   desktop/scripts/ffmpeg-windows.sh [target triple, default x86_64-pc-windows-msvc]
set -euo pipefail
cd "$(dirname "$0")/../src-tauri"

RELEASE=autobuild-2026-10-31-12-59
BUILD=ffmpeg-n9.0.2-39-g7adfbe40dc-win64-lgpl-9.0
SHA256=9d37c564e0d01c18cde184a079fa6ef04d7d2783ada41da81d04165cc8f1a650
COMMIT=7adfbe40dc  # FFmpeg's commit, from the build's name
TRIPLE="${1:-x86_64-pc-windows-msvc}"
URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/$RELEASE/$BUILD.zip"

mkdir -p binaries resources
STAMP=binaries/.ffmpeg-windows
if [[ -s "binaries/ffmpeg-$TRIPLE.exe" && -s "binaries/ffprobe-$TRIPLE.exe" && -s resources/ffmpeg-license.txt \
      && "$(cat "$STAMP" 2>/dev/null)" == "$BUILD" ]]; then
    echo "✅ Already there: $BUILD"
    exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
curl -fL --retry 3 -o "$WORK/ffmpeg.zip" "$URL"
SUM="$( (sha256sum "$WORK/ffmpeg.zip" 2>/dev/null || shasum -a 256 "$WORK/ffmpeg.zip") | cut -d' ' -f1)"
[[ "$SUM" == "$SHA256" ]] || { echo "❌ $BUILD.zip isn't the expected file (SHA-256 $SUM)."; exit 1; }

# unzip comes with Git for Windows; Python's zipfile otherwise
if command -v unzip >/dev/null; then
    unzip -q "$WORK/ffmpeg.zip" -x "*/ffplay.exe" -d "$WORK"
else
    "$(command -v python3 || command -v python)" -I -m zipfile -e "$WORK/ffmpeg.zip" "$WORK"
fi
SRC="$WORK/$BUILD"
[[ -s "$SRC/bin/ffmpeg.exe" && -s "$SRC/bin/ffprobe.exe" ]] || { echo "❌ No ffmpeg.exe/ffprobe.exe in $BUILD.zip."; exit 1; }

# On Windows itself: check it really is LGPL and can write Opus (Stenny's audio archive)
if [[ "${OS:-}" == "Windows_NT" ]]; then
    CONF="$("$SRC/bin/ffmpeg.exe" -hide_banner -buildconf)"
    if grep -qE -- "--enable-(gpl|nonfree)" <<<"$CONF"; then echo "❌ $BUILD has GPL or nonfree parts."; exit 1; fi
    "$SRC/bin/ffmpeg.exe" -hide_banner -encoders | grep -q libopus || { echo "❌ $BUILD can't write Opus."; exit 1; }
fi

cp "$SRC/bin/ffmpeg.exe" "binaries/ffmpeg-$TRIPLE.exe"
cp "$SRC/bin/ffprobe.exe" "binaries/ffprobe-$TRIPLE.exe"

# The LGPL's notice travels with the programs; the source stays reachable (and goes with each Windows release)
{
    echo "Stenny for Windows comes with ffmpeg.exe and ffprobe.exe from FFmpeg (https://ffmpeg.org), version"
    echo "${BUILD#ffmpeg-}, licensed under the GNU Lesser General Public License (LGPL) version 2.1 or later."
    echo "Stenny runs them as separate programs; it doesn't change them."
    echo
    echo "They were built by BtbN/FFmpeg-Builds, \"lgpl\" variant (no GPL or nonfree parts):"
    echo "  $URL"
    echo "Source code: FFmpeg at commit $COMMIT, https://github.com/FFmpeg/FFmpeg/tree/$COMMIT"
    echo "The libraries in the build and how it was made: https://github.com/BtbN/FFmpeg-Builds/tree/$RELEASE"
    echo
    if [[ -f "$SRC/LICENSE.txt" ]]; then cat "$SRC/LICENSE.txt"; fi
} > resources/ffmpeg-license.txt
echo "$BUILD" > "$STAMP"
echo "✅ ffmpeg and ffprobe for $TRIPLE in desktop/src-tauri/binaries ($BUILD)"
