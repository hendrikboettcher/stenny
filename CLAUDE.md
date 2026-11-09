# CLAUDE.md

Developer notes for Stenny, a lecture transcription tool for **macOS and Windows**: university lectures (2–4 h, any language, sometimes mixed) → clean, lossless Markdown in a folder such as an Obsidian vault, to be used later as AI study context.

**Setting Stenny up for a user?** Follow [SETUP.md](SETUP.md) step by step instead of these notes.

**Platforms:** Stenny was first built for Apple Silicon Macs only and is now being made available for Windows too.
The new app (`desktop/`, Tauri) is the way there: **everything new must work on both macOS and Windows.** A native
feature gets its Windows counterpart in the same change (e.g. Share…: macOS's share menu in `macos.rs`, Windows'
Share panel in `windows_share.rs`), file paths and labels handle both ("Show in Finder" / "Show in Explorer"), and
whatever couldn't be tested on Windows is said so. Still Mac-only, each needing a Windows version: transcription
(mlx-whisper, Metal; faster-whisper is there for Windows but not yet tried on a PC), the microphone recorder
(`stenny-rec`; its WASAPI twin `stenny-rec.exe` in `desktop/stenny-rec` is there but not yet tried with a real
microphone), the calendar and its lecture notifications (`stenny-agent`; on Windows the app does the agent's other
work itself: pause shortcut, open at login, PDF for Share… in `windows_agent.rs` / `windows_pdf.rs`, not yet tried
on a PC), and the old Swift app in `app/`.

## Goals (in priority order)
1. **Nothing from the lecture is lost.** The raw Whisper output is always kept as its own file. Post-processing may structure the text but must never invent, drop or paraphrase content.
2. **High transcript quality** with a local Whisper model (default: large-v3-turbo through mlx-whisper on Apple Silicon; Windows needs its own engine behind the same interface).
3. **Nearly free.** Transcription is local. Optional LLM post-processing is cheap: Claude Code `claude -p` (subscription) or the Anthropic API with Claude Haiku. Target is under $5 for 100+ lectures.
4. **The computer stays usable.** Heavy work runs at low priority and can wait until it is on AC power or until night.
5. **Private.** Audio never leaves the machine. Only transcript text goes to the optional LLM step.

