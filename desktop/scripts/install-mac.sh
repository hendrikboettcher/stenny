#!/bin/zsh
# Installs a built Stenny.app on this Mac and makes sure macOS knows one Stenny only: one in Spotlight and Launchpad,
# one icon in the Dock. Use it every time instead of copying the app by hand.
#   scripts/install-mac.sh                     src-tauri/target.noindex/release/bundle/macos/Stenny.app, then open it
#   scripts/install-mac.sh --debug             the debug build (src-tauri/target.noindex/debug/…)
#   scripts/install-mac.sh --no-open [<app>]   install only; <app>: another Stenny.app to install
#
# Why: macOS registers every Stenny.app it sees (each build folder, each worktree) under the same bundle id, and a
# reinstall that deletes and copies the app gives the Dock's tile a dead bookmark. The Dock then doesn't recognise
# the running Stenny as its pinned one and adds it again under recent apps: two or three Stenny icons. So this
#   1. updates the installed app in place (rsync), which keeps the bundle the Dock's bookmark points to,
#   2. unregisters every other Stenny.app (build folders, worktrees, old app/ builds),
#   3. removes Stenny tiles the Dock already has twice, and restarts the Dock only if it changed one.
# Goes to /Applications, or ~/Applications when this account may not write there or another account's copy is there
# (never replace someone else's copy). Won't touch a running recording or final transcript. macOS only: Windows' installer (NSIS) keeps one
# Stenny in the Start menu and taskbar by itself.
set -euo pipefail
cd "$(dirname "$0")/.."

profile=release open_after=1 app=""
for arg in "$@"; do
    case "$arg" in
        --debug) profile=debug ;;
        --no-open) open_after=0 ;;
        -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
        *) app="$arg" ;;
    esac
done
app="${app:-src-tauri/target.noindex/${profile}/bundle/macos/Stenny.app}"
[[ -d "$app" ]] || { echo "❌ $app doesn't exist. Build it first: npm run tauri build -- --bundles app"; exit 1; }
app="${app:A}"
plist() { /usr/libexec/PlistBuddy -c "Print :$2" "$1/Contents/Info.plist" 2>/dev/null || true; }
BUNDLE_ID="$(plist "$app" CFBundleIdentifier)"
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister

# Signed with a stable certificate ("Stenny Dev", made once by app/make-cert.sh), so the microphone and calendar
# permissions survive updates; Tauri's builds are only signed ad hoc, which changes with every build and makes macOS ask
# again. STENNY_SIGN_IDENTITY picks another certificate from the keychain.
identity="${STENNY_SIGN_IDENTITY:-Stenny Dev}"
if security find-identity -p codesigning | grep -q "\"$identity\""; then
    codesign --force --deep --sign "$identity" "$app"
elif ! codesign -dv "$app" 2>&1 | grep -q 'Signature=adhoc'; then
    :   # a release, already signed with the certificate (scripts/install.sh on a user's Mac): keep that signature
else
    echo "⚠️  Certificate \"$identity\" not found: installing the ad-hoc signed build, so macOS will ask for the microphone"
    echo "   and calendar again. Run app/make-cert.sh once to fix that."
fi

# A recording comes first: never quit Stenny in the middle of one, nor of a final pass (`stenny queue run`: it uses
# the app's ffmpeg, which quitting Stenny kills, and the pass then fails). Only the real processes count (`stenny live`
# started by Python, the recorder), not a shell or script whose command line merely mentions "stenny live".
recording='^[^ ]*/python[0-9.]* (-m stenny|[^ ]*/stenny) (live|queue run)( |$)|^[^ ]*/Contents/MacOS/stenny-rec( |$)'
if pgrep -u "$(id -u)" -f "$recording" >/dev/null; then
    echo "❌ Stenny is recording or making a final transcript. Install again once it's done."
    exit 1
fi

dest=/Applications
if [[ ! -w /Applications ]] || [[ -e /Applications/Stenny.app && ! -O /Applications/Stenny.app ]]; then
    dest="$HOME/Applications"
    mkdir -p "$dest"
    echo "ℹ️  /Applications/Stenny.app belongs to another account (or /Applications isn't writable): installing to ~/Applications"
fi
target="$dest/Stenny.app"

# Quit the running Stenny (and its helpers) the way the user would, then make sure
if pgrep -u "$(id -u)" -f "Stenny.app/Contents/MacOS/" >/dev/null; then
    osascript -e "quit app id \"$BUNDLE_ID\"" >/dev/null 2>&1 || true
    for _ in {1..20}; do pgrep -u "$(id -u)" -f "Stenny.app/Contents/MacOS/stenny-desktop" >/dev/null || break; sleep 0.25; done
    pkill -u "$(id -u)" -f "Stenny.app/Contents/MacOS/" || true
fi

# 1. In place: the bundle folder stays the same one, so the Dock's tile still points at it
mkdir -p "$target"
/usr/bin/rsync -a --delete "$app/" "$target/"
touch "$target"

# 2. One Stenny for macOS: forget every other Stenny.app (build folders, worktrees, scratch builds, app/build.noindex)
"$LSREGISTER" -dump 2>/dev/null | sed -n 's/^path: *\(.*Stenny\.app\) (0x[0-9a-f]*)$/\1/p' | sort -u | while IFS= read -r other; do
    [[ "$other" == "$target" ]] || "$LSREGISTER" -u "$other" 2>/dev/null || true
done
"$LSREGISTER" -f "$target"

# 3. The Dock: one Stenny tile. Pinned → none under recent apps; never two pinned, never two recent.
dock="$(mktemp -t stenny-dock).plist"
defaults export com.apple.dock "$dock"
pb() { /usr/libexec/PlistBuddy -c "$1" "$dock" 2>/dev/null; }
stenny_tiles() {   # indexes of the Stenny tiles in a Dock section, last first (so deleting keeps the others' numbers)
    local i=0 url found=()
    while pb "Print :${1}:${i}" >/dev/null; do   # spacers and folders have no file URL: skip them, don't stop
        url="$(pb "Print :${1}:${i}:tile-data:file-data:_CFURLString" || true)"
        [[ "$url" == */Stenny.app/ ]] && found=("$i" "${found[@]}")
        i=$((i + 1))
    done
    print -r -- "${found[@]}"
}
changed=0
pinned=(${=$(stenny_tiles persistent-apps)})
recent=(${=$(stenny_tiles recent-apps)})
for i in "${pinned[@]:0:$(( ${#pinned} > 1 ? ${#pinned} - 1 : 0 ))}"; do pb "Delete :persistent-apps:${i}" && changed=1; done
keep_recent=$(( ${#pinned} > 0 ? 0 : 1 ))
for i in "${recent[@]:0:$(( ${#recent} > keep_recent ? ${#recent} - keep_recent : 0 ))}"; do pb "Delete :recent-apps:${i}" && changed=1; done
if (( changed )); then
    defaults import com.apple.dock "$dock"
    killall Dock 2>/dev/null || true
    echo "ℹ️  Removed Stenny icons the Dock had twice"
fi
rm -f "$dock"

echo "✅ Installed: $target ($(plist "$target" CFBundleShortVersionString))"
(( open_after )) && open "$target"
exit 0
