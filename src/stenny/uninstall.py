"""`stenny uninstall`: what Stenny's engine set up on this computer, undone. scripts/uninstall.sh and
scripts/uninstall.ps1 run it first, then remove the app and the engine itself and put the places it lists into the
Trash (Recycle Bin on Windows), so they can still be brought back.

Removed here: the background job for final transcripts (launchd agent / Task Scheduler task), the connector in Claude
Code, the saved secrets (Anthropic API key, calendar links) and what can be made again: logs, cache, the Whisper
model. Listed for the Trash: the settings (config place) and the data place (the recordings' audio, the queue, the
calendars). Never touched: the Stenny folder, with the transcripts, summaries and glossaries, which are the student's.
Claude Desktop's extension can only be removed in Claude Desktop, so it is reported, not removed."""

import shutil
import sys
from pathlib import Path

from . import calendars, claude_setup, jobs, keychain, paths
from . import config as config_mod
from .llm.claude_code import find_claude


def whisper_model_dirs(model: str) -> list[Path]:
    """Where the configured Whisper model is. Windows: Stenny's own Models folder. Mac: mlx-whisper's copy in the
    Hugging Face cache, that model's folder only (the cache may hold other programs' models). A model that is a local
    folder is the user's own and stays."""
    models = paths.models_dir()
    if models is not None:
        return [models]
    if "/" not in model or Path(model).expanduser().exists():
        return []
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
    except ImportError:
        return []
    return [Path(HF_HUB_CACHE) / ("models--" + model.replace("/", "--"))]


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def run(act: bool) -> dict:
    """With `act` False only says what it would do. Returns what was (or would be) removed, the places for the
    Trash, the Stenny folder that stays, whether Claude Desktop still has the extension, and any problems."""
    try:
        cfg = config_mod.load()
    except config_mod.ConfigError:
        cfg = None
    folder = cfg.folder if cfg else None
    removed: list[str] = []
    problems: list[str] = []

    def keeps_folder(place: Path) -> bool:
        """A place that holds the Stenny folder (a data_dir set around it, say) is never deleted or trashed."""
        if folder is not None and _inside(folder, place):
            problems.append(f"{place} holds the Stenny folder ({folder}), so it stays.")
            return True
        return False

    # the background job for final transcripts
    if jobs.AGENT_PATH.exists() or sys.platform == "win32":
        if not act:
            removed.append("background job")
        else:
            try:
                if jobs.uninstall_agent():
                    removed.append("background job")
            except OSError as e:
                problems.append(f"The background job couldn't be removed: {e}")

    # the connector in Claude Code (Claude Desktop removes its own extension)
    if find_claude() and claude_setup._claude_code_has_it():
        try:
            if act:
                claude_setup.remove_from_claude_code(cfg)
            removed.append("Claude Code connector")
        except claude_setup.SetupError as e:
            problems.append(str(e))
    desktop_extension = claude_setup.installed_extension() is not None

    # saved secrets: the API key and every calendar link
    accounts = [keychain.ACCOUNT, *(calendars._account(link["id"]) for link in calendars._load())]
    for account in accounts:
        if not act:
            continue
        try:
            keychain.delete_secret(account)
        except keychain.KeychainError as e:
            problems.append(f"A saved secret couldn't be removed: {e}")
    removed.append("saved API key and calendar links")

    # what can be made again
    model = cfg.model if cfg else config_mod.Config.model
    for place in [paths.LOGS_DIR, paths.CACHE_DIR, *whisper_model_dirs(model)]:
        if place.exists() and not keeps_folder(place):
            if act:
                shutil.rmtree(place, ignore_errors=True)
            removed.append(str(place))

    # for the Trash: settings and data (recordings' audio)
    trash = [str(p) for p in dict.fromkeys([paths.CONFIG_DIR, paths.DATA_DIR]) if p.exists() and not keeps_folder(p)]

    return {"removed": removed, "trash": trash, "folder": str(folder) if folder else None,
            "claude_desktop_extension": desktop_extension, "problems": problems}
