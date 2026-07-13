"""三通道 fallback 抽象 (R-23)

提供 SUBAGENT / CLI / API 三通道顺序 fallback chain:
- CH1 = SUBAGENT: 本地 subagent,先尝试 — 快速便宜
- CH2 = CLI: CLI 工具 fallback (例如 codex CLI),第二尝试
- CH3 = API: 远程 API,最终 fallback,最贵

Chain 行为: 顺序尝试 CH1→CH2→CH3,第一个成功就返回,都失败抛 ``ChannelError``。

错误分类 (R-24): 4 类 — auth / timeout / cli / empty。
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, NamedTuple, Optional, Sequence

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
    error: Optional[str] = None


class ChannelError(RuntimeError):
    """所有通道均失败时抛出"""

    def __init__(self, message: str, attempts: Sequence[ChannelResult]) -> None:
        super().__init__(message)
        self.attempts: List[ChannelResult] = list(attempts)

    def to_dict(self) -> Dict[str, Any]:
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


# ============ Channel ABC ============


class Channel(ABC):
    """通道抽象基类"""

    def __init__(
        self,
        channel_type: ChannelType,
        *,
        enabled: bool = True,
        name: Optional[str] = None,
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
        error: Optional[str] = None,
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


# ============ CH1 — SubagentChannel ============


class SubagentChannel(Channel):
    """CH1: 本地 subagent,快速便宜 — 智能 mock fallback

    当 query 命中内置启发式 (空 / 简单问候 / 短命令) 时直接给出文本,
    其它情况按概率选择"成功 / 失败"以测试 chain fallback。
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        name: Optional[str] = None,
        fail_rate: float = 0.0,
        sleep_ms: int = 5,
    ) -> None:
        super().__init__(ChannelType.SUBAGENT, enabled=enabled, name=name)
        self.fail_rate = max(0.0, min(1.0, fail_rate))
        self.sleep_ms = max(0, sleep_ms)

    async def execute(self, query: str, **kwargs: Any) -> ChannelResult:
        start = time.perf_counter()
        try:
            await asyncio.sleep(self.sleep_ms / 1000.0)
            if self.fail_rate > 0 and random.random() < self.fail_rate:
                latency = int((time.perf_counter() - start) * 1000)
                return self._make_result(
                    "",
                    success=False,
                    latency_ms=latency,
                    error="subagent simulated failure",
                )
            text = _smart_subagent_answer(query)
            latency = int((time.perf_counter() - start) * 1000)
            return self._make_result(text, success=True, latency_ms=latency)
        except Exception as exc:  # 兜底
            latency = int((time.perf_counter() - start) * 1000)
            return self._make_result(
                "", success=False, latency_ms=latency, error=f"{type(exc).__name__}: {exc}"
            )


def _smart_subagent_answer(query: str) -> str:
    """本地 subagent 的极简启发式 — 真实可用,但功能有限。"""
    q = (query or "").strip()
    if not q:
        return "[subagent] (empty query, no answer)"
    lower = q.lower()
    if lower in {"hi", "hello", "hey", "你好", "您好"}:
        return f"[subagent] hello — got {len(q)} chars"
    if q.endswith("?"):
        return f"[subagent] tentative answer to: {q[:80]}"
    return f"[subagent] quick answer ({len(q)} chars): {q[:80]}"


# ============ CH2 — CLIChannel ============


