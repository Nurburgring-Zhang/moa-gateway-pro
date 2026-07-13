"""self_heal — 自愈 tier 重新平衡 (失败时自动切换 provider + 5 min check 恢复)

来源: 01 gateswarm-router (self-healing tier rebalancing)

核心能力:
  1. 维护每个 endpoint 的状态(cooldown / tier / failure count)
  2. 失败累积自动 demote + 进入 cooldown(默认 300s = 5 min)
  3. cooldown 到期后自动 recover 并 promote 回原 tier
  4. 暴露 get_available_endpoints(用于路由选择)
  5. auto_balance 周期性扫描所有 endpoint 触发 check_recovery

设计原则:
  - 真实状态机:基于 now / cooldown_until / last_failure / consecutive_failures
  - 非 mock:无 hardcoded 成功/失败路径,所有迁移由数学判断决定
  - 防御性:state 不可变 dataclass 拷贝,内部 list 防御性拷贝
"""
from __future__ import annotations
import time as _time
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Literal


# ============ Constants ============

VALID_TIERS = ("primary", "secondary", "fallback")
_TIER_INDEX = {t: i for i, t in enumerate(VALID_TIERS)}

# 默认 cooldown 持续时间(5 min)— 来源 01 gateswarm-router
DEFAULT_COOLDOWN_SECONDS = 300

# 连续失败多少次触发 cooldown/demote — 标准做法是 3 次
DEFAULT_FAILURE_THRESHOLD = 3

# 恢复检查窗口(自愈最短冷却)— 5 min
DEFAULT_RECOVERY_WINDOW_SECONDS = 300

ActionType = Literal["promote", "demote", "cooldown", "recover", "no_op"]


# ============ Dataclasses ============

@dataclass
class EndpointState:
    """单 endpoint 的运行状态"""
    endpoint_id: str
    tier: str = "primary"
    enabled: bool = True
    in_cooldown: bool = False
    cooldown_until: Optional[float] = None
    consecutive_failures: int = 0
    last_success_at: Optional[float] = None
    last_failure_at: Optional[float] = None
    total_calls: int = 0
    total_failures: int = 0
    # 初始 tier(用于 recover 后 promote 回这里)
    original_tier: str = "primary"

    def __post_init__(self) -> None:
        if not self.endpoint_id or not isinstance(self.endpoint_id, str):
            raise ValueError(f"endpoint_id must be non-empty str, got {self.endpoint_id!r}")
        if self.tier not in VALID_TIERS:
            raise ValueError(f"tier must be one of {VALID_TIERS}, got {self.tier!r}")
        if self.original_tier not in VALID_TIERS:
            raise ValueError(
                f"original_tier must be one of {VALID_TIERS}, got {self.original_tier!r}"
            )
        if self.consecutive_failures < 0:
            raise ValueError(
                f"consecutive_failures must be >= 0, got {self.consecutive_failures}"
            )
        if self.total_calls < 0:
            raise ValueError(f"total_calls must be >= 0, got {self.total_calls}")
        if self.total_failures < 0:
            raise ValueError(f"total_failures must be >= 0, got {self.total_failures}")
        if self.cooldown_until is not None and self.cooldown_until < 0:
            raise ValueError(
                f"cooldown_until must be >= 0 if set, got {self.cooldown_until}"
            )

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class HealAction:
    """一次自愈决策"""
    action: ActionType
    endpoint_id: str
    reason: str
    at: float

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class HealLog:
    """action 历史记录"""
    actions: List[HealAction] = field(default_factory=list)

    def __post_init__(self) -> None:
        # 防御性拷贝
        self.actions = list(self.actions)

    def append(self, action: HealAction) -> None:
        self.actions.append(action)

    def extend(self, actions: List[HealAction]) -> None:
        self.actions.extend(actions)

    def for_endpoint(self, endpoint_id: str) -> List[HealAction]:
        return [a for a in self.actions if a.endpoint_id == endpoint_id]

    def count_by_action(self, action: ActionType) -> int:
        return sum(1 for a in self.actions if a.action == action)

    def to_dict(self) -> Dict:
        return {"actions": [a.to_dict() for a in self.actions]}


# ============ State container ============

