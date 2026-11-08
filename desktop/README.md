# Stenny's app (in development)

The new Stenny app: Tauri 2 (Rust shell) with a Svelte 5 + TypeScript interface in the stenny.eu style. It will
replace the Swift menu bar app in `app/`. Everything about lectures comes from the Python engine, through one
`stenny app-server` process (src/stenny/app_server.py) that the app starts and talks to in JSON lines
(src-tauri/src/engine.rs).

```bash
npm install                                   # once
scripts/helpers.sh                            # the Swift helpers it bundles: stenny-agent, stenny-rec
npm run check                                 # type check
npm run tauri build -- --debug --bundles app  # src-tauri/target/debug/bundle/macos/Stenny.app
open -n --env STENNY_CLI=../.venv/bin/stenny src-tauri/target/debug/bundle/macos/Stenny.app --args --library
STENNY_CLI=../.venv/bin/stenny npx vite dev --port 1420   # the interface in a browser, real engine (dev/engine-bridge.js)
```

`--library` runs the window without the menu bar icon, for trying a build next to the installed Stenny (never two
Stenny icons in the menu bar). `STENNY_CLI` picks the engine (default `~/.local/bin/stenny`); `STENNY_HOME` and
`STENNY_CONFIG` point it at a test copy of the data. Rust comes from rustup (`~/.cargo/bin`).

Without `--library` it is the menu bar app (src-tauri/src/menubar.rs): the classic menu, the clock next to the icon,
the calendar's lecture with its "Record this lecture?" notification, and the pause shortcut, the last two through
`stenny-agent` (app/Sources/stenny-agent), which runs inside the bundle so the permissions are Stenny's.
`STENNY_TEST_FILE=<audio>` records that file instead of the microphone (with `STENNY_HOME` on a test copy).
The library window's footer opens Settings… in the same window (src/lib/Settings.svelte, Escape goes back): general (open at login, the pause shortcut,
where the files are), recording (microphone, backup microphone, live draft, keeping audio), the AI step and talk
languages, background work (priority, power, night hours) and each course's language. Changes are saved at once:
config.toml through the engine (src/stenny/settings.py), the microphone and login item through the app (menubar.rs,
the same choices as the menu bar's; a new pause shortcut restarts stenny-agent). Also in the menu and the library
window's footer: Send Feedback… (to stenny.eu, like the website's form) and
Check for Updates… (installing comes with the app's own updater). An installed copy (in an Applications folder)
adds itself to the login items once, through stenny-agent; after that it's the user's choice.
