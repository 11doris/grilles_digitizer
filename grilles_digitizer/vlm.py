"""Thin Anthropic client wrapper: one cached VLM call per crop, with backoff."""

from __future__ import annotations

import time

import anthropic

from .config import Config
from .prompt import SYSTEM_PROMPT

# Retry transient failures (rate limits, overload, 5xx, connection drops) with a
# short exponential backoff. These are distinct from per-unit validation retries.
_TRANSIENT_BACKOFF = (1.0, 2.0, 4.0)


class VLMRefusal(Exception):
    """The model declined the request (stop_reason == 'refusal')."""


class VLMClient:
    def __init__(self, config: Config):
        self.config = config
        # The SDK resolves ANTHROPIC_API_KEY (or an ant-login profile) from the env.
        self._client = anthropic.Anthropic()

    def transcribe(self, user_content: list[dict], *, extra_reminder: str = "") -> str:
        """Send the cached system prompt + this crop; return the model's raw text."""
        system = [
            {
                "type": "text",
                "text": SYSTEM_PROMPT + extra_reminder,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        kwargs: dict = {
            "model": self.config.model,
            "max_tokens": self.config.max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_content}],
        }
        if self.config.supports_temperature:
            kwargs["temperature"] = 0  # most deterministic where the model allows it

        response = self._call_with_backoff(kwargs)

        if response.stop_reason == "refusal":
            raise VLMRefusal("model refused the request")

        return "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()

    def _call_with_backoff(self, kwargs: dict):
        last_exc: Exception | None = None
        for attempt in range(len(_TRANSIENT_BACKOFF) + 1):
            try:
                return self._client.messages.create(**kwargs)
            except (
                anthropic.RateLimitError,
                anthropic.APIConnectionError,
                anthropic.InternalServerError,
            ) as exc:
                last_exc = exc
            except anthropic.APIStatusError as exc:
                if exc.status_code < 500 and exc.status_code != 429:
                    raise  # non-transient client error — don't retry
                last_exc = exc
            if attempt < len(_TRANSIENT_BACKOFF):
                time.sleep(_TRANSIENT_BACKOFF[attempt])
        assert last_exc is not None
        raise last_exc
