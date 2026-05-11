"""WebSearch interface. The scout agent uses Anthropic's server-side web_search tool
inside the agent loop; this module is for non-agent fallback searches (Tavily).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""


class WebSearch(ABC):
    @abstractmethod
    def search(self, query: str, *, max_results: int = 10) -> list[SearchHit]: ...


class TavilySearch(WebSearch):
    def __init__(self, api_key: str) -> None:
        from tavily import TavilyClient

        self._client = TavilyClient(api_key=api_key)

    def search(self, query: str, *, max_results: int = 10) -> list[SearchHit]:
        result = self._client.search(query=query, max_results=max_results)
        out: list[SearchHit] = []
        for r in result.get("results", []):
            out.append(SearchHit(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("content", "")))
        return out


class FakeWebSearch(WebSearch):
    def __init__(self, hits: list[SearchHit] | None = None) -> None:
        self._hits = hits or []
        self.queries: list[str] = []

    def search(self, query: str, *, max_results: int = 10) -> list[SearchHit]:
        self.queries.append(query)
        return self._hits[:max_results]


# The agent-loop web search lives inside the Anthropic API as a server-side tool.
# Surface the current tool descriptor here so the scout agent stays version-aware.
ANTHROPIC_WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
}
