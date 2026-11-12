#!/bin/zsh
# Installs Stenny on a Mac, or updates it, with one line in Terminal and no questions:
#   curl -fsSL https://github.com/hendrikboettcher/stenny/releases/latest/download/install.sh | zsh
#
# 1. checks the Mac (Apple Silicon, macOS 14.4+, 5 GB free) and that nothing is being recorded
# 2. downloads the newest release into ~/Applications/stenny.noindex (an older one goes to the Trash)
# 3. installs uv and Stenny's engine (`uv tool install -e`), as SETUP.md does
# 4. installs the app with desktop/scripts/install-mac.sh (one Stenny in the Dock, Spotlight and Launchpad)
# 5. first time only: the Stenny folder (~/Documents/Stenny); every time: the background job for final transcripts
# 6. opens Stenny, then downloads Whisper (1.6 GB) with progress. The app's setup list does the rest
#    (courses, Claude, calendar).
# Needs no password. Downloading with curl, not a browser, is what lets macOS open an app signed with the author's
# own certificate (SETUP.md step 0). Speaks German when the Mac's first language is German.
#
# For testing: STENNY_ZIP=<file or URL> installs that Stenny.zip instead of the newest release.
# STENNY_FORCE=1 replaces an engine that runs from a git checkout (a developer's), which it otherwise leaves alone.

