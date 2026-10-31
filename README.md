# Stenny

Stenny records your lectures on a Mac and turns them into clean, searchable transcripts, so you can study them later with Claude. It runs in the menu bar: start a lecture, pause with one shortcut, stop. Whisper transcribes everything on your own Mac and saves Markdown files in a folder of your choice, for example an [Obsidian](https://obsidian.md) vault.

- **Private:** transcription runs locally on the Mac's GPU (Whisper large-v3-turbo through mlx-whisper). Audio never leaves your Mac.
- **Nothing is lost:** every lecture keeps the raw Whisper output, unfiltered, next to its audio.
- **Nearly free:** transcription costs nothing. An optional AI step (section headings, a summary, checked fixes for misheard terms) runs through your Claude subscription (Claude Code) or the Anthropic API (about $0.01 per lecture). The AI never rewrites what was said.
- **Made for studying with Claude:** the files are named, numbered and linked so that a Claude project can read a whole course at once.
- **Your Mac stays usable:** heavy work runs at low priority and can wait until the Mac is plugged in.

Website and docs: [stenny.eu](https://stenny.eu/docs/quickstart/)

> **Ask before you record.** Many universities need the lecturer's consent before a lecture is recorded. Check your university's rules and keep recordings and transcripts to yourself.

## Requirements

| | |
|---|---|
| Mac | Apple Silicon (M1 or newer), macOS 14.4 (Sonoma) or newer |
| Disk space | About 5 GB: the Whisper model (1.6 GB), Apple's Command Line Tools (about 1.8 GB, if you don't have them yet) and the tools. Recordings take about 1 GB per semester. |
| Memory | 2.5 to 3.5 GB free while transcribing |
| Optional | [Obsidian](https://obsidian.md) (free) to read the transcripts, a calendar with your lectures, and Claude (Claude Code or an API key) for the AI step |

Stenny comes as source code, not as a finished app. You build it on your own Mac, so you need no Apple developer account and macOS doesn't block it.

## Setup

### The easy way: let Claude Code do it

Paste this sentence into [Claude Code](https://claude.com/claude-code) (in the Claude desktop app's Code tab, or in the terminal):

```
Install Stenny on my Mac by following https://github.com/hendrikboettcher/stenny/blob/main/SETUP.md
```

That's all. Claude Code downloads Stenny to `~/Applications/stenny.noindex`, checks your Mac, installs what's missing and the menu bar app, asks where your transcripts should go and which courses you take, downloads the Whisper model, makes a test recording with you and helps you set up a Claude project per course. It explains each step and asks before anything important. [SETUP.md](SETUP.md) is the exact script it follows; you can read it first.

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
```

See `config.example.toml` for every setting. The AI step uses the `claude` command (Claude Code in the terminal) by default; the Claude desktop app doesn't install it, so run `curl -fsSL https://claude.ai/install.sh | bash` and `claude auth login` once. For the API instead: `uv tool install -e '.[api]' --python 3.12`, set `ANTHROPIC_API_KEY` and `[llm] backend = "api"`. With `backend = "none"` everything stays offline.

## Daily use

**Start:** click Stenny's stick figure in the menu bar ▸ *Start Lecture* ▸ your course. If your calendar has the lecture, Stenny offers it at the top of the menu from 10 minutes before, and a notification asks *Record this lecture?* with a **Start Recording** button. It never starts by itself.

**While recording:** the stick figure turns into a dot with the time; click it for the menu. The live draft appears in the transcript file about 10 seconds behind the lecturer (*Open Transcript*). The open menu shows a level meter: green is fine, orange means the microphone is too far away.

**Pause:** press **⌘⇧Ü** (the default; choose your own with `[app] pause_shortcut` in the config). While paused, the microphone is really off and nothing is recorded.

**Stop:** click the dot ▸ *Stop & Make Final Transcript*. On power, the whole recording is transcribed again at full quality (a 3-hour lecture takes about 20–25 minutes) and replaces the draft. On battery it waits until you plug in.

**Keep the lid open while recording.** A closed MacBook on battery sleeps and records nothing.

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
stenny match-course "VL Calculus (Room 1.01)"     # which course a calendar event belongs to
stenny queue list | run                           # waiting final passes
stenny schedule install                           # run the queue every 10 min when plugged in (launchd)
```

## Studying with Claude

The transcripts are meant as context for Claude. The least work is one Claude project per course that reads them straight from your Mac:

1. In the Claude desktop app, turn on the **Filesystem** connector (Settings ▸ Connectors) and give it your lectures folder (and the `Recordings` folder, for talks).
2. Create a project for each course.
3. Run `stenny project-instructions` and paste each course's block into that project's instructions.

Every chat in the project can then read the newest transcripts and summaries, cite them as "Lecture 3 [0:12:30]", and keep the course glossary up to date. Claude only changes the glossary, never the transcripts.

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
stenny course add "Name" -l en        # or Stenny's menu: Start Lecture ▸ Add Course…
app/make-cert.sh                       # once: a signing certificate, so macOS remembers permissions across builds
app/build.sh                           # build the app and install it (needs Apple's Command Line Tools; --no-install: only build)
app/release.sh                         # dist/Stenny.zip for a release (--publish: also tag and upload it)
uv run python bench/run_bench.py --help
```

See [CLAUDE.md](CLAUDE.md) for the architecture and the rules the code follows.

## License

[MIT](LICENSE).