## Stack
- Python 3.12, uv, packaged as the `stenny` CLI (`uv tool install -e .`)
- mlx-whisper (Metal) for transcription on a Mac, faster-whisper (CTranslate2) on Windows; ffmpeg for audio decoding/encoding
- pypdf for slide text (dormant: slides are no longer part of Stenny, #20)
- Optional: `claude` CLI or the `anthropic` SDK for structuring and summaries
- `mcp` (the official MCP SDK) for the connector, `stenny mcp`
- pytest for tests

## Layout
```
src/stenny/
  cli.py          argparse entry point (`stenny …` subcommands)
  paths.py        the ONLY code that knows Stenny's own places (config, data, logs, cache, models) on macOS and
                  Windows; everyone else asks the engine (`stenny where --json`, `app-settings`)
  config.py       config.toml in the config place (or $STENNY_CONFIG): the Stenny folder, model, courses, shortcut
  audio.py        load/convert/archive audio via ffmpeg
  engines/        Whisper engines behind one interface: mlx (Mac), faster-whisper (Windows); `[transcription] engine`
  textproc.py     segment cleanup: hallucination filter, gap filling, paragraphs
  language.py     fixed or per-chunk language handling
  glossary.py     per-course glossary, Stenny's list of correct spellings: Whisper prompt (token budget) + word-for-word
                  corrections; fills itself from the student's fixes ("Always write it like this?" → their own sections)
                  and from Claude (connector → the "(added automatically)" sections, never over the student's)
  slides.py       dormant (#20): a lecture's slides PDF; nothing new may rely on it or point users to it
  course_files.py generated _Course Files/<Course> (overview).md (links only: each lecture's text exists once, in
                  its folder) and _Topics/<topic>.md; renumbers lectures, sets previous/next links
  share.py        the library (every lecture and talk, for Stenny's window) and clean copies to share
  word.py         Word copies (.docx) without textutil, for Windows: share.py's blocks as a few XML parts in a zip
  connector.py    what Claude may read through the connector, as clean text (no paths, no microphones): courses,
                  lectures, transcripts in parts, summaries, search, glossary; two writes, both to the glossary's automatic
                  sections (`suggest_glossary_terms`, `suggest_glossary_corrections`: taken as given if guard.check_term /
                  check_correction accept their shape; the student's own entries win)
  mcp_server.py   `stenny mcp`: connector.py as a local MCP server (stdio, official `mcp` SDK) + study instructions
  app_server.py   `stenny app-server`: the engine behind the new app (desktop/), JSON lines over stdio (library,
                  lecture, make_copy, finish_now); plain functions of (cfg, params), so a phone page can serve them too
  i18n.py         the engine's messages to the app in its language (English/German, `tr()`, keyed by the English
                  text); the app's own texts are desktop/src/lib/locales/*.json, shared by Svelte and Rust
  settings.py     the app's Settings: what it shows (`preferences`) and one change at a time
                  (`set_preference`, `set_course_language`); config.toml edited as text, never written if it won't load
  app_live.py     the app's recording methods (status, start → `stenny live` in its own process, pause/resume/stop,
                  microphone, devices, match_courses, recover) and the consent answers (consent.json next to config.toml)
  pipeline.py     audio → segments → paragraphs → files; `postprocess` (text + AI step) and `refresh` (AI step
                  again from segments.json, no re-transcription)
  llm/            backends (claude_code, anthropic_api, its key from keychain.py) + guard (no invented content) + guard (no invented content)
  keychain.py     the Anthropic API key in the system keychain (macOS: /usr/bin/security, Windows: keyring), never config.toml
  claude_setup.py what Settings checks and sets up for Claude, no tokens spent: is the AI step's option ready (Claude Code
                  installed + signed in, an API key Anthropic accepts), the connector in Claude Desktop / Claude Code
  render.py       Markdown + YAML frontmatter
  vault.py        safe writes into the Stenny folder (courses, Talks/, _Glossaries …), folder rule enforcement
  migrate.py      `stenny migrate`: an older install (~/Lectures Data, talks in <vault>/Recordings) into today's
                  places, once; the app runs it at start (`on_start`, also makes a config if there is none)
  jobs.py         queue, AC-power check, priority wrappers, keep awake; the queue's background job (launchd agent,
                  on Windows a Task Scheduler task started with pythonw.exe, `run_without_console`)
  winsys.py       Windows' own calls (ctypes, kernel32) behind jobs.py and resources.py: priority class and background
                  mode, AC power, SetThreadExecutionState, GlobalMemoryStatusEx
  live/           live mode: sources.py (mic/file via ffmpeg), session.py (record, chunk, draft,
                  pause/resume, spans → lecture time), control.py (JSON lines: a Unix socket, on Windows a named pipe)
app/              Swift menu bar app (SwiftPM + build.sh): starts `stenny live`, pause shortcut (`[app] pause_shortcut`,
                  read via `stenny app-settings`, default ⌘⇧Ü), status via socket; make-cert.sh: signing certificate;
                  also builds `stenny-rec`, the native microphone recorder bundled in Stenny.app;
                  build-ffmpeg.sh: the LGPL ffmpeg/ffprobe bundled there; install.sh: installs the built app;
                  release.sh: the release zip, which holds the signed app at app/build.noindex/Stenny.app
desktop/          the new app (in development, replaces app/): Tauri 2 + Svelte 5/TypeScript, stenny.eu style; talks to
                  the engine only through `stenny app-server` (src-tauri/src/engine.rs); see desktop/README.md.
                  Windows installer: tauri.windows.conf.json (per-user NSIS + MSI), scripts/ffmpeg-windows.sh
desktop/stenny-rec/  `stenny-rec.exe`, the microphone recorder on Windows (Rust, WASAPI): the Swift stenny-rec's
                  command line, file (16 kHz mono s16le, appended) and exit codes; stops on `stop` on stdin (Windows
                  has no SIGTERM), `--list-json` for the engine's device list; scripts/stenny-rec.sh bundles it
connector/        the Claude Desktop extension (.mcpb): manifest.json, server/index.js (finds the installed `stenny`
                  and runs `stenny mcp`, also `stenny.exe` on Windows), build.sh → dist/Stenny.mcpb (icon.png committed,
                  drawn by app/make-icon.swift: `build.sh --icon`)
tests/            pytest, no model downloads needed
bench/            engine benchmark scripts
```

Stenny folder (`folder` in config.toml, default `~/Documents/Stenny`, may be inside an Obsidian vault): `<folder>/<Course>/<YYYY-MM-DD>/<Course> <YYYY-MM-DD>{.md, (summary).md}`, talks in `<folder>/Talks/<Title>/<date>/` (`vault.TALKS_DIR`; a course can't be called that), plus `<folder>/_Glossaries/<Course> (glossary).md`, `<folder>/_Course Files/` and `<folder>/_Topics/`. **All file names come from `vault.LectureFiles`** (older lectures keep `Transcript.md` etc.). The files are meant as AI context: keep them self-describing (course, lecture number, date in frontmatter and title) and linked to each other.

## Rules
- **Never create, modify or delete anything outside the Stenny folder** (`folder` in config.toml; it may sit inside the user's Obsidian vault). All writes there go through `vault.py`.
- **Folder rule:** each folder contains either only folders or only files, never both. `vault.py` enforces this; don't bypass it.
- **Raw transcript is sacred:** the raw output (`Recordings/<Course>/… (raw).md` in Stenny's data place, outside the Stenny folder) is written before any post-processing and never changed by the LLM step.
- **The LLM never rewrites text.** It returns an edit list (section headings, term fixes) that `llm/guard.py` validates and applies. A term fix must point to a glossary term; the AI step never fixes a term from its own knowledge. Free-form LLM text appears only in `Summary.md`, clearly labelled. Topics don't change the text, but they need evidence (`guard.validate_topics`: a topic's words must be in the transcript). The AI step adds no glossary terms or corrections.
- **Slides are not part of Stenny** (#20): nothing users see (app, connector, docs, CLI help) mentions slides or assumes they exist. The slide code (`slides.py`, the hidden `--slides`) stays dormant; nothing new relies on it.
- **The glossary fills itself; the student's entries always win.** Its sources: the student's own fixes ("Always write it like this?" → `glossary.add_correction`, their Terms/Corrections) and Claude through the connector (taken as given, into the "(added automatically)" sections, never over the student's). Claude's and the student's corrections are applied word for word, so they must pass `guard.check_correction` (a spelling of a few words, never a common word).
- `segments.json` (in Recordings/, outside the Stenny folder) keeps every segment plus the lecture details (`meta.lecture`), so `stenny refresh` can rebuild everything after Whisper without the audio.
- **Only what you or Claude read goes into the Stenny folder** (transcripts, summaries, glossaries, course files, topic notes). Everything else lives in the system's app-data places, and only `paths.py` knows them: config.toml + consent.json in `~/Library/Application Support/Stenny/` (Windows `%APPDATA%\Stenny\`); `Recordings/<Course>/` (audio, segments.json, sessions, raw output) and `Queue/` in `~/Library/Application Support/Stenny/` (Windows `%LOCALAPPDATA%\Stenny\`, or `data_dir` in the config); logs in `~/Library/Logs/Stenny/` (Windows `%LOCALAPPDATA%\Stenny\Logs\`); shared copies in the caches folder. `STENNY_HOME` puts them all in one folder (tests, development). Never upload audio anywhere.
- **No hardcoded personal paths, names or courses in code, tests or docs.** Everything comes from the config; examples stay neutral.
- Heavy commands must respect the priority settings (`nice`/`taskpolicy`; on Windows the priority class, `winsys.py`) unless the user passes `--now`.
- **Microphone capture goes through `stenny-rec` (AVAudioEngine; on Windows `stenny-rec.exe`, WASAPI), never through Python** and not through ffmpeg's avfoundation input, which silently dropped ~1 s every ~8 s (nor ffmpeg's Windows inputs). The two recorders speak the same command line; change both together. Check every change to live mode with `stenny check`.
- **The backup microphone only fills "no input" gaps** of the main one (final pass, `fill_from_backup`); never mix it into audio the main microphone delivered. It stops on pause like the main one. Its text is marked `backup` in segments and paragraphs.
- **Pause means the microphone is off:** while paused, the audio source is stopped; never record or buffer paused audio.
- **The recording comes first (`resources.py`):** low memory costs the live draft, low disk the backup microphone, and a final pass waits for memory like it waits for power. A process that dies mustn't lose a lecture: `stenny recover` queues its final pass, and Stenny starts recording again without the draft.
- Live timestamps are lecture-clock time (pauses included); keep `spans`/`pauses` in session.json consistent with that.
- **The app speaks English and German.** New user-facing text goes into both `desktop/src/lib/locales/*.json`
  (Svelte `t()`, Rust `tr!`) or, for the engine, `tr()` with an entry in `i18n.DE`; German with "du" and the terms
  in desktop/README.md ("Language"). Vault content (transcripts, summaries, file names) is never translated.
- **One branch, `main`.** Work on a short branch, open a pull request into `main`; CI's quick tier (engine tests and
  the app compiled on macOS and Windows, English/German checked by tests/test_i18n.py) must pass before merging. A
  release is a tag `vX.Y.Z` on main, which also runs the full tier (installers built on both systems). There is no
  `dev` branch any more (merged into main on 2026-11-09).
- **Every change is for macOS and Windows, in English and German** (see Platforms at the top and the English/German rule above); a PR says
  what it couldn't test on Windows.
- **Keep README.md, SETUP.md and config.example.toml current** in the same commit as a change to a feature, a command, a setting or a storage location.
- Don't install large dependencies (>100 MB) or persistent system config without asking the user. The one exception: setup installs the queue's launchd agent (Windows: Task Scheduler task) by itself (`stenny schedule uninstall` removes it).

## Commands
```bash
uv run pytest                          # tests
uv run stenny add <audio> --course "<name>" [--lang fr] [--no-llm]
uv run stenny refresh "<course>" <YYYY-MM-DD[ (n)]>   # AI step again (glossary edits), no Whisper
uv run stenny courses                 # list configured courses
uv run stenny mcp                     # the connector for Claude (MCP over stdio); --desktop-config: its config entry
connector/build.sh                     # dist/Stenny.mcpb, the one-click Claude Desktop extension (macOS or Git Bash)
desktop/scripts/ffmpeg-windows.sh      # the pinned, checksummed LGPL ffmpeg/ffprobe for the Windows installer
uv run python bench/run_bench.py       # engine benchmark
uv run stenny live --course "<name>" --file sample.m4a --speed 3   # test live mode without a microphone
app/build.sh                           # build + install the menu bar app (--no-install: only build)
(cd desktop/stenny-rec && cargo test)  # the Windows recorder's tests (also on a Mac); desktop/scripts/stenny-rec.sh builds it on Windows
uv run stenny course add "<name>" -l en  # or `course hide`; config.toml stays as text, comments intact
uv run stenny recover                  # queue the final pass of recordings whose process died
uv run stenny where                    # where the Stenny folder, config, recordings and logs are (--json)
uv run stenny migrate --dry-run        # what moving an older install (~/Lectures Data) would do
app/release.sh                         # release zip; --publish tags and uploads it. Push and release together
```
