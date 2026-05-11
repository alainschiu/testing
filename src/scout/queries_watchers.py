"""CRUD + listing helpers for the watchers table."""
from __future__ import annotations

import sqlite3
from typing import Any

from scout.db import connection
from scout.models import utc_now_iso

WATCHER_KINDS = ("html_static", "html_js", "rss", "json_api")
EDITABLE_FIELDS = {
    "name", "url", "kind", "selector", "schedule_cron", "headers_json", "notes", "active",
}


def list_watchers(*, active_only: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM watchers"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY name ASC"
    with connection() as conn:
        return list(conn.execute(sql))


def get_watcher(watcher_id: int) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute("SELECT * FROM watchers WHERE id = ?", (watcher_id,)).fetchone()


def get_watcher_by_name(name: str) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute("SELECT * FROM watchers WHERE name = ?", (name,)).fetchone()


def insert_watcher(
    *,
    name: str,
    url: str,
    kind: str,
    schedule_cron: str,
    selector: str | None = None,
    headers_json: str | None = None,
    notes: str | None = None,
    active: bool = True,
) -> int:
    if kind not in WATCHER_KINDS:
        raise ValueError(f"unknown kind: {kind!r}; expected one of {WATCHER_KINDS}")
    now = utc_now_iso()
    with connection() as conn:
        cur = conn.execute(
            "INSERT INTO watchers"
            " (name, url, kind, selector, schedule_cron, headers_json, notes, active, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, url, kind, selector, schedule_cron, headers_json, notes, 1 if active else 0, now, now),
        )
        return int(cur.lastrowid)


def upsert_watcher_by_name(
    *,
    name: str,
    url: str,
    kind: str,
    schedule_cron: str,
    selector: str | None = None,
    headers_json: str | None = None,
    notes: str | None = None,
    active: bool = True,
) -> int:
    """Insert if name is new, otherwise update mutable fields. Returns id."""
    existing = get_watcher_by_name(name)
    if existing is None:
        return insert_watcher(
            name=name, url=url, kind=kind, schedule_cron=schedule_cron,
            selector=selector, headers_json=headers_json, notes=notes, active=active,
        )
    now = utc_now_iso()
    with connection() as conn:
        conn.execute(
            "UPDATE watchers SET url=?, kind=?, selector=?, schedule_cron=?,"
            " headers_json=?, notes=?, active=?, updated_at=? WHERE id=?",
            (url, kind, selector, schedule_cron, headers_json, notes,
             1 if active else 0, now, existing["id"]),
        )
    return int(existing["id"])


_UNCHANGED: Any = object()


def update_watcher_state(
    watcher_id: int,
    *,
    last_checked_at: Any = _UNCHANGED,
    last_content_hash: Any = _UNCHANGED,
    consecutive_failures: Any = _UNCHANGED,
    last_error: Any = _UNCHANGED,
    active: Any = _UNCHANGED,
) -> None:
    """Update mutable runtime fields. Pass `None` to set a column to NULL;
    omit the kwarg (default sentinel) to leave the column alone."""
    sets: list[str] = []
    params: list[Any] = []
    if last_checked_at is not _UNCHANGED:
        sets.append("last_checked_at = ?")
        params.append(last_checked_at)
    if last_content_hash is not _UNCHANGED:
        sets.append("last_content_hash = ?")
        params.append(last_content_hash)
    if consecutive_failures is not _UNCHANGED:
        sets.append("consecutive_failures = ?")
        params.append(consecutive_failures)
    if last_error is not _UNCHANGED:
        sets.append("last_error = ?")
        params.append(last_error)
    if active is not _UNCHANGED:
        sets.append("active = ?")
        params.append(1 if active else 0)
    if not sets:
        return
    sets.append("updated_at = ?")
    params.append(utc_now_iso())
    params.append(watcher_id)
    with connection() as conn:
        conn.execute(f"UPDATE watchers SET {', '.join(sets)} WHERE id = ?", params)


def set_active(watcher_id: int, active: bool) -> None:
    update_watcher_state(watcher_id, active=active)


def delete_watcher(watcher_id: int) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM watchers WHERE id = ?", (watcher_id,))
