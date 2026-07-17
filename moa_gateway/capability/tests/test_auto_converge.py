"""auto_converge 单元测试 (>= 16 测试, 真实 assert, 严禁 mock)

覆盖:
  - ConvergenceState / ConvergenceConfig 字段
  - check_convergence 提升/无提升/阈值/边界/max_iter
  - classify_tier 0/1/3/5/10/超量
  - detect_stagnation 全相同/变化大/窗口外
  - calibrate_confidence 阶梯函数
  - JSON 序列化往返
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.auto_converge import (
    CONFIDENCE_HIGH_SAMPLES,
    CONFIDENCE_LOW_SAMPLES,
    CONFIDENCE_ZERO_SAMPLES,
    TIER_LABELS,
    TIER_THRESHOLDS,
    ConvergenceConfig,
    ConvergenceState,
    calibrate_confidence,
    check_convergence,
    classify_tier,
    convergence_config_to_dict,
    convergence_state_from_dict,
    convergence_state_to_dict,
    detect_stagnation,
)

# ============ Dataclass 字段 ============

def test_state_default_fields():
    s = ConvergenceState()
    assert s.iteration == 0
    assert s.best_score_history == []
    assert s.stagnation_count == 0
    assert s.converged is False


def test_config_default_fields():
    c = ConvergenceConfig()
    assert c.stagnation_threshold == 3
    assert c.improvement_threshold == 0.001
    assert c.max_iterations == 10


# ============ check_convergence ============

def test_check_convergence_first_iter_accepted():
    """首次迭代, history 为空, 直接接受新 score, stagnation=1"""
    s = ConvergenceState()
    cfg = ConvergenceConfig()
    ns = check_convergence(s, cfg, new_score=0.5)
    assert ns.iteration == 1
    assert ns.best_score_history == [0.5]
    assert ns.stagnation_count == 1
    assert ns.converged is False


def test_check_convergence_improvement_resets_stagnation():
    """new_score 显著提升 → stagnation_count 重置为 0"""
    s = ConvergenceState(iteration=1, best_score_history=[0.5], stagnation_count=2)
    cfg = ConvergenceConfig()
    ns = check_convergence(s, cfg, new_score=0.9)
    assert ns.stagnation_count == 0
    assert ns.best_score_history[-1] == 0.9
    assert ns.converged is False


def test_check_convergence_no_improvement_increments_stagnation():
    """new_score 与上次持平 → stagnation_count += 1"""
    s = ConvergenceState(iteration=1, best_score_history=[0.5], stagnation_count=1)
    cfg = ConvergenceConfig()
    ns = check_convergence(s, cfg, new_score=0.5)
    assert ns.stagnation_count == 2
    assert ns.iteration == 2
    assert ns.converged is False


def test_check_convergence_reaches_threshold_marks_converged():
    """连续 3 次无提升 → converged=True"""
    s = ConvergenceState(iteration=2, best_score_history=[0.5, 0.5], stagnation_count=2)
    cfg = ConvergenceConfig(stagnation_threshold=3)
    ns = check_convergence(s, cfg, new_score=0.5)
    assert ns.stagnation_count == 3
    assert ns.converged is True


def test_check_convergence_max_iter_forces_converge():
    """iteration 达到 max_iterations → 强制 converged=True"""
    s = ConvergenceState(iteration=9, best_score_history=[0.5] * 9, stagnation_count=0)
    cfg = ConvergenceConfig(max_iterations=10)
    ns = check_convergence(s, cfg, new_score=0.99)  # 大幅提升也救不了
    assert ns.iteration == 10
    assert ns.converged is True


def test_check_convergence_improvement_threshold_boundary():
    """delta <= improvement_threshold 不算提升 (严格大于)"""
    s = ConvergenceState(iteration=1, best_score_history=[0.5], stagnation_count=0)
    cfg = ConvergenceConfig(improvement_threshold=0.01)
    # delta = 0.005 < 0.01, 不算提升 → stagnation
    ns = check_convergence(s, cfg, new_score=0.505)
    assert ns.stagnation_count == 1
    # delta = 0.02 > 0.01, 算提升 → stagnation 重置
    ns2 = check_convergence(s, cfg, new_score=0.52)
    assert ns2.stagnation_count == 0


# ============ classify_tier ============

def test_classify_tier_1_event():
    assert classify_tier(1) == 1


def test_classify_tier_3_events():
    assert classify_tier(3) == 2


def test_classify_tier_5_events():
    assert classify_tier(5) == 3


def test_classify_tier_10_events():
    assert classify_tier(10) == 4


def test_classify_tier_0_events_returns_0():
    assert classify_tier(0) == 0


def test_classify_tier_between_thresholds():
    """2 events → 仍 tier 1 (>=1 但 <3)"""
    assert classify_tier(2) == 1
    assert classify_tier(4) == 2
    assert classify_tier(7) == 3
    assert classify_tier(50) == 4


# ============ detect_stagnation ============

def test_detect_stagnation_all_equal_true():
    """滑窗内全部相等 → std=0 < epsilon → True"""
    history = [0.5, 0.5, 0.5]
    assert detect_stagnation(history, threshold=3, epsilon=0.001) is True


def test_detect_stagnation_high_variance_false():
    """滑窗内变化大 → std > epsilon → False"""
    history = [0.0, 1.0, 0.0]
    assert detect_stagnation(history, threshold=3, epsilon=0.001) is False


def test_detect_stagnation_window_too_short_false():
    """history 长度 < threshold → 数据不足 → False"""
    history = [0.5, 0.5]  # 只有 2 个, threshold=3
    assert detect_stagnation(history, threshold=3, epsilon=0.001) is False


def test_detect_stagnation_uses_tail_window():
    """stagnation 仅看末尾 threshold 个, 前面变化大不影响"""
    # 前面变化大, 末尾 3 个全等 → 仍 stagnant
    history = [0.0, 1.0, 0.0, 0.7, 0.7, 0.7]
    assert detect_stagnation(history, threshold=3, epsilon=0.001) is True


# ============ calibrate_confidence ============

def test_calibrate_confidence_zero_samples():
    assert calibrate_confidence(0.5, 0) == CONFIDENCE_ZERO_SAMPLES == 0.5


def test_calibrate_confidence_low_samples():
    """1..9 samples → 0.7"""
    for n in [1, 5, 9]:
        assert calibrate_confidence(0.5, n) == CONFIDENCE_LOW_SAMPLES == 0.7


def test_calibrate_confidence_high_samples():
    """>= 10 samples → 0.9"""
    for n in [10, 50, 1000]:
        assert calibrate_confidence(0.5, n) == CONFIDENCE_HIGH_SAMPLES == 0.9


def test_calibrate_confidence_negative_samples_treated_as_zero():
    """负数 samples 走 0 样本分支 (容错)"""
    assert calibrate_confidence(0.0, -5) == CONFIDENCE_ZERO_SAMPLES


# ============ JSON 序列化 ============

def test_state_json_roundtrip():
    s = ConvergenceState(
        iteration=5,
        best_score_history=[0.1, 0.2, 0.5, 0.5, 0.5],
        stagnation_count=3,
        converged=True,
    )
    d = convergence_state_to_dict(s)
    j = json.dumps(d)
    d2 = json.loads(j)
    s2 = convergence_state_from_dict(d2)
    assert s2.iteration == 5
    assert s2.best_score_history == [0.1, 0.2, 0.5, 0.5, 0.5]
    assert s2.stagnation_count == 3
    assert s2.converged is True


def test_config_to_dict():
    c = ConvergenceConfig(stagnation_threshold=5, improvement_threshold=0.01, max_iterations=20)
    d = convergence_config_to_dict(c)
    assert d == {
        "stagnation_threshold": 5,
        "improvement_threshold": 0.01,
        "max_iterations": 20,
    }


# ============ 集成: 一次完整迭代循环 ============

def test_integration_loop_converges_after_3_stagnations():
    """集成: 一次大幅提升 + 连续 3 次无提升 → 收敛"""
    cfg = ConvergenceConfig(stagnation_threshold=3, improvement_threshold=0.001, max_iterations=100)
    s = ConvergenceState()
    # iter 1: 首次
    s = check_convergence(s, cfg, 0.5)
    assert s.stagnation_count == 1
    assert s.converged is False
    # iter 2: 大幅提升
    s = check_convergence(s, cfg, 0.9)
    assert s.stagnation_count == 0
    assert s.converged is False
    # iter 3-5: 连续 3 次持平
    s = check_convergence(s, cfg, 0.9)
    assert s.stagnation_count == 1
    assert s.converged is False
    s = check_convergence(s, cfg, 0.9)
    assert s.stagnation_count == 2
    assert s.converged is False
    s = check_convergence(s, cfg, 0.9)
    assert s.stagnation_count == 3
    assert s.converged is True


def test_tier_thresholds_consistent():
    """TIER_THRESHOLDS 与 TIER_LABELS 长度一致, 单调递增"""
    assert len(TIER_THRESHOLDS) == len(TIER_LABELS)
    for i in range(1, len(TIER_THRESHOLDS)):
        assert TIER_THRESHOLDS[i] > TIER_THRESHOLDS[i - 1]
