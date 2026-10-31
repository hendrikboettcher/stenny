#!/bin/zsh
# Installs app/build.noindex/Stenny.app and opens it. Used by build.sh, setup and `stenny update`.
#   app/install.sh            install and open
#   app/install.sh --no-open  install only
#
# Goes to /Applications, or to ~/Applications when this account may not write there (a standard user)
# or another account's different version is installed there (a shared Mac): never replace someone else's copy.
# Then unregister the source copy, so macOS only knows one Stenny.
set -euo pipefail
cd "$(dirname "$0")"

APP="$PWD/build.noindex/Stenny.app"
if [[ ! -d "$APP" ]]; then
    echo "❌ $APP doesn't exist."
    exit 1
fi
version() { /usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" "$1/Contents/Info.plist" 2>/dev/null; }

DEST=/Applications
if [[ -e /Applications/Stenny.app && ! -O /Applications/Stenny.app && "$(version /Applications/Stenny.app)" == "$(version "$APP")" ]]; then
    # A shared Mac where another account has this very version: one app is enough, or search shows two
    rm -rf "$HOME/Applications/Stenny.app"
    echo "ℹ️  Using the Stenny that's already in /Applications"
    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -u "$APP" 2>/dev/null || true
    echo "✅ Installed: /Applications/Stenny.app"
    [[ "${1:-}" == "--no-open" ]] || open /Applications/Stenny.app
    exit 0
fi
if [[ ! -w /Applications ]] || [[ -e /Applications/Stenny.app && ! -O /Applications/Stenny.app ]]; then
    DEST="$HOME/Applications"
    mkdir -p "$DEST"
    echo "ℹ️  /Applications/Stenny.app belongs to another account (or /Applications isn't writable): installing to ~/Applications"
fi
pkill -u "$(id -u)" -f "Stenny.app/Contents/MacOS/Stenny" || true
rm -rf "$DEST/Stenny.app"
/usr/bin/ditto "$APP" "$DEST/Stenny.app"
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
"$LSREGISTER" -u "$APP" 2>/dev/null || true
"$LSREGISTER" -f "$DEST/Stenny.app"
echo "✅ Installed: $DEST/Stenny.app"
[[ "${1:-}" == "--no-open" ]] || open "$DEST/Stenny.app"
