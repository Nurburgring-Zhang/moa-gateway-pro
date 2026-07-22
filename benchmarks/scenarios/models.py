"""Models endpoint benchmark scenario."""
from __future__ import annotations

import httpx


async def list_models(client: httpx.AsyncClient) -> None:
    """GET /v1/models — list available models (requires API key)."""
    resp = await client.get(
        "/v1/models",
        headers={"Authorization": "Bearer demo-key-please-change"},
    )
    resp.raise_for_status()


async def list_models_no_auth(client: httpx.AsyncClient) -> None:
    """GET /v1/models — without auth (should still return preset models)."""
    resp = await client.get("/v1/models")
    resp.raise_for_status()
