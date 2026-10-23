"""Optional LLM post-processing backends. Only transcript text is ever sent; audio never leaves the Mac."""

from dataclasses import dataclass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0       # reported (Claude Code) or computed from list prices (API)
    billed: bool = True         # False when it runs on a subscription (no per-token charge)

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens,
                     self.cost_usd + other.cost_usd, self.billed and other.billed)


class LLMError(Exception):
    pass


def get_backend(cfg):
    """Backend for cfg.llm_backend, or None when post-processing is disabled."""
    if cfg.llm_backend == "none":
        return None
    if cfg.llm_backend == "claude-code":
        from .claude_code import ClaudeCodeBackend
        return ClaudeCodeBackend(cfg.llm_model)
    if cfg.llm_backend == "api":
        from .anthropic_api import AnthropicBackend
        return AnthropicBackend(cfg.api_model)
    raise LLMError(f"Unknown llm backend '{cfg.llm_backend}' (use claude-code, api or none)")
