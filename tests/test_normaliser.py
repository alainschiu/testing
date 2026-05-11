"""Phase 5a normaliser: verdict routing, dedup, multi-opportunity, retry cap.

Each test inserts a hand-crafted raw_finding directly (the source-agnostic
`insert_raw_finding` helper), then drains it via `normalise_pending` with a
scripted FakeLLMClient. We assert on raw_findings.status, the linked
opportunity_id, and the resulting opportunities row(s)."""
from __future__ import annotations

import json

from scout.agents.normaliser import (
    insert_raw_finding,
    normalise_pending,
    pending_count,
)
from scout.db import connection, run_migrations
from scout.llm.client import FakeLLMClient, LLMResponse, LLMUsage


def _resp(payload: dict) -> LLMResponse:
    return LLMResponse(
        text=json.dumps(payload),
        usage=LLMUsage(model="fake-sonnet", input_tokens=500, output_tokens=200, cost_usd=0.0),
        stop_reason="end_turn",
    )


def _opportunity_payload(**overrides) -> dict:
    base = {
        "verdict": "opportunity",
        "reject_reason": None,
        "opportunities": [{
            "title": "ResArtis Mock Residency",
            "url": "https://example.org/residency-mock",
            "type": "residency",
            "deadline": "2026-09-30",
            "fit_score": 4,
            "primary_angle": "B",
        }],
    }
    base.update(overrides)
    return base


def _fake(verdict_payload: dict | list[dict]) -> FakeLLMClient:
    """Single-payload fake. Always returns the same LLMResponse for any
    template containing 'normaliser'. List form is used for multi-call tests."""
    if isinstance(verdict_payload, list):
        responses = [_resp(p) for p in verdict_payload]
        return FakeLLMClient(responses={"normaliser": responses})
    return FakeLLMClient(responses={"normaliser": _resp(verdict_payload)})


# ── Acceptance: not_an_opportunity gets rejected ──────────────────────────


def test_not_an_opportunity_is_rejected_with_reason() -> None:
    run_migrations()
    fid = insert_raw_finding(
        source="manual",
        raw_text="My grandmother's chocolate-chip cookie recipe: cream butter and sugar...",
    )
    fake = _fake({
        "verdict": "not_an_opportunity",
        "reject_reason": "not_funding",
        "opportunities": [],
    })

    results = normalise_pending(client=fake)
    assert len(results) == 1
    assert results[0].status == "rejected"
    assert results[0].reject_reason == "not_funding"

    with connection() as conn:
        row = conn.execute("SELECT * FROM raw_findings WHERE id=?", (fid,)).fetchone()
        n = conn.execute("SELECT COUNT(*) AS n FROM opportunities").fetchone()["n"]
    assert row["status"] == "rejected"
    assert row["reject_reason"] == "not_funding"
    assert row["opportunity_id"] is None
    assert n == 0, "no opportunity row should be created for a rejection"


# ── Acceptance: duplicate URL gets linked, not re-inserted ────────────────


def test_duplicate_url_links_to_existing_opportunity() -> None:
    run_migrations()
    insert_raw_finding(source="scout_agent", raw_text="...", source_url="https://example.org/x")
    payload = _opportunity_payload()
    payload["opportunities"][0]["url"] = "https://example.org/x"

    fake = _fake([payload, payload])  # both findings normalise to same URL

    # Insert a SECOND finding pointing at the same URL.
    insert_raw_finding(source="manual", raw_text="...", source_url="https://example.org/x")

    results = normalise_pending(client=fake)
    statuses = sorted(r.status for r in results)
    assert statuses == ["duplicate", "normalised"], statuses

    with connection() as conn:
        n_opp = conn.execute("SELECT COUNT(*) AS n FROM opportunities").fetchone()["n"]
        rf = conn.execute("SELECT id, status, opportunity_id FROM raw_findings ORDER BY id").fetchall()
    assert n_opp == 1, "duplicate URL must not insert a second opportunity row"
    # The duplicate raw_finding must still link to the opportunity for audit.
    duplicate_row = next(r for r in rf if r["status"] == "duplicate")
    normalised_row = next(r for r in rf if r["status"] == "normalised")
    assert duplicate_row["opportunity_id"] == normalised_row["opportunity_id"]


# ── Multi-opportunity newsletter splits cleanly ───────────────────────────


