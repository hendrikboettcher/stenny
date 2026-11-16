# Setting up Stenny (instructions for Claude Code)

**For the person reading this:** you don't have to do anything here yourself. Stenny is in a closed alpha and invite-only: you need the install line from your invite ([Invites](#invites-closed-alpha)). Give Claude Code this file and that line, for example:

> Install Stenny on my Mac by following SETUP.md, with the install line from my invite: `curl -fsSL https://stenny.eu/i/<your-code> | zsh`

(On a Windows PC: "Install Stenny on my PC …", with the line for Windows.)

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
- **Don't change anything** outside the user's Stenny folder, Stenny's own folders (`stenny where` lists them) and what the steps below install. The only background job is the one in step 12.
- Long commands (downloads) take minutes: run them in the background, say roughly how long they take, and report when they finish.

The setup has three phases. **A** is everything Stenny needs to work. **B** connects the calendar. **C** is recommended, never required: Stenny works without it.

Start by greeting the user in three lines: Stenny records lectures from the menu bar, transcribes them on this Mac (nothing is uploaded), and saves text files Claude can study with. The essential part takes about 15–20 minutes, mostly waiting for a download. They'll see a few "Allow" dialogs from macOS (calendar, notifications, microphone).

# Phase A: Stenny working

## 0. Get Stenny onto the Mac

**First, Stenny's installer.** Stenny is invite-only during its closed alpha: use the user's own install line from their invite. If they haven't given it to you, ask for it; without one, stop here and point them to https://stenny.eu, where they can ask for an invite. One script does steps 0–4 and 12 in one go: the app (with Stenny's engine inside it), the `stenny` command, the Stenny folder `~/Documents/Stenny`, the background job, the Whisper model and the invite, saved for Stenny's updates; at the end it opens Stenny. It asks nothing. Run it in the background (the Whisper download takes 2–10 minutes) and say so in one line:

```bash
curl -fsSL https://stenny.eu/i/<their-code> | zsh
```

- **It stops with `❌ This invite isn't valid`:** the code is mistyped or the invite was withdrawn. Check the line with the user; if it's right, they can ask for a new invite on https://stenny.eu.

- **It ends with `✅ Stenny … is installed`:** go on with step 5. The config exists already: if they'd like another folder than `~/Documents/Stenny`, change `folder` in config.toml (`stenny where` shows where it is). Step 7's `open` is done too; the rest of step 7 isn't. The app is in `/Applications` (or `~/Applications` if `/Applications` isn't writable): that's "the app's path" below.
- **It stops with `❌`:** tell the user what it says. If it's about the Mac itself, see step 1's *If it fails*. Otherwise do steps 0–4 below by hand.

Download the newest release into a temporary folder (it's only needed until step 3), with the code from their install line (the part after `/i/`):

```bash
rm -rf /tmp/stenny-setup && mkdir /tmp/stenny-setup
echo "X-Stenny-Invite: <their-code>" | curl -fL -H @- -o /tmp/stenny-setup/Stenny.zip "https://stenny.eu/api/download?asset=Stenny.zip"
ditto -x -k /tmp/stenny-setup/Stenny.zip /tmp/stenny-setup
```

After step 3, save the invite so Stenny finds its updates: `echo "<their-code>" | stenny invite --stdin`.

**Check:** `ls /tmp/stenny-setup/stenny.noindex/app/build.noindex/Stenny.app/Contents/Resources/engine/bin/python3` finds it.

Stenny's app is signed with its author's own certificate, not one from Apple. Say so in one sentence.

**If they already downloaded Stenny in a browser,** don't use that copy: macOS marks it as downloaded and refuses to open the app. Downloading it here, as above, doesn't.

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

## 2. Install the app

If Stenny is already installed and running, this replaces it; first make sure no recording is running (`stenny status` must say none is running, if `stenny` exists yet).

```bash
/tmp/stenny-setup/stenny.noindex/desktop/scripts/install-mac.sh --no-open /tmp/stenny-setup/stenny.noindex/app/build.noindex/Stenny.app
```

It installs in place, so the Dock, Spotlight and Launchpad show one Stenny. **Check:** the output ends with `✅ Installed: <path>/Stenny.app`: that's the app's path (remember it). `"<path>/Stenny.app/Contents/MacOS/ffmpeg" -version` prints a version. Don't open the app yet; that's step 7.

## 3. The `stenny` command

Stenny's engine (Python and the speech recognition) is inside the app; nothing else needs installing. Let it set itself up once:

```bash
PYTHONDONTWRITEBYTECODE=1 "<path>/Stenny.app/Contents/Resources/engine/bin/python3" -m stenny app-settings >/dev/null
rm -rf /tmp/stenny-setup
```

That writes `~/.local/bin/stenny`, a small launcher for the app's engine (`src/stenny/launcher.py`), and starts a config with `~/Documents/Stenny` as the Stenny folder. It also removes an engine an older Stenny setup installed with `uv` (`~/Applications/stenny.noindex`, which goes to the Trash), and never touches another `stenny` (a developer's).

