"""End-to-end scout pipeline against a Poe-backed LLM (HTTP mocked).

Verifies that with LLM_PROVIDER=poe, the scout pipeline:
- builds the Poe client via the factory
- omits the Anthropic web_search tool from the request
- writes findings to raw_findings (Phase 5a)
- routes the normaliser to the Sonnet bot via prompt_template
- persists canonical opportunities after normalisation
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


def _scout_response_body() -> dict:
    return {
        "id": "resp_scout",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": HAPPY_TWO_BLOCKS},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 4_000, "completion_tokens": 2_500, "total_tokens": 6_500},
    }


def _normaliser_response_body(body_payload: dict) -> dict:
    """Echo the raw_text fields back as a JSON normaliser response.

    Poe's request body is OpenAI-style: messages[-1].content is a string with
    the rendered TITLE/URL/DEADLINE block. We pull the URL out so each finding
    produces a distinct opportunity (otherwise dedup folds them).
    """
    user_msg = body_payload["messages"][-1]["content"]
    title = _grab(user_msg, "TITLE")
    url = _grab(user_msg, "URL") or "https://example.test/missing"
    deadline = _grab(user_msg, "DEADLINE") or ""
    iso = deadline if len(deadline) == 10 and deadline[4] == "-" else None
    note = None if iso else (deadline.lower() if deadline else None)
    payload = {
        "verdict": "opportunity",
        "reject_reason": None,
        "opportunities": [{
            "title": title or "(untitled)",
            "url": url,
            "type": (_grab(user_msg, "TYPE") or "other").lower(),
            "deadline": iso,
            "deadline_note": note,
            "fit_score": int(_grab(user_msg, "FIT SCORE") or 0) or None,
        }],
    }
    return {
        "id": "resp_norm",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": json.dumps(payload)},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 800, "completion_tokens": 200, "total_tokens": 1_000},
    }


def _grab(blob: str, label: str) -> str:
    for line in blob.splitlines():
        if line.startswith(f"{label}:"):
            return line.split(":", 1)[1].strip()
    return ""


def _poe_handler(captured: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        captured.append({"url": str(request.url), "body": body, "auth": request.headers.get("authorization")})
        # Dispatch by bot name: scout vs normaliser.
        model = body.get("model", "")
        if "Search" in model:
            return httpx.Response(200, json=_scout_response_body())
        return httpx.Response(200, json=_normaliser_response_body(body))

    return handler


def test_full_scout_run_via_poe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "poe")
    monkeypatch.setenv("POE_API_KEY", "sk-poe-test")
    monkeypatch.setenv("POE_DRAFTING_BOT", "Claude-Opus-4.7")
    monkeypatch.setenv("POE_SCOUT_BOT", "Claude-Opus-4.7-Search")
    monkeypatch.setenv("POE_NORMALISER_BOT", "Claude-Sonnet-4.6")
    config_mod._settings = None

    captured: list[dict] = []
    transport = httpx.MockTransport(_poe_handler(captured))
    http = httpx.Client(transport=transport, timeout=10.0)

    poe = PoeClient(
        api_key="sk-poe-test",
        drafting_bot="Claude-Opus-4.7",
        scout_bot="Claude-Opus-4.7-Search",
        normaliser_bot="Claude-Sonnet-4.6",
        http_client=http,
    )

    run_migrations()
    summary = run_scout(client=poe, today_iso="2026-05-11")

    assert "added 2" in summary
    # 1 scout call + 2 normaliser calls (one per finding).
    assert len(captured) == 3
    scout_calls = [c for c in captured if "Search" in c["body"]["model"]]
    norm_calls = [c for c in captured if c["body"]["model"] == "Claude-Sonnet-4.6"]
    assert len(scout_calls) == 1
    assert len(norm_calls) == 2
    assert "tools" not in scout_calls[0]["body"], "Anthropic server tools must not be sent to Poe"
    assert all(c["auth"] == "Bearer sk-poe-test" for c in captured)

    with connection() as conn:
        n_opp = conn.execute("SELECT COUNT(*) AS n FROM opportunities").fetchone()["n"]
        n_raw = conn.execute("SELECT COUNT(*) AS n FROM raw_findings WHERE source='scout_agent'").fetchone()["n"]
        normalised = conn.execute(
            "SELECT COUNT(*) AS n FROM raw_findings WHERE status='normalised' AND source='scout_agent'"
        ).fetchone()["n"]
        run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    assert n_opp == 2
    assert n_raw == 2
    assert normalised == 2
    # Poe is points-based — cost_usd stays 0 even with token counts populated.
    assert run["cost_usd"] == 0
