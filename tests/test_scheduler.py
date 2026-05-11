"""Scheduler registers the weekly job + the 15-min normaliser drainer + one
job per active watcher. We don't trigger any of them (they'd call the real
LLM or hit the network); just verify wiring + cron expressions."""
from scout.db import run_migrations
from scout.jobs.scheduler import start_scheduler, stop_scheduler
from scout.queries_watchers import insert_watcher


def test_scheduler_registers_weekly_scout_and_normaliser_jobs() -> None:
    run_migrations()
    sched = start_scheduler()
    try:
        ids = [j.id for j in sched.get_jobs()]
        assert "weekly_scout" in ids
        assert "normaliser_drain" in ids
        assert sched.get_job("weekly_scout").next_run_time is not None
        assert sched.get_job("normaliser_drain").next_run_time is not None
    finally:
        stop_scheduler()


def test_scheduler_registers_one_job_per_active_watcher() -> None:
    run_migrations()
    wid_a = insert_watcher(
        name="alpha", url="https://example.org/a", kind="html_static",
        schedule_cron="0 6 * * *", active=True,
    )
    wid_b = insert_watcher(
        name="beta", url="https://example.org/b", kind="html_static",
        schedule_cron="0 7 * * *", active=False,
    )
    sched = start_scheduler()
    try:
        ids = {j.id for j in sched.get_jobs() if j.id.startswith("watcher:")}
        assert f"watcher:{wid_a}" in ids
        assert f"watcher:{wid_b}" not in ids, "inactive watchers must not be scheduled"
    finally:
        stop_scheduler()
