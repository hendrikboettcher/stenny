# Stenny's app (in development)

The new Stenny app: Tauri 2 (Rust shell) with a Svelte 5 + TypeScript interface in the stenny.eu style. It will
replace the Swift menu bar app in `app/`. Everything about lectures comes from the Python engine, through one
`stenny app-server` process (src/stenny/app_server.py) that the app starts and talks to in JSON lines
(src-tauri/src/engine.rs).

```bash
npm install                                   # once
scripts/helpers.sh                            # what it bundles: stenny-agent, stenny-rec, Stenny.mcpb (needs Node)
npm run check                                 # type check
npm run tauri build -- --debug --bundles app  # src-tauri/target/debug/bundle/macos/Stenny.app
open -n --env STENNY_CLI=../.venv/bin/stenny src-tauri/target/debug/bundle/macos/Stenny.app --args --library
STENNY_CLI=../.venv/bin/stenny npx vite dev --port 1420   # the interface in a browser, real engine (dev/engine-bridge.js)
```

`--library` runs the window without the menu bar icon, for trying a build next to the installed Stenny (never two
Stenny icons in the menu bar). `STENNY_CLI` picks the engine (default `~/.local/bin/stenny`); `STENNY_HOME` puts all of
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
the calendar's lecture with its "Record this lecture?" notification, and the pause shortcut, the last two through
`stenny-agent` (app/Sources/stenny-agent), which runs inside the bundle so the permissions are Stenny's.
`STENNY_TEST_FILE=<audio>` records that file instead of the microphone (with `STENNY_HOME` on a test copy).
The library window's footer opens Settings… in the same window (also ⌘, or Stenny ▸ Settings…; Ctrl+, on Windows) (src/lib/Settings.svelte, Escape goes back): general (language, open at login, the pause shortcut,
where the files are), recording (microphone, backup microphone, live draft, keeping audio), the AI step (Claude Code,
Anthropic API or Off, each with what it does and costs, and a bar under the chosen one when it isn't ready: install
or sign in to Claude Code, paste an API key, or what Off leaves out), Use with Claude (the
connector: installed in Claude Desktop or not, Install…, Add to Claude Code), background work (priority, power, night hours) and each course's language. Changes are saved at once:
config.toml through the engine (src/stenny/settings.py; the AI step's and the connector's status through
src/stenny/claude_setup.py, checked again whenever the window gets the focus back), the microphone and login item through the app (menubar.rs,
the same choices as the menu bar's; a new pause shortcut restarts stenny-agent). Also in the menu and the library
window's footer: Send Feedback… (to stenny.eu, like the website's form) and
Check for Updates… (installing comes with the app's own updater). An installed copy (in an Applications folder)
adds itself to the login items once, through stenny-agent; after that it's the user's choice.

## Language

The app is in English and German: every window, menu, dialog and notification, and the engine's messages. By
default it follows the system (the first of the user's preferred languages Stenny has, else English); Settings ▸
General ▸ Language picks *System default*, *English* or *Deutsch* (`[app] language` in config.toml) and every
window, the menu bar's menu, the app menu and stenny-agent follow at once (`set_ui_language` → the "language" event).

- **Texts:** `src/lib/locales/en.json` and `de.json`, one key per text, `{name}` placeholders; plurals as `….one` /
  `….other`; a `….windows` twin where Windows says it differently (Show in Explorer). Svelte: `t()` / `tn()` from
  `src/lib/i18n.svelte.ts`; Rust: `tr!` from `src-tauri/src/i18n.rs` (the same files, compiled in). The engine
  (`src/stenny/i18n.py`, `tr()` keyed by the English text) and stenny-agent (`--lang de`, a handful of texts) mirror
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
| slides | Folien |
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
