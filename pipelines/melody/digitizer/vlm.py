"""Thin Anthropic client wrapper for the melody read pass (mirrors the chords
digitizer's vlm.py): one cached call, forced tool use, transient backoff."""

from __future__ import annotations

import time

import anthropic

from .config import Config
from .prompt import (REPAIR_TOOL, REPAIR_TOOL_NAME, SYSTEM_PROMPT, TOOL_NAME,
                     TRANSCRIBE_TOOL)

_TRANSIENT_BACKOFF = (1.0, 2.0, 4.0)
_AUTH_RESOLVE_MARKER = "Could not resolve authentication"


class VLMRefusal(Exception):
    """The model declined the request or returned no tool call."""


class MissingCredentials(Exception):
    """No usable Anthropic credentials could be resolved."""


class VLMTruncated(Exception):
    """Output hit max_tokens before the tool call completed."""


def build_request_kwargs(config: Config, user_content: list[dict], *,
                         extra_reminder: str = "",
                         max_tokens: int | None = None,
                         tool: dict = TRANSCRIBE_TOOL,
                         tool_name: str = TOOL_NAME) -> dict:
    """messages.create kwargs — shared by interactive and (future) batch mode.
    The system block stays byte-identical so it caches; per-retry reminders go
    in the user tail, never in the cached prefix."""
    if extra_reminder:
        user_content = user_content + [
            {"type": "text", "text": extra_reminder.strip()}]
    kwargs: dict = {
        "model": config.model,
        "max_tokens": max_tokens or config.max_output_tokens,
        "system": [
            {"type": "text", "text": SYSTEM_PROMPT,
             "cache_control": {"type": "ephemeral"}}
        ],
        "messages": [{"role": "user", "content": user_content}],
        "tools": [tool],
        "tool_choice": {"type": "tool", "name": tool_name},
    }
    if config.supports_temperature:
        kwargs["temperature"] = 0
    return kwargs


def extract_tool_input(response, tool_name: str = TOOL_NAME) -> dict:
    """The forced tool call's input dict."""
    if response.stop_reason == "refusal":
        raise VLMRefusal("model refused the request")
    if response.stop_reason == "max_tokens":
        raise VLMTruncated(
            "output hit max_tokens before completing; raise max_output_tokens")
    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            return dict(block.input)
    raise VLMRefusal("model did not return the expected tool call")


class VLMClient:
    def __init__(self, config: Config):
        self.config = config
        self._client = anthropic.Anthropic()
        self.last_usage = None

    @property
    def api(self) -> anthropic.Anthropic:
        return self._client

    def read(self, user_content: list[dict], *, extra_reminder: str = "",
             max_tokens: int | None = None) -> dict:
        kwargs = build_request_kwargs(self.config, user_content,
                                      extra_reminder=extra_reminder,
                                      max_tokens=max_tokens)
        response = self._call_with_backoff(kwargs)
        self.last_usage = getattr(response, "usage", None)
        if self.config.debug:
            self._log_cache_usage(response)
        return extract_tool_input(response)

    def repair(self, user_content: list[dict], *,
               max_tokens: int | None = None) -> dict:
        kwargs = build_request_kwargs(self.config, user_content,
                                      max_tokens=max_tokens,
                                      tool=REPAIR_TOOL, tool_name=REPAIR_TOOL_NAME)
        response = self._call_with_backoff(kwargs)
        self.last_usage = getattr(response, "usage", None)
        if self.config.debug:
            self._log_cache_usage(response)
        return extract_tool_input(response, tool_name=REPAIR_TOOL_NAME)

    @staticmethod
    def _log_cache_usage(response) -> None:
        u = getattr(response, "usage", None)
        if u is None:
            return
        read = getattr(u, "cache_read_input_tokens", 0) or 0
        write = getattr(u, "cache_creation_input_tokens", 0) or 0
        state = "HIT" if read else ("WRITE" if write else "MISS")
        print(f"  cache {state}: read={read} write={write} "
              f"input={getattr(u, 'input_tokens', 0)} "
              f"output={getattr(u, 'output_tokens', 0)}", flush=True)

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
                raise
            except (anthropic.RateLimitError, anthropic.APIConnectionError,
                    anthropic.InternalServerError) as exc:
                last_exc = exc
            except anthropic.APIStatusError as exc:
                if exc.status_code < 500 and exc.status_code != 429:
                    raise
                last_exc = exc
            if attempt < len(_TRANSIENT_BACKOFF):
                time.sleep(_TRANSIENT_BACKOFF[attempt])
        assert last_exc is not None
        raise last_exc


# Anthropic list price per token (USD), for the per-tune cost log / budget cap.
_PRICING = {
    "claude-opus-4-8": (5e-6, 25e-6),
    "claude-opus-4-7": (5e-6, 25e-6),
    "claude-sonnet-5": (3e-6, 15e-6),
    "claude-fable-5": (5e-6, 25e-6),
}


def usage_cost(model: str, usage) -> float:
    """Approximate USD cost of one call from its usage object (cached input
    billed at the same input rate here — a slight overestimate, safe for a cap)."""
    if usage is None:
        return 0.0
    inp = (getattr(usage, "input_tokens", 0) or 0)
    inp += (getattr(usage, "cache_read_input_tokens", 0) or 0)
    inp += (getattr(usage, "cache_creation_input_tokens", 0) or 0)
    out = getattr(usage, "output_tokens", 0) or 0
    in_rate, out_rate = _PRICING.get(model, (5e-6, 25e-6))
    return inp * in_rate + out * out_rate
