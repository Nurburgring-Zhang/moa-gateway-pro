"""Auth endpoint benchmark scenarios."""
from __future__ import annotations

import httpx


async def login_flow(client: httpx.AsyncClient) -> None:
    """POST /api/auth/login — login attempt (may fail with 401, that's OK)."""
    resp = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "BenchmarkPass#2024!"},
    )
    # We don't raise_for_status here — login may fail due to wrong password
    # We just measure latency of the auth pipeline
    _ = resp.status_code
