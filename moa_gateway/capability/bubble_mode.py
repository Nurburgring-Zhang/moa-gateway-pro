"""A-06 Bubble Mode (parent escalate) + A-26 Event scheduling (Trigger/Neutral/Terminal)

来源: 06 moai-adk-multiagent (bubble + event scheduling)

真实实现,非 mock:
- BubbleStatus 三态:ALLOWED / DENIED / ESCALATED
- BubbleManager 维护 parent 视角的升级请求队列,支持阻塞等待解析
- EventScheduler 三类事件调度:TRIGGER (继续) / NEUTRAL (继续) / TERMINAL (停止)
- wait_for_resolution 用 threading.Event 实现线程安全阻塞
- should_continue 用反向 tail 扫描决定是否继续
- 全部数据类支持 JSON 序列化
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional, Any


# ============ A-06 Bubble Mode ============

class BubbleStatus(str, Enum):
    """Bubble 升级请求的终态

    - ALLOWED: 父节点允许该 action
    - DENIED: 父节点拒绝该 action
    - ESCALATED: 等待父节点裁决(初始状态)
    """
    ALLOWED = "allowed"
    DENIED = "denied"
    ESCALATED = "escalated"


@dataclass
class EscalationRequest:
    """一次升级请求(子 agent → 父 agent)"""
    request_id: str
    agent_id: str
    parent_id: Optional[str]
    action: str
    reason: str
    created_at: float
    status: BubbleStatus = BubbleStatus.ESCALATED
    resolved_at: Optional[float] = None
    resolver_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class BubbleManager:
    """父节点上的升级请求管理器

    设计:
      - 父节点持有一个 BubbleManager(parent_id)
      - 子节点 escalate() 投递请求,父节点 resolve() 裁决
      - 线程安全:所有 mutation 加锁
      - wait_for_resolution 阻塞等待该 request_id 被 resolve
    """

    def __init__(self, parent_id: str) -> None:
        self.parent_id: str = parent_id
        self._requests: Dict[str, EscalationRequest] = {}
        self._events: Dict[str, threading.Event] = {}
        self._lock: threading.Lock = threading.Lock()
        self._escalate_count: int = 0
        self._resolve_count: int = 0

    def escalate(self, agent_id: str, action: str, reason: str) -> str:
        """子 agent 发起一次升级请求,返回 request_id

        request_id 可用于后续 wait_for_resolution()
        """
        request_id = f"esc_{uuid.uuid4().hex[:12]}"
        req = EscalationRequest(
            request_id=request_id,
            agent_id=agent_id,
            parent_id=self.parent_id,
            action=action,
            reason=reason,
            created_at=time.time(),
            status=BubbleStatus.ESCALATED,
        )
        with self._lock:
            self._requests[request_id] = req
            self._events[request_id] = threading.Event()
            self._escalate_count += 1
        return request_id

    def resolve(self, request_id: str, status: BubbleStatus, resolver_note: str = "") -> bool:
        """父节点裁决,返回是否成功(状态必须是 ALLOWED / DENIED,且请求存在)

        完成后唤醒可能正在 wait_for_resolution 的线程
        """
        if status not in (BubbleStatus.ALLOWED, BubbleStatus.DENIED):
            return False
        with self._lock:
            req = self._requests.get(request_id)
            if req is None:
                return False
            if req.status != BubbleStatus.ESCALATED:
                # 重复 resolve 视为失败(已裁决)
                return False
            req.status = status
            req.resolved_at = time.time()
            req.resolver_note = resolver_note
            self._resolve_count += 1
            ev = self._events.get(request_id)
        if ev is not None:
            ev.set()
        return True

    def get_request(self, request_id: str) -> Optional[EscalationRequest]:
        with self._lock:
            r = self._requests.get(request_id)
            return r

    def get_pending(self) -> List[EscalationRequest]:
        """所有未裁决的升级请求"""
        with self._lock:
            return [r for r in self._requests.values() if r.status == BubbleStatus.ESCALATED]

    def get_resolved(self) -> List[EscalationRequest]:
        """所有已裁决的升级请求"""
        with self._lock:
            return [r for r in self._requests.values() if r.status != BubbleStatus.ESCALATED]

    def wait_for_resolution(self, request_id: str, timeout: float = 30.0) -> EscalationRequest:
        """阻塞等待请求被 resolve,返回最终的 EscalationRequest

        超时后仍返回当前状态(可能仍是 ESCALATED)
        """
        with self._lock:
            ev = self._events.get(request_id)
            req = self._requests.get(request_id)
        if ev is None or req is None:
            raise KeyError(f"unknown request_id: {request_id}")
        ev.wait(timeout=timeout)
        with self._lock:
            cur = self._requests.get(request_id)
            # 返回当前快照(不会为 None,因为前文检查过)
            return cur  # type: ignore[return-value]

    @property
    def escalate_count(self) -> int:
        return self._escalate_count

    @property
    def resolve_count(self) -> int:
        return self._resolve_count

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "parent_id": self.parent_id,
                "total": len(self._requests),
                "pending": sum(1 for r in self._requests.values() if r.status == BubbleStatus.ESCALATED),
                "resolved": sum(1 for r in self._requests.values() if r.status != BubbleStatus.ESCALATED),
                "escalate_count": self._escalate_count,
                "resolve_count": self._resolve_count,
            }


# ============ A-26 Event scheduling ============

class EventType(str, Enum):
    """事件类型 — 决定 agent 是否继续

    - TRIGGER: 触发事件,agent 应继续
    - NEUTRAL: 中性事件,agent 应继续
    - TERMINAL: 终止事件,agent 应停止
    """
    TRIGGER = "trigger"
    NEUTRAL = "neutral"
    TERMINAL = "terminal"


@dataclass
class Event:
    """调度事件"""
    event_id: str
    event_type: EventType
    agent_id: str
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class EventScheduler:
    """每 agent 独立的事件流调度器

    设计:
      - schedule() 追加事件到该 agent 的流(按时间戳)
      - should_continue() 反向 tail 扫描:
        * 无事件 → True(允许继续)
        * 末事件 TERMINAL → False
        * 末事件 TRIGGER → True
        * 末事件 NEUTRAL → True
      - recent_events(n) 返回最近 n 条
      - clear() 清空该 agent 的事件流
    """

    def __init__(self) -> None:
        self._streams: Dict[str, List[Event]] = {}
        self._lock: threading.Lock = threading.Lock()
        self._schedule_count: int = 0

    def schedule(self, event: Event) -> str:
        """调度一个事件,返回 event_id"""
        if not event.event_id:
            event.event_id = f"evt_{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._streams.setdefault(event.agent_id, []).append(event)
            self._schedule_count += 1
        return event.event_id

    def should_continue(self, agent_id: str) -> bool:
        """根据该 agent 的末事件决定是否继续

        - 无事件 → True
        - 末事件 TERMINAL → False
        - 末事件 TRIGGER/NEUTRAL → True
        """
        with self._lock:
            stream = self._streams.get(agent_id)
        if not stream:
            return True
        last = stream[-1]
        return last.event_type != EventType.TERMINAL

    def recent_events(self, agent_id: str, n: int = 10) -> List[Event]:
        """最近 n 条事件(按时间顺序,最新在末尾)"""
        if n <= 0:
            return []
        with self._lock:
            stream = self._streams.get(agent_id, [])
            return list(stream[-n:])

    def clear(self, agent_id: str) -> int:
        """清空该 agent 的事件流,返回被清空的数量"""
        with self._lock:
            stream = self._streams.pop(agent_id, None)
            return len(stream) if stream else 0

    def event_count(self, agent_id: str) -> int:
        with self._lock:
            stream = self._streams.get(agent_id, [])
            return len(stream)

    @property
    def schedule_count(self) -> int:
        return self._schedule_count

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "agents": list(self._streams.keys()),
                "schedule_count": self._schedule_count,
                "per_agent": {aid: len(s) for aid, s in self._streams.items()},
            }


# ============ Self-check (import 时不执行测试) ============

if __name__ == "__main__":
    # 简单冒烟,仅当显式运行此文件时
    bm = BubbleManager(parent_id="root")
    rid = bm.escalate("child1", "delete_file", "dangerous action")
    print("escalate:", rid, "pending=", len(bm.get_pending()))
    assert bm.resolve(rid, BubbleStatus.ALLOWED, "ok")
    print("after resolve:", bm.to_dict())

    es = EventScheduler()
    for et in (EventType.TRIGGER, EventType.NEUTRAL, EventType.TERMINAL):
        eid = es.schedule(Event(
            event_id="",
            event_type=et,
            agent_id="a1",
            payload={"k": et.value},
        ))
        print(f"  scheduled {et.value}: continue={es.should_continue('a1')}")
