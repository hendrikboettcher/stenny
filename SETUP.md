# Setting up Stenny (instructions for Claude Code)

**For the person reading this:** you don't have to do anything here yourself. Paste this sentence into Claude Code:

> Install Stenny on my Mac by following https://github.com/hendrikboettcher/stenny/blob/main/SETUP.md

(On a Windows PC: "Install Stenny on my PC by following …", the same link.)

Claude Code will go through the steps below with you and ask whenever it needs a decision or a click.

---

**For Claude Code.** You are setting up Stenny for someone who may never have used a terminal. Work through the steps below in order, together with them.

**On Windows,** follow [Stenny on Windows](#stenny-on-windows-preview) at the end instead of Phases A–C. The rules under *How to work* apply there too, with Windows' apps instead of macOS's.

How to work:
- **Short and engaging.** Nobody wants to read for hours. Say a sentence or two per step: only what helps them decide or click. No walls of text, no jargon. These instructions are detailed; what you say to the user isn't.
- **One step at a time.** Run the step, then run its **Check**. Only go on when the check passes. If it fails, look at the *If it fails* notes, then the *Troubleshooting* section at the end. If you're still stuck, explain the problem simply and ask the user how to go on. Never skip a failed step silently.
- **Check what's already there first.** Many Macs already have some of these tools. If something is installed and works, say so in one line and go on. Never reinstall or remove something that works.
- **Ask, don't guess,** for anything personal: the folder for transcripts, courses, languages, the shortcut.
- **Every question has a recommended answer.** Ask one thing at a time. If you have a tool for questions with clickable options (such as AskUserQuestion), use it for every choice: at most four options, your recommendation first and marked "(Recommended)", with a short reason; the user can always type something else. Ask for free text only where nothing can be suggested. Never make the user write long answers.
- **Always name the app when you open something.** A plain `open <link or file>` lets macOS choose, and on a fresh account that can be any installed app that claims the type. Use `open -a Obsidian …`, `open -a TextEdit …` and so on. Web pages (`open https://…`) may go to the default browser.
- **Keep the user out of the Terminal.** Run every command yourself. The user only clicks: macOS dialogs, System Settings, the browser, the Claude app. Nothing in this setup needs their Mac password.
- **Don't change anything** outside this folder, the user's Stenny folder, Stenny's own folders (`stenny where` lists them) and what the steps below install. The only background job is the one in step 12.
- Long commands (downloads) take minutes: run them in the background, say roughly how long they take, and report when they finish.

The setup has three phases. **A** is everything Stenny needs to work. **B** connects the calendar. **C** is recommended, never required: Stenny works without it.

Start by greeting the user in three lines: Stenny records lectures from the menu bar, transcribes them on this Mac (nothing is uploaded), and saves text files Claude can study with. The essential part takes about 15–20 minutes, mostly waiting for a download. They'll see a few "Allow" dialogs from macOS (calendar, notifications, microphone).

# Phase A: Stenny working

## 0. Get Stenny onto the Mac

**First, Stenny's installer.** One script does steps 0–4 and 12 in one go: this folder (`~/Applications/stenny.noindex`), uv and the `stenny` command, the app, the Stenny folder `~/Documents/Stenny`, the background job and the Whisper model; at the end it opens Stenny. It asks nothing. Run it in the background (the Whisper download takes 2–10 minutes) and say so in one line:

```bash
curl -fsSL https://github.com/hendrikboettcher/stenny/releases/latest/download/install.sh | zsh
```

- **It ends with `✅ Stenny … is installed`:** go on with step 5. The config exists already, so don't run `stenny init` there: if they'd like another folder than `~/Documents/Stenny`, change `folder` in config.toml (`stenny where` shows where it is). Step 7's `open` is done too; the rest of step 7 isn't.
- **It stops with `❌`:** tell the user what it says. If it's about the Mac itself, see step 1's *If it fails*. Otherwise do steps 0–4 below by hand.

Stenny runs from its folder for good, so it needs a permanent place. If the current folder already contains Stenny (`SETUP.md`, `pyproject.toml` and `app/` are here), use it. Otherwise use `~/Applications/stenny.noindex` without asking; the user can move it another day by asking you.

First look whether it's already there (an earlier attempt): `ls ~/Applications/stenny.noindex/SETUP.md 2>/dev/null`. If it is, ask "Use the existing folder (Recommended)" / "Replace it with the newest version"; replace only by moving the old folder to the Trash first.

Download the newest version and unzip it (it unpacks into a folder called `stenny.noindex`; `.noindex` keeps its files out of the user's Spotlight searches, so searching for Stenny finds only the app):

```bash
mkdir -p ~/Applications
curl -fL -o /tmp/Stenny.zip https://github.com/hendrikboettcher/stenny/releases/latest/download/Stenny.zip
ditto -x -k /tmp/Stenny.zip ~/Applications && rm /tmp/Stenny.zip
```

**Check:** `ls ~/Applications/stenny.noindex/SETUP.md ~/Applications/stenny.noindex/Stenny.mcpb ~/Applications/stenny.noindex/app/build.noindex/Stenny.app` shows all three. From now on, "this folder" means `~/Applications/stenny.noindex`: run every command there (`cd ~/Applications/stenny.noindex && …`), even if your session started somewhere else, and continue with the copy of this file in it (it matches the downloaded version).

Stenny's app is signed with its author's own certificate, not one from Apple. Say so in one sentence, and that the code is open at github.com/hendrikboettcher/stenny.

**If they downloaded the zip in a browser,** macOS marks the app as downloaded and will refuse to open it:

```bash
xattr -p com.apple.quarantine app/build.noindex/Stenny.app 2>/dev/null
```

If that prints something, explain this in one sentence and ask "Download it again here (Recommended)" / "Stop". Downloading here means moving this folder to the Trash and doing the download above.

## 1. Check the Mac

```bash
uname -m                          # must be arm64
sw_vers -productVersion           # must be 14.4 or higher
df -h ~ | tail -1                 # "Avail" should be at least 5G
sysctl -n hw.memsize              # memory in bytes
```

**Check:** `arm64`, a version ≥ 14.4, ≥ 5 GB free.

With 8 GB of memory (8589934592), tell the user in one sentence that Stenny hasn't been tested on 8 GB Macs yet, so this setup is a real test of that. Go on.

*If it fails:* on an Intel Mac (`x86_64`) Stenny can't run: tell the user kindly and stop. If macOS is older, they can update it in System Settings ▸ General ▸ Software Update and come back. If the disk is too full, help them see what's big (`du -sh ~/Downloads ~/Movies ~/Desktop 2>/dev/null`), but don't delete anything yourself.

**Where the folder lives:** it must stay where it is after setup. If it's in Downloads or on the Desktop, move it to `~/Applications/stenny.noindex` (`mkdir -p ~/Applications && mv "<this folder>" ~/Applications/stenny.noindex`) and go on from there.

## 2. Install the `stenny` command

`uv` installs Python programs, and Python itself if needed. It needs no password.

```bash
command -v uv || ls ~/.local/bin/uv /opt/homebrew/bin/uv 2>/dev/null
```

If nothing is printed, install it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Use the full path `~/.local/bin/uv` from here on if plain `uv` isn't found. Then check whether `stenny` is already installed (an earlier attempt):

```bash
grep -o 'editable = "[^"]*"' "$(uv tool dir)/stenny/uv-receipt.toml" 2>/dev/null   # the folder it was installed from
```

No output: not installed yet. The same folder as this one: already done. Another folder: tell the user and ask before switching it to this folder (`--force` below). Then:

```bash
uv tool install -e . --python 3.12   # add --force to replace an existing install
uv tool update-shell                 # makes sure ~/.local/bin is on the PATH in new terminals
```

**Check:** `~/.local/bin/stenny --version` prints a version.

Always pass `--python 3.12`: without it uv may pick the newest Python on the Mac, which some of Stenny's dependencies don't support yet.

*If it fails:* if `uv tool install` can't find Python 3.12, run `uv python install 3.12` and retry.

## 3. Install the app

If Stenny is already installed and running, this replaces it; first make sure no recording is running (`stenny status` must say none is running).

```bash
app/install.sh --no-open
```

**Check:** the output ends with `✅ Installed: <path>/Stenny.app` (remember that path). `"<path>/Stenny.app/Contents/MacOS/ffmpeg" -version` prints a version. Don't open the app yet; that's step 7.

## 4. Start the Whisper download

Whisper is the speech recognition; it runs entirely on this Mac. Start it in the background so it runs while you talk: `stenny download-model` (about 1.6 GB, 2–10 minutes). If it was already on this Mac, it finishes within seconds.

## 5. Where the transcripts go

Stenny writes plain text files (Markdown) into one folder, the Stenny folder, which Claude reads: one folder per course, talks in `Talks`, glossaries and course files next to them. Ask "`~/Documents/Stenny` (Recommended)" / "Another folder". Another folder can be anywhere, also inside an Obsidian vault they already use (then e.g. `<vault>/Lectures`).

```bash
stenny init                              # ~/Documents/Stenny
stenny init --folder "<the folder>"      # only if they chose another one
```

**Check:** `stenny where` prints `folder: <the folder>`, and `stenny courses` the two example courses. (If a config already exists, `stenny init` refuses: tell the user what's in it, and ask whether to keep it (Recommended, if it has their courses) or start fresh.)

## 6. The pause shortcut

One global keyboard shortcut pauses and resumes the recording from any app.

```bash
defaults read com.apple.HIToolbox AppleEnabledInputSources
```

Ask with options:
- German, Swiss or Austrian layout: "⌘⇧Ü (Recommended)", "⌃⌥P", "Something else".
- Any other layout (no Ü key): "⌃⌥P, Control-Option-P (Recommended)", "⌘⇧. (Command-Shift-Period)", "Something else".

Set it in the config under `[app]`:

```toml
[app]
pause_shortcut = "ctrl+opt+p"
```

Format: modifiers `cmd`, `shift`, `ctrl`, `opt`, joined with `+`, then one key as printed on their keyboard (or `space`, `f1`–`f20`). At least one of cmd/ctrl/opt.

On Windows `cmd` means Ctrl and `opt` means Alt (Settings shows it as *Ctrl+Shift+Ü*); without a Ü key the default takes the key right of P. Ask there with "Ctrl+Shift+Ü (Recommended)" for a German layout, else "Ctrl+Alt+P (Recommended)" (`ctrl+opt+p`).

**Check:** `stenny app-settings` prints their shortcut and `"error": null`.

## 7. Open Stenny and allow access

```bash
open "<path from step 3>/Stenny.app"
```

Tell the user to look for **a small stick figure in the menu bar** (top right of the screen), and to click **Allow** for what comes next:
- **Calendar** and **Notifications**, right away. Nothing is changed or sent anywhere.
- **Microphone**, the first time a recording starts (step 11).

Then one setting, so the *Start Recording* button stays on screen: **System Settings ▸ Notifications ▸ Stenny ▸ Alert Style ▸ Persistent** (on macOS 14 it's called *Alerts*). Recommended.

Stenny speaks English or German, following the system's language. If the user prefers the other one, it's **Settings… ▸ General ▸ Language** in Stenny's window (System default / English / Deutsch); it changes at once. Talk to the user in their language either way.

Stenny opens at login by itself; macOS may show a short notice about it. They can turn it off in **System Settings ▸ General ▸ Login Items**.

**Check:** the icon is there. `tail -20 ~/Library/Logs/Stenny/stenny.log` shows `pause shortcut: …` with their shortcut and `hotkey registered`.

*If it fails:* no icon → the menu bar may be full (on a MacBook with a notch, icons hide behind it; quit some menu bar apps). Permissions denied by accident → System Settings ▸ Privacy & Security ▸ Microphone / Calendars ▸ turn Stenny on. macOS refuses to open the app → step 0, the part about browsers.

## 8. Courses

Read the titles from the Mac's calendar: `cat ~/Library/Application\ Support/Stenny/calendar-titles.json`. Stenny wrote this file when it opened in the step before (if it isn't there yet, wait a few seconds, and check that they clicked Allow for Calendar). Titles often contain extras such as "VL", "Lecture", "Übung", a room or lecturer names.

- **Titles found:** turn them into clean course names, leave out what clearly isn't a course (meetings, birthdays), and ask which ones they're taking, several selected at once. If there are more than four, ask in groups.
- **No titles:** just ask them to type their course names. Don't offer anything else, such as screenshots.

Then, **one course at a time**, ask with options:
- **Language** of the lecture. Guess from the course name and offer, for example: "English (Recommended)", "German", "Mixed: German and English", "Other". Explain *mixed* once: for lecturers who switch languages within a lecture.
- **Short name** for matching calendar events: suggest the abbreviation students actually use (e.g. `micro` for Microeconomics) and "No short name".

Show the final list and ask "Looks right (Recommended)" / "Change something". Then edit `~/Library/Application Support/Stenny/config.toml` to **delete the two example courses** (`Calculus`, `International Economics`) at the end, and add each course:

```bash
stenny course add "Microeconomics" --language en --alias micro
stenny course add "Business Communication 2 – French" --language mixed --languages fr,de --alias bizcom
```

Then restart Stenny, so it matches calendar events against these courses: `pkill -x Stenny; open "<path from step 3>/Stenny.app"`.

**Check:** `stenny courses` lists exactly their courses with the right languages.

Tell them they can add or remove courses later in Stenny's window (Stenny's icon ▸ *Open Stenny…*: the **+** next to *Courses*, or right-click a course), without you.

## 9. The AI step (the `claude` command)

Stenny adds section headings, a summary and checked fixes for misheard terms, through their Claude subscription and the `claude` command, at no extra cost. You're probably running as that command already; the desktop app doesn't always install it, so check:

```bash
command -v claude || ls ~/.local/bin/claude 2>/dev/null
claude auth status 2>/dev/null
```

- **Installed and `"loggedIn": true`:** go on.
- **Missing:** ask "Install it (Recommended)" / "Skip the AI step". To install, run Anthropic's installer yourself (no password needed): `curl -fsSL https://claude.ai/install.sh | bash`.
- **Not signed in:** run `~/.local/bin/claude auth login` in the background. It opens the browser: the user signs in and clicks *Authorize*. Then check again.
- **Skip:** set `[llm] backend = "none"` in the config. Transcripts work the same, just without headings and summaries.
- **They'd rather pay per use with an Anthropic API key** (no subscription): they run `stenny api-key` in their own terminal and paste the key there (never into this chat), then set `[llm] backend = "api"`. Stenny checks the key and keeps it in the system keychain. Later they can do all of this in Stenny's *Settings… ▸ Transcripts & AI*.

**Check:** `claude auth status` shows `"loggedIn": true` (or `stenny api-key status` says the key works, or the backend is `"none"`).

## 10. Wait for Whisper

**Check:** the download from step 4 ended with `Done: …/models--mlx-community--whisper-large-v3-turbo/…`.

*If it fails:* network errors → just run it again (it resumes). Behind a university proxy/VPN, try another network.

## 11. Test recording

Two short tests, saved as a *talk*, so they don't count as lecture 1 of a course.

**a) Transcription without the microphone**, run by you:

```bash
say -o /tmp/stenny-test.aiff "This is a short test of Stenny. The lecture today is about supply and demand."
stenny add /tmp/stenny-test.aiff --event "Stenny test" --now
```

**Check:** it ends without errors, and a transcript exists: `ls "<Stenny folder>/Talks/Stenny test/"*/`. Show the user the text here in the chat. If the log says the AI step was skipped, go back to step 9.

**b) Microphone, through the app** (about 30 seconds):
1. Stenny's icon ▸ *Microphone*: check the right microphone is ticked (recommended: the Mac's own).
2. Stenny's icon ▸ *Start Lecture* ▸ *Other…*, enter `Mic test`, start it, and click **Allow** when macOS asks for the microphone. The icon turns into a dot with the time.
3. Talk for about 10 seconds. Press the pause shortcut, wait a few seconds, press it again, talk for another 10 seconds.
4. Click the dot ▸ *Stop & Make Final Transcript*. Wait about a minute.

