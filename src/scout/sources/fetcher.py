"""Fetcher abstraction. One function — `fetch(watcher) → FetchResult` —
dispatches internally by `watcher.kind`. Each path is independently testable
with httpx.MockTransport / monkeypatched feedparser / a stubbed Playwright.

Playwright is optional (extra `js`); if Chromium isn't installed, `html_js`
watchers raise `PlaywrightUnavailable` which the runner converts to a
recorded failure on that watcher. Other kinds keep working — disabling
Playwright must not crash the app (5b acceptance #4).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import httpx
from selectolax.parser import HTMLParser

from scout.sources.politeness import (
    USER_AGENT,
    allowed_by_robots,
    record_failure,
    wait_for_slot,
)


class PlaywrightUnavailable(RuntimeError):
    """Raised when an html_js watcher runs but Playwright isn't installed."""


class RobotsDisallowed(RuntimeError):
    """Raised when robots.txt forbids fetching the configured URL."""


@dataclass
class FetchResult:
    content_text: str
    content_html: str | None
    content_hash: str
    items: list[dict[str, Any]]  # for RSS: parsed entries; for others: empty
    fetched_at: str


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()


def _scope_html(html: str, selector: str | None) -> str:
    if not selector:
        return html
    tree = HTMLParser(html)
    nodes = tree.css(selector) or []
    return "\n".join(n.html or "" for n in nodes)


# ── HTTP / RSS / JSON ─────────────────────────────────────────────────────


def _fetch_static(url: str, *, headers: dict[str, str] | None = None) -> str:
    headers = {"User-Agent": USER_AGENT, **(headers or {})}
    with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as c:
        r = c.get(url)
    if r.status_code >= 400:
        record_failure(url, status_code=r.status_code)
        raise httpx.HTTPStatusError(f"{r.status_code} on {url}", request=r.request, response=r)
    return r.text


def _fetch_rss(url: str, *, headers: dict[str, str] | None = None) -> tuple[str, list[dict[str, Any]]]:
    import feedparser

    headers = {"User-Agent": USER_AGENT, **(headers or {})}
    with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as c:
        r = c.get(url)
    if r.status_code >= 400:
        record_failure(url, status_code=r.status_code)
        raise httpx.HTTPStatusError(f"{r.status_code} on {url}", request=r.request, response=r)
    feed = feedparser.parse(r.text)
    items: list[dict[str, Any]] = []
    for e in feed.entries:
        items.append({
            "title": getattr(e, "title", None),
            "url": getattr(e, "link", None),
            "guid": getattr(e, "id", None) or getattr(e, "link", None),
            "summary": getattr(e, "summary", None),
            "published": getattr(e, "published", None),
        })
    return r.text, items


def _fetch_json(url: str, *, headers: dict[str, str] | None = None) -> tuple[str, Any]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as c:
        r = c.get(url)
    if r.status_code >= 400:
        record_failure(url, status_code=r.status_code)
        raise httpx.HTTPStatusError(f"{r.status_code} on {url}", request=r.request, response=r)
    return r.text, r.json()


def _fetch_js(url: str, *, headers: dict[str, str] | None = None) -> str:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError as e:
        raise PlaywrightUnavailable(
            "playwright not installed; install the `js` extra (`uv pip install -e .[js]`) "
            "and run `playwright install chromium` to enable html_js watchers"
        ) from e

    extra_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # most commonly: chromium binary not installed
            raise PlaywrightUnavailable(f"chromium not available: {e}") from e
        context = browser.new_context(user_agent=extra_headers["User-Agent"])
        page = context.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=30_000)
            html = page.content()
        finally:
            context.close()
            browser.close()
    return html


# ── Public entry point ────────────────────────────────────────────────────


def fetch(watcher: dict[str, Any], *, now_iso: str) -> FetchResult:
    """Single dispatch surface used by `watcher_runner.run_watcher`.

    `watcher` is a sqlite3.Row-like dict with keys: url, kind, selector,
    headers_json. `now_iso` lets the caller stamp the result consistently."""
    url = watcher["url"]
    kind = watcher["kind"]
    selector = watcher.get("selector") if isinstance(watcher, dict) else watcher["selector"]
    headers_json = watcher.get("headers_json")
    headers = json.loads(headers_json) if headers_json else None

    if not allowed_by_robots(url):
        raise RobotsDisallowed(f"robots.txt disallows {USER_AGENT} on {url}")

    wait_for_slot(url)

    items: list[dict[str, Any]] = []
    raw_html: str | None = None

    if kind == "html_static":
        raw_html = _fetch_static(url, headers=headers)
        scoped = _scope_html(raw_html, selector)
        text = HTMLParser(scoped).text(separator="\n", strip=True) if scoped else ""
    elif kind == "html_js":
        raw_html = _fetch_js(url, headers=headers)
        scoped = _scope_html(raw_html, selector)
        text = HTMLParser(scoped).text(separator="\n", strip=True) if scoped else ""
    elif kind == "rss":
        raw_html, items = _fetch_rss(url, headers=headers)
        text = "\n\n".join(
            f"{i.get('title') or ''}\n{i.get('summary') or ''}\n{i.get('url') or ''}"
            for i in items
        )
    elif kind == "json_api":
        raw_html, payload = _fetch_json(url, headers=headers)
        # `selector` field is a JSONPath-ish dotted key for json_api (e.g. "data.items").
        if selector:
            cur: Any = payload
            for key in selector.split("."):
                if isinstance(cur, dict):
                    cur = cur.get(key)
                else:
                    cur = None
                    break
            scoped_payload = cur if cur is not None else payload
        else:
            scoped_payload = payload
        text = json.dumps(scoped_payload, ensure_ascii=False, indent=2)
        if isinstance(scoped_payload, list):
            items = [x for x in scoped_payload if isinstance(x, dict)]
    else:
        raise ValueError(f"unknown watcher kind: {kind!r}")

    return FetchResult(
        content_text=text,
        content_html=raw_html,
        content_hash=_hash(text),
        items=items,
        fetched_at=now_iso,
    )
