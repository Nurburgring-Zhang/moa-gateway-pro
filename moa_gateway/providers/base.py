"""moa_gateway.providers.base — Provider 抽象基类"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """Provider 调用错误"""

    def __init__(self, message: str, status: int = 0, provider: str = ""):
        super().__init__(message)
        self.status = status
        self.provider = provider


@dataclass
class ChatRequest:
    """统一的 chat 请求"""

    model: str
    messages: list[dict[str, Any]]
    temperature: float = 0.6
    max_tokens: int = 4096
    top_p: float = 1.0
    stop: list[str] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any | None = None
    stream: bool = False
    timeout: int = 120
    # 一些 provider 特定的额外字段
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResponse:
    """统一的 chat 响应"""

    content: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    finish_reason: str = "stop"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    provider: str = ""
    latency_ms: float = 0.0
    cost: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class Provider(ABC):
    """所有 provider 必须实现的抽象接口"""

    def __init__(
        self,
        api_base: str,
        api_key: str,
        timeout: int = 120,
        client: httpx.AsyncClient | None = None,
    ):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._owned_client = client is None
        self._client = client

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        if self._owned_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @abstractmethod
    async def chat(self, req: ChatRequest) -> ChatResponse:
        """非流式 chat"""
        raise NotImplementedError

    async def chat_stream(self, req: ChatRequest) -> AsyncIterator[str]:
        """流式 chat(子类按需 override)"""
        # 默认 fallback: 调用 chat 然后 yield 全部
        r = await self.chat(req)
        yield r.content

    async def health_check(self) -> bool:
        """轻量级健康检查:子类可重写。默认:有 key 就算 OK"""
        return bool(self.api_key)

    @staticmethod
    def estimate_cost(usage: dict[str, int], cost_in: float, cost_out: float) -> float:
        """根据 token 用量估算成本"""
        pt = usage.get("prompt_tokens", 0) or 0
        ct = usage.get("completion_tokens", 0) or 0
        return (pt / 1000.0) * cost_in + (ct / 1000.0) * cost_out
