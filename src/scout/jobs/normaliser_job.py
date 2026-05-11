"""APScheduler-friendly drainer for pending raw_findings.

Runs every 15 minutes (configured in scheduler.py). Cheap on idle: returns
immediately when the pending queue is empty. Wraps exceptions so a single
failure doesn't crash the scheduler thread.
"""
from __future__ import annotations

from scout.agents.normaliser import normalise_pending, pending_count
from scout.logging import get_logger

log = get_logger("scout.jobs")


def normaliser_job() -> None:
    try:
        n = pending_count()
        if n == 0:
            return
        results = normalise_pending(limit=50)
        counts: dict[str, int] = {}
        cost = 0.0
        for r in results:
            counts[r.status] = counts.get(r.status, 0) + 1
            cost += r.cost_usd
        log.info("normaliser_job_done", processed=len(results), pending=n, counts=counts, cost_usd=cost)
    except Exception as e:
        log.exception("normaliser_job_failed", error=repr(e))
