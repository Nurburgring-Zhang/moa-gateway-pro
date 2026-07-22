"""Chat completions benchmark scenario."""
from __future__ import annotations

import httpx


async def chat_completions(client: httpx.AsyncClient) -> None:
    """POST /v1/chat/completions — main inference endpoint.

    Note: This requires a running LLM backend.
    In benchmark mode we measure gateway overhead (may get 502/timeout).
    """
    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 10,
        },
        headers={"Authorization": "Bearer demo-key-please-change"},
    )
    # Don't raise — backend may not be available
    _ = resp.status_code