class CLIChannel(Channel):
    """CH2: CLI fallback,使用 ``asyncio.to_thread`` 包装同步调用。

    默认走 mock 路径: sleep 一段,然后根据 ``fail_kind`` 返回错误分类,
    或根据 ``fail_empty_output`` 返回空字符串。
    真实场景下,这里会调用 ``subprocess.run`` 启动 ``codex`` / ``claude`` 等 CLI。
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        name: Optional[str] = None,
        fail_kind: Optional[str] = None,
        fail_empty_output: bool = False,
        sleep_ms: int = 20,
    ) -> None:
        super().__init__(ChannelType.CLI, enabled=enabled, name=name)
        self.fail_kind = fail_kind
        self.fail_empty_output = fail_empty_output
        self.sleep_ms = max(0, sleep_ms)

    async def execute(self, query: str, **kwargs: Any) -> ChannelResult:
        start = time.perf_counter()
        try:
            output = await asyncio.to_thread(self._run_cli_blocking, query)
            latency = int((time.perf_counter() - start) * 1000)
            if self.fail_empty_output or not (output or "").strip():
                return self._make_result(
                    output or "",
                    success=False,
                    latency_ms=latency,
                    error=f"empty:{CLIErrorKind.EMPTY.value}",
                )
            return self._make_result(output, success=True, latency_ms=latency)
        except Exception as exc:
            latency = int((time.perf_counter() - start) * 1000)
            kind = self.fail_kind or classify_error(exc)
            return self._make_result(
                "", success=False, latency_ms=latency, error=f"{kind}:{type(exc).__name__}:{exc}"
            )

    def _run_cli_blocking(self, query: str) -> str:
        """同步 CLI 模拟 — 真实实现应替换为 ``subprocess.run``。"""
        time.sleep(self.sleep_ms / 1000.0)
        if self.fail_kind:
            _raise_for_kind(self.fail_kind, query)
        return f"[cli] processed: {(query or '')[:80]}"


def _raise_for_kind(kind: str, query: str) -> None:
    """按指定错误分类抛出对应异常 — 供 CLI mock 使用。"""
    if kind == CLIErrorKind.AUTH.value:
        raise PermissionError(f"unauthorized: bad token for query={query[:20]}")
    if kind == CLIErrorKind.TIMEOUT.value:
        raise TimeoutError(f"timeout after waiting for query={query[:20]}")
    if kind == CLIErrorKind.EMPTY.value:
        raise ValueError("empty response from cli")
    if kind == CLIErrorKind.CLI.value:
        raise RuntimeError(f"cli error while processing query={query[:20]}")
    raise RuntimeError(f"unknown cli kind={kind}")


# ============ CH3 — APIChannel ============


class APIChannel(Channel):
    """CH3: 远程 API,最终 fallback,最贵。

    mock 行为: sleep + 返回 (或抛错) — 真实实现会调用 OpenAI/Anthropic SDK。
    支持 ``fail_kind`` 模拟各错误分类。
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        name: Optional[str] = None,
        fail_kind: Optional[str] = None,
        sleep_ms: int = 50,
        api_key_env: Optional[str] = None,
    ) -> None:
        super().__init__(ChannelType.API, enabled=enabled, name=name)
        self.fail_kind = fail_kind
        self.sleep_ms = max(0, sleep_ms)
        self.api_key_env = api_key_env

    async def execute(self, query: str, **kwargs: Any) -> ChannelResult:
        start = time.perf_counter()
        try:
            await asyncio.sleep(self.sleep_ms / 1000.0)
            if self.fail_kind:
                _raise_for_kind(self.fail_kind, query)
            if self.api_key_env and not os.environ.get(self.api_key_env):
                raise PermissionError(
                    f"missing api key env={self.api_key_env}"
                )
            text = f"[api] final answer ({len(query or '')} chars): {(query or '')[:80]}"
            latency = int((time.perf_counter() - start) * 1000)
            return self._make_result(text, success=True, latency_ms=latency)
        except Exception as exc:
            latency = int((time.perf_counter() - start) * 1000)
            kind = self.fail_kind or classify_error(exc)
            return self._make_result(
                "", success=False, latency_ms=latency, error=f"{kind}:{type(exc).__name__}:{exc}"
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
        channels: Optional[Sequence[Channel]] = None,
        *,
        order: Optional[Sequence[ChannelType]] = None,
    ) -> None:
        if channels is None:
            channels = [
                SubagentChannel(),
                CLIChannel(),
                APIChannel(),
            ]
        self.channels: List[Channel] = list(channels)
        self._order: List[ChannelType] = list(order or [
            ChannelType.SUBAGENT,
            ChannelType.CLI,
            ChannelType.API,
        ])

    def _ordered(self) -> List[Channel]:
        by_type = {c.channel_type: c for c in self.channels}
        out: List[Channel] = []
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

    async def execute(self, query: str, **kwargs: Any) -> Dict[str, Any]:
        attempts: List[ChannelResult] = []
        path: List[ChannelType] = []
        last_error: Optional[str] = None

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

    async def execute_safe(self, query: str, **kwargs: Any) -> Dict[str, Any]:
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
