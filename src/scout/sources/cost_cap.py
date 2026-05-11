"""Daily extraction-cost cap.

Watchers that hit the LLM-fallback path cost real money. The brief defaults
to $2/day across all watchers. When the cap is hit, fetchers keep running
(cheap), content hashes still update, but LLM extraction defers to the next
day. The runner sets `extraction_deferred=true` on the watcher row when this
happens so the UI surfaces it.

Spend is tracked in the `runs` table — each watcher run inserts a row with
kind='watcher:<name>' and cost_usd. We sum cost_usd over today's runs to
decide whether to allow a new LLM call. On Poe (where cost_usd is always 0)
the cap effectively never trips — that's intentional, since Poe spend lives
on a separate dashboard.
"""
from __future__ import annotations

from datetime import date

from scout.config import get_settings
from scout.db import connection
from scout.logging import get_logger

log = get_logger("scout.sources.cost_cap")


def todays_extraction_spend_usd() -> float:
    today_start = f"{date.today().isoformat()}T00:00:00Z"
    with connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM runs"
            " WHERE kind LIKE 'watcher:%' AND started_at >= ?",
            (today_start,),
        ).fetchone()
    return float(row["total"] or 0)


def extraction_allowed() -> tuple[bool, float, float]:
    """Returns (allowed, spent_today, cap)."""
    s = get_settings()
    cap = s.extraction_daily_usd_cap
    spent = todays_extraction_spend_usd()
    if cap <= 0:
        return True, spent, cap
    return spent < cap, spent, cap
