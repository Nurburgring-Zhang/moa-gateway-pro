"""moa_gateway.providers.anthropic_provider — Anthropic Claude 实现
使用 Anthropic 的 /v1/messages 端点,与 OpenAI 协议不同。
"""
from __future__ import annotations
import time
import logging
from typing import Dict, List, Any, Optional, AsyncIterator
import httpx
import json

from .base import Provider, ChatRequest, ChatResponse, ProviderError

logger = logging.getLogger(__name__)


class AnthropicProvider(Provider):
    """Anthropic Claude 专用实现"""

    def __init__(self, api_base: str, api_key: str, timeout: int = 120,
                 client: Optional[httpx.AsyncClient] = None):
        super().__init__(api_base, api_key, timeout, client)
        # Anthropic 的 base url 是 https://api.anthropic.com,API 在 /v1/messages
        if not self.api_base.endswith("/v1"):
            self.api_base = self.api_base.rstrip("/")

    async def chat(self, req: ChatRequest) -> ChatResponse:
        # 拆 system
        system = ""
        user_messages: List[Dict[str, Any]] = []
        for m in req.messages:
            if m.get("role") == "system":
                system += (m.get("content") or "") + "\n"
            else:
                user_messages.append(m)

        payload: Dict[str, Any] = {
            "model": req.model,
            "messages": user_messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }
        if system.strip():
            payload["system"] = system.strip()
        if req.top_p != 1.0:
            payload["top_p"] = req.top_p
        if req.stop:
            payload["stop_sequences"] = req.stop
        if req.tools:
            # 转换 OpenAI 风格 tools -> Anthropic 风格
            payload["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
                }
                for t in req.tools if "function" in t
            ]
        for k, v in req.extra.items():
            payload[k] = v

        url = f"{self.api_base}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        start = time.time()
        try:
            resp = await self.client.post(
                url, json=payload, headers=headers,
                timeout=httpx.Timeout(req.timeout)
            )
        except httpx.TimeoutException as e:
            raise ProviderError(f"timeout: {e}", status=408)
        except httpx.HTTPError as e:
            raise ProviderError(f"http error: {e}", status=502)

        if resp.status_code != 200:
            raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:500]}",
                                status=resp.status_code)
        data = resp.json()
        content_blocks = data.get("content") or []
        text_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        for blk in content_blocks:
            if blk.get("type") == "text":
                text_parts.append(blk.get("text", ""))
            elif blk.get("type") == "tool_use":
                tool_calls.append({
                    "id": blk.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": blk.get("name", ""),
                        "arguments": json.dumps(blk.get("input", {}), ensure_ascii=False)
                    }
                })

        usage = data.get("usage") or {}
        return ChatResponse(
            content="".join(text_parts),
            tool_calls=tool_calls or None,
            finish_reason=data.get("stop_reason", "stop") or "stop",
            prompt_tokens=usage.get("input_tokens", 0) or 0,
            completion_tokens=usage.get("output_tokens", 0) or 0,
            total_tokens=(usage.get("input_tokens", 0) or 0) + (usage.get("output_tokens", 0) or 0),
            model=data.get("model", req.model),
            provider="anthropic",
            latency_ms=(time.time() - start) * 1000,
            cost=0.0,
            raw=data,
        )

    async def chat_stream(self, req: ChatRequest) -> AsyncIterator[str]:
        """Anthropic SSE 流式"""
        system = ""
        user_messages: List[Dict[str, Any]] = []
        for m in req.messages:
            if m.get("role") == "system":
                system += (m.get("content") or "") + "\n"
            else:
                user_messages.append(m)

        payload: Dict[str, Any] = {
            "model": req.model,
            "messages": user_messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "stream": True,
        }
        if system.strip():
            payload["system"] = system.strip()

        url = f"{self.api_base}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        try:
            async with self.client.stream(
                "POST", url, json=payload, headers=headers,
                timeout=httpx.Timeout(req.timeout)
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise ProviderError(
                        f"Stream HTTP {resp.status_code}: {body[:300]!r}",
                        status=resp.status_code
                    )
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[len("data:"):].strip()
                    try:
                        d = json.loads(chunk)
                    except Exception:
                        continue
                    if d.get("type") == "content_block_delta":
                        delta = d.get("delta", {}) or {}
                        if delta.get("type") == "text_delta":
                            yield delta.get("text", "")
        except httpx.TimeoutException as e:
            raise ProviderError(f"timeout: {e}", status=408)
        except httpx.HTTPError as e:
            raise ProviderError(f"http error: {e}", status=502)

    async def health_check(self) -> bool:
        if not self.api_key:
            return False
        try:
            # 极小的 max_tokens 请求(不会真计费太多)
            url = f"{self.api_base}/v1/messages"
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "claude-3-5-haiku-20241022",
                "max_tokens": 4,
                "messages": [{"role": "user", "content": "ping"}]
            }
            resp = await self.client.post(url, json=payload, headers=headers,
                                         timeout=httpx.Timeout(8))
            return resp.status_code < 500
        except Exception:
            return False
