"""三通道 fallback 抽象 (R-23)

提供 SUBAGENT / CLI / API 三通道顺序 fallback chain:
- CH1 = SUBAGENT: 本地 subagent,先尝试 — 快速便宜
- CH2 = CLI: CLI 工具 fallback (例如 codex CLI),第二尝试
- CH3 = API: 远程 API,最终 fallback,最贵

Chain 行为: 顺序尝试 CH1→CH2→CH3,第一个成功就返回,都失败抛 ``ChannelError``。

错误分类 (R-24): 4 类 — auth / timeout / cli / empty。

v3.1.1 审计整改: 三通道全部真实执行,不再有 sleep+模板字符串模拟:
- ``SubagentChannel`` 经内部鉴权头回环调用本网关 ``/v1/chat/completions``
  (与 yaml_workflow 的 D2 回环模式一致),persona 作为 system prompt。
- ``CLIChannel`` 经 ``cli_registry`` 真实 ``subprocess.run`` 执行已注册外部
  CLI 工具 (或内联 argv 模板),捕获 stdout/stderr/exit code/耗时。
- ``APIChannel`` 经 ``model_pool.call`` 真实调用 LLM 端点 (pool 可注入以便
  测试;生产路径直连真实 provider)。
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from enum import Enum
from typing import Any, NamedTuple

__all__ = [
    "ChannelType",
    "ChannelResult",
    "ChannelError",
    "CLIErrorKind",
    "Channel",
    "SubagentChannel",
    "CLIChannel",
    "APIChannel",
    "ChannelChain",
    "classify_error",
]


# ============ Enums & Types ============


class ChannelType(str, Enum):
    """通道类型 — 按优先级排序 (SUBAGENT > CLI > API)"""

    SUBAGENT = "ch1"
    CLI = "ch2"
    API = "ch3"

    @property
    def label(self) -> str:
        return {
            ChannelType.SUBAGENT: "subagent",
            ChannelType.CLI: "cli",
            ChannelType.API: "api",
        }[self]


class CLIErrorKind(str, Enum):
    """CLI 错误分类 (R-24) — 4 类"""

    AUTH = "auth"
    TIMEOUT = "timeout"
    CLI = "cli"
    EMPTY = "empty"


class ChannelResult(NamedTuple):
    """单次通道执行结果"""

    channel: ChannelType
    success: bool
    output: str
    latency_ms: int
    error: str | None = None


class ChannelError(RuntimeError):
    """所有通道均失败时抛出"""

    def __init__(self, message: str, attempts: Sequence[ChannelResult]) -> None:
        super().__init__(message)
        self.attempts: list[ChannelResult] = list(attempts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": str(self),
            "attempts": [
                {
                    "channel": r.channel.value,
                    "success": r.success,
                    "output": r.output,
                    "latency_ms": r.latency_ms,
                    "error": r.error,
                }
                for r in self.attempts
            ],
        }


# ============ Error Classification (R-24) ============


def classify_error(exc: BaseException) -> str:
    """根据异常类型与消息,把异常归类为 R-24 四种之一。

    分类:
    - ``auth``:    鉴权失败 (PermissionError, 401/403, "auth"/"unauthorized"/"forbidden")
    - ``timeout``: 超时 (TimeoutError, asyncio.TimeoutError, "timeout")
    - ``empty``:   空响应 (ValueError 含 "empty", "no result", "no output")
    - ``cli``:     其它 CLI 错误 (兜底)
    """
    if exc is None:
        return CLIErrorKind.CLI.value

    name = type(exc).__name__.lower()
    msg = str(exc).lower()

    # 1) auth — 鉴权类
    if isinstance(exc, PermissionError):
        return CLIErrorKind.AUTH.value
    if any(k in name for k in ("auth", "permission")):
        return CLIErrorKind.AUTH.value
    if any(k in msg for k in ("unauthorized", "forbidden", "auth", "permission", "401", "403")):
        return CLIErrorKind.AUTH.value

    # 2) timeout — 超时
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return CLIErrorKind.TIMEOUT.value
    if "timeout" in name or "timeout" in msg or "timed out" in msg:
        return CLIErrorKind.TIMEOUT.value

    # 3) empty — 空响应
    if isinstance(exc, ValueError):
        if any(k in msg for k in ("empty", "no result", "no output", "blank")):
            return CLIErrorKind.EMPTY.value
    if any(k in msg for k in ("empty response", "no result", "no output", "blank output")):
        return CLIErrorKind.EMPTY.value

    # 4) cli — 兜底
    return CLIErrorKind.CLI.value


def _error_str(kind: str, exc: BaseException) -> str:
    """统一错误串格式: ``<kind>:<异常类型>:<消息>``。"""
    return f"{kind}:{type(exc).__name__}:{exc}"


# ============ Channel ABC ============


class Channel(ABC):
    """通道抽象基类"""

    def __init__(
        self,
        channel_type: ChannelType,
        *,
        enabled: bool = True,
        name: str | None = None,
    ) -> None:
        self.channel_type = channel_type
        self.enabled = enabled
        self.name = name or channel_type.label

    @abstractmethod
    async def execute(self, query: str, **kwargs: Any) -> ChannelResult:
        """执行一次通道调用,返回 :class:`ChannelResult`。

        实现方必须捕获自身异常,转译为 ``success=False`` 的结果
        (而不是让异常逃逸) — 由 chain 决定是否走 fallback。
        """
        raise NotImplementedError

    def _make_result(
        self,
        output: str,
        *,
        success: bool = True,
        latency_ms: int = 0,
        error: str | None = None,
    ) -> ChannelResult:
        return ChannelResult(
            channel=self.channel_type,
            success=success,
            output=output,
            latency_ms=latency_ms,
            error=error,
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(type={self.channel_type.value}, enabled={self.enabled})"


# ============ CH1 — SubagentChannel (真实网关回环) ============


class SubagentChannel(Channel):
    """CH1: 本地 subagent — 真实回环调用本网关 ``/v1/chat/completions``。

    与 ``yaml_workflow._http_post`` 的 D2 模式一致:内部回环请求携带网关
    ``Authorization`` 头 (``internal_auth_headers``),persona 参数作为
    system prompt 注入。

    可注入项 (测试用):
    - ``client``: 现成的 ``httpx.AsyncClient`` (例如挂 ASGITransport 直连
      测试 app);生产路径不传,自建真实 HTTP 客户端。
    - ``base_url``: 覆盖回环地址 (默认 ``internal_gateway_url()``)。
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        name: str | None = None,
        client: Any | None = None,
        base_url: str | None = None,
        model: str = "auto",
        timeout_s: float = 120.0,
    ) -> None:
        super().__init__(ChannelType.SUBAGENT, enabled=enabled, name=name)
        self._client = client
        self.base_url = base_url
        self.model = model
        self.timeout_s = timeout_s

    async def execute(self, query: str, **kwargs: Any) -> ChannelResult:
        import httpx

        from ..internal_callback import internal_auth_headers, internal_gateway_url

        start = time.perf_counter()
        try:
            persona = str(kwargs.get("persona") or "").strip()
            model = str(kwargs.get("model") or self.model)
            messages: list[dict[str, str]] = []
            if persona:
                messages.append({"role": "system", "content": persona})
            messages.append({"role": "user", "content": query or ""})
            base = (self.base_url or internal_gateway_url()).rstrip("/")
            url = f"{base}/v1/chat/completions"
            body = {"model": model, "messages": messages, "stream": False}
            headers = internal_auth_headers()

            if self._client is not None:
                resp = await self._client.post(url, json=body, headers=headers)
            else:
                async with httpx.AsyncClient(
                    timeout=self.timeout_s, trust_env=False
                ) as client:
                    resp = await client.post(url, json=body, headers=headers)

            latency = int((time.perf_counter() - start) * 1000)
            if resp.status_code in (401, 403):
                exc = PermissionError(
                    f"unauthorized: gateway loopback returned {resp.status_code}"
                )
                return self._make_result(
                    "", success=False, latency_ms=latency, error=_error_str(classify_error(exc), exc)
                )
            if resp.status_code != 200:
                exc = RuntimeError(
                    f"gateway loopback HTTP {resp.status_code}: {resp.text[:200]}"
                )
                return self._make_result(
                    "", success=False, latency_ms=latency, error=_error_str(classify_error(exc), exc)
                )
            data = resp.json()
            content = ""
            try:
                content = data["choices"][0]["message"].get("content") or ""
            except (KeyError, IndexError, TypeError, AttributeError):
                content = ""
            if not content.strip():
                exc = ValueError("empty response from subagent loopback")
                return self._make_result(
                    "", success=False, latency_ms=latency, error=_error_str(classify_error(exc), exc)
                )
            return self._make_result(content, success=True, latency_ms=latency)
        except Exception as exc:
            latency = int((time.perf_counter() - start) * 1000)
            return self._make_result(
                "", success=False, latency_ms=latency, error=_error_str(classify_error(exc), exc)
            )


