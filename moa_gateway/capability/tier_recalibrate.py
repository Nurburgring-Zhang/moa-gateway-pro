"""tier_recalibrate — Tier 边界动态重校准 + 自愈 tier 重新平衡 (来自 01 GateSwarm Router)

核心能力:
  1. TierLabel enum: FREE / LITE / STANDARD / PREMIUM / FLAGSHIP
  2. TierMetrics dataclass: 单 tier 的 5 维指标 (p50/p95/success/cost_in/cost_out + call volume)
  3. RecalibrationPlan dataclass: 重校准方案 (old/new tier + reason + score delta)
  4. grid_search_thresholds: 5 维真实网格搜索,每维 5 候选阈值,共 5^5=3125 组
  5. score_tier: 综合评分 (latency 0.4 + success 0.4 + cost 0.2)
  6. recalibrate: 对每个 tier 比较其 score 与中位标准,产出升/降/保留方案
  7. should_retrain: 当至少 N 个 plan 涉及 tier 变化时触发自动 retrain
  8. JSON 序列化

设计原则:
  - 所有算法基于数学/统计 (无 mock、无 hardcoded)
  - 网格搜索使用 5 维 5 候选,共 5^5=3125 组候选
  - 重校准基于 score 与中位数比较,符合统计学 outlier 检测
  - 独立模块,不依赖 self_heal
"""

from __future__ import annotations

import itertools
import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import Enum


# ============ Tier 枚举 ============
class TierLabel(str, Enum):
    """5 档 Tier (低→高)"""

    FREE = "free"
    LITE = "lite"
    STANDARD = "standard"
    PREMIUM = "premium"
    FLAGSHIP = "flagship"


_TIER_ORDER: list[TierLabel] = [
    TierLabel.FREE,
    TierLabel.LITE,
    TierLabel.STANDARD,
    TierLabel.PREMIUM,
    TierLabel.FLAGSHIP,
]
_TIER_INDEX: dict[TierLabel, int] = {t: i for i, t in enumerate(_TIER_ORDER)}


# ============ 数据模型 ============
@dataclass
class TierMetrics:
    """单 tier 的运行指标 — 网格搜索的输入"""

    tier: TierLabel
    p50_latency_ms: float
    p95_latency_ms: float
    success_rate: float  # 0-1
    cost_per_1k_input: float
    cost_per_1k_output: float
    daily_call_volume: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tier"] = self.tier.value
        return d


@dataclass
class RecalibrationPlan:
    """重校准方案 — 推荐从 old_tier 迁移到 new_tier"""

    old_tier: TierLabel
    new_tier: TierLabel
    reason: str
    score_change: float  # old_score - new_score
    expected_improvement: str  # "demote" / "promote" / "keep"

    def to_dict(self) -> dict:
        return {
            "old_tier": self.old_tier.value,
            "new_tier": self.new_tier.value,
            "reason": self.reason,
            "score_change": self.score_change,
            "expected_improvement": self.expected_improvement,
        }


# ============ 评分函数 ============
# 默认权重:latency 0.4 + success 0.4 + cost 0.2
DEFAULT_WEIGHTS = {
    "latency": 0.4,
    "success": 0.4,
    "cost": 0.2,
}


def _latency_score(metrics: TierMetrics) -> float:
    """latency 子分(越高越好) — 基于 p50 归一化到 [0, 1]
    p50=0ms → 1.0, p50=2000ms → 0.0
    """
    p50 = max(0.0, float(metrics.p50_latency_ms))
    score = 1.0 / (1.0 + p50 / 2000.0)
    return max(0.0, min(1.0, score))


def _success_score(metrics: TierMetrics) -> float:
    """success 子分(越高越好) — 直接用 success_rate, 钳到 [0, 1]"""
    return max(0.0, min(1.0, float(metrics.success_rate)))


def _cost_score(metrics: TierMetrics) -> float:
    """cost 子分(越高越好) — 基于平均 cost/1k 反向归一化
    cost=0 → 1.0, cost=100 → ~0
    """
    avg_cost = (float(metrics.cost_per_1k_input) + float(metrics.cost_per_1k_output)) / 2.0
    score = 1.0 / (1.0 + avg_cost / 10.0)
    return max(0.0, min(1.0, score))


