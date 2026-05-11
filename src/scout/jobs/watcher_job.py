"""APScheduler-friendly wrapper for a single watcher run.

Each active watcher gets its own scheduled job (id = `watcher:{id}`) firing
on its configured `schedule_cron`. The job loads the watcher row fresh
(captures any state changes since the scheduler started), runs it, and
chains a normaliser pass when new findings were inserted so they don't sit
in `pending` until the 15-min global drainer.
"""
from __future__ import annotations

from scout.agents.normaliser import normalise_pending, pending_count
from scout.logging import get_logger
from scout.queries_watchers import get_watcher
from scout.sources.watcher_runner import run_watcher

log = get_logger("scout.jobs.watcher")


def run_watcher_job(watcher_id: int) -> None:
    try:
        w = get_watcher(watcher_id)
        if w is None:
            log.warning("watcher_not_found", watcher_id=watcher_id)
            return
        if not w["active"]:
            log.info("watcher_skipped_inactive", watcher_id=watcher_id, name=w["name"])
            return

        result = run_watcher(w)
        log.info(
            "watcher_job_done",
            watcher_id=watcher_id,
            name=w["name"],
            status=result.status,
            inserted=result.findings_inserted,
            cost_usd=result.cost_usd,
        )
        # Drain the queue for this watcher's contribution rather than waiting
        # 15 minutes — keeps end-to-end latency low.
        if result.findings_inserted and pending_count():
            normalise_pending(limit=result.findings_inserted + 5)
    except Exception as e:
        log.exception("watcher_job_failed", watcher_id=watcher_id, error=repr(e))
