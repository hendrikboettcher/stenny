# Stenny

Stenny records your lectures on your Mac or Windows laptop and transcribes them right there, with Whisper. The transcripts are plain Markdown files in a folder of your choice, and Stenny's connector lets Claude read them, so any chat can answer *"What did the lecturer say about k-means in lecture 4?"* and cite the minute.

- **Private:** audio never leaves your computer.
- **Free:** transcription costs nothing. An optional AI step (headings, a summary, fixes for misheard terms) runs on your Claude subscription or the Anthropic API.
- **Nothing invented:** the AI never rewrites what was said, and the raw transcript is always kept.

**Docs: [stenny.eu/docs](https://stenny.eu/docs/)**

> **Ask before you record.** Many universities need the lecturer's consent. Check your university's rules and keep recordings to yourself.

## Install

**Mac** (Apple chip, macOS 14.4 or newer): in Terminal, run

```bash
curl -fsSL https://github.com/hendrikboettcher/stenny/releases/latest/download/install.sh | zsh
```

**Windows** (10 or 11, 64-bit Intel or AMD): in PowerShell, run

```powershell
irm https://github.com/hendrikboettcher/stenny/releases/latest/download/install.ps1 | iex
```

It asks nothing, needs no password and takes about 5 minutes, mostly downloading the speech model (1.6 GB). Run it again to update. What it sets up, and how to remove it: [Installation](https://stenny.eu/docs/installation/). The scripts are [install.sh](scripts/install.sh) and [install.ps1](scripts/install.ps1).

## Development

```bash
uv run pytest      # tests, no model download needed
app/build.sh       # build and install the Mac app
```

[CLAUDE.md](CLAUDE.md) has the architecture and the rules the code follows.

## License

[MIT](LICENSE)