# Everything is in one function that runs on the last line, so a download cut off halfway runs nothing.
stenny_install() {
    emulate -L zsh
    setopt err_exit pipe_fail no_unset
    autoload -Uz is-at-least

    local REPO=hendrikboettcher/stenny
    local ZIP="${STENNY_ZIP:-https://github.com/$REPO/releases/latest/download/Stenny.zip}"
    local FOLDER="$HOME/Applications/stenny.noindex"
    local de=0
    [[ "$(defaults read -g AppleLanguages 2>/dev/null | sed -n '2p')" == *'"de'* ]] && de=1
    say()  { (( de )) && print -r -- "$2" || print -r -- "$1"; }
    step() { print; (( de )) && print -P -- "%B$2%b" || print -P -- "%B$1%b"; }
    fail() { print; say "❌ $1" "❌ $2" >&2; }   # the caller returns 1

    # 1. The Mac
    step "Checking this Mac…" "Prüfe den Mac…"
    [[ "$(uname -m)" == arm64 ]] \
        || { fail "Stenny needs a Mac with Apple Silicon (M1 or newer); this one has an Intel processor." \
                  "Stenny braucht einen Mac mit Apple Silicon (M1 oder neuer); dieser hat einen Intel-Prozessor."; return 1; }
    local os="$(sw_vers -productVersion)"
    is-at-least 14.4 "$os" \
        || { fail "Stenny needs macOS 14.4 or newer (this Mac has $os): System Settings ▸ General ▸ Software Update." \
                  "Stenny braucht macOS 14.4 oder neuer (dieser Mac hat $os): Systemeinstellungen ▸ Allgemein ▸ Softwareupdate."; return 1; }
    local free_gb="$(df -g "$HOME" | awk 'NR==2 {print $4}')"
    (( free_gb >= 5 )) \
        || { fail "Stenny needs 5 GB of free space; this Mac has ${free_gb} GB." \
                  "Stenny braucht 5 GB freien Speicher; dieser Mac hat ${free_gb} GB."; return 1; }
    # The same processes install-mac.sh waits for: a recording, or a final transcript using the app's ffmpeg
    local recording='^[^ ]*/python[0-9.]* (-m stenny|[^ ]*/stenny) (live|queue run)( |$)|^[^ ]*/Contents/MacOS/stenny-rec( |$)'
    if pgrep -u "$(id -u)" -f "$recording" >/dev/null; then
        fail "Stenny is recording or making a final transcript. Run this again once it's done." \
             "Stenny nimmt gerade auf oder macht ein finales Transkript. Starte das hier noch mal, wenn es fertig ist."
        return 1
    fi

    # uv, and where the engine is installed from now
    local uv="$(command -v uv || true)"
    [[ -n "$uv" ]] || for c in "$HOME/.local/bin/uv" /opt/homebrew/bin/uv; do [[ -x "$c" ]] && { uv="$c"; break; }; done
    if [[ -n "$uv" ]]; then
        local from="$(sed -n 's/.*editable = "\([^"]*\)".*/\1/p' "$("$uv" tool dir)/stenny/uv-receipt.toml" 2>/dev/null || true)"
        if [[ -n "$from" && -e "$from/.git" && "${STENNY_FORCE:-}" != 1 ]]; then
            fail "Stenny's engine here runs from a git checkout ($from), so this Mac is set up for development." \
                 "Stennys Engine läuft hier aus einem Git-Checkout ($from); dieser Mac ist zum Entwickeln eingerichtet."
            say "   Leaving it alone. To replace it anyway: STENNY_FORCE=1" \
                "   Ich lasse ihn, wie er ist. Um ihn trotzdem zu ersetzen: STENNY_FORCE=1"
            return 1
        fi
    fi
    [[ -e "$FOLDER/.git" ]] && { fail "$FOLDER is a git checkout: use git pull there." \
                                      "$FOLDER ist ein Git-Checkout: nutze dort git pull."; return 1; }

    # 2. The release. Unpacked next to the folder, so moving it in place is a rename on the same disk.
    step "Downloading Stenny…" "Lade Stenny herunter…"
    mkdir -p "$HOME/Applications"
    local work="$(mktemp -d "$HOME/Applications/.stenny-install.XXXXXX")"
    trap "rm -rf '$work'" EXIT INT TERM
    if [[ -f "$ZIP" ]]; then cp "$ZIP" "$work/Stenny.zip"; else curl -fL --progress-bar -o "$work/Stenny.zip" "$ZIP"; fi
    /usr/bin/ditto -x -k "$work/Stenny.zip" "$work"
    local new="$work/stenny.noindex"
    [[ -f "$new/pyproject.toml" && -d "$new/app/build.noindex/Stenny.app" && -x "$new/desktop/scripts/install-mac.sh" ]] \
        || { fail "The download doesn't contain the Stenny app." "Der Download enthält die Stenny-App nicht."; return 1; }
    local version="$(sed -n 's/^version = "\(.*\)"/\1/p' "$new/pyproject.toml")"

    # Quit Stenny before its engine changes under it (install-mac.sh would only quit it later)
    if pgrep -u "$(id -u)" -f "Stenny.app/Contents/MacOS/" >/dev/null; then
        say "Quitting Stenny for the update…" "Beende Stenny für das Update…"
        osascript -e 'quit app id "eu.stenny.Stenny"' >/dev/null 2>&1 || true
        for _ in {1..20}; do pgrep -u "$(id -u)" -f "Stenny.app/Contents/MacOS/" >/dev/null || break; sleep 0.25; done
        pkill -u "$(id -u)" -f "Stenny.app/Contents/MacOS/" || true
    fi
    local old=""
    if [[ -e "$FOLDER" ]]; then old="$work/before"; mv "$FOLDER" "$old"; fi
    mv "$new" "$FOLDER"

    # 3. uv and the engine. If that fails, the old folder comes back and nothing else has changed.
    step "Installing Stenny $version…" "Installiere Stenny $version…"
    if [[ -z "$uv" ]]; then
        curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh   # ~/.local/bin, no shell files changed
        uv="$HOME/.local/bin/uv"
    fi
    if ! "$uv" tool install -e "$FOLDER" --force --python 3.12 --quiet; then
        rm -rf "$FOLDER"
        if [[ -n "$old" ]]; then mv "$old" "$FOLDER"; "$uv" tool install -e "$FOLDER" --force --python 3.12 --quiet || true; fi
        fail "Installing Stenny's engine failed (see above). Nothing was changed." \
             "Stennys Engine ließ sich nicht installieren (siehe oben). Es wurde nichts geändert."
        return 1
    fi
    local stenny="$("$uv" tool dir --bin)/stenny"
    # The old folder only now that the new engine works: to the Trash, so it can still be brought back
    if [[ -n "$old" ]]; then
        mv "$old" "$HOME/.Trash/stenny (replaced $(date '+%Y-%m-%d %H.%M')).noindex" 2>/dev/null || true
    fi

    # 4. The app, in place, with one Stenny for macOS
    "$FOLDER/desktop/scripts/install-mac.sh" --no-open "$FOLDER/app/build.noindex/Stenny.app"

    # 5. First time: the Stenny folder. Every time: the job that finishes waiting final transcripts on AC power.
    if ! "$stenny" where --json 2>/dev/null | grep -q '"error": null'; then
        "$stenny" init || true   # refuses, and says why, when a config is already there
    fi
    "$stenny" schedule install >/dev/null || say "⚠️  The background job for final transcripts couldn't be installed." \
                                                 "⚠️  Der Hintergrund-Job für finale Transkripte ließ sich nicht installieren."

    # 6. Stenny, then Whisper (in the foreground, so its progress shows; already there: done in seconds)
    open -b eu.stenny.Stenny || true
    step "Downloading the speech recognition (Whisper, about 1.6 GB, once)…" \
         "Lade die Spracherkennung herunter (Whisper, etwa 1,6 GB, nur einmal)…"
    say "Keep this window open until it's done; Stenny is already open." \
        "Lass dieses Fenster offen, bis es fertig ist; Stenny ist schon offen."
    "$stenny" download-model || say "⚠️  Whisper will download with the first transcription instead." \
                                    "⚠️  Whisper wird stattdessen beim ersten Transkript heruntergeladen."

    print
    say "✅ Stenny $version is installed: the stick figure in the menu bar. Its window shows what's left to set up." \
        "✅ Stenny $version ist installiert: das Strichmännchen in der Menüleiste. Sein Fenster zeigt, was noch einzurichten ist."
    say "   You can close this window." "   Du kannst dieses Fenster schließen."
}

stenny_install "$@"
