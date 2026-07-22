"""Trace Propagation 链路追踪 (W3C traceparent 兼容)

来源:
- 04 moa-main-commercial (T-13 Trace Propagation 链路追踪)
- 06 moai-adk-multiagent (L-32 跨 span 串联强化)

核心概念: 每个 request 一个 trace_id,跨多个 span (provider call / moa aggregate
/ cache hit) 通过 parent_span_id 串联,形成完整调用树。

W3C Trace Context 格式 (RFC):
    traceparent = version "-" trace-id "-" parent-id "-" trace-flags
    version    = 2 hex (e.g. "00")
    trace-id   = 32 hex chars
    parent-id  = 16 hex chars
    trace-flags = 2 hex chars (00 = not sampled, 01 = sampled)

本模块:
- 真实 UUID4 hex 生成 trace_id / span_id
- 真实 W3C 格式序列化 / 反序列化
- thread-safe (RLock) 内部存储
- 支持按时间 / 耗时 / 状态过滤查询
- 完整的 trace tree 重建 (parent_span_id → children 递归)

非 mock。所有时间戳基于 time.time(),UUID 基于 uuid.uuid4().hex。
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

# ============ Constants ============

_TRACEPARENT_RE = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})(?:-[0-9a-f]{1,32}-[0-9a-f]{1,64})?$"
)

# all-zero 是不合法 trace_id / span_id (W3C 规范)
_INVALID_IDS = {"0" * 32, "0" * 16}

_STATUS_OK = "ok"
_STATUS_ERROR = "error"
_VALID_STATUS = {_STATUS_OK, _STATUS_ERROR}

_FLAGS_DEFAULT = "01"  # sampled
_VERSION_DEFAULT = "00"


# ============ Dataclasses ============


@dataclass
class TraceContext:
    """单次 span 上下文 (同时代表 trace 根,parent_span_id 为 None)"""

    trace_id: str  # 32 hex chars
    parent_span_id: str | None  # 父 span_id;None 表示 root
    span_id: str  # 16 hex chars
    start_ts: float  # time.time()
    tags: dict[str, Any] = field(default_factory=dict)
    baggage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============ Span helpers ============


def _new_trace_id() -> str:
    """生成 32 hex 字符 trace_id (W3C)"""
    return uuid.uuid4().hex


def _new_span_id() -> str:
    """生成 16 hex 字符 span_id (W3C)"""
    return uuid.uuid4().hex[:16]


def new_trace(
    tags: dict[str, Any] | None = None, baggage: dict[str, Any] | None = None
) -> TraceContext:
    """创建一个新的 root trace (parent_span_id = None)

    Args:
        tags: 静态标签 (例如 service / env / user)
        baggage: 透传 baggage (跨 span 携带的键值)

    Returns:
        TraceContext (parent_span_id == None)
    """
    return TraceContext(
        trace_id=_new_trace_id(),
        parent_span_id=None,
        span_id=_new_span_id(),
        start_ts=time.time(),
        tags=dict(tags) if tags else {},
        baggage=dict(baggage) if baggage else {},
    )


def new_span(parent: TraceContext, name: str, tags: dict[str, Any] | None = None) -> TraceContext:
    """基于 parent 创建一个 child span

    Args:
        parent: 父 trace context
        name: span 名称 (会存到 tags["span.name"])
        tags: 额外标签

    Returns:
        新的 TraceContext (trace_id 继承,parent_span_id = parent.span_id)
    """
    merged_tags: dict[str, Any] = dict(parent.tags)
    if tags:
        merged_tags.update(tags)
    merged_tags["span.name"] = name
    return TraceContext(
        trace_id=parent.trace_id,
        parent_span_id=parent.span_id,
        span_id=_new_span_id(),
        start_ts=time.time(),
        tags=merged_tags,
        baggage=dict(parent.baggage),
    )


# ============ W3C traceparent ============


def format_traceparent(ctx: TraceContext, flags: str = _FLAGS_DEFAULT) -> str:
    """序列化为 W3C traceparent header

    Args:
        ctx: TraceContext
        flags: 2 hex 字符 (默认 "01" = sampled)

    Returns:
        形如 "00-<32hex>-<16hex>-01" 字符串
    """
    if not re.fullmatch(r"[0-9a-f]{2}", flags):
        flags = _FLAGS_DEFAULT
    return f"{_VERSION_DEFAULT}-{ctx.trace_id}-{ctx.span_id}-{flags}"


def parse_traceparent(header: str) -> TraceContext | None:
    """从 W3C traceparent header 解析出 TraceContext

    任何格式错误 (长度 / 非 hex / 全零) 均返回 None。

    Args:
        header: 原始 header 字符串

    Returns:
        TraceContext (parent_span_id = span_id, 因为这是上游传下来的)
        或 None (解析失败)
    """
    if not header or not isinstance(header, str):
        return None
    header = header.strip()
    m = _TRACEPARENT_RE.match(header)
    if not m:
        return None
    version, trace_id, parent_id, flags = m.groups()
    if version != _VERSION_DEFAULT:
        return None
    if trace_id in _INVALID_IDS or parent_id in _INVALID_IDS:
        return None
    ctx = TraceContext(
        trace_id=trace_id,
        parent_span_id=parent_id,
        span_id=_new_span_id(),
        start_ts=time.time(),
        tags={"traceparent.flags": flags},
        baggage={},
    )
    return ctx


# ============ Collector ============


class TraceCollector:
    """线程安全的 trace 收集器

    内部存储: trace_id → {ctx, spans: [...]}
    - ctx: start_trace 时的 root TraceContext
    - spans: 每次 record_span 追加一条 span 记录

    使用 RLock 保护 _traces / _order 两张表。
    """

    def __init__(self, max_traces: int = 10000) -> None:
        if max_traces <= 0:
            max_traces = 1
        self._max_traces = max_traces
        self._traces: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []  # 插入顺序,便于 LRU 淘汰
        self._lock = threading.RLock()

    # ----- start / end -----

    def start_trace(self, traceparent_header: str | None = None) -> TraceContext:
        """开始一个 trace

        如果 traceparent_header 存在且能解析,继承其 trace_id (生成新 span_id);
        否则创建新 trace。

        Returns:
            根 TraceContext
        """
        with self._lock:
            if traceparent_header:
                try:
                    ctx = parse_traceparent(traceparent_header)
                    if ctx is not None:
                        # parent_span_id 设为 None,这是新 root
                        ctx = TraceContext(
                            trace_id=ctx.trace_id,
                            parent_span_id=None,
                            span_id=ctx.span_id,
                            start_ts=ctx.start_ts,
                            tags=ctx.tags,
                            baggage=ctx.baggage,
                        )
                        self._register(ctx)
                        return ctx
                except Exception:
                    pass
            ctx = new_trace()
            self._register(ctx)
            return ctx

    def _register(self, ctx: TraceContext) -> None:
        """内部: 把新 trace 注册到 _traces,满了就 LRU 淘汰"""
        if ctx.trace_id in self._traces:
            # 已存在: 移除旧位置以便插到末尾
            with contextlib.suppress(ValueError):
                self._order.remove(ctx.trace_id)
        # 容量检查
        elif len(self._traces) >= self._max_traces and self._order:
            evict_id = self._order.pop(0)
            self._traces.pop(evict_id, None)
        self._traces[ctx.trace_id] = {
            "ctx": ctx,
            "spans": [],
            "status": _STATUS_OK,
            "error": None,
            "end_ts": None,
        }
        self._order.append(ctx.trace_id)

    def end_trace(self, ctx: TraceContext, status: str = "ok", error: str | None = None) -> None:
        """结束一个 trace

        Args:
            ctx: 根 TraceContext (通常 start_trace 返回)
            status: "ok" | "error"
            error: 错误描述 (status=error 时建议提供)
        """
        if status not in _VALID_STATUS:
            status = _STATUS_OK
        with self._lock:
            entry = self._traces.get(ctx.trace_id)
            if entry is None:
                return
            entry["status"] = status
            entry["error"] = error
            entry["end_ts"] = time.time()

    # ----- spans -----

    def record_span(
        self,
        ctx: TraceContext,
        name: str,
        duration_ms: float,
        attrs: dict[str, Any] | None = None,
        status: str = _STATUS_OK,
        error: str | None = None,
    ) -> None:
        """记录一个 span 节点

        Args:
            ctx: 该 span 对应的 TraceContext (可用 new_span 产生)
            name: span 名称
            duration_ms: 耗时 (毫秒)
            attrs: 附加属性
            status: "ok" | "error"
            error: 错误信息
        """
        if status not in _VALID_STATUS:
            status = _STATUS_OK
        try:
            start_ts = ctx.start_ts
            end_ts = start_ts + max(0.0, float(duration_ms)) / 1000.0
        except Exception:
            start_ts = time.time()
            end_ts = start_ts
        with self._lock:
            entry = self._traces.get(ctx.trace_id)
            if entry is None:
                # 兜底: 若 ctx 没注册过,自动注册
                self._register(ctx)
                entry = self._traces[ctx.trace_id]
            entry["spans"].append(
                {
                    "name": name,
                    "parent_span_id": ctx.parent_span_id,
                    "span_id": ctx.span_id,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                    "attrs": dict(attrs) if attrs else {},
                    "status": status,
                    "error": error,
                    "duration_ms": max(0.0, float(duration_ms)),
                }
            )

    # ----- read -----

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        """获取完整 trace tree

        返回 dict:
            {
              "trace_id": ...,
              "root": {...ctx 字段...},
              "spans": [...],
              "tree": [递归 children 树],
              "status": "ok" | "error",
              "error": ... | null,
              "start_ts": ...,
              "end_ts": ... | null,
              "total_duration_ms": float,
            }
        """
        with self._lock:
            entry = self._traces.get(trace_id)
            if entry is None:
                return None
            ctx: TraceContext = entry["ctx"]
            spans: list[dict[str, Any]] = list(entry["spans"])
            status = entry["status"]
            error = entry["error"]
            end_ts = entry["end_ts"]

        # 构建 span 树 (基于 span_id 索引)
        by_id: dict[str, dict[str, Any]] = {}
        for sp in spans:
            child = dict(sp)
            child["children"] = []
            by_id[sp["span_id"]] = child
        roots: list[dict[str, Any]] = []
        for sp in by_id.values():
            parent_id = sp.get("parent_span_id")
            if parent_id and parent_id in by_id:
                by_id[parent_id]["children"].append(sp)
            else:
                roots.append(sp)
        # 排序: 按 start_ts 升序
        roots.sort(key=lambda x: x.get("start_ts", 0.0))

        # 耗时计算
        total_ms = 0.0
        if end_ts is not None:
            total_ms = max(0.0, (end_ts - ctx.start_ts) * 1000.0)
        elif spans:
            first = min((s["start_ts"] for s in spans), default=ctx.start_ts)
            last = max((s["end_ts"] for s in spans), default=ctx.start_ts)
            total_ms = max(0.0, (last - first) * 1000.0)

        return {
            "trace_id": trace_id,
            "root": ctx.to_dict(),
            "spans": spans,
            "tree": roots,
            "status": status,
            "error": error,
            "start_ts": ctx.start_ts,
            "end_ts": end_ts,
            "total_duration_ms": total_ms,
        }

    def query(
        self,
        since_ts: float | None = None,
        min_duration_ms: float | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """查询 trace 列表 (按 start_ts 倒序)

        Args:
            since_ts: 只返回 start_ts >= since_ts 的
            min_duration_ms: 只返回 total_duration_ms >= 该值的
            status: "ok" | "error"
            limit: 最大返回数

        Returns:
            trace dict 列表 (不含 tree,只含 summary)
        """
        if limit <= 0:
            return []
        if status is not None and status not in _VALID_STATUS:
            status = None
        with self._lock:
            items = list(self._traces.items())

        # 按 start_ts 倒序
        items.sort(key=lambda kv: kv[1]["ctx"].start_ts, reverse=True)

        results: list[dict[str, Any]] = []
        for trace_id, entry in items:
            ctx: TraceContext = entry["ctx"]
            start_ts = ctx.start_ts
            if since_ts is not None and start_ts < since_ts:
                continue
            end_ts = entry.get("end_ts")
            if end_ts is not None:
                total_ms = max(0.0, (end_ts - start_ts) * 1000.0)
            else:
                spans = entry.get("spans") or []
                if spans:
                    first = min((s["start_ts"] for s in spans), default=start_ts)
                    last = max((s["end_ts"] for s in spans), default=start_ts)
                    total_ms = max(0.0, (last - first) * 1000.0)
                else:
                    total_ms = 0.0
            if min_duration_ms is not None and total_ms < min_duration_ms:
                continue
            if status is not None and entry.get("status") != status:
                continue
            results.append(
                {
                    "trace_id": trace_id,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                    "status": entry.get("status", _STATUS_OK),
                    "error": entry.get("error"),
                    "span_count": len(entry.get("spans") or []),
                    "total_duration_ms": total_ms,
                    "tags": dict(ctx.tags),
                }
            )
            if len(results) >= limit:
                break
        return results

    def stats(self) -> dict[str, int]:
        """全局统计

        Returns:
            {
              "total": 当前 trace 数,
              "ok": status=ok 数,
              "error": status=error 数,
              "in_flight": 未结束 (end_ts=None) 数,
              "max_traces": 容量上限,
            }
        """
        with self._lock:
            total = len(self._traces)
            ok = 0
            err = 0
            in_flight = 0
            for entry in self._traces.values():
                st = entry.get("status", _STATUS_OK)
                if st == _STATUS_ERROR:
                    err += 1
                else:
                    ok += 1
                if entry.get("end_ts") is None:
                    in_flight += 1
        return {
            "total": total,
            "ok": ok,
            "error": err,
            "in_flight": in_flight,
            "max_traces": self._max_traces,
        }

    def cleanup(self, older_than_seconds: float) -> int:
        """清理 end_ts 距今超过 N 秒的已完成 trace (in_flight 不删)

        Returns:
            实际删除的 trace 数量
        """
        if older_than_seconds < 0:
            older_than_seconds = 0.0
        threshold = time.time() - older_than_seconds
        removed = 0
        with self._lock:
            for trace_id in list(self._order):
                entry = self._traces.get(trace_id)
                if entry is None:
                    continue
                end_ts = entry.get("end_ts")
                if end_ts is None:
                    continue  # in_flight
                if end_ts < threshold:
                    self._traces.pop(trace_id, None)
                    with contextlib.suppress(ValueError):
                        self._order.remove(trace_id)
                    removed += 1
        return removed

    def clear(self) -> None:
        """清空所有 (主要用于测试)"""
        with self._lock:
            self._traces.clear()
            self._order.clear()
