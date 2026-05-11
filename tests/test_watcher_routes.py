"""Watcher routes and the dashboard source-health strip."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scout.db import run_migrations
from scout.queries_watchers import get_watcher, insert_watcher
from scout.web.app import app


@pytest.fixture
def client() -> TestClient:
    run_migrations()
    return TestClient(app)


def test_watchers_list_renders_seeded_rows(client: TestClient) -> None:
    insert_watcher(name="row-a", url="https://x.test/a", kind="rss",
                   schedule_cron="0 6 * * *", active=True)
    insert_watcher(name="row-b", url="https://x.test/b", kind="html_static",
                   schedule_cron="0 7 * * *", active=False)

    r = client.get("/watchers")
    assert r.status_code == 200
    assert "row-a" in r.text
    assert "row-b" in r.text
    assert "rss" in r.text


def test_watchers_empty_state_renders(client: TestClient) -> None:
    r = client.get("/watchers")
    assert r.status_code == 200
    assert "No watchers yet" in r.text


def test_watchers_toggle_flips_active(client: TestClient) -> None:
    wid = insert_watcher(name="tog", url="https://x.test/t", kind="rss",
                         schedule_cron="0 6 * * *", active=True)
    r = client.post(f"/watchers/{wid}/toggle", data={"active": "0"}, follow_redirects=False)
    assert r.status_code == 303
    assert get_watcher(wid)["active"] == 0


def test_watchers_delete_removes_row(client: TestClient) -> None:
    wid = insert_watcher(name="goner", url="https://x.test/g", kind="rss",
                         schedule_cron="0 6 * * *", active=True)
    r = client.post(f"/watchers/{wid}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert get_watcher(wid) is None


def test_dashboard_renders_source_health_strip(client: TestClient) -> None:
    insert_watcher(name="hsa", url="https://x.test/a", kind="rss",
                   schedule_cron="0 6 * * *", active=True)
    r = client.get("/")
    assert r.status_code == 200
    assert "Source health" in r.text
    assert "watchers active" in r.text
