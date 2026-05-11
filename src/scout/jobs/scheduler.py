"""APScheduler setup. In-process cron; no Celery, no Redis.

Phase 1 wires a weekly scout job. Future phases can add daily deadline checks.
"""
from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from scout.config import get_settings
from scout.jobs.scout_job import weekly_scout_job
from scout.logging import get_logger

log = get_logger("scout.scheduler")

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> BackgroundScheduler:
    """Start the in-process scheduler. Idempotent."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    s = get_settings()
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        weekly_scout_job,
        CronTrigger(
            day_of_week=s.cron_day_of_week,
            hour=s.cron_hour,
            minute=s.cron_minute,
        ),
        id="weekly_scout",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    log.info(
        "scheduler_started",
        weekly_cron=f"{s.cron_day_of_week} {s.cron_hour:02d}:{s.cron_minute:02d}",
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("scheduler_stopped")
    _scheduler = None
