"""Shared fixtures: temp DB, fake LLM, env isolation."""
from __future__ import annotations

from pathlib import Path

import pytest

from scout import config as config_mod
from scout.llm.client import FakeLLMClient, LLMResponse, LLMUsage


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets its own data dir + DB. Prevents tests touching real ~/data."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("SCOUT_DATA_DIR", str(data))
    monkeypatch.setenv("SCOUT_DB_PATH", str(data / "scout.db"))
    monkeypatch.setenv("SCOUT_LOG_JSON_PATH", str(data / "scout.log"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-do-not-use")
    monkeypatch.setenv("SCOUT_MAX_WEB_SEARCHES", "5")
    monkeypatch.setenv("SCOUT_MAX_OUTPUT_TOKENS", "10000")
    # Force settings re-creation so the per-test paths take effect
    config_mod._settings = None
    yield
    config_mod._settings = None


@pytest.fixture
def fake_llm() -> FakeLLMClient:
    return FakeLLMClient(default=LLMResponse(text="", usage=LLMUsage(model="fake")))


@pytest.fixture(autouse=True)
def _disable_politeness(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the real robots.txt fetch and the 10s/domain rate limit in tests.
    fetcher.py binds `allowed_by_robots`/`wait_for_slot` at import time, so we
    have to patch the names on the fetcher module — patching politeness only
    wouldn't take effect."""
    from scout.sources import fetcher, politeness

    monkeypatch.setattr(politeness, "allowed_by_robots", lambda _url: True)
    monkeypatch.setattr(politeness, "wait_for_slot", lambda _url: None)
    monkeypatch.setattr(fetcher, "allowed_by_robots", lambda _url: True)
    monkeypatch.setattr(fetcher, "wait_for_slot", lambda _url: None)
    politeness.reset_for_tests()
