"""AgentService — wraps subagent_comms, session_lock, bubble_mode.

Exposes simple delegation to the underlying capability modules.
The complexity of session_lock (class-based) is encapsulated in this service.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict

from .base import ServiceBase, ServiceMethod

logger = logging.getLogger(__name__)


# Audit fix: subagent_comms exposes SubagentHub / TaskBoard classes (there are
# no module-level send_message/broadcast/inbox/create_task/list_tasks).
# Keep per-session hubs and task boards so state flows across calls.
_subagent_hubs: dict[str, object] = {}
_task_boards: dict[str, object] = {}


def _load_subagent():
    from ..capability.subagent_comms import (
        Message,
        SubagentHub,
        TaskBoard,
        message_to_json,
        task_to_json,
    )

    return SubagentHub, TaskBoard, Message, message_to_json, task_to_json


def _get_hub(session_id: str):
    SubagentHub, *_ = _load_subagent()
    hub = _subagent_hubs.get(session_id)
    if hub is None:
        hub = SubagentHub(session_id=str(session_id))
        _subagent_hubs[session_id] = hub
    return hub


def _get_board(session_id: str):
    _, TaskBoard, *_ = _load_subagent()
    board = _task_boards.get(session_id)
    if board is None:
        board = TaskBoard(session_id=str(session_id))
        _task_boards[session_id] = board
    return board


def _get_lock_manager():
    """Get singleton SessionLockManager from the module."""
    from ..capability import session_lock as _sl_module

    if not hasattr(_sl_module, "_mgr"):
        from ..capability.session_lock import SessionLockManager

        _sl_module._mgr = SessionLockManager()
    return _sl_module._mgr


def _get_mcp_registry():
    from ..capability import session_lock as _sl_module

    if not hasattr(_sl_module, "_mcp"):
        from ..capability.session_lock import MCPRegistry

        _sl_module._mcp = MCPRegistry()
    return _sl_module._mcp


# Audit fix: BubbleManager requires a parent_id; EventScheduler owns the
# schedule/should_continue/recent primitives. Keep one per parent.
_bubble_managers: dict[str, object] = {}
_event_scheduler = None


def _get_bubble_mgr(parent_id: str):
    from ..capability.bubble_mode import BubbleManager

    mgr = _bubble_managers.get(parent_id)
    if mgr is None:
        mgr = BubbleManager(parent_id=str(parent_id))
        _bubble_managers[parent_id] = mgr
    return mgr


def _get_event_scheduler():
    global _event_scheduler
    from ..capability.bubble_mode import EventScheduler

    if _event_scheduler is None:
        _event_scheduler = EventScheduler()
    return _event_scheduler


def _escalation_to_dict(req) -> dict:
    d = asdict(req)
    if hasattr(d.get("status"), "value"):
        d["status"] = d["status"].value
    return d


class AgentService(ServiceBase):
    name = "agent"
    description = "多 agent 协作: comms / session lock / bubble escalation / MCP tools"

    def _register_methods(self):
        # subagent comms
        self._methods["send_message"] = ServiceMethod(
            name="send_message",
            description="agent 间发送消息 (SubagentHub.send_message, 自动投递到收件方 inbox)",
            func=self.send_message,
            input_required=["session_id", "to_session", "content"],
            input_optional=["kind"],
        )
        self._methods["broadcast"] = ServiceMethod(
            name="broadcast",
            description="广播到多个 session (SubagentHub.broadcast)",
            func=self.broadcast,
            input_required=["session_id", "sessions", "content"],
        )
        self._methods["inbox"] = ServiceMethod(
            name="inbox",
            description="读取 session inbox",
            func=self.inbox,
            input_required=["session_id"],
        )
        self._methods["create_task"] = ServiceMethod(
            name="create_task",
            description="创建 agent 任务 (TaskBoard.create_task)",
            func=self.create_task,
            input_required=["session_id", "title"],
            input_optional=["assignee", "parent"],
        )
        self._methods["list_tasks"] = ServiceMethod(
            name="list_tasks",
            description="列出 session 任务 (TaskBoard.list_tasks)",
            func=self.list_tasks,
            input_required=["session_id"],
            input_optional=["status", "assignee"],
        )
        # session lock
        self._methods["try_acquire"] = ServiceMethod(
            name="try_acquire",
            description="尝试获取 lock",
            func=self.try_acquire,
            input_required=["lock_id", "session_id"],
            input_optional=["ttl"],
        )
        self._methods["release_lock"] = ServiceMethod(
            name="release_lock",
            description="释放 lock",
            func=self.release_lock,
            input_required=["lock_id", "session_id"],
        )
        self._methods["get_lock_state"] = ServiceMethod(
            name="get_lock_state",
            description="查询 lock 状态",
            func=self.get_lock_state,
            input_required=["lock_id"],
        )
        self._methods["acquire_with_wait"] = ServiceMethod(
            name="acquire_with_wait",
            description="阻塞等待获取 lock",
            func=self.acquire_with_wait,
            input_required=["lock_id", "session_id"],
            input_optional=["timeout", "retry_interval"],
        )
        self._methods["register_mcp"] = ServiceMethod(
            name="register_mcp",
            description="注册 MCP tool (handler 返回 returns 模板值)",
            func=self.register_mcp,
            input_required=["name"],
            input_optional=["description", "parameters", "returns"],
        )
        self._methods["invoke_mcp"] = ServiceMethod(
            name="invoke_mcp",
            description="调用 MCP tool (MCPRegistry.invoke(name, **kwargs))",
            func=self.invoke_mcp,
            input_required=["name"],
            input_optional=["kwargs"],
        )
        self._methods["list_mcp"] = ServiceMethod(
            name="list_mcp",
            description="列出所有 MCP tools (MCPRegistry.list_tools)",
            func=self.list_mcp,
        )
        # bubble
        self._methods["bubble_escalate"] = ServiceMethod(
            name="bubble_escalate",
            description="发起 bubble escalation (BubbleManager(parent).escalate)",
            func=self.bubble_escalate,
            input_required=["parent_id", "agent_id", "action_desc", "reason"],
        )
        self._methods["bubble_pending"] = ServiceMethod(
            name="bubble_pending",
            description="查询 parent 的 pending bubble",
            func=self.bubble_pending,
            input_required=["parent_id"],
        )
        self._methods["bubble_resolved"] = ServiceMethod(
            name="bubble_resolved",
            description="标记 bubble resolved (给定 request_id 则解析单条, 否则解析该 parent 全部 pending)",
            func=self.bubble_resolved,
            input_required=["parent_id"],
            input_optional=["request_id", "status", "note"],
        )
        self._methods["bubble_schedule"] = ServiceMethod(
            name="bubble_schedule",
            description="调度 bubble 事件 (EventScheduler.schedule; event_type: trigger/neutral/terminal)",
            func=self.bubble_schedule,
            input_required=["event_id", "event_type", "agent_id"],
            input_optional=["payload", "timestamp"],
        )
        self._methods["bubble_should_continue"] = ServiceMethod(
            name="bubble_should_continue",
            description="检查 agent 是否继续 (EventScheduler.should_continue)",
            func=self.bubble_should_continue,
            input_required=["agent_id"],
        )
        self._methods["bubble_recent"] = ServiceMethod(
            name="bubble_recent",
            description="最近 bubble 事件 (EventScheduler.recent_events)",
            func=self.bubble_recent,
            input_required=["agent_id"],
            input_optional=["n"],
        )

    # subagent
    def send_message(self, session_id, to_session, content, kind="send"):
        # Audit fix: drive SubagentHub instances (module-level functions never
        # existed). The message is also delivered into the recipient's own hub
        # so inbox(to_session) sees it.
        _, _, _, message_to_json, _ = _load_subagent()
        hub = _get_hub(session_id)
        msg = hub.send_message(to_session=str(to_session), content=str(content), kind=kind)
        if str(to_session) != str(session_id):
            _get_hub(to_session).deliver(msg)
        return json.loads(message_to_json(msg))

    def broadcast(self, session_id, sessions, content):
        _, _, _, message_to_json, _ = _load_subagent()
        hub = _get_hub(session_id)
        msgs = hub.broadcast(sessions=[str(s) for s in (sessions or [])], content=str(content))
        for m in msgs:
            if m.to_session != str(session_id):
                _get_hub(m.to_session).deliver(m)
        return {"messages": [json.loads(message_to_json(m)) for m in msgs], "count": len(msgs)}

    def inbox(self, session_id):
        _, _, _, message_to_json, _ = _load_subagent()
        hub = _get_hub(session_id)
        return {"messages": [json.loads(message_to_json(m)) for m in hub.inbox()]}

    def create_task(self, session_id, title, assignee=None, parent=None):
        # Audit fix: drive TaskBoard(session_id).create_task.
        _, _, _, _, task_to_json = _load_subagent()
        board = _get_board(session_id)
        task_id = board.create_task(title=str(title), assignee=assignee, parent=parent)
        task = board.get_task(task_id)
        out = {"task_id": task_id}
        if task is not None:
            out["task"] = json.loads(task_to_json(task))
        return out

    def list_tasks(self, session_id, status=None, assignee=None):
        _, _, _, _, task_to_json = _load_subagent()
        board = _get_board(session_id)
        tasks = board.list_tasks(status=status, assignee=assignee)
        return {"tasks": [json.loads(task_to_json(t)) for t in tasks], "count": len(tasks)}

    # session lock
    def try_acquire(self, lock_id, session_id, ttl=60.0):
        mgr = _get_lock_manager()
        return {"acquired": mgr.try_acquire(lock_id, session_id, ttl=ttl)}

    def release_lock(self, lock_id, session_id):
        mgr = _get_lock_manager()
        return {"released": mgr.release(lock_id, session_id)}

    def get_lock_state(self, lock_id):
        # Audit fix: serialize via lock_to_json (SessionLock.__dict__ carries
        # enum state that is not JSON-safe).
        from ..capability.session_lock import lock_to_json

        mgr = _get_lock_manager()
        lock = mgr.get_lock_state(lock_id)
        return {"lock": json.loads(lock_to_json(lock)) if lock else None}

    def acquire_with_wait(self, lock_id, session_id, timeout=10.0, retry_interval=0.01):
        mgr = _get_lock_manager()
        ok = mgr.acquire_with_wait(
            lock_id, session_id, timeout=timeout, retry_interval=retry_interval
        )
        return {"acquired": ok}

    def register_mcp(self, name, description="", parameters=None, returns="ok"):
        # Audit fix: MCPTool has fields (name, description, parameters,
        # handler) — there is no `returns` field.
        from ..capability.session_lock import MCPTool

        mcp = _get_mcp_registry()

        def handler(**kwargs):
            return returns if not callable(returns) else returns(**kwargs)

        tool = MCPTool(
            name=str(name),
            description=str(description or ""),
            parameters={k: str(v) for k, v in (parameters or {}).items()},
            handler=handler,
        )
        mcp.register(tool)
        return {"registered": True, "name": str(name), "tools": mcp.list_tools()}

    def invoke_mcp(self, name, kwargs=None):
        # Audit fix: MCPRegistry.invoke(name, **kwargs) — kwargs must be
        # expanded, not passed as a single dict.
        mcp = _get_mcp_registry()
        result = mcp.invoke(str(name), **(kwargs or {}))
        return {"result": result}

    def list_mcp(self):
        # Audit fix: MCPRegistry.list_tools() returns list[dict] (list_all
        # never existed).
        mcp = _get_mcp_registry()
        return {"tools": mcp.list_tools()}

    # bubble
    def bubble_escalate(self, parent_id, agent_id, action_desc, reason):
        # Audit fix: BubbleManager is per-parent; escalate(agent_id, action,
        # reason) returns a request_id.
        mgr = _get_bubble_mgr(parent_id)
        request_id = mgr.escalate(agent_id=str(agent_id), action=str(action_desc), reason=str(reason))
        return {"request_id": request_id, "parent_id": str(parent_id), "status": "escalated"}

    def bubble_pending(self, parent_id):
        mgr = _get_bubble_mgr(parent_id)
        pending = mgr.get_pending()
        return {"pending": [_escalation_to_dict(r) for r in pending], "count": len(pending)}

    def bubble_resolved(self, parent_id, request_id=None, status="allowed", note=""):
        # Audit fix: resolution is per-request via BubbleManager.resolve.
        from ..capability.bubble_mode import BubbleStatus

        try:
            final_status = BubbleStatus(status)
        except ValueError as e:
            valid = [s.value for s in BubbleStatus]
            raise ValueError(f"unknown status: {status!r}, expected one of {valid}") from e
        mgr = _get_bubble_mgr(parent_id)
        if request_id:
            ok = mgr.resolve(str(request_id), final_status, resolver_note=str(note or ""))
            return {"resolved": 1 if ok else 0, "request_id": str(request_id)}
        resolved_count = 0
        for req in list(mgr.get_pending()):
            if mgr.resolve(req.request_id, final_status, resolver_note=str(note or "")):
                resolved_count += 1
        return {"resolved": resolved_count}

    def bubble_schedule(self, event_id, event_type, agent_id, payload=None, timestamp=None):
        # Audit fix: scheduling lives on EventScheduler (not BubbleManager).
        from ..capability.bubble_mode import Event, EventType

        try:
            etype = EventType(str(event_type))
        except ValueError as e:
            valid = [t.value for t in EventType]
            raise ValueError(f"unknown event_type: {event_type!r}, expected one of {valid}") from e
        scheduler = _get_event_scheduler()
        event = Event(
            event_id=str(event_id),
            event_type=etype,
            agent_id=str(agent_id),
            payload=dict(payload or {}),
            timestamp=float(timestamp) if timestamp else time.time(),
        )
        scheduled_id = scheduler.schedule(event)
        return {"scheduled": scheduled_id, "event_count": scheduler.event_count(str(agent_id))}

    def bubble_should_continue(self, agent_id):
        scheduler = _get_event_scheduler()
        return {"should_continue": scheduler.should_continue(str(agent_id))}

    def bubble_recent(self, agent_id, n=5):
        scheduler = _get_event_scheduler()
        events = scheduler.recent_events(str(agent_id), n=int(n))
        serialized = []
        for e in events:
            d = asdict(e)
            if hasattr(d.get("event_type"), "value"):
                d["event_type"] = d["event_type"].value
            serialized.append(d)
        return {"recent": serialized, "count": len(serialized)}
