# Setting up Stenny (instructions for Claude Code)

**For the person reading this:** you don't have to do anything here yourself. Paste this sentence into Claude Code:

> Install Stenny on my Mac by following https://github.com/hendrikboettcher/stenny/blob/main/SETUP.md

Claude Code will go through the steps below with you and ask whenever it needs a decision or a click.

---

**For Claude Code.** You are setting up Stenny for someone who may never have used a terminal. Work through the steps below in order, together with them.

How to work:
- **Short and engaging.** Nobody wants to read for hours. Say a sentence or two per step: only what helps them decide or click. No walls of text, no jargon. These instructions are detailed; what you say to the user isn't.
- **One step at a time.** Run the step, then run its **Check**. Only go on when the check passes. If it fails, look at the *If it fails* notes, then the *Troubleshooting* section at the end. If you're still stuck, explain the problem simply and ask the user how to go on. Never skip a failed step silently.
- **Check what's already there first.** Many Macs already have some of these tools. If something is installed and works, say so in one line and go on. Never reinstall or remove something that works.
- **Ask, don't guess,** for anything personal: the folder for transcripts, courses, languages, the shortcut.
- **Every question has a recommended answer.** Ask one thing at a time. If you have a tool for questions with clickable options (such as AskUserQuestion), use it for every choice: at most four options, your recommendation first and marked "(Recommended)", with a short reason; the user can always type something else. Ask for free text only where nothing can be suggested. Never make the user write long answers.
- **Always name the app when you open something.** A plain `open <link or file>` lets macOS choose, and on a fresh account that can be any installed app that claims the type. Use `open -a Obsidian …`, `open -a TextEdit …` and so on. Web pages (`open https://…`) may go to the default browser.
- **Keep the user out of the Terminal.** Run every command yourself. The user only clicks: macOS dialogs, System Settings, the browser, the Claude app. Nothing in this setup needs their Mac password.
- **Don't change anything** outside this folder, the user's chosen transcripts folder, `~/Lectures Data` and what the steps below install. Don't install a launchd agent unless the user chooses it in step 12.
- Long commands (downloads) take minutes: run them in the background, say roughly how long they take, and report when they finish.

The setup has three phases. **A** is everything Stenny needs to work. **B** connects the calendar. **C** is recommended, never required: Stenny works without it.

Start by greeting the user in three lines: Stenny records lectures from the menu bar, transcribes them on this Mac (nothing is uploaded), and saves text files Claude can study with. The essential part takes about 15–20 minutes, mostly waiting for a download. They'll see a few "Allow" dialogs from macOS (calendar, notifications, microphone).

# Phase A: Stenny working

## 0. Get Stenny onto the Mac

Stenny runs from its folder for good, so it needs a permanent place. If the current folder already contains Stenny (`SETUP.md`, `pyproject.toml` and `app/` are here), use it. Otherwise use `~/Applications/stenny` without asking; the user can move it another day by asking you.

First look whether it's already there (an earlier attempt): `ls ~/Applications/stenny/SETUP.md 2>/dev/null`. If it is, ask "Use the existing folder (Recommended)" / "Replace it with the newest version"; replace only by moving the old folder to the Trash first.

Download the newest version and unzip it (it unpacks into a folder called `stenny`):

```bash
mkdir -p ~/Applications
curl -fL -o /tmp/Stenny.zip https://github.com/hendrikboettcher/stenny/releases/latest/download/Stenny.zip
ditto -x -k /tmp/Stenny.zip ~/Applications && rm /tmp/Stenny.zip
```

**Check:** `ls ~/Applications/stenny/SETUP.md ~/Applications/stenny/app/build.noindex/Stenny.app` shows both. From now on, "this folder" means `~/Applications/stenny`: run every command there (`cd ~/Applications/stenny && …`), even if your session started somewhere else, and continue with the copy of this file in it (it matches the downloaded version).

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

**Where the folder lives:** it must stay where it is after setup. If it's in Downloads or on the Desktop, move it to `~/Applications/stenny` (`mkdir -p ~/Applications && mv "<this folder>" ~/Applications/stenny`) and go on from there.

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

**Check:** the output ends with `✅ Installed: <path>/Stenny.app` (remember that path). `"<path>/Stenny.app/Contents/MacOS/ffmpeg" -version` prints a version. Don't open the app yet; that's step 10.

## 4. Start the Whisper download

