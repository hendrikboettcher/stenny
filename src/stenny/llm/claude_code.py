"""Run prompts through the local Claude Code CLI (`claude -p`), using the user's subscription."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import LLMError, Usage
from .anthropic_api import PRICES


def find_claude() -> str | None:
    """The `claude` executable. Apps (Stenny) and launchd start us with a minimal PATH, and
    Claude Code is often installed via npm under nvm, so also look in the usual places."""
    if found := shutil.which("claude"):
        return found
    home = Path.home()
    candidates = [home / ".claude/local/claude", home / ".local/bin/claude",
                  Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude"),
                  *sorted(home.glob(".nvm/versions/node/*/bin/claude"), reverse=True)]
    return next((str(c) for c in candidates if c.exists()), None)


class ClaudeCodeBackend:
    def __init__(self, model: str = "claude-haiku-5-5"):
        self.model = model
        self.name = f"Claude Code ({model})"
        self.exe = find_claude()
        if not self.exe:
            raise LLMError("`claude` CLI not found. Install Claude Code or set llm.backend = \"none\".")

    def complete(self, system: str, prompt: str, timeout: int = 600) -> tuple[str, Usage]:
        cmd = [self.exe, "-p", "--model", self.model, "--effort", "low", "--output-format", "json",
               "--tools", "", "--strict-mcp-config", "--no-session-persistence",
               "--system-prompt", system]
        # Run from an empty temp dir so no project CLAUDE.md or settings leak into the prompt, and without
        # the session variables of a parent Claude Code (e.g. the desktop app), so the user's own login is used.
        # Without an API key either: with ANTHROPIC_API_KEY set, `claude` would bill the API instead of the
        # subscription. The "api" backend is the way to use API credits.
        env = {k: v for k, v in os.environ.items()
               if not (k.startswith("CLAUDE") or k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"))}
        # No thinking: the task is mechanical, and thinking tokens were 95% of the output (and the time).
        env["MAX_THINKING_TOKENS"] = "0"
        # npm-installed claude is a node script: make sure its node is on the PATH too
        env["PATH"] = f"{Path(self.exe).parent}:{env.get('PATH', '/usr/bin:/bin')}"
        with tempfile.TemporaryDirectory() as cwd:
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
        try:
            data = json.loads(p.stdout)
        except json.JSONDecodeError:
            raise LLMError(f"claude -p failed: {(p.stderr or p.stdout)[-500:]}") from None
        if data.get("is_error") or p.returncode != 0:
            hint = " Run `claude` in a terminal and log in again." if data.get("api_error_status") == 401 else ""
            raise LLMError(f"claude -p: {str(data.get('result')).rstrip('.')}.{hint}")
        u = data.get("usage", {})
        usage = Usage(
            input_tokens=u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
            + u.get("cache_creation_input_tokens", 0),
            output_tokens=u.get("output_tokens", 0),
            billed=False,
        )
        # what the same tokens would cost on the API (Claude Code's own figure includes its overhead pricing)
        price_in, price_out = PRICES.get(self.model, (0.0, 0.0))
        usage.cost_usd = (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1e6
        return data.get("result", ""), usage