**Check:** `~/.local/bin/stenny --version` prints a version. If plain `stenny` isn't found in the steps below, use `~/.local/bin/stenny`.

## 4. Start the Whisper download

Whisper is the speech recognition; it runs entirely on this Mac. Start it in the background so it runs while you talk: `stenny download-model` (about 1.6 GB, 2–10 minutes). If it was already on this Mac, it finishes within seconds.

## 5. Where the transcripts go

Stenny writes plain text files (Markdown) into one folder, the Stenny folder, which Claude reads: one folder per course, talks in `Talks`, glossaries and course files next to them. Ask "`~/Documents/Stenny` (Recommended)" / "Another folder". Another folder can be anywhere, also inside an Obsidian vault they already use (then e.g. `<vault>/Lectures`).

The config from step 3 (or the installer) already uses `~/Documents/Stenny`. For another folder, change `folder` in config.toml (`stenny where` shows where config.toml is); keep everything else in it.

**Check:** `stenny where` prints `folder: <the folder>`. (If the config was there before this setup, tell the user which courses are in it (`stenny courses`) and keep it, unless they want to start fresh.)

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
open "<the app's path>/Stenny.app"
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

Then restart Stenny, so it matches calendar events against these courses: `pkill -x Stenny; open "<the app's path>/Stenny.app"`.

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
open -a Claude "<the app's path>/Stenny.app/Contents/Resources/Stenny.mcpb"
```

The user clicks **Install** in the window Claude shows. You can't click it for them. In the app this is one button, **Connect** (setup card and *Settings ▸ Use with Claude*). If no window appears, don't send them dragging files: in the app, **Didn't work? ▸ Tell us** sends a report of what happened (system, Claude's version, Claude's log lines about extensions) so it can be fixed.

**Check:** Settings ▸ Customize ▸ Connectors lists **Stenny**, switched on. Then, in a new chat in the Claude desktop app, ask: *"Which courses do you have in Stenny?"* Claude should use Stenny's tools (the first time it asks: *Allow*) and list the courses from step 8. If Claude says Stenny isn't set up, it explains why: usually step 3. If the menu looks different, ask the user to describe or screenshot what they see and help from there.

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
- New versions: Stenny looks for them by itself; when there is one, the bottom of its window says *Update to …* (otherwise *Check for Updates…*) ▸ *Update Now*: Stenny downloads the new version, installs it and opens again by itself (never during a recording or a final transcript). No Terminal needed.
- Docs: https://stenny.eu/docs/quickstart/

**Stenny is made to be adjusted.** Whenever they want something different, they can reopen this chat and ask in plain language: "add a course", "change my pause shortcut".

**Feedback is very welcome:** *Send Feedback…* at the bottom of Stenny's window, or the form at https://stenny.eu.

Write a short setup report: Mac model (`sysctl -n hw.model`), memory, macOS version, Stenny version, and any step that needed a workaround. No names, courses or file paths. Show it and offer to send it to Stenny's author: "Send it (Recommended)" / "Keep it to myself". If yes, send exactly what you showed them:

```bash
PYTHONDONTWRITEBYTECODE=1 "<the app's path>/Stenny.app/Contents/Resources/engine/bin/python3" -c 'import json,sys; print(json.dumps({"source": "setup", "message": sys.argv[1]}))' "<the report>" | curl -fsS -X POST https://stenny.eu/api/feedback -H "Content-Type: application/json" -d @-
```

If it prints `{"ok":true}`, say it was sent; otherwise say it didn't go through and go on.

## Troubleshooting

| Problem | What to do |
|---|---|
| `stenny: command not found` | Use `~/.local/bin/stenny`. If that's missing too, step 3 writes it (the app also does at each start). |
| The app and `stenny` in the terminal show different versions | `~/.local/bin/stenny` isn't Stenny's launcher but another `stenny` (a developer's `uv tool install`, which Stenny leaves alone). `head -3 ~/.local/bin/stenny` shows which. |
| macOS says Stenny can't be opened | It was downloaded in a browser: step 0. |
| `ffmpeg` not found | `<path>/Stenny.app/Contents/MacOS/ffmpeg` should exist. If it doesn't, the app wasn't installed from a release: step 2. |
| Microphone permission was denied | System Settings ▸ Privacy & Security ▸ Microphone ▸ Stenny on. Then restart Stenny. |
| The shortcut does nothing | `stenny app-settings` must show `"error": null`. Another app may already use that shortcut: pick another (step 6) and restart Stenny (menu ▸ *Quit Stenny*, then open it again as in step 7). |
| "Only … GB free" when a recording starts | Stenny needs about 1.5 GB to start a lecture. Help them see what's big, but don't delete anything yourself. |
| The calendar lecture isn't offered | `stenny match-course "<event title>"` (step 14); events shorter than 20 minutes are ignored. |
| Transcript is empty or wrong language | Check the course's `language`; for lectures that switch languages use `"mixed"`. Check the microphone level meter. |
| The AI step fails ("claude CLI not found") | Step 9. Or set `[llm] backend = "none"`. The transcript is always written anyway. |
| Anything else | Logs are in `~/Library/Logs/Stenny/` (Windows: `%LOCALAPPDATA%\Stenny\Logs\`): `stenny.log` (the app's engine), `live.log` (the recordings, each one added under a `---` line), `queue.log` (final transcripts, also those started with Make Final Transcript Now). Read the end of the newest one. Send Feedback with the details ticked sends the end of all three. |

# Stenny on Windows (preview)

**Not yet tested on a real Windows PC.** Stenny's Windows version is being built (issue #2 on GitHub); only GitHub's Windows test machines have run it so far. Say this to the user in one sentence before you start, and that this setup is a real test of it. Some parts may not be there yet (recording from the microphone, the pause shortcut, lecture notifications, the background queue): when a step below can't work for that reason, say so in one line and go on, and note it for the report at the end.

**Commands.** Claude Code on Windows runs commands in Git Bash, so the commands below are written for it. The Windows-only parts run through `powershell -NoProfile -Command '…'` (single quotes, so Git Bash leaves PowerShell's `$env:…` alone). `$LOCALAPPDATA`, `$APPDATA` and `$USERPROFILE` work in Git Bash too. To open something, name the app: `explorer.exe "<folder>"`, `notepad.exe "<file>"`; web pages with `cmd //c start "" "https://…"`.