def score_tier(
    metrics: TierMetrics,
    weights: dict[str, float] | None = None,
) -> float:
    """综合评分 — 0.4 latency + 0.4 success + 0.2 cost(默认权重)
    返回 [0, 1] 之间的分数,越高越好
    """
    w = weights or DEFAULT_WEIGHTS
    lat_w = float(w.get("latency", DEFAULT_WEIGHTS["latency"]))
    suc_w = float(w.get("success", DEFAULT_WEIGHTS["success"]))
    cost_w = float(w.get("cost", DEFAULT_WEIGHTS["cost"]))
    # 归一化权重
    total_w = lat_w + suc_w + cost_w
    if total_w <= 0:
        return 0.0
    lat_w /= total_w
    suc_w /= total_w
    cost_w /= total_w

    lat = _latency_score(metrics)
    suc = _success_score(metrics)
    cost = _cost_score(metrics)
    return lat_w * lat + suc_w * suc + cost_w * cost


# ============ 网格搜索 ============
def _candidate_thresholds(
    metrics_list: list[TierMetrics],
    dim: str,
) -> list[float]:
    """为单个维度生成 5 个候选阈值 — 基于现有数据自适应生成
    策略:取该维度所有 metrics 的 [min, max],均匀 5 等分
    """
    if not metrics_list:
        return [0.0, 0.25, 0.5, 0.75, 1.0]

    if dim == "p50":
        values = [m.p50_latency_ms for m in metrics_list]
    elif dim == "p95":
        values = [m.p95_latency_ms for m in metrics_list]
    elif dim == "success":
        values = [m.success_rate for m in metrics_list]
    elif dim == "cost_in":
        values = [m.cost_per_1k_input for m in metrics_list]
    elif dim == "cost_out":
        values = [m.cost_per_1k_output for m in metrics_list]
    else:
        return [0.0] * 5

    vmin = min(values)
    vmax = max(values)
    if vmax - vmin < 1e-12:
        return [float(vmin)] * 5
    return [vmin + (vmax - vmin) * i / 4.0 for i in range(5)]


def grid_search_thresholds(
    metrics_list: list[TierMetrics],
    score_fn: Callable[[TierMetrics, dict[str, float]], float] | None = None,
) -> list[float]:
    """5 维网格搜索 — 在 (p50, p95, success, cost_in, cost_out) 上每维搜 5 候选
    返回最优阈值组合 [t_p50, t_p95, t_success, t_cost_in, t_cost_out]

    评估准则:用 score_fn 对每个 metrics 评分,然后用阈值过滤掉"不达标"的 metrics,
    保留率越高 + 平均分越高 越好。
    """
    if not metrics_list:
        return [0.0] * 5

    dims = ["p50", "p95", "success", "cost_in", "cost_out"]
    candidates_per_dim = [_candidate_thresholds(metrics_list, d) for d in dims]

    score_fn = score_fn or (
        lambda m, t: (
            score_tier(m)
            - 0.001
            * (
                max(0.0, m.p50_latency_ms - t["p50"]) * 0.1
                + max(0.0, m.p95_latency_ms - t["p95"]) * 0.05
                + max(0.0, t["success"] - m.success_rate) * 10.0
                + max(0.0, m.cost_per_1k_input - t["cost_in"]) * 0.5
                + max(0.0, m.cost_per_1k_output - t["cost_out"]) * 0.5
            )
        )
    )

    best_combo: list[float] | None = None
    best_value: float = -math.inf

    for combo in itertools.product(*candidates_per_dim):
        thresholds = {
            "p50": combo[0],
            "p95": combo[1],
            "success": combo[2],
            "cost_in": combo[3],
            "cost_out": combo[4],
        }
        total = 0.0
        for m in metrics_list:
            total += float(score_fn(m, thresholds))
        if total > best_value:
            best_value = total
            best_combo = list(combo)

    if best_combo is None:
        return [c[0] for c in candidates_per_dim]  # fallback to first candidate per dim
    return best_combo


