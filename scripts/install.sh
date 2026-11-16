#!/bin/zsh
# Installs Stenny on a Mac, or updates it, with one line in Terminal and no questions. Stenny is invite-only during
# its closed alpha, so the line is the invite's own:
#   curl -fsSL https://stenny.eu/i/<code> | zsh
# stenny.eu answers with this script and the code set on its first line (STENNY_INVITE=…; the stenny-website repo,
# api/install.js); it downloads Stenny from stenny.eu with the code (api/download.js), and saves the code for
# Stenny's updates (`stenny invite`, in the Keychain).
#
# 1. checks the Mac (Apple Silicon, macOS 14.4+, 5 GB free) and that nothing is being recorded
# 2. downloads the newest release (into a temporary folder, removed at the end)
# 3. installs the app with its desktop/scripts/install-mac.sh (one Stenny in the Dock, Spotlight and Launchpad). The
#    engine is inside the app (#92): nothing else is installed. An engine an older setup installed with uv
#    (~/Applications/stenny.noindex) is removed by the app at its next start (src/stenny/old_engine.py).
# 4. first time only: the Stenny folder (~/Documents/Stenny); every time: the background job for final transcripts
# 5. opens Stenny, then downloads Whisper (1.6 GB) with progress. The app's setup list does the rest
#    (courses, Claude, calendar).
# Needs no password. Downloading with curl, not a browser, is what lets macOS open an app signed with the author's
# own certificate (SETUP.md step 0). Speaks German when the Mac's first language is German.
#
# For testing: STENNY_ZIP=<file> installs that Stenny.zip instead of the newest release (no invite needed);
# STENNY_SITE=<url> asks a preview deployment of stenny.eu instead.

