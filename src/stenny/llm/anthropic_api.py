"""Anthropic API backend (pay per token), with the key from the system keychain (keychain.py) or $ANTHROPIC_API_KEY."""

from . import LLMError, Usage

# USD per million tokens (prompts up to 100K tokens), list prices as of 2026-10
PRICES = {"claude-haiku-5-5": (0.10, 0.50)}


class AnthropicBackend:
    def __init__(self, model: str = "claude-haiku-5-5"):
        import anthropic

        from ..keychain import api_key
        key = api_key()
        if not key:
            raise LLMError("No Anthropic API key: add one in Settings ▸ Transcripts & AI, or run `stenny api-key`.")
        self.client = anthropic.Anthropic(api_key=key)
        self.model = model
        self.name = f"Anthropic API ({model})"

    def complete(self, system: str, prompt: str) -> tuple[str, Usage]:
        import anthropic

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                thinking={"type": "disabled"},  # mechanical task; thinking tokens would be billed as output
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError:
            raise LLMError("Anthropic didn't accept the API key: check it in Settings ▸ Transcripts & AI.") from None
        except anthropic.APIError as e:
            raise LLMError(f"Anthropic API error: {e}") from None
        if response.stop_reason == "refusal":
            raise LLMError("The model declined the request.")
        text = "".join(b.text for b in response.content if b.type == "text")
        price_in, price_out = PRICES.get(self.model, (0.0, 0.0))
        u = response.usage
        cost = (u.input_tokens * price_in + u.output_tokens * price_out) / 1e6
        return text, Usage(u.input_tokens, u.output_tokens, cost, billed=True)
