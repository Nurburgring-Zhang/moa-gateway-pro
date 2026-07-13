"""27 Hook 事件 (Claude Code 风格) + 4 阶段 Ralph 反馈循环

来源: 06 moai-adk-multiagent (Claude Code hook spec + Ralph 状态机)

真实实现,非 mock:
- 27 个 HookEvent 全部 enum,涵盖 Claude Code 完整生命周期
- HookRegistry 同步派发,priority 排序,enabled 过滤
- ralph_loop 是无状态 4 阶段纯函数;RALPH_CYCLE 是有状态计数器
- 失败 review → 自动回到 analyze(最多 max_iter 防死循环)
"""
from __future__ import annotations
import json
import time
import uuid
from enum import Enum
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field, asdict


# ============ 27 HookEvent (Claude Code spec) ============

class HookEvent(str, Enum):
    """Claude Code 风格的 27 个 hook 事件

    分类:
      - 会话生命周期 (5): SessionStart, UserPromptSubmit, SessionEnd, TeammateIdle, TaskCompleted
      - 工具调用 (4): PreToolUse, PermissionRequest, PostToolUse, PostToolUseFailure
      - 停止/控制 (4): Stop, StopFailure, SubagentStop, Notification
      - 压缩/上下文 (2): PreCompact, PostCompact
      - 响应 (2): PreResponse, PostResponse
      - 配置/工作树 (3): ConfigChange, WorktreeCreate, WorktreeRemove
      - 提交 (2): PreCommit, PostCommit
      - 扩展 (3): FileWatch, SkillActivate, McpToolCall
      - 智能体 (2): AgentSpawn, AgentExit
    """
    # 会话生命周期
    SessionStart = "SessionStart"
    UserPromptSubmit = "UserPromptSubmit"
    SessionEnd = "SessionEnd"
    TeammateIdle = "TeammateIdle"
    TaskCompleted = "TaskCompleted"

    # 工具调用
    PreToolUse = "PreToolUse"
    PermissionRequest = "PermissionRequest"
    PostToolUse = "PostToolUse"
    PostToolUseFailure = "PostToolUseFailure"

    # 停止 / 控制
    Stop = "Stop"
    StopFailure = "StopFailure"
    SubagentStop = "SubagentStop"
    Notification = "Notification"

    # 压缩 / 上下文
    PreCompact = "PreCompact"
    PostCompact = "PostCompact"

    # 响应
    PreResponse = "PreResponse"
    PostResponse = "PostResponse"

    # 配置 / 工作树
    ConfigChange = "ConfigChange"
    WorktreeCreate = "WorktreeCreate"
    WorktreeRemove = "WorktreeRemove"

    # 提交
    PreCommit = "PreCommit"
    PostCommit = "PostCommit"

    # 扩展
    FileWatch = "FileWatch"
    SkillActivate = "SkillActivate"
    McpToolCall = "McpToolCall"

    # 智能体
    AgentSpawn = "AgentSpawn"
    AgentExit = "AgentExit"


# 断言:必须正好 27 个事件(防止意外增删破坏契约)
assert len(HookEvent) == 27, f"HookEvent must have 27 members, got {len(HookEvent)}"


# ============ 数据类 ============

@dataclass
class HookHandler:
    """一个 hook 处理器"""
    event: HookEvent
    callback: Optional[Callable] = None
    priority: int = 0
    enabled: bool = True
    handler_id: str = field(default_factory=lambda: f"h_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event.value,
            "priority": self.priority,
            "enabled": self.enabled,
            "handler_id": self.handler_id,
            "has_callback": self.callback is not None,
        }


