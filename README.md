# Stenny

Stenny records your lectures on a Mac and turns them into clean, searchable transcripts, so you can study them later with Claude. It runs in the menu bar: start a lecture, pause with one shortcut, stop. Whisper transcribes everything on your own Mac and saves Markdown files in a folder of your choice, for example an [Obsidian](https://obsidian.md) vault. Stenny's connector lets Claude read them in any chat.

- **Private:** transcription runs locally on the Mac's GPU (Whisper large-v3-turbo through mlx-whisper). Audio never leaves your Mac.
- **Nothing is lost:** every lecture keeps the raw Whisper output, unfiltered, next to its audio.
- **Nearly free:** transcription costs nothing. An optional AI step (section headings, a summary, checked fixes for misheard terms) runs through your Claude subscription (Claude Code) or the Anthropic API (about $0.01 per lecture). The AI never rewrites what was said.
- **Made for studying with Claude:** Stenny's connector lets any Claude chat search your lectures, read them and cite them as "Lecture 3 [0:12:30]".
- **Your Mac stays usable:** heavy work runs at low priority and can wait until the Mac is plugged in.

Website and docs: [stenny.eu](https://stenny.eu/docs/quickstart/)

> **Ask before you record.** Many universities need the lecturer's consent before a lecture is recorded. Check your university's rules and keep recordings and transcripts to yourself.

## Requirements

| | |
|---|---|
| Mac | Apple Silicon (M1 or newer), macOS 14.4 (Sonoma) or newer |
| Disk space | About 5 GB: the Whisper model (1.6 GB), the app and the tools. Recordings take about 1 GB per semester. |
| Memory | 2.5 to 3.5 GB free while transcribing |
| Claude | The Claude desktop app to study with your lectures, and Claude Code to set Stenny up |
| Optional | [Obsidian](https://obsidian.md) (free) to read the transcripts, and a calendar with your lectures |

Stenny comes as a finished app with its source code next to it. Changing the menu bar app's own code later needs Apple's Command Line Tools (about 1.8 GB), which Claude Code can install then.

**Windows (preview, not yet tested on a real PC):** Stenny's Windows version is being built ([#2](https://github.com/hendrikboettcher/stenny/issues/2)); some parts, such as recording from the microphone, may not be there yet.

| | |
|---|---|
| PC | Windows 10 (version 1809) or Windows 11, 64-bit Intel or AMD (not ARM) |
| Speed | With an NVIDIA graphics card (and its CUDA libraries) much faster than realtime; on the processor alone, transcribing takes about as long as the lecture |
| Disk space and memory | As on a Mac: about 5 GB, and 8 GB of memory or more |
| Claude | The Claude desktop app for Windows, and Claude Code (with Git for Windows) to set Stenny up |

## Setup

### Install

**On a Mac:** open Terminal (⌘Space, type *Terminal*, Return), paste this line and press Return:

```bash
curl -fsSL https://github.com/hendrikboettcher/stenny/releases/latest/download/install.sh | zsh
```

**On Windows:** open PowerShell (Start, type *PowerShell*, Enter), paste this line and press Enter:

```powershell
irm https://github.com/hendrikboettcher/stenny/releases/latest/download/install.ps1 | iex
```

That's all; it asks nothing and needs no password. It checks the computer, downloads Stenny's code (Mac: `~/Applications/stenny.noindex`, Windows: `%LOCALAPPDATA%\Programs\stenny`), installs [uv](https://docs.astral.sh/uv/) and the `stenny` engine with it, installs the app (Mac: `/Applications`, Windows: for your user only, into `%LOCALAPPDATA%\Stenny`), makes your Stenny folder (`~/Documents/Stenny`), adds the background job that finishes final transcripts, opens Stenny and downloads the Whisper model (1.6 GB, once). Stenny's window then lists what's left: your courses, Claude and your calendar. Running the line again updates Stenny; an older copy of the code goes to the Trash (Windows: Recycle Bin). The scripts are [scripts/install.sh](scripts/install.sh) and [scripts/install.ps1](scripts/install.ps1), if you'd like to read them first.

**Why a line in a terminal and not a download:** the Mac app is signed with the author's own certificate, not one from Apple, and the Windows app isn't signed yet. macOS refuses such an app when it was downloaded in a browser, and Windows says *"Windows protected your PC"*. Downloaded by the script, neither happens. The code is open, here.

To get a new version later: *Check for Updates…* at the bottom of Stenny's window, `stenny update` (Mac), or the line above again. Claude Code can still set Stenny up step by step with you, too: [SETUP.md](SETUP.md).

### Uninstall

On a Mac, in Terminal:

```bash
curl -fsSL https://github.com/hendrikboettcher/stenny/releases/latest/download/uninstall.sh | zsh
```

On Windows, in PowerShell:

```powershell
irm https://github.com/hendrikboettcher/stenny/releases/latest/download/uninstall.ps1 | iex
```

It quits Stenny and removes the app, the engine and its code, the background job, the connector in Claude Code (and on Windows in Claude Desktop), the saved API key and calendar links, the logs, the cache and the Whisper model. Stenny's settings and the recordings' audio go to the Trash (Windows: Recycle Bin), so you can still bring them back. **Your transcripts stay** in your Stenny folder, and uv stays too, in case other programs use it. In Claude Desktop on a Mac, remove the extension yourself: *Settings ▸ Extensions ▸ Stenny* (on Windows the uninstall takes Stenny out of Claude's settings itself). The scripts are [scripts/uninstall.sh](scripts/uninstall.sh) and [scripts/uninstall.ps1](scripts/uninstall.ps1); `stenny uninstall` alone shows what the engine part would remove.

### By hand

```bash
# 0. Download the code without a browser, so macOS doesn't block the app:
mkdir -p ~/Applications
curl -fL -o /tmp/Stenny.zip https://github.com/hendrikboettcher/stenny/releases/latest/download/Stenny.zip
ditto -x -k /tmp/Stenny.zip ~/Applications
cd ~/Applications/stenny.noindex

# 1. Tools
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install -e . --python 3.12   # puts `stenny` on your PATH (~/.local/bin)

# 2. The app
app/install.sh --no-open        # installs the finished Stenny.app to /Applications (or ~/Applications)

# 3. Settings
stenny init                     # your Stenny folder: ~/Documents/Stenny (or --folder "<any folder>")
open -e ~/"Library/Application Support/Stenny/config.toml"   # replace the example courses with yours

# 4. Whisper model (1.6 GB, once)
stenny download-model

open /Applications/Stenny.app   # allow microphone, calendar and notifications

# 5. The connector for Claude Desktop: click Install
open -a Claude Stenny.mcpb
```

**Language:** Stenny's windows, menus, dialogs and notifications are in English or German. By default they follow the system's language; *Settings… ▸ General ▸ Language* switches between *System default*, *English* and *Deutsch* at once, without restarting (`[app] language` in the config). Transcripts and summaries keep each course's own language.

See `config.example.toml` for every setting.

**The AI step** (section headings, checked fixes for misheard terms, topics, the summary) runs one of three ways, chosen in *Settings… ▸ Transcripts & AI*, which also says whether the chosen one works and how to fix it:

- **Claude Code** (default): your Claude subscription, no extra cost. Needs the `claude` command, which the Claude desktop app doesn't install: `curl -fsSL https://claude.ai/install.sh | bash`, then `claude auth login` once (Settings has a *Sign in…* button for that).
- **Anthropic API**: pay as you go with your own key, about $0.01 per lecture. Paste the key in Settings, or run `stenny api-key`: Stenny checks it with Anthropic (a free call) and keeps it in the macOS Keychain (Windows: Credential Manager), never in config.toml. `ANTHROPIC_API_KEY` still works and wins when it's set.
- **Off** (`backend = "none"`): everything stays offline. Transcripts stay complete, just without headings, term fixes, topics and summaries.

## Daily use

**Start:** click Stenny's stick figure in the menu bar ▸ *Start Lecture* ▸ your course. If your calendar has the lecture, Stenny offers it at the top of the menu from 10 minutes before, and a notification asks *Record this lecture?* with a **Start Recording** button. It never starts by itself. *Settings… ▸ Calendar* shows which calendars Stenny sees and how many of their events match your courses. If your lectures are in Google Calendar or Outlook in the browser, or in your university's timetable, paste that calendar's iCal link there (*Add a calendar link…*); on Windows that is the way to connect a calendar. The link is kept in the Keychain (Windows: Credential Manager), and Stenny only reads titles and times. Stenny's window starts it too: *Record Lecture 6* (the next number) at the top of a course's lectures, and, once you've recorded a talk, the record button next to *Talks & events* for another one. The first talk starts from the menu bar's *Record a Talk or Event…*. Either way Stenny's window asks for the talk's title and language.

**While recording:** the stick figure turns into a dot with the time; click it for the menu. With *Transcribe while recording* on (Settings ▸ Recording, the default), the text so far appears in Stenny's window as the lecture goes on, plugged in or on battery. Below 20 % battery it pauses and says so; the recording goes on, and the final transcript has everything. The open menu shows a level meter: green is fine, orange means the microphone is too far away.

**Pause:** press **⌘⇧Ü** (the default; choose your own in *Settings… ▸ General* or with `[app] pause_shortcut` in the config; on Windows ⌘ is Ctrl, so the default is **Ctrl+Shift+Ü**, or Ctrl+Shift with the key right of P on a keyboard without Ü). While paused, the microphone is really off and nothing is recorded.

**Stop:** click the dot ▸ *Stop & Make Final Transcript*. On power, the whole recording is transcribed again at full quality (a 3-hour lecture takes about 20–25 minutes) and replaces the draft. On battery it waits until you plug in, and starts as soon as you do while Stenny is open (otherwise within 10 minutes). Meanwhile Stenny's window shows the lecture as *Finishing* with a progress bar: *Transcribing* (how far into the lecture it has got), *Tidying up* (the clean transcript and the optional AI step), *Saving*, and, once it has measured the speed, about how long is left. A pass that was interrupted picks its bar up where it stopped.

**In the window:** while a lecture records, a slim bar across the top of Stenny's window shows it, whichever course is open: the time, the course and lecture, whether the live text is on, and *Show*, Pause and Stop (the same as the menu's). The recording course has a small red dot in the sidebar. After *Stop* the bar says *Making the final transcript* until it's done.

**Keep the lid open while recording.** A closed MacBook on battery sleeps and records nothing.

**Your lectures:** Stenny's menu ▸ *Open Stenny…* opens a window with your courses, each course's lectures (newest first) and the selected lecture's summary, with *Open Transcript* and *Show in Finder*. Click a lecture's title to rename it (a talk's renames the talk); the trash icon after its buttons moves it to the Trash (the Recycle Bin on Windows), after asking. Add a course with the **+** next to *Courses* (or *Add Course* in *Settings… ▸ Courses*); right-click a course, or use its ⋯ there, to remove or delete it. Removing deletes nothing: the course moves to *Past courses* in *Settings… ▸ Courses*, where *Bring Back* restores it and *Delete…* moves it and all its lectures to the Trash (the Recycle Bin on Windows). While a lecture records or its final transcript is being made, the window opens on it, with the text so far; otherwise where you left it. **⌘R** (Ctrl+R on Windows) or *View ▸ Reload* reloads the window if it ever looks stuck; a recording and a final transcript in progress go on untouched.

**Finish setting up Stenny:** until the four things that make Stenny really useful work (at least one course, the AI step, the connector in Claude, a calendar Stenny can read), a small card at the bottom of the window's sidebar shows how many are done. Click it for the steps, each with its action right there (*Add a course…*, *Sign in…*, *Install…*); a step counts as done when it really works, checked again whenever the window comes back to the front. Choosing *Off* for the AI step counts as done, and so does switching *Lecture reminders* off in Settings ▸ Calendar. The × hides the card until Stenny opens again; after a few times it also offers *Don't show this again*. Once everything works it says *You're all set* and is gone for good (`[app] setup_checklist = false`). Settings stays the full place for all of it.

**Share with classmates:** *Share…* in the window makes a clean copy as PDF, Word or Markdown (summary on top, if you like): no file paths, microphone names or Obsidian links. PDF and Word end with a small "Transcribed with Stenny · stenny.eu" line; Markdown doesn't. (Word files come from macOS's textutil on a Mac and from Stenny's own small writer on Windows, with Word's heading styles and real bullet lists.) The copies are saved in Stenny's caches folder (`~/Library/Caches/Stenny/Shared/`). Stenny asks once per course whether the lecturer is fine with it.

**Other things you can do:**
- **Use your iPhone as the microphone** (*Microphone ▸*, via Continuity) and put it closer to the lecturer. The Mac's own microphone records as a backup and fills the gaps when you use the phone.
- **Talks and events** that aren't courses: *Start Lecture ▸ Other…*. They go to the `Talks` folder in your Stenny folder.
- **Recordings from elsewhere** (Voice Memos, a file): `stenny add recording.m4a --course "Calculus"`.

### From the terminal

```bash
stenny add recording.m4a --course calc            # transcribe a file (low priority)
stenny add recording.m4a --course calc --now      # full speed
stenny add recording.m4a --course calc --later    # queue it: runs on power / at night
stenny refresh calc 2026-10-15                    # redo only the AI step (e.g. after editing the glossary)

stenny live --course calc                         # record without the menu bar app
stenny toggle | pause | resume | stop | status
stenny mic "MacBook Air Microphone"               # switch microphone during a recording
stenny devices                                    # list microphones
stenny check                                      # health check of the last recording

stenny courses                                    # list your courses
stenny library                                    # every lecture and talk with a transcript
stenny share calc 2026-10-15 --with-summary       # a clean copy to share (--format docx for Word)
stenny mcp --desktop-config                       # the connector for Claude Desktop (see Studying with Claude)
stenny api-key [status | remove]                  # the Anthropic API key, in the system keychain
stenny match-course "VL Calculus (Room 1.01)"     # which course a calendar event belongs to
stenny queue list | run                           # waiting final passes (run --lecture COURSE FOLDER: just that one, now)
stenny uninstall [--yes]                          # what Stenny set up here, removed (keeps transcripts; see Uninstall)
stenny schedule install                           # run the queue every 10 min when plugged in (launchd; Windows: Task Scheduler); with Stenny open, plugging in starts it at once
```

## Studying with Claude

### The Stenny connector

`stenny mcp` lets Claude Desktop read your lectures directly: no Filesystem connector, no project per course, no
instructions to paste. Any chat can then answer *"What did the lecturer say about k-means in lecture 4?"*. Claude
gets these tools:

- `list_courses`, `list_lectures`: your courses, and each lecture with its date, topics and section headings
- `search_lectures`: where something was said (lecture, date, timestamp, snippet). Automatic transcripts misspell
  terms, so a word that occurs nowhere is searched in its closest transcribed spelling ("dendrogram" → "dendogram")
- `get_transcript` (in parts of about 10k tokens; `at="1:05:30"` jumps to a passage), `get_summary`, `get_glossary`
- `suggest_glossary_corrections` and `suggest_glossary_terms`: the only tools that write, and only to a course's
  glossary. When Claude sees a name or term misheard, it adds `wrong → right` (applied word for word to new
  transcripts), or a correctly spelled term for Whisper's prompt. They're taken as Claude gives them, as long as
  they're the shape of a spelling (a few words, not a common word like "the"), and go into the glossary's
  "(added automatically)" sections. Your own terms and corrections are never changed and always win.

Everything Claude gets is clean text, like a shared copy: no paths on your Mac, no microphone names. The connector
also tells Claude how to study with Stenny (cite as "Lecture 3 [0:12:30]", check terms against the glossary), and
offers a *Study a course* prompt.

To add it, open Stenny's *Settings… ▸ Use with Claude* and click **Install…** (it also shows whether Claude has it,
and adds it to Claude Code with one more click). Or double-click `Stenny.mcpb` in Stenny's folder (it comes with the
download; `connector/build.sh` makes it from the code), or drag it onto Claude's Settings window, and click
**Install**. Setup does this for you. It shows up under Settings ▸ Customize ▸ Connectors.

On Windows the button is **Add to Claude**: nothing there opens a `.mcpb` with Claude reliably, so Stenny writes
itself into Claude's own settings file (`claude_desktop_config.json`, `mcpServers` ▸ `stenny`; `stenny mcp
--add-to-claude` does the same) and leaves everything else in it as it was. Quit Claude (right-click its icon at the
right of the taskbar ▸ Quit) and open it again; Stenny then shows up under Settings ▸ Developer. *Remove…* takes it
out again, and so does uninstalling Stenny. The extension starts the `stenny` installed on
your computer, so it always reads the same lectures as the app. It only works in the desktop app on this computer:
Claude on the web and on your phone can't reach it.

Developing the connector? Set `[connector] stenny` in config.toml to your copy's `.venv/bin/stenny` (see
config.example.toml), or put the entry `stenny mcp --desktop-config` prints into `mcpServers` in Claude Desktop's
`claude_desktop_config.json` while Claude Desktop is closed (it rewrites that file when it quits).

### Without the connector

The transcripts are plain Markdown files, so any tool that reads files can use them. In the Claude desktop app, the
**Filesystem** connector (Settings ▸ Connectors), limited to your Stenny folder, works
too. Then a project per course, with the block from `stenny project-instructions` pasted into its instructions, makes
its chats read the newest transcripts, cite them and keep the course glossary up to date.

## What ends up where

```
~/Documents/Stenny/                      your Stenny folder: everything you or Claude read (any folder, e.g. in a vault)
├── _Course Files/                       generated after every lecture, don't edit
│   └── Calculus (overview).md           every lecture with links to its transcript and summary, and its topics
├── _Glossaries/
│   └── Calculus (glossary).md           correct spellings for this course (fills itself)
├── _Topics/                             one note per topic, linking the lectures that cover it
├── Calculus/
│   └── 2026-10-15/
│       ├── Calculus 2026-10-15.md            clean transcript: frontmatter, sections, [h:mm:ss] per paragraph
│       └── Calculus 2026-10-15 (summary).md  AI summary with timestamps (optional)
└── Talks/                               talks and events (so no course can be called "Talks")

Stenny's own files, out of sight (`stenny where` lists them):
~/Library/Application Support/Stenny/     Windows: %APPDATA%\Stenny (config) and %LOCALAPPDATA%\Stenny (the rest)
├── config.toml, consent.json
├── Recordings/Calculus/                  audio (.opus, ~14 MB per hour), segments.json, raw Whisper output
│                                         (and, while a final pass runs, its .progress.json for the window)
├── Recordings/Talks/                     the same for talks
├── Queue/                                final passes waiting (e.g. on battery)
└── calendars.json, Calendars/            the linked calendars (host and name; the links themselves are in the
                                          Keychain / Credential Manager) and their last fetched copies
~/Library/Logs/Stenny/                    stenny.log, live.log, queue.log (Windows: %LOCALAPPDATA%\Stenny\Logs)
~/.cache/huggingface/                     the Whisper model (Windows: %LOCALAPPDATA%\Stenny\Models)
```

Time Machine backs up `~/Library`, so the recordings are in your Mac's backups. On Windows, File History doesn't back
up AppData: copy `%LOCALAPPDATA%\Stenny\Recordings` yourself if you want the audio backed up (`data_dir` in the
config moves it elsewhere). Older installs kept everything in `~/Lectures Data` and talks in
`<vault>/Recordings`; Stenny moves them on its first start (`stenny migrate --dry-run` shows what it does).
Earlier versions also wrote each course's transcripts and summaries a second time into `_Course Files/`
(`(all lectures)`, `(summaries)`); Stenny moves those copies to the Trash (the Recycle Bin on Windows) when it starts.
Every lecture keeps its own transcript and summary.

Stenny never writes outside the Stenny folder (if it is in your vault, nowhere else in the vault). Each folder holds either only folders or only files. A 1-hour lecture is about 11k tokens, so a semester of one course fits comfortably in a Claude project.

## Courses and languages

Each `[courses."Name"]` table in the config has a `language`:
- `"fr"`, `"en"`, `"de"`, …: a fixed language (best when a course is always in one language);
- `"auto"`: one language per lecture, detected among `languages`;
- `"mixed"`: detected per short piece (4–12 s) among `languages`, for lectures that switch language. With a fixed language, Whisper tends to *translate* other-language passages instead of transcribing them.

Each course also gets a glossary note (`_Glossaries/<Course> (glossary).md`): Stenny's list of correct spellings for the course. Its terms go into Whisper's prompt, and `wrong → right` corrections are applied word for word to new transcripts. You don't have to keep it; it fills itself from things that cost you nothing:
- **Your fixes.** When you correct a misheard name or term in a transcript in Stenny's window, Stenny asks once: *Always write "Bolzano-Weierstrass" like this in Data Analytics?* **Yes** adds the correction and the term to your own sections, so every lecture from now on gets it right; **Only here** leaves the glossary alone. Fixing ordinary words or rewording asks nothing.
- **Claude**, through Stenny's connector: corrections and terms it adds when it notices a misheard name, in the "(added automatically)" sections. Yours always win.

Stenny doesn't add terms from the transcripts by themselves any more: that mostly fed Whisper's own spelling of a misheard name back to it. Terms added that way earlier stay until you delete them; a correction removes a wrong one.

## How cleanup works (and why nothing is invented)

1. Audio is cut into chunks of at most 28 s at quiet moments and transcribed with the course glossary as Whisper's prompt.
2. Likely hallucinations (silence, glossary echoes, loops) are left out of the transcript but kept, marked ⚠, in the raw output.
3. Segments become paragraphs at pauses; glossary corrections are applied.
4. Optional AI step: the model sees numbered paragraphs and returns only a JSON edit list: section headings, term fixes and topics. A term fix is applied only if it points to a glossary term (never to a spelling the model thinks of itself), and a final word-level diff must show only those changes. Otherwise the AI's edits are discarded.
5. The summary is the only free-form AI text, in its own clearly labelled file.

## Limits

- **Distance matters.** From the back of a big lecture hall the final transcript is mostly right, with single-word errors; the live draft is much rougher. Put the phone near the lecturer.
- The live draft is for following along. Study from the final transcript.
- Closing the lid stops the recording (the Mac sleeps).
- Apple Silicon only: the transcription engine (MLX) doesn't run on Intel Macs.
- Tested mainly with French, English and German lectures.

## Speed (MacBook Air M4, large-v3-turbo)

| Engine | Speed | Peak RAM | WER (18 min of French speech) |
|---|---|---|---|
| **mlx-whisper, chunked + glossary (Stenny)** | **9.4× realtime** | ~3.4 GB | **2.7 %** |
| mlx-whisper, native long-form | 7.9× | 3.4 GB | 3.1 % |
| whisper.cpp (Metal, beam 5) | 6.1× | 2.2 GB | 3.2 % |
| faster-whisper (CPU int8) | 2.0× | 3.3 GB | 2.9 % |

German and English, measured the same way without a glossary (LibriVox recordings against their Project Gutenberg texts):

| Language | Recording | Length | WER |
|---|---|---|---|
| German | Kafka, *Ein Landarzt* | 18:02 | 3.1 % |
| English | O. Henry, *The Gift of the Magi* | 13:22 | 3.1 % |

At the default low priority Stenny runs at about 8× realtime. While recording, live mode uses about 2.5 GB of RAM and around 6 % of one CPU core.

**Windows (in development, #2):** the same model runs through faster-whisper (an NVIDIA GPU when CUDA is installed, else the CPU, int8), with the same decoding as on the Mac. Through Stenny's own chunked pipeline on the same clips, the two engines match: on six one-minute lecture-style clips in English, German and French (synthetic speech, clean and with room echo and noise), the word error rate was the same on five and differed by one word on the sixth. On a Mac's CPU faster-whisper runs at about 1.4× realtime, so a CPU-only Windows PC needs about as long as the lecture, or longer; an NVIDIA GPU is much faster. Not yet measured on a Windows PC. `[transcription] engine` picks one by hand (`auto`, `mlx`, `faster-whisper`).

**Background work on Windows (in development, #2):** `low` priority is Windows' below-normal priority class, `background` the idle class (programs Stenny starts, such as ffmpeg, inherit both). While Stenny records or makes a final transcript, the PC doesn't go to sleep when idle (closing a laptop's lid still sleeps). Memory counts as low from 90 % in use: the live text pauses, and a final transcript waits. `stenny schedule install` adds the task *Stenny Queue* to Task Scheduler, for your user and without admin rights: every 10 minutes while you're signed in, without a window; whether it waits for power or the night is read from the config each time, as on the Mac. Tested on GitHub's Windows runners, not yet on a Windows PC.

**Recording on Windows (in development, #2):** the microphone is recorded by `stenny-rec.exe` (desktop/stenny-rec), the Windows twin of the Mac's recorder: WASAPI in shared mode, event-driven with a one-second buffer, Windows' own resampler for 16 kHz mono, and the same file, pause (the microphone is off) and backup microphone as on the Mac. `stenny devices` and Settings list the microphones through it, without the virtual ones apps install (Microsoft Teams Audio, virtual cables); the backup microphone's "auto" takes the PC's own microphone. If Windows blocks the microphone, turn on *Settings ▸ Privacy & security ▸ Microphone ▸ Let desktop apps access your microphone*. Not yet tried with a real microphone on a Windows PC.

## Development

```bash
uv run pytest                          # tests, no model download needed
uv run stenny live --course calc --file sample.m4a --speed 3   # try live mode without a microphone
stenny course add "Name" -l en        # or the + next to Courses in Stenny's window
app/make-cert.sh                       # once: a signing certificate, so macOS remembers permissions across builds
app/build.sh                           # build the app and install it (needs Apple's Command Line Tools; --no-install: only build)
app/release.sh                         # dist/Stenny.zip for a release (--publish: also tag and upload it)
connector/build.sh                     # dist/Stenny.mcpb, the connector (macOS or Git Bash on Windows; --icon: redraw its icon)
desktop/scripts/ffmpeg-windows.sh      # the pinned LGPL ffmpeg/ffprobe for the Windows installer (CI does this)
(cd desktop/stenny-rec && cargo test)  # the Windows microphone recorder's tests (run on a Mac too)
bash desktop/scripts/stenny-rec.sh     # on Windows (Git Bash): build stenny-rec.exe for the app's bundle
uv run python bench/run_bench.py --help
```

See [CLAUDE.md](CLAUDE.md) for the architecture and the rules the code follows.

## License

[MIT](LICENSE).
