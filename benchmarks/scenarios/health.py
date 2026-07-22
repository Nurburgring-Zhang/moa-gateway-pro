"""Health endpoint benchmark scenario."""
from __future__ import annotations

import httpx


async def health_check(client: httpx.AsyncClient) -> None:
    """GET /health — lightweight health check."""
    resp = await client.get("/health")
    resp.raise_for_status()


async def health_detailed(client: httpx.AsyncClient) -> None:
    """GET /api/health/detailed — detailed health info."""
    resp = await client.get("/api/health/detailed")
    resp.raise_for_status()
