"""moa_gateway.providers.cohere_provider -- Cohere v2 API adapter

Cohere v2 uses /v2/chat endpoint, differs from OpenAI format:
- Auth: Bearer token
- Chat: POST /v2/chat
- Response message.content is an array [{type: text, text: ...}]
- usage nests billed_units / tokens
- finish_reason uses Cohere enum (COMPLETE/MAX_TOKENS/ERROR/TOOL_CALL)
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import ChatRequest, ChatResponse, Provider, ProviderError

logger = logging.getLogger(__name__)


class CohereProvider(Provider):
    """Cohere v2 API adapter.

    Key differences from OpenAI format:
    - Response content is an array of content blocks, not a plain string
    - usage structure nests billed_units / tokens
    - finish_reason enum values differ
    - Request param p replaces top_p (v2 also accepts top_p)
    """

    DEFAULT_BASE = "https://api.cohere.com/v2"

    def __init__(self, api_base: str = "", api_key: str = "", timeout: int = 120, **kwargs):
        if not api_base:
            api_base = self.DEFAULT_BASE
        super().__init__(api_base, api_key, timeout)

    async def chat(self, req: ChatRequest) -> ChatResponse:
        url = f"{self.api_base}/chat"

        payload: dict[str, Any] = {
            "model": req.model,
            "messages": req.messages,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }
        if req.top_p != 1.0:
            payload["p"] = req.top_p
        if req.stop:
            payload["stop_sequences"] = req.stop
        if req.tools:
            payload["tools"] = req.tools
        if req.tool_choice is not None:
            payload["tool_choice"] = req.tool_choice
        for k, v in req.extra.items():
            payload[k] = v

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        start = time.time()
        try:
            resp = await self.client.post(
                url, json=payload, headers=headers, timeout=httpx.Timeout(req.timeout)
            )
        except httpx.TimeoutException as e:
            raise ProviderError(f"timeout: {e}", status=408) from e
        except httpx.HTTPError as e:
            raise ProviderError(f"http error: {e}", status=502) from e

        if resp.status_code != 200:
            raise ProviderError(
                f"HTTP {resp.status_code}: {resp.text[:500]}", status=resp.status_code
            )

        data = resp.json()
        message = data.get("message") or {}

        # Cohere v2 content is an array of content blocks
        content_blocks = message.get("content") or []
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        for blk in content_blocks:
            if isinstance(blk, dict):
                if blk.get("type") == "text":
                    text_parts.append(blk.get("text", ""))
                elif blk.get("type") == "tool_use":
                    tool_calls.append(
                        {
                            "id": blk.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": blk.get("name", ""),
                                "arguments": json.dumps(
                                    blk.get("parameters", {}), ensure_ascii=False
                                ),
                            },
                        }
                    )
            elif isinstance(blk, str):
                text_parts.append(blk)

        # map Cohere finish_reason -> OpenAI finish_reason
        finish_reason = data.get("finish_reason", "COMPLETE")
        finish_map = {
            "COMPLETE": "stop",
            "MAX_TOKENS": "length",
            "ERROR": "error",
            "TOOL_CALL": "tool_calls",
        }
        finish_reason = finish_map.get(finish_reason, "stop")

        # Cohere usage: {billed_units: {...}, tokens: {...}}
        usage = data.get("usage") or {}
        billed = usage.get("billed_units") or {}
        tokens = usage.get("tokens") or {}
        prompt_tokens = billed.get("input_tokens") or tokens.get("input_tokens") or 0
        completion_tokens = billed.get("output_tokens") or tokens.get("output_tokens") or 0

        return ChatResponse(
            content="".join(text_parts),
            tool_calls=tool_calls or None,
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            model=data.get("model", req.model),
            provider="cohere",
            latency_ms=(time.time() - start) * 1000,
            cost=0.0,
            raw=data,
        )

    async def chat_stream(self, req: ChatRequest) -> AsyncIterator[str]:
        """Cohere v2 SSE streaming."""
        url = f"{self.api_base}/chat"
        payload: dict[str, Any] = {
            "model": req.model,
            "messages": req.messages,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
            "stream": True,
        }
        if req.top_p != 1.0:
            payload["p"] = req.top_p
        for k, v in req.extra.items():
            payload[k] = v

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            async with self.client.stream(
                "POST", url, json=payload, headers=headers, timeout=httpx.Timeout(req.timeout)
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise ProviderError(
                        f"Stream HTTP {resp.status_code}: {body[:300]!r}",
                        status=resp.status_code,
                    )
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[len("data:"):].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        d = json.loads(chunk)
                    except Exception:
                        continue
                    # Cohere v2 streaming event types
                    event_type = d.get("type", "")
                    if event_type == "content-delta":
                        delta = d.get("delta", {})
                        msg = delta.get("message", {})
                        content = msg.get("content", {})
                        if isinstance(content, dict):
                            if content.get("type") == "text":
                                yield content.get("text", "")
                        elif isinstance(content, str):
                            yield content
                    elif event_type == "text-delta":
                        # compat fallback
                        yield d.get("text", "")
        except httpx.TimeoutException as e:
            raise ProviderError(f"timeout: {e}", status=408) from e
        except httpx.HTTPError as e:
            raise ProviderError(f"http error: {e}", status=502) from e

    async def health_check(self) -> bool:
        """GET /v2/models to check API availability."""
        if not self.api_key:
            return False
        try:
            url = f"{self.api_base}/models"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            resp = await self.client.get(url, headers=headers, timeout=httpx.Timeout(8))
            return 200 <= resp.status_code < 400
        except Exception:
            return False
