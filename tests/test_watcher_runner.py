"""watcher_runner: fetch → diff → extract → raw_findings, with 3-strike auto-deactivate."""
from __future__ import annotations

import httpx
import pytest

from scout.config import get_settings
from scout.db import connection, run_migrations
from scout.queries_watchers import get_watcher, insert_watcher
from scout.sources import fetcher as fetcher_mod
from scout.sources.watcher_runner import run_watcher


def _patch_httpx(monkeypatch, handler):
    real = httpx.Client

    def _client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(fetcher_mod.httpx, "Client", _client)


def _seed_watcher(**kw) -> int:
    base = dict(
        name="test-watcher",
        url="https://x.test/listings",
        kind="rss",
        schedule_cron="0 6 * * *",
        active=True,
    )
    base.update(kw)
    return insert_watcher(**base)


def test_happy_path_inserts_raw_findings_and_resets_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    run_migrations()
    wid = _seed_watcher(kind="rss", url="https://feed.test/x")

    feed = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>A</title><link>https://feed.test/a</link><guid>g-a</guid></item>
      <item><title>B</title><link>https://feed.test/b</link><guid>g-b</guid></item>
    </channel></rss>"""
    _patch_httpx(monkeypatch, lambda req: httpx.Response(200, text=feed,
                                                         headers={"content-type": "application/xml"}))

    w = get_watcher(wid)
    result = run_watcher(w)
    assert result.status == "ok"
    assert result.findings_inserted == 2

    with connection() as conn:
        rf = conn.execute(
            "SELECT source, source_url, status FROM raw_findings"
            " WHERE source = 'watcher:test-watcher' ORDER BY id"
        ).fetchall()
    assert len(rf) == 2
    assert rf[0]["source_url"] == "https://feed.test/a"
    assert rf[0]["status"] == "pending"

    refreshed = get_watcher(wid)
    assert refreshed["last_content_hash"]
    assert refreshed["consecutive_failures"] == 0
    assert refreshed["last_error"] is None
    assert refreshed["active"] == 1


def test_unchanged_content_skips_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    run_migrations()
    wid = _seed_watcher(kind="rss", url="https://feed.test/x")

    feed = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Same</title><link>https://feed.test/same</link><guid>g</guid></item>
    </channel></rss>"""
    _patch_httpx(monkeypatch, lambda req: httpx.Response(200, text=feed,
                                                         headers={"content-type": "application/xml"}))

    w = get_watcher(wid)
    r1 = run_watcher(w)
    assert r1.status == "ok"
    assert r1.findings_inserted == 1

    w = get_watcher(wid)
    r2 = run_watcher(w)
    assert r2.status == "unchanged"
    assert r2.findings_inserted == 0

    with connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM raw_findings WHERE source = 'watcher:test-watcher'"
        ).fetchone()["n"]
    assert n == 1, "unchanged run must not insert duplicates"


def test_three_consecutive_failures_auto_deactivates(monkeypatch: pytest.MonkeyPatch) -> None:
    run_migrations()
    wid = _seed_watcher(kind="rss", url="https://feed.test/broken")

    def boom(_req):
        return httpx.Response(500, text="server error")

    _patch_httpx(monkeypatch, boom)
    s = get_settings()
    assert s.watcher_failure_cap == 3

    for _ in range(s.watcher_failure_cap - 1):
        w = get_watcher(wid)
        result = run_watcher(w)
        assert result.status == "error"
        assert get_watcher(wid)["active"] == 1, "still active before reaching cap"

    # Final failure crosses the cap → deactivate.
    w = get_watcher(wid)
    final = run_watcher(w)
    assert final.status == "deactivated"
    refreshed = get_watcher(wid)
    assert refreshed["active"] == 0
    assert refreshed["consecutive_failures"] == s.watcher_failure_cap
    assert refreshed["last_error"]


def test_successful_run_resets_failure_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    run_migrations()
    wid = _seed_watcher(kind="rss", url="https://feed.test/flaky")

    state = {"fail": True}

    def handler(_req):
        if state["fail"]:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text="""<?xml version="1.0"?>
            <rss version="2.0"><channel>
              <item><title>OK</title><link>https://feed.test/ok</link></item>
            </channel></rss>""")

    _patch_httpx(monkeypatch, handler)

    w = get_watcher(wid)
    run_watcher(w)
    assert get_watcher(wid)["consecutive_failures"] == 1

    state["fail"] = False
    w = get_watcher(wid)
    run_watcher(w)
    assert get_watcher(wid)["consecutive_failures"] == 0
    assert get_watcher(wid)["last_error"] is None


def test_extraction_deferred_when_daily_cap_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """html_static + no selector + LLM-spend-cap-tripped → defer extraction."""
    run_migrations()
    wid = _seed_watcher(kind="html_static", url="https://html.test/listings")
    _patch_httpx(monkeypatch, lambda req: httpx.Response(200, text="<html><body><p>x</p></body></html>"))

    monkeypatch.setattr("scout.sources.watcher_runner.extraction_allowed",
                        lambda: (False, 5.0, 2.0))

    w = get_watcher(wid)
    result = run_watcher(w)
    assert result.status == "deferred"
    refreshed = get_watcher(wid)
    assert refreshed["last_content_hash"], "hash still updates on a deferred run"
    assert refreshed["active"] == 1
    assert "deferred" in (refreshed["last_error"] or "")
