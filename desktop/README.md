# Stenny's app (in development)

The new Stenny app: Tauri 2 (Rust shell) with a Svelte 5 + TypeScript interface in the stenny.eu style. It will
replace the Swift menu bar app in `app/`. Everything about lectures comes from the Python engine, through one
`stenny app-server` process (src/stenny/app_server.py) that the app starts and talks to in JSON lines
(src-tauri/src/engine.rs).

The look is the website's (its assets/site.css): the tokens, Geist and the shared buttons in `src/app.css`, each
window's own layout in its component. Geist and Geist Mono ship in `static/fonts` (SIL OFL, from Vercel's `geist`
package), so the app looks the same offline; Geist Mono is for times, clocks and file paths.

```bash
npm install                                   # once
scripts/helpers.sh                            # what it bundles: stenny-agent as Stenny Agent.app (macOS only, tauri.macos.conf.json), stenny-rec, Stenny.mcpb (needs Node)
scripts/bundle-engine.sh                      # the engine inside the app (src-tauri/engine, ~670 MB on a Mac; needs uv; Git Bash on Windows)
bash scripts/stenny-rec.sh                    # on Windows (Git Bash): stenny-rec.exe, the WASAPI recorder (stenny-rec/)
npm run check                                 # type check
npm run tauri build -- --debug --bundles app  # src-tauri/target.noindex/debug/bundle/macos/Stenny.app
scripts/install-mac.sh                        # the release build into /Applications, one Stenny in the Dock (--debug, --no-open)
open -n --env STENNY_CLI=../.venv/bin/stenny src-tauri/target.noindex/debug/bundle/macos/Stenny.app --args --library
STENNY_CLI=../.venv/bin/stenny npx vite dev --port 1420   # the interface in a browser, real engine (dev/engine-bridge.js)
```

