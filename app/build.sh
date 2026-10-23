#!/bin/zsh
# Builds Stenny.app (no Xcode needed, Command Line Tools only) and installs it to /Applications
# (or ~/Applications, see the end).
#   app/build.sh               build, sign, install
#   app/build.sh --no-install  only build and sign (app/build/Stenny.app)
set -euo pipefail
cd "$(dirname "$0")"

INSTALL=1
[[ "${1:-}" == "--no-install" ]] && INSTALL=0
if ! xcrun --find swift >/dev/null 2>&1; then
    echo "❌ Swift not found. Install Apple's Command Line Tools with: xcode-select --install"
    exit 1
fi

APP="build/Stenny.app"
swift build -c release

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$(swift build -c release --show-bin-path)/Stenny" "$APP/Contents/MacOS/Stenny"
# app icon: drawn by make-icon.swift (the website's stick figure)
swift make-icon.swift build/AppIcon.iconset
iconutil -c icns build/AppIcon.iconset -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf build/AppIcon.iconset
# the recorder runs as a child of Stenny, so it shares Stenny's microphone permission
cp "$(swift build -c release --show-bin-path)/stenny-rec" "$APP/Contents/MacOS/stenny-rec"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Stenny</string>
    <key>CFBundleDisplayName</key><string>Stenny</string>
    <key>CFBundleIdentifier</key><string>app.stenny</string>
    <key>CFBundleExecutable</key><string>Stenny</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <!-- New installs show notifications as Alerts, so Start Recording / Not now are always visible -->
    <key>NSUserNotificationAlertStyle</key><string>alert</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>CFBundleDevelopmentRegion</key><string>en</string>
    <key>LSMinimumSystemVersion</key><string>14.4</string>
    <key>NSCalendarsFullAccessUsageDescription</key><string>Stenny looks for the lecture that's on now in your calendar, so you can start recording it with one click. Nothing is changed or sent anywhere.</string>
    <key>NSMicrophoneUsageDescription</key><string>Stenny records lectures you start from its menu. Audio stays on this Mac; pausing turns the microphone off.</string>
    <!-- No Dock icon, no app menu: menu bar only -->
    <key>LSUIElement</key><true/>
    <key>NSHumanReadableCopyright</key><string>Built with Claude</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# Signing: with a stable certificate ("Stenny Dev", made once by make-cert.sh) the microphone and
# calendar permissions survive rebuilds. Ad-hoc signatures change with every build, so macOS would ask again.
# STENNY_SIGN_IDENTITY picks another certificate from your keychain.
IDENTITY="${STENNY_SIGN_IDENTITY:-Stenny Dev}"
if security find-identity -p codesigning | grep -q "\"$IDENTITY\""; then
    codesign --force --deep --sign "$IDENTITY" "$APP"
    echo "✅ Signed with \"$IDENTITY\""
else
    echo "⚠️  Certificate \"$IDENTITY\" not found: signed ad hoc. Stenny works, but macOS will ask for the"
    echo "   microphone and calendar again after every rebuild. Run app/make-cert.sh once to fix that."
    codesign --force --deep --sign - "$APP"
fi
echo "✅ Built: $APP"

if (( ! INSTALL )); then
    exit 0
fi

# Install to /Applications, or to ~/Applications when this account may not write there (a standard user)
# or another account's Stenny is installed there (a shared Mac): never replace someone else's copy.
# Then unregister the copy in the build folder, so macOS only knows one Stenny.
DEST=/Applications
if [[ ! -w /Applications ]] || [[ -e /Applications/Stenny.app && ! -O /Applications/Stenny.app ]]; then
    DEST="$HOME/Applications"
    mkdir -p "$DEST"
    echo "ℹ️  /Applications/Stenny.app belongs to another account (or /Applications isn't writable): installing to ~/Applications"
fi
pkill -u "$(id -u)" -f "Stenny.app/Contents/MacOS/Stenny" || true
rm -rf "$DEST/Stenny.app"
cp -R "$APP" "$DEST/"
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
"$LSREGISTER" -u "$APP" 2>/dev/null || true
"$LSREGISTER" -f "$DEST/Stenny.app"
echo "✅ Installed: $DEST/Stenny.app"