**Check:** `stenny check` shows no missing audio, and the transcript in `<Stenny folder>/Talks/Mic test/<date>/` contains what they said, with a `*[Recording paused …]*` line. If the text is empty: check the selected microphone and the level meter in the open menu while recording.

Afterwards, ask "Delete the test recordings (Recommended)" / "Keep them". If delete: move only these to the Trash: `<Stenny folder>/Talks/Stenny test`, `<Stenny folder>/Talks/Mic test`, `~/Library/Application Support/Stenny/Recordings/Talks/Stenny test`, `~/Library/Application Support/Stenny/Recordings/Talks/Mic test`, `/tmp/stenny-test.aiff`.

## 12. Finish waiting transcripts automatically

On battery or when memory is low, final transcripts wait. Install the small background job that finishes them, without asking; it checks every 10 minutes and only works when the Mac is plugged in (while Stenny is open, plugging in starts it at once):

```bash
stenny schedule install
```

On Windows the same command adds the task *Stenny Queue* to Task Scheduler (for this user, no admin rights, no window). If it says `Task Scheduler: … Access is denied`, tell the user and go on: final transcripts then wait until `stenny queue run`.

Tell the user in one sentence. They can remove it any time by asking in this chat.

## 13. Let Claude read the transcripts

This is what Stenny is for: Claude answers from what was said in class. The **Stenny connector** gives Claude your lectures: any chat can search them, read a transcript, and cite it as "Lecture 3 [0:12:30]". It needs the **Claude desktop app** on this Mac (claude.ai/download, if it's missing); Claude on the web and on the phone can't reach it.

Check that the connector can start: `stenny mcp --desktop-config` prints a short JSON entry. Then open the extension in Claude:

```bash
open -a Claude Stenny.mcpb
```

The user clicks **Install** in the window Claude shows. You can't click it for them. If no window appears, they can drag `Stenny.mcpb` from this folder onto Claude's Settings window (open the folder for them with `open -a Finder .`).

**Check:** Settings ▸ Customize ▸ Connectors lists **Stenny**, switched on. Then, in a new chat in the Claude desktop app, ask: *"Which courses do you have in Stenny?"* Claude should use Stenny's tools (the first time it asks: *Allow*) and list the courses from step 8. If Claude says Stenny isn't set up, it explains why: usually step 2. If the menu looks different, ask the user to describe or screenshot what they see and help from there.

Tell them in one line: they can just ask, in any chat, e.g. *"Quiz me on my last Microeconomics lecture."*

Tell the user: **Stenny works now.** Two short parts are left.

# Phase B: Calendar

## 14. Calendar

Say in a sentence or two: Stenny offers lectures from the calendar, from 10 minutes before they start, and never starts by itself. On a Mac it reads the calendars the Mac knows. If the timetable is somewhere else (Google Calendar or Outlook in the browser, the university's timetable), or on Windows, the user pastes that calendar's iCal link in Stenny ▸ *Settings… ▸ Calendar ▸ Add a calendar link…*; the sheet there says where to find it. The link is a secret: never ask for it in the chat, they paste it into Stenny themselves. Without a calendar, recording from the menu still works. Don't teach how to use a calendar.

*Settings… ▸ Calendar* also shows whether Stenny may read the Mac's calendar and send notifications, with a button to System Settings if not, and how many lectures match the courses.

**Check, without asking anything:** for each lecture-like title in `calendar-titles.json`, run `stenny match-course "<title>"`. If one of their courses doesn't match its events, add a word that's in the titles to that course as `calendar = ["<word>"]` and check again. Only tell the user the result, in one line. If there are no titles, skip this.

# Phase C: Recommended, not required

Stenny works without this. Ask for each part, with the recommendation given there.

## 15. Claude projects (one per course)

Not needed for the transcripts: the connector from step 13 works in every chat, and keeps the glossaries up to date by itself. A project only helps to keep a course's other material in one place, such as the syllabus, a reading list or old exams. Ask "Skip (Recommended)" / "Set it up".

If they want it, for each course, one at a time: in the Claude desktop app, **Projects ▸ New project**, named after the course, then add the files. No instructions to paste. You can't create projects yourself, so guide them one click at a time.

## 16. Obsidian

Obsidian (free) shows the transcripts, summaries and topics linked together, and the menu's *Open Transcript* opens the live draft there. The files stay plain text in the same folder; nothing moves. Stenny opens a transcript in Obsidian only when its folder is one of Obsidian's vaults (or inside one); otherwise it uses the default Markdown app. Ask "Set it up (Recommended)" / "Skip".

Check: `ls -d /Applications/Obsidian.app ~/Applications/Obsidian.app 2>/dev/null`. If it's missing, open https://obsidian.md/download and wait until they've dragged it to Applications. If the folder from step 5 isn't a vault yet, open Obsidian and have them add it. On first launch Obsidian may create a vault by itself and skip its start screen, and its command palette has no such command: then it's the vault name at the bottom left ▸ *Manage vaults…* ▸ *Open folder as vault* (⌘⇧G lets them paste the path).

# The end

## 17. Finish

Say clearly: **✅ Setup is finished.** Then, short:
- Before a lecture: wait for the notification, or click Stenny's icon ▸ *Start Lecture* ▸ course. Keep the MacBook **open** while recording.
- Stop: click the dot ▸ *Stop & Make Final Transcript*. On power, a 3-hour lecture's transcript is ready after about 20–25 minutes; on battery it waits until they plug in.
- Afterwards: Stenny's icon ▸ *Open Stenny…* shows every lecture with its summary. *Share…* there makes a clean copy (PDF, Word or Markdown) to send to classmates.
- If a step above was skipped (no course, the AI step not signed in, the connector not installed), the window's *Finish setting up Stenny* card lists what's left, with a button for each, until it's done.
- If memory or disk runs low, the recording goes on first: the live text pauses, or the final transcript waits. If something crashes, Stenny saves the audio and starts recording again by itself.
- **Ask the lecturer first.** Many universities need the lecturer's consent before a lecture is recorded. Keep recordings and transcripts to themselves.
- New versions: *Check for Updates…* at the bottom of Stenny's window (its icon ▸ *Open Stenny…*).
- Docs: https://stenny.eu/docs/quickstart/

**Stenny is made to be built on.** It's open source and lives in this folder. Whenever they want something different, they can reopen this chat (or this folder in Claude Code) and ask in plain language: "add a course", "change my pause shortcut", or a new feature. Changing the menu bar app itself needs Apple's Command Line Tools, which Claude Code can set up then. An update replaces the folder and keeps the old one next to it, so their changes can be carried over.

**Feedback is very welcome:** *Send Feedback…* at the bottom of Stenny's window, or the form at https://stenny.eu.

Write a short setup report: Mac model (`sysctl -n hw.model`), memory, macOS version, Stenny version, and any step that needed a workaround. No names, courses or file paths. Show it and offer to send it to Stenny's author: "Send it (Recommended)" / "Keep it to myself". If yes, send exactly what you showed them:

```bash
uv run --no-project --python 3.12 python -c 'import json,sys; print(json.dumps({"source": "setup", "message": sys.argv[1]}))' "<the report>" | curl -fsS -X POST https://stenny.eu/api/feedback -H "Content-Type: application/json" -d @-
```

If it prints `{"ok":true}`, say it was sent; otherwise say it didn't go through and go on.

## Troubleshooting

| Problem | What to do |
|---|---|
| `stenny: command not found` | Use `~/.local/bin/stenny`, run `uv tool update-shell` and open a new terminal. |
| macOS says Stenny can't be opened | It was downloaded in a browser: step 0. |
| `ffmpeg` not found | `<path>/Stenny.app/Contents/MacOS/ffmpeg` should exist. If it doesn't, the app wasn't installed from a release: step 3. |
| Microphone permission was denied | System Settings ▸ Privacy & Security ▸ Microphone ▸ Stenny on. Then restart Stenny. |
| The shortcut does nothing | `stenny app-settings` must show `"error": null`. Another app may already use that shortcut: pick another (step 6) and restart Stenny (menu ▸ *Quit Stenny*, then open it again as in step 7). |
| "Only … GB free" when a recording starts | Stenny needs about 1.5 GB to start a lecture. Help them see what's big, but don't delete anything yourself. |
| The calendar lecture isn't offered | `stenny match-course "<event title>"` (step 14); events shorter than 20 minutes are ignored. |
| Transcript is empty or wrong language | Check the course's `language`; for lectures that switch languages use `"mixed"`. Check the microphone level meter. |
| The AI step fails ("claude CLI not found") | Step 9. Or set `[llm] backend = "none"`. The transcript is always written anyway. |
| Anything else | Logs are in `~/Library/Logs/Stenny/`: `stenny.log` (the menu bar app), `live.log` (the running recording), `queue.log` (waiting transcripts). Read the end of the newest one. |

# Stenny on Windows (preview)

**Not yet tested on a real Windows PC.** Stenny's Windows version is being built (issue #2 on GitHub); only GitHub's Windows test machines have run it so far. Say this to the user in one sentence before you start, and that this setup is a real test of it. Some parts may not be there yet (recording from the microphone, the pause shortcut, lecture notifications, the background queue): when a step below can't work for that reason, say so in one line and go on, and note it for the report at the end.

**Commands.** Claude Code on Windows runs commands in Git Bash, so the commands below are written for it. The Windows-only parts run through `powershell -NoProfile -Command '…'` (single quotes, so Git Bash leaves PowerShell's `$env:…` alone). `$LOCALAPPDATA`, `$APPDATA` and `$USERPROFILE` work in Git Bash too. To open something, name the app: `explorer.exe "<folder>"`, `notepad.exe "<file>"`; web pages with `cmd //c start "" "https://…"`.

Greet the user as in Phase A, with "PC" for "Mac". The essential part takes about 20–30 minutes, mostly downloads. Windows will ask once whether to run the installer (it isn't signed yet, see W4).

## W1. Check the PC

```bash
echo "$PROCESSOR_ARCHITECTURE"                                                           # must be AMD64
powershell -NoProfile -Command '[Environment]::OSVersion.Version.Build'                  # 17763 or higher
powershell -NoProfile -Command '[math]::Round((Get-PSDrive C).Free/1GB)'                 # GB free
powershell -NoProfile -Command '[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB)'  # GB memory
nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null                            # an NVIDIA graphics card?
```

**Check:** `AMD64`, a build ≥ 17763 (Windows 10 version 1809 or Windows 11), ≥ 5 GB free, ≥ 8 GB memory.

Tell the user in one sentence how fast transcription will be: with an NVIDIA card, much faster than the lecture lasted (if its CUDA libraries are installed; Stenny never installs them, they're several GB); without one, on the processor, a lecture takes about as long to transcribe as it lasted, or longer. Both give the same text.

*If it fails:* `ARM64` (a Snapdragon PC): Stenny's speech recognition doesn't run there yet; tell the user kindly and stop. An older Windows: Settings ▸ Windows Update, then come back. Too little disk space: help them see what's big, but don't delete anything yourself.

## W2. Get Stenny onto the PC

**First, Stenny's installer.** It does W2–W5 and W11 in one go (the code folder, uv and the `stenny` command, the app through its setup program, without the *"Windows protected your PC"* warning, the Stenny folder `Documents\Stenny`, the task for final transcripts, the Whisper model) and starts Stenny. Run it in the background and say so:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -Command 'irm https://github.com/hendrikboettcher/stenny/releases/latest/download/install.ps1 | iex'
```

If it ends with `Stenny … is installed`, go on with W6 (the config exists: for another folder change `folder` in config.toml instead of `stenny init`). If it stops with `X`, tell the user what it says and do W2–W5 below by hand.

Stenny's code lives in a permanent folder: `%LOCALAPPDATA%\Programs\stenny` (in Git Bash `"$LOCALAPPDATA/Programs/stenny"`), without asking. If the current folder already contains Stenny (`SETUP.md`, `pyproject.toml`), use it instead. If `ls "$LOCALAPPDATA/Programs/stenny/SETUP.md"` finds an earlier attempt, ask "Use the existing folder (Recommended)" / "Replace it with the newest version"; replace only by moving the old folder to the Recycle Bin first (the command is in W10).

Download the code of the newest release and unpack it there:

```bash
powershell -NoProfile -Command '
  $ProgressPreference = "SilentlyContinue"
  $tag = (Invoke-RestMethod https://api.github.com/repos/hendrikboettcher/stenny/releases/latest).tag_name
  $zip = "$env:TEMP\stenny.zip"
  Invoke-WebRequest "https://github.com/hendrikboettcher/stenny/archive/refs/tags/$tag.zip" -OutFile $zip
  New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\Programs" | Out-Null
  Expand-Archive $zip "$env:LOCALAPPDATA\Programs" -Force
  Rename-Item "$env:LOCALAPPDATA\Programs\stenny-$($tag.TrimStart("v"))" stenny
  Remove-Item $zip'
```

**Check:** `ls "$LOCALAPPDATA/Programs/stenny/SETUP.md" "$LOCALAPPDATA/Programs/stenny/pyproject.toml"` shows both. From now on "this folder" means `"$LOCALAPPDATA/Programs/stenny"`: run every command there (`cd "$LOCALAPPDATA/Programs/stenny" && …`) and continue with the copy of this file in it.

## W3. Install the `stenny` command

As in step 2, with Windows' installer for `uv`:

```bash
command -v uv || ls ~/.local/bin/uv.exe 2>/dev/null
powershell -NoProfile -ExecutionPolicy ByPass -Command 'irm https://astral.sh/uv/install.ps1 | iex'   # only if missing
```

Use `~/.local/bin/uv.exe` if plain `uv` isn't found. Look for an earlier install as in step 2 (`uv-receipt.toml`), then:

```bash
uv tool install -e . --python 3.12   # Python 3.12 and Stenny's parts, about 300 MB: a few minutes
uv tool update-shell
```

**Check:** `~/.local/bin/stenny.exe --version` prints a version. Always pass `--python 3.12`. uv brings Python itself; nothing here needs administrator rights.

## W4. Install the app

The Windows app comes as a setup program, `Stenny_<version>_x64-setup.exe`, attached to the release. See whether the newest release has one:

```bash
powershell -NoProfile -Command '(Invoke-RestMethod https://api.github.com/repos/hendrikboettcher/stenny/releases/latest).assets | ? name -like "*-setup.exe" | % browser_download_url'
```

- **Nothing printed:** the Windows app isn't released yet. Tell the user in one sentence, and that they can still transcribe recordings and study them with Claude (the rest of this setup). Stenny then needs `ffmpeg` from elsewhere: `command -v ffmpeg`; if it's missing, ask "Install ffmpeg with winget (Recommended, about 100 MB)" / "Skip" and run `winget install --id Gyan.FFmpeg -e` (then open a new terminal). Skip W10's microphone test.
- **A link:** download it and start it:

  ```bash
  curl -fL -o "$USERPROFILE/Downloads/Stenny-setup.exe" "<the link>"
  cmd //c start "" "$USERPROFILE\Downloads\Stenny-setup.exe"
  ```

  Stenny isn't signed with a certificate yet, so Windows shows **"Windows protected your PC"**. Tell the user to click **More info ▸ Run anyway**, and that the code is open at github.com/hendrikboettcher/stenny. The installer speaks English or German (Windows' language), installs for this user only (no administrator) into `%LOCALAPPDATA%\Stenny`, next to Stenny's own data, and sets up Microsoft's WebView2 if the PC lacks it. Its uninstaller removes only the app's own files, never recordings. They can uncheck *Run Stenny* at the end; it opens in W10.

**Check:** `"$LOCALAPPDATA/Stenny/ffmpeg.exe" -version` prints a version (without the app: `ffmpeg -version`). If they picked another folder in the installer, `ffmpeg.exe` is in that one; Stenny finds it there.

## W5. Start the Whisper download

In the background, as in step 4: `stenny download-model`. On Windows it's faster-whisper's version of the same model (about 1.6 GB), in `%LOCALAPPDATA%\Stenny\Models`.

## W6. Where the transcripts go

As in step 5. The recommended folder is `Documents\Stenny` (`stenny init` picks Windows' Documents folder, which may be in OneDrive; that's fine for text files). **Check:** `stenny where` prints `folder: …`.

## W7. Courses

As in step 8: ask the user to type their course names. (If they already pasted a calendar link in Stenny ▸ Settings ▸ Calendar, `%LOCALAPPDATA%\Stenny\calendar-titles.json` has this term's event titles to suggest from.) The config is `%APPDATA%\Stenny\config.toml` (`stenny where` shows it): delete the two example courses at its end, then `stenny course add …` for each course. **Check:** `stenny courses`.

## W8. The AI step

As in step 9, with Windows' installer for the `claude` command if it's missing:

```bash
command -v claude || ls ~/.local/bin/claude.exe 2>/dev/null
powershell -NoProfile -ExecutionPolicy ByPass -Command 'irm https://claude.ai/install.ps1 | iex'
```

An Anthropic API key goes into Windows' Credential Manager (`stenny api-key`, which the user runs in their own PowerShell window).

## W9. Wait for Whisper

**Check:** the download from W5 ended with `Done: …`, a folder under `Models`.

## W10. Test recording

**a) Transcription without the microphone**, run by you:

```bash
powershell -NoProfile -Command 'Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.SetOutputToWaveFile("$env:TEMP\stenny-test.wav"); $s.Speak("This is a short test of Stenny. The lecture today is about supply and demand."); $s.Dispose()'
stenny add "$(cygpath -w /tmp)\stenny-test.wav" --event "Stenny test" --now
```

(Git Bash's `/tmp` is Windows' `%TEMP%`.) Without an NVIDIA card the first run takes a minute or two, as the model loads first. **Check:** as in step 11 a.

**b) Microphone, through the app:** only with the app installed (W4). Open it (`cmd //c start "" "$LOCALAPPDATA\Stenny\Stenny.exe"`), then as in step 11 b, with Stenny's icon in the taskbar's notification area (behind the **^** arrow if it's hidden). Windows asks for the microphone the first time. If the app can't record yet, skip this and say so.

Deleting the test recordings (step 11's list, with `%LOCALAPPDATA%\Stenny\Recordings\Talks\…` for the recordings, and `stenny-test.wav`): to the Recycle Bin, one at a time (`DeleteFile` for the .wav):

```bash
powershell -NoProfile -Command 'Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("<folder>", "OnlyErrorDialogs", "SendToRecycleBin")'
```

## W11. Finish waiting transcripts automatically

Step 12. If `stenny schedule install` says it can't do that on Windows yet, skip it and note it for the report.

## W12. Let Claude read the transcripts

As in step 13, with the Claude desktop app for Windows (claude.ai/download). The extension comes with the app as `%LOCALAPPDATA%\Stenny\Stenny.mcpb`; without the app, download it: `curl -fL -o "$USERPROFILE/Downloads/Stenny.mcpb" https://github.com/hendrikboettcher/stenny/releases/latest/download/Stenny.mcpb`. Open it with Claude:

```bash
cmd //c start "" "$LOCALAPPDATA\Stenny\Stenny.mcpb"
```

If Claude doesn't open it, the user can add it in Claude's **Settings ▸ Extensions ▸ Advanced settings ▸ Install Extension…** (open its folder for them with `explorer.exe "<folder>"`). **Check:** as in step 13.

## W13. Recommended, not required

Steps 15 (Claude projects) and 16 (Obsidian: the Windows download at obsidian.md/download) work the same on Windows.

## W14. Finish

As in step 17, with Windows' words: the taskbar's notification area instead of the menu bar, the Recycle Bin instead of the Trash, *Show in Explorer*. For the report: `powershell -NoProfile -Command '(Get-CimInstance Win32_ComputerSystem).Model'`, the memory, the Windows build, the graphics card, the Stenny version, and which steps couldn't work on Windows yet. Sending it is the same command as in step 17.

## Updating on Windows

`stenny update` doesn't work on Windows (Windows won't let it replace the program that is running it), and *Check for Updates…* only says whether there is a new version. When the user asks you to update Stenny, run the installer again (W2): it waits for no recording or final transcript to be running (it stops and says so), quits Stenny, replaces the code (the old folder goes to the Recycle Bin), the engine and the app, and starts Stenny again. **Check:** `stenny --version` shows the new version, and `stenny mcp --desktop-config` works.

## Troubleshooting on Windows

| Problem | What to do |
|---|---|
| `stenny: command not found` | Use `~/.local/bin/stenny.exe`, run `uv tool update-shell` and open a new terminal. |
| "Windows protected your PC" | The installer isn't signed yet: *More info ▸ Run anyway* (W4). |
| `ffmpeg` not found | `%LOCALAPPDATA%\Stenny\ffmpeg.exe` (or in the folder the installer was given) should exist; otherwise W4. |
| Transcription is very slow | Without an NVIDIA card (and its CUDA libraries) the processor does it: about as long as the lecture. That's expected. |
| Anything else | Logs are in `%LOCALAPPDATA%\Stenny\Logs\`. Read the end of the newest one. |
