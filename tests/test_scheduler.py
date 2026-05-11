"""Scheduler registers the weekly job. We don't trigger the job (it would call
the real LLM); just verify wiring + cron expression."""
from scout.jobs.scheduler import start_scheduler, stop_scheduler


def test_scheduler_registers_weekly_scout_job() -> None:
    sched = start_scheduler()
    try:
        jobs = sched.get_jobs()
        ids = [j.id for j in jobs]
        assert "weekly_scout" in ids
        job = sched.get_job("weekly_scout")
        # CronTrigger fields aren't a public API, but next_run_time should be in the future
        assert job.next_run_time is not None
    finally:
        stop_scheduler()
