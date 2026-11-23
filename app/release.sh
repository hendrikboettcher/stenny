#!/bin/zsh
# Makes dist/Stenny.zip for a GitHub release: the committed code plus the finished app (desktop/, the new app), signed
# with "Stenny Dev" so permissions survive updates. Refuses anything that wouldn't match what's on GitHub.
#   app/release.sh              build the zip
#   app/release.sh --publish    also tag the version and create the GitHub release, with the update for Stenny's
#                               updater (Stenny.app.tar.gz, latest.json; needs the updater key) and the installers
#                               scripts/install.sh and scripts/install.ps1 (README: Install), and their uninstall twins. CI adds the Windows setup
#                               program to the release once it has built it (ci.yml, Installer).
# The app goes to app/build.noindex/Stenny.app in the zip, where scripts/install.sh and an older `stenny update` look.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)"
TAG="v$VERSION"
[[ -z "$(git status --porcelain)" ]] || { echo "❌ Commit your changes first."; exit 1; }
[[ -z "$(git tag -l "$TAG")" ]] || { echo "❌ $TAG already exists: raise the version in pyproject.toml."; exit 1; }
APP_VERSION="$(sed -n 's/^  "version": "\(.*\)",$/\1/p' desktop/src-tauri/tauri.conf.json)"
[[ "$APP_VERSION" == "$VERSION" ]] \
    || { echo "❌ The app says $APP_VERSION, pyproject.toml $VERSION: give both the same version."; exit 1; }
security find-identity -p codesigning | grep -q '"Stenny Dev"' \
    || { echo "❌ No \"Stenny Dev\" certificate: run app/make-cert.sh, and keep it for every release."; exit 1; }
# The updater's key signs the update (Check for Updates…, desktop/src-tauri/src/updater.rs): $TAURI_SIGNING_PRIVATE_KEY
# (+ _PASSWORD), or the files `tauri signer generate` made, ~/.tauri/stenny-updater.key (+ .password). The same key is
# CI's secret for Windows. Without it no installed Stenny could ever check an update: keep a copy somewhere safe.
UPDATER_KEY="${STENNY_UPDATER_KEY:-$HOME/.tauri/stenny-updater.key}"
if [[ -z "${TAURI_SIGNING_PRIVATE_KEY:-}" ]]; then
    [[ -s "$UPDATER_KEY" ]] || { echo "❌ No updater key ($UPDATER_KEY or \$TAURI_SIGNING_PRIVATE_KEY): copy it here from where it's kept."; exit 1; }
    export TAURI_SIGNING_PRIVATE_KEY="$(cat "$UPDATER_KEY")"
    [[ -n "${TAURI_SIGNING_PRIVATE_KEY_PASSWORD:-}" || ! -s "$UPDATER_KEY.password" ]] \
        || export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="$(cat "$UPDATER_KEY.password")"
fi

app/build-ffmpeg.sh                 # app/build.noindex/tools: helpers.sh puts them into the app
desktop/scripts/helpers.sh          # stenny-agent, stenny-rec, ffmpeg/ffprobe, the connector
desktop/scripts/bundle-engine.sh    # the engine inside the app: a standalone Python with Stenny and its packages
(cd desktop && npm ci && npm run tauri build -- --bundles app)
BUILT=desktop/src-tauri/target.noindex/release/bundle/macos/Stenny.app
desktop/scripts/sign-mac.sh "$BUILT" "Stenny Dev"   # the engine's native libraries too, then the app

# The update for Stenny's updater: the signed app as Stenny.app.tar.gz (no ._ files), its signature with the updater's
# key, and latest.json naming both (CI adds Windows' setup program to it, ci.yml). Made from the app as signed above,
# so an updated Mac keeps its microphone and calendar permissions.
mkdir -p dist
rm -f dist/Stenny.app.tar.gz dist/Stenny.app.tar.gz.sig dist/latest.json
COPYFILE_DISABLE=1 tar --no-mac-metadata -czf dist/Stenny.app.tar.gz -C "$(dirname "$BUILT")" Stenny.app
(cd desktop && npx tauri signer sign --app-version "$VERSION" ../dist/Stenny.app.tar.gz >/dev/null)  # version signed too
python3 -I desktop/scripts/latest-json.py dist/latest.json --version "$VERSION" --platform darwin-aarch64 \
    --url "https://github.com/hendrikboettcher/stenny/releases/download/$TAG/Stenny.app.tar.gz" \
    --signature-file dist/Stenny.app.tar.gz.sig \
    --notes "https://github.com/hendrikboettcher/stenny/releases/tag/$TAG"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
# ".noindex": it stays out of Spotlight, or searching for Stenny lists every folder in the code
TOP=stenny.noindex
git archive --format=tar --prefix="$TOP/" HEAD | tar -x -C "$WORK"
mkdir -p "$WORK/$TOP/app/build.noindex"
/usr/bin/ditto "$BUILT" "$WORK/$TOP/app/build.noindex/Stenny.app"
codesign --verify --deep --strict "$WORK/$TOP/app/build.noindex/Stenny.app"

# The connector for Claude Desktop, at the top of the folder: the app's Connect button opens it
connector/build.sh
cp dist/Stenny.mcpb "$WORK/$TOP/Stenny.mcpb"

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
    # How to update comes first: Stenny's Check for Updates… led here before it could update by itself (up to
    # 0.5.7), and the files below can't simply be downloaded (the engine and the app must be updated together, and a
    # browser download is blocked). gh puts these notes before the generated list of changes.
    repo_url="https://github.com/hendrikboettcher/stenny/releases/latest/download"
    notes="$(cat <<NOTES
**Update or install:** in Stenny, *Check for Updates…* ▸ *Update Now*, or run the line that installed it again:

- **Mac**, in Terminal: \`curl -fsSL $repo_url/install.sh | zsh\`
- **Windows**, in PowerShell: \`irm $repo_url/install.ps1 | iex\`

It updates the engine and the app together and keeps your lectures. Please don't download the files below by hand: the line does that.

**Aktualisieren oder installieren:** in Stenny *Nach Updates suchen …* ▸ *Jetzt aktualisieren*, oder führ die Zeile, mit der du Stenny installiert hast, noch mal aus (Mac: im Terminal, Windows: in PowerShell, siehe oben). Deine Vorlesungen bleiben, wie sie sind.

[Code signing policy](https://stenny.eu/code-signing/) · [Privacy](https://stenny.eu/privacy/#app)

NOTES
)"
    # The LGPL asks that ffmpeg's source stays available next to the binary
    gh release create "$TAG" dist/Stenny.zip dist/Stenny.mcpb dist/Stenny.app.tar.gz dist/latest.json \
        scripts/install.sh scripts/install.ps1 \
        scripts/uninstall.sh scripts/uninstall.ps1 \
        app/build.noindex/tools-src/ffmpeg-*.tar.xz \
        --title "Stenny ${VERSION}" --notes "$notes" --generate-notes
    echo "✅ Released $TAG"
fi
