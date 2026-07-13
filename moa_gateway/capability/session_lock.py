"""A-22 Multi-session 协调 (advisory lock) + A-20 MCP 工具注册 (rmcp) 简化版

来源: 06 moai-adk-multiagent (advisory lock) + 04 moa-main-commercial (MCP tools)

真实实现,非 mock,线程安全:
- SessionLockManager 多 lock_id 并发管理,带 TTL 过期 + 等待队列
- try_acquire 非阻塞;acquire_with_wait 阻塞重试(可配 retry_interval / timeout)
- cleanup_expired 主动回收过期锁,过期锁等同于未占用
- MCPRegistry 注册 / 注销 / 调用工具,handler 是任意 Callable
- 所有可变状态由 threading.RLock 守护,适配多 session 并发
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Dict, Any, Optional, Callable


# ============ SessionLockState ============

class SessionLockState(str, Enum):
    """锁状态字面量"""
    FREE = "free"          # 无人占用
    ACQUIRED = "acquired"  # 已获取
    WAITING = "waiting"    # 至少有一个 session 在等待


# ============ SessionLock ============

@dataclass
class SessionLock:
    """单条锁记录(含等待队列)"""
    lock_id: str
    session_id: str
    acquired_at: float
    expires_at: float
    state: SessionLockState = SessionLockState.ACQUIRED
    waiters: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ============ SessionLockManager ============

class SessionLockManager:
    """进程内多锁管理:每个 lock_id 独立 holder,带 TTL + 等待队列

    设计:
      - _locks: lock_id -> SessionLock(占用时存在;过期或释放后移除)
      - try_acquire: 一次性检查 + 占锁;占不上立即返回 False
      - acquire_with_wait: 循环重试(每 retry_interval 秒),直到拿到或 timeout
      - release: 仅 holder 能释放;非 holder 返回 False
      - cleanup_expired: 扫描并删除所有过期锁记录
      - get_lock_state: 读取快照(FREE 表示未占用)
    """

    def __init__(self, default_ttl: float = 30.0) -> None:
        if default_ttl <= 0:
            raise ValueError("default_ttl must be > 0")
        self.default_ttl: float = float(default_ttl)
        self._locks: Dict[str, SessionLock] = {}
        self._lock: threading.RLock = threading.RLock()

    # ---- 内部 ----

    def _is_expired(self, lock: SessionLock, now: float) -> bool:
        return now >= lock.expires_at

    def _remove_lock(self, lock_id: str) -> None:
        self._locks.pop(lock_id, None)

    # ---- 公共 API ----

    def try_acquire(
        self,
        lock_id: str,
        session_id: str,
        ttl: Optional[float] = None,
    ) -> bool:
        """非阻塞获取;成功返回 True,否则 False

        行为:
          1. 锁槽空 → 直接占锁
          2. 锁存在但已过期 → 视为 FREE,强制夺锁(原 holder 视为泄漏)
          3. 锁存在且未过期、且 holder == session_id → 重入(刷新 expires_at)
          4. 其他情况:加入 waiters,返回 False
        """
        if not lock_id:
            raise ValueError("lock_id must be non-empty")
        if not session_id:
            raise ValueError("session_id must be non-empty")
        effective_ttl = float(self.default_ttl if ttl is None else ttl)
        if effective_ttl <= 0:
            raise ValueError("ttl must be > 0")

        with self._lock:
            now = time.time()
            existing = self._locks.get(lock_id)
            if existing is None:
                self._locks[lock_id] = SessionLock(
                    lock_id=lock_id,
                    session_id=session_id,
                    acquired_at=now,
                    expires_at=now + effective_ttl,
                    state=SessionLockState.ACQUIRED,
                    waiters=[],
                )
                return True

            if self._is_expired(existing, now):
                # 过期 → 强制夺锁
                existing.session_id = session_id
                existing.acquired_at = now
                existing.expires_at = now + effective_ttl
                existing.state = SessionLockState.ACQUIRED
                existing.waiters = []
                return True

            if existing.session_id == session_id:
                # 重入:刷新 TTL
                existing.expires_at = now + effective_ttl
                return True

            # 别人持锁
            if session_id not in existing.waiters:
                existing.waiters.append(session_id)
            if len(existing.waiters) > 0:
                existing.state = SessionLockState.WAITING
            return False

    def acquire_with_wait(
        self,
        lock_id: str,
        session_id: str,
        timeout: float = 10.0,
        retry_interval: float = 0.01,
    ) -> bool:
        """阻塞获取:循环调用 try_acquire,直到拿到或 timeout 耗尽

        Args:
            lock_id: 锁 ID
            session_id: 申请者 session ID
            timeout: 总等待上限(秒);<= 0 直接返回 False
            retry_interval: 两次重试之间的间隔(秒);<= 0 退化为不 sleep
        """
        if timeout <= 0:
            return False
        if retry_interval < 0:
            raise ValueError("retry_interval must be >= 0")

        deadline = time.time() + timeout
        while True:
            if self.try_acquire(lock_id, session_id):
                return True
            remaining = deadline - time.time()
            if remaining <= 0:
                return False
            # 本轮 sleep 不能超过剩余时间
            time.sleep(min(retry_interval, remaining))

    def release(self, lock_id: str, session_id: str) -> bool:
        """释放锁:仅 holder(session_id 匹配)能成功释放

        释放后从 _locks 移除该条目(等下次 try_acquire 重新创建)
        """
        with self._lock:
            existing = self._locks.get(lock_id)
            if existing is None:
                return False
            if existing.session_id != session_id:
                return False
            self._remove_lock(lock_id)
            return True

    def get_lock_state(self, lock_id: str) -> Optional[SessionLock]:
        """读取锁快照(深拷贝 waiters,防止外部修改内部 list)

        返回 None 表示该 lock_id 当前无记录(等价 FREE)
        """
        with self._lock:
            existing = self._locks.get(lock_id)
            if existing is None:
                return None
            return SessionLock(
                lock_id=existing.lock_id,
                session_id=existing.session_id,
                acquired_at=existing.acquired_at,
                expires_at=existing.expires_at,
                state=existing.state,
                waiters=list(existing.waiters),
            )

    def cleanup_expired(self) -> int:
        """清理所有过期锁,返回清理数量"""
        with self._lock:
            now = time.time()
            expired = [k for k, v in self._locks.items() if self._is_expired(v, now)]
            for k in expired:
                self._remove_lock(k)
            return len(expired)

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "default_ttl": self.default_ttl,
                "lock_count": len(self._locks),
                "locks": [v.to_dict() for v in self._locks.values()],
            }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ============ MCPTool ============

@dataclass
class MCPTool:
    """单个 MCP 工具描述 + handler"""
    name: str
    description: str
    parameters: Dict[str, str]  # 参数名 -> 类型提示(如 "int" / "str" / "List[str]")
    handler: Callable[..., Any]

    def to_dict(self) -> Dict[str, Any]:
        # handler 是 Callable,不能 JSON 序列化;只导出元数据
        return {
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.parameters),
        }


# ============ MCPRegistry ============

class MCPRegistry:
    """MCP 工具注册表(rmcp 简化版)

    设计:
      - _tools: name -> MCPTool(全量)
      - register / unregister: 增删工具;name 重复时 register 抛 ValueError
      - invoke: 用 **kwargs 调用 handler,handler 抛异常会向上传播
      - list_tools: 返回元数据字典列表(不含 handler)
      - get_tool: 按名取 MCPTool
      - 全部操作由 _lock 守护
    """

    def __init__(self) -> None:
        self._tools: Dict[str, MCPTool] = {}
        self._lock: threading.RLock = threading.RLock()

    def register(self, tool: MCPTool) -> None:
        if not isinstance(tool, MCPTool):
            raise TypeError(f"tool must be MCPTool, got {type(tool).__name__}")
        if not tool.name:
            raise ValueError("tool.name must be non-empty")
        if not callable(tool.handler):
            raise TypeError("tool.handler must be callable")
        with self._lock:
            if tool.name in self._tools:
                raise ValueError(f"tool {tool.name!r} already registered")
            self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        with self._lock:
            return self._tools.pop(name, None) is not None

    def invoke(self, name: str, **kwargs: Any) -> Any:
        """按名调用工具;若不存在抛 KeyError"""
        with self._lock:
            tool = self._tools.get(name)
            if tool is None:
                raise KeyError(f"tool not found: {name!r}")
            handler = tool.handler
        return handler(**kwargs)

    def list_tools(self) -> List[Dict[str, Any]]:
        """返回所有工具的元数据字典列表(按 name 排序)"""
        with self._lock:
            return sorted(
                [t.to_dict() for t in self._tools.values()],
                key=lambda d: d["name"],
            )

    def get_tool(self, name: str) -> Optional[MCPTool]:
        with self._lock:
            return self._tools.get(name)

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "tool_count": len(self._tools),
                "tools": self.list_tools(),
            }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ============ 顶层 JSON 序列化辅助 ============

def lock_to_json(lock: SessionLock) -> str:
    return lock.to_json()


def tool_to_json(tool: MCPTool) -> str:
    return json.dumps(tool.to_dict(), ensure_ascii=False)


# 模块级断言:防止字面量被误改
assert {s.value for s in SessionLockState} == {"free", "acquired", "waiting"}
