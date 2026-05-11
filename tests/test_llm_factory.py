"""build_default_client picks the right concrete client by env."""
from __future__ import annotations

import pytest

from scout import config as config_mod
from scout.llm.client import AnthropicClient
from scout.llm.factory import build_default_client
from scout.llm.poe import PoeClient


def test_anthropic_provider_returns_anthropic_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    config_mod._settings = None
    client = build_default_client()
    assert isinstance(client, AnthropicClient)


def test_poe_provider_returns_poe_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "poe")
    monkeypatch.setenv("POE_API_KEY", "sk-poe-test")
    monkeypatch.setenv("POE_DRAFTING_BOT", "Claude-Opus-4.7")
    monkeypatch.setenv("POE_SCOUT_BOT", "Claude-Opus-4.7-Search")
    config_mod._settings = None
    client = build_default_client()
    assert isinstance(client, PoeClient)


def test_poe_provider_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "poe")
    monkeypatch.setenv("POE_API_KEY", "")
    config_mod._settings = None
    with pytest.raises(RuntimeError, match="POE_API_KEY"):
        build_default_client()


def test_anthropic_provider_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    config_mod._settings = None
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_default_client()


def test_unknown_provider_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    config_mod._settings = None
    with pytest.raises(RuntimeError, match="unknown LLM_PROVIDER"):
        build_default_client()
