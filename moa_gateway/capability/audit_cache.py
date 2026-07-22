"""24h Audit Cache — LRU + TTL event store (reference table A-36).

来源:
- 04 moa-main-commercial (Audit Cache — 24h TTL 滑动窗口,LRU 淘汰)
- 06 moai-adk-multiagent (Persistence — to_dict / from_dict JSON round-trip)

真实实现,非 stub。所有时间戳基于 ``time.time()``,LRU 通过 ``OrderedDict``
实现,过期采用 lazy 删除(get / query 时检查),定期 ``cleanup()`` 主动 GC。
线程安全由 ``threading.RLock`` 保护所有 mutator。
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any

# ============ Constants ============

VALID_OUTCOMES = ("allow", "deny", "error")


# ============ Dataclass ============


@dataclass
class AuditEvent:
    """单条审计事件。

    Attributes:
        event_id: 唯一事件 ID(空时自动生成 uuid4)
        timestamp: 事件发生时间(Unix epoch 秒)
        event_type: 事件类型,如 ``"tool.invoke"`` / ``"policy.deny"``
        actor: 触发主体,通常是 user / agent / service id
        resource: 受影响资源,如 ``"fs:/etc/passwd"``
        action: 动作,如 ``"read"`` / ``"write"`` / ``"delete"``
        outcome: 处置结果,``"allow"`` / ``"deny"`` / ``"error"``
        metadata: 任意附加上下文(必须可 JSON 序列化以保证 round-trip)
    """

    event_type: str
    actor: str
    resource: str
    action: str
    outcome: str = "allow"
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, str) or self.outcome not in VALID_OUTCOMES:
            raise ValueError(f"outcome 必须是 {VALID_OUTCOMES} 之一,收到: {self.outcome!r}")
        if not self.event_id:
            self.event_id = str(uuid.uuid4())
        if self.metadata is None:
            self.metadata = {}
        if not isinstance(self.metadata, dict):
            # 兼容传入奇怪类型,降级为空 dict 而非崩溃
            self.metadata = {"_raw": str(self.metadata)}

    # ----- 序列化 -----

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON-safe 字典。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditEvent:
        """从 dict 反序列化。缺失字段使用 dataclass 默认值。"""
        if not isinstance(data, dict):
            raise TypeError(f"需要 dict,收到 {type(data).__name__}")
        # 过滤未知字段,保留 dataclass 字段
        allowed = {
            "event_id",
            "timestamp",
            "event_type",
            "actor",
            "resource",
            "action",
            "outcome",
            "metadata",
        }
        kwargs = {k: v for k, v in data.items() if k in allowed}
        return cls(**kwargs)


# ============ Cache ============


class AuditCache:
    """带 LRU 淘汰 + TTL 过期的线程安全审计缓存。

    Args:
        max_size: 最大事件数(超过则 LRU 淘汰最久未访问)。默认 10000。
        ttl_seconds: 事件存活秒数(默认 86400 = 24h)。
        time_func: 时间源(便于测试注入 mock)。默认 ``time.time``。

    Notes:
        - 过期采用 lazy 策略:``get`` / ``query`` 时检查;``count`` 不主动清理。
        - ``cleanup()`` 主动扫描整张表删除过期项,周期性调用即可。
        - 所有 mutator / 查询都持 ``RLock``,并发安全;RLock 允许同线程重入。
        - LRU 顺序:``get`` 命中会 ``move_to_end``;新 ``record`` 走 ``move_to_end``。
    """

    def __init__(
        self,
        max_size: int = 10000,
        ttl_seconds: int = 86400,
        time_func: Any | None = None,
    ) -> None:
        if max_size <= 0:
            raise ValueError(f"max_size 必须 > 0,收到 {max_size}")
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds 必须 > 0,收到 {ttl_seconds}")

        self._max_size = int(max_size)
        self._ttl = int(ttl_seconds)
        self._time = time_func if time_func is not None else time.time
        self._lock = threading.RLock()
        # OrderedDict: key=event_id, value=AuditEvent
        # 顺序语义: 最近访问(写入或命中)在末尾,最久未访问在头部
        self._store: OrderedDict[str, AuditEvent] = OrderedDict()

    # ----- 内部辅助 -----

    def _is_expired(self, ev: AuditEvent) -> bool:
        return (self._time() - ev.timestamp) > self._ttl

    def _touch(self, key: str) -> None:
        """把 key 移到末尾(标记最近使用)。要求持锁。"""
        with contextlib.suppress(KeyError):
            self._store.move_to_end(key)

    def _evict_lru(self) -> None:
        """淘汰头部(最久未访问)。要求持锁。"""
        if self._store:
            self._store.popitem(last=False)

    # ----- 写入 -----

    def record(self, event: AuditEvent) -> str:
        """记录一个事件。

        - 同一 ``event_id`` 已存在时,会用新事件覆盖并刷新时间戳(更新语义)。
        - 超过 ``max_size`` 时淘汰最久未访问的。

        Returns:
            事件的 ``event_id``。
        """
        if event is None:
            raise ValueError("event 不能为 None")
        with self._lock:
            existed = event.event_id in self._store
            self._store[event.event_id] = event
            self._store.move_to_end(event.event_id)
            if not existed and len(self._store) > self._max_size:
                self._evict_lru()
            return event.event_id

    # ----- 读取 -----

    def get(self, event_id: str) -> AuditEvent | None:
        """按 id 取出事件。

        - 命中且未过期:返回事件并刷新 LRU 顺序。
        - 命中但过期:lazy 删除,返回 ``None``。
        - 不存在:返回 ``None``。
        """
        with self._lock:
            ev = self._store.get(event_id)
            if ev is None:
                return None
            if self._is_expired(ev):
                with contextlib.suppress(KeyError):
                    del self._store[event_id]
                return None
            self._store.move_to_end(event_id)
            return ev

    def query(
        self,
        event_type: str | None = None,
        actor: str | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """按过滤条件查询事件(按 timestamp 升序,稳定顺序)。

        - ``event_type`` / ``actor`` 精确匹配;为 ``None`` 表示不过滤。
        - ``since`` 仅返回 ``timestamp >= since`` 的事件(用于"近 N 秒")。
        - 过期事件会在遍历时 lazy 删除。
        - ``limit`` 截断命中数,默认 100;<= 0 时返回空列表。
        """
        if limit <= 0:
            return []
        with self._lock:
            expired_keys: list[str] = []
            matched: list[AuditEvent] = []
            now = self._time()
            # 遍历时不立刻改 dict(改 key 列表稍后处理)
            for key, ev in self._store.items():
                if (now - ev.timestamp) > self._ttl:
                    expired_keys.append(key)
                    continue
                if event_type is not None and ev.event_type != event_type:
                    continue
                if actor is not None and ev.actor != actor:
                    continue
                if since is not None and ev.timestamp < since:
                    continue
                matched.append(ev)
            for k in expired_keys:
                with contextlib.suppress(KeyError):
                    del self._store[k]
            matched.sort(key=lambda e: e.timestamp)
            return matched[:limit]

    # ----- 维护 -----

    def count(self) -> int:
        """返回当前缓存事件数(包含已过期但未清理的条目)。"""
        with self._lock:
            return len(self._store)

    def cleanup(self) -> int:
        """主动清理过期事件,返回清理条数。"""
        with self._lock:
            now = self._time()
            expired = [k for k, ev in self._store.items() if (now - ev.timestamp) > self._ttl]
            for k in expired:
                with contextlib.suppress(KeyError):
                    del self._store[k]
            return len(expired)

    def stats(self) -> dict[str, int]:
        """按 ``event_type`` 统计未过期事件数。

        Returns:
            ``{event_type: count}``;空缓存返回 ``{}``。
        """
        with self._lock:
            now = self._time()
            out: dict[str, int] = {}
            for ev in self._store.values():
                if (now - ev.timestamp) > self._ttl:
                    continue
                out[ev.event_type] = out.get(ev.event_type, 0) + 1
            return out

    def clear(self) -> None:
        """清空整个缓存(主要用于测试)。"""
        with self._lock:
            self._store.clear()

    # ----- 序列化 -----

    def to_dict(self) -> dict[str, Any]:
        """导出为可 JSON 序列化的字典(仅含未过期事件)。"""
        with self._lock:
            now = self._time()
            events = [
                ev.to_dict() for ev in self._store.values() if (now - ev.timestamp) <= self._ttl
            ]
            return {
                "max_size": self._max_size,
                "ttl_seconds": self._ttl,
                "count": len(events),
                "events": events,
            }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        time_func: Any | None = None,
    ) -> AuditCache:
        """从 ``to_dict`` 产物重建缓存。

        过期事件不会进入新实例(避免导入即脏数据)。
        """
        if not isinstance(data, dict):
            raise TypeError(f"需要 dict,收到 {type(data).__name__}")
        max_size = int(data.get("max_size", 10000))
        ttl = int(data.get("ttl_seconds", 86400))
        cache = cls(max_size=max_size, ttl_seconds=ttl, time_func=time_func)
        now = time_func() if time_func is not None else time.time()
        events = data.get("events", []) or []
        for raw in events:
            try:
                ev = AuditEvent.from_dict(raw)
            except (TypeError, ValueError):
                continue
            if (now - ev.timestamp) > ttl:
                continue
            cache.record(ev)
        return cache

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(
        cls,
        payload: str,
        time_func: Any | None = None,
    ) -> AuditCache:
        return cls.from_dict(json.loads(payload), time_func=time_func)
