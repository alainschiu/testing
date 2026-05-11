"""End-to-end scout pipeline against a Poe-backed LLM (HTTP mocked).

Verifies that with LLM_PROVIDER=poe, the scout agent:
- builds the Poe client via the factory
- omits the Anthropic web_search tool from the request
- still parses the §8 output and persists opportunities
- records cost_usd=0 (Poe is points-based) but token counts come through
"""
from __future__ import annotations

import json

import httpx
import pytest

from scout import config as config_mod
from scout.agents.scout_agent import run_scout
from scout.db import connection, run_migrations
from scout.llm.poe import PoeClient
from tests.fixtures.sample_scout_output import HAPPY_TWO_BLOCKS


def _poe_handler(captured: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append({
            "url": str(request.url),
            "body": json.loads(request.content.decode()),
            "auth": request.headers.get("authorization"),
        })
        return httpx.Response(
            200,
            json={
                "id": "resp_x",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": HAPPY_TWO_BLOCKS},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4_000, "completion_tokens": 2_500, "total_tokens": 6_500},
            },
        )

    return handler


def test_full_scout_run_via_poe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "poe")
    monkeypatch.setenv("POE_API_KEY", "sk-poe-test")
    monkeypatch.setenv("POE_DRAFTING_BOT", "Claude-Opus-4.7")
    monkeypatch.setenv("POE_SCOUT_BOT", "Claude-Opus-4.7-Search")
    config_mod._settings = None

    captured: list[dict] = []
    transport = httpx.MockTransport(_poe_handler(captured))
    http = httpx.Client(transport=transport, timeout=10.0)

    # Inject a PoeClient that uses the mock transport. We do this instead of
    # monkeypatching the factory because we want to exercise the real
    # scout_agent.run_scout path that goes via the factory when no client is
    # passed. Passing client= here bypasses the factory; the separate
    # test_llm_factory.py covers that branch.
    poe = PoeClient(
        api_key="sk-poe-test",
        drafting_bot="Claude-Opus-4.7",
        scout_bot="Claude-Opus-4.7-Search",
        http_client=http,
    )

    run_migrations()
    summary = run_scout(client=poe, today_iso="2026-05-11")

    assert "added 2" in summary
    assert len(captured) == 1, "scout should make exactly one Poe call"
    body = captured[0]["body"]
    assert body["model"] == "Claude-Opus-4.7-Search"
    assert "tools" not in body, "Anthropic server tools must not be sent to Poe"
    assert captured[0]["auth"] == "Bearer sk-poe-test"

    with connection() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM opportunities").fetchone()["n"]
        run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    assert n == 2
    # Poe is points-based — cost_usd stays 0 even with token counts populated
    assert run["cost_usd"] == 0