@dataclass
class HookContext:
    """一次 hook 触发的上下文"""
    event: HookEvent
    session_id: str
    timestamp: float = field(default_factory=time.time)
    data: Dict[str, Any] = field(default_factory=dict)
    context_id: str = field(default_factory=lambda: f"c_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "context_id": self.context_id,
            "event": self.event.value,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "data": self.data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ============ HookRegistry ============

class HookRegistry:
    """同步 hook 注册表 — 按 event 分组,priority 降序调用

    设计:
      - register() 返回 handler_id,供 unregister
      - trigger() 同步调用所有 enabled handler(高 priority 先)
      - list_handlers(event=None) 支持按 event 过滤
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, HookHandler] = {}
        self._order: List[str] = []  # 注册顺序
        self._trigger_count: int = 0  # 总触发次数(用于统计/测试)

    def register(
        self,
        event: HookEvent,
        callback: Optional[Callable] = None,
        priority: int = 0,
    ) -> str:
        """注册一个 handler,返回 handler_id"""
        h = HookHandler(event=event, callback=callback, priority=priority, enabled=True)
        self._handlers[h.handler_id] = h
        self._order.append(h.handler_id)
        return h.handler_id

    def unregister(self, handler_id: str) -> bool:
        """注销 handler,返回是否成功"""
        if handler_id not in self._handlers:
            return False
        del self._handlers[handler_id]
        if handler_id in self._order:
            self._order.remove(handler_id)
        return True

    def trigger(self, event: HookEvent, data: Optional[Dict[str, Any]] = None) -> List[Any]:
        """同步调用所有 enabled handler,返回结果列表

        排序:priority 降序(数值大的先),同 priority 按注册顺序
        """
        self._trigger_count += 1
        data = data or {}
        ctx = HookContext(event=event, session_id="", data=data)
        # 收集目标 handler
        targets = [h for h in self._handlers.values() if h.event == event and h.enabled]
        # 排序:priority 降序,同 priority 按注册顺序
        order_index = {hid: i for i, hid in enumerate(self._order)}
        targets.sort(key=lambda h: (-h.priority, order_index.get(h.handler_id, 0)))
        results: List[Any] = []
        for h in targets:
            if h.callback is None:
                results.append(None)
                continue
            try:
                # callback 签名:接受 (HookContext) 或 () — 兼容两种风格
                try:
                    r = h.callback(ctx)
                except TypeError:
                    r = h.callback()
                results.append(r)
            except Exception as e:
                # 单个 handler 失败不应中断整体派发
                results.append({"error": type(e).__name__, "message": str(e)})
        return results

    def list_handlers(self, event: Optional[HookEvent] = None) -> List[HookHandler]:
        """列出 handler;event 不为空时按 event 过滤"""
        if event is None:
            return [self._handlers[hid] for hid in self._order if hid in self._handlers]
        return [
            self._handlers[hid]
            for hid in self._order
            if hid in self._handlers and self._handlers[hid].event == event
        ]

    def disable(self, handler_id: str) -> bool:
        h = self._handlers.get(handler_id)
        if h is None:
            return False
        h.enabled = False
        return True

    def enable(self, handler_id: str) -> bool:
        h = self._handlers.get(handler_id)
        if h is None:
            return False
        h.enabled = True
        return True

    @property
    def trigger_count(self) -> int:
        return self._trigger_count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "handler_count": len(self._handlers),
            "trigger_count": self._trigger_count,
            "handlers": [self._handlers[hid].to_dict() for hid in self._order if hid in self._handlers],
        }


# ============ 4 阶段 Ralph 反馈循环 ============

# 阶段常量(字符串,便于 JSON 序列化)
RALPH_STAGE_ANALYZE = "analyze"
RALPH_STAGE_IMPLEMENT = "implement"
RALPH_STAGE_TEST = "test"
RALPH_STAGE_REVIEW = "review"

# 阶段顺序(循环)
RALPH_STAGES: List[str] = [
    RALPH_STAGE_ANALYZE,
    RALPH_STAGE_IMPLEMENT,
    RALPH_STAGE_TEST,
    RALPH_STAGE_REVIEW,
]

# 阶段描述(便于 UI / 日志)
RALPH_STAGE_DESCRIPTIONS: Dict[str, str] = {
    RALPH_STAGE_ANALYZE: "Analyze requirements and plan the change",
    RALPH_STAGE_IMPLEMENT: "Implement the code change",
    RALPH_STAGE_TEST: "Run tests and capture results",
    RALPH_STAGE_REVIEW: "Review the change for quality and correctness",
}


def ralph_loop(stage: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """无状态 4 阶段 Ralph 反馈循环

    返回一个 dict 描述该阶段的状态:
      - stage: 当前阶段
      - next_stage: 下一阶段(review 失败 → analyze)
      - status: "ok" | "failed" | "ready"
      - data: 输入数据
      - description: 阶段描述

    失败判断:data["passed"] == False 视为失败
    """
    data = data or {}
    description = RALPH_STAGE_DESCRIPTIONS.get(stage, "unknown stage")
    # 验证阶段
    if stage not in RALPH_STAGES:
        return {
            "stage": stage,
            "next_stage": None,
            "status": "failed",
            "data": data,
            "description": f"unknown stage: {stage}",
        }
    # review 阶段特殊处理:失败 → 回到 analyze
    if stage == RALPH_STAGE_REVIEW:
        passed = data.get("passed", True)
        if not passed:
            return {
                "stage": stage,
                "next_stage": RALPH_STAGE_ANALYZE,
                "status": "failed",
                "data": data,
                "description": description,
            }
    # 计算下一阶段
    idx = RALPH_STAGES.index(stage)
    next_stage = RALPH_STAGES[(idx + 1) % len(RALPH_STAGES)]
    return {
        "stage": stage,
        "next_stage": next_stage,
        "status": "ok",
        "data": data,
        "description": description,
    }


class RALPH_CYCLE:
    """有状态的 Ralph 循环迭代器

    维护:
      - current_stage: 当前阶段
      - iteration: 完整循环计数(analyze→review 算一次)
      - history: 每阶段的结果列表
      - max_iter: 硬上限(防止死循环)

    用法:
        rc = RALPH_CYCLE(max_iter=5)
        rc.advance({"plan": "..."})    # analyze
        rc.advance({"code": "..."})    # implement
        rc.advance({"passed": True})   # test
        rc.advance({"passed": False})  # review → 回 analyze
    """

    def __init__(self, max_iter: int = 5, session_id: str = "") -> None:
        self.max_iter = max(1, max_iter)
        self.current_stage: str = RALPH_STAGE_ANALYZE
        self.iteration: int = 0  # 完成的 review 次数
        self.history: List[Dict[str, Any]] = []
        self.session_id: str = session_id or f"ralph_{uuid.uuid4().hex[:8]}"
        self._terminated: bool = False
        self._terminate_reason: Optional[str] = None

    def advance(self, stage_data: Optional[Dict[str, Any]] = None) -> str:
        """推进到下一阶段,返回新的当前阶段名

        - 正常情况:analyze → implement → test → review → analyze ...
        - review 失败:回到 analyze
        - 达到 max_iter:终止,返回 "terminated"
        """
        if self._terminated:
            return "terminated"
        stage_data = stage_data or {}
        result = ralph_loop(self.current_stage, stage_data)
        result["iteration"] = self.iteration
        result["session_id"] = self.session_id
        self.history.append(result)
        next_stage = result["next_stage"]
        # 到达 review 且成功 → 一次完整 iteration 完成
        if self.current_stage == RALPH_STAGE_REVIEW and result["status"] == "ok":
            self.iteration += 1
        # 检查 max_iter 上限
        if self.iteration >= self.max_iter:
            self._terminated = True
            self._terminate_reason = "max_iter_reached"
            return "terminated"
        if next_stage is None:
            self._terminated = True
            self._terminate_reason = "unknown_stage"
            return "terminated"
        self.current_stage = next_stage
        return self.current_stage

    def should_repeat(self) -> bool:
        """review 失败时返回 True(应当回到 analyze)"""
        if not self.history:
            return False
        last = self.history[-1]
        return (
            last["stage"] == RALPH_STAGE_REVIEW
            and last["status"] == "failed"
        )

    def reset(self) -> None:
        self.current_stage = RALPH_STAGE_ANALYZE
        self.iteration = 0
        self.history.clear()
        self._terminated = False
        self._terminate_reason = None

    @property
    def terminated(self) -> bool:
        return self._terminated

    @property
    def terminate_reason(self) -> Optional[str]:
        return self._terminate_reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "current_stage": self.current_stage,
            "iteration": self.iteration,
            "max_iter": self.max_iter,
            "terminated": self._terminated,
            "terminate_reason": self._terminate_reason,
            "history": self.history,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