Greet the user as in Phase A, with "PC" for "Mac". The essential part takes about 20–30 minutes, mostly downloads. Windows will ask once whether to run the installer (it isn't signed yet, see W3).

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

**First, Stenny's installer.** It does W2–W5 and W11 in one go (the app through its setup program, with Stenny's engine inside it and without the *"Windows protected your PC"* warning, the `stenny` command, the Stenny folder `Documents\Stenny`, the task for final transcripts, the Whisper model) and starts Stenny. Run it in the background and say so:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -Command 'irm https://stenny.eu/i/<their-code>/win | iex'
```

As on a Mac, that's the user's own line from their invite; without one, stop and point them to https://stenny.eu.

If it ends with `Stenny … is installed`, go on with W6 (the config exists: for another folder change `folder` in config.toml). If it stops with `X`, tell the user what it says and do W2–W5 below by hand.

The Windows app comes as a setup program, `Stenny_<version>_x64-setup.exe`, attached to the release:

```bash
powershell -NoProfile -Command '(Invoke-RestMethod https://stenny.eu/api/release -Headers @{ "X-Stenny-Invite" = "<their-code>" }).assets | ? name -like "*-setup.exe" | % browser_download_url'
```

**Check:** it prints a link (one on stenny.eu, which wants the invite too: add `-H "X-Stenny-Invite: <their-code>"` to the download below). After W4, save the invite for Stenny's updates: `echo "<their-code>" | stenny invite --stdin`. If it prints nothing, the newest release's Windows app is still being built: wait a few minutes and try again.

## W3. Install the app

Download the setup program and start it:

```bash
curl -fL -H "X-Stenny-Invite: <their-code>" -o "$USERPROFILE/Downloads/Stenny-setup.exe" "<the link>"
cmd //c start "" "$USERPROFILE\Downloads\Stenny-setup.exe"
```

Stenny isn't signed with a certificate yet, so Windows shows **"Windows protected your PC"**. Tell the user to click **More info ▸ Run anyway**. The installer speaks English or German (Windows' language), installs for this user only (no administrator) into `%LOCALAPPDATA%\Stenny`, next to Stenny's own data, and sets up Microsoft's WebView2 if the PC lacks it. Its uninstaller removes only the app's own files, never recordings. They can uncheck *Run Stenny* at the end; it opens in W10.

**Check:** `"$LOCALAPPDATA/Stenny/ffmpeg.exe" -version` prints a version and `ls "$LOCALAPPDATA/Stenny/engine/python.exe"` finds the engine. If they picked another folder in the installer, both are in that one ("the app's folder" below).

## W4. The `stenny` command

As in step 3, with the engine next to `Stenny.exe`:

```bash
PYTHONDONTWRITEBYTECODE=1 "$LOCALAPPDATA/Stenny/engine/python.exe" -m stenny app-settings >/dev/null
rm -f "$USERPROFILE/Downloads/Stenny-setup.exe"
```

That writes `~/.local/bin/stenny.cmd`, which runs the app's engine, and starts a config with `Documents\Stenny`. An engine an older Stenny setup installed with `uv` (`%LOCALAPPDATA%\Programs\stenny`) is removed, its folder to the Recycle Bin. **Check:** `~/.local/bin/stenny.cmd --version` prints a version. Git Bash only adds `.exe` to a command by itself, so where the steps below say `stenny`, write `stenny.cmd` there. Nothing here needs administrator rights.

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

**b) Microphone, through the app:** only with the app installed (W3). Open it (`cmd //c start "" "$LOCALAPPDATA\Stenny\Stenny.exe"`), then as in step 11 b, with Stenny's icon in the taskbar's notification area (behind the **^** arrow if it's hidden). Windows asks for the microphone the first time. If the app can't record yet, skip this and say so.

