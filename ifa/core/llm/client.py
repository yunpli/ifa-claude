"""OpenAI-compatible chat client with primary→fallback failover.

Both the primary (jmrai gateway, model `gpt-5.4`) and the fallback
(`177.54.159.23:8081`, model `gpt-5.5`) speak the OpenAI Chat Completions
protocol, so we use the official `openai` SDK with a custom `base_url`.

Failover triggers: connection errors, timeouts, 5xx, rate-limit (429).
A successful primary response is preferred whenever possible.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI, RateLimitError

from ifa.config import Settings, get_settings

_TRANSIENT_EXC = (APIConnectionError, APITimeoutError, RateLimitError)


@dataclass
class LLMResponse:
    """Result of a single chat completion."""

    content: str
    model: str            # actual model id returned by server
    endpoint: str         # "primary" | "fallback"
    base_url: str
    latency_seconds: float
    prompt_tokens: int | None
    completion_tokens: int | None
    raw: dict[str, Any]   # full server response (for audit / report_model_outputs)

    def parse_json(self) -> Any:
        """Parse content as JSON, tolerating ```json fences."""
        s = self.content.strip()
        if s.startswith("```"):
            s = s.split("```", 2)[1]
            if s.startswith(("json", "JSON")):
                s = s[4:]
            s = s.strip()
        return json.loads(s)


class LLMClient:
    """Thin wrapper that tries the primary endpoint, then the fallback."""

    def __init__(self, settings: Settings | None = None, *, request_timeout: float = 120.0) -> None:
        self.settings = settings or get_settings()
        self._timeout = request_timeout
        self._primary = OpenAI(
            base_url=self.settings.llm_primary_base_url,
            api_key=self.settings.llm_primary_api_key.get_secret_value(),
            timeout=request_timeout,
            max_retries=0,  # we handle retries / failover ourselves
        )
        self._fallback = OpenAI(
            base_url=self.settings.llm_fallback_base_url,
            api_key=self.settings.llm_fallback_api_key.get_secret_value(),
            timeout=request_timeout,
            max_retries=0,
        )

    # ---- public API ----------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        force_endpoint: str | None = None,  # "primary" | "fallback" — for diagnostics
    ) -> LLMResponse:
        """Run a chat completion, failing over to the fallback endpoint on transient errors."""
        order: list[tuple[str, OpenAI, str, str]]
        if force_endpoint == "fallback":
            order = [("fallback", self._fallback, self.settings.llm_fallback_model, self.settings.llm_fallback_base_url)]
        elif force_endpoint == "primary":
            order = [("primary", self._primary, self.settings.llm_primary_model, self.settings.llm_primary_base_url)]
        else:
            order = [
                ("primary", self._primary, self.settings.llm_primary_model, self.settings.llm_primary_base_url),
                ("fallback", self._fallback, self.settings.llm_fallback_model, self.settings.llm_fallback_base_url),
            ]

        last_error: Exception | None = None
        for label, client, model, base_url in order:
            try:
                return self._call(client, model, label, base_url, messages,
                                  max_tokens=max_tokens, temperature=temperature,
                                  response_format=response_format)
            except _TRANSIENT_EXC as exc:
                last_error = exc
                continue
            except APIStatusError as exc:
                if 500 <= exc.status_code < 600:
                    last_error = exc
                    continue
                raise
            except APIError as exc:
                last_error = exc
                continue

        assert last_error is not None
        raise last_error

    # ---- internals -----------------------------------------------------------

    def _call(
        self,
        client: OpenAI,
        model: str,
        label: str,
        base_url: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None,
        temperature: float | None,
        response_format: dict[str, Any] | None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature
        if response_format is not None:
            kwargs["response_format"] = response_format

        t0 = time.monotonic()
        resp = client.chat.completions.create(**kwargs)
        latency = time.monotonic() - t0

        content = resp.choices[0].message.content or ""
        usage = resp.usage
        return LLMResponse(
            content=content,
            model=resp.model or model,
            endpoint=label,
            base_url=base_url,
            latency_seconds=latency,
            prompt_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            completion_tokens=getattr(usage, "completion_tokens", None) if usage else None,
            raw=resp.model_dump(),
        )
