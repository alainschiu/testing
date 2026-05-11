"""APScheduler setup. In-process cron; no Celery, no Redis.

Phase 1 wires a weekly scout job. Future phases can add daily deadline checks.
"""
from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from scout.config import get_settings
from scout.jobs.normaliser_job import normaliser_job
from scout.jobs.scout_job import weekly_scout_job
from scout.jobs.watcher_job import run_watcher_job
from scout.logging import get_logger
from scout.queries_watchers import list_watchers

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
    # Drain pending raw_findings every 15 minutes. Cheap when the queue is
    # empty (a single COUNT(*) query) so always-on is fine.
    _scheduler.add_job(
        normaliser_job,
        IntervalTrigger(minutes=15),
        id="normaliser_drain",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    register_watchers(_scheduler)
    _scheduler.start()
    log.info(
        "scheduler_started",
        weekly_cron=f"{s.cron_day_of_week} {s.cron_hour:02d}:{s.cron_minute:02d}",
        normaliser_interval_min=15,
        watchers_registered=sum(1 for j in _scheduler.get_jobs() if j.id.startswith("watcher:")),
    )
    return _scheduler


def register_watchers(scheduler: BackgroundScheduler) -> int:
    """Sync the active-watchers set with APScheduler jobs.

    Adds a job per active watcher; removes any `watcher:*` job whose watcher
    is no longer active or no longer exists. Called at startup, and exposed
    so the UI can reload the scheduler after CRUD operations."""
    existing = {j.id for j in scheduler.get_jobs() if j.id.startswith("watcher:")}
    seen: set[str] = set()
    added = removed = 0
    for w in list_watchers(active_only=True):
        job_id = f"watcher:{w['id']}"
        seen.add(job_id)
        try:
            trigger = CronTrigger.from_crontab(w["schedule_cron"])
        except ValueError as e:
            log.warning("watcher_bad_cron", watcher=w["name"], cron=w["schedule_cron"], error=repr(e))
            continue
        scheduler.add_job(
            run_watcher_job,
            trigger,
            args=[int(w["id"])],
            id=job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        added += 1
    for stale in existing - seen:
        scheduler.remove_job(stale)
        removed += 1
    log.info("watchers_registered", added=added, removed=removed)
    return added


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("scheduler_stopped")
    _scheduler = None
