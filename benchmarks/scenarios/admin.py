"""Admin endpoints benchmark scenarios."""
from __future__ import annotations

import httpx


async def admin_stats(client: httpx.AsyncClient) -> None:
    """GET /api/admin/stats — admin dashboard stats (requires JWT)."""
    # This will likely return 401 without valid JWT, but we measure latency
    resp = await client.get("/api/admin/stats")
    _ = resp.status_code
