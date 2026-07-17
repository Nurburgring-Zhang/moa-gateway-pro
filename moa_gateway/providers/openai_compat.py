"""moa_gateway.providers.openai_compat — OpenAI 兼容协议
覆盖:openai / deepseek / zhipu / moonshot / qwen / doubao / lingyi /
     baichuan / mistral / cohere / groq / openrouter / minimax 等
所有 OpenAI 兼容 /chat/completions 端点。
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


class OpenAICompatProvider(Provider):
    """OpenAI 兼容协议实现"""

    async def chat(self, req: ChatRequest) -> ChatResponse:
        url = f"{self.api_base}/chat/completions"
        payload: dict[str, Any] = {
            "model": req.model,
            "messages": req.messages,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }
        if req.top_p != 1.0:
            payload["top_p"] = req.top_p
        if req.stop:
            payload["stop"] = req.stop
        if req.tools:
            payload["tools"] = req.tools
        if req.tool_choice is not None:
            payload["tool_choice"] = req.tool_choice
        if req.stream:
            payload["stream"] = True
        for k, v in req.extra.items():
            payload[k] = v

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        start = time.time()
        try:
            if req.stream:
                # 流式
                content_parts: list[str] = []
                tool_calls_data: list[dict[str, Any]] = []
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
                        if chunk == "[DONE]":
                            break
                        try:
                            d = json.loads(chunk)
                        except Exception:
                            continue
                        if "choices" in d and d["choices"]:
                            ch = d["choices"][0]
                            delta = ch.get("delta", {}) or {}
                            if "content" in delta and delta["content"] is not None:
                                content_parts.append(delta["content"])
                            if "tool_calls" in delta and delta["tool_calls"]:
                                tool_calls_data.extend(delta["tool_calls"])
                content = "".join(content_parts)
                return ChatResponse(
                    content=content,
                    tool_calls=tool_calls_data or None,
                    finish_reason="stop",
                    model=req.model,
                    provider="openai-compat",
                    latency_ms=(time.time() - start) * 1000,
                    cost=0.0,  # 由 ModelPool 根据 endpoint 配置算
                    raw={"content_parts": len(content_parts)},
                )
            else:
                resp = await self.client.post(
                    url, json=payload, headers=headers,
                    timeout=httpx.Timeout(req.timeout)
                )
        except httpx.TimeoutException as e:
            raise ProviderError(f"timeout: {e}", status=408) from e
        except httpx.HTTPError as e:
            raise ProviderError(f"http error: {e}", status=502) from e

        if resp.status_code != 200:
            txt = resp.text[:500]
            raise ProviderError(f"HTTP {resp.status_code}: {txt}",
                                status=resp.status_code)
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = data.get("usage") or {}
        return ChatResponse(
            content=message.get("content", "") or "",
            tool_calls=message.get("tool_calls"),
            finish_reason=choice.get("finish_reason", "stop"),
            prompt_tokens=usage.get("prompt_tokens", 0) or 0,
            completion_tokens=usage.get("completion_tokens", 0) or 0,
            total_tokens=usage.get("total_tokens", 0) or 0,
            model=data.get("model", req.model),
            provider="openai-compat",
            latency_ms=(time.time() - start) * 1000,
            cost=0.0,
            raw=data,
        )

    async def chat_stream(self, req: ChatRequest) -> AsyncIterator[str]:
        """真实流式:逐 chunk 产出"""
        url = f"{self.api_base}/chat/completions"
        payload: dict[str, Any] = {
            "model": req.model,
            "messages": req.messages,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
            "stream": True,
        }
        if req.tools:
            payload["tools"] = req.tools
        if req.tool_choice is not None:
            payload["tool_choice"] = req.tool_choice
        if req.top_p != 1.0:
            payload["top_p"] = req.top_p
        if req.stop:
            payload["stop"] = req.stop
        for k, v in req.extra.items():
            payload[k] = v
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
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
                    if chunk == "[DONE]":
                        break
                    try:
                        d = json.loads(chunk)
                    except Exception:
                        continue
                    if d.get("choices"):
                        delta = (d["choices"][0] or {}).get("delta", {}) or {}
                        if delta.get("content"):
                            yield delta["content"]
        except httpx.TimeoutException as e:
            raise ProviderError(f"timeout: {e}", status=408) from e
        except httpx.HTTPError as e:
            raise ProviderError(f"http error: {e}", status=502) from e

    async def health_check(self) -> bool:
        """轻量 GET /models;401/403 视为 key 无效(返回 False 让 pool 自动 fallback mock)"""
        if not self.api_key:
            return False
        try:
            url = f"{self.api_base}/models"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            resp = await self.client.get(url, headers=headers,
                                         timeout=httpx.Timeout(8))
            # 401/403 = key 无效(不算"通")
            return 200 <= resp.status_code < 500 and resp.status_code not in (401, 403)
        except Exception:
            return False
