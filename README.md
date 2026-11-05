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

## Setup

### The easy way: let Claude Code do it

Paste this sentence into [Claude Code](https://claude.com/claude-code) (in the Claude desktop app's Code tab, or in the terminal):

```
Install Stenny on my Mac by following https://github.com/hendrikboettcher/stenny/blob/main/SETUP.md
```

That's all. Claude Code downloads Stenny to `~/Applications/stenny.noindex`, checks your Mac, installs what's missing and the menu bar app, asks where your transcripts should go and which courses you take, downloads the Whisper model, makes a test recording with you and adds the Stenny connector to Claude. It explains each step and asks before anything important. [SETUP.md](SETUP.md) is the exact script it follows; you can read it first.

The app is signed with the author's own certificate, not one from Apple, so macOS refuses a copy downloaded in a browser. Claude Code's download doesn't have that problem. To get a new version later: Stenny's menu ▸ *Check for Updates…* (or `stenny update`).

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

# 2. Menu bar app
app/install.sh --no-open        # installs the finished Stenny.app to /Applications (or ~/Applications)

# 3. Settings
stenny init --vault "~/Documents/My Vault"    # any folder works; Obsidian is optional
open -e ~/"Lectures Data/_System/config.toml" # replace the example courses with yours

# 4. Whisper model (1.6 GB, once)
stenny download-model

open /Applications/Stenny.app   # allow microphone, calendar and notifications

# 5. The connector for Claude Desktop: click Install
open -a Claude Stenny.mcpb
```

See `config.example.toml` for every setting. The AI step uses the `claude` command (Claude Code in the terminal) by default; the Claude desktop app doesn't install it, so run `curl -fsSL https://claude.ai/install.sh | bash` and `claude auth login` once. For the API instead: `uv tool install -e '.[api]' --python 3.12`, set `ANTHROPIC_API_KEY` and `[llm] backend = "api"`. With `backend = "none"` everything stays offline.

## Daily use

**Start:** click Stenny's stick figure in the menu bar ▸ *Start Lecture* ▸ your course. If your calendar has the lecture, Stenny offers it at the top of the menu from 10 minutes before, and a notification asks *Record this lecture?* with a **Start Recording** button. It never starts by itself.

**While recording:** the stick figure turns into a dot with the time; click it for the menu. The live draft appears in the transcript file about 10 seconds behind the lecturer (*Open Transcript*). The open menu shows a level meter: green is fine, orange means the microphone is too far away.

**Pause:** press **⌘⇧Ü** (the default; choose your own with `[app] pause_shortcut` in the config). While paused, the microphone is really off and nothing is recorded.

**Stop:** click the dot ▸ *Stop & Make Final Transcript*. On power, the whole recording is transcribed again at full quality (a 3-hour lecture takes about 20–25 minutes) and replaces the draft. On battery it waits until you plug in.

**Keep the lid open while recording.** A closed MacBook on battery sleeps and records nothing.

**Your lectures:** Stenny's menu ▸ *Open Stenny…* opens a window with your courses, each course's lectures (newest first) and the selected lecture's summary, with *Open Transcript* and *Show in Finder*. Add a course with the **+** next to *Courses*; right-click a course to remove it. Removing deletes nothing: its transcripts move to *Past Courses*, and *Bring Back to the Menu* restores it.

**Share with classmates:** *Share…* in the window makes a clean copy as PDF, Word or Markdown (summary on top, if you like): no file paths, microphone names or Obsidian links. PDF and Word end with a small "Transcribed with Stenny · stenny.eu" line; Markdown doesn't. The copies are saved in `~/Lectures Data/_Shared/`. Stenny asks once per course whether the lecturer is fine with it.

**Other things you can do:**
- **Use your iPhone as the microphone** (*Microphone ▸*, via Continuity) and put it closer to the lecturer. The Mac's own microphone records as a backup and fills the gaps when you use the phone.
- **Add the slides** (a PDF dropped into the lecture folder, or `--slides deck.pdf`): better spelling of names and terms, and headings that link to the slide pages.
- **Talks and events** that aren't courses: *Start Lecture ▸ Other…*. They go to their own `Recordings` folder.
- **Recordings from elsewhere** (Voice Memos, a file): `stenny add recording.m4a --course "Calculus"`.

### From the terminal

```bash
stenny add recording.m4a --course calc            # transcribe a file (low priority)
stenny add recording.m4a --course calc --now      # full speed
stenny add recording.m4a --course calc --later    # queue it: runs on power / at night
stenny add recording.m4a --course calc --slides "Week 4.pdf"
stenny refresh calc 2026-10-15                    # redo only the AI step (e.g. after adding slides)

stenny live --course calc                         # record without the menu bar app
stenny toggle | pause | resume | stop | status
stenny mic "MacBook Air Microphone"               # switch microphone during a recording
stenny devices                                    # list microphones
stenny check                                      # health check of the last recording

stenny courses                                    # list your courses
stenny library                                    # every lecture and talk with a transcript
stenny share calc 2026-10-15 --with-summary       # a clean copy to share (--format docx for Word)
stenny mcp --desktop-config                       # the connector for Claude Desktop (see Studying with Claude)
stenny match-course "VL Calculus (Room 1.01)"     # which course a calendar event belongs to
stenny queue list | run                           # waiting final passes
stenny schedule install                           # run the queue every 10 min when plugged in (launchd)
```

## Studying with Claude

### The Stenny connector

`stenny mcp` lets Claude Desktop read your lectures directly: no Filesystem connector, no project per course, no
instructions to paste. Any chat can then answer *"What did the lecturer say about k-means in lecture 4?"*. Claude
gets these tools:

- `list_courses`, `list_lectures`: your courses, and each lecture with its date, topics and section headings
- `search_lectures`: where something was said (lecture, date, timestamp, snippet). Automatic transcripts misspell
  terms, so a word that occurs nowhere is searched in its closest transcribed spelling ("dendrogram" → "dendogram")
- `get_transcript` (in parts of about 10k tokens; `at="1:05:30"` jumps to a passage), `get_summary`,
  `get_slides_text`, `get_glossary`
- `suggest_glossary_terms`: the one tool that writes. It adds terms to the glossary's "Terms (added
  automatically)" section only if they are on the slides or were transcribed the same way twice (the same check as
  after every lecture). Corrections and your own terms stay yours to edit.

