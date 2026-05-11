"""Factory for the active LLM provider. Reads `LLM_PROVIDER` from settings.

Centralised so scout and drafting both get the same provider without
hard-coding `AnthropicClient` at call sites.
"""
from __future__ import annotations

from scout.config import get_settings
from scout.llm.client import AnthropicClient, LLMClient


def build_default_client() -> LLMClient:
    """Construct the active provider's client. Raises if its key is missing."""
    s = get_settings()
    provider = (s.llm_provider or "anthropic").strip().lower()
    if provider == "poe":
        if not s.poe_api_key:
            raise RuntimeError("LLM_PROVIDER=poe but POE_API_KEY is not set")
        from scout.llm.poe import PoeClient

        return PoeClient(
            api_key=s.poe_api_key,
            drafting_bot=s.poe_drafting_bot,
            scout_bot=s.poe_scout_bot,
        )
    if provider == "anthropic":
        if not s.anthropic_api_key:
            raise RuntimeError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
        return AnthropicClient(api_key=s.anthropic_api_key, model=s.scout_model)
    raise RuntimeError(
        f"unknown LLM_PROVIDER={s.llm_provider!r} (expected 'anthropic' or 'poe')"
    )
