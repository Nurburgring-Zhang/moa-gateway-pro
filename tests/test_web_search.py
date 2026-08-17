"""Tests for web_search skill with Tavily/DuckDuckGo/Mock fallback."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.anyio
async def test_web_search_tavily_success():
    """Tavily API key exists and request succeeds -> use Tavily."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "results": [
            {"title": "Test Result", "url": "https://test.com", "content": "Test snippet"}
        ]
    }

    with patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"}):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            from moa_gateway.agent_loop.skills.web_search import web_search
            result = await web_search("test query", max_results=3)

            assert "via tavily" in result
            assert "Test Result" in result
            assert "https://test.com" in result


@pytest.mark.anyio
async def test_web_search_fallback_to_duckduckgo():
    """Tavily fails -> fallback to DuckDuckGo."""
    with patch.dict("os.environ", {}, clear=True):
        with patch("moa_gateway.agent_loop.skills.web_search._search_duckduckgo") as mock_ddg:
            mock_ddg.return_value = [
                {"title": "DDG Result", "url": "https://ddg.com", "snippet": "DDG snippet"}
            ]

            from moa_gateway.agent_loop.skills.web_search import web_search
            result = await web_search("test query")

            assert "via duckduckgo" in result
            assert "DDG Result" in result


@pytest.mark.anyio
async def test_web_search_no_backend_reports_unavailable():
    """Tavily AND DuckDuckGo both fail -> honest 'unavailable' (audit F11).

    No fabricated results may be produced; the tool must say so explicitly.
    """
    with patch.dict("os.environ", {}, clear=True):
        with patch("moa_gateway.agent_loop.skills.web_search._search_duckduckgo") as mock_ddg:
            mock_ddg.side_effect = Exception("DDG unavailable")

            from moa_gateway.agent_loop.skills.web_search import web_search
            result = await web_search("test query")

            assert "unavailable" in result.lower()
            assert "example.com" not in result  # no fabricated URLs
            assert "no results were fabricated" in result.lower()


@pytest.mark.anyio
async def test_web_search_respects_max_results():
    """Verify max_results is applied on a REAL backend result set."""
    with patch.dict("os.environ", {}, clear=True):
        with patch("moa_gateway.agent_loop.skills.web_search._search_duckduckgo") as mock_ddg:
            mock_ddg.return_value = [
                {"title": f"R{i}", "url": f"https://r{i}.com", "snippet": f"s{i}"}
                for i in range(5)
            ]

            from moa_gateway.agent_loop.skills.web_search import web_search
            result = await web_search("test", max_results=2)

            assert result.count("URL:") == 2


@pytest.mark.anyio
async def test_web_search_output_format():
    """Verify output format consistency on a REAL backend result set."""
    with patch.dict("os.environ", {}, clear=True):
        with patch("moa_gateway.agent_loop.skills.web_search._search_duckduckgo") as mock_ddg:
            mock_ddg.return_value = [
                {"title": "Async Python", "url": "https://docs.python.org", "snippet": "guide"}
            ]

            from moa_gateway.agent_loop.skills.web_search import web_search
            result = await web_search("python async")

            assert "Found" in result
            assert "results for" in result
            assert "URL:" in result
