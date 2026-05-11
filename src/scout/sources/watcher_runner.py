"""Run a single watcher end-to-end.

  fetch → hash-diff (cheap no-op when unchanged) → extract → dedup-within-watcher
  → raw_findings inserts → state update

Failures bump `consecutive_failures`. At `WATCHER_FAILURE_CAP` (default 3),
the watcher auto-deactivates and the dashboard shows the warning. The error
is logged verbatim so the user knows what to fix.

LLM extraction is gated by `cost_cap.extraction_allowed()`. When the daily
cap is hit the watcher still fetches and updates its hash, but extraction
is deferred — the watcher row's `last_error` records the deferral and a
follow-up run picks up the work the next day.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from scout.agents.normaliser import insert_raw_finding
from scout.config import get_settings
from scout.db import connection
from scout.logging import get_logger
from scout.models import utc_now_iso
from scout.queries_watchers import update_watcher_state
from scout.sources.cost_cap import extraction_allowed
from scout.sources.extractor import extract_with_llm, extract_with_selector
from scout.sources.fetcher import (
    PlaywrightUnavailable,
    RobotsDisallowed,
    fetch,
)

log = get_logger("scout.sources.runner")


@dataclass
class WatcherRunResult:
    watcher_id: int
    name: str
    status: str  # 'ok' | 'unchanged' | 'deferred' | 'error' | 'deactivated'
    findings_inserted: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    detail: str | None = None


def _url_hash(url: str | None) -> str:
    return hashlib.sha256((url or "").encode("utf-8")).hexdigest()


def _start_run(name: str) -> int:
    now = utc_now_iso()
    with connection() as conn:
        cur = conn.execute(
            "INSERT INTO runs (kind, started_at, status) VALUES (?, ?, 'running')",
            (f"watcher:{name}", now),
        )
        return int(cur.lastrowid)


def _finish_run(
    run_id: int,
    *,
    status: str,
    found: int = 0,
    added: int = 0,
    cost_usd: float = 0.0,
    error: str | None = None,
) -> None:
    with connection() as conn:
        conn.execute(
            "UPDATE runs SET finished_at=?, status=?, opportunities_found=?,"
            " opportunities_added=?, cost_usd=?, error=? WHERE id=?",
            (utc_now_iso(), status, found, added, cost_usd, error, run_id),
        )


def _record_failure(
    watcher: sqlite3.Row, *, error: str, run_id: int
) -> WatcherRunResult:
    s = get_settings()
    next_failures = (watcher["consecutive_failures"] or 0) + 1
    new_active = next_failures < s.watcher_failure_cap
    update_watcher_state(
        watcher["id"],
        last_checked_at=utc_now_iso(),
        consecutive_failures=next_failures,
        last_error=error[:500],
        active=new_active,
    )
    _finish_run(run_id, status="error", error=error[:500])
    log.warning(
        "watcher_failure",
        watcher=watcher["name"],
        consecutive=next_failures,
        deactivated=not new_active,
        error=error,
    )
    return WatcherRunResult(
        watcher_id=int(watcher["id"]),
        name=watcher["name"],
        status="deactivated" if not new_active else "error",
        error=error,
    )


def _dedup_listings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        u = it.get("url") or ""
        key = _url_hash(u) if u else f"no-url::{(it.get('title') or '')[:120]}"
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _serialise_listing(it: dict[str, Any]) -> str:
    """Render a listing item into the text body of a raw_finding."""
    lines = []
    if it.get("title"):
        lines.append(f"TITLE: {it['title']}")
    if it.get("url"):
        lines.append(f"URL: {it['url']}")
    if it.get("snippet"):
        lines.append("")
        lines.append(it["snippet"])
    if it.get("summary"):
        lines.append("")
        lines.append(it["summary"])
    return "\n".join(lines).strip() or json.dumps(it, ensure_ascii=False)


def run_watcher(watcher: sqlite3.Row, *, extractor_client=None) -> WatcherRunResult:
    """Execute one watcher pass. Always returns a result; never raises."""
    name = watcher["name"]
    run_id = _start_run(name)
    log.info("watcher_run_start", watcher=name, kind=watcher["kind"])

    # 1. Fetch.
    try:
        result = fetch(dict(watcher), now_iso=utc_now_iso())
    except PlaywrightUnavailable as e:
        return _record_failure(watcher, error=f"playwright_unavailable: {e}", run_id=run_id)
    except RobotsDisallowed as e:
        return _record_failure(watcher, error=f"robots_disallowed: {e}", run_id=run_id)
    except Exception as e:
        return _record_failure(watcher, error=f"fetch_failed: {type(e).__name__}: {e}", run_id=run_id)

    # 2. Cheap path: content unchanged.
    prev_hash = watcher["last_content_hash"]
    if prev_hash and prev_hash == result.content_hash:
        update_watcher_state(
            watcher["id"],
            last_checked_at=result.fetched_at,
            consecutive_failures=0,
            last_error=None,
            active=True,
        )
        _finish_run(run_id, status="success", found=0, added=0)
        log.info("watcher_unchanged", watcher=name, hash=result.content_hash[:12])
        return WatcherRunResult(
            watcher_id=int(watcher["id"]),
            name=name,
            status="unchanged",
        )

    # 3. Extract.
    cost = 0.0
    detail: str | None = None
    if result.items:
        # RSS / json_api already give us structured entries.
        listings = result.items
    elif watcher["selector"] and watcher["kind"] in ("html_static", "html_js"):
        listings = extract_with_selector(result.content_html or "", base_url=watcher["url"])
    elif watcher["kind"] in ("html_static", "html_js"):
        allowed, spent, cap = extraction_allowed()
        if not allowed:
            update_watcher_state(
                watcher["id"],
                last_checked_at=result.fetched_at,
                last_content_hash=result.content_hash,
                consecutive_failures=0,
                last_error=f"extraction_deferred: spent ${spent:.2f}/${cap:.2f} today",
                active=True,
            )
            _finish_run(run_id, status="success", found=0, added=0)
            log.info("watcher_extraction_deferred", watcher=name, spent=spent, cap=cap)
            return WatcherRunResult(
                watcher_id=int(watcher["id"]),
                name=name,
                status="deferred",
                detail=f"extraction_deferred: spent ${spent:.2f}/${cap:.2f} today",
            )
        listings, cost = extract_with_llm(
            result.content_html or "",
            base_url=watcher["url"],
            watcher_name=name,
            client=extractor_client,
        )
    else:
        # Unknown extraction path; treat content_text as a single big blob.
        listings = [{"title": name, "url": watcher["url"], "snippet": result.content_text[:240]}]

    # 4. Dedup within this watcher run.
    listings = _dedup_listings(listings)

    # 5. Insert raw_findings.
    inserted = 0
    for it in listings:
        raw_text = _serialise_listing(it)
        if not raw_text:
            continue
        insert_raw_finding(
            source=f"watcher:{name}",
            raw_text=raw_text,
            source_url=it.get("url"),
            source_meta={
                "watcher_id": int(watcher["id"]),
                "watcher_kind": watcher["kind"],
                "guid": it.get("guid"),
            },
        )
        inserted += 1

    # 6. Update state.
    update_watcher_state(
        watcher["id"],
        last_checked_at=result.fetched_at,
        last_content_hash=result.content_hash,
        consecutive_failures=0,
        last_error=None,
        active=True,
    )
    _finish_run(run_id, status="success", found=len(listings), added=inserted, cost_usd=cost)
    log.info(
        "watcher_run_done",
        watcher=name,
        items=len(listings),
        inserted=inserted,
        cost_usd=cost,
    )
    return WatcherRunResult(
        watcher_id=int(watcher["id"]),
        name=name,
        status="ok",
        findings_inserted=inserted,
        cost_usd=cost,
        detail=detail,
    )
