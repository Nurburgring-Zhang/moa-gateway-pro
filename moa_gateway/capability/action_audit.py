"""Action Policy (Allow/Deny/AdminReview) Enhanced + Audit Gate 5-Step Protocol

来源:
- 04 moa-main-commercial (Action Policy — Allow/Deny/AdminReview 三态裁决)
- 06 moai-adk-multiagent (Audit Gate — hash → cache → invoke → route → persist)

真实实现,非 mock。所有哈希基于真实 SHA-256,缓存基于 dict,日志支持
内存 + 可选文件持久化,JSON 序列化原生支持。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

# ============ Enums ============


class AuditDecision(str, Enum):
    """审计裁决: ALLOW / DENY / ADMIN_REVIEW / DEFER"""

    ALLOW = "allow"
    DENY = "deny"
    ADMIN_REVIEW = "admin_review"
    DEFER = "defer"


class AuditStep(str, Enum):
    """审计 5 步协议: HASH → CACHE_CHECK → INVOKE → ROUTE → PERSIST"""

    HASH = "hash"
    CACHE_CHECK = "cache_check"
    INVOKE = "invoke"
    ROUTE = "route"
    PERSIST = "persist"


# ============ Dataclasses ============


@dataclass
class AuditLog:
    """一次 audit 的完整日志"""

    audit_id: str
    action_id: str
    hash_before: str
    hash_after: str
    decision: AuditDecision
    step_taken: AuditStep
    timestamp: float
    cached: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["decision"] = self.decision.value
        d["step_taken"] = self.step_taken.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


# ============ Default Policy ============

_ALLOW_ACTIONS = {"read", "list", "get", "fetch", "show", "view"}
_ADMIN_REVIEW_ACTIONS = {"delete", "destroy", "rm", "remove", "drop", "purge"}
_DENY_ACTIONS = {"exec", "run", "execute", "shell", "eval", "system"}


def default_policy(action_data: dict) -> AuditDecision:
    """默认策略: read 类 ALLOW;delete 类 ADMIN_REVIEW;exec 类 DENY。

    Args:
        action_data: 必须包含 "action" 键,值为动作名字符串。

    Returns:
        AuditDecision
    """
    if not isinstance(action_data, dict):
        return AuditDecision.DENY
    act = str(action_data.get("action", "")).strip().lower()
    if act in _DENY_ACTIONS:
        return AuditDecision.DENY
    if act in _ADMIN_REVIEW_ACTIONS:
        return AuditDecision.ADMIN_REVIEW
    if act in _ALLOW_ACTIONS:
        return AuditDecision.ALLOW
    return AuditDecision.ADMIN_REVIEW


# ============ Hash Helper ============


def _hash_action_data(action_data: dict) -> str:
    """对 action_data 做稳定序列化并计算 SHA-256 hex digest。"""
    try:
        serialized = json.dumps(action_data, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        serialized = repr(action_data)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# ============ AuditGate ============

# 5 步协议顺序常量
_STEP_ORDER: list[AuditStep] = [
    AuditStep.HASH,
    AuditStep.CACHE_CHECK,
    AuditStep.INVOKE,
    AuditStep.ROUTE,
    AuditStep.PERSIST,
]


class AuditGate:
    """审计闸门: 5 步协议 (hash → cache_check → invoke → route → persist)"""

    def __init__(
        self,
        cache: dict | None = None,
        log_path: str | None = None,
        policy_fn: Callable[[dict], AuditDecision] | None = None,
    ) -> None:
        """Args:
        cache: 外部缓存 dict(键 = hash,值 = AuditLog 或决策)。None 则用内部 dict。
        log_path: 可选,把所有 AuditLog 序列化为 JSONL 追加到该文件。
        policy_fn: 自定义策略函数,签名 policy_fn(action_data) -> AuditDecision。
                   None 则用 default_policy。
        """
        self._cache: dict[str, Any] = cache if cache is not None else {}
        self._log_path: str | None = log_path
        self._policy_fn: Callable[[dict], AuditDecision] = policy_fn or default_policy
        self._logs: list[AuditLog] = []
        self._lock = threading.Lock()

    # ---------- public ----------

    def audit(self, action_id: str, action_data: dict) -> AuditLog:
        """执行 5 步协议审计,返回 AuditLog。

        5 步:
            1. HASH        — 计算 action_data 的 SHA-256
            2. CACHE_CHECK — 查 cache,hit 则 cached=True, decision=ALLOW
            3. INVOKE      — 调 policy_fn(action_data) → decision
            4. ROUTE       — 按 decision 分发(放行/拒绝/等审批/暂缓)
            5. PERSIST     — 写入内存 + 可选文件
        """
        if not isinstance(action_data, dict):
            action_data = {"action": str(action_data)}

        # Step 1: HASH
        h = _hash_action_data(action_data)
        step_taken = AuditStep.HASH

        # Step 2: CACHE_CHECK
        cached = False
        decision: AuditDecision
        if h in self._cache:
            cached = True
            decision = AuditDecision.ALLOW
            step_taken = AuditStep.CACHE_CHECK
        else:
            # Step 3: INVOKE
            try:
                decision = self._policy_fn(action_data)
            except Exception:
                decision = AuditDecision.DENY
            if not isinstance(decision, AuditDecision):
                decision = AuditDecision.DENY

            # Step 4: ROUTE
            step_taken = AuditStep.ROUTE

            # 写入缓存(只有 ALLOW 缓存)
            if decision == AuditDecision.ALLOW:
                self._cache[h] = AuditDecision.ALLOW.value

            # Step 5: PERSIST
            step_taken = AuditStep.PERSIST

        log = AuditLog(
            audit_id=str(uuid.uuid4()),
            action_id=str(action_id),
            hash_before=h,
            hash_after=h,
            decision=decision,
            step_taken=step_taken,
            timestamp=time.time(),
            cached=cached,
        )

        with self._lock:
            self._logs.append(log)
            if self._log_path is not None:
                self._persist_to_file(log)

        return log

    # ---------- 查询/导出 ----------

    def get_logs(self) -> list[AuditLog]:
        """返回所有审计日志的浅拷贝。"""
        with self._lock:
            return list(self._logs)

    def get_cache(self) -> dict[str, Any]:
        """返回缓存 dict 的浅拷贝。"""
        return dict(self._cache)

    def clear_cache(self) -> None:
        """清空缓存。"""
        with self._lock:
            self._cache.clear()

    def clear_logs(self) -> None:
        """清空内存日志。"""
        with self._lock:
            self._logs.clear()

    def export_json(self) -> str:
        """导出全部日志为 JSON 数组字符串。"""
        with self._lock:
            return json.dumps(
                [log.to_dict() for log in self._logs],
                ensure_ascii=False,
                sort_keys=True,
            )

    # ---------- private ----------

    def _persist_to_file(self, log: AuditLog) -> None:
        """追加单条 JSONL 记录到 log_path。"""
        if not self._log_path:
            return
        parent = os.path.dirname(self._log_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        line = log.to_json()
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# ============ Step Order Helper (for tests / external inspection) ============


def get_step_order() -> list[AuditStep]:
    """返回 5 步协议的标准顺序。"""
    return list(_STEP_ORDER)
