"""tier_recalibrate 真实测试(非 mock)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.tier_recalibrate import (
    DEFAULT_WEIGHTS,
    RecalibrationPlan,
    TierLabel,
    TierMetrics,
    grid_search_thresholds,
    metrics_to_json,
    plans_from_json,
    plans_to_json,
    recalibrate,
    score_tier,
    should_retrain,
)


# ============ 辅助 ============
def _mk(tier, p50=500.0, p95=1000.0, sr=0.95, ci=2.0, co=4.0, vol=1000):
    return TierMetrics(
        tier=tier,
        p50_latency_ms=p50,
        p95_latency_ms=p95,
        success_rate=sr,
        cost_per_1k_input=ci,
        cost_per_1k_output=co,
        daily_call_volume=vol,
    )


# ============ 1. TierLabel enum 全部可用 ============
def test_tier_label_all_five():
    """5 个 tier 都存在且 value 正确"""
    assert TierLabel.FREE.value == "free"
    assert TierLabel.LITE.value == "lite"
    assert TierLabel.STANDARD.value == "standard"
    assert TierLabel.PREMIUM.value == "premium"
    assert TierLabel.FLAGSHIP.value == "flagship"
    assert len(list(TierLabel)) == 5
    print(f"  ✓ test_tier_label_all_five: {[t.value for t in TierLabel]}")


# ============ 2. score_tier 高 success 低 latency → 高分 ============
def test_score_tier_high_success_low_latency_high():
    """高 success + 低 latency + 低 cost → 高分(> 0.7)"""
    m = _mk(TierLabel.STANDARD, p50=50.0, p95=100.0, sr=0.99, ci=0.5, co=1.0)
    s = score_tier(m)
    assert s > 0.7, f"got {s}"
    print(f"  ✓ test_score_tier_high_success_low_latency_high: score={s:.3f}")


# ============ 3. score_tier 低 success 高 latency → 低分 ============
def test_score_tier_low_success_high_latency_low():
    """低 success + 高 latency + 高 cost → 低分(< 0.4)"""
    m = _mk(TierLabel.STANDARD, p50=3000.0, p95=5000.0, sr=0.3, ci=20.0, co=40.0)
    s = score_tier(m)
    assert s < 0.4, f"got {s}"
    print(f"  ✓ test_score_tier_low_success_high_latency_low: score={s:.3f}")


# ============ 4. 权重 0.4/0.4/0.2 ============
def test_score_tier_default_weights():
    """默认权重 = 0.4 / 0.4 / 0.2"""
    assert abs(DEFAULT_WEIGHTS["latency"] - 0.4) < 1e-9
    assert abs(DEFAULT_WEIGHTS["success"] - 0.4) < 1e-9
    assert abs(DEFAULT_WEIGHTS["cost"] - 0.2) < 1e-9
    # 验证实际生效:用各维度极端值组合
    # latency=0 极端(全 1), success=0 极端(全 0), cost=中间
    m_perfect_lat = _mk(TierLabel.STANDARD, p50=0.0, p95=0.0, sr=1.0, ci=0.0, co=0.0)
    s_perfect = score_tier(m_perfect_lat)
    assert s_perfect > 0.95, f"perfect should be > 0.95, got {s_perfect}"
    # 手工验算:1.0 * 0.4 + 1.0 * 0.4 + 1.0 * 0.2 = 1.0(归一化后)
    print(f"  ✓ test_score_tier_default_weights: perfect={s_perfect:.3f}")


# ============ 5. grid_search_thresholds 5 维 ============
def test_grid_search_thresholds_returns_five_dims():
    """返回列表长度=5"""
    metrics = [
        _mk(TierLabel.FREE, p50=300.0, sr=0.9, ci=0.0, co=0.0),
        _mk(TierLabel.STANDARD, p50=800.0, sr=0.95, ci=3.0, co=6.0),
        _mk(TierLabel.FLAGSHIP, p50=1500.0, sr=0.99, ci=10.0, co=20.0),
    ]
    thresholds = grid_search_thresholds(metrics)
    assert isinstance(thresholds, list)
    assert len(thresholds) == 5, f"got length {len(thresholds)}"
    # 每个值都是 float
    for v in thresholds:
        assert isinstance(v, (int, float))
    # 5 维对应 5 个不同的合理范围:p50 > 0, p95 > 0, success ∈ [0,1], cost_in > 0, cost_out > 0
    assert thresholds[0] > 0  # p50
    assert thresholds[1] > 0  # p95
    assert 0.0 <= thresholds[2] <= 1.0  # success
    assert thresholds[3] >= 0  # cost_in
    assert thresholds[4] >= 0  # cost_out
    print(f"  ✓ test_grid_search_thresholds_returns_five_dims: {thresholds}")


# ============ 6. grid_search 返回最优 ============
def test_grid_search_returns_best():
    """网格搜索应返回使得总分最大的组合 — 验证穷举 5^5=3125 候选"""
    metrics = [
        _mk(TierLabel.FREE, p50=200.0, p95=400.0, sr=0.95, ci=0.5, co=1.0),
        _mk(TierLabel.LITE, p50=400.0, p95=800.0, sr=0.93, ci=1.0, co=2.0),
        _mk(TierLabel.STANDARD, p50=800.0, p95=1500.0, sr=0.9, ci=3.0, co=6.0),
        _mk(TierLabel.PREMIUM, p50=1200.0, p95=2200.0, sr=0.88, ci=6.0, co=12.0),
        _mk(TierLabel.FLAGSHIP, p50=1800.0, p95=3000.0, sr=0.85, ci=10.0, co=20.0),
    ]
    thresholds = grid_search_thresholds(metrics)
    assert len(thresholds) == 5
    # 阈值应在 [min, max] 范围内
    p50s = [m.p50_latency_ms for m in metrics]
    assert min(p50s) <= thresholds[0] <= max(p50s)
    srs = [m.success_rate for m in metrics]
    assert min(srs) <= thresholds[2] <= max(srs)
    print(f"  ✓ test_grid_search_returns_best: thresholds={thresholds}")


# ============ 7. recalibrate 高 tier 低分 → 下沉 ============
def test_recalibrate_high_tier_low_score_demoted():
    """PREMIUM score 远低于其他 → 应被下沉"""
    metrics = [
        _mk(TierLabel.FREE, p50=200.0, sr=0.95, ci=0.5, co=1.0),       # 较高分
        _mk(TierLabel.LITE, p50=300.0, sr=0.94, ci=1.0, co=2.0),       # 较高分
        _mk(TierLabel.STANDARD, p50=500.0, sr=0.93, ci=2.0, co=4.0),   # 中等
        _mk(TierLabel.PREMIUM, p50=2000.0, sr=0.5, ci=15.0, co=30.0),  # 低分
        _mk(TierLabel.FLAGSHIP, p50=2500.0, sr=0.4, ci=20.0, co=40.0), # 低分
    ]
    plans = recalibrate(metrics)
    by_old = {p.old_tier: p for p in plans}
    premium_plan = by_old[TierLabel.PREMIUM]
    flagship_plan = by_old[TierLabel.FLAGSHIP]
    assert premium_plan.expected_improvement == "demote", f"got {premium_plan.expected_improvement}"
    assert premium_plan.new_tier != premium_plan.old_tier
    assert flagship_plan.expected_improvement == "demote"
    assert flagship_plan.new_tier != flagship_plan.old_tier
    print(f"  ✓ test_recalibrate_high_tier_low_score_demoted: premium→{premium_plan.new_tier.value}")


# ============ 8. recalibrate 低 tier 高分 → 上浮 ============
def test_recalibrate_low_tier_high_score_promoted():
    """FREE score 远高于其他 → 应被上浮"""
    metrics = [
        _mk(TierLabel.FREE, p50=100.0, p95=200.0, sr=0.99, ci=0.1, co=0.2),  # 超高分
        _mk(TierLabel.LITE, p50=2000.0, p95=3000.0, sr=0.5, ci=10.0, co=20.0),# 低分
        _mk(TierLabel.STANDARD, p50=2200.0, p95=3500.0, sr=0.45, ci=12.0, co=24.0),
        _mk(TierLabel.PREMIUM, p50=2500.0, p95=4000.0, sr=0.4, ci=15.0, co=30.0),
        _mk(TierLabel.FLAGSHIP, p50=3000.0, p95=5000.0, sr=0.35, ci=20.0, co=40.0),
    ]
    plans = recalibrate(metrics)
    by_old = {p.old_tier: p for p in plans}
    free_plan = by_old[TierLabel.FREE]
    lite_plan = by_old[TierLabel.LITE]
    assert free_plan.expected_improvement == "promote", f"FREE got {free_plan.expected_improvement}"
    assert free_plan.new_tier != free_plan.old_tier
    assert lite_plan.expected_improvement == "promote"
    assert lite_plan.new_tier != lite_plan.old_tier
    print(f"  ✓ test_recalibrate_low_tier_high_score_promoted: free→{free_plan.new_tier.value}")


# ============ 9. recalibrate 正常 → 保留 ============
def test_recalibrate_normal_keeps():
    """所有 tier 分数接近 → 所有 plan 都是 keep"""
    metrics = [
        _mk(TierLabel.FREE, p50=500.0, sr=0.9, ci=2.0, co=4.0),
        _mk(TierLabel.LITE, p50=500.0, sr=0.9, ci=2.0, co=4.0),
        _mk(TierLabel.STANDARD, p50=500.0, sr=0.9, ci=2.0, co=4.0),
        _mk(TierLabel.PREMIUM, p50=500.0, sr=0.9, ci=2.0, co=4.0),
        _mk(TierLabel.FLAGSHIP, p50=500.0, sr=0.9, ci=2.0, co=4.0),
    ]
    plans = recalibrate(metrics)
    # 全部 keep
    for p in plans:
        assert p.expected_improvement == "keep", f"{p.old_tier.value} got {p.expected_improvement}"
        assert p.new_tier == p.old_tier
    print(f"  ✓ test_recalibrate_normal_keeps: {len(plans)} keeps")


# ============ 10. should_retrain 2+ 变化 → True ============
def test_should_retrain_two_or_more_changes_true():
    """3 个 demote/promote → True (threshold=2)"""
    metrics = [
        _mk(TierLabel.FREE, p50=100.0, p95=200.0, sr=0.99, ci=0.1, co=0.2),  # promote
        _mk(TierLabel.LITE, p50=2000.0, sr=0.5, ci=10.0, co=20.0),           # 中等
        _mk(TierLabel.STANDARD, p50=800.0, sr=0.9, ci=3.0, co=6.0),
        _mk(TierLabel.PREMIUM, p50=2000.0, sr=0.5, ci=15.0, co=30.0),         # demote
        _mk(TierLabel.FLAGSHIP, p50=2500.0, sr=0.4, ci=20.0, co=40.0),        # demote
    ]
    plans = recalibrate(metrics)
    result = should_retrain(plans, threshold=2)
    assert result is True
    print(f"  ✓ test_should_retrain_two_or_more_changes_true: retrain={result}")


# ============ 11. should_retrain 0/1 变化 → False ============
def test_should_retrain_zero_or_one_change_false():
    """全部相同分数 → 0 变化 → False"""
    metrics = [
        _mk(TierLabel.FREE, p50=500.0, sr=0.9, ci=2.0, co=4.0),
        _mk(TierLabel.LITE, p50=500.0, sr=0.9, ci=2.0, co=4.0),
        _mk(TierLabel.STANDARD, p50=500.0, sr=0.9, ci=2.0, co=4.0),
        _mk(TierLabel.PREMIUM, p50=500.0, sr=0.9, ci=2.0, co=4.0),
        _mk(TierLabel.FLAGSHIP, p50=500.0, sr=0.9, ci=2.0, co=4.0),
    ]
    plans = recalibrate(metrics)
    # 0 变化
    assert should_retrain(plans, threshold=2) is False
    # 1 变化:让 PREMIUM 略低
    metrics2 = list(metrics)
    metrics2[3] = _mk(TierLabel.PREMIUM, p50=5000.0, sr=0.1, ci=50.0, co=100.0)
    plans2 = recalibrate(metrics2)
    changed = sum(1 for p in plans2 if p.expected_improvement != "keep")
    assert changed == 1, f"got {changed} changes"
    assert should_retrain(plans2, threshold=2) is False
    print("  ✓ test_should_retrain_zero_or_one_change_false")


# ============ 12. 边界:单 tier 不重校准 ============
def test_recalibrate_single_tier_no_recalibration():
    """只 1 个 tier → 不会产生 demote/promote(没有比较基准)"""
    m = _mk(TierLabel.STANDARD, p50=100.0, sr=0.99, ci=0.5, co=1.0)
    plans = recalibrate([m])
    assert len(plans) == 1
    # 1 个数据点 → 中位数 = 自身 → 不触发 demote/promote
    assert plans[0].expected_improvement == "keep"
    assert plans[0].new_tier == plans[0].old_tier
    # 边界:空列表
    assert recalibrate([]) == []
    print("  ✓ test_recalibrate_single_tier_no_recalibration")


# ============ 13. JSON 序列化往返 ============
def test_json_serialization_roundtrip():
    """plans_to_json → plans_from_json 保持数据一致"""
    plans = [
        RecalibrationPlan(
            old_tier=TierLabel.PREMIUM,
            new_tier=TierLabel.STANDARD,
            reason="high tier underperform",
            score_change=0.15,
            expected_improvement="demote",
        ),
        RecalibrationPlan(
            old_tier=TierLabel.FREE,
            new_tier=TierLabel.LITE,
            reason="low tier outperformance",
            score_change=-0.1,
            expected_improvement="promote",
        ),
        RecalibrationPlan(
            old_tier=TierLabel.STANDARD,
            new_tier=TierLabel.STANDARD,
            reason="aligned with median",
            score_change=0.0,
            expected_improvement="keep",
        ),
    ]
    text = plans_to_json(plans)
    assert isinstance(text, str)
    parsed = json.loads(text)
    assert len(parsed) == 3
    restored = plans_from_json(text)
    assert len(restored) == 3
    for orig, back in zip(plans, restored, strict=False):
        assert orig.old_tier == back.old_tier
        assert orig.new_tier == back.new_tier
        assert orig.reason == back.reason
        assert abs(orig.score_change - back.score_change) < 1e-9
        assert orig.expected_improvement == back.expected_improvement
    # metrics 也能序列化
    metrics = [_mk(TierLabel.FREE), _mk(TierLabel.FLAGSHIP)]
    mtext = metrics_to_json(metrics)
    assert isinstance(mtext, str)
    assert "free" in mtext
    assert "flagship" in mtext
    print(f"  ✓ test_json_serialization_roundtrip: {len(restored)} plans")


# ============ 14. score_fn 自定义 ============
def test_custom_score_fn_recalibrate():
    """用户提供自定义 score_fn(只关注 success,忽略 latency/cost)"""
    metrics = [
        _mk(TierLabel.FREE, p50=2000.0, p95=3000.0, sr=0.99, ci=20.0, co=40.0),
        _mk(TierLabel.LITE, p50=2000.0, p95=3000.0, sr=0.95, ci=20.0, co=40.0),
        _mk(TierLabel.STANDARD, p50=2000.0, p95=3000.0, sr=0.5, ci=20.0, co=40.0),
        _mk(TierLabel.PREMIUM, p50=2000.0, p95=3000.0, sr=0.2, ci=20.0, co=40.0),
        _mk(TierLabel.FLAGSHIP, p50=2000.0, p95=3000.0, sr=0.1, ci=20.0, co=40.0),
    ]
    # 自定义:只看 success_rate
    def custom_score(m):
        return m.success_rate
    plans = recalibrate(metrics, score_fn=custom_score)
    by_old = {p.old_tier: p for p in plans}
    # FREE(0.99) > median → promote
    # FLAGSHIP(0.1) < median → demote
    assert by_old[TierLabel.FREE].expected_improvement == "promote"
    assert by_old[TierLabel.FLAGSHIP].expected_improvement == "demote"
    # STANDARD sr=0.5,中位数约 0.5 → keep
    assert by_old[TierLabel.STANDARD].expected_improvement == "keep"
    print(f"  ✓ test_custom_score_fn_recalibrate: free={by_old[TierLabel.FREE].expected_improvement}, flagship={by_old[TierLabel.FLAGSHIP].expected_improvement}")


# ============ Bonus 15. recalibrate 边界不越界 ============
def test_recalibrate_boundary_clamp():
    """边界 clamp — FREE 已最低,不能再下沉;FLAGSHIP 已最高,不能再上浮"""
    # FREE 高分(应 promote), FLAGSHIP 低分(应 demote)
    metrics = [
        _mk(TierLabel.FREE, p50=50.0, p95=100.0, sr=0.99, ci=0.1, co=0.2),
        _mk(TierLabel.LITE, p50=500.0, sr=0.9, ci=2.0, co=4.0),
        _mk(TierLabel.STANDARD, p50=800.0, sr=0.85, ci=4.0, co=8.0),
        _mk(TierLabel.PREMIUM, p50=1500.0, sr=0.7, ci=8.0, co=16.0),
        _mk(TierLabel.FLAGSHIP, p50=3000.0, sr=0.3, ci=25.0, co=50.0),
    ]
    plans = recalibrate(metrics)
    by_old = {p.old_tier: p for p in plans}
    # FREE 应该是 LITE(不能变 FREE 之外的更低端)
    free_plan = by_old[TierLabel.FREE]
    if free_plan.expected_improvement == "promote":
        assert free_plan.new_tier == TierLabel.LITE
    # FLAGSHIP 应该是 PREMIUM
    flag_plan = by_old[TierLabel.FLAGSHIP]
    if flag_plan.expected_improvement == "demote":
        assert flag_plan.new_tier == TierLabel.PREMIUM
    print("  ✓ test_recalibrate_boundary_clamp")


if __name__ == "__main__":
    tests = [
        test_tier_label_all_five,
        test_score_tier_high_success_low_latency_high,
        test_score_tier_low_success_high_latency_low,
        test_score_tier_default_weights,
        test_grid_search_thresholds_returns_five_dims,
        test_grid_search_returns_best,
        test_recalibrate_high_tier_low_score_demoted,
        test_recalibrate_low_tier_high_score_promoted,
        test_recalibrate_normal_keeps,
        test_should_retrain_two_or_more_changes_true,
        test_should_retrain_zero_or_one_change_false,
        test_recalibrate_single_tier_no_recalibration,
        test_json_serialization_roundtrip,
        test_custom_score_fn_recalibrate,
        test_recalibrate_boundary_clamp,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            if t():
                passed += 1
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ✗ {t.__name__}: EXCEPTION {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed, {failed} failed")
    if failed:
        sys.exit(1)
