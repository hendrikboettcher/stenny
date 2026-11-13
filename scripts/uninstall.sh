#!/bin/zsh
# Removes Stenny from a Mac with one line in Terminal:
#   curl -fsSL https://github.com/hendrikboettcher/stenny/releases/latest/download/uninstall.sh | zsh
#
# 1. stops if Stenny is recording or making a final transcript, or runs from a git checkout (a developer's Mac)
# 2. quits Stenny
# 3. `stenny uninstall --yes --json` (src/stenny/uninstall.py): the background job, the connector in Claude Code,
#    the saved API key and calendar links, logs, cache and the Whisper model
# 4. the app (to the Trash, unregistered from Launch Services), its permissions and the app's own web data
# 5. the engine (`uv tool uninstall stenny`) and its code (~/Applications/stenny.noindex)
# 6. Stenny's settings and data, with the recordings' audio, to the Trash: they can still be brought back
# The transcripts (the Stenny folder) stay: they're the student's. uv stays too: other programs may use it.
# Needs no password. Speaks German when the Mac's first language is German.

# Everything is in one function that runs on the last line, so a download cut off halfway runs nothing.
stenny_uninstall() {
    emulate -L zsh
    setopt pipe_fail no_unset

    local FOLDER="$HOME/Applications/stenny.noindex"
    local LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
    local de=0
    [[ "$(defaults read -g AppleLanguages 2>/dev/null | sed -n '2p')" == *'"de'* ]] && de=1
    say()  { (( de )) && print -r -- "$2" || print -r -- "$1"; }
    step() { print; (( de )) && print -P -- "%B$2%b" || print -P -- "%B$1%b"; }
    fail() { print; say "❌ $1" "❌ $2" >&2; }   # the caller returns 1
    local stamp="$(date '+%Y-%m-%d %H.%M')"
    # into the Trash under a name of its own, so nothing already there is replaced
    to_trash() { [[ -e "$1" ]] || return 0; mv "$1" "$HOME/.Trash/${1:t:r} (removed $stamp)${${1:e}:+.${1:e}}" 2>/dev/null; }

    step "Removing Stenny…" "Entferne Stenny…"
    # The same processes install.sh waits for: a recording, or a final transcript
    local recording='^[^ ]*/python[0-9.]* (-m stenny|[^ ]*/stenny) (live|queue run)( |$)|^[^ ]*/Contents/MacOS/stenny-rec( |$)'
    if pgrep -u "$(id -u)" -f "$recording" >/dev/null; then
        fail "Stenny is recording or making a final transcript. Run this again once it's done." \
             "Stenny nimmt gerade auf oder macht ein finales Transkript. Starte das hier noch mal, wenn es fertig ist."
        return 1
    fi
    local uv="$(command -v uv || true)"
    [[ -n "$uv" ]] || for c in "$HOME/.local/bin/uv" /opt/homebrew/bin/uv; do [[ -x "$c" ]] && { uv="$c"; break; }; done
    if [[ -n "$uv" ]]; then
        local from="$(sed -n 's/.*editable = "\([^"]*\)".*/\1/p' "$("$uv" tool dir)/stenny/uv-receipt.toml" 2>/dev/null || true)"
        if [[ -n "$from" && -e "$from/.git" && "${STENNY_FORCE:-}" != 1 ]]; then
            fail "Stenny's engine here runs from a git checkout ($from), so this Mac is set up for development." \
                 "Stennys Engine läuft hier aus einem Git-Checkout ($from); dieser Mac ist zum Entwickeln eingerichtet."
            say "   Leaving it alone. To remove it anyway: STENNY_FORCE=1" \
                "   Ich lasse ihn, wie er ist. Um ihn trotzdem zu entfernen: STENNY_FORCE=1"
            return 1
        fi
    fi

    # 2. Quit Stenny
    if pgrep -u "$(id -u)" -f "Stenny.app/Contents/MacOS/" >/dev/null; then
        osascript -e 'quit app id "eu.stenny.Stenny"' >/dev/null 2>&1 || true
        for _ in {1..20}; do pgrep -u "$(id -u)" -f "Stenny.app/Contents/MacOS/" >/dev/null || break; sleep 0.25; done
        pkill -u "$(id -u)" -f "Stenny.app/Contents/MacOS/" || true
    fi

    # 3. What the engine set up. Without a working engine: the usual places.
    local stenny=""
    [[ -n "$uv" ]] && stenny="$("$uv" tool dir --bin 2>/dev/null)/stenny"
    [[ -x "$stenny" ]] || stenny="$HOME/.local/bin/stenny"
    local result="" folder="" desktop_extension=false
    local -a trash problems
    [[ -x "$stenny" ]] && result="$("$stenny" uninstall --yes --json 2>/dev/null)" || result=""
    field() { print -r -- "$result" | plutil -extract "$1" raw -o - - 2>/dev/null; }
    if [[ -n "$result" ]]; then
        local i=0 item
        while item="$(field "trash.$i")"; do trash+=("$item"); (( i++ )); done
        i=0; while item="$(field "problems.$i")"; do problems+=("$item"); (( i++ )); done
        folder="$(field folder || true)"
        desktop_extension="$(field claude_desktop_extension || print false)"
    else
        launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.stenny.queue.plist" >/dev/null 2>&1 || true
        rm -f "$HOME/Library/LaunchAgents/local.stenny.queue.plist"
        security delete-generic-password -s Stenny -a anthropic-api-key >/dev/null 2>&1 || true
        rm -rf "$HOME/Library/Logs/Stenny" "$HOME/Library/Caches/Stenny"
        trash=("$HOME/Library/Application Support/Stenny")
    fi

    # 4. The app: every installed copy (the new app and the old menu bar app), to the Trash
    local app id
    for app in /Applications/Stenny.app "$HOME/Applications/Stenny.app"; do
        [[ -d "$app" ]] || continue
        id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$app/Contents/Info.plist" 2>/dev/null || true)"
        [[ "$id" == eu.stenny.Stenny || "$id" == app.stenny ]] || continue
        "$LSREGISTER" -u "$app" >/dev/null 2>&1 || true
        to_trash "$app" || say "⚠️  $app couldn't be moved to the Trash: drag it there yourself." \
                               "⚠️  $app ließ sich nicht in den Papierkorb legen: zieh es selbst hinein."
    done
    for id in eu.stenny.Stenny app.stenny; do
        tccutil reset All "$id" >/dev/null 2>&1 || true   # microphone and calendar permissions
        defaults delete "$id" >/dev/null 2>&1 || true
        rm -rf "$HOME/Library/WebKit/$id" "$HOME/Library/Caches/$id" "$HOME/Library/HTTPStorages/$id" \
               "$HOME/Library/Application Support/$id" "$HOME/Library/Saved Application State/$id.savedState"
    done

    # 5. The engine and its code
    [[ -n "$uv" ]] && "$uv" tool uninstall stenny >/dev/null 2>&1
    [[ -e "$FOLDER/.git" ]] || rm -rf "$FOLDER"

    # 6. Settings and the recordings' audio, to the Trash
    local place
    for place in "${trash[@]}"; do
        to_trash "$place" || say "⚠️  $place couldn't be moved to the Trash." "⚠️  $place ließ sich nicht in den Papierkorb legen."
    done

    print
    say "✅ Stenny is removed. Its settings and the recordings' audio are in the Trash, until you empty it." \
        "✅ Stenny ist entfernt. Seine Einstellungen und das Audio der Aufnahmen liegen im Papierkorb, bis du ihn leerst."
    [[ -n "$folder" ]] && say "   Your transcripts stay in $folder." "   Deine Transkripte bleiben in $folder."
    [[ "$desktop_extension" == true ]] && say "   Claude Desktop still has the Stenny extension: Settings ▸ Extensions ▸ Stenny ▸ Uninstall." \
                                              "   Claude Desktop hat noch die Stenny-Erweiterung: Einstellungen ▸ Erweiterungen ▸ Stenny ▸ Deinstallieren."
    for place in "${problems[@]}"; do say "⚠️  $place" "⚠️  $place"; done
    say "   uv stays, as other programs may use it. To remove it too: rm ~/.local/bin/uv ~/.local/bin/uvx" \
        "   uv bleibt, falls andere Programme es nutzen. Um es auch zu entfernen: rm ~/.local/bin/uv ~/.local/bin/uvx"
    return 0
}

stenny_uninstall "$@"
