# Setting up Stenny (instructions for Claude Code)

**For the person reading this:** you don't have to do anything here yourself. Open this folder in Claude Code and type `follow SETUP.md`. Claude Code will go through the steps below with you, explain each one, and ask you whenever it needs a decision or your password.

---

**For Claude Code.** You are setting up Stenny for someone who may never have used a terminal. Work through the steps below in order, together with them.

How to work:
- **Plain language.** Explain each step in one or two short sentences before you run it: what it does and why it's needed. No jargon. If you have to use a term (Homebrew, terminal, certificate), explain it once.
- **One step at a time.** Run the step, then run its **Check**. Only go on when the check passes. If it fails, look at the *If it fails* notes, then the *Troubleshooting* section at the end. If you're still stuck, explain the problem simply and ask the user how to go on. Never skip a failed step silently.
- **Check what's already there first.** Many Macs already have some of these tools, possibly from an earlier attempt, another app or another user account. Before installing anything, check whether it's installed. If it is and it's recent enough, tell the user in one line ("Homebrew is already installed, skipping that") and go on. If it's there but outdated, say so and offer to update it; update only if they agree, and go on with the old version if it's still recent enough. Never reinstall or remove something that works.
- **Ask, don't guess,** for anything personal: the folder for transcripts, courses, languages, the shortcut, the calendar.
- **Every question has a recommended answer.** Ask one thing at a time. If you have a tool for questions with clickable options (such as AskUserQuestion), use it for every choice: at most four options, your recommendation first and marked "(Recommended)", with a short reason; the user can always type something else. Ask for free text only where nothing can be suggested (for example a course name you couldn't find anywhere). Never make the user write long answers that cover several things at once.
- **Always name the app when you open something.** A plain `open <link or file>` lets macOS choose, and on a fresh account that can be any installed app that claims the type (Notion Calendar for calendar links, RStudio or Xcode for Markdown files). Use `open -a Calendar …`, `open -a Obsidian …`, `open -a TextEdit …` and so on. Web pages (`open https://…`) may go to the default browser.
- **Keep the user out of the Terminal.** Run every command yourself. The user only clicks: macOS dialogs, System Settings, the browser, the Claude app. The single exception is installing Homebrew (step 2), which needs their password in a terminal; there, give them exactly one line to paste.
- **Passwords:** you can't type passwords. When a command needs `sudo` or opens a macOS password dialog, tell the user what will appear and that it's macOS asking, not you. For commands that need `sudo` in the terminal, ask the user to run them in their own Terminal window (give them the exact line to copy) and to tell you when it's done.
- **Don't change anything** outside this folder, the user's chosen transcripts folder, `~/Lectures Data`, the login keychain (step 4) and what the steps below install. Don't install a launchd agent unless the user chooses it in step 14.
- Long commands (building, downloading) take minutes: run them in the background, say roughly how long they take, and report when they finish.
- At the end, give a short summary of what was set up and what to do on the day of the first lecture.

Start by greeting the user and giving a three-line overview: Stenny records lectures from the menu bar, transcribes them on this Mac (nothing is uploaded), and saves text files that Claude can study with. Setup takes about 15–30 minutes, depending on what's already installed; most of it is waiting for downloads, and you'll only need them for a few clicks and questions.

## 1. Check the Mac

Stenny needs a Mac with Apple Silicon (M1 or newer), macOS 14.4 or newer and about 5 GB of free disk space.

```bash
uname -m                          # must be arm64
sw_vers -productVersion           # must be 14.4 or higher
df -h ~ | tail -1                 # "Avail" should be at least 5G
```

**Check:** `arm64`, a version ≥ 14.4, ≥ 5 GB free. The 5 GB are for a Mac that has nothing yet; every tool that is already installed (steps 2–3) or a Whisper model that's already downloaded (step 10) needs less.

*If it fails:* on an Intel Mac (`x86_64`) Stenny can't run (its transcription engine needs Apple Silicon): tell the user kindly and stop. If macOS is older, they can update it in System Settings ▸ General ▸ Software Update and come back. (If it's new enough but an update is waiting there, mention it, but it's not needed for Stenny.) If the disk is too full, help them see what's big (`du -sh ~/Downloads ~/Movies ~/Desktop 2>/dev/null`), but don't delete anything yourself.

**Where the folder lives:** Stenny runs from this folder, so it must stay where it is after setup. If it's in Downloads or on the Desktop, suggest moving it to a permanent place first, for example `~/Applications/stenny` (`mkdir -p ~/Applications && mv "<this folder>" ~/Applications/stenny`). If you move it, tell the user to reopen Claude Code in the new place (`cd ~/Applications/stenny && claude`, then `follow SETUP.md` again; finished steps will pass their checks quickly).

## 2. Apple's Command Line Tools and Homebrew

**Command Line Tools** are Apple's free developer basics (the compiler that builds the menu bar app).

```bash
xcode-select -p
```

**Check:** prints a path (e.g. `/Library/Developer/CommandLineTools`, or one inside `Xcode.app` if the full Xcode is installed; both work). If so, they're already there: say so and go on. If not, run `xcode-select --install`: a macOS dialog appears, the user clicks *Install* and accepts. It downloads about 1.8 GB and takes 5–15 minutes. Wait until the user says it's done, then check again.

Already installed but old? `softwareupdate --list 2>&1 | grep -i "command line"` shows an update if there is one. Offer it (System Settings ▸ General ▸ Software Update installs it). It's only *needed* if the build in step 4 fails.

**Homebrew** is a free, widely used installer for command-line tools on the Mac (brew.sh). Stenny uses it to install two tools.

```bash
command -v brew || ls /opt/homebrew/bin/brew
```

**Check:** a path is printed. Then check that this user may install with it (on a Mac shared by several accounts, Homebrew often belongs to whoever installed it first):

```bash
B=$(command -v brew || echo /opt/homebrew/bin/brew); [ -w "$("$B" --prefix)" ] && echo "can install" || echo "Homebrew belongs to another user"
```

- **"can install":** Homebrew is ready. Say it's already there and go on to step 3.
- **"Homebrew belongs to another user":** don't try to fix the permissions (that would change things for the other account). Go to step 3 anyway: if `uv` and `ffmpeg` are already installed through it, they can simply be used. Step 3 says what to do for whatever is missing.
- **No path:** Homebrew is missing. It must be installed by the user in their own Terminal, because it asks for their Mac password:
1. Ask them to open the Terminal app (⌘-Space, type "Terminal", Enter).
2. Give them this line to paste and press Enter:
   `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`
3. It asks for their Mac password (typing shows no dots, that's normal) and to press Enter to continue. It takes a few minutes.
4. At the end it shows "Next steps" with two or three lines to run. Ask them to run those too (they add `brew` to the terminal's PATH).

Then check again with `/opt/homebrew/bin/brew --version`. In your own shell, use `/opt/homebrew/bin/brew` (or run `eval "$(/opt/homebrew/bin/brew shellenv)"` first), since your session may not see the new PATH.

## 3. Install the tools and the `stenny` command

`uv` installs Python programs (and Python itself, if needed); `ffmpeg` reads and converts audio. Then `stenny` itself is installed from this folder.

First see what's already there (`HOMEBREW_NO_AUTO_UPDATE=1` keeps Homebrew from updating itself just for this check):

```bash
command -v uv && uv --version
command -v ffmpeg && ffmpeg -version | head -1
HOMEBREW_NO_AUTO_UPDATE=1 brew outdated uv ffmpeg     # lists the ones with a newer version
```

- **Both installed:** say so. If `brew outdated` lists them, offer `brew upgrade uv ffmpeg` (a minute or two); it's optional, Stenny works with the installed versions. Only `uv` older than 0.8 should really be updated.
- **Missing, and Homebrew can install (step 2):** `brew install uv ffmpeg` (only the missing ones).
- **Missing, and Homebrew belongs to another user:**
  - `uv` can be installed for this user alone, without Homebrew or a password: `curl -LsSf https://astral.sh/uv/install.sh | sh` (it goes to `~/.local/bin`).
  - `ffmpeg` has no such installer. Ask the user to install it once from the account that owns Homebrew (`brew install ffmpeg` there); after that every account can use it. Don't download ffmpeg from other websites.

Stenny's menu bar app looks for `ffmpeg` and `stenny` in `/opt/homebrew/bin`, `/usr/local/bin` and `~/.local/bin`. If `command -v ffmpeg` shows another place (for example MacPorts' `/opt/local/bin`), the app won't find it: then install it with Homebrew as above.

Now `stenny` itself. Check first whether it's already installed (e.g. from an earlier attempt):

```bash
grep -o 'editable = "[^"]*"' "$(uv tool dir)/stenny/uv-receipt.toml" 2>/dev/null   # the folder it was installed from
```

No output: not installed yet. The same folder as this one: already done, say so and skip to the check. Another folder: tell the user and ask before switching it to this folder (`--force` below). Then:

```bash
uv tool install -e . --python 3.12   # add --force to replace an existing install
uv tool update-shell              # makes sure ~/.local/bin is on the PATH in new terminals
```

**Check:** `~/.local/bin/stenny --help` prints the list of commands, and `ffmpeg -version` prints a version.

Always pass `--python 3.12`: without it uv may pick the newest Python on the Mac, which some of Stenny's dependencies (PyTorch) don't support yet, and the install fails.

*If it fails:* if `uv tool install` can't find Python 3.12, run `uv python install 3.12` and retry. If it complains the tool already exists, add `--force`.

## 4. Build the menu bar app

First a **signing certificate**: macOS remembers permissions (microphone, calendar) per app signature. Without a fixed certificate, every rebuild would look like a new app and ask again. The script creates a private, self-signed certificate called "Stenny Dev" in the user's login keychain. It's only used to sign Stenny on this Mac; nothing is sent anywhere.

```bash
app/make-cert.sh
```

If the certificate already exists (an earlier attempt), the script says so and changes nothing.

**Check:** `security find-identity -p codesigning | grep "Stenny Dev"` prints a line (it may say `CSSMERR_TP_NOT_TRUSTED`; that's fine, signing works anyway).

*If it fails* (for example `security import` errors), use the manual fallback. Ask the user to:
1. Open **Keychain Access** (⌘-Space, "Keychain Access").
2. Menu **Keychain Access ▸ Certificate Assistant ▸ Create a Certificate…**
3. Name: `Stenny Dev`. Identity Type: *Self-Signed Root*. Certificate Type: *Code Signing*. Click *Create*, then *Continue*/*Done*.

Then run the check again. If neither works, go on without it: build.sh signs "ad hoc" and Stenny works, but macOS asks for the microphone again after every rebuild. Tell the user.

Now build the app (1–3 minutes the first time) and install it to /Applications (or to `~/Applications`, if another account's Stenny is already in /Applications or this account may not write there; build.sh decides and never replaces another account's copy). If Stenny is already installed and running, the build replaces it; first make sure no recording is running (`stenny status` must say none is running), and tell the user the app will restart.

```bash
app/build.sh
```

The first time, macOS may ask whether `codesign` may use the key "Stenny Dev": the user enters their Mac (login) password and clicks **Always Allow**.

**Check:** the output ends with `✅ Installed: <path>/Stenny.app` (remember that path: steps 10 and later open the app from there) and `✅ Signed with "Stenny Dev"` appears (or the ad-hoc warning, if you skipped the certificate). Don't open the app yet; that's step 11.

*If it fails:* "Swift not found" → step 2's Command Line Tools are missing. A compiler error mentioning the SDK → `softwareupdate --list` may offer newer Command Line Tools; or reinstall them (`sudo rm -rf /Library/Developer/CommandLineTools` in the user's own Terminal, then `xcode-select --install`). Show the user the last lines of the error.

## 5. Where the transcripts go (and Obsidian)

**First, start the Whisper download in the background** (step 10), so it runs while you talk: `stenny download-model`. Tell the user it's downloading about 1.6 GB.

Stenny writes plain Markdown text files into one folder of the user's choice. Explain, then ask:

- **Obsidian** (free, obsidian.md) is the recommended way to read them: lectures, topics, glossaries and slides are linked, the menu's *Open Transcript* opens the live draft there, and slide links open the PDF at the right page. Stenny and Claude work the same without it; any folder works and the files open in any text editor.

Check whether Obsidian is installed (`ls -d /Applications/Obsidian.app ~/Applications/Obsidian.app 2>/dev/null`) and which vaults it knows (`cat ~/Library/Application\ Support/obsidian/obsidian.json 2>/dev/null`, the `"path"` entries). Then ask, with options:
- **They have vaults:** offer each vault (recommended: the one that looks like their study vault) and "a new, separate folder".
- **Obsidian isn't installed:** "Install Obsidian (Recommended)" or "No, just a folder". To install: if Homebrew can install for this user (step 2), run `brew install --cask obsidian` yourself (a minute); otherwise open https://obsidian.md/download for them (`open` it) and wait until they've dragged it to Applications.
- **New folder:** suggest `~/Documents/Study Vault` (recommended) and create it. If they use Obsidian, ask them to open Obsidian ▸ *Open folder as vault* ▸ choose that folder (⌘⇧G lets them paste the path), so Obsidian knows it.

Then the folder inside it for lectures: recommend `Lectures` (Stenny only ever writes into that folder, plus a `Recordings` folder next to it for talks).

Create the settings file:

```bash
stenny init --vault "<the folder>" --lectures-dir "Lectures"
```

**Check:** `stenny courses` prints `Lectures: <the folder>/Lectures` and the two example courses. (If a config already exists, `stenny init` refuses: tell the user what's in it, and ask whether to keep it (Recommended, if it has their courses) or start fresh.)

## 6. Calendar

Stenny can offer the lecture that's on now: from 10 minutes before a lecture, its menu shows *Start <course>* and a notification asks *Record this lecture?* with a **Start Recording** button. It never starts by itself. For that, the lectures must be in the Mac's Calendar app. The timetable also lets you suggest their courses in step 7, so they don't have to type them.

Ask (recommended: yes) whether their university offers the timetable as a calendar subscription: often a link called "iCal", "ICS", "webcal" or "export/subscribe to calendar" in the course or timetable system. Options: "I have the link" (Recommended), "It's already in my Calendar app", "I don't have one".

- **They paste a link:** subscribe for them: `open -a Calendar "webcal://<the link without https://>"` opens the Calendar app's subscribe dialog with the link filled in; they click *Subscribe*, set *Auto-refresh* to *Every hour* (recommended) and click *OK*. Then read the course names from it: `curl -sL "<link>" | grep "^SUMMARY" | sed 's/^SUMMARY[^:]*://' | sort | uniq -c | sort -rn | head -40`.
- **Already in Calendar** (also Google or Exchange calendars added in System Settings ▸ Internet Accounts): fine. You can't read the Calendar app directly; in step 7, ask for a screenshot of a typical week or of their timetable instead, if they like.
- **None:** fine, skip it. Stenny works from its menu; the calendar can be added any time later.

**Check (after step 11, once Stenny has calendar access):** run `stenny match-course "<title>"` for one or two real event titles; each must print the right course. If not, add a word that's in the titles to that course as `calendar = ["<word>"]` (or an alias) and check again.

## 7. Courses

Find the courses with as little typing as possible:
- From the timetable (step 6): event titles usually contain the course name plus extras such as "VL", "Lecture", "Übung", a room, an ID or lecturer names. Turn them into clean course names and ask which ones they're taking (several selected at once; if more than four, ask in groups).
- Without a timetable: offer to read a screenshot or PDF of it (they can drag it into this chat); only if that's not possible, ask for the names one at a time.

Then, **one course at a time**, ask with options:
- **Language** of the lecture. Guess from the course name and offer, for example: "English (Recommended)", "German", "Mixed: German and English", "Other". Explain *mixed* once: for lecturers who switch languages within a lecture; with a fixed language Whisper tends to translate the other passages instead of writing them down. (`auto` is for courses where each lecture is in one language, but not always the same one.)
- **Short name** for the terminal and calendar matching: suggest one or two (e.g. `micro` for Microeconomics; recommended: the abbreviation students actually use) and "No short name". Short names of 3+ letters also match calendar events.

Show the final list in a short table and ask "Looks right (Recommended)" / "Change something". Then edit `~/Lectures Data/_System/config.toml`: **delete the two example courses** (`Calculus`, `International Economics`) at the end, and add one table per course, keeping everything else:

```toml
[courses."Microeconomics"]
language = "en"
aliases = ["micro"]

[courses."Business Communication 2 – French"]
language = "mixed"
languages = ["fr", "de"]
aliases = ["bizcom"]
```

**Check:** `stenny courses` lists exactly their courses with the right languages. If they gave you real event titles, `stenny match-course "<title>"` finds the right course for each.

## 8. The AI step (the `claude` command)

Stenny can add section headings, a summary and checked fixes for misheard terms. It uses the user's Claude subscription through the `claude` command (Claude Code in the terminal), at no extra cost. The Claude desktop app does **not** install this command, so check:

```bash
command -v claude || ls ~/.local/bin/claude 2>/dev/null
claude auth status 2>/dev/null
```

- **Installed and `"loggedIn": true`:** say so and go on.
- **Missing:** ask "Install it (Recommended)" / "Skip the AI step". To install, run Anthropic's installer yourself (no password needed): `curl -fsSL https://claude.ai/install.sh | bash`.
- **Not signed in:** run `~/.local/bin/claude auth login` in the background. It opens the browser: the user signs in with their Claude account and clicks *Authorize*. Then check `claude auth status` again. (If it instead shows a code to paste or waits for input, ask the user to open Terminal once, type `claude`, sign in there and type `/exit`.)
- **Skip:** set `[llm] backend = "none"` in the config. Transcripts work the same, just without headings and summaries; it can be turned on later.

**Check:** `claude auth status` shows `"loggedIn": true` (or the backend is `"none"`).

## 9. The pause shortcut

Stenny has one global keyboard shortcut: pause and resume the recording from any app (the microphone is really off while paused). The default is **⌘⇧Ü** (Command-Shift-Ü), what the author uses; Ü sits right of P on German keyboards.

Find their keyboard layout:

```bash
defaults read com.apple.HIToolbox AppleCurrentKeyboardLayoutInputSourceID
```

Ask with options:
- German, Swiss or Austrian layout: "⌘⇧Ü (Recommended)", "⌃⌥P", "Something else".
- Any other layout (no Ü key): "⌃⌥P, Control-Option-P (Recommended)", "⌘⇧. (Command-Shift-Period)", "Something else". Mention that the global shortcut wins over the same shortcut in other apps, so it should be one they don't use.

Set it in the config under `[app]`:

```toml
[app]
pause_shortcut = "ctrl+opt+p"
```

Format: modifiers `cmd`, `shift`, `ctrl`, `opt`, joined with `+`, then one key as printed on their keyboard (or `space`, `f1`–`f20`). At least one of cmd/ctrl/opt.

**Check:** `stenny app-settings` prints their shortcut and `"error": null`.

## 10. The Whisper model

Whisper is the speech recognition model; it runs entirely on this Mac. You started the download in step 5 (`stenny download-model`, about 1.6 GB, 2–10 minutes). Check that it finished; if you didn't start it yet, run it now in the background. If the model was already on this Mac for this user, it finished within seconds: say it was already there.

**Check:** it ended with `Done: …/models--mlx-community--whisper-large-v3-turbo/…`.

*If it fails:* network errors → just run it again (it resumes). Behind a university proxy/VPN, try another network.

## 11. Open Stenny and allow access

```bash
open "<path from step 4>/Stenny.app"     # /Applications/Stenny.app or ~/Applications/Stenny.app
```

Tell the user where to look: **Stenny's icon in the menu bar** (top right of the screen) is a small stick figure; while recording it turns into a red dot with the running time, and while paused into a pause symbol. Everything is in the menu that opens when they click it. Then tell them what to expect and to click **Allow** each time:
- **Calendar** access (right away), so it can offer the current lecture. Nothing is changed or sent anywhere.
- **Notifications** (right away).
- **Microphone** (the first time a recording starts, step 12).

Then two settings, one click path each:
- Notification style, so the *Start Recording* button stays on screen: **System Settings ▸ Notifications ▸ Stenny ▸ Alert Style ▸ Persistent** (on macOS 14 it's called *Alerts*). Recommended.
- Start with the Mac: **System Settings ▸ General ▸ Login Items ▸ + ▸ Stenny**. Recommended, so the notification works without thinking of it.

To show the notification once: quit Stenny (its menu ▸ *Quit Stenny*), then `open "<path>/Stenny.app" --args --test-notification`.

**Check:** the icon is there; its menu ▸ *Start Lecture* lists their courses. `tail -20 ~/Lectures\ Data/_System/stenny.log` shows `pause shortcut: …` with their shortcut and `hotkey registered`.

*If it fails:* "No courses in …config.toml" in the menu → step 7. No icon → the menu bar may be full (on a MacBook with a notch, icons hide behind it; quit some menu bar apps). Permissions denied by accident → System Settings ▸ Privacy & Security ▸ Microphone / Calendars ▸ turn Stenny on.

## 12. Test recording

Two short tests, saved as a *talk* (not a lecture), so they don't count as lecture 1 of a course. You'll delete them afterwards.

**a) Transcription without the microphone** (checks Whisper and the AI step), run by you:

```bash
say -o /tmp/stenny-test.aiff "This is a short test of Stenny. The lecture today is about supply and demand."
stenny add /tmp/stenny-test.aiff --event "Stenny test" --now
```

**Check:** it ends without errors, and a transcript exists: `ls "<vault>/Recordings/Stenny test/"*/`. Show the user the text right here in the chat (read the `.md` file); if they want to see the file itself, open it with `open -a Obsidian` (if they use Obsidian) or `open -a TextEdit`. If the log says the AI step was skipped, go back to step 8.

**b) Microphone, through the app** (about 30 seconds):
1. Click Stenny's menu bar icon ▸ *Microphone* and check the right microphone is ticked (recommended: the Mac's own; virtual devices such as "Microsoft Teams Audio" or "ZoomAudioDevice" aren't real microphones).
2. Stenny's icon ▸ *Start Lecture* ▸ *Talk or Event…*, enter `Mic test`, start it, and click **Allow** when macOS asks for the microphone. The icon turns into a red dot with the time.
3. Talk normally for about 10 seconds. Press the pause shortcut (the icon shows paused), wait a few seconds, press it again, talk for another 10 seconds.
4. Click the red dot with the time ▸ *Stop & Make Final Transcript*. Wait about a minute.

**Check:** `stenny check` shows no missing audio, and the transcript in `<vault>/Recordings/Mic test/<date>/` contains what they said, with a `*[Recording paused …]*` line. If the text is empty: check the selected microphone and the level meter in the open menu while recording.

Afterwards, ask "Delete the test recordings (Recommended)" / "Keep them". If delete: move only these to the Trash: `<vault>/Recordings/Stenny test`, `<vault>/Recordings/Mic test`, `~/Lectures Data/_Recordings/Stenny test`, `~/Lectures Data/_Recordings/Mic test`, `/tmp/stenny-test.aiff`.

## 13. Claude projects (one per course)

This step is worth taking time for: it's how the user studies with the transcripts. Each course gets a Claude project, and its chats read the newest transcripts and summaries straight from this Mac, cite them ("Lecture 3 [0:12:30]") and keep the course glossary up to date, which makes later transcripts more accurate. Nothing has to be uploaded by hand after each lecture. You can't create claude.ai projects or change Claude's settings yourself, so guide the user through it, one click at a time.

**a) Create the folders Claude will read.** They only appear with the first recording, but the connector needs them now. Use the vault and lectures folder from step 5:

```bash
mkdir -p "<vault>/Lectures" "<vault>/Recordings"
```

(If they set `[recordings] folder`, use that instead of `Recordings`.) Tell the user both full paths; they'll pick them in a moment.

**b) Turn on the Filesystem connector** (if the Claude desktop app isn't installed, it's at claude.ai/download; if the connector is already on, just add the two folders) in the **Claude desktop app** (it doesn't exist on the website or the phone app; the chats that read the transcripts must run in the desktop app on this Mac):
1. Claude desktop app ▸ Settings ▸ **Connectors** (in some versions **Extensions**; it may be under *Browse connectors/extensions*) ▸ **Filesystem** ▸ install or enable it.
2. In its settings, add the allowed folders: the `Lectures` folder and the `Recordings` folder from a). In the folder picker, ⌘⇧G lets them paste a path. Only these two, not the whole home folder or the whole vault.
3. Save, and restart the Claude app if it asks.

If the menu looks different from these names, ask the user to describe or screenshot what they see and help from there.

**c) One project per course.** For each course, one at a time:
1. Copy the course's instructions to the clipboard for them: `stenny project-instructions --course "<name>" | pbcopy`.
2. In the Claude desktop app: **Projects ▸ New project**, named after the course.
3. In the project: **Instructions** (*Set project instructions*) ▸ paste (⌘V) ▸ Save.
4. Optionally add the course's slides, syllabus or reading list as project files.

**Check right away:** in one of the new projects, start a chat and ask: *"Which folders and files can you see in my Lectures folder?"* Claude should use the Filesystem connector (the first time, it asks for permission: *Allow*) and answer that the folder exists but is still empty, or list `_Glossaries` etc. If it says it has no access or doesn't know the folder, go back to b): the connector is off, or the folder wasn't added. After the first real lecture, *"What was the last lecture about?"* will work.

## 14. Finish waiting transcripts automatically

On battery, final transcripts wait in a queue until the Mac is plugged in. Ask: "Finish them automatically when plugged in (Recommended)" / "I'll start them myself". The first installs a small background job (a "launchd agent") that checks every 15 minutes, only on power; `stenny schedule uninstall` removes it. Only if they choose it:

```bash
stenny schedule install
```

## 15. Done

Summarise for the user, short:
- Before a lecture: wait for the notification, or click Stenny's icon ▸ *Start Lecture* ▸ course. Keep the MacBook **open** while recording.
- While recording, the icon is a red dot with the time. Pause/resume: their shortcut. Stop: click the red dot ▸ *Stop & Make Final Transcript*. On power, the final transcript of a 3-hour lecture is ready after about 20–25 minutes; on battery it waits until they plug in.
- An iPhone nearby (same Apple ID) can be the microphone (*Microphone ▸*), placed closer to the lecturer.
- Slides: drop the PDF into the lecture's folder.
- **Ask the lecturer first.** Many universities need the lecturer's consent before a lecture is recorded. Keep recordings and transcripts to themselves.
- Docs: https://stenny.hendrikboettcher.com/docs/quickstart/ (recording, microphones, slides, studying with Claude, troubleshooting).

End with this, in your own words: **Stenny is made to be adapted.** It's open source, and it now lives in this folder on their Mac. Whenever they want something changed, they can come back: open this folder in Claude Code (or this same chat) and just say what they want, for example "add a new course", "next semester I take these courses", "change my pause shortcut", "the transcript misheard this name", or an idea for a new feature. Claude Code reads the project's notes (CLAUDE.md) and can change Stenny for them; the changes stay on their Mac.

## Troubleshooting

| Problem | What to do |
|---|---|
| `stenny: command not found` | Use `~/.local/bin/stenny`, run `uv tool update-shell` and open a new terminal. |
| `brew: command not found` right after installing Homebrew | `eval "$(/opt/homebrew/bin/brew shellenv)"`, and add that line to `~/.zprofile` if the installer didn't. |
| Build fails | Show the last 20 lines. Most often the Command Line Tools are missing or outdated (step 2). |
| macOS asks for the microphone after every rebuild | No "Stenny Dev" certificate: step 4, then `app/build.sh` again. |
| Microphone permission was denied | System Settings ▸ Privacy & Security ▸ Microphone ▸ Stenny on. Then restart Stenny. |
| The shortcut does nothing | `stenny app-settings` must show `"error": null`. Another app may already use that shortcut: pick another (step 9) and restart Stenny (menu ▸ *Quit Stenny*, then open it again as in step 11). |
| The calendar lecture isn't offered | `stenny match-course "<event title>"` (step 6); events shorter than 20 minutes are ignored. |
| Transcript is empty or wrong language | Check the course's `language`; for lectures that switch languages use `"mixed"`. Check the microphone level meter. |
| The AI step fails ("claude CLI not found") | Step 8: install and sign in to the `claude` command. Or set `[llm] backend = "none"`. The transcript is always written anyway. |
| Anything else | Logs are in `~/Lectures Data/_System/`: `stenny.log` (the menu bar app), `live.log` (the running recording). Read the end of the newest one. |