Everything Claude gets is clean text, like a shared copy: no paths on your Mac, no microphone names. The connector
also tells Claude how to study with Stenny (cite as "Lecture 3 [0:12:30]", check terms against the glossary and the
slides), and offers a *Study a course* prompt.

To add it, double-click `Stenny.mcpb` in Stenny's folder (it comes with the download;
`connector/build.sh` makes it from the code), or drag it onto Claude's Settings window, and click **Install**. Setup
does this for you. It shows up under Settings ▸ Customize ▸ Connectors. The extension starts the `stenny` installed on
your computer, so it always reads the same lectures as the app. It only works in the desktop app on this computer:
Claude on the web and on your phone can't reach it.

Developing the connector? Set `[connector] stenny` in config.toml to your copy's `.venv/bin/stenny` (see
config.example.toml), or put the entry `stenny mcp --desktop-config` prints into `mcpServers` in Claude Desktop's
`claude_desktop_config.json` while Claude Desktop is closed (it rewrites that file when it quits).

### Without the connector

The transcripts are plain Markdown files, so any tool that reads files can use them. In the Claude desktop app, the
**Filesystem** connector (Settings ▸ Connectors), limited to your lectures folder and the `Recordings` folder, works
too. Then a project per course, with the block from `stenny project-instructions` pasted into its instructions, makes
its chats read the newest transcripts, cite them and keep the course glossary up to date.

## What ends up where

```
<your vault>/Lectures/
├── _Course Files/                       generated after every lecture, don't edit
│   ├── Calculus (overview).md           every lecture with links and topics
│   ├── Calculus (all lectures).md       every final transcript of the course, with contents
│   └── Calculus (summaries).md          every summary
├── _Glossaries/
│   └── Calculus (glossary).md           terms + corrections for this course (partly filled automatically)
├── _Topics/                             one note per topic, linking the lectures that cover it
└── Calculus/
    └── 2026-10-15/
        ├── Calculus 2026-10-15.md            clean transcript: frontmatter, sections, [h:mm:ss] per paragraph
        ├── Calculus 2026-10-15 (summary).md  AI summary with timestamps (optional)
        └── Calculus 2026-10-15 (slides).pdf  the slides (optional)

~/Lectures Data/                          everything that isn't for reading
├── Calculus/                             audio (.opus, ~14 MB per hour), segments.json, raw Whisper output
├── _Queue/                               final passes waiting (e.g. on battery)
└── _System/                              config.toml, logs
```

Stenny never writes anywhere else in your vault. Each folder holds either only folders or only files. A 1-hour lecture is about 11k tokens, so a semester of one course fits comfortably in a Claude project.

## Courses and languages

Each `[courses."Name"]` table in the config has a `language`:
- `"fr"`, `"en"`, `"de"`, …: a fixed language (best when a course is always in one language);
- `"auto"`: one language per lecture, detected among `languages`;
- `"mixed"`: detected per short piece (4–12 s) among `languages`, for lectures that switch language. With a fixed language, Whisper tends to *translate* other-language passages instead of transcribing them.

Each course also gets a glossary note (`_Glossaries/<Course> (glossary).md`). Its terms go into Whisper's prompt, and `wrong → right` corrections are applied word for word. It fills itself over time: terms that are on the slides, or were transcribed the same way twice, are added automatically.

## How cleanup works (and why nothing is invented)

1. Audio is cut into chunks of at most 28 s at quiet moments and transcribed with the course glossary as Whisper's prompt.
2. Likely hallucinations (silence, glossary echoes, loops) are left out of the transcript but kept, marked ⚠, in the raw output.
3. Segments become paragraphs at pauses; glossary corrections are applied.
4. Optional AI step: the model sees numbered paragraphs and the slide text and returns only a JSON edit list: section headings (with slide pages), term fixes, topics and glossary terms. A term fix is applied only if it points to a glossary term or to words found exactly on the slides, and a final word-level diff must show only those changes. Otherwise the AI's edits are discarded.
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

## Development

```bash
uv run pytest                          # tests, no model download needed
uv run stenny live --course calc --file sample.m4a --speed 3   # try live mode without a microphone
stenny course add "Name" -l en        # or the + next to Courses in Stenny's window
app/make-cert.sh                       # once: a signing certificate, so macOS remembers permissions across builds
app/build.sh                           # build the app and install it (needs Apple's Command Line Tools; --no-install: only build)
app/release.sh                         # dist/Stenny.zip for a release (--publish: also tag and upload it)
uv run python bench/run_bench.py --help
```

See [CLAUDE.md](CLAUDE.md) for the architecture and the rules the code follows.

## License

[MIT](LICENSE).
