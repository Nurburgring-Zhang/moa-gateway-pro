"""Web search skill -- real implementation with graceful degradation.

Degradation chain: Tavily (requires TAVILY_API_KEY) -> DuckDuckGo (no key).
If BOTH real backends are unavailable, the tool reports an honest
"search unavailable" message — it never fabricates results (audit F11 fix).
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

    Degradation chain: Tavily -> DuckDuckGo -> honest failure. When both
    real backends are unavailable the function returns an explicit
    "unavailable" message and fabricates nothing (audit F11 policy).

    Args:
        query: The search query string.
        max_results: Maximum number of results to return.

    Returns:
        A formatted string of search results, or an explicit
        unavailability message when no backend is reachable.
    """
    logger.info("web_search: query=%r max_results=%d", query, max_results)

    results: list[dict[str, Any]] = []
    source = "none"

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
            # 3. BOTH real backends unavailable — report honestly. Fabricating
            #    example.com results would feed the agent false data (audit F11).
            logger.warning("DuckDuckGo search failed: %s — no real search backend available", e2)
            return (
                f"Web search unavailable for '{query}': no search backend could be "
                f"reached (Tavily: {e}; DuckDuckGo: {e2}). Set TAVILY_API_KEY or "
                f"install/verify duckduckgo-search to enable real web search. "
                f"No results were fabricated."
            )

    # Enforce max_results defensively (backends already slice, but never trust
    # an upstream to honour the limit).
    results = results[:max_results]

    # Format output
    lines = [f"Found {len(results)} results for '{query}' (via {source}):"]
    for i, r in enumerate(results, 1):
        lines.append(f"\n{i}. {r['title']}")
        lines.append(f"   URL: {r['url']}")
        lines.append(f"   {r['snippet']}")

    return "\n".join(lines)
