"""Selector-based + LLM-based listing extraction."""
from __future__ import annotations

from scout.llm.client import FakeLLMClient, LLMResponse, LLMUsage
from scout.sources.extractor import extract_with_llm, extract_with_selector


def test_selector_extracts_one_item_per_top_level_node() -> None:
    html = """
      <article><h2><a href="/calls/a">Call A</a></h2><p>blurb A blurb A blurb A blurb A</p></article>
      <article><h2><a href="/calls/b">Call B</a></h2><p>blurb B blurb B blurb B blurb B</p></article>
    """
    items = extract_with_selector(html, base_url="https://x.test")
    titles = [i["title"] for i in items]
    assert "Call A" in titles
    assert "Call B" in titles
    assert all(i["url"].startswith("https://x.test/calls/") for i in items if i.get("url"))


def test_selector_dedups_repeated_url() -> None:
    html = """
      <li><a href="/c/1">Same</a> first</li>
      <li><a href="/c/1">Same</a> second</li>
    """
    items = extract_with_selector(html, base_url="https://x.test")
    urls = {i.get("url") for i in items if i.get("url")}
    assert urls == {"https://x.test/c/1"}


def test_llm_extractor_parses_array_and_absolutises_urls() -> None:
    fake = FakeLLMClient(responses={"listing_extractor": LLMResponse(
        text='[{"title": "Call A", "url": "/calls/a"}, {"title": "Call B", "url": "https://other.test/b"}]',
        usage=LLMUsage(model="fake", cost_usd=0.0),
        stop_reason="end_turn",
    )})
    items, cost = extract_with_llm("<html>…</html>", base_url="https://funder.test/listings", watcher_name="test", client=fake)
    urls = {i["url"] for i in items}
    assert urls == {"https://funder.test/calls/a", "https://other.test/b"}
    assert cost == 0.0


def test_llm_extractor_tolerates_fenced_json() -> None:
    fake = FakeLLMClient(responses={"listing_extractor": LLMResponse(
        text='```json\n[{"title": "X", "url": "https://x.test/x"}]\n```',
        usage=LLMUsage(model="fake"), stop_reason="end_turn",
    )})
    items, _ = extract_with_llm("html", base_url="https://x.test", watcher_name="t", client=fake)
    assert len(items) == 1
    assert items[0]["title"] == "X"


def test_llm_extractor_returns_empty_on_non_json_text() -> None:
    fake = FakeLLMClient(responses={"listing_extractor": LLMResponse(
        text="I cannot find any opportunities on this page.",
        usage=LLMUsage(model="fake"), stop_reason="end_turn",
    )})
    items, _ = extract_with_llm("html", base_url="https://x.test", watcher_name="t", client=fake)
    assert items == []