def test_multi_opportunity_newsletter_produces_one_opp_per_item() -> None:
    run_migrations()
    insert_raw_finding(
        source="email",
        raw_text="Three calls in this issue: A, B, C — see links below.",
        source_meta={"message_id": "<test@ex>"},
    )
    payload = {
        "verdict": "opportunity",
        "reject_reason": None,
        "opportunities": [
            {"title": "Call A", "url": "https://ex.org/a", "type": "grant"},
            {"title": "Call B", "url": "https://ex.org/b", "type": "residency"},
            {"title": "Call C", "url": "https://ex.org/c", "type": "fellowship"},
        ],
    }
    fake = _fake(payload)

    results = normalise_pending(client=fake)
    assert len(results) == 1
    assert results[0].status == "normalised"
    assert len(results[0].opportunity_ids) == 3

    with connection() as conn:
        opps = conn.execute(
            "SELECT title, type, raw_finding_id FROM opportunities ORDER BY id"
        ).fetchall()
    assert [o["title"] for o in opps] == ["Call A", "Call B", "Call C"]
    # All three opportunities link back to the same raw_finding.
    rf_ids = {o["raw_finding_id"] for o in opps}
    assert len(rf_ids) == 1


# ── Verdict routing: unclear stays as 'error' for manual triage ───────────


def test_unclear_verdict_marked_error_visible_for_manual_review() -> None:
    run_migrations()
    fid = insert_raw_finding(source="manual", raw_text="something about a grant maybe")
    fake = _fake({"verdict": "unclear", "reject_reason": "other", "opportunities": []})

    results = normalise_pending(client=fake)
    assert results[0].status == "error"

    with connection() as conn:
        row = conn.execute("SELECT * FROM raw_findings WHERE id=?", (fid,)).fetchone()
    assert row["status"] == "error"
    assert row["reject_reason"] is not None
    assert row["attempts"] == 1


# ── JSON parse failure escalates to fallback model, then gives up ─────────


def test_json_parse_failure_escalates_then_errors_after_two_strikes() -> None:
    run_migrations()
    insert_raw_finding(source="manual", raw_text="some text")

    bad = LLMResponse(
        text="here is some prose with no JSON in it at all",
        usage=LLMUsage(model="fake-sonnet", cost_usd=0.0),
        stop_reason="end_turn",
    )
    fake = FakeLLMClient(responses={"normaliser": [bad, bad]})

    results = normalise_pending(client=fake)
    assert results[0].status == "error"
    assert results[0].error and "json_parse_failed" in results[0].error

    # Second call to normalise_pending should also process this row (attempts=1)
    fake2 = FakeLLMClient(responses={"normaliser": [bad, bad]})
    results2 = normalise_pending(client=fake2)
    assert len(results2) == 1
    assert results2[0].status == "error"

    # Now attempts=2 → row is permanently parked. pending_count drops to 0.
    assert pending_count() == 0
    results3 = normalise_pending(client=fake2)
    assert results3 == []


# ── Forgiving JSON parser handles fenced output ───────────────────────────


def test_forgiving_json_parser_strips_markdown_fences() -> None:
    run_migrations()
    insert_raw_finding(source="manual", raw_text="x", source_url="https://ex.org/y")

    payload_text = "```json\n" + json.dumps(_opportunity_payload()) + "\n```"
    fenced = LLMResponse(text=payload_text, usage=LLMUsage(model="f", cost_usd=0.0), stop_reason="end_turn")
    fake = FakeLLMClient(responses={"normaliser": fenced})

    results = normalise_pending(client=fake)
    assert results[0].status == "normalised"


# ── insert_raw_finding contract ───────────────────────────────────────────


def test_insert_raw_finding_returns_id_and_persists_metadata() -> None:
    run_migrations()
    fid = insert_raw_finding(
        source="watcher:resartis",
        raw_text="Open call: blah",
        source_url="https://resartis.org/x",
        source_meta={"watcher_id": 7, "discovered_at": "2026-05-11"},
    )
    with connection() as conn:
        row = conn.execute("SELECT * FROM raw_findings WHERE id=?", (fid,)).fetchone()
    assert row["source"] == "watcher:resartis"
    assert row["status"] == "pending"
    assert row["attempts"] == 0
    meta = json.loads(row["source_meta_json"])
    assert meta["watcher_id"] == 7
