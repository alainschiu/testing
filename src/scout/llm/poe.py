"""Poe API client. Poe exposes an OpenAI-compatible /v1/chat/completions
endpoint that wraps many backend models (Claude, GPT, Perplexity, Gemini,
etc.) by bot name.

Translation cost vs. AnthropicClient:
- System prompt: any list of cache-control blocks is flattened to a single
  string. Poe has no prompt-cache surface, so cache_control is dropped.
- Tools (Anthropic server-side `web_search` and any user-defined tools)
  are silently ignored. The scout agent expects to call Poe with a
  search-capable bot (e.g. Claude-Opus-4.7-Search) that does retrieval
  internally; for drafting, tools are unused anyway.
- `thinking` / `effort` parameters: ignored.
- `stop_reason` is mapped from OpenAI's `finish_reason` so downstream code
  (the pause_turn cycle gate, route handlers) stays unchanged.

Pricing: Poe is points-based and the per-message cost varies by bot. The
chat-completions response doesn't carry a USD-priced usage object — we
capture token counts when present and leave `cost_usd = 0`. Actual point
spend lives on the Poe dashboard.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from scout.llm.client import LLMClient, LLMResponse, LLMUsage
from scout.logging import get_logger

log = get_logger("scout.llm.poe")

POE_BASE_URL = "https://api.poe.com/v1"

_FINISH_REASON_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "refusal",
}


class PoeClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        *,
        drafting_bot: str = "Claude-Opus-4.7",
        scout_bot: str = "Claude-Opus-4.7-Search",
        http_client: httpx.Client | None = None,
        base_url: str = POE_BASE_URL,
    ) -> None:
        if not api_key:
            raise ValueError("POE_API_KEY is required to construct PoeClient")
        self._api_key = api_key
        self._drafting_bot = drafting_bot
        self._scout_bot = scout_bot
        self._base_url = base_url.rstrip("/")
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(300.0))
        self._warned_tools = False

    def _bot_for(self, prompt_template: str | None) -> str:
        # Anything named "scout_agent" goes to the search-capable bot;
        # everything else (drafting, critic) goes to the drafting bot.
        if prompt_template and "scout_agent" in prompt_template:
            return self._scout_bot
        return self._drafting_bot

    @staticmethod
    def _flatten_system(system: str | list[dict[str, Any]]) -> str:
        if isinstance(system, str):
            return system
        parts: list[str] = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n\n".join(p for p in parts if p)

    @staticmethod
    def _flatten_message_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    parts.append(c.get("text", ""))
            return "\n\n".join(p for p in parts if p)
        return str(content)

    def _to_openai_messages(
        self,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        sys_text = self._flatten_system(system)
        if sys_text:
            out.append({"role": "system", "content": sys_text})
        for m in messages:
            role = m.get("role", "user")
            content = self._flatten_message_content(m.get("content", ""))
            out.append({"role": role, "content": content})
        return out

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
        if tools and not self._warned_tools:
            log.warning(
                "poe_ignoring_tools",
                count=len(tools),
                hint="Poe bots handle retrieval/tooling internally; use a search-capable bot for scout.",
            )
            self._warned_tools = True

        bot = self._bot_for(prompt_template)
        payload: dict[str, Any] = {
            "model": bot,
            "messages": self._to_openai_messages(system, messages),
            "max_tokens": max_tokens,
        }

        t0 = time.monotonic()
        r = self._http.post(
            f"{self._base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        r.raise_for_status()
        body = r.json()
        latency_ms = int((time.monotonic() - t0) * 1000)

        choice = body["choices"][0]
        text = choice["message"].get("content") or ""
        finish_reason = choice.get("finish_reason", "stop")
        stop_reason = _FINISH_REASON_MAP.get(finish_reason, finish_reason)

        usage_dict = body.get("usage") or {}
        usage = LLMUsage(
            model=bot,
            input_tokens=int(usage_dict.get("prompt_tokens") or 0),
            output_tokens=int(usage_dict.get("completion_tokens") or 0),
        )
        # Poe is points-based; leave cost_usd at zero.

        log.info(
            "llm_call",
            provider="poe",
            bot=bot,
            prompt_template=prompt_template,
            run_id=run_id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=latency_ms,
            stop_reason=stop_reason,
            finish_reason=finish_reason,
        )

        return LLMResponse(
            text=text,
            raw_content=[{"type": "text", "text": text}],
            usage=usage,
            stop_reason=stop_reason,
            raw=body,
        )
