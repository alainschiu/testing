"""End-to-end application + drafting routes.

Exercises the same FakeLLMClient pattern but plugs it into the route
handler by monkeypatching AnthropicClient. Verifies the full path:
opportunity → start-draft → generate draft → mark submitted.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from scout.agents.artist_profile import refresh_artist_profile
from scout.db import connection, run_migrations
from scout.llm.client import FakeLLMClient, LLMResponse, LLMUsage
from scout.models import utc_now_iso
from scout.web.app import app


@pytest.fixture
async def client() -> AsyncClient:
    run_migrations()
    refresh_artist_profile()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _seed_opp() -> int:
    now = utc_now_iso()
    with connection() as conn:
        cur = conn.execute(
            "INSERT INTO opportunities (title, type, url, url_hash, deadline, fit_score,"
            " primary_angle, why_fits, status, discovered_at, updated_at)"
            " VALUES ('DAAD Berliner Künstlerprogramm', 'residency',"
            " 'https://www.daad.de/x', 'daad-x', '2026-08-31', 5, 'A',"
            " 'Funds practice-led research with no production deliverable.',"
            " 'lead', ?, ?)",
            (now, now),
        )
        return int(cur.lastrowid)


def _patch_llm(monkeypatch: pytest.MonkeyPatch, response_text: str) -> FakeLLMClient:
    fake = FakeLLMClient(default=LLMResponse(
        text=response_text,
        usage=LLMUsage(
            model="claude-opus-4-7", input_tokens=2_000, output_tokens=500, cost_usd=0.022,
        ),
        stop_reason="end_turn",
    ))
    monkeypatch.setattr(
        "scout.agents.drafting_agent.AnthropicClient",
        lambda *a, **kw: fake,
    )
    return fake


async def test_full_drafting_loop(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    opp_id = _seed_opp()
    fake = _patch_llm(
        monkeypatch,
        "Alain Chiu's practice centres on listening. Drawing Room Project responds to"
        " DAAD Berliner Künstlerprogramm's contemplative line."
    )

    # 1. Start draft → redirected to application page
    r = await client.post(f"/opportunities/{opp_id}/start-draft", follow_redirects=False)
    assert r.status_code == 303
    app_url = r.headers["location"]
    app_id = int(app_url.rsplit("/", 1)[-1])

    # 2. Application page renders with empty tabs
    r = await client.get(app_url)
    assert r.status_code == 200
    assert "Artist statement" in r.text
    assert "No draft yet" in r.text

    # 3. Generate an artist statement draft
    r = await client.post(
        f"/applications/{app_id}/drafts",
        data={"kind": "artist_statement", "variant": "500w"},
    )
    assert r.status_code == 200
    assert "Drawing Room Project" in r.text
    assert len(fake.calls) == 1

    # 4. Mark submitted → opportunity flips to 'applied'
    r = await client.post(f"/applications/{app_id}/submit", follow_redirects=False)
    assert r.status_code == 303
    with connection() as conn:
        opp = conn.execute(
            "SELECT status FROM opportunities WHERE id=?", (opp_id,)
        ).fetchone()
        app_row = conn.execute(
            "SELECT status, submitted_at FROM applications WHERE id=?", (app_id,)
        ).fetchone()
    assert opp["status"] == "applied"
    assert app_row["status"] == "submitted"
    assert app_row["submitted_at"] is not None


async def test_unknown_kind_returns_400(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_llm(monkeypatch, "x")
    opp_id = _seed_opp()
    r = await client.post(f"/opportunities/{opp_id}/start-draft", follow_redirects=False)
    app_id = int(r.headers["location"].rsplit("/", 1)[-1])
    r = await client.post(
        f"/applications/{app_id}/drafts",
        data={"kind": "not_a_kind"},
    )
    assert r.status_code == 400


async def test_application_page_404(client: AsyncClient) -> None:
    r = await client.get("/applications/99999")
    assert r.status_code == 404
