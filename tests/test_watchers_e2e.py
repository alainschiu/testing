"""End-to-end: a watcher run produces raw_findings that flow through the
normaliser and land in `opportunities`, indistinguishable from scout_agent
findings except for the `source` field (5b acceptance #3)."""
from __future__ import annotations

import httpx
import pytest

from scout.agents.normaliser import normalise_pending
from scout.db import connection, run_migrations
from scout.llm.client import FakeLLMClient, LLMResponse, LLMUsage
from scout.queries_watchers import get_watcher, insert_watcher
from scout.sources import fetcher as fetcher_mod
from scout.sources.watcher_runner import run_watcher
from tests.fixtures.normaliser_passthrough import build_passthrough_response


def _patch_httpx(monkeypatch, handler):
    real = httpx.Client

    def _client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(fetcher_mod.httpx, "Client", _client)


def test_watcher_findings_flow_through_normaliser_to_opportunities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_migrations()
    wid = insert_watcher(
        name="acme-feed",
        url="https://acme.test/feed",
        kind="rss",
        schedule_cron="0 6 * * *",
        active=True,
    )

    feed = """<?xml version="1.0"?><rss version="2.0"><channel>
      <title>Acme</title>
      <item><title>Acme Open Call 2026</title>
            <link>https://acme.test/calls/2026</link>
            <description>Funding for emerging composers; CHF 8,000; due 2026-09-15</description></item>
      <item><title>Acme Residency</title>
            <link>https://acme.test/residency</link>
            <description>Three-month residency in Zurich; open to all disciplines</description></item>
    </channel></rss>"""
    _patch_httpx(monkeypatch, lambda req: httpx.Response(200, text=feed,
                                                         headers={"content-type": "application/xml"}))

    w = get_watcher(wid)
    result = run_watcher(w)
    assert result.status == "ok"
    assert result.findings_inserted == 2

    # Drain the normaliser with a passthrough fake — it echoes whatever the
    # raw_text described, so we get one opportunity per finding.
    fake = FakeLLMClient(responses={
        "normaliser": build_passthrough_response,
        "normaliser_escalated": build_passthrough_response,
    }, default=LLMResponse(text="{}", usage=LLMUsage(model="fake")))

    norm_results = normalise_pending(client=fake)
    assert len(norm_results) == 2
    assert all(r.status == "normalised" for r in norm_results), [r.status for r in norm_results]

    with connection() as conn:
        opps = conn.execute(
            "SELECT id, title, url, status, raw_finding_id FROM opportunities ORDER BY id"
        ).fetchall()
        sources = conn.execute(
            "SELECT DISTINCT source FROM raw_findings WHERE opportunity_id IS NOT NULL"
        ).fetchall()
    assert len(opps) == 2
    titles = sorted(o["title"] for o in opps)
    assert titles == ["Acme Open Call 2026", "Acme Residency"]
    # Every opportunity links back to its raw_finding for audit.
    assert all(o["raw_finding_id"] is not None for o in opps)
    # The source label survives the pipeline so the dashboard can show it.
    assert {r["source"] for r in sources} == {"watcher:acme-feed"}
