"""Anthropic SDK wrapper with retry/backoff and prompt caching.

Centralized so we can swap the model name, timeout, or retry policy from
one place. Returns the raw SDK response object so the orchestrator can
introspect tool_use blocks and usage stats.
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from typing import Any

import anthropic

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
DEFAULT_MAX_TOKENS = 2000


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @classmethod
    def from_response(cls, resp: Any) -> "LLMUsage":
        u = getattr(resp, "usage", None)
        if u is None:
            return cls()
        return cls(
            input_tokens=int(getattr(u, "input_tokens", 0) or 0),
            output_tokens=int(getattr(u, "output_tokens", 0) or 0),
            cache_read_tokens=int(getattr(u, "cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        )


class LLMClient:
    def __init__(self, *, model: str = DEFAULT_MODEL, api_key: str | None = None, timeout: float = 60.0) -> None:
        self.model = model
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ["ANTHROPIC_API_KEY"], timeout=timeout
        )

    def create(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_retries: int = 3,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "tools": tools,
        }
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return self._client.messages.create(**kwargs)
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                last_exc = e
                status = getattr(e, "status_code", None)
                if status is not None and status < 500 and status != 429:
                    raise
                if attempt == max_retries:
                    break
                delay = (2 ** attempt) * 0.5 + random.uniform(0, 0.25)
                time.sleep(delay)
        raise RuntimeError(f"anthropic_call_failed_after_{max_retries}_retries: {last_exc}")
