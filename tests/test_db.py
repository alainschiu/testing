"""Migrations are idempotent and produce the expected schema."""
from scout.db import connection, run_migrations


def test_migrations_run_cleanly_twice() -> None:
    first = run_migrations()
    assert "001_init.sql" in first

    second = run_migrations()
    assert second == [], "second run should be a no-op"

    with connection() as conn:
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    expected = {"artist_profile", "funders", "runs", "opportunities", "applications", "drafts", "work_samples", "_migrations"}
    assert expected.issubset(tables), tables - expected


def test_opportunities_url_hash_is_unique() -> None:
    import sqlite3

    run_migrations()
    now = "2026-05-11T00:00:00Z"
    with connection() as conn:
        conn.execute(
            "INSERT INTO opportunities (title, type, url, url_hash, status, discovered_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("A", "grant", "https://x/y", "abc", "lead", now, now),
        )
        try:
            conn.execute(
                "INSERT INTO opportunities (title, type, url, url_hash, status, discovered_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("B", "grant", "https://x/z", "abc", "lead", now, now),
            )
            raise AssertionError("expected IntegrityError on duplicate url_hash")
        except sqlite3.IntegrityError:
            pass