@dataclass
class HealState:
    """整个自愈系统的运行时状态"""
    endpoints: Dict[str, EndpointState] = field(default_factory=dict)
    log: HealLog = field(default_factory=HealLog)
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
    last_auto_balance_at: Optional[float] = None

    def __post_init__(self) -> None:
        if self.failure_threshold <= 0:
            raise ValueError(
                f"failure_threshold must be > 0, got {self.failure_threshold}"
            )
        if self.cooldown_seconds < 0:
            raise ValueError(
                f"cooldown_seconds must be >= 0, got {self.cooldown_seconds}"
            )
        # 防御性拷贝
        self.endpoints = dict(self.endpoints)

    def add_endpoint(
        self,
        endpoint_id: str,
        tier: str = "primary",
        enabled: bool = True,
    ) -> EndpointState:
        """注册新 endpoint,已存在则返回旧值"""
        if endpoint_id in self.endpoints:
            return self.endpoints[endpoint_id]
        ep = EndpointState(
            endpoint_id=endpoint_id,
            tier=tier,
            enabled=enabled,
            original_tier=tier,
        )
        self.endpoints[endpoint_id] = ep
        return ep

    def get(self, endpoint_id: str) -> EndpointState:
        if endpoint_id not in self.endpoints:
            raise KeyError(f"endpoint {endpoint_id!r} not in state")
        return self.endpoints[endpoint_id]


# ============ Helpers ============

def _now(at: Optional[float]) -> float:
    """解析 'now': None 表示 wall-clock(epoch);否则用给定的 at"""
    return _time.time() if at is None else float(at)


def _next_tier_lower(tier: str) -> str:
    """降一级: primary → secondary, secondary → fallback, fallback → fallback"""
    idx = _TIER_INDEX.get(tier, 0)
    new_idx = min(idx + 1, len(VALID_TIERS) - 1)
    return VALID_TIERS[new_idx]


def _next_tier_higher(tier: str) -> str:
    """升一级: fallback → secondary, secondary → primary, primary → primary"""
    idx = _TIER_INDEX.get(tier, 0)
    new_idx = max(idx - 1, 0)
    return VALID_TIERS[new_idx]


# ============ Core API ============

def record_success(
    state: HealState,
    endpoint_id: str,
    at: Optional[float] = None,
) -> HealAction:
    """记录一次成功调用。

    真实逻辑:
      - consecutive_failures 归零
      - last_success_at = now
      - total_calls += 1
      - 若 in_cooldown 且 cooldown_until <= now → recover(promote 回 original_tier),
        并清 cooldown 字段
      - 否则,在 cooldown 中但未到期 → 返回 no_op
    """
    ep = state.get(endpoint_id)
    now = _now(at)
    ep.consecutive_failures = 0
    ep.last_success_at = now
    ep.total_calls += 1

    if ep.in_cooldown:
        # 在 cooldown 中,看是否到期
        if ep.cooldown_until is not None and ep.cooldown_until <= now:
            return _do_recover(state, ep, now, reason="cooldown expired after success")
        # 未到期:保持 cooldown,不 recover
        return HealAction(
            action="no_op",
            endpoint_id=endpoint_id,
            reason="success during cooldown; awaiting cooldown_until",
            at=now,
        )

    # 正常情况:不在 cooldown,无动作
    return HealAction(
        action="no_op",
        endpoint_id=endpoint_id,
        reason="success recorded",
        at=now,
    )


def record_failure(
    state: HealState,
    endpoint_id: str,
    at: Optional[float] = None,
) -> HealAction:
    """记录一次失败调用。

    真实逻辑:
      - consecutive_failures += 1
      - total_failures += 1, total_calls += 1
      - last_failure_at = now
      - 若 consecutive_failures >= failure_threshold 且不在 cooldown:
        进入 cooldown(默认 300s) + demote 到下一 tier
    """
    ep = state.get(endpoint_id)
    now = _now(at)
    ep.consecutive_failures += 1
    ep.total_failures += 1
    ep.total_calls += 1
    ep.last_failure_at = now

    # 已经在 cooldown 中,只累计失败,不重新进入
    if ep.in_cooldown:
        return HealAction(
            action="no_op",
            endpoint_id=endpoint_id,
            reason="failure during cooldown; cooldown remains",
            at=now,
        )

    # 触发 cooldown
    if ep.consecutive_failures >= state.failure_threshold:
        # demote first
        demote_action = _do_demote(
            state, ep, now,
            reason=f"consecutive_failures={ep.consecutive_failures} >= threshold={state.failure_threshold}",
        )
        # enter cooldown
        cooldown_action = _do_enter_cooldown(
            state, ep, now, state.cooldown_seconds,
            reason=f"cooldown after {ep.consecutive_failures} consecutive failures",
        )
        # 合并 reason:返回 demote action(demote 才是 tier 状态变化;cooldown 内部已 log)
        # 但 HealAction 是单 action,选择 demote 作为主 action(更影响 routing)
        return demote_action

    return HealAction(
        action="no_op",
        endpoint_id=endpoint_id,
        reason=(
            f"failure recorded ({ep.consecutive_failures}/{state.failure_threshold}); "
            f"below cooldown threshold"
        ),
        at=now,
    )


