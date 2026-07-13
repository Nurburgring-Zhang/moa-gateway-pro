"""A-38 Subagent 通信 (SendMessage/TaskCreate) + A-22 Multi-session 协调 (advisory lock)

来源: 06 moai-adk-multiagent (subagent comms + advisory lock)

真实实现,非 mock,线程安全:
- SubagentHub 同步消息派发,inbox 按 to_session 隔离,kind 区分 send/broadcast/reply
- TaskBoard 父子任务树,status/assignee 过滤,JSON 序列化
- AdvisoryLock 单 holder + 3-retry / 10ms 退避,持锁超时保护
- 所有可变状态由 threading.RLock 守护,适配多 session 并发
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Literal


# ============ 数据类 ============

@dataclass
class Message:
    """subagent 之间传递的消息"""
    msg_id: str
    from_session: str
    to_session: str
    content: str
    timestamp: float
    kind: Literal["send", "broadcast", "reply"] = "send"
    parent_msg_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class TaskCreate:
    """任务描述,支持父子层级"""
    task_id: str
    title: str
    assignee_session: Optional[str] = None
    parent_task_id: Optional[str] = None
    status: Literal["pending", "in_progress", "completed", "failed"] = "pending"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ============ 状态合法的字面量集合(用于 update_status 校验) ============

_VALID_TASK_STATUS = {"pending", "in_progress", "completed", "failed"}
_VALID_MSG_KIND = {"send", "broadcast", "reply"}


# ============ SubagentHub ============

class SubagentHub:
    """同步消息总线:inbox 按 to_session 隔离,send_message 自动投递给收件人

    设计:
      - _inboxes: session -> List[Message](只追加)
      - send_message: 写收件人 inbox,返回新 Message
      - broadcast: 一次创建 N 条 Message(每条 to_session 不同),全返回
      - reply: 找到 parent_msg_id,自动在 reply 上挂 parent_msg_id
      - inbox: 返回本 session 的消息快照(防止外部直接改内部 list)
      - 全部操作由 _lock 守护
    """

    def __init__(self, session_id: str) -> None:
        self.session_id: str = session_id
        self._inboxes: Dict[str, List[Message]] = {session_id: []}
        self._outbox: List[Message] = []  # 本 session 主动发出的(便于回溯/调试)
        self._lock: threading.RLock = threading.RLock()

    # ---- 内部 ----

    def _register(self, session: str) -> None:
        if session not in self._inboxes:
            self._inboxes[session] = []

    def _new_msg(
        self,
        to_session: str,
        content: str,
        kind: str,
        parent_msg_id: Optional[str] = None,
    ) -> Message:
        if kind not in _VALID_MSG_KIND:
            raise ValueError(f"invalid kind: {kind!r}; must be one of {_VALID_MSG_KIND}")
        return Message(
            msg_id=f"m_{uuid.uuid4().hex[:12]}",
            from_session=self.session_id,
            to_session=to_session,
            content=content,
            timestamp=time.time(),
            kind=kind,
            parent_msg_id=parent_msg_id,
        )

    # ---- 公共 API ----

    def send_message(
        self,
        to_session: str,
        content: str,
        kind: str = "send",
    ) -> Message:
        """发送单条消息 → 写入收件人 inbox,返回新 Message

        kind 默认 "send";若显式 "reply" 需配合 reply() 使用,这里仅校验字面量
        """
        with self._lock:
            self._register(to_session)
            msg = self._new_msg(to_session=to_session, content=content, kind=kind)
            self._inboxes[to_session].append(msg)
            self._outbox.append(msg)
            return msg

    def broadcast(self, sessions: List[str], content: str) -> List[Message]:
        """广播:为每个 session 生成一条独立 Message(便于单独 ack / reply)

        sessions 允许包含 sender 自身(自身也会收到一份)
        """
        if not sessions:
            return []
        with self._lock:
            results: List[Message] = []
            for s in sessions:
                self._register(s)
                msg = self._new_msg(to_session=s, content=content, kind="broadcast")
                self._inboxes[s].append(msg)
                results.append(msg)
            self._outbox.extend(results)
            return results

    def inbox(self) -> List[Message]:
        """返回本 session 当前 inbox 的快照(不暴露内部 list 引用)"""
        with self._lock:
            return list(self._inboxes.get(self.session_id, []))

    def reply(self, parent_msg_id: str, content: str) -> Message:
        """回复 parent_msg_id:在 to_session 上找到原消息的 from_session(回寄)

        若父消息不存在(self inbox 内找不到对应 parent_msg_id),仍允许发送,但
        返回的 Message.parent_msg_id 仍为用户传入的值(便于上层重建线程)
        """
        with self._lock:
            my_inbox = self._inboxes.get(self.session_id, [])
            target: Optional[str] = None
            for m in my_inbox:
                if m.msg_id == parent_msg_id:
                    target = m.from_session
                    break
            if target is None:
                # 父消息不可见:依旧发,目标用父 ID 的字面前缀(便于排查)
                target = f"unknown:{parent_msg_id[:8]}"
            self._register(target)
            msg = self._new_msg(
                to_session=target,
                content=content,
                kind="reply",
                parent_msg_id=parent_msg_id,
            )
            self._inboxes[target].append(msg)
            self._outbox.append(msg)
            return msg

    def deliver(self, msg: Message) -> None:
        """外部消息直接投递给指定 session(便于跨进程/跨 hub 集成场景)

        通常由更上层 router 调用;此处只做 inbox 追加
        """
        with self._lock:
            self._register(msg.to_session)
            self._inboxes[msg.to_session].append(msg)

    def sessions(self) -> List[str]:
        """列出所有已知 session(用于诊断/UI)"""
        with self._lock:
            return list(self._inboxes.keys())

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "session_id": self.session_id,
                "sessions": list(self._inboxes.keys()),
                "inbox_count": len(self._inboxes.get(self.session_id, [])),
                "outbox_count": len(self._outbox),
            }


# ============ TaskBoard ============

class TaskBoard:
    """任务板:父子任务树 + status/assignee 过滤

    设计:
      - _tasks: task_id -> TaskCreate(全量)
      - create_task 自动生成 task_id 并注册
      - update_status 校验 status 合法
      - list_tasks 支持 status / assignee 单独或组合过滤
      - get_subtasks 用 parent_task_id 索引(O(N) 一次,小规模可接受)
    """

    def __init__(self, session_id: str) -> None:
        self.session_id: str = session_id
        self._tasks: Dict[str, TaskCreate] = {}
        self._lock: threading.RLock = threading.RLock()

    def create_task(
        self,
        title: str,
        assignee: Optional[str] = None,
        parent: Optional[str] = None,
    ) -> str:
        """创建任务,返回 task_id"""
        with self._lock:
            if parent is not None and parent not in self._tasks:
                raise KeyError(f"parent task not found: {parent!r}")
            task_id = f"t_{uuid.uuid4().hex[:12]}"
            t = TaskCreate(
                task_id=task_id,
                title=title,
                assignee_session=assignee,
                parent_task_id=parent,
                status="pending",
                created_at=time.time(),
            )
            self._tasks[task_id] = t
            return task_id

    def update_status(self, task_id: str, status: str) -> None:
        """更新任务状态;status 必须合法;task_id 必须存在"""
        if status not in _VALID_TASK_STATUS:
            raise ValueError(
                f"invalid status: {status!r}; must be one of {_VALID_TASK_STATUS}"
            )
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                raise KeyError(f"task not found: {task_id!r}")
            t.status = status

    def get_task(self, task_id: str) -> Optional[TaskCreate]:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(
        self,
        status: Optional[str] = None,
        assignee: Optional[str] = None,
    ) -> List[TaskCreate]:
        """按 status / assignee 过滤;都为 None 时返回全部(按 created_at 升序)"""
        with self._lock:
            results = list(self._tasks.values())
            if status is not None:
                results = [t for t in results if t.status == status]
            if assignee is not None:
                results = [t for t in results if t.assignee_session == assignee]
            results.sort(key=lambda t: t.created_at)
            return results

    def get_subtasks(self, parent_task_id: str) -> List[TaskCreate]:
        """取所有 parent_task_id == parent_task_id 的子任务(按 created_at 升序)"""
        with self._lock:
            return sorted(
                [t for t in self._tasks.values() if t.parent_task_id == parent_task_id],
                key=lambda t: t.created_at,
            )

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "session_id": self.session_id,
                "task_count": len(self._tasks),
                "tasks": [t.to_dict() for t in self._tasks.values()],
            }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ============ AdvisoryLock ============

# 退避:第 1 次失败 → 等 10ms, 第 2 次 → 20ms, 第 3 次 → 40ms(指数)
BACKOFF_BASE_SEC: float = 0.010
MAX_RETRY: int = 3


class AdvisoryLock:
    """进程内 advisory lock(同 lock_id 单 holder)

    设计:
      - _locks[lock_id] 持有当前 holder;无则为空
      - acquire: 最多重试 3 次,每次失败 sleep 退避(10ms, 20ms, 40ms)
      - release: 仅 holder 能释放;释放后置空
      - is_held: 快照读取(不持锁,只读引用,Python 原子赋值足够)
      - timeout: 总占用时间上限(秒);超过视为泄漏,自动 force-release
        —— 避免持锁线程卡死后锁永远拿不回来
    """

    _registry: Dict[str, "AdvisoryLock"] = {}
    _registry_lock: threading.RLock = threading.RLock()

    def __init__(self, lock_id: str, holder: str, timeout: float = 10.0) -> None:
        if not lock_id:
            raise ValueError("lock_id must be non-empty")
        if not holder:
            raise ValueError("holder must be non-empty")
        if timeout <= 0:
            raise ValueError("timeout must be > 0")
        self.lock_id: str = lock_id
        self.holder: str = holder
        self.timeout: float = float(timeout)
        self._acquired_at: Optional[float] = None
        # 同一 lock_id 在同进程内只允许一个实例
        with AdvisoryLock._registry_lock:
            if lock_id in AdvisoryLock._registry:
                raise ValueError(
                    f"AdvisoryLock for lock_id={lock_id!r} already exists in this process"
                )
            AdvisoryLock._registry[lock_id] = self

    @classmethod
    def get(cls, lock_id: str) -> Optional["AdvisoryLock"]:
        with cls._registry_lock:
            return cls._registry.get(lock_id)

    def acquire(self) -> bool:
        """尝试获取锁;3-retry / 10ms 退避;返回是否成功

        行为细节:
          1. 先检查自己是否已经持锁 —— 是则直接返回 True(重入语义)
          2. 当前 holder 为空:直接占锁
          3. 当前 holder 存在但已超时(超过 self.timeout 秒):强制夺锁(force)
          4. 否则按指数退避重试,直到 3 次都失败 → 返回 False
        """
        for attempt in range(MAX_RETRY):
            now = time.time()
            current = self._current_holder()
            if current is None:
                # 槽位空
                if self._try_claim(now):
                    return True
            elif current == self.holder:
                # 同一 holder 重入
                return True
            else:
                # 别人持锁
                ts = self._acquired_at_snapshot()
                st = AdvisoryLock._state(self.lock_id)
                holder_timeout = st.get("timeout")
                # 持锁者自己的 timeout 是判定基准;无值则用 self.timeout
                effective_timeout = holder_timeout if holder_timeout is not None else self.timeout
                if ts is not None and (now - ts) >= effective_timeout:
                    # 持锁超时 → 强制夺锁
                    if self._force_claim(now):
                        return True
            # 本轮没拿到 → 退避(最后一次不再 sleep,直接退出)
            if attempt < MAX_RETRY - 1:
                time.sleep(BACKOFF_BASE_SEC * (2 ** attempt))
        return False

    def release(self) -> bool:
        """释放锁;仅 holder 能成功释放;返回是否真的释放了"""
        with self._registry_lock:
            entry = AdvisoryLock._registry.get(self.lock_id)
            if entry is not self:
                return False
        current = self._current_holder()
        if current != self.holder:
            return False
        self._set_holder(None, None)
        return True

    def is_held(self) -> bool:
        return self._current_holder() is not None

    def is_held_by_me(self) -> bool:
        return self._current_holder() == self.holder

    def held_by(self) -> Optional[str]:
        return self._current_holder()

    # ---- 内部(进程级共享状态) ----

    @staticmethod
    def _state(lock_id: str) -> Dict[str, Any]:
        """返回进程级共享 holder 槽位(每个 lock_id 独立 dict)"""
        # 借用 registry 锁保证 _state dict 自身的创建/读取原子
        with AdvisoryLock._registry_lock:
            if lock_id not in _LOCK_STATE:
                _LOCK_STATE[lock_id] = {"holder": None, "acquired_at": None, "timeout": None}
            return _LOCK_STATE[lock_id]

    def _current_holder(self) -> Optional[str]:
        return AdvisoryLock._state(self.lock_id)["holder"]

    def _acquired_at_snapshot(self) -> Optional[float]:
        return AdvisoryLock._state(self.lock_id)["acquired_at"]

    def _try_claim(self, now: float) -> bool:
        """原子地检查并占槽;成功返回 True"""
        with AdvisoryLock._registry_lock:
            st = AdvisoryLock._state(self.lock_id)
            if st["holder"] is not None:
                return False
            st["holder"] = self.holder
            st["acquired_at"] = now
            st["timeout"] = self.timeout
            self._acquired_at = now
            return True

    def _force_claim(self, now: float) -> bool:
        """强制夺锁(原 holder 视为泄漏);返回 True"""
        with AdvisoryLock._registry_lock:
            st = AdvisoryLock._state(self.lock_id)
            st["holder"] = self.holder
            st["acquired_at"] = now
            st["timeout"] = self.timeout
            self._acquired_at = now
            return True

    def _set_holder(self, holder: Optional[str], acquired_at: Optional[float]) -> None:
        with AdvisoryLock._registry_lock:
            st = AdvisoryLock._state(self.lock_id)
            st["holder"] = holder
            st["acquired_at"] = acquired_at
            if acquired_at is None:
                st["timeout"] = None
            self._acquired_at = acquired_at


# 进程级共享 holder 槽位
_LOCK_STATE: Dict[str, Dict[str, Any]] = {}
_LOCK_STATE_LOCK = threading.RLock()


# ============ 顶层 JSON 序列化辅助 ============

def hub_to_json(hub: SubagentHub) -> str:
    return json.dumps(hub.to_dict(), ensure_ascii=False)


def task_to_json(t: TaskCreate) -> str:
    return t.to_json()


def message_to_json(m: Message) -> str:
    return m.to_json()


# 模块级断言:防止 Literal 集合被误改
assert _VALID_MSG_KIND == {"send", "broadcast", "reply"}
assert _VALID_TASK_STATUS == {"pending", "in_progress", "completed", "failed"}
