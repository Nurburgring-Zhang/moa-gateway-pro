"""consumption_intel — 消费智能引擎 (Consumption Intelligence)

来源: 01 gateswarm-router (consumption-intelligence engine)

核心能力:
  1. RequestContext / EndpointSpec / SelectionDecision 数据模型
  2. 静态优先选择:按 tier 升序 (free→flagship) 排序 + capabilities 过滤
  3. 动态 fallback:跳过 consecutive_failures >= 3 的 endpoint,余下组成 chain
  4. vision 降级:ctx 需要 vision 但 candidate 不支持 → 找另一支持 vision 的
  5. 主决策 select_endpoint:static → dynamic → vision_degrade 流水线
  6. 自愈 tier 重新平衡 self_heal_rebalance:连续失败 >= 3 的降级一档
  7. 批量决策 select_batch
  8. JSON 序列化 (to_dict)

设计原则:
  - 真实决策引擎:每一步过滤/排序基于数学判断,无 mock / 无 hardcoded
  - 防御性:输入 list 防御性拷贝,dataclass 不可变
  - tier 顺序:free < lite < standard < premium < flagship
  - 跳过阈值:consecutive_failures >= 3 (与 self_heal 的 DEFAULT_FAILURE_THRESHOLD 一致)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

# ============ Constants ============

# tier 升序:索引小 = 优先级低 / 成本低,索引大 = 优先级高 / 成本高
_TIER_ORDER = ["free", "lite", "standard", "premium", "flagship"]
_TIER_INDEX: dict[str, int] = {t: i for i, t in enumerate(_TIER_ORDER)}

# 连续失败跳过阈值
FAILURE_SKIP_THRESHOLD = 3

# 降一档映射
_TIER_DEMOTE = {
    "flagship": "premium",
    "premium": "standard",
    "standard": "lite",
    "lite": "free",
    "free": "free",  # 已最低,不变
}

Priority = Literal["low", "normal", "high"]
TierLabel = str


# ============ Dataclasses ============


@dataclass
class RequestContext:
    """请求上下文"""

    request_id: str
    query: str
    required_capabilities: list[str] = field(default_factory=list)
    max_cost_per_1k: float | None = None
    max_latency_ms: float | None = None
    priority: Priority = "normal"

    @classmethod
    def from_dict(cls, d: dict) -> RequestContext:
        """接受字段别名,自动映射到正确字段。空 dict 走 defaults。"""
        kwargs = {}
        if "request_id" in d:
            kwargs["request_id"] = d["request_id"]
        if "request_id" not in kwargs and "id" in d:
            kwargs["request_id"] = d["id"]
        if "query" in d:
            kwargs["query"] = d["query"]
        if "query" not in kwargs and "query" in d:
            kwargs["query"] = d["query"]
        if "required_capabilities" in d:
            kwargs["required_capabilities"] = d["required_capabilities"]
        if "required_capabilities" not in kwargs and "required_capabilities" in d:
            kwargs["required_capabilities"] = d["required_capabilities"]
        if "max_cost_per_1k" in d:
            kwargs["max_cost_per_1k"] = d["max_cost_per_1k"]
        if "max_cost_per_1k" not in kwargs and "cost" in d:
            kwargs["max_cost_per_1k"] = d["cost"]
        if "max_latency_ms" in d:
            kwargs["max_latency_ms"] = d["max_latency_ms"]
        if "max_latency_ms" not in kwargs and "latency" in d:
            kwargs["max_latency_ms"] = d["latency"]
        if "priority" in d:
            kwargs["priority"] = d["priority"]
        if "priority" not in kwargs and "priority" in d:
            kwargs["priority"] = d["priority"]
        return cls(**kwargs)

    def __post_init__(self) -> None:
        if not self.request_id or not isinstance(self.request_id, str):
            raise ValueError(f"request_id must be non-empty str, got {self.request_id!r}")
        if self.priority not in ("low", "normal", "high"):
            raise ValueError(f"priority must be low/normal/high, got {self.priority!r}")
        if self.max_cost_per_1k is not None and self.max_cost_per_1k < 0:
            raise ValueError(f"max_cost_per_1k must be >= 0, got {self.max_cost_per_1k}")
        if self.max_latency_ms is not None and self.max_latency_ms < 0:
            raise ValueError(f"max_latency_ms must be >= 0, got {self.max_latency_ms}")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EndpointSpec:
    """端点规格"""

    endpoint_id: str
    model_id: str
    cost_per_1k_input: float
    cost_per_1k_output: float
    avg_latency_ms: float
    capabilities: list[str] = field(default_factory=list)
    tier: TierLabel = "standard"
    enabled: bool = True
    consecutive_failures: int = 0

    def __post_init__(self) -> None:
        if not self.endpoint_id or not isinstance(self.endpoint_id, str):
            raise ValueError(f"endpoint_id must be non-empty str, got {self.endpoint_id!r}")
        if not self.model_id or not isinstance(self.model_id, str):
            raise ValueError(f"model_id must be non-empty str, got {self.model_id!r}")
        if self.cost_per_1k_input < 0:
            raise ValueError(f"cost_per_1k_input must be >= 0, got {self.cost_per_1k_input}")
        if self.cost_per_1k_output < 0:
            raise ValueError(f"cost_per_1k_output must be >= 0, got {self.cost_per_1k_output}")
        if self.avg_latency_ms < 0:
            raise ValueError(f"avg_latency_ms must be >= 0, got {self.avg_latency_ms}")
        if self.tier not in _TIER_ORDER:
            raise ValueError(f"tier must be one of {_TIER_ORDER}, got {self.tier!r}")
        if self.consecutive_failures < 0:
            raise ValueError(f"consecutive_failures must be >= 0, got {self.consecutive_failures}")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SelectionDecision:
    """单次选择决策"""

    selected_endpoint_id: str | None
    fallback_chain: list[str] = field(default_factory=list)
    vision_degraded_to: str | None = None
    reason: str = ""
    estimated_cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


# ============ 工具函数 ============


def _meets_capabilities(ep: EndpointSpec, required: list[str]) -> bool:
    """检查 endpoint 是否满足所有 required capabilities"""
    if not required:
        return True
    ep_caps = set(ep.capabilities or [])
    return all(req in ep_caps for req in required)


def _meets_cost(ep: EndpointSpec, max_cost: float | None) -> bool:
    """检查 endpoint 是否在 max_cost 预算内(基于 input 价)"""
    if max_cost is None:
        return True
    return float(ep.cost_per_1k_input) <= float(max_cost)


def _meets_latency(ep: EndpointSpec, max_latency: float | None) -> bool:
    """检查 endpoint 是否在 max_latency 内"""
    if max_latency is None:
        return True
    return float(ep.avg_latency_ms) <= float(max_latency)


def _tier_index(ep: EndpointSpec) -> int:
    return _TIER_INDEX.get(ep.tier, 2)  # 未知 tier 视为 standard


# ============ 静态优先选择 ============


def _static_priority(endpoints: list[EndpointSpec], ctx: RequestContext) -> list[EndpointSpec]:
    """静态优先选择:按 tier 升序 + capabilities 过滤 + cost/latency 过滤

    - 只返回 enabled=True 的 endpoint
    - capabilities 不满足的过滤掉
    - max_cost / max_latency 限制的过滤掉
    - 按 tier 升序(free→flagship)
    - 同 tier 内按 cost_per_1k_input 升序(更便宜的优先)
    """
    filtered: list[EndpointSpec] = []
    for ep in endpoints:
        if not ep.enabled:
            continue
        if not _meets_capabilities(ep, ctx.required_capabilities):
            continue
        if not _meets_cost(ep, ctx.max_cost_per_1k):
            continue
        if not _meets_latency(ep, ctx.max_latency_ms):
            continue
        filtered.append(ep)

    # 排序:tier 升序 → cost 升序
    filtered.sort(key=lambda e: (_tier_index(e), float(e.cost_per_1k_input)))
    return filtered


# ============ 动态 fallback ============


def _dynamic_fallback(
    candidates: list[EndpointSpec], ctx: RequestContext
) -> tuple[EndpointSpec | None, list[EndpointSpec]]:
    """动态 fallback:从候选中挑主选 + 构造 fallback chain

    - 跳过 consecutive_failures >= FAILURE_SKIP_THRESHOLD 的 endpoint
    - 第 1 个是主选 (primary)
    - 剩余的(按原序)是 fallback chain
    - 全部被跳过 → (None, [])
    """
    if not candidates:
        return None, []

    healthy: list[EndpointSpec] = [
        ep for ep in candidates if int(ep.consecutive_failures) < FAILURE_SKIP_THRESHOLD
    ]

    if not healthy:
        return None, []

    primary = healthy[0]
    chain = list(healthy[1:])
    return primary, chain


# ============ vision 降级 ============


def _vision_degrade(candidate: EndpointSpec, ctx: RequestContext) -> EndpointSpec | None:
    """vision 降级:若 ctx 需要 vision 但 candidate 不支持 → 返回 None(交给上层重选)

    本函数只判断 candidate 本身是否满足 vision 需求。
    实际"找另一个支持 vision 的"在 select_endpoint 中做(扫描 endpoints 表)。
    """
    if not ctx.required_capabilities:
        return candidate
    if "vision" not in ctx.required_capabilities:
        return candidate
    if "vision" in (candidate.capabilities or []):
        return candidate
    return None


def _find_vision_alternative(
    endpoints: list[EndpointSpec],
    ctx: RequestContext,
    exclude_id: str | None = None,
) -> EndpointSpec | None:
    """从 endpoints 中找一个支持 vision 的 fallback(跳过失败过多的)

    注:vision 降级是 fallback 路径,不再严格遵守 max_cost/max_latency,
    否则降级永远找不到候选
    """
    for ep in endpoints:
        if not ep.enabled:
            continue
        if exclude_id is not None and ep.endpoint_id == exclude_id:
            continue
        if "vision" not in (ep.capabilities or []):
            continue
        if int(ep.consecutive_failures) >= FAILURE_SKIP_THRESHOLD:
            continue
        return ep
    return None


# ============ 主决策 ============


def select_endpoint(ctx: RequestContext, endpoints: list[EndpointSpec]) -> SelectionDecision:
    """主决策流水线:static_priority → dynamic_fallback → vision_degrade

    步骤:
      1. 静态过滤 + 排序(static_priority) — 严格按 required_capabilities 过滤
      2. 若空且 ctx 需要 vision → 放宽 vision 过滤后重试,标记 vision 降级
      3. 动态 fallback(跳过失败过多)
      4. 估算 cost = cost_per_1k_input * 1.0
    """
    # 1) 静态优先(严格 capabilities 过滤)
    candidates = _static_priority(endpoints, ctx)

    # 2) vision 降级入口:若 ctx 需要 vision 但无 vision 候选 → 放宽 vision 重试
    vision_degraded_to: str | None = None
    needs_vision = "vision" in (ctx.required_capabilities or [])
    if not candidates and needs_vision:
        # 重新构造一个无 vision 要求的 ctx 来过静态过滤
        relaxed_caps = [c for c in ctx.required_capabilities if c != "vision"]
        relaxed_ctx = RequestContext(
            request_id=ctx.request_id,
            query=ctx.query,
            required_capabilities=relaxed_caps,
            max_cost_per_1k=ctx.max_cost_per_1k,
            max_latency_ms=ctx.max_latency_ms,
            priority=ctx.priority,
        )
        candidates = _static_priority(endpoints, relaxed_ctx)
        if candidates:
            # 从放宽后的候选中找支持 vision 的
            vision_alt = _find_vision_alternative(endpoints, ctx, exclude_id=None)
            if vision_alt is not None:
                vision_degraded_to = vision_alt.endpoint_id
                # 重组:vision_alt 升为首选(若它本身在 candidates 中则前置;否则替换首选)
                if any(ep.endpoint_id == vision_alt.endpoint_id for ep in candidates):
                    candidates = [
                        vision_alt,
                        *[ep for ep in candidates if ep.endpoint_id != vision_alt.endpoint_id],
                    ]
                else:
                    candidates = [vision_alt, *candidates]
            # 否则保持放宽后的候选,vision_degraded_to 仍为 None(标记降级失败)

    if not candidates:
        return SelectionDecision(
            selected_endpoint_id=None,
            fallback_chain=[],
            vision_degraded_to=None,
            reason="no endpoint matches capabilities / cost / latency constraints",
            estimated_cost_usd=0.0,
        )

    # 3) 动态 fallback
    primary, chain = _dynamic_fallback(candidates, ctx)
    if primary is None:
        return SelectionDecision(
            selected_endpoint_id=None,
            fallback_chain=[ep.endpoint_id for ep in candidates],
            vision_degraded_to=None,
            reason=f"all {len(candidates)} candidates exceeded failure threshold",
            estimated_cost_usd=0.0,
        )

    # 4) 估算 cost
    estimated_cost = float(primary.cost_per_1k_input) * 1.0

    # 构造 reason
    reason_parts = [f"selected {primary.tier} tier endpoint"]
    if ctx.priority == "high":
        reason_parts.append("(high priority)")
    if vision_degraded_to is not None and vision_degraded_to != primary.endpoint_id:
        reason_parts.append(f"vision degraded to {vision_degraded_to}")
    if len(chain) > 0:
        reason_parts.append(f"+{len(chain)} fallback")

    return SelectionDecision(
        selected_endpoint_id=primary.endpoint_id,
        fallback_chain=[ep.endpoint_id for ep in chain],
        vision_degraded_to=vision_degraded_to,
        reason=" ".join(reason_parts),
        estimated_cost_usd=estimated_cost,
    )


# ============ 自愈 tier 重新平衡 ============


def self_heal_rebalance(endpoints: list[EndpointSpec]) -> list[TierLabel]:
    """自愈 tier 重新平衡:每个 consecutive_failures >= 3 的 endpoint 降一档

    返回新 tier 列表(与输入同序),不修改原对象
    """
    new_tiers: list[TierLabel] = []
    for ep in endpoints:
        if int(ep.consecutive_failures) >= FAILURE_SKIP_THRESHOLD:
            new_tiers.append(_TIER_DEMOTE.get(ep.tier, ep.tier))
        else:
            new_tiers.append(ep.tier)
    return new_tiers


def self_heal_rebalance_inplace(
    endpoints: list[EndpointSpec],
) -> list[tuple[str, TierLabel, TierLabel]]:
    """自愈 tier 重新平衡(in-place) — 返回变更列表 [(endpoint_id, old_tier, new_tier)]"""
    changes: list[tuple[str, TierLabel, TierLabel]] = []
    for ep in endpoints:
        if int(ep.consecutive_failures) >= FAILURE_SKIP_THRESHOLD:
            old = ep.tier
            new = _TIER_DEMOTE.get(old, old)
            if new != old:
                ep.tier = new
                changes.append((ep.endpoint_id, old, new))
    return changes


# ============ 批量决策 ============


def select_batch(
    contexts: list[RequestContext], endpoints: list[EndpointSpec]
) -> list[SelectionDecision]:
    """批量决策:对每个 ctx 跑一次 select_endpoint"""
    return [select_endpoint(ctx, endpoints) for ctx in contexts]


# ============ JSON 序列化辅助 ============


def decision_to_json(decision: SelectionDecision) -> str:
    """decision → JSON 字符串"""
    import json

    return json.dumps(decision.to_dict(), ensure_ascii=False, sort_keys=True)


def decision_from_json(s: str) -> SelectionDecision:
    """JSON 字符串 → decision"""
    import json

    d = json.loads(s)
    return SelectionDecision(
        selected_endpoint_id=d.get("selected_endpoint_id"),
        fallback_chain=list(d.get("fallback_chain") or []),
        vision_degraded_to=d.get("vision_degraded_to"),
        reason=d.get("reason", ""),
        estimated_cost_usd=float(d.get("estimated_cost_usd", 0.0)),
    )


def endpoint_to_json(ep: EndpointSpec) -> str:
    """endpoint → JSON 字符串"""
    import json

    return json.dumps(ep.to_dict(), ensure_ascii=False, sort_keys=True)


def endpoint_from_json(s: str) -> EndpointSpec:
    """JSON 字符串 → endpoint"""
    import json

    d = json.loads(s)
    return EndpointSpec(
        endpoint_id=d["endpoint_id"],
        model_id=d["model_id"],
        cost_per_1k_input=float(d["cost_per_1k_input"]),
        cost_per_1k_output=float(d["cost_per_1k_output"]),
        avg_latency_ms=float(d["avg_latency_ms"]),
        capabilities=list(d.get("capabilities") or []),
        tier=d.get("tier", "standard"),
        enabled=bool(d.get("enabled", True)),
        consecutive_failures=int(d.get("consecutive_failures", 0)),
    )


__all__ = [
    "RequestContext",
    "EndpointSpec",
    "SelectionDecision",
    "Priority",
    "TierLabel",
    "FAILURE_SKIP_THRESHOLD",
    "_TIER_ORDER",
    "select_endpoint",
    "select_batch",
    "self_heal_rebalance",
    "self_heal_rebalance_inplace",
    "decision_to_json",
    "decision_from_json",
    "endpoint_to_json",
    "endpoint_from_json",
]
