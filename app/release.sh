#!/bin/zsh
# Makes dist/Stenny.zip for a GitHub release: the committed code plus the finished app, signed with
# "Stenny Dev" so permissions survive updates. Refuses anything that wouldn't match what's on GitHub.
#   app/release.sh              build the zip
#   app/release.sh --publish    also tag the version and create the GitHub release
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)"
TAG="v$VERSION"
[[ -z "$(git status --porcelain)" ]] || { echo "❌ Commit your changes first."; exit 1; }
[[ -z "$(git tag -l "$TAG")" ]] || { echo "❌ $TAG already exists: raise the version in pyproject.toml."; exit 1; }
security find-identity -p codesigning | grep -q '"Stenny Dev"' \
    || { echo "❌ No \"Stenny Dev\" certificate: run app/make-cert.sh, and keep it for every release."; exit 1; }

app/build-ffmpeg.sh
STENNY_SIGN_IDENTITY="Stenny Dev" app/build.sh --no-install

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
# ".noindex": it stays out of Spotlight, or searching for Stenny lists every folder in the code
TOP=stenny.noindex
git archive --format=tar --prefix="$TOP/" HEAD | tar -x -C "$WORK"
mkdir -p "$WORK/$TOP/app/build.noindex"
/usr/bin/ditto app/build.noindex/Stenny.app "$WORK/$TOP/app/build.noindex/Stenny.app"
codesign --verify --deep --strict "$WORK/$TOP/app/build.noindex/Stenny.app"

# Checks the shipped audio tools can do Stenny's job, and that they use nothing outside macOS
MACOS="$WORK/$TOP/app/build.noindex/Stenny.app/Contents/MacOS"
if otool -L "$MACOS/ffmpeg" "$MACOS/ffprobe" | grep -E '^\s' | grep -vE '/usr/lib/|/System/Library/'; then
    echo "❌ ffmpeg depends on a library that other Macs don't have."
    exit 1
fi
"$MACOS/ffmpeg" -hide_banner -encoders 2>/dev/null | grep -q libopus || { echo "❌ ffmpeg can't write Opus."; exit 1; }

mkdir -p dist
rm -f dist/Stenny.zip
/usr/bin/ditto -c -k --norsrc --noextattr --keepParent "$WORK/$TOP" dist/Stenny.zip  # no ._ files
echo "✅ dist/Stenny.zip ($(du -h dist/Stenny.zip | cut -f1)) for $TAG"

if [[ "${1:-}" == "--publish" ]]; then
    git push origin HEAD
    git tag "$TAG"
    git push origin "$TAG"
    # The LGPL asks that ffmpeg's source stays available next to the binary
    gh release create "$TAG" dist/Stenny.zip app/build.noindex/tools-src/ffmpeg-*.tar.xz \
        --title "Stenny ${VERSION}" --generate-notes
    echo "✅ Released $TAG"
fi
