"""Route tests via httpx.AsyncClient hitting the FastAPI app directly.

Lifespan migration is bypassed (it doesn't fire under ASGITransport); each
test calls run_migrations() explicitly via the conftest-isolated DB.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from scout.db import connection, run_migrations
from scout.models import utc_now_iso
from scout.web.app import app


@pytest.fixture
async def client() -> AsyncClient:
    run_migrations()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _seed_opp(*, title="DAAD Berliner Künstlerprogramm", deadline="2026-08-31", status="lead", fit=5, citizenship="Both", angle="A"):
    now = utc_now_iso()
    with connection() as conn:
        cur = conn.execute(
            "INSERT INTO opportunities (title, type, url, url_hash, deadline, fit_score,"
            " primary_angle, eligibility_citizenship, why_fits, status, discovered_at, updated_at)"
            " VALUES (?, 'residency', ?, ?, ?, ?, ?, ?, 'Practice-led research fits.', ?, ?, ?)",
            (title, f"https://example.org/{title.replace(' ','-')}", title, deadline, fit, angle, citizenship, status, now, now),
        )
        return int(cur.lastrowid)


async def test_dashboard_empty(client: AsyncClient) -> None:
    r = await client.get("/")
    assert r.status_code == 200
    assert "Scout" in r.text
    assert "empty" in r.text  # column placeholder


async def test_dashboard_with_data_shows_top3_and_columns(client: AsyncClient) -> None:
    _seed_opp(title="DAAD", deadline="2026-05-20")  # within 14 days
    _seed_opp(title="Solitude", deadline="2027-01-01", fit=3)  # far out
    _seed_opp(title="In drafts", status="drafting", deadline="2026-06-01")
    _seed_opp(title="Already applied", status="applied", deadline="2026-04-01")

    r = await client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "DAAD" in body
    assert "In drafts" in body
    assert "Already applied" in body
    # Top 3 panel renders the fit-5 entry by title
    assert "Top 3" in body


async def test_list_filters_round_trip(client: AsyncClient) -> None:
    _seed_opp(title="HK Eligible Match", citizenship="HK ✓ both", fit=5)
    _seed_opp(title="CA Only", citizenship="CA ✓", fit=2)

    r = await client.get("/opportunities?citizenship=hk&min_fit=4")
    assert r.status_code == 200
    assert "HK Eligible Match" in r.text
    assert "CA Only" not in r.text


async def test_search_filter(client: AsyncClient) -> None:
    _seed_opp(title="Berlin DAAD")
    _seed_opp(title="Stuttgart Solitude")
    r = await client.get("/opportunities?search=berlin")
    assert "Berlin DAAD" in r.text
    assert "Stuttgart Solitude" not in r.text


async def test_detail_404_on_missing(client: AsyncClient) -> None:
    r = await client.get("/opportunities/9999")
    assert r.status_code == 404


async def test_detail_renders_with_status_buttons(client: AsyncClient) -> None:
    opp_id = _seed_opp()
    r = await client.get(f"/opportunities/{opp_id}")
    assert r.status_code == 200
    assert "Triage in" in r.text
    assert "Start draft" in r.text


async def test_inline_field_patch_user_notes(client: AsyncClient) -> None:
    opp_id = _seed_opp()
    r = await client.post(
        f"/opportunities/{opp_id}/field",
        data={"field": "user_notes", "value": "Talk to Berlin contact about referee."},
    )
    assert r.status_code == 200
    assert "Berlin contact" in r.text  # rendered back into textarea

    with connection() as conn:
        notes = conn.execute(
            "SELECT user_notes FROM opportunities WHERE id = ?", (opp_id,)
        ).fetchone()["user_notes"]
    assert "Berlin contact" in notes


async def test_field_patch_rejects_unknown_field(client: AsyncClient) -> None:
    opp_id = _seed_opp()
    r = await client.post(
        f"/opportunities/{opp_id}/field",
        data={"field": "status", "value": "won"},
    )
    assert r.status_code == 400


async def test_status_transition_writes_audit_row(client: AsyncClient) -> None:
    opp_id = _seed_opp()
    r = await client.post(f"/opportunities/{opp_id}/status?to=triaged")
    assert r.status_code == 200
    # HTMX response is the status-buttons partial reflecting the new state
    assert 'hx-post="/opportunities/' in r.text

    with connection() as conn:
        opp = conn.execute(
            "SELECT status FROM opportunities WHERE id = ?", (opp_id,)
        ).fetchone()
        audit = conn.execute(
            "SELECT from_status, to_status FROM status_audit WHERE opportunity_id = ?",
            (opp_id,),
        ).fetchall()
    assert opp["status"] == "triaged"
    assert len(audit) == 1
    assert (audit[0]["from_status"], audit[0]["to_status"]) == ("lead", "triaged")


async def test_status_persists_across_simulated_restart(client: AsyncClient) -> None:
    opp_id = _seed_opp()
    await client.post(f"/opportunities/{opp_id}/status?to=triaged")
    # "Restart" — new TestClient lifecycle. SQLite file persists per the conftest tmp_path.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as fresh:
        r = await fresh.get(f"/opportunities/{opp_id}")
    assert r.status_code == 200
    assert ">triaged<" in r.text


async def test_invalid_status_target_400(client: AsyncClient) -> None:
    opp_id = _seed_opp()
    r = await client.post(f"/opportunities/{opp_id}/status?to=not-a-status")
    assert r.status_code == 400


async def test_start_draft_creates_application_and_redirects(client: AsyncClient) -> None:
    opp_id = _seed_opp()
    r = await client.post(f"/opportunities/{opp_id}/start-draft", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/applications/")

    with connection() as conn:
        opp = conn.execute("SELECT status FROM opportunities WHERE id=?", (opp_id,)).fetchone()
        apps = conn.execute(
            "SELECT id, status FROM applications WHERE opportunity_id=?", (opp_id,)
        ).fetchall()
    assert opp["status"] == "drafting"
    assert len(apps) == 1
    assert apps[0]["status"] == "drafting"


async def test_runs_page_renders(client: AsyncClient) -> None:
    now = utc_now_iso()
    with connection() as conn:
        conn.execute(
            "INSERT INTO runs (kind, started_at, finished_at, status, opportunities_found,"
            " opportunities_added, cost_usd) VALUES ('scout', ?, ?, 'success', 12, 8, 0.42)",
            (now, now),
        )
    r = await client.get("/runs")
    assert r.status_code == 200
    assert "success" in r.text
    assert "0.4200" in r.text


async def test_settings_page_redacts_keys(client: AsyncClient) -> None:
    r = await client.get("/settings")
    assert r.status_code == 200
    body = r.text
    assert "redacted" in body or "<unset>" in body or "test-key-do-not-use" not in body
    assert "Artist profile snapshot" in body
    assert "Alain Chiu" in body or "趙朗天" in body


async def test_settings_dry_run_button(client: AsyncClient) -> None:
    r = await client.post("/settings/run-scout?dry_run=true")
    assert r.status_code == 200
    assert "dry-run" in r.text
