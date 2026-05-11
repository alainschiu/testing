"""Factory for the active LLM provider. Reads `LLM_PROVIDER` from settings.

Centralised so scout and drafting both get the same provider without
hard-coding `AnthropicClient` at call sites.
"""
from __future__ import annotations

from scout.config import get_settings
from scout.llm.client import AnthropicClient, LLMClient


def build_default_client() -> LLMClient:
    """Construct the active provider's client (scout/drafting model). Raises if key missing."""
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
            normaliser_bot=s.poe_normaliser_bot,
        )
    if provider == "anthropic":
        if not s.anthropic_api_key:
            raise RuntimeError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
        return AnthropicClient(api_key=s.anthropic_api_key, model=s.scout_model)
    raise RuntimeError(
        f"unknown LLM_PROVIDER={s.llm_provider!r} (expected 'anthropic' or 'poe')"
    )


def build_normaliser_client() -> LLMClient:
    """Construct a client tuned for normaliser/extractor calls.

    Anthropic: returns a fresh AnthropicClient pinned to NORMALISER_MODEL
    (Sonnet-class by default, ~5x cheaper than Opus).
    Poe: returns the same PoeClient as build_default_client; routing to the
    Sonnet bot is handled inside PoeClient via prompt_template.
    """
    s = get_settings()
    provider = (s.llm_provider or "anthropic").strip().lower()
    if provider == "poe":
        return build_default_client()
    if provider == "anthropic":
        if not s.anthropic_api_key:
            raise RuntimeError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
        return AnthropicClient(api_key=s.anthropic_api_key, model=s.normaliser_model)
    raise RuntimeError(
        f"unknown LLM_PROVIDER={s.llm_provider!r} (expected 'anthropic' or 'poe')"
    )
