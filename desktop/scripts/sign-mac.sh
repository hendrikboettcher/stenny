#!/bin/zsh
# Signs a built Stenny.app with one identity, its engine included, and checks the result:
#   scripts/sign-mac.sh <Stenny.app> [identity]     identity: default "Stenny Dev" (app/make-cert.sh); "-" = ad hoc
#
# `codesign --deep` signs the helpers in Contents/MacOS but never code among the resources, and the engine inside the
# app (Contents/Resources/engine, scripts/bundle-engine.sh) is a Python with ~160 native libraries (.so/.dylib). Those
# are signed here one by one first, with the same identity, then the app around them, so its seal covers them as
# they are. macOS remembers the microphone and calendar permissions per signature (that's why "Stenny Dev" and not
# Tauri's ad-hoc one); a Developer ID and notarization (#93) would need these same steps plus the hardened runtime.
# Used by scripts/install-mac.sh and app/release.sh.
set -euo pipefail
app="${1:?usage: sign-mac.sh <Stenny.app> [identity]}"
identity="${2:-Stenny Dev}"

engine="$app/Contents/Resources/engine"
if [[ -d "$engine" ]]; then
    # every Mach-O file: by name (.so, .dylib) or as a program; `file` tells which really are code
    find "$engine" -type f \( -name '*.so' -o -name '*.dylib' -o -perm -u+x \) -print0 \
        | xargs -0 file --no-pad --separator '|' | sed -n 's/|.*Mach-O.*$//p' \
        | tr '\n' '\0' | xargs -0 codesign --force --timestamp=none --sign "$identity" 2>&1 \
        | { grep -v ': replacing existing signature$' || true; }
fi
codesign --force --deep --timestamp=none --sign "$identity" "$app" 2>&1 | { grep -v ': replacing existing signature$' || true; }
codesign --verify --deep --strict "$app"
# and the engine's libraries one by one: --deep doesn't look into the resources either
if [[ -d "$engine" ]]; then
    find "$engine" -type f \( -name '*.so' -o -name '*.dylib' \) -print0 | xargs -0 -n 50 codesign --verify --strict
    codesign --verify --strict "$engine/bin/python3"
fi
