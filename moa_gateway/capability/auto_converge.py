"""auto_converge — Auto-converge + stagnation detection (A-15) + Tier Classification (A-14)

核心能力 (来自 06 moai-adk-multiagent auto-converge + tier classification):
  1. ConvergenceState: 追踪每轮最佳 score、连续无提升计数、是否收敛
  2. ConvergenceConfig: 收敛参数 (stagnation 阈值 / improvement 阈值 / max_iterations)
  3. check_convergence: 比较新 score, 决定 stagnation 计数 / 收敛标志
  4. classify_tier: 1/3/5/10 events → tier 1/2/3/4 (用 thresholds 列表二分)
  5. detect_stagnation: 滑窗 std < epsilon → 判定 stagnant
  6. calibrate_confidence: 基于样本数的置信度校准 (0 / <10 / >=10)
  7. JSON 序列化: dataclass <-> dict

设计原则:
  - 所有判定基于真实数学 (delta 比较 / 滑窗 std / 二分查找) — 无 mock
  - dataclass 不可变更新: 用 dataclasses.replace 返回新对象 (符合函数式风格)
  - classify_tier 阈值用模块级常量 + sorted list 保障二分查找单调性
  - calibrate_confidence 用阶梯函数, 不做插值 (与 spec 一致)
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

__all__ = [
    "ConvergenceState",
    "ConvergenceConfig",
    "TIER_THRESHOLDS",
    "TIER_LABELS",
    "CONFIDENCE_ZERO_SAMPLES",
    "CONFIDENCE_LOW_SAMPLES",
    "CONFIDENCE_HIGH_SAMPLES",
    "LOW_SAMPLES_CUTOFF",
    "check_convergence",
    "classify_tier",
    "detect_stagnation",
    "calibrate_confidence",
    "convergence_state_to_dict",
    "convergence_state_from_dict",
    "convergence_config_to_dict",
]


# ============ 启发式常量 ============

# Tier 分类: events_count >= threshold → tier index
# tier 1: >= 1 event
# tier 2: >= 3 events
# tier 3: >= 5 events
# tier 4: >= 10 events
# < 1 event → tier 0
TIER_THRESHOLDS: list[int] = [1, 3, 5, 10]
TIER_LABELS: list[int] = [1, 2, 3, 4]

# 置信度校准阶梯
CONFIDENCE_ZERO_SAMPLES: float = 0.5  # 无样本: 瞎猜
CONFIDENCE_LOW_SAMPLES: float = 0.7  # 样本 < 10: 弱信号
CONFIDENCE_HIGH_SAMPLES: float = 0.9  # 样本 >= 10: 强信号
LOW_SAMPLES_CUTOFF: int = 10


# ============ Dataclass 定义 ============


@dataclass
class ConvergenceConfig:
    """收敛检测的配置参数"""

    stagnation_threshold: int = 3  # 连续 N 次无提升 → 收敛
    improvement_threshold: float = 0.001  # 视为提升的最小 delta
    max_iterations: int = 10  # 强制收敛的硬上限


@dataclass
class ConvergenceState:
    """收敛检测的状态 (每轮迭代后更新)"""

    iteration: int = 0
    best_score_history: list[float] = field(default_factory=list)
    stagnation_count: int = 0
    converged: bool = False


# ============ 核心: 收敛检测 ============


def check_convergence(
    state: ConvergenceState,
    config: ConvergenceConfig,
    new_score: float,
) -> ConvergenceState:
    """根据 new_score 与历史比较, 返回新状态。

    规则:
      - state.best_score_history 为空: 视为首次, 直接接受
      - delta = new_score - last_best
      - delta > improvement_threshold → 提升: stagnation_count = 0, 记录到 history
      - delta <= improvement_threshold → 无提升: stagnation_count += 1
      - stagnation_count >= config.stagnation_threshold → converged = True
      - iteration + 1 >= config.max_iterations → converged = True
    """
    new_history = list(state.best_score_history)
    new_stag = state.stagnation_count
    new_iter = state.iteration
    new_converged = state.converged

    if not new_history:
        # 首次迭代, 直接记录, stagnation 仍记 1 (无基线可比, 视为未提升)
        new_history.append(new_score)
        new_stag = 1
    else:
        last_best = new_history[-1]
        delta = new_score - last_best
        if delta > config.improvement_threshold:
            # 提升, 重置 stagnation, 但仍记录新 score (作为新基线)
            new_history.append(new_score)
            new_stag = 0
        else:
            # 无显著提升, 累加 stagnation
            new_stag += 1

    new_iter = new_iter + 1

    # 收敛判定 (任一条件触发即收敛)
    if new_stag >= config.stagnation_threshold:
        new_converged = True
    if new_iter >= config.max_iterations:
        new_converged = True

    return ConvergenceState(
        iteration=new_iter,
        best_score_history=new_history,
        stagnation_count=new_stag,
        converged=new_converged,
    )


# ============ Tier 分类 ============


def classify_tier(events_count: int) -> int:
    """根据事件数量返回 tier 等级 (1/2/3/4, 不足 1 event → 0)。

    用 TIER_THRESHOLDS 二分查找: 找到最大的 threshold 使得 events_count >= threshold。
    """
    if events_count < TIER_THRESHOLDS[0]:
        return 0

    # 二分: 找 >= events_count 的最小 threshold 的下界, 再 -1 即为 tier index
    lo, hi = 0, len(TIER_THRESHOLDS)
    while lo < hi:
        mid = (lo + hi) // 2
        if TIER_THRESHOLDS[mid] <= events_count:
            lo = mid + 1
        else:
            hi = mid
    # lo 是第一个 > events_count 的位置, tier index = lo - 1
    return TIER_LABELS[lo - 1]


# ============ Stagnation 检测 (滑窗) ============


def detect_stagnation(
    history: list[float],
    threshold: int = 3,
    epsilon: float = 0.001,
) -> bool:
    """滑窗检测: 取 history 末尾 threshold 个值, std < epsilon → 判定 stagnant。

    要求 history 长度 >= threshold, 否则数据不足, 视为未 stagnant。
    """
    if len(history) < threshold:
        return False

    window = list(history[-threshold:])
    n = len(window)
    if n <= 1:
        # 单点 std = 0, 但 spec 要求"滑窗 threshold 个值", 不够不算 stagnant
        return False

    mean = sum(window) / n
    variance = sum((x - mean) ** 2 for x in window) / n
    std = math.sqrt(variance)
    return std < epsilon


# ============ 置信度校准 ============


def calibrate_confidence(score: float, samples: int) -> float:
    """基于样本数校准置信度 (阶梯函数)。

    - 0 samples → 0.5
    - samples < 10 → 0.7
    - samples >= 10 → 0.9
    """
    if samples <= 0:
        return CONFIDENCE_ZERO_SAMPLES
    if samples < LOW_SAMPLES_CUTOFF:
        return CONFIDENCE_LOW_SAMPLES
    return CONFIDENCE_HIGH_SAMPLES


# ============ JSON 序列化 ============


def convergence_state_to_dict(state: ConvergenceState) -> dict:
    """ConvergenceState → dict (用于 JSON 序列化)"""
    return asdict(state)


def convergence_state_from_dict(d: dict) -> ConvergenceState:
    """dict → ConvergenceState (load JSON 反序列化)"""
    return ConvergenceState(
        iteration=int(d.get("iteration", 0)),
        best_score_history=list(d.get("best_score_history", [])),
        stagnation_count=int(d.get("stagnation_count", 0)),
        converged=bool(d.get("converged", False)),
    )


def convergence_config_to_dict(config: ConvergenceConfig) -> dict:
    """ConvergenceConfig → dict"""
    return asdict(config)
