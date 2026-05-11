"""LLMClient interface + Anthropic implementation + Fake for tests.

All LLM calls go through this so tests can mock without hitting the network and
so we can swap providers later.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from scout.config import get_settings
from scout.logging import get_logger

log = get_logger("scout.llm")

# Per skill: Opus 4.7 input $5/1M, output $25/1M. Cache reads ~0.1x, cache writes ~1.25x.
# Conservative: count cache_creation as 1.25x input, cache_read as 0.1x input.
_PRICES_PER_1M = {
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


@dataclass
class LLMUsage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class LLMResponse:
    text: str
    raw_content: list[dict[str, Any]] = field(default_factory=list)
    usage: LLMUsage | None = None
    stop_reason: str | None = None
    raw: Any = None


def estimate_cost(model: str, usage: LLMUsage) -> float:
    in_price, out_price = _PRICES_PER_1M.get(model, (5.00, 25.00))
    base = (usage.input_tokens / 1_000_000) * in_price
    write = (usage.cache_creation_input_tokens / 1_000_000) * in_price * 1.25
    read = (usage.cache_read_input_tokens / 1_000_000) * in_price * 0.1
    out = (usage.output_tokens / 1_000_000) * out_price
    return round(base + write + read + out, 6)


class LLMClient(ABC):
    @abstractmethod
    def create_message(
        self,
        *,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int = 16_000,
        tools: list[dict[str, Any]] | None = None,
        thinking: dict[str, Any] | None = None,
        effort: str | None = None,
        prompt_template: str | None = None,
        run_id: int | None = None,
    ) -> LLMResponse: ...


class AnthropicClient(LLMClient):
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        import anthropic

        s = get_settings()
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key or s.anthropic_api_key)
        self._model = model or s.scout_model

    def create_message(
        self,
        *,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int = 16_000,
        tools: list[dict[str, Any]] | None = None,
        thinking: dict[str, Any] | None = None,
        effort: str | None = None,
        prompt_template: str | None = None,
        run_id: int | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "system": system,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
        if thinking is not None:
            kwargs["thinking"] = thinking
        if effort is not None:
            kwargs["output_config"] = {"effort": effort}

        t0 = time.monotonic()
        # Stream when max_tokens is large enough to risk the SDK's wall-clock guard.
        # Skill: "Large max_tokens without streaming raises ValueError" above ~16K.
        if max_tokens > 16_000:
            with self._client.messages.stream(**kwargs) as stream:
                resp = stream.get_final_message()
        else:
            resp = self._client.messages.create(**kwargs)
        latency_ms = int((time.monotonic() - t0) * 1000)

        raw_content = [b.model_dump() if hasattr(b, "model_dump") else dict(b) for b in resp.content]
        text_parts = [b["text"] for b in raw_content if b.get("type") == "text"]

        u = getattr(resp, "usage", None)
        usage = LLMUsage(
            model=self._model,
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        )
        usage.cost_usd = estimate_cost(self._model, usage)

        log.info(
            "llm_call",
            prompt_template=prompt_template,
            run_id=run_id,
            model=self._model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read=usage.cache_read_input_tokens,
            cache_create=usage.cache_creation_input_tokens,
            cost_usd=usage.cost_usd,
            latency_ms=latency_ms,
            stop_reason=resp.stop_reason,
        )

        return LLMResponse(
            text="\n".join(text_parts),
            raw_content=raw_content,
            usage=usage,
            stop_reason=resp.stop_reason,
            raw=resp,
        )


class FakeLLMClient(LLMClient):
    """Scripted responses for tests. Key by prompt_template; falls back to default.

    Each response value can be:
    - a single LLMResponse (returned for every call with that template)
    - a list of LLMResponse (popped in order; raises IndexError when exhausted)
    - a callable (kwargs) -> LLMResponse (computes a response from the call,
      e.g. echoing input — useful for the normaliser whose output mirrors
      whichever raw_finding it was given)
    """

    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        default: LLMResponse | None = None,
    ) -> None:
        self.responses = responses or {}
        self.default = default or LLMResponse(text="", usage=LLMUsage(model="fake"))
        self.calls: list[dict[str, Any]] = []

    def create_message(
        self,
        *,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int = 16_000,
        tools: list[dict[str, Any]] | None = None,
        thinking: dict[str, Any] | None = None,
        effort: str | None = None,
        prompt_template: str | None = None,
        run_id: int | None = None,
    ) -> LLMResponse:
        kw = {
            "prompt_template": prompt_template,
            "run_id": run_id,
            "system_preview": (system if isinstance(system, str) else "[list]")[:200],
            "system": system,
            "messages": messages,
            "max_tokens": max_tokens,
            "tools": tools,
            "effort": effort,
        }
        self.calls.append(kw)
        # Match the literal template, the basename without extension, and any
        # template variant containing the key (e.g. "normaliser_escalated"
        # matches "normaliser"). First match wins.
        candidates = [prompt_template or ""]
        if prompt_template:
            candidates.append(prompt_template.split("/")[-1].split(".", 1)[0])
        entry = None
        for key, value in self.responses.items():
            if any(key == c or (key in c if c else False) for c in candidates):
                entry = value
                break
        if entry is None:
            return self.default
        if callable(entry):
            return entry(kw)
        if isinstance(entry, list):
            return entry.pop(0)
        return entry
