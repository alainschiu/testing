"""Fetcher dispatch by kind. Each path is mocked at the HTTP layer (or
monkeypatched for Playwright/feedparser) so no real network calls happen."""
from __future__ import annotations

import json
import sys

import httpx
import pytest

from scout.sources import fetcher as fetcher_mod
from scout.sources.fetcher import PlaywrightUnavailable, RobotsDisallowed, fetch
from scout.sources.politeness import reset_for_tests


@pytest.fixture(autouse=True)
def _reset_politeness():
    reset_for_tests()
    yield
    reset_for_tests()


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def _watcher(**kw) -> dict:
    base = {
        "id": 1,
        "name": "test",
        "url": "https://example.org/listings",
        "kind": "html_static",
        "selector": None,
        "headers_json": None,
    }
    base.update(kw)
    return base


def _patch_httpx(monkeypatch, handler):
    real_client_cls = httpx.Client

    def _client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client_cls(*args, **kwargs)

    monkeypatch.setattr(fetcher_mod.httpx, "Client", _client)


def test_html_static_extracts_text_via_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    html = """<html><body>
      <ul class="calls">
        <li class="call"><a href="/c/1">Call One</a></li>
        <li class="call"><a href="/c/2">Call Two</a></li>
      </ul></body></html>"""

    def handler(req):
        return httpx.Response(200, text=html)

    _patch_httpx(monkeypatch, handler)
    result = fetch(_watcher(selector=".call"), now_iso="2026-05-11T00:00:00Z")
    assert "Call One" in result.content_text
    assert "Call Two" in result.content_text
    assert result.content_hash, "hash must be non-empty"
    assert result.items == []  # selector path returns text; extraction is in extractor.py


def test_html_static_without_selector_returns_full_text(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "<html><body><h1>Hello</h1><p>world</p></body></html>"
    _patch_httpx(monkeypatch, lambda req: httpx.Response(200, text=html))
    result = fetch(_watcher(), now_iso="2026-05-11T00:00:00Z")
    assert "Hello" in result.content_text
    assert "world" in result.content_text


def test_rss_parses_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    feed_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <title>e-flux test</title>
      <item><title>Residency A</title><link>https://e.flux/a</link>
            <guid>https://e.flux/a</guid><description>desc a</description></item>
      <item><title>Grant B</title><link>https://e.flux/b</link>
            <guid>https://e.flux/b</guid><description>desc b</description></item>
    </channel></rss>"""
    _patch_httpx(monkeypatch, lambda req: httpx.Response(200, text=feed_xml,
                                                         headers={"content-type": "application/xml"}))
    result = fetch(_watcher(kind="rss", url="https://e.flux/feed"), now_iso="2026-05-11T00:00:00Z")
    assert len(result.items) == 2
    assert result.items[0]["title"] == "Residency A"
    assert result.items[0]["url"] == "https://e.flux/a"
    assert result.items[1]["guid"] == "https://e.flux/b"


def test_json_api_applies_dotted_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"meta": {"x": 1}, "data": {"items": [
        {"title": "P1", "url": "https://api.test/p1"},
        {"title": "P2", "url": "https://api.test/p2"},
    ]}})
    _patch_httpx(monkeypatch, lambda req: httpx.Response(200, text=body,
                                                         headers={"content-type": "application/json"}))
    result = fetch(_watcher(kind="json_api", selector="data.items"), now_iso="2026-05-11T00:00:00Z")
    assert len(result.items) == 2
    assert result.items[0]["title"] == "P1"


def test_html_js_raises_playwright_unavailable_when_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure import("playwright.sync_api") fails for this test.
    monkeypatch.setitem(sys.modules, "playwright", None)
    with pytest.raises(PlaywrightUnavailable):
        fetch(_watcher(kind="html_js"), now_iso="2026-05-11T00:00:00Z")


def test_robots_disallowed_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetcher_mod, "allowed_by_robots", lambda _url: False)
    with pytest.raises(RobotsDisallowed):
        fetch(_watcher(), now_iso="2026-05-11T00:00:00Z")


def test_http_4xx_raises_status_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_httpx(monkeypatch, lambda req: httpx.Response(404, text="nope"))
    with pytest.raises(httpx.HTTPStatusError):
        fetch(_watcher(), now_iso="2026-05-11T00:00:00Z")


def test_content_hash_is_stable_for_same_input(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "<html><body><p>stable</p></body></html>"
    _patch_httpx(monkeypatch, lambda req: httpx.Response(200, text=html))
    r1 = fetch(_watcher(), now_iso="2026-05-11T00:00:00Z")
    r2 = fetch(_watcher(), now_iso="2026-05-11T01:00:00Z")
    assert r1.content_hash == r2.content_hash, "identical content must hash identically"
