"""AgentService — wraps subagent_comms, session_lock, bubble_mode.

Exposes simple delegation to the underlying capability modules.
The complexity of session_lock (class-based) is encapsulated in this service.
"""

from __future__ import annotations

from .base import ServiceBase, ServiceMethod


def _load_subagent():
    from ..capability.subagent_comms import (
        broadcast,
        create_task,
        inbox,
        list_tasks,
        send_message,
    )

    return send_message, broadcast, inbox, create_task, list_tasks


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


def _get_bubble_mgr():
    from ..capability import bubble_mode as _bm

    if not hasattr(_bm, "_mgr"):
        from ..capability.bubble_mode import BubbleManager

        _bm._mgr = BubbleManager()
    return _bm._mgr


class AgentService(ServiceBase):
    name = "agent"
    description = "多 agent 协作: comms / session lock / bubble escalation / MCP tools"

    def _register_methods(self):
        # subagent comms
        self._methods["send_message"] = ServiceMethod(
            name="send_message",
            description="agent 间发送消息",
            func=self.send_message,
            input_required=["session_id", "to_session", "content"],
        )
        self._methods["broadcast"] = ServiceMethod(
            name="broadcast",
            description="广播到多个 session",
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
            description="创建 agent 任务",
            func=self.create_task,
            input_required=["session_id", "title"],
        )
        self._methods["list_tasks"] = ServiceMethod(
            name="list_tasks",
            description="列出 session 任务",
            func=self.list_tasks,
            input_required=["session_id"],
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
            description="注册 MCP tool",
            func=self.register_mcp,
            input_required=["name"],
            input_optional=["description", "parameters", "returns"],
        )
        self._methods["invoke_mcp"] = ServiceMethod(
            name="invoke_mcp",
            description="调用 MCP tool",
            func=self.invoke_mcp,
            input_required=["name"],
            input_optional=["kwargs"],
        )
        self._methods["list_mcp"] = ServiceMethod(
            name="list_mcp",
            description="列出所有 MCP tools",
            func=self.list_mcp,
        )
        # bubble
        self._methods["bubble_escalate"] = ServiceMethod(
            name="bubble_escalate",
            description="发起 bubble escalation",
            func=self.bubble_escalate,
            input_required=["parent_id", "agent_id", "action_desc", "reason"],
        )
        self._methods["bubble_pending"] = ServiceMethod(
            name="bubble_pending",
            description="查询 pending bubble",
            func=self.bubble_pending,
            input_required=["parent_id"],
        )
        self._methods["bubble_resolved"] = ServiceMethod(
            name="bubble_resolved",
            description="标记 bubble resolved",
            func=self.bubble_resolved,
            input_required=["parent_id"],
        )
        self._methods["bubble_schedule"] = ServiceMethod(
            name="bubble_schedule",
            description="调度 bubble 事件",
            func=self.bubble_schedule,
            input_required=["event_id", "event_type", "agent_id"],
            input_optional=["payload", "timestamp"],
        )
        self._methods["bubble_should_continue"] = ServiceMethod(
            name="bubble_should_continue",
            description="检查 agent 是否继续",
            func=self.bubble_should_continue,
            input_required=["agent_id"],
        )
        self._methods["bubble_recent"] = ServiceMethod(
            name="bubble_recent",
            description="最近 bubble 事件",
            func=self.bubble_recent,
            input_required=["agent_id"],
            input_optional=["n"],
        )

    # subagent
    def send_message(self, session_id, to_session, content, kind="send"):
        send, *_ = _load_subagent()
        return send(session_id=session_id, to_session=to_session, content=content, kind=kind)

    def broadcast(self, session_id, sessions, content):
        _, broadcast, *_ = _load_subagent()
        return broadcast(session_id=session_id, sessions=sessions, content=content)

    def inbox(self, session_id):
        _, _, inbox, *_ = _load_subagent()
        return inbox(session_id=session_id)

    def create_task(self, session_id, title):
        _, _, _, create_task, _ = _load_subagent()
        return create_task(session_id=session_id, title=title)

    def list_tasks(self, session_id):
        _, _, _, _, list_tasks = _load_subagent()
        return list_tasks(session_id=session_id)

    # session lock
    def try_acquire(self, lock_id, session_id, ttl=60.0):
        mgr = _get_lock_manager()
        return {"acquired": mgr.try_acquire(lock_id, session_id, ttl=ttl)}

    def release_lock(self, lock_id, session_id):
        mgr = _get_lock_manager()
        return {"released": mgr.release(lock_id, session_id)}

    def get_lock_state(self, lock_id):
        mgr = _get_lock_manager()
        lock = mgr.get_lock_state(lock_id)
        return {"lock": lock.__dict__ if lock else None}

    def acquire_with_wait(self, lock_id, session_id, timeout=10.0, retry_interval=0.01):
        mgr = _get_lock_manager()
        ok = mgr.acquire_with_wait(
            lock_id, session_id, timeout=timeout, retry_interval=retry_interval
        )
        return {"acquired": ok}

    def register_mcp(self, name, description="", parameters=None, returns="ok"):
        mcp = _get_mcp_registry()
        from ..capability.session_lock import MCPTool

        def handler(**kwargs):
            return returns if not callable(returns) else returns(**kwargs)

        tool = MCPTool(
            name=name,
            description=description,
            parameters=parameters or {},
            handler=handler,
            returns=str(returns),
        )
        return mcp.register(tool)

    def invoke_mcp(self, name, kwargs=None):
        mcp = _get_mcp_registry()
        return mcp.invoke(name, kwargs or {})

    def list_mcp(self):
        mcp = _get_mcp_registry()
        return {"tools": [t.to_dict() if hasattr(t, "to_dict") else str(t) for t in mcp.list_all()]}

    # bubble
    def bubble_escalate(self, parent_id, agent_id, action_desc, reason):
        mgr = _get_bubble_mgr()
        return mgr.escalate(
            parent_id=parent_id, agent_id=agent_id, action_desc=action_desc, reason=reason
        )

    def bubble_pending(self, parent_id):
        mgr = _get_bubble_mgr()
        return {"pending": mgr.pending(parent_id=parent_id)}

    def bubble_resolved(self, parent_id):
        mgr = _get_bubble_mgr()
        return {"resolved": mgr.resolved(parent_id=parent_id)}

    def bubble_schedule(self, event_id, event_type, agent_id, payload=None, timestamp=None):
        mgr = _get_bubble_mgr()
        return mgr.schedule_event(
            event_id=event_id,
            event_type=event_type,
            agent_id=agent_id,
            payload=payload or {},
            timestamp=timestamp or 0.0,
        )

    def bubble_should_continue(self, agent_id):
        mgr = _get_bubble_mgr()
        return {"should_continue": mgr.should_continue(agent_id=agent_id)}

    def bubble_recent(self, agent_id, n=5):
        mgr = _get_bubble_mgr()
        return {"recent": mgr.recent(agent_id=agent_id, n=n)}
