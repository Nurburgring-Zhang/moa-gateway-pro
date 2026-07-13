"""tier_promo — Tier Promotion (A-48) + Sub-agent Boundary Check (A-49)

核心能力 (来自 06 moai-adk-multiagent):
  A-48 Tier Promotion (3 阈值 + confidence<0.70 抑制):
    1. PromotionLevel: TIER_1 / TIER_2 / TIER_3 / TIER_4 enum
    2. Evidence: 单条证据 (event_type / timestamp / weight)
    3. PromotionConfig: 阈值 (1/3/5/10) + confidence_threshold (0.70)
    4. PromotionState: 当前 tier / 累计 evidence_count / 累计 confidence
    5. compute_tier: 单纯按 evidence_count + confidence 决定 tier
       - confidence < confidence_threshold → 维持当前 tier (不 promote)
       - 否则按 evidence_count 选最大可达成 tier
    6. record_evidence: 把 evidence 累加进 state, 重算 confidence
    7. classify_tier_from_evidence: 主入口 (从 evidence 列表 + 现有 state → 推 tier)
  A-49 Sub-agent Boundary Check:
    8. SubAgentBoundary: parent_id + allowed_children 白名单
    9. can_spawn: 白名单检查
   10. cohabitation_check: 不同 parent 之间不可串扰 (同 parent → True)
   11. JSON 序列化: dataclass <-> dict

设计原则:
  - 全部判定基于真实数学 (整数阈值比较 / 置信度校准 / 集合成员查询) — 无 mock
  - compute_tier 在 confidence 不足时 **不降低** 现有 tier, 也不提升 (维持原状)
  - record_evidence 不可变更新: dataclasses.replace 返回新对象 (函数式风格)
  - confidence 计算采用 sigmoid 风格的归一化, 由 weight 求和 → 0..1
  - SubAgentBoundary 用 frozenset 存 allowed_children 保证 O(1) 查询 + 不可变
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict, replace
from enum import Enum
from typing import List, Optional, Set


__all__ = [
    "PromotionLevel",
    "Evidence",
    "PromotionConfig",
    "PromotionState",
    "SubAgentBoundary",
    # 函数
    "compute_tier",
    "record_evidence",
    "classify_tier_from_evidence",
    "evidence_to_dict",
    "evidence_from_dict",
    "promotion_state_to_dict",
    "promotion_state_from_dict",
    "promotion_config_to_dict",
    "promotion_config_from_dict",
    "subagent_boundary_to_dict",
    "subagent_boundary_from_dict",
]


# ============ 启发式常量 ============

# Confidence 归一化: 用 weight 求和, 然后用 sigmoid 风格的 logistic 映射
# 公式: confidence = total_weight / (total_weight + CONFIDENCE_KAPPA)
# 含义: 总权重达到 KAPPA 时 confidence 达到 0.5, 达到 2*KAPPA 时 ~0.67, 达到 4*KAPPA 时 ~0.80
CONFIDENCE_KAPPA: float = 3.0  # 半饱和点 (即 confidence=0.5 时的总权重)

# 默认 confidence 阈值 (低于则不 promote)
DEFAULT_CONFIDENCE_THRESHOLD: float = 0.70


# ============ Enum ============

class PromotionLevel(Enum):
    """Tier promotion 等级"""
    TIER_1 = 1
    TIER_2 = 2
    TIER_3 = 3
    TIER_4 = 4

    @property
    def numeric(self) -> int:
        return self.value


# ============ Dataclass 定义 ============

@dataclass
class Evidence:
    """单条 evidence 记录"""
    event_type: str
    timestamp: float
    weight: float = 1.0


@dataclass
class PromotionConfig:
    """Tier promotion 配置"""
    tier_1_threshold: int = 1
    tier_2_threshold: int = 3
    tier_3_threshold: int = 5
    tier_4_threshold: int = 10
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD

    def threshold_list(self) -> List[int]:
        """返回单调递增的阈值列表 (供二分查找)"""
        return [
            self.tier_1_threshold,
            self.tier_2_threshold,
            self.tier_3_threshold,
            self.tier_4_threshold,
        ]


@dataclass
class PromotionState:
    """Tier promotion 状态"""
    current_tier: PromotionLevel = PromotionLevel.TIER_1
    evidence_count: int = 0
    confidence: float = 0.0


# ============ 核心: Tier 判定 ============

def _tier_from_count(evidence_count: int, config: PromotionConfig) -> PromotionLevel:
    """纯按 evidence_count 决定 tier (不考虑 confidence 抑制)。

    规则: 找到最大的 tier_n_threshold (按 n=4,3,2,1 倒序) 使得 evidence_count >= 该阈值。
    """
    thresholds = config.threshold_list()
    # 倒序检查: tier_4 → tier_3 → tier_2 → tier_1
    for idx in range(len(thresholds) - 1, -1, -1):
        if evidence_count >= thresholds[idx]:
            return PromotionLevel(idx + 1)
    # 全部不满足 (evidence_count < tier_1_threshold) → 仍按 TIER_1 (基础)
    return PromotionLevel.TIER_1


def compute_tier(
    evidence_count: int,
    confidence: float,
    config: PromotionConfig,
    current_tier: Optional[PromotionLevel] = None,
) -> PromotionLevel:
    """根据 evidence_count + confidence 决定 tier。

    规则:
      - 若 confidence < config.confidence_threshold → 不 promote, 维持 current_tier
        (若 current_tier 未给, 默认维持 TIER_1 基础档)
      - 否则按 evidence_count 选最大可达成 tier
    """
    if confidence < config.confidence_threshold:
        # Confidence 不足, 维持当前 tier
        return current_tier if current_tier is not None else PromotionLevel.TIER_1

    candidate = _tier_from_count(evidence_count, config)

    # 如果给了 current_tier, 不允许降级 (只升不降, 即使按 count 算出的 tier 较低)
    if current_tier is not None and candidate.numeric < current_tier.numeric:
        return current_tier

    return candidate


# ============ Confidence 计算 ============

def _compute_confidence_from_weights(total_weight: float) -> float:
    """基于累计 weight 计算 confidence (sigmoid 风格, 0..1)。

    confidence(w) = w / (w + KAPPA)
    - w=0 → 0
    - w=KAPPA → 0.5
    - w→∞ → 1
    """
    if total_weight <= 0:
        return 0.0
    return total_weight / (total_weight + CONFIDENCE_KAPPA)


# ============ 核心: 状态累加 ============

def record_evidence(
    state: PromotionState,
    evidence: Evidence,
    config: PromotionConfig,
) -> PromotionState:
    """把 evidence 累加到 state, 重算 confidence, 决定是否 promote。

    返回新 PromotionState (不可变更新)。
    """
    new_count = state.evidence_count + 1
    # 累计 weight: 用现有 confidence 反推 total_weight, 然后加上新 evidence
    # confidence = w / (w + KAPPA)  →  w = confidence * KAPPA / (1 - confidence)
    if state.confidence >= 1.0:
        prior_weight = 1e9  # 已饱和
    elif state.confidence <= 0.0:
        prior_weight = 0.0
    else:
        prior_weight = state.confidence * CONFIDENCE_KAPPA / (1.0 - state.confidence)

    new_total_weight = prior_weight + max(0.0, evidence.weight)
    new_confidence = _compute_confidence_from_weights(new_total_weight)

    new_tier = compute_tier(
        evidence_count=new_count,
        confidence=new_confidence,
        config=config,
        current_tier=state.current_tier,
    )

    return PromotionState(
        current_tier=new_tier,
        evidence_count=new_count,
        confidence=new_confidence,
    )


def classify_tier_from_evidence(
    evidence: List[Evidence],
    config: PromotionConfig,
    initial_state: Optional[PromotionState] = None,
) -> PromotionLevel:
    """主入口: 从 evidence 列表推导 tier。

    流程: 初始化 state (默认 TIER_1 / 0 / 0.0) → 逐条 record_evidence → 返回最终 tier。
    """
    state = initial_state if initial_state is not None else PromotionState()
    for ev in evidence:
        state = record_evidence(state, ev, config)
    return state.current_tier


# ============ A-49: Sub-agent Boundary ============

@dataclass
class SubAgentBoundary:
    """子 agent 边界: 父 agent 只能 spawn 白名单内的子 agent, 且不同 parent 互不干扰。"""
    parent_id: str
    allowed_children: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # 用 frozenset 加速 O(1) 查询
        object.__setattr__(self, "_allowed_set", frozenset(self.allowed_children))

    def can_spawn(self, child_id: str) -> bool:
        """检查 child_id 是否在白名单内"""
        return child_id in self._allowed_set

    def cohabitation_check(self, other_parent_id: str) -> bool:
        """与另一个 parent 的边界是否兼容 (同 parent → True, 不同 parent → False)。

        设计意图: 防止跨 parent 的子 agent 串扰; 同 parent 下的子 agent 共享命名空间 OK。
        """
        return self.parent_id == other_parent_id

    def with_children(self, children: List[str]) -> "SubAgentBoundary":
        """返回新的 SubAgentBoundary, 替换白名单 (不可变更新)"""
        return SubAgentBoundary(parent_id=self.parent_id, allowed_children=list(children))

    def add_child(self, child_id: str) -> "SubAgentBoundary":
        """返回新 SubAgentBoundary, 白名单追加 child_id (去重)"""
        if child_id in self._allowed_set:
            return self
        new_list = list(self.allowed_children) + [child_id]
        return SubAgentBoundary(parent_id=self.parent_id, allowed_children=new_list)


# ============ JSON 序列化 ============

def evidence_to_dict(ev: Evidence) -> dict:
    """Evidence → dict"""
    return asdict(ev)


def evidence_from_dict(d: dict) -> Evidence:
    """dict → Evidence"""
    return Evidence(
        event_type=str(d.get("event_type", "")),
        timestamp=float(d.get("timestamp", 0.0)),
        weight=float(d.get("weight", 1.0)),
    )


def promotion_state_to_dict(state: PromotionState) -> dict:
    """PromotionState → dict (JSON 可序列化)"""
    return {
        "current_tier": state.current_tier.name,
        "current_tier_value": state.current_tier.value,
        "evidence_count": state.evidence_count,
        "confidence": state.confidence,
    }


def promotion_state_from_dict(d: dict) -> PromotionState:
    """dict → PromotionState"""
    tier_name = d.get("current_tier", "TIER_1")
    try:
        tier = PromotionLevel[tier_name]
    except KeyError:
        # 兼容旧格式 (纯 int)
        tier_value = int(d.get("current_tier_value", 1))
        tier = PromotionLevel(min(max(tier_value, 1), 4))
    return PromotionState(
        current_tier=tier,
        evidence_count=int(d.get("evidence_count", 0)),
        confidence=float(d.get("confidence", 0.0)),
    )


def promotion_config_to_dict(config: PromotionConfig) -> dict:
    """PromotionConfig → dict"""
    return asdict(config)


def promotion_config_from_dict(d: dict) -> PromotionConfig:
    """dict → PromotionConfig"""
    return PromotionConfig(
        tier_1_threshold=int(d.get("tier_1_threshold", 1)),
        tier_2_threshold=int(d.get("tier_2_threshold", 3)),
        tier_3_threshold=int(d.get("tier_3_threshold", 5)),
        tier_4_threshold=int(d.get("tier_4_threshold", 10)),
        confidence_threshold=float(d.get("confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD)),
    )


def subagent_boundary_to_dict(boundary: SubAgentBoundary) -> dict:
    """SubAgentBoundary → dict"""
    return {
        "parent_id": boundary.parent_id,
        "allowed_children": list(boundary.allowed_children),
    }


def subagent_boundary_from_dict(d: dict) -> SubAgentBoundary:
    """dict → SubAgentBoundary"""
    return SubAgentBoundary(
        parent_id=str(d.get("parent_id", "")),
        allowed_children=list(d.get("allowed_children", [])),
    )