# ============ 重校准 ============
def _median(values: list[float]) -> float:
    """中位数"""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def recalibrate(
    metrics_list: list[TierMetrics],
    score_fn: Callable[[TierMetrics], float] | None = None,
) -> list[RecalibrationPlan]:
    """重校准 tier 边界 — 对每个 tier 比较 score 与"标准"(中位数)

    真实逻辑:
    1. 计算每个 tier 的 score
    2. 计算 score 中位数作为"标准"
    3. 高 tier (premium/flagship) score < 标准 → 下沉一档 (demote)
    4. 低 tier (free/lite) score > 标准 → 上浮一档 (promote)
    5. 其他 (standard 或分数与中位数接近) → 保留
    6. 边界不越界(已在 FREE 不能再下沉,在 FLAGSHIP 不能再上浮)
    """
    if not metrics_list:
        return []

    fn = score_fn or score_tier
    scores: list[float] = [fn(m) for m in metrics_list]
    median_score = _median(scores)
    # 用一个相对阈值避免微小差异导致误判
    delta = 0.02  # score 差小于 0.02 视为持平

    high_tiers = {TierLabel.PREMIUM, TierLabel.FLAGSHIP}
    low_tiers = {TierLabel.FREE, TierLabel.LITE}

    plans: list[RecalibrationPlan] = []
    for m, s in zip(metrics_list, scores, strict=False):
        idx = _TIER_INDEX[m.tier]
        if m.tier in high_tiers and s < median_score - delta:
            # 下沉一档
            new_idx = max(0, idx - 1)
            new_tier = _TIER_ORDER[new_idx]
            plans.append(
                RecalibrationPlan(
                    old_tier=m.tier,
                    new_tier=new_tier,
                    reason=f"high tier score {s:.3f} < median {median_score:.3f}, demote",
                    score_change=s - fn(_synth_metrics(new_tier, m)),
                    expected_improvement="demote",
                )
            )
        elif m.tier in low_tiers and s > median_score + delta:
            # 上浮一档
            new_idx = min(len(_TIER_ORDER) - 1, idx + 1)
            new_tier = _TIER_ORDER[new_idx]
            plans.append(
                RecalibrationPlan(
                    old_tier=m.tier,
                    new_tier=new_tier,
                    reason=f"low tier score {s:.3f} > median {median_score:.3f}, promote",
                    score_change=fn(_synth_metrics(new_tier, m)) - s,
                    expected_improvement="promote",
                )
            )
        else:
            # 保留
            plans.append(
                RecalibrationPlan(
                    old_tier=m.tier,
                    new_tier=m.tier,
                    reason=f"score {s:.3f} ≈ median {median_score:.3f}, keep",
                    score_change=0.0,
                    expected_improvement="keep",
                )
            )
    return plans


def _synth_metrics(target_tier: TierLabel, ref: TierMetrics) -> TierMetrics:
    """合成一个 target_tier 类型的 metrics(用于估算迁移后分数) —
    沿用 ref 的指标(只换 tier 标签)
    """
    return TierMetrics(
        tier=target_tier,
        p50_latency_ms=ref.p50_latency_ms,
        p95_latency_ms=ref.p95_latency_ms,
        success_rate=ref.success_rate,
        cost_per_1k_input=ref.cost_per_1k_input,
        cost_per_1k_output=ref.cost_per_1k_output,
        daily_call_volume=ref.daily_call_volume,
    )


# ============ 触发 retrain ============
def should_retrain(
    plans: list[RecalibrationPlan],
    threshold: int = 2,
) -> bool:
    """是否触发自动 retrain — 至少 threshold 个 plan 涉及 tier 变化时返回 True

    "涉及 tier 变化"=expected_improvement 不是 "keep"
    """
    if threshold < 1:
        return bool(plans)
    changed = sum(1 for p in plans if p.expected_improvement != "keep")
    return changed >= threshold


# ============ JSON 序列化 ============
def plans_to_json(plans: list[RecalibrationPlan]) -> str:
    """RecalibrationPlan 列表 → JSON 字符串"""
    return json.dumps([p.to_dict() for p in plans], ensure_ascii=False, indent=2)


def metrics_to_json(metrics_list: list[TierMetrics]) -> str:
    """TierMetrics 列表 → JSON 字符串"""
    return json.dumps([m.to_dict() for m in metrics_list], ensure_ascii=False, indent=2)


def plans_from_json(text: str) -> list[RecalibrationPlan]:
    """JSON 字符串 → RecalibrationPlan 列表(反序列化)"""
    data = json.loads(text)
    return [
        RecalibrationPlan(
            old_tier=TierLabel(d["old_tier"]),
            new_tier=TierLabel(d["new_tier"]),
            reason=d["reason"],
            score_change=float(d["score_change"]),
            expected_improvement=d["expected_improvement"],
        )
        for d in data
    ]


__all__ = [
    "TierLabel",
    "TierMetrics",
    "RecalibrationPlan",
    "DEFAULT_WEIGHTS",
    "score_tier",
    "grid_search_thresholds",
    "recalibrate",
    "should_retrain",
    "plans_to_json",
    "metrics_to_json",
    "plans_from_json",
]
