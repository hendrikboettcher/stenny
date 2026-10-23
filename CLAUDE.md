# CLAUDE.md

Developer notes for Stenny, a lecture transcription tool for Apple Silicon Macs: university lectures (2–4 h, any language, sometimes mixed) → clean, lossless Markdown in a folder such as an Obsidian vault, to be used later as AI study context.

**Setting Stenny up for a user?** Follow [SETUP.md](SETUP.md) step by step instead of these notes.

## Goals (in priority order)
1. **Nothing from the lecture is lost.** The raw Whisper output is always kept as its own file. Post-processing may structure the text but must never invent, drop or paraphrase content.
2. **High transcript quality** with a local Whisper model (default: large-v3-turbo through mlx-whisper on Apple Silicon).
3. **Nearly free.** Transcription is local. Optional LLM post-processing is cheap: Claude Code `claude -p` (subscription) or the Anthropic API with Claude Haiku. Target is under $5 for 100+ lectures.
4. **The Mac stays usable.** Heavy work runs at low priority and can wait until the Mac is on AC power or until night.
5. **Private.** Audio never leaves the machine. Only transcript text goes to the optional LLM step.

## Stack
- Python 3.12, uv, packaged as the `stenny` CLI (`uv tool install -e .`)
- mlx-whisper (Metal) for transcription; ffmpeg for audio decoding/encoding
- pypdf for slide text (poppler's `pdftotext` is preferred when installed)
- Optional: `claude` CLI or the `anthropic` SDK for structuring and summaries
- pytest for tests

## Layout
```
src/stenny/
  cli.py          argparse entry point (`stenny …` subcommands)
  config.py       ~/Lectures Data/_System/config.toml (or $STENNY_CONFIG): paths, model, courses, pause shortcut
  audio.py        load/convert/archive audio via ffmpeg
  engines/        Whisper engines behind one interface (mlx first)
  textproc.py     segment cleanup: hallucination filter, gap filling, paragraphs
  language.py     fixed or per-chunk language handling
  glossary.py     per-course glossary: Whisper prompt (token budget, glossary first) + deterministic replacements;
                  the "Terms (added automatically)" section is the only part the tool writes
  slides.py       the lecture's slides PDF: page text, prompt terms, reference text for the LLM
  course_files.py generated _Course Files/<Course> (overview|all lectures|summaries).md and _Topics/<topic>.md;
                  renumbers lectures, sets previous/next links
  pipeline.py     audio → segments → paragraphs → files; `postprocess` (text + AI step) and `refresh` (AI step
                  again from segments.json, no re-transcription)
  llm/            backends (claude_code, anthropic_api) + guard (no invented content)
  render.py       Markdown + YAML frontmatter
  vault.py        safe writes into the vault, folder rule enforcement
  jobs.py         queue, AC-power check, priority wrappers
  live/           live mode: sources.py (mic/file via ffmpeg), session.py (record, chunk, draft,
                  pause/resume, spans → lecture time), control.py (Unix socket JSON API)
app/              Swift menu bar app (SwiftPM + build.sh): starts `stenny live`, pause shortcut (`[app] pause_shortcut`,
                  read via `stenny app-settings`, default ⌘⇧Ü), status via socket; make-cert.sh: signing certificate;
                  also builds `stenny-rec`, the native microphone recorder bundled in Stenny.app
tests/            pytest, no model downloads needed
bench/            engine benchmark scripts
```

Vault output: `<vault>/<lectures_dir>/<Course>/<YYYY-MM-DD>/<Course> <YYYY-MM-DD>{.md, (summary).md, (slides).pdf}`, plus `<lectures_dir>/_Glossaries/<Course> (glossary).md`, `<lectures_dir>/_Course Files/` and `<lectures_dir>/_Topics/`. **All file names come from `vault.LectureFiles`** (older lectures keep `Transcript.md` etc.). The files are meant as AI context: keep them self-describing (course, lecture number, date in frontmatter and title) and linked to each other.

## Rules
- **Never create, modify or delete anything in the vault outside the configured `lectures_dir`.** All vault writes go through `vault.py`.
- **Folder rule:** each folder contains either only folders or only files, never both. `vault.py` enforces this; don't bypass it.
- **Raw transcript is sacred:** the raw output (`~/Lectures Data/<Course>/… (raw).md`, outside the vault) is written before any post-processing and never changed by the LLM step.
- **The LLM never rewrites text.** It returns an edit list (section headings with optional slide ranges, term fixes) that `llm/guard.py` validates and applies. A term fix must point to a glossary term, or to words found verbatim in the slide text (stricter where Whisper was confident). Slide ranges must exist in the PDF. Free-form LLM text appears only in `Summary.md`, clearly labelled. Topics and automatic glossary terms don't change the text, but they too need evidence (`guard.validate_topics` / `validate_terms`): a topic's words must be in the transcript or slides, a term on the slides or transcribed the same way twice. Never add corrections automatically.
- `segments.json` (outside the vault) keeps every segment plus the lecture details (`meta.lecture`), so `stenny refresh` can rebuild everything after Whisper without the audio.
- **Only what you or Claude read goes into the vault** (transcripts, summaries, glossaries, course files, topic notes). Everything else (audio, segments.json, raw output, queue, logs, config) lives in ONE folder, `~/Lectures Data/` (`paths.py`; `data_dir` in the config). Never upload audio anywhere.
- **No hardcoded personal paths, names or courses in code, tests or docs.** Everything comes from the config; examples stay neutral.
- Heavy commands must respect the priority settings (`nice`/`taskpolicy`) unless the user passes `--now`.
- **Microphone capture goes through `stenny-rec` (AVAudioEngine), never through Python** and not through ffmpeg's avfoundation input, which silently dropped ~1 s every ~8 s. Check every change to live mode with `stenny check`.
- **The backup microphone only fills "no input" gaps** of the main one (final pass, `fill_from_backup`); never mix it into audio the main microphone delivered. It stops on pause like the main one. Its text is marked `backup` in segments and paragraphs.
- **Pause means the microphone is off:** while paused, the audio source is stopped; never record or buffer paused audio.
- Live timestamps are lecture-clock time (pauses included); keep `spans`/`pauses` in session.json consistent with that.
- **Keep README.md, SETUP.md and config.example.toml current** in the same commit as a change to a feature, a command, a setting or a storage location.
- Don't install large dependencies (>100 MB) or persistent system config (launchd agents) without asking the user.

## Commands
```bash
uv run pytest                          # tests
uv run stenny add <audio> --course "<name>" [--lang fr] [--no-llm]
uv run stenny add <audio> --course "<name>" --slides deck.pdf
uv run stenny refresh "<course>" <YYYY-MM-DD[ (n)]>   # AI step again (slides, glossary edits), no Whisper
uv run stenny courses                 # list configured courses
uv run python bench/run_bench.py       # engine benchmark
uv run stenny live --course "<name>" --file sample.m4a --speed 3   # test live mode without a microphone
app/build.sh                           # build + install the menu bar app (--no-install: only build)
```