Whisper is the speech recognition; it runs entirely on this Mac. Start it in the background so it runs while you talk: `stenny download-model` (about 1.6 GB, 2–10 minutes). If it was already on this Mac, it finishes within seconds.

## 5. Where the transcripts go

Stenny writes plain text files (Markdown) into one folder, which Claude reads.

Check which Obsidian vaults exist: `cat ~/Library/Application\ Support/obsidian/obsidian.json 2>/dev/null` (the `"path"` entries). A vault is just a folder.
- **They have vaults:** offer each one (recommended: the one that looks like their study vault) and "A new folder".
- **Otherwise:** suggest `~/Documents/Study Vault` (Recommended) and create it.

Lectures go into a `Lectures` folder inside it; don't ask about this.

```bash
stenny init --vault "<the folder>" --lectures-dir "Lectures"
```

**Check:** `stenny courses` prints `Lectures: <the folder>/Lectures` and the two example courses. (If a config already exists, `stenny init` refuses: tell the user what's in it, and ask whether to keep it (Recommended, if it has their courses) or start fresh.)

## 6. Courses

Ask for a screenshot or PDF of their timetable, which they can drag into this chat (Recommended), or for the course names. Event titles often contain extras such as "VL", "Lecture", "Übung", a room or lecturer names: turn them into clean course names and ask which ones they're taking.

Then, **one course at a time**, ask with options:
- **Language** of the lecture. Guess from the course name and offer, for example: "English (Recommended)", "German", "Mixed: German and English", "Other". Explain *mixed* once: for lecturers who switch languages within a lecture.
- **Short name** for matching calendar events: suggest the abbreviation students actually use (e.g. `micro` for Microeconomics) and "No short name".

Show the final list and ask "Looks right (Recommended)" / "Change something". Then edit `~/Lectures Data/_System/config.toml` to **delete the two example courses** (`Calculus`, `International Economics`) at the end, and add each course:

```bash
stenny course add "Microeconomics" --language en --alias micro
stenny course add "Business Communication 2 – French" --language mixed --languages fr,de --alias bizcom
```

**Check:** `stenny courses` lists exactly their courses with the right languages.

Tell them they can add or hide courses later from Stenny's menu (*Start Lecture ▸ Add Course…*), without you.

## 7. The AI step (the `claude` command)

Stenny adds section headings, a summary and checked fixes for misheard terms, through their Claude subscription and the `claude` command, at no extra cost. You're probably running as that command already; the desktop app doesn't always install it, so check:

```bash
command -v claude || ls ~/.local/bin/claude 2>/dev/null
claude auth status 2>/dev/null
```

- **Installed and `"loggedIn": true`:** go on.
- **Missing:** ask "Install it (Recommended)" / "Skip the AI step". To install, run Anthropic's installer yourself (no password needed): `curl -fsSL https://claude.ai/install.sh | bash`.
- **Not signed in:** run `~/.local/bin/claude auth login` in the background. It opens the browser: the user signs in and clicks *Authorize*. Then check again.
- **Skip:** set `[llm] backend = "none"` in the config. Transcripts work the same, just without headings and summaries.

**Check:** `claude auth status` shows `"loggedIn": true` (or the backend is `"none"`).

## 8. The pause shortcut

One global keyboard shortcut pauses and resumes the recording from any app.

```bash
defaults read com.apple.HIToolbox AppleCurrentKeyboardLayoutInputSourceID
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

**Check:** `stenny app-settings` prints their shortcut and `"error": null`.

## 9. Wait for Whisper

**Check:** the download from step 4 ended with `Done: …/models--mlx-community--whisper-large-v3-turbo/…`.

*If it fails:* network errors → just run it again (it resumes). Behind a university proxy/VPN, try another network.

## 10. Open Stenny and allow access

```bash
open "<path from step 3>/Stenny.app"
```

Tell the user to look for **a small stick figure in the menu bar** (top right of the screen), and to click **Allow** for what comes next:
- **Calendar** and **Notifications**, right away. Nothing is changed or sent anywhere.
- **Microphone**, the first time a recording starts (step 11).

Then one setting, so the *Start Recording* button stays on screen: **System Settings ▸ Notifications ▸ Stenny ▸ Alert Style ▸ Persistent** (on macOS 14 it's called *Alerts*). Recommended.

Stenny opens at login by itself; macOS may show a short notice about it. They can turn it off in **System Settings ▸ General ▸ Login Items**.

**Check:** the icon is there; its menu ▸ *Start Lecture* lists their courses. `tail -20 ~/Lectures\ Data/_System/stenny.log` shows `pause shortcut: …` with their shortcut and `hotkey registered`.

*If it fails:* no courses in the menu → step 6. No icon → the menu bar may be full (on a MacBook with a notch, icons hide behind it; quit some menu bar apps). Permissions denied by accident → System Settings ▸ Privacy & Security ▸ Microphone / Calendars ▸ turn Stenny on. macOS refuses to open the app → step 0, the part about browsers.

## 11. Test recording

Two short tests, saved as a *talk*, so they don't count as lecture 1 of a course.

**a) Transcription without the microphone**, run by you:

```bash
say -o /tmp/stenny-test.aiff "This is a short test of Stenny. The lecture today is about supply and demand."
stenny add /tmp/stenny-test.aiff --event "Stenny test" --now
```

**Check:** it ends without errors, and a transcript exists: `ls "<vault>/Recordings/Stenny test/"*/`. Show the user the text here in the chat. If the log says the AI step was skipped, go back to step 7.

**b) Microphone, through the app** (about 30 seconds):
1. Stenny's icon ▸ *Microphone*: check the right microphone is ticked (recommended: the Mac's own).
2. Stenny's icon ▸ *Start Lecture* ▸ *Other…*, enter `Mic test`, start it, and click **Allow** when macOS asks for the microphone. The icon turns into a dot with the time.
3. Talk for about 10 seconds. Press the pause shortcut, wait a few seconds, press it again, talk for another 10 seconds.
4. Click the dot ▸ *Stop & Make Final Transcript*. Wait about a minute.

**Check:** `stenny check` shows no missing audio, and the transcript in `<vault>/Recordings/Mic test/<date>/` contains what they said, with a `*[Recording paused …]*` line. If the text is empty: check the selected microphone and the level meter in the open menu while recording.

Afterwards, ask "Delete the test recordings (Recommended)" / "Keep them". If delete: move only these to the Trash: `<vault>/Recordings/Stenny test`, `<vault>/Recordings/Mic test`, `~/Lectures Data/_Recordings/Stenny test`, `~/Lectures Data/_Recordings/Mic test`, `/tmp/stenny-test.aiff`.

## 12. Finish waiting transcripts automatically

On battery, final transcripts wait until the Mac is plugged in. Ask: "Finish them automatically when plugged in (Recommended)" / "I'll start them myself". The first installs a small background job that checks every 15 minutes, only on power; `stenny schedule uninstall` removes it. Only if they choose it:

```bash
stenny schedule install
```

Tell the user: **Stenny works now.** Two short parts are left.

# Phase B: Calendar

## 13. Calendar

Say this in a sentence or two: Stenny offers lectures from the calendar on this Mac, from 10 minutes before they start, and never starts by itself. If the timetable isn't in the Mac's Calendar, that part won't work well, but recording from the menu still does. Don't teach how to subscribe to or use a calendar.

**Check:** ask for one or two real event titles, or use those from the timetable, and run `stenny match-course "<title>"`. Each must print the right course. If not, add a word that's in the titles to that course as `calendar = ["<word>"]` (or an alias) and check again. If they have no timetable in Calendar, skip this.

# Phase C: Recommended, not required

Stenny works without this. Without a project, any Claude chat with the Filesystem connector can still read the transcripts. Ask for each part: "Set it up (Recommended)" / "Skip".

## 14. Claude projects (one per course)

Each course gets a Claude project whose chats read the newest transcripts from this Mac, cite them ("Lecture 3 [0:12:30]") and keep the course glossary up to date, which makes later transcripts more accurate. You can't create projects or change Claude's settings yourself, so guide the user, one click at a time.

**a) Create the folders Claude will read:**

```bash
mkdir -p "<vault>/Lectures" "<vault>/Recordings"
```

Tell the user both full paths; they'll pick them in a moment.

**b) Turn on the Filesystem connector** in the **Claude desktop app** (claude.ai/download, if it's missing; the chats that read the transcripts must run in the desktop app on this Mac):
1. Settings ▸ **Connectors** (in some versions **Extensions**) ▸ **Filesystem** ▸ install or enable it.
2. Add the allowed folders: only the `Lectures` and `Recordings` folders from a). In the folder picker, ⌘⇧G lets them paste a path.
3. Save, and restart the Claude app if it asks.

If the menu looks different, ask the user to describe or screenshot what they see and help from there.

**c) One project per course.** For each course, one at a time:
1. Copy the course's instructions for them: `stenny project-instructions --course "<name>" | pbcopy`.
2. In the Claude desktop app: **Projects ▸ New project**, named after the course.
3. **Instructions** (*Set project instructions*) ▸ paste (⌘V) ▸ Save.
4. Optionally add the course's slides, syllabus or reading list as project files.

**Check:** in one of the new projects, start a chat and ask: *"Which folders and files can you see in my Lectures folder?"* Claude should use the Filesystem connector (the first time, it asks: *Allow*). If it says it has no access, go back to b).

## 15. Obsidian

Obsidian (free) shows the transcripts, topics and slides linked together, and the menu's *Open Transcript* opens the live draft there. The files stay plain text in the same folder; nothing moves.

Check: `ls -d /Applications/Obsidian.app ~/Applications/Obsidian.app 2>/dev/null`. If it's missing, open https://obsidian.md/download and wait until they've dragged it to Applications. If the folder from step 5 isn't a vault yet, ask them to open Obsidian ▸ *Open folder as vault* ▸ choose that folder (⌘⇧G lets them paste the path).

# The end

## 16. Finish

Say clearly: **✅ Setup is finished.** Then, short:
- Before a lecture: wait for the notification, or click Stenny's icon ▸ *Start Lecture* ▸ course. Keep the MacBook **open** while recording.
- Stop: click the dot ▸ *Stop & Make Final Transcript*. On power, a 3-hour lecture's transcript is ready after about 20–25 minutes; on battery it waits until they plug in.
- If memory or disk runs low, the recording goes on first: the live text pauses, or the final transcript waits. If something crashes, Stenny saves the audio and starts recording again by itself.
- **Ask the lecturer first.** Many universities need the lecturer's consent before a lecture is recorded. Keep recordings and transcripts to themselves.
- New versions: Stenny's icon ▸ *Check for Updates…*.
- Docs: https://stenny.eu/docs/quickstart/

**Stenny is made to be built on.** It's open source and lives in this folder. Whenever they want something different, they can reopen this chat (or this folder in Claude Code) and ask in plain language: "add a course", "change my pause shortcut", or a new feature. Changing the menu bar app itself needs Apple's Command Line Tools, which Claude Code can set up then. An update replaces the folder and keeps the old one next to it, so their changes can be carried over.

**Feedback is very welcome:** *Report a problem* at https://stenny.eu.

Write a short setup report: Mac model (`sysctl -n hw.model`), memory, macOS version, Stenny version, and any step that needed a workaround. No names, courses or file paths. Show it and offer to open it as a report for the author: "Open it (Recommended)" / "Keep it to myself". If yes, open `https://github.com/hendrikboettcher/stenny/issues/new?title=Setup%20report&body=<the report, URL-encoded>`; they need a GitHub account to send it. Otherwise leave it.

## Troubleshooting

| Problem | What to do |
|---|---|
| `stenny: command not found` | Use `~/.local/bin/stenny`, run `uv tool update-shell` and open a new terminal. |
| macOS says Stenny can't be opened | It was downloaded in a browser: step 0. |
| `ffmpeg` not found | `<path>/Stenny.app/Contents/MacOS/ffmpeg` should exist. If it doesn't, the app wasn't installed from a release: step 3. |
| Microphone permission was denied | System Settings ▸ Privacy & Security ▸ Microphone ▸ Stenny on. Then restart Stenny. |
| The shortcut does nothing | `stenny app-settings` must show `"error": null`. Another app may already use that shortcut: pick another (step 8) and restart Stenny (menu ▸ *Quit Stenny*, then open it again as in step 10). |
| "Only … GB free" when a recording starts | Stenny needs about 1.5 GB to start a lecture. Help them see what's big, but don't delete anything yourself. |
| The calendar lecture isn't offered | `stenny match-course "<event title>"` (step 13); events shorter than 20 minutes are ignored. |
| Transcript is empty or wrong language | Check the course's `language`; for lectures that switch languages use `"mixed"`. Check the microphone level meter. |
| The AI step fails ("claude CLI not found") | Step 7. Or set `[llm] backend = "none"`. The transcript is always written anyway. |
| Anything else | Logs are in `~/Lectures Data/_System/`: `stenny.log` (the menu bar app), `live.log` (the running recording). Read the end of the newest one. |
