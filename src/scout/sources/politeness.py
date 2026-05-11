"""Politeness layer: per-domain rate limit + robots.txt cache.

Every outbound fetch from a watcher passes through `politeness.allow(url)`
which (1) checks robots.txt for the host (cached for a day) and (2) sleeps
until at least `_MIN_GAP_SECONDS` have elapsed since the last fetch to the
same host. State is held in an in-memory dict; restart resets it. That's
fine — robots.txt re-fetch is cheap and rate-limit memory is per-process
anyway. The User-Agent is honest: identifies us as a single-user research
agent with a contact email so funders can block us by reputation rather
than waste effort on us.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from urllib import robotparser
from urllib.parse import urlparse

import httpx

from scout.logging import get_logger

log = get_logger("scout.sources.politeness")

USER_AGENT = "Scout/0.1 (single-user research agent; contact: alain.chiu@gmail.com)"
_MIN_GAP_SECONDS = 10.0
_ROBOTS_TTL_SECONDS = 24 * 3600


@dataclass
class _DomainState:
    last_fetched_at: float = 0.0
    robots_fetched_at: float = 0.0
    robots: robotparser.RobotFileParser | None = None
    backoff_until: float = 0.0


_state: dict[str, _DomainState] = {}
_lock = threading.Lock()


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def _fetch_robots(host: str, scheme: str) -> robotparser.RobotFileParser:
    rp = robotparser.RobotFileParser()
    url = f"{scheme}://{host}/robots.txt"
    try:
        with httpx.Client(timeout=10.0, headers={"User-Agent": USER_AGENT}) as c:
            r = c.get(url)
        if r.status_code == 200:
            rp.parse(r.text.splitlines())
        else:
            # No robots.txt → assume allow.
            rp.parse(["User-agent: *", "Allow: /"])
    except Exception as e:
        log.warning("robots_fetch_failed", host=host, error=repr(e))
        rp.parse(["User-agent: *", "Allow: /"])
    return rp


def allowed_by_robots(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if not host:
        return True
    with _lock:
        st = _state.setdefault(host, _DomainState())
        now = time.time()
        if st.robots is None or (now - st.robots_fetched_at) > _ROBOTS_TTL_SECONDS:
            st.robots = _fetch_robots(host, parsed.scheme or "https")
            st.robots_fetched_at = now
    return st.robots.can_fetch(USER_AGENT, url)


def wait_for_slot(url: str) -> None:
    """Block until it's been ≥ _MIN_GAP_SECONDS since the last fetch to this host.

    Also honours any backoff window set by record_failure (e.g. 429/403 response)."""
    host = _host(url)
    while True:
        with _lock:
            st = _state.setdefault(host, _DomainState())
            now = time.time()
            wait_for_rate = max(0.0, (st.last_fetched_at + _MIN_GAP_SECONDS) - now)
            wait_for_backoff = max(0.0, st.backoff_until - now)
            wait = max(wait_for_rate, wait_for_backoff)
            if wait <= 0:
                st.last_fetched_at = time.time()
                return
        time.sleep(wait)


def record_failure(url: str, *, status_code: int | None) -> None:
    """Apply exponential backoff for the host when we get 429/403."""
    if status_code not in (429, 403):
        return
    host = _host(url)
    with _lock:
        st = _state.setdefault(host, _DomainState())
        current = max(0.0, st.backoff_until - time.time())
        next_window = max(60.0, min(3600.0, (current + 60.0) * 2))
        st.backoff_until = time.time() + next_window
    log.warning("politeness_backoff", host=host, status=status_code, seconds=next_window)


def reset_for_tests() -> None:
    """Clear all per-host state. Tests use this in fixtures to keep isolation."""
    with _lock:
        _state.clear()
