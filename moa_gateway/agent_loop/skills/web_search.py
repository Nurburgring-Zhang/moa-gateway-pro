"""Web search skill -- real implementation with graceful fallback.

Degradation chain: Tavily (requires TAVILY_API_KEY) -> DuckDuckGo (no key) -> Mock.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


async def _search_tavily(query: str, max_results: int) -> list[dict[str, Any]]:
    """Search using Tavily API (requires TAVILY_API_KEY env var)."""
    import httpx

    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY not set")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": False,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
            for r in data.get("results", [])[:max_results]
        ]


async def _search_duckduckgo(query: str, max_results: int) -> list[dict[str, Any]]:
    """Search using DuckDuckGo (no API key required)."""
    from duckduckgo_search import DDGS

    def _sync_search() -> list[dict[str, Any]]:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            return [
                {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
                for r in results[:max_results]
            ]

    return await asyncio.get_event_loop().run_in_executor(None, _sync_search)


async def web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return formatted results.

    Degradation chain: Tavily -> DuckDuckGo -> Mock.

    Args:
        query: The search query string.
        max_results: Maximum number of results to return.

    Returns:
        A formatted string of search results.
    """
    logger.info("web_search: query=%r max_results=%d", query, max_results)

    results: list[dict[str, Any]] = []
    source = "mock"

    # 1. Try Tavily
    try:
        results = await _search_tavily(query, max_results)
        source = "tavily"
    except Exception as e:
        logger.warning("Tavily search failed: %s, falling back to DuckDuckGo", e)

        # 2. Try DuckDuckGo
        try:
            results = await _search_duckduckgo(query, max_results)
            source = "duckduckgo"
        except Exception as e2:
            logger.warning("DuckDuckGo search failed: %s, using mock results", e2)

            # 3. Final fallback: mock
            results = [
                {
                    "title": f"Search result {i + 1} for: {query}",
                    "url": f"https://example.com/result-{i + 1}",
                    "snippet": (
                        f"This is a simulated search result for '{query}'. "
                        f"Configure TAVILY_API_KEY or install duckduckgo-search for real results."
                    ),
                }
                for i in range(min(max_results, 5))
            ]

    # Format output
    lines = [f"Found {len(results)} results for '{query}' (via {source}):"]
    for i, r in enumerate(results, 1):
        lines.append(f"\n{i}. {r['title']}")
        lines.append(f"   URL: {r['url']}")
        lines.append(f"   {r['snippet']}")

    return "\n".join(lines)