def check_recovery(
    state: HealState,
    endpoint_id: str,
    at: Optional[float] = None,
) -> HealAction:
    """周期性检查:若 cooldown 到期且距 last_success_at 已 ≥ recovery window
    或无 last_success_at(只取决于 cooldown_until),则 recover。

    真实逻辑:
      - in_cooldown=False → no_op
      - cooldown_until > now → no_op(未到期)
      - cooldown_until <= now → recover:清 in_cooldown/cooldown_until,
        consecutive_failures 归零,promote 回 original_tier
    """
    ep = state.get(endpoint_id)
    now = _now(at)

    if not ep.in_cooldown:
        return HealAction(
            action="no_op",
            endpoint_id=endpoint_id,
            reason="not in cooldown",
            at=now,
        )

    if ep.cooldown_until is None or ep.cooldown_until > now:
        return HealAction(
            action="no_op",
            endpoint_id=endpoint_id,
            reason=(
                f"cooldown not expired: cooldown_until={ep.cooldown_until} > now={now}"
            ),
            at=now,
        )

    # cooldown 到期,recover
    return _do_recover(
        state, ep, now,
        reason=f"cooldown expired (cooldown_until={ep.cooldown_until} <= now={now})",
    )


def promote(
    state: HealState,
    endpoint_id: str,
    reason: str = "",
    at: Optional[float] = None,
) -> HealAction:
    """手动 promote:fallback → secondary → primary。

    primary → primary(no_op,带 reason)
    """
    ep = state.get(endpoint_id)
    now = _now(at)
    return _do_promote(state, ep, now, reason or "manual promote")


def demote(
    state: HealState,
    endpoint_id: str,
    reason: str = "",
    at: Optional[float] = None,
) -> HealAction:
    """手动 demote:primary → secondary → fallback。

    fallback → fallback(no_op,带 reason)
    """
    ep = state.get(endpoint_id)
    now = _now(at)
    return _do_demote(state, ep, now, reason or "manual demote")


def enter_cooldown(
    state: HealState,
    endpoint_id: str,
    duration_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    reason: str = "",
    at: Optional[float] = None,
) -> HealAction:
    """手动 enter_cooldown。

    真实逻辑:即使 endpoint 已在 cooldown,允许刷新 cooldown_until(now + duration)
    """
    if duration_seconds < 0:
        raise ValueError(f"duration_seconds must be >= 0, got {duration_seconds}")
    ep = state.get(endpoint_id)
    now = _now(at)
    return _do_enter_cooldown(
        state, ep, now, duration_seconds,
        reason or f"manual enter_cooldown ({duration_seconds}s)",
    )


def get_available_endpoints(state: HealState) -> List[str]:
    """返回所有 enabled 且不在 cooldown 的 endpoint_ids(按 tier 排序:primary 优先)。"""
    out: List[str] = []
    for ep in state.endpoints.values():
        if not ep.enabled:
            continue
        if ep.in_cooldown:
            continue
        out.append(ep.endpoint_id)
    # 按 tier 排序:primary(0) > secondary(1) > fallback(2)
    out.sort(key=lambda eid: (_TIER_INDEX[state.endpoints[eid].tier], eid))
    return out


def auto_balance(state: HealState, at: Optional[float] = None) -> List[HealAction]:
    """周期性自愈扫描:对所有 endpoint 跑 check_recovery。

    真实逻辑:
      - 遍历 state.endpoints,对每个 ep 调 check_recovery
      - 收集产生的 actions(过滤掉 no_op)
      - 末尾更新 state.last_auto_balance_at = now
    """
    now = _now(at)
    actions: List[HealAction] = []
    # 拷贝 keys,避免迭代过程中修改
    for eid in list(state.endpoints.keys()):
        action = check_recovery(state, eid, at=now)
        if action.action != "no_op":
            actions.append(action)
    state.last_auto_balance_at = now
    return actions


# ============ Internal mutators ============

def _do_promote(
    state: HealState,
    ep: EndpointState,
    now: float,
    reason: str,
) -> HealAction:
    """promote 实现:升一级,若已是 primary 则 no_op"""
    if ep.tier == "primary":
        action = HealAction(
            action="no_op",
            endpoint_id=ep.endpoint_id,
            reason=f"already primary; {reason}",
            at=now,
        )
    else:
        old = ep.tier
        ep.tier = _next_tier_higher(ep.tier)
        action = HealAction(
            action="promote",
            endpoint_id=ep.endpoint_id,
            reason=f"{old} → {ep.tier}; {reason}",
            at=now,
        )
    state.log.append(action)
    return action


