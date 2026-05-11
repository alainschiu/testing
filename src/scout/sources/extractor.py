"""Extract per-listing items from fetched content.

Two paths, picked per-watcher:
- **selector**: the watcher has a CSS selector targeting a repeating element
  (e.g. `.opportunity-card`). Each match becomes one listing with its inner
  text and the first absolute-URL anchor we find inside.
- **LLM fallback**: no selector → we hand the scoped HTML to the Sonnet bot
  with `prompts/listing_extractor.md` and parse a JSON array out of the
  response. This is the bootstrapping path; once we know a site, the user
  (or a follow-up task) writes a selector and the LLM call disappears.

`extract` is structure-agnostic: it returns a list of `{title, url, snippet}`
dicts. The runner turns each into one `raw_findings` row.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from scout.llm.client import LLMClient
from scout.llm.factory import build_normaliser_client
from scout.llm.prompts import load_prompt
from scout.logging import get_logger

log = get_logger("scout.sources.extractor")

_JSON_FENCE_RX = re.compile(r"^```(?:json)?\s*|\s*```\s*$", re.IGNORECASE | re.MULTILINE)
_JSON_ARRAY_RX = re.compile(r"\[.*\]", re.DOTALL)
_MAX_LLM_HTML_CHARS = 60_000  # ~15K tokens; cap so we don't burn money on huge pages


def extract_with_selector(scoped_html: str, *, base_url: str) -> list[dict[str, Any]]:
    """Each `<a>` with an href becomes one listing; dedup by absolute URL.

    The selector path's contract is "the user pointed at a repeating element
    that contains one opportunity per match." A robust heuristic given that
    contract: for each anchor inside the scoped fragment, walk up the DOM
    until we find a container with ≥ 30 chars of text, take that as the
    snippet, and use the anchor text as the title. Brittle DOM parsing
    (only direct body children, etc.) breaks on real funder sites whose
    listing markup varies; anchor-driven extraction degrades more gracefully.
    """
    parser = HTMLParser(scoped_html)
    items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for link in parser.css("a[href]"):
        href = (link.attributes.get("href") or "").strip()
        if not href or href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
            continue
        url = urljoin(base_url, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        # Walk up to find the smallest enclosing container with substantive text.
        container = link.parent
        hops = 0
        while container is not None and hops < 4:
            t = container.text(separator=" ", strip=True)
            if len(t) >= 30:
                break
            container = container.parent
            hops += 1
        snippet = container.text(separator=" ", strip=True) if container else ""
        title = link.text(strip=True) or (snippet[:160] if snippet else "(no title)")
        items.append({"title": title, "url": url, "snippet": snippet[:240]})
    return items


def extract_with_llm(
    scoped_html: str,
    *,
    base_url: str,
    watcher_name: str,
    client: LLMClient | None = None,
) -> tuple[list[dict[str, Any]], float]:
    """LLM-driven listing extraction. Returns (items, cost_usd).

    Caller is responsible for checking the daily extraction cost cap *before*
    invoking this. We don't gate inside because the runner needs to know
    whether the call would be skipped vs. attempted vs. failed."""
    payload = scoped_html[:_MAX_LLM_HTML_CHARS]
    if len(scoped_html) > _MAX_LLM_HTML_CHARS:
        log.info(
            "listing_extractor_truncated",
            watcher=watcher_name,
            full_chars=len(scoped_html),
            sent_chars=_MAX_LLM_HTML_CHARS,
        )

    system = load_prompt("listing_extractor.md")
    user = (
        f"Base URL: {base_url}\n"
        f"Watcher: {watcher_name}\n\n"
        f"--- BEGIN HTML ---\n{payload}\n--- END HTML ---"
    )
    c = client or build_normaliser_client()
    try:
        resp = c.create_message(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=6_000,
            prompt_template="listing_extractor",
            run_id=None,
        )
    except Exception as e:
        log.warning("listing_extractor_call_failed", watcher=watcher_name, error=repr(e))
        return [], 0.0

    cost = resp.usage.cost_usd if resp.usage else 0.0
    items = _parse_json_array(resp.text or "")
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        url = it.get("url")
        if url and not _is_absolute(url):
            url = urljoin(base_url, url)
            it["url"] = url
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        if it.get("title"):
            cleaned.append(it)
    log.info("listing_extractor_done", watcher=watcher_name, items=len(cleaned), cost_usd=cost)
    return cleaned, cost


def _parse_json_array(text: str) -> list[Any]:
    s = _JSON_FENCE_RX.sub("", text.strip()).strip()
    try:
        result = json.loads(s)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        pass
    m = _JSON_ARRAY_RX.search(s)
    if not m:
        return []
    try:
        result = json.loads(m.group(0))
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        return []


def _is_absolute(url: str) -> bool:
    return bool(urlparse(url).netloc)