Deleting the test recordings (step 11's list, with `%LOCALAPPDATA%\Stenny\Recordings\Talks\…` for the recordings, and `stenny-test.wav`): to the Recycle Bin, one at a time (`DeleteFile` for the .wav):

```bash
powershell -NoProfile -Command 'Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("<folder>", "OnlyErrorDialogs", "SendToRecycleBin")'
```

## W11. Finish waiting transcripts automatically

Step 12. If `stenny schedule install` says it can't do that on Windows yet, skip it and note it for the report.

## W12. Let Claude read the transcripts

As in step 13, with the Claude desktop app for Windows (claude.ai/download). The extension comes with the app as `%LOCALAPPDATA%\Stenny\Stenny.mcpb`; without the app, download it: `curl -fL -H "X-Stenny-Invite: <their-code>" -o "$USERPROFILE/Downloads/Stenny.mcpb" "https://stenny.eu/api/download?asset=Stenny.mcpb"`.

Claude on Windows reads a `.mcpb` it's handed only when it starts: a running Claude just brings its window up and ignores the file. So the app's **Connect** starts Claude with the file when Claude isn't running; if it is, the app asks the user to close Claude (*Close Claude. Stenny opens it again.*) and opens it with the file once it's gone. Claude then shows its Install window, and the user clicks **Install**. Without the app, do the same: have them quit Claude, then run `"$LOCALAPPDATA/AnthropicClaude/claude.exe" "<folder>/Stenny.mcpb"`. Claude from the Microsoft Store can't be started with a file (and its own Install extension is reported broken, anthropics/claude-code#102035): the app then shows **Didn't work?** with **Tell us**, which sends a report. **Check:** as in step 13.

## W13. Recommended, not required

Steps 15 (Claude projects) and 16 (Obsidian: the Windows download at obsidian.md/download) work the same on Windows.

## W14. Finish

As in step 17, with Windows' words: the taskbar's notification area instead of the menu bar, the Recycle Bin instead of the Trash, *Show in Explorer*. For the report: `powershell -NoProfile -Command '(Get-CimInstance Win32_ComputerSystem).Model'`, the memory, the Windows build, the graphics card, the Stenny version, and which steps couldn't work on Windows yet. Sending it is the same command as in step 17.

## Updating on Windows

As on a Mac: the bottom of Stenny's window says *Update to …* when there is a new version (or *Check for Updates…*) ▸ *Update Now* downloads the new setup program and runs it without questions (a progress window), then Stenny opens again. Never during a recording or a final transcript. When the user asks you to update Stenny, running the installer again (W2) does the same. **Check:** `stenny --version` shows the new version, and `stenny mcp --desktop-config` works.

## Troubleshooting on Windows

| Problem | What to do |
|---|---|
| `stenny: command not found` | Use `~/.local/bin/stenny.cmd`, which runs the engine inside the app. If it's missing, W4 writes it (the app also does at each start). |
| "Windows protected your PC" | The installer isn't signed yet: *More info ▸ Run anyway* (W3). |
| `ffmpeg` not found | `%LOCALAPPDATA%\Stenny\ffmpeg.exe` (or in the folder the installer was given) should exist; otherwise W3. |
| Transcription is very slow | Without an NVIDIA card (and its CUDA libraries) the processor does it: about as long as the lecture. That's expected. |
| Anything else | Logs are in `%LOCALAPPDATA%\Stenny\Logs\`. Read the end of the newest one. |

# Invites (closed alpha)

**For Stenny's author, and for anyone wondering how the install line works.** During the closed alpha the repo and its releases are private, and only stenny.eu hands Stenny out: to people with an invite, and only Hendrik makes invites. Invited users can't make invites. The website's side is in the stenny-website repo (`api/`, `lib/`, `admin/`).

- **An invite** is a code like `42-k7m2q9x4c3v8b6n5`: the number of an issue labelled `invite` in the private feedback repo, then 16 random characters. The issue keeps only the code's SHA-256, so the repo doesn't hold working codes. Closing the issue revokes the invite (within a minute); reopening it gives it back. Each run of its install line adds a comment to the issue.
- **Making one:** https://stenny.eu/admin/, with the admin password (`ADMIN_TOKEN` in Vercel's env vars, never in a repo). It shows the invite's two install lines and a message to send, in English or German, and lists everyone invited, with Revoke. The code is shown only once.
- **The install line** `curl -fsSL https://stenny.eu/i/<code> | zsh` (Windows: `irm https://stenny.eu/i/<code>/win | iex`) gets the newest release's `install.sh` / `install.ps1` with the code set on its first line. A wrong or revoked code gets a script that only says so. The old lines (`stenny.eu/install.sh`, `install.ps1`) say Stenny is invite-only.
- **Downloads** (`/api/download?asset=…`, `/api/release`) want the code in the `X-Stenny-Invite` header and redirect to GitHub's short-lived signed link for the file, so nothing big passes through Vercel.
- **Updates:** the install scripts save the code with `stenny invite --stdin` (in the Keychain or Windows' Credential Manager, `src/stenny/update.py`). The app's updater asks `https://stenny.eu/api/update` with the code in the same header (`desktop/src-tauri/src/updater.rs`); the answer is the release's `latest.json` with its downloads behind the gateway, still checked against Stenny's updater key. `stenny update` asks the same place. A revoked invite gets no more updates; Stenny keeps working.
- **Vercel env vars:** `ADMIN_TOKEN` (at least 20 characters), `RELEASES_TOKEN` (a fine-grained GitHub token with Contents: read-only on the Stenny repo), `RELEASES_REPO` (default `hendrikboettcher/stenny`), and the existing `FEEDBACK_TOKEN` / `FEEDBACK_REPO` (invites and invite requests are issues there).
- **What it can't do:** someone with Stenny can pass on the app they downloaded, or their line until it's revoked. Invites are personal and visible, so that shows up in the issue's install comments.
