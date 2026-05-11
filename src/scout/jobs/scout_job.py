"""Wrapped callable for APScheduler. Catches and logs exceptions so a single
failure doesn't crash the scheduler thread.
"""
from __future__ import annotations

from scout.agents.scout_agent import run_scout
from scout.logging import get_logger

log = get_logger("scout.jobs")


def weekly_scout_job() -> None:
    try:
        summary = run_scout(kind="scout")
        log.info("weekly_scout_job_done", summary=summary)
    except Exception as e:
        log.exception("weekly_scout_job_failed", error=repr(e))
