"""Web search skill — simulated implementation.

Replace the body of :func:`web_search` with a real search API call
(DuckDuckGo, SearXNG, Tavily, etc.) for production use.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return formatted results.

    This is a simulated implementation that returns mock results.
    In production, replace with a real search API integration.

    Args:
        query: The search query string.
        max_results: Maximum number of results to return.

    Returns:
        A formatted string of search results.
    """
    logger.info("web_search: query=%r max_results=%d", query, max_results)

    # --- Simulated results ---
    # Replace with real implementation, e.g.:
    #   import aiohttp
    #   async with aiohttp.ClientSession() as session:
    #       async with session.get(
    #           "https://api.duckduckgo.com/",
    #           params={"q": query, "format": "json"},
    #       ) as resp:
    #           data = await resp.json()
    #           ...

    mock_results: list[dict[str, Any]] = [
        {
            "title": f"Search result {i + 1} for: {query}",
            "url": f"https://example.com/result-{i + 1}",
            "snippet": (
                f"This is a simulated search result for the query '{query}'. "
                f"In a real implementation, this would contain an actual "
                f"web page snippet relevant to the query."
            ),
        }
        for i in range(min(max_results, 5))
    ]

    lines = [f"Found {len(mock_results)} results for '{query}':"]
    for i, r in enumerate(mock_results, 1):
        lines.append(f"\n{i}. {r['title']}")
        lines.append(f"   URL: {r['url']}")
        lines.append(f"   {r['snippet']}")

    return "\n".join(lines)
