"""Thin Anthropic client wrapper: one cached VLM call per crop, with backoff."""

from __future__ import annotations

import json
import time

import anthropic

from .config import Config
from .prompt import SYSTEM_PROMPT, TOOL_NAME, TUNE_TOOL

# Retry transient failures (rate limits, overload, 5xx, connection drops) with a
# short exponential backoff. These are distinct from per-unit validation retries.
_TRANSIENT_BACKOFF = (1.0, 2.0, 4.0)


class VLMRefusal(Exception):
    """The model declined the request (stop_reason == 'refusal')."""


class MissingCredentials(Exception):
    """No usable Anthropic credentials could be resolved (no key/token/profile)."""


class VLMTruncated(Exception):
    """Output hit max_tokens before the tool call completed."""


# The SDK raises a bare TypeError carrying this text when it can resolve no
# credentials at call time. Match on it to fail fast with a clear message.
_AUTH_RESOLVE_MARKER = "Could not resolve authentication"


class VLMClient:
    def __init__(self, config: Config):
        self.config = config
        # The SDK resolves ANTHROPIC_API_KEY (or an ant-login profile) from the env.
        self._client = anthropic.Anthropic()

    def transcribe(self, user_content: list[dict], *, extra_reminder: str = "",
                   max_tokens: int | None = None) -> str:
        """Send the cached system prompt + this crop; return the model's raw text.

        `max_tokens` overrides the configured cap for this call (the runner
        raises it when a previous attempt was truncated).
        """
        # The system block must stay byte-identical across every call so it caches
        # (spec §18.3). The per-retry reminder therefore goes in the user message tail,
        # never in the cached prefix.
        system = [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        if extra_reminder:
            user_content = user_content + [{"type": "text", "text": extra_reminder.strip()}]
        kwargs: dict = {
            "model": self.config.model,
            "max_tokens": max_tokens or self.config.max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_content}],
            # Force the model to answer by calling the tool — guarantees structured
            # JSON with no prose preamble, on every model (current Claude models reject
            # assistant-message prefill, and chattier ones otherwise add commentary).
            "tools": [TUNE_TOOL],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
        }
        if self.config.supports_temperature:
            kwargs["temperature"] = 0  # most deterministic where the model allows it

        response = self._call_with_backoff(kwargs)

        if self.config.debug:
            self._log_cache_usage(response)

        if response.stop_reason == "refusal":
            raise VLMRefusal("model refused the request")
        if response.stop_reason == "max_tokens":
            raise VLMTruncated(
                "output hit max_tokens before completing; raise --max-output-tokens"
            )

        for block in response.content:
            if block.type == "tool_use" and block.name == TOOL_NAME:
                return json.dumps(block.input)
        raise VLMRefusal("model did not return the expected tool call")

    @staticmethod
    def _log_cache_usage(response) -> None:
        """Print cache hit/miss stats so caching can be confirmed (spec §18.3)."""
        u = getattr(response, "usage", None)
        if u is None:
            return
        read = getattr(u, "cache_read_input_tokens", 0) or 0
        write = getattr(u, "cache_creation_input_tokens", 0) or 0
        state = "HIT" if read else ("WRITE" if write else "MISS")
        print(
            f"  cache {state}: read={read} write={write} "
            f"input={getattr(u, 'input_tokens', 0)} output={getattr(u, 'output_tokens', 0)}",
            flush=True,
        )

    def _call_with_backoff(self, kwargs: dict):
        last_exc: Exception | None = None
        for attempt in range(len(_TRANSIENT_BACKOFF) + 1):
            try:
                return self._client.messages.create(**kwargs)
            except TypeError as exc:
                if _AUTH_RESOLVE_MARKER in str(exc):
                    raise MissingCredentials(
                        "No Anthropic credentials found. Set ANTHROPIC_API_KEY "
                        "(or ANTHROPIC_AUTH_TOKEN, or run `ant auth login`)."
                    ) from exc
                raise  # a genuine bug, not an auth issue — surface it
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
