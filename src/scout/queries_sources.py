"""Source-health queries: per-source finding counts, deactivated watchers."""
from __future__ import annotations

from datetime import datetime, timedelta

from scout.db import connection
from scout.models import utc_now_iso


def _categorise(source: str) -> str:
    if source.startswith("watcher:"):
        return "watchers"
    if source.startswith("rss:"):
        return "rss"
    if source == "scout_agent":
        return "scout"
    if source == "email":
        return "email"
    if source == "manual":
        return "manual"
    if source == "legacy_scout":
        return "scout"
    return "other"


def source_health_summary(*, days: int = 7) -> dict:
    """Return per-category counts of raw_findings in the last N days plus the
    number of deactivated watchers. Used by the dashboard footer strip."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds") + "Z"
    with connection() as conn:
        rows = conn.execute(
            "SELECT source, COUNT(*) AS n FROM raw_findings"
            " WHERE captured_at >= ? GROUP BY source",
            (cutoff,),
        ).fetchall()
        deact = conn.execute(
            "SELECT COUNT(*) AS n FROM watchers"
            " WHERE active = 0 AND consecutive_failures > 0"
        ).fetchone()["n"]
        active = conn.execute("SELECT COUNT(*) AS n FROM watchers WHERE active = 1").fetchone()["n"]
    by_cat: dict[str, int] = {}
    for r in rows:
        by_cat[_categorise(r["source"])] = by_cat.get(_categorise(r["source"]), 0) + int(r["n"])
    return {
        "by_category": by_cat,
        "deactivated_watchers": int(deact or 0),
        "active_watchers": int(active or 0),
        "days": days,
        "as_of": utc_now_iso(),
    }
