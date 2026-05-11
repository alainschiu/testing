"""Plain SQL queries. Kept here so routes don't grow inline SQL and tests can
exercise them directly. Returns sqlite3.Row (dict-like) or lists thereof.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import date, timedelta

from scout.db import connection
from scout.models import OPPORTUNITY_STATUSES, utc_now_iso

# ── opportunities ─────────────────────────────────────────────────────────


def get_opportunity(opp_id: int) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM opportunities WHERE id = ?", (opp_id,)
        ).fetchone()


def list_opportunities_with_search(
    search: str | None,
    *,
    statuses: Iterable[str] | None = None,
    types: Iterable[str] | None = None,
    citizenship: str | None = None,
    min_fit: int | None = None,
    angle: str | None = None,
    deadline_within_days: int | None = None,
    limit: int = 500,
) -> list[sqlite3.Row]:
    """Single entry point used by routes; handles all filters including search."""
    clauses: list[str] = []
    params: list[object] = []

    if statuses:
        s_list = list(statuses)
        clauses.append(f"status IN ({','.join('?' * len(s_list))})")
        params.extend(s_list)
    if types:
        t_list = list(types)
        clauses.append(f"type IN ({','.join('?' * len(t_list))})")
        params.extend(t_list)
    if citizenship and citizenship.lower() in ("hk", "ca"):
        clauses.append("LOWER(COALESCE(eligibility_citizenship,'')) LIKE ?")
        params.append(f"%{citizenship.lower()}%")
    if min_fit is not None:
        clauses.append("fit_score IS NOT NULL AND fit_score >= ?")
        params.append(min_fit)
    if angle:
        clauses.append("UPPER(COALESCE(primary_angle,'')) LIKE ?")
        params.append(f"{angle.upper()}%")
    if deadline_within_days is not None:
        cutoff = (date.today() + timedelta(days=deadline_within_days)).isoformat()
        clauses.append("(deadline IS NULL OR deadline <= ?)")
        params.append(cutoff)
    if search:
        clauses.append(
            "(LOWER(title) LIKE ? OR LOWER(COALESCE(why_fits,'')) LIKE ?"
            "  OR LOWER(COALESCE(eligibility_citizenship,'')) LIKE ?)"
        )
        like = f"%{search.lower()}%"
        params.extend([like, like, like])

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        "SELECT * FROM opportunities"
        f" {where}"
        " ORDER BY"
        "   CASE WHEN deadline IS NULL THEN 1 ELSE 0 END,"
        "   deadline ASC,"
        "   fit_score DESC NULLS LAST"
        " LIMIT ?"
    )
    params.append(limit)
    with connection() as conn:
        return list(conn.execute(sql, params))


def update_opportunity_field(opp_id: int, field: str, value: object) -> None:
    """Update a single column. Field name is validated against a whitelist."""
    allowed = {
        "user_notes",
        "title",
        "why_fits",
        "risk_watchout",
        "primary_angle",
        "backup_angle",
        "eligibility_citizenship",
        "eligibility_career_stage",
        "eligibility_other",
        "deadline",
        "deadline_note",
        "fit_score",
        "effort_estimate",
        "competitiveness",
        "location",
        "amount",
    }
    if field not in allowed:
        raise ValueError(f"field not editable: {field}")
    if field == "fit_score" and value not in (None, "", "0", "1", "2", "3", "4", "5"):
        try:
            value = max(1, min(5, int(value)))
        except (TypeError, ValueError) as e:
            raise ValueError("fit_score must be 1-5") from e
    elif field == "fit_score":
        value = None if value in (None, "", "0") else int(value)

    now = utc_now_iso()
    with connection() as conn:
        conn.execute(
            f"UPDATE opportunities SET {field} = ?, updated_at = ? WHERE id = ?",
            (value, now, opp_id),
        )


def change_status(opp_id: int, to_status: str, note: str | None = None) -> sqlite3.Row | None:
    if to_status not in OPPORTUNITY_STATUSES:
        raise ValueError(f"invalid status: {to_status}")
    now = utc_now_iso()
    with connection() as conn:
        row = conn.execute(
            "SELECT status FROM opportunities WHERE id = ?", (opp_id,)
        ).fetchone()
        if row is None:
            return None
        from_status = row["status"]
        if from_status == to_status:
            return get_opportunity(opp_id)
        conn.execute(
            "UPDATE opportunities SET status = ?, status_changed_at = ?, updated_at = ?"
            " WHERE id = ?",
            (to_status, now, now, opp_id),
        )
        conn.execute(
            "INSERT INTO status_audit (opportunity_id, from_status, to_status, at, note)"
            " VALUES (?, ?, ?, ?, ?)",
            (opp_id, from_status, to_status, now, note),
        )
    return get_opportunity(opp_id)


def status_history(opp_id: int) -> list[sqlite3.Row]:
    with connection() as conn:
        return list(
            conn.execute(
                "SELECT from_status, to_status, at, note FROM status_audit"
                " WHERE opportunity_id = ? ORDER BY at DESC",
                (opp_id,),
            )
        )


# ── dashboard / counts ────────────────────────────────────────────────────


def status_counts() -> dict[str, int]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM opportunities GROUP BY status"
        ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def dashboard_columns(*, decide_within_days: int = 14) -> dict[str, list[sqlite3.Row]]:
    cutoff = (date.today() + timedelta(days=decide_within_days)).isoformat()
    with connection() as conn:
        decide = list(
            conn.execute(
                "SELECT * FROM opportunities"
                " WHERE status IN ('lead','triaged')"
                "   AND deadline IS NOT NULL"
                "   AND deadline <= ?"
                " ORDER BY deadline ASC, fit_score DESC NULLS LAST",
                (cutoff,),
            )
        )
        drafting = list(
            conn.execute(
                "SELECT * FROM opportunities WHERE status = 'drafting'"
                " ORDER BY deadline ASC NULLS LAST"
            )
        )
        applied = list(
            conn.execute(
                "SELECT * FROM opportunities WHERE status = 'applied'"
                " ORDER BY status_changed_at DESC"
            )
        )
    return {"decide": decide, "drafting": drafting, "applied": applied}


def top3_picks(*, decide_within_days: int = 30) -> list[sqlite3.Row]:
    """Same ranking as the CLI digest, scoped to actionable items."""
    with connection() as conn:
        return list(
            conn.execute(
                "SELECT * FROM opportunities"
                " WHERE status IN ('lead','triaged')"
                " ORDER BY fit_score DESC NULLS LAST,"
                "          CASE WHEN deadline IS NULL THEN 1 ELSE 0 END,"
                "          deadline ASC"
                " LIMIT 3"
            )
        )


def latest_run() -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM runs WHERE kind = 'scout' ORDER BY id DESC LIMIT 1"
        ).fetchone()


def cost_this_month() -> float:
    today = date.today()
    start = today.replace(day=1).isoformat()
    with connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM runs"
            " WHERE started_at >= ?",
            (start,),
        ).fetchone()
    return float(row["total"] or 0)


def list_runs(limit: int = 50) -> list[sqlite3.Row]:
    with connection() as conn:
        return list(
            conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            )
        )