**Install on a Mac only with `scripts/install-mac.sh`**, never by copying the app: it signs the build with "Stenny
Dev" (app/make-cert.sh; Tauri signs only ad hoc, and macOS would ask for the microphone and calendar again), updates
the installed app in place (so the Dock's tile still knows it), unregisters every other Stenny.app (build folders, worktrees) and removes
Stenny tiles the Dock has twice, so Stenny shows in the Dock once. Builds land in `src-tauri/target.noindex` (src-tauri/.cargo/config.toml): Spotlight
skips `.noindex` folders, so a build never shows up in Spotlight or Launchpad as a second Stenny. It won't quit a Stenny that's recording.

`--library` runs the window without the menu bar icon, for trying a build next to the installed Stenny (never two
Stenny icons in the menu bar). `STENNY_CLI` picks the engine (default: the one inside the app, else
`~/.local/bin/stenny`, see below); `STENNY_HOME` puts all of
Stenny's own files into one test folder (its config.toml at the top, Recordings/, Queue/, Logs/; the Stenny folder is
whatever that config's `folder` says), `STENNY_CONFIG` names another config. The app never computes a path itself: it
asks the engine (`settings`, `preferences`; src/stenny/paths.py). When the engine starts it moves an older install
(`~/Lectures Data`) into today's places and starts a config if there is none (src/stenny/migrate.py, `on_start`).
Rust comes from rustup (`~/.cargo/bin`).
⌘R / Ctrl+R and View ▸ Reload (on Windows in each window's menu bar) reload only the window's page
(src-tauri/src/lib.rs, `reload`; routes/+layout.svelte catches the key in every window). The library opens on a
running recording, or one whose final transcript is being made, whenever it comes back: loaded (start, ⌘R), or
shown again from the Dock, Spotlight or the menu bar (the "library-shown" event). Otherwise it keeps the last
selection (localStorage); the refresh every few seconds never changes it.

Without `--library` it is the menu bar app (src-tauri/src/menubar.rs): the classic menu, the clock next to the icon,
the calendar's lecture with its "Record this lecture?" notification, and the pause shortcut. The Mac's calendar and
the pause shortcut come through `stenny-agent` (app/Sources/stenny-agent), a small app of its own inside Stenny's
(Contents/Helpers/Stenny Agent.app with its own Info.plist and LSUIElement, scripts/helpers.sh), so it never shows up
in the Dock as a second Stenny (#52) while Stenny itself, without LSUIElement, bounces in the Dock as it opens; the app
starts it as its child, so the calendar permission is still Stenny's. Notifications don't: macOS turns a second program in the bundle down at once, without a prompt
(#60), so Stenny asks for them and posts them itself (src-tauri/src/macos_notifications.rs, the UserNotifications
framework): "Record this lecture?" with Start Recording, and its other messages.

On Windows the same icon sits in the notification area (white on a dark taskbar; the clock is its tooltip) and the app
does stenny-agent's work itself (src-tauri/src/windows_agent.rs, windows_pdf.rs), with Windows' own calls and no
helper program:

- **Pause shortcut:** `RegisterHotKey` on a thread of its own. `cmd` in the config is Ctrl, `opt` is Alt; the key is
  looked up in the current keyboard layout as printed on it, and without a Ü key the default (`cmd+shift+ü`) takes
  the key right of P, as on a Mac. Settings and the menu write it the Windows way (*Strg+Umschalt+Ü*). Tauri's
  global-shortcut plugin wasn't used: it takes physical key names (US positions), not the printed key.
- **Open at login:** the `Run` value in the user's registry, with `--autostart` so the library window stays closed;
  turned off in Task Manager ▸ Startup apps it counts as off. Not the autostart plugin: it writes the program's path
  without quotes, which breaks under "Program Files". An installed copy (under Program Files or
  `%LOCALAPPDATA%\Programs`, or next to its uninstaller) adds itself once, as on a Mac.
- **PDF for Share…:** WebView2's own `PrintToPdf` in a hidden window (A4, or Letter in the US and Canada; no header
  or footer), the same contract as stenny-agent's `--pdf`.
- **Calendar:** by link only (Settings ▸ Calendar ▸ Add a calendar link…, see "Calendar" below). An app without a
  package identity (MSIX) can't use Windows' calendar (`AppointmentManager`), and Outlook's COM interface only
  reaches classic Outlook, with security prompts.
- **"Record this lecture?":** a notification with a Start Recording button (`windows_agent::announce_lecture`,
  `tauri-winrt-notification`), under Stenny's AppUserModelID (the bundle identifier), which both installers put on
  the Start-menu shortcut; a build copy uses PowerShell's. The button's click only comes back while Stenny runs.
- Also: the beep when there's nothing to pause (`MessageBeep`), the clipboard for Share Stenny, Obsidian found by its
  `obsidian://` registration, one Stenny at a time (`tauri-plugin-single-instance`: starting it again brings the
  library window up), no app menu inside the windows, and the engine started without a console window.
`STENNY_TEST_FILE=<audio>` records that file instead of the microphone (with `STENNY_HOME` on a test copy).
The library window shows what's running in an activity bar across the list and detail (whichever course is open):
recording or paused with the menu bar's clock, the course · lecture and whether live text is on, Show (selects it),
Pause/Resume and Stop; then "Making the final transcript" (the live process's final pass, or one from the queue);
the calendar's lecture with a Record button while nothing runs. The recording course gets a red dot in the sidebar. A
course's list starts with "Record Lecture n", Talks & events (shown once there is a talk) with a record button: a sheet in the window asks for the talk's title and
language, as the menu bar's Record a Talk or Event… does (it brings the library up, "record-talk"). No window of its
own: on Windows a second window made from the event loop froze Stenny (lib.rs, `present`). The
window never records by itself: it goes through the menu bar's own code (`start_recording`, `recording_command`,
`recording_status` in src-tauri/src/lib.rs → menubar.rs `start_with`, `command`, `window_status`), so the lecturer's
consent, the microphone and the language work as from the menu; the status is the menu bar's last poll, no extra work.
The library window's footer opens Settings… in the same window (also ⌘, or Stenny ▸ Settings…; Ctrl+, on Windows) (src/lib/Settings.svelte, Escape goes back): general (language, open at login, the pause shortcut,
where the files are), recording (microphone, backup microphone, live draft, keeping audio), the AI step (Claude Code,
Anthropic API or Off, each with what it does and costs, and a bar under the chosen one when it isn't ready: install
or sign in to Claude Code, paste an API key, or what Off leaves out), Use with Claude (the
connector: installed in Claude Desktop or not, Install…, Add to Claude Code), background work (priority, power, night hours) and each course's language. Changes are saved at once:
config.toml through the engine (src/stenny/settings.py; the AI step's and the connector's status through
src/stenny/claude_setup.py, checked again whenever the window gets the focus back), the microphone and login item through the app (menubar.rs,
the same choices as the menu bar's; a new pause shortcut is registered by the running stenny-agent (`shortcut …`), or again on Windows). Also in the menu and the library
window's footer: Send Feedback… (to stenny.eu, like the website's form) and
Check for Updates… (see *Updates* below). An installed copy (in an Applications folder)
adds itself to the login items once, from its own process (src-tauri/src/macos.rs, SMAppService; on Windows: the Run
value, see above); after that it's the
user's choice.

## The engine inside the app

The app carries Stenny's Python engine (#92), so it needs no separate `uv tool install` and app and engine can't drift
apart. `scripts/bundle-engine.sh` builds it from this checkout into `src-tauri/engine`, which Tauri bundles as a
resource (tauri.conf.json): a standalone Python (python-build-standalone, pinned and checked against its SHA-256) with
the packages exactly as in uv.lock (mlx-whisper on a Mac, faster-whisper on Windows) and Stenny itself as a wheel.
Nothing is frozen, so mlx (Metal) and CTranslate2 load their libraries as always. It ends up in
`Stenny.app/Contents/Resources/engine` (macOS) or `engine\` next to Stenny.exe (Windows), and `engine.rs` runs it as
`<engine>/bin/python3 -m stenny app-server` (`python.exe` on Windows). Which engine the app runs, first found wins:
`$STENNY_CLI`, the engine inside the app (only with its `stenny-engine.json`: CI compiles with a placeholder), then
`~/.local/bin/stenny` for builds without one. The Whisper models stay downloads (src/stenny/paths.py, `models_dir`).

- **Size** (0.5.11, measured on a Mac): the engine is ~670 MB in ~16,000 files, Stenny.app ~695 MB; mlx (212 MB),
  llvmlite and numba (mlx-whisper's word timings, 155 MB) and scipy (98 MB) are most of it. torch is left out
  (pyproject.toml, `exclude-dependencies`): mlx-whisper lists it but only its model converter uses it (−530 MB).
- **Never written to:** every .pyc is compiled at build time ("unchecked-hash") and the app runs the engine with
  `PYTHONDONTWRITEBYTECODE`, so Python never writes into the app (which would break its signature). The Python's
  `site.py` has the user's own site-packages turned off, so nothing from another Python 3.12 can shadow the app's.
- **The engine knows it's the app's** (src/stenny/paths.py, `in_app()`): it finds the app's own ffmpeg, stenny-rec,
  stenny-agent and Stenny.mcpb first, the queue's background job and Claude Code's connector start the app's Python
  (the same path after every update), and `stenny update` only checks and says the app is what gets updated.
- **Claude Desktop's connector** (connector/server/index.js) runs the app's engine too: it looks for it in
  `Stenny.app` (~/Applications, then /Applications) or the Windows install folder (the installer's registry
  entry, `%LOCALAPPDATA%\Stenny`, `%ProgramFiles%\Stenny`) before any `stenny` command, and starts it the way
  the app does (no `PYTHONHOME`/`PYTHONPATH`, no bytecode written). `node connector/server/index.js --candidates`
  lists what it would try.
- **`stenny` in the terminal:** at each start the engine writes `~/.local/bin/stenny` (Windows: `stenny.cmd`), a
  launcher for itself, unless another `stenny` is there (a developer's `uv tool install -e .`), which it never
  replaces (src/stenny/launcher.py; `stenny uninstall` removes only its own).
- **The engine older setups installed** with `uv tool install -e` from ~/Applications/stenny.noindex (Windows:
  %LOCALAPPDATA%\Programs\stenny), up to 0.5.13, goes at the app's first start after the update
  (src/stenny/old_engine.py): `uv tool uninstall stenny`, the launcher in its place, the queue's background job and
  Claude Code's connector pointed at the app's engine, the folder to the Trash / Recycle Bin. Only that one: an
  engine installed from anywhere else (a checkout) stays, and never during a recording or a final pass. The install
  lines (scripts/install.sh, install.ps1) install only the app now and set it up with its own engine.
- **Signing on a Mac:** `codesign --deep` doesn't reach code among the resources, so `scripts/sign-mac.sh` signs the
  engine's ~165 native libraries and its Python one by one with the app's identity, then the app, and verifies all of
  it. `install-mac.sh` and `app/release.sh` use it.

## Updates

Check for Updates… uses Tauri's updater (src-tauri/src/updater.rs, #92): it reads `latest.json` from the newest
GitHub release (tauri.conf.json, `plugins.updater`), downloads that system's update and checks it against the
updater's public key (also in tauri.conf.json) before installing. App and engine are updated together, since the
engine is inside the app. It never installs during a recording or a final pass (the engine's `update_blocked`); if
one starts while it downloads, it waits until it's done.

- **Quietly, by itself:** a minute after Stenny starts and then twice a day (`updater::watch`), Stenny looks for a
  newer version; if there is one, the footer's Check for Updates… becomes *Update to 0.5.x…* (accent colour, the
  `update-available` event and the `update_available` command). Nothing pops up or downloads by itself, and it
  doesn't look while recording or making a final transcript (then an hour later).
- **Counted on stenny.eu (#130):** the first update address is `stenny.eu/update/{{target}}/{{arch}}/{{current_version}}`
  (stenny-website's `api/update-check.js`). It adds 1 to that day's count for the version and system (no ID, no IP
  address kept) and redirects to GitHub's `latest.json`. GitHub's address stays second in `endpoints`, so an
  unreachable or failing stenny.eu never stops an update (`CONNECT_TIMEOUT` in updater.rs; a test keeps GitHub last).
  The counts are update checks, not people: each Stenny checks at start and twice a day.

- **macOS, in place:** the plugin would swap the whole Stenny.app, which leaves the Dock's tile pointing at a bundle
  that's gone (two Stenny icons). So updater.rs unpacks Stenny.app.tar.gz next to the app, checks it with
  `codesign --verify --deep --strict`, copies it into the installed bundle with rsync (as install-mac.sh does) and
  restarts through LaunchServices. The archive is the app as signed with "Stenny Dev", so the microphone and
  calendar permissions stay.
- **Windows:** the plugin runs the new setup program passively (a progress window, no questions) after the engine
  is stopped; the setup program opens Stenny again.
- **Fallback:** if there's no `latest.json` (releases before the updater, or Windows' part still being built) or the
  updater fails, Check for Updates… does what it did before: the engine's version check and the install line in a
  window of its own (menubar.rs, `check_updates`).

**The updater key** signs every update; without it no installed Stenny can update any more, so keep a copy. Its
private half is `~/.tauri/stenny-updater.key` (+ `.password`) on the Mac that makes releases and the CI secrets
`TAURI_SIGNING_PRIVATE_KEY` / `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`; the public half is in tauri.conf.json. A release:
`app/release.sh --publish` makes `Stenny.app.tar.gz` from the signed app, signs it (`tauri signer sign`) and writes
`latest.json` with it (`scripts/latest-json.py`); CI's installer job then builds the Windows setup program with
`createUpdaterArtifacts` (only there, so ordinary builds need no key), uploads it and adds it to the release's
`latest.json`.

## The Windows installer

`npm run tauri build` on Windows makes two: the setup program (`bundle/nsis/Stenny_<version>_x64-setup.exe`, the one
to hand out) and an .msi (for administrators who install for everyone, into `%ProgramFiles%\Stenny`). Their settings
are in src-tauri/tauri.windows.conf.json: the setup program installs for the current user only, without administrator
rights, into `%LOCALAPPDATA%\Stenny` (Tauri's default, so next to the engine's own data; its uninstaller deletes only
the files it installed), in English or German after Windows' language, with Microsoft's WebView2 bootstrapper inside
(installs WebView2 only where it's missing). Before building, `scripts/ffmpeg-windows.sh` puts a pinned, checksummed
LGPL ffmpeg/ffprobe into src-tauri/binaries and their licence notice into src-tauri/resources (installed as
`ffmpeg-license.txt`), `../connector/build.sh` makes Stenny.mcpb and `scripts/bundle-engine.sh` the engine; CI's
installer job does all three (Git Bash). The engine finds the bundled ffmpeg next to Stenny.exe (src/stenny/paths.py,
`bundled()`).

**Not signed yet:** until SignPath signs a release (below), Windows SmartScreen warns about the setup program
("Windows protected your PC" ▸ *More info* ▸ *Run anyway*; SETUP.md says so). `bundle.windows.signCommand` /
`certificateThumbprint` stay unset: signing happens in CI, not in `tauri build`.

### Code signing

Windows releases are to be signed for free by [SignPath Foundation](https://signpath.org) (applied for; its
conditions: https://signpath.org/terms). Windows then names "SignPath Foundation" as the publisher. What their terms
ask of Stenny:

- **The code signing policy** on stenny.eu (`/code-signing/`, `/de/codesignatur/`; stenny-website,
  `docs-src/build_privacy.py`), linked as "Code signing policy" from the home page, the docs, the install page and
  each release's notes (app/release.sh). It names what is signed, the roles (committers/reviewers and the approver)
  and links the privacy page, whose app section lists every connection Stenny makes. **Keep that list true** when
  the app starts talking to anything new. Once a release is signed, `SIGNPATH = True` there switches the page to
  SignPath's own sentence.
- **Multi-factor authentication** for GitHub and SignPath, for everyone in a role.
- **Only Stenny's own programs are signed:** `Stenny.exe`, `stenny-rec.exe` and the setup program. Python (inside the
  engine) and ffmpeg go in as their projects publish them.
- **Product name "Stenny" and one version** in every signed file: tauri-build writes them into Stenny.exe and the
  setup program, and `stenny-rec/build.rs` writes them into stenny-rec.exe (from tauri.conf.json's `version`).
- **Every signing is approved by hand** in SignPath, and its files must come from CI's build of the tag (SignPath
  checks this with GitHub).

**In CI** (ci.yml, installer job, Windows, release tags only): with SignPath set up, `tauri build --no-bundle`
builds the programs; they go to SignPath as one artifact (artifact configuration `programs`) and come back signed;
`tauri bundle` makes the setup program from them; that goes to SignPath (`installer`); and only then is the updater's
signature made over the signed setup program (`tauri signer sign --app-version`), since Authenticode changes the
file. Each request waits up to two hours for its approval. Without SignPath (main, pull requests, or before it's set
up) the job builds the unsigned setup program in one `tauri build`, as before.

**Setting it up, once accepted:** in SignPath, a project `stenny` with the GitHub repository as its trusted build
system, a signing policy `release-signing`, and two artifact configurations, `programs` and `installer`, as in
`desktop/signpath/*.xml`. In GitHub, the repository variable `SIGNPATH_ORGANIZATION_ID` and the secret
`SIGNPATH_API_TOKEN` (a CI user's token, allowed to submit to that policy). The variable turns signing on.

## Calendar

Settings ▸ Calendar (`src/lib/Settings.svelte`, built like "Use with Claude") and the fourth step of the setup card
(`src/lib/SetupChecklist.svelte`) show what the engine's `calendar_status` says (`src/stenny/calendars.py`): counts
and names only, never a list of events. Checked when the section opens, when the window gets the focus back and when
the Mac's helper reports a change (the "calendar" event).

- **Two sources, one list.** On a Mac `stenny-agent` reads the calendars the Mac knows (EventKit) and sends
  `events […]` every 30 seconds; on both systems the engine's `calendar_events` gives the same list for the linked
  calendars (asked every 30 seconds by `menubar::poll_calendar_links`; a link is fetched again when its copy is five
  minutes old). `menubar::calendar` merges them (a lecture in both counts once, by title and start), matches titles
  to courses, sets the menu's suggestion and announces a lecture once: `macos_notifications::announce_lecture` on a
  Mac, `windows_agent::announce_lecture` on Windows. With `[app] reminders = false` nothing is announced; the menu's
  suggestion stays.
- **Permissions (macOS).** The agent writes `calendar-mac.json` into the data place (calendar access, the Mac's
  accounts with this term's titles) and says `calendar-state …` when that changes. `calendar check` (sent by the
  `calendar_status` and `calendar_check` commands) makes it look again; `calendar allow` asks macOS, which only works
  while it hasn't been answered. Notifications are the app's own (`macos_notifications`): `calendar_status` adds
  their state, Allow… calls `request`, and if macOS turns it down, its reason (`notifications_problem`) shows in the
  row instead of a silent click. The first time, the agent says `calendar-asked` after the calendar question and
  the app asks for notifications next. After that the buttons open System Settings (`open_system_settings`:
  Privacy & Security ▸ Calendars, Notifications, Internet Accounts).
- **Links** are secrets: one entry each in the Keychain / Credential Manager (`keychain.py`), fetched by curl, which
  reads the link on stdin; `calendars.json` in the data place only has the host and the calendar's name.
- **Notifications on Windows:** `windows_agent::notifications_state` (the toast notifier's setting); the button
  opens `ms-settings:notifications`.

## Language

The app is in English and German: every window, menu, dialog and notification, and the engine's messages. By
default it follows the system (the first of the user's preferred languages Stenny has, else English); Settings ▸
General ▸ Language picks *System default*, *English* or *Deutsch* (`[app] language` in config.toml) and every
window, the menu bar's menu, the app menu and stenny-agent follow at once (`set_ui_language` → the "language" event;
the agent gets `lang de` on its stdin and is never started again for it: a second process with Stenny's bundle id
shows up in the Dock as another Stenny, #46).

- **Texts:** `src/lib/locales/en.json` and `de.json`, one key per text, `{name}` placeholders; plurals as `….one` /
  `….other`; a `….windows` twin where Windows says it differently (Show in Explorer). Svelte: `t()` / `tn()` from
  `src/lib/i18n.svelte.ts`; Rust: `tr!` from `src-tauri/src/i18n.rs` (the same files, compiled in). The engine
  (`src/stenny/i18n.py`, `tr()` keyed by the English text) and stenny-agent (`--lang de`, later `lang de`, a handful of texts) mirror
  only what they show themselves. `tests/test_i18n.py` checks both files have the same keys and placeholders, that
  every key used exists, and that the engine's German is complete.
- **Not translated:** transcripts, summaries and file names in the vault (each course keeps its own language).
- **German:** natural UI German written for German speakers, never a word-for-word translation of the English.
  Write each text as a German app would say it, then check it against the English for meaning, not for shape:
  - the words macOS and Windows use for the same thing (*Im Finder anzeigen*, *Kurzbefehl*, *Anmeldeobjekte*,
    *Mitteilungen*, *am Netzteil*, *Wiederherstellen*; Voice Memos' *Pausieren / Fortsetzen*);
  - buttons and menu items as infinitive or noun (*Kurs hinzufügen*, not *Leg einen Kurs an*);
  - say what happens to what (*Der Kurs wird gelöscht*), not *Damit löschst du …* or a sentence starting with *Er …*;
  - short sentences; a dash for an aside (*– dort kannst du sie wiederherstellen*), not a semicolon chain;
  - the user is "du" everywhere; Apple's style for menus ("Einstellungen …", a space before the ellipsis).
  `tests/test_i18n.py` (`NOT_GERMAN`) catches the word-for-word phrasings the German once had.

One word for each thing, everywhere:

| English | Deutsch |
|---|---|
| lecture | Vorlesung |
| course | Kurs |
| talk / talks & events | Vortrag / Vorträge & Veranstaltungen |
| recording | Aufnahme |
| transcript | Transkript |
| summary | Zusammenfassung |
| glossary | Glossar |
| draft (live draft) | Entwurf (Live-Entwurf) |
| pause shortcut | Kurzbefehl zum Pausieren |
| Settings | Einstellungen |
| Trash / Recycle Bin | Papierkorb (both) |
| Show in Finder / Explorer | Im Finder / Explorer anzeigen |
| stop (a recording) / cancel (a recording) / quit (Stenny) | beenden / abbrechen / beenden |
| bring back (a course) | wiederherstellen |
| microphone / backup microphone / this Mac's microphone | Mikrofon / Ersatzmikrofon / Integriertes Mikrofon |
| lecturer / speaker | Lehrperson / vortragende Person |
| the Mac is plugged in | der Mac ist am Netzteil |
| on battery / the battery is low | im Akkubetrieb / der Akku ist fast leer |