# ============ CH2 — CLIChannel (真实 subprocess) ============


class CLIChannel(Channel):
    """CH2: CLI fallback — 真实 ``subprocess.run`` 执行外部 CLI 工具。

    两种配置方式 (二选一):
    - ``tool="name"``: 执行 ``cli_registry`` 里已注册的工具 (admin 注册,
      白名单可执行文件 + argv 模板 + 沙箱 cwd + 超时/输出上限)。
    - ``argv=[...]``: 内联 argv 模板,走与注册工具同等的白名单/沙箱校验。

    占位符: 模板里的 ``{key}`` 由 ``params`` + ``{"query": <本次 query>}``
    替换;替换结果保持单个 argv 元素,绝不经过 shell。

    都不配置时,execute 返回 ``success=False`` (error 归类 cli) — chain 会
    继续 fallback,而不是假装成功。
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        name: str | None = None,
        tool: str | None = None,
        argv: list[str] | None = None,
        params: dict[str, Any] | None = None,
        timeout_s: float | None = None,
        registry: Any | None = None,
    ) -> None:
        super().__init__(ChannelType.CLI, enabled=enabled, name=name)
        if tool and argv:
            raise ValueError("CLIChannel accepts either tool= or argv=, not both")
        self.tool = tool
        self.argv = list(argv) if argv else None
        self.static_params = dict(params or {})
        self.timeout_s = timeout_s
        self._registry = registry

    @property
    def registry(self):
        if self._registry is None:
            from .cli_registry import get_cli_registry

            self._registry = get_cli_registry()
        return self._registry

    async def execute(self, query: str, **kwargs: Any) -> ChannelResult:
        start = time.perf_counter()
        try:
            params: dict[str, Any] = {
                **self.static_params,
                **(kwargs.get("params") or {}),
                "query": query or "",
            }
            if self.tool:
                res = await self.registry.aexecute(
                    self.tool, params, timeout_s=self.timeout_s
                )
            elif self.argv:
                res = await asyncio.to_thread(
                    self.registry.execute_argv,
                    self.argv,
                    params,
                    timeout_s=self.timeout_s,
                )
            else:
                raise RuntimeError(
                    "cli channel not configured: provide tool= (registered) or argv= (inline)"
                )
            latency = int((time.perf_counter() - start) * 1000)
            if res.ok:
                return self._make_result(res.stdout, success=True, latency_ms=latency)
            # 子进程失败:携带注册表给出的四分类 + 逐路证据
            return self._make_result(
                res.stdout or "",
                success=False,
                latency_ms=latency,
                error=f"{res.error_kind}:{res.error}",
            )
        except Exception as exc:
            latency = int((time.perf_counter() - start) * 1000)
            return self._make_result(
                "", success=False, latency_ms=latency, error=_error_str(classify_error(exc), exc)
            )


# ============ CH3 — APIChannel (真实 model_pool.call) ============


class APIChannel(Channel):
    """CH3: 远程 API,最终 fallback,最贵 — 真实经 ``model_pool.call`` 调 LLM。

    - ``pool`` 可注入 (测试用 fake/真实 ModelPool 均可);生产路径默认
      ``get_model_pool()``。
    - ``endpoint_id`` 指定端点;缺省时取第一个可用端点。
    - 无可用端点按 auth 归类 (没有可用凭据),空响应按 empty 归类,
      ProviderError 401/403 由消息特征归入 auth — 全部走 classify_error。
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        name: str | None = None,
        pool: Any | None = None,
        endpoint_id: str | None = None,
        temperature: float = 0.6,
        max_tokens: int = 1024,
    ) -> None:
        super().__init__(ChannelType.API, enabled=enabled, name=name)
        self._pool = pool
        self.endpoint_id = endpoint_id
        self.temperature = temperature
        self.max_tokens = max_tokens

    @property
    def pool(self):
        if self._pool is None:
            from ..model_pool import get_model_pool

            self._pool = get_model_pool()
        return self._pool

    def _pick_endpoint(self, endpoint_id: str | None):
        pool = self.pool
        if endpoint_id:
            ep = pool.endpoints.get(endpoint_id)
            if ep is None:
                raise ValueError(f"endpoint {endpoint_id} not found")
            return ep
        available = pool.available_endpoints()
        if not available:
            raise PermissionError(
                "unauthorized: no available model endpoint (missing API keys?)"
            )
        return available[0]

    async def execute(self, query: str, **kwargs: Any) -> ChannelResult:
        start = time.perf_counter()
        try:
            endpoint_id = kwargs.get("endpoint_id") or self.endpoint_id
            ep = self._pick_endpoint(endpoint_id)
            system = str(kwargs.get("system") or "").strip()
            messages: list[dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": query or ""})
            resp = await self.pool.call(
                ep.id,
                messages,
                temperature=float(kwargs.get("temperature", self.temperature)),
                max_tokens=int(kwargs.get("max_tokens", self.max_tokens)),
            )
            latency = int((time.perf_counter() - start) * 1000)
            content = (resp.content or "").strip()
            if not content:
                exc = ValueError("empty response from api channel")
                return self._make_result(
                    "", success=False, latency_ms=latency, error=_error_str(classify_error(exc), exc)
                )
            return self._make_result(resp.content, success=True, latency_ms=latency)
        except Exception as exc:
            latency = int((time.perf_counter() - start) * 1000)
            return self._make_result(
                "", success=False, latency_ms=latency, error=_error_str(classify_error(exc), exc)
            )


# ============ Chain ============


class ChannelChain:
    """三通道顺序 fallback chain — CH1→CH2→CH3。

    返回 dict::

        {
            "channel": ChannelType,        # 成功的通道
            "result":   ChannelResult,      # 成功结果
            "fallback_path": List[ChannelType],  # 实际尝试过的通道 (含成功)
            "attempts": List[ChannelResult],     # 全部尝试结果 (成功 + 失败)
        }
    """

    def __init__(
        self,
        channels: Sequence[Channel] | None = None,
        *,
        order: Sequence[ChannelType] | None = None,
    ) -> None:
        if channels is None:
            channels = [
                SubagentChannel(),
                CLIChannel(),
                APIChannel(),
            ]
        self.channels: list[Channel] = list(channels)
        self._order: list[ChannelType] = list(
            order
            or [
                ChannelType.SUBAGENT,
                ChannelType.CLI,
                ChannelType.API,
            ]
        )

    def _ordered(self) -> list[Channel]:
        by_type = {c.channel_type: c for c in self.channels}
        out: list[Channel] = []
        for ct in self._order:
            ch = by_type.get(ct)
            if ch is not None:
                out.append(ch)
        return out

    def set_enabled(self, channel_type: ChannelType, enabled: bool) -> None:
        for c in self.channels:
            if c.channel_type == channel_type:
                c.enabled = enabled
                return
        raise KeyError(f"no such channel: {channel_type}")

    def is_enabled(self, channel_type: ChannelType) -> bool:
        for c in self.channels:
            if c.channel_type == channel_type:
                return c.enabled
        return False

    async def execute(self, query: str, **kwargs: Any) -> dict[str, Any]:
        attempts: list[ChannelResult] = []
        path: list[ChannelType] = []
        last_error: str | None = None

        for ch in self._ordered():
            if not ch.enabled:
                continue
            path.append(ch.channel_type)
            result = await ch.execute(query, **kwargs)
            attempts.append(result)
            if result.success:
                return {
                    "channel": result.channel,
                    "result": result,
                    "fallback_path": path,
                    "attempts": attempts,
                }
            last_error = result.error

        raise ChannelError(
            f"all channels failed; last_error={last_error}",
            attempts=attempts,
        )

    async def execute_safe(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """永不抛错的执行 — 失败时返回 ``{"error": ChannelError.to_dict()}``。"""
        try:
            return await self.execute(query, **kwargs)
        except ChannelError as exc:
            return {
                "channel": None,
                "result": None,
                "fallback_path": [r.channel for r in exc.attempts],
                "attempts": list(exc.attempts),
                "error": exc.to_dict(),
            }