# Everything is in one function that runs on the last line, so a download cut off halfway runs nothing.
stenny_install() {
    emulate -L zsh
    setopt err_exit pipe_fail no_unset
    autoload -Uz is-at-least

    local SITE="${STENNY_SITE:-https://stenny.eu}"
    local ZIP="${STENNY_ZIP:-}"
    local INVITE="${STENNY_INVITE:-}"
    local de=0
    [[ "$(defaults read -g AppleLanguages 2>/dev/null | sed -n '2p')" == *'"de'* ]] && de=1
    say()  { (( de )) && print -r -- "$2" || print -r -- "$1"; }
    step() { print; (( de )) && print -P -- "%B$2%b" || print -P -- "%B$1%b"; }
    fail() { print; say "❌ $1" "❌ $2" >&2; }   # the caller returns 1

    # 0. The invite: the line from stenny.eu set it. Without one, only a local Stenny.zip (testing).
    if [[ -z "$INVITE" && -z "$ZIP" ]]; then
        fail "Stenny is invite-only during its closed alpha. Run the install line from your invite, or ask for one on $SITE" \
             "Stenny gibt es in der geschlossenen Alpha nur mit Einladung. Führ die Installationszeile aus deiner Einladung aus, oder frag auf $SITE nach einer."
        return 1
    fi

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

    # 2. The release
    step "Downloading Stenny…" "Lade Stenny herunter…"
    local work="$(mktemp -d "${TMPDIR:-/tmp}/stenny-install.XXXXXX")"
    trap "rm -rf '$work'" EXIT INT TERM
    if [[ -n "$ZIP" ]]; then
        cp "$ZIP" "$work/Stenny.zip"
    else
        # the code reaches curl on stdin (-H @-), never on its command line; stenny.eu redirects to the file
        local http
        http="$(print -r -- "X-Stenny-Invite: $INVITE" | curl -L --progress-bar -H @- -w '%{http_code}' \
                -o "$work/Stenny.zip" "$SITE/api/download?asset=Stenny.zip")" || http=000
        if [[ "$http" == 403 ]]; then
            fail "This invite isn't valid (any more). Ask for a new one on $SITE" \
                 "Diese Einladung gilt nicht (mehr). Frag auf $SITE nach einer neuen."
            return 1
        elif [[ "$http" != 200 ]]; then
            fail "Stenny couldn't be downloaded (HTTP $http). Please try again in a few minutes." \
                 "Stenny ließ sich nicht herunterladen (HTTP $http). Versuch es bitte in ein paar Minuten noch mal."
            return 1
        fi
    fi
    /usr/bin/ditto -x -k "$work/Stenny.zip" "$work"
    local new="$work/stenny.noindex"
    [[ -f "$new/pyproject.toml" && -x "$new/app/build.noindex/Stenny.app/Contents/Resources/engine/bin/python3" \
       && -x "$new/desktop/scripts/install-mac.sh" ]] \
        || { fail "The download doesn't contain the Stenny app." "Der Download enthält die Stenny-App nicht."; return 1; }
    local version="$(sed -n 's/^version = "\(.*\)"/\1/p' "$new/pyproject.toml")"

    # 3. The app, in place, with one Stenny for macOS (it quits a running Stenny first). Where it goes, as
    # install-mac.sh decides: /Applications, or ~/Applications when that isn't writable or another account's is there.
    step "Installing Stenny $version…" "Installiere Stenny $version…"
    "$new/desktop/scripts/install-mac.sh" --no-open "$new/app/build.noindex/Stenny.app"
    local app=/Applications/Stenny.app
    if [[ ! -w /Applications ]] || [[ -e "$app" && ! -O "$app" ]]; then app="$HOME/Applications/Stenny.app"; fi
    # the engine inside the app, as Stenny runs it (never writing bytecode into the signed app)
    local python="$app/Contents/Resources/engine/bin/python3"
    [[ -x "$python" ]] || { fail "Stenny's engine isn't in $app." "Stennys Engine ist nicht in $app."; return 1; }
    stenny() { PYTHONDONTWRITEBYTECODE=1 "$python" -m stenny "$@"; }

    # 4. First time: the Stenny folder. Every time: the job that finishes waiting final transcripts on AC power.
    if ! stenny where --json 2>/dev/null | grep -q '"error": null'; then
        stenny init >/dev/null || true   # quiet: its "Edit it" is for the command line; refuses on stderr if one is there
    fi
    stenny schedule install >/dev/null || say "⚠️  The background job for final transcripts couldn't be installed." \
                                                 "⚠️  Der Hintergrund-Job für finale Transkripte ließ sich nicht installieren."
    # The invite, for Stenny's updates (in the Keychain, through the engine; on stdin, never on a command line)
    if [[ -n "$INVITE" ]]; then
        print -r -- "$INVITE" | stenny invite --stdin >/dev/null \
            || say "⚠️  Your invite couldn't be saved: Stenny won't find updates. Run this line again later." \
                   "⚠️  Deine Einladung ließ sich nicht speichern: Stenny findet keine Updates. Führ die Zeile später noch mal aus."
    fi

    # 5. Stenny, then Whisper (in the foreground, so its progress shows; already there: done in seconds)
    open -b eu.stenny.Stenny || true
    step "Downloading the speech recognition (Whisper, about 1.6 GB, once)…" \
         "Lade die Spracherkennung herunter (Whisper, etwa 1,6 GB, nur einmal)…"
    say "Keep this window open until it's done; Stenny is already open." \
        "Lass dieses Fenster offen, bis es fertig ist; Stenny ist schon offen."
    stenny download-model || say "⚠️  Whisper will download with the first transcription instead." \
                                    "⚠️  Whisper wird stattdessen beim ersten Transkript heruntergeladen."

    print
    say "✅ Stenny $version is installed: the stick figure in the menu bar. Its window shows what's left to set up." \
        "✅ Stenny $version ist installiert: das Strichmännchen in der Menüleiste. Sein Fenster zeigt, was noch einzurichten ist."
    say "   You can close this window." "   Du kannst dieses Fenster schließen."
}

stenny_install "$@"