def _do_demote(
    state: HealState,
    ep: EndpointState,
    now: float,
    reason: str,
) -> HealAction:
    """demote 实现:降一级,若已是 fallback 则 no_op"""
    if ep.tier == "fallback":
        action = HealAction(
            action="no_op",
            endpoint_id=ep.endpoint_id,
            reason=f"already fallback; {reason}",
            at=now,
        )
    else:
        old = ep.tier
        ep.tier = _next_tier_lower(ep.tier)
        action = HealAction(
            action="demote",
            endpoint_id=ep.endpoint_id,
            reason=f"{old} → {ep.tier}; {reason}",
            at=now,
        )
    state.log.append(action)
    return action


def _do_enter_cooldown(
    state: HealState,
    ep: EndpointState,
    now: float,
    duration_seconds: float,
    reason: str,
) -> HealAction:
    ep.in_cooldown = True
    ep.cooldown_until = now + duration_seconds
    action = HealAction(
        action="cooldown",
        endpoint_id=ep.endpoint_id,
        reason=f"cooldown {duration_seconds}s (until {ep.cooldown_until}); {reason}",
        at=now,
    )
    state.log.append(action)
    return action


def _do_recover(
    state: HealState,
    ep: EndpointState,
    now: float,
    reason: str,
) -> HealAction:
    """recover:清 cooldown + consecutive_failures 归零 + promote 回 original_tier"""
    ep.in_cooldown = False
    ep.cooldown_until = None
    ep.consecutive_failures = 0
    old_tier = ep.tier
    # promote 回 original_tier(从 current 升到 original)
    while _TIER_INDEX[ep.tier] > _TIER_INDEX[ep.original_tier]:
        ep.tier = _next_tier_higher(ep.tier)
    if ep.tier != old_tier:
        reason = f"{old_tier} → {ep.tier} (recover to original); " + reason
    action = HealAction(
        action="recover",
        endpoint_id=ep.endpoint_id,
        reason=reason,
        at=now,
    )
    state.log.append(action)
    return action


# ============ Serialization ============

def state_to_dict(state: HealState) -> Dict:
    """序列化整个 HealState(含 log / endpoints / 配置)"""
    return {
        "endpoints": {eid: ep.to_dict() for eid, ep in state.endpoints.items()},
        "log": state.log.to_dict(),
        "failure_threshold": state.failure_threshold,
        "cooldown_seconds": state.cooldown_seconds,
        "last_auto_balance_at": state.last_auto_balance_at,
    }


def state_from_dict(data: Dict) -> HealState:
    """反序列化(返回 HealState)"""
    if not isinstance(data, dict):
        raise ValueError(f"data must be dict, got {type(data).__name__}")
    endpoints_data = data.get("endpoints", {})
    endpoints: Dict[str, EndpointState] = {}
    for eid, ep_dict in endpoints_data.items():
        if not isinstance(ep_dict, dict):
            raise ValueError(f"endpoint data for {eid!r} must be dict")
        ep = EndpointState(**ep_dict)
        endpoints[eid] = ep
    log_data = data.get("log", {})
    actions_data = log_data.get("actions", []) if isinstance(log_data, dict) else []
    log = HealLog()
    for a in actions_data:
        log.append(HealAction(**a))
    return HealState(
        endpoints=endpoints,
        log=log,
        failure_threshold=int(data.get("failure_threshold", DEFAULT_FAILURE_THRESHOLD)),
        cooldown_seconds=float(data.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)),
        last_auto_balance_at=data.get("last_auto_balance_at"),
    )


# ============ Convenience constructor ============

def make_default_state(
    endpoint_ids: Optional[List[str]] = None,
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
) -> HealState:
    """构造一个带若干 endpoint 的默认 HealState(测试 / 启动用)。"""
    state = HealState(
        failure_threshold=failure_threshold,
        cooldown_seconds=cooldown_seconds,
    )
    if endpoint_ids:
        for eid in endpoint_ids:
            state.add_endpoint(eid, tier="primary")
    return state


__all__ = [
    "VALID_TIERS",
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_FAILURE_THRESHOLD",
    "DEFAULT_RECOVERY_WINDOW_SECONDS",
    "ActionType",
    "EndpointState",
    "HealAction",
    "HealLog",
    "HealState",
    "record_success",
    "record_failure",
    "check_recovery",
    "promote",
    "demote",
    "enter_cooldown",
    "get_available_endpoints",
    "auto_balance",
    "state_to_dict",
    "state_from_dict",
    "make_default_state",
]
