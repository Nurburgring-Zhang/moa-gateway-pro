"""provider_health 真实测试(非 mock)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.provider_health import (
    HealthMetrics,
    HealthScore,
    aggregate_scores,
    compute_score,
    metrics_to_dict,
    rank_providers,
    recommend,
    score_to_dict,
    should_circuit_break,
)


# ---------- 工具 ----------
def _perfect_metrics(provider: str = "p") -> HealthMetrics:
    """全 success、低 latency 的理想指标"""
    return HealthMetrics(
        provider=provider,
        total_calls=200,
        success_count=200,
        failure_count=0,
        rate_limit_hits=0,
        consecutive_429s=0,
        consecutive_failures=0,
        avg_latency_ms=500.0,
        p95_latency_ms=600.0,
        last_error_type=None,
    )


# ============ 测试 1 ============
def test_perfect_metrics_score_above_100_clamped():
    """完美指标:success_rate=1.0 > 0.99 → +5;latency=500 < 1000 → +5;consec=200 > 100 → +3
    100 + 5 + 5 + 3 = 113,clamp 到 100,tier=excellent"""
    m = _perfect_metrics()
    s = compute_score(m)
    assert s.score == 100, f"clamp 后应=100, got {s.score}"
    assert s.tier == "excellent", f"tier 应=excellent, got {s.tier}"
    print(f"  ✓ test_perfect_metrics: score={s.score}, tier={s.tier}, reasons={s.reasons}")
    assert True


# ============ 测试 2 ============
def test_one_429_deduct_15():
    """1 次 429 → 扣 15;无加分项(总调用 10, success_rate=0.9 < 0.99, latency=2000)"""
    m = HealthMetrics(
        provider="p1", total_calls=10, success_count=9, failure_count=1,
        rate_limit_hits=1, avg_latency_ms=2000.0, p95_latency_ms=2100.0,
    )
    s = compute_score(m)
    # 100 - 15(429) - 0.1*30(failure_rate) = 82
    assert s.score == 82, f"got {s.score}"
    assert any("rate_limit_hits=1 -> -15" in r for r in s.reasons)
    print(f"  ✓ test_one_429: score={s.score}")
    assert True


# ============ 测试 3 ============
def test_three_429_deduct_45():
    """3 次 429 → 扣 45(用 >1000ms latency 屏蔽 low_latency 加分,success_rate < 0.99)"""
    m = HealthMetrics(
        provider="p2", total_calls=100, success_count=97, failure_count=3,
        rate_limit_hits=3, avg_latency_ms=2000.0, p95_latency_ms=2100.0,
    )
    s = compute_score(m)
    # 100 - 45(429) - 0.03*30(failure_rate=0.03 → 0.9) = 54.1 → round = 54
    assert s.score == 54, f"got {s.score}"
    assert any("rate_limit_hits=3 -> -45" in r for r in s.reasons)
    print(f"  ✓ test_three_429: score={s.score}")
    assert True


# ============ 测试 4 ============
def test_five_consecutive_429_underflow_to_zero():
    """consecutive_429s=5 → 扣 125(下溢到 0)"""
    m = HealthMetrics(
        provider="p3", total_calls=10, success_count=5, failure_count=5,
        rate_limit_hits=5, consecutive_429s=5,
        avg_latency_ms=800.0, p95_latency_ms=900.0,
    )
    s = compute_score(m)
    assert s.score == 0, f"下溢到 0, got {s.score}"
    assert s.tier == "dead", f"tier=dead, got {s.tier}"
    print(f"  ✓ test_five_consecutive_429: score={s.score}, tier={s.tier}")
    assert True


# ============ 测试 5 ============
def test_failure_rate_half_deduct_15():
    """failure_rate=0.5 → 扣 15(= 0.5 × 30)"""
    m = HealthMetrics(
        provider="p4", total_calls=10, success_count=5, failure_count=5,
        avg_latency_ms=2000.0, p95_latency_ms=2100.0,
    )
    s = compute_score(m)
    # 100 - 15 = 85
    assert s.score == 85, f"got {s.score}"
    assert any("failure_rate=0.5000" in r for r in s.reasons)
    print(f"  ✓ test_failure_rate_half: score={s.score}")
    assert True


# ============ 测试 6 ============
def test_high_latency_over_5s_deduct_10():
    """avg_latency=6000ms > 5000 → 扣 10(success_rate < 0.99 屏蔽 +5)"""
    m = HealthMetrics(
        provider="p5", total_calls=100, success_count=90, failure_count=10,
        avg_latency_ms=6000.0, p95_latency_ms=7000.0,
    )
    s = compute_score(m)
    # 100 - 0.1*30(=3) - 10 = 87
    assert s.score == 87, f"got {s.score}"
    assert any("> 5000" in r for r in s.reasons)
    print(f"  ✓ test_high_latency_5s: score={s.score}")
    assert True


# ============ 测试 7 ============
def test_high_latency_over_10s_deduct_20():
    """avg_latency=15000ms > 10000 → 扣 20(success_rate < 0.99)"""
    m = HealthMetrics(
        provider="p6", total_calls=100, success_count=90, failure_count=10,
        avg_latency_ms=15000.0, p95_latency_ms=16000.0,
    )
    s = compute_score(m)
    # 100 - 0.1*30(=3) - 20 = 77
    assert s.score == 77, f"got {s.score}"
    assert any("> 10000" in r for r in s.reasons)
    print(f"  ✓ test_high_latency_10s: score={s.score}")
    assert True


# ============ 测试 8 ============
def test_p95_outlier_deduct_5():
    """p95 > avg×3 → 扣 5"""
    m = HealthMetrics(
        provider="p7", total_calls=10, success_count=10, failure_count=0,
        avg_latency_ms=1000.0, p95_latency_ms=3500.0,  # 3500 > 3000
    )
    s = compute_score(m)
    # 100 - 5 = 95; avg=1000 < 1000 不触发 +5(严格小于); success_rate=1.0 > 0.99 +5 = 100
    assert s.score == 100, f"clamp 后=100, got {s.score}"
    assert any("p95" in r and "avg" in r for r in s.reasons)
    print(f"  ✓ test_p95_outlier: score={s.score}")
    assert True


# ============ 测试 9 ============
def test_breaker_open_score_zero():
    """breaker_open → score=0, tier=dead"""
    m = HealthMetrics(
        provider="p8", total_calls=100, success_count=50, failure_count=50,
        avg_latency_ms=500.0, p95_latency_ms=600.0, breaker_open=True,
    )
    s = compute_score(m)
    assert s.score == 0
    assert s.tier == "dead"
    assert any("breaker_open" in r for r in s.reasons)
    print(f"  ✓ test_breaker_open: score={s.score}, tier={s.tier}")
    assert True


# ============ 测试 10 ============
def test_tier_boundaries():
    """5 档 tier 边界:≥90 excellent / ≥75 good / ≥50 fair / ≥25 poor / <25 dead"""
    # 通过手动构造 metrics 命中各档
    # excellent: 完美
    m_ex = _perfect_metrics()
    assert compute_score(m_ex).tier == "excellent"

    # good: -25
    m_good = HealthMetrics(
        provider="g", total_calls=10, success_count=10, failure_count=0,
        rate_limit_hits=0, consecutive_429s=1,  # -25
        avg_latency_ms=2000.0, p95_latency_ms=2100.0,
    )
    assert compute_score(m_good).tier == "good", f"75 should be good, got {compute_score(m_good).tier}"

    # fair: -50
    m_fair = HealthMetrics(
        provider="f", total_calls=10, success_count=10, failure_count=0,
        rate_limit_hits=0, consecutive_429s=2,  # -50
        avg_latency_ms=2000.0, p95_latency_ms=2100.0,
    )
    assert compute_score(m_fair).tier == "fair", f"50 should be fair, got {compute_score(m_fair).tier}"

    # poor: -75
    m_poor = HealthMetrics(
        provider="po", total_calls=10, success_count=10, failure_count=0,
        rate_limit_hits=1,  # -15
        consecutive_429s=2,  # -50
        avg_latency_ms=8000.0,  # -10
        p95_latency_ms=8500.0,
    )
    # 100 - 15 - 50 - 10 = 25
    assert compute_score(m_poor).tier == "poor", f"25 should be poor, got {compute_score(m_poor).tier}"

    # dead: -125
    m_dead = HealthMetrics(
        provider="d", total_calls=10, success_count=5, failure_count=5,
        consecutive_429s=5,  # -125
        avg_latency_ms=2000.0, p95_latency_ms=2100.0,
    )
    assert compute_score(m_dead).tier == "dead", f"0 should be dead, got {compute_score(m_dead).tier}"
    print("  ✓ test_tier_boundaries: all 5 tiers verified")
    assert True


# ============ 测试 11 ============
def test_aggregate_scores_multi_provider():
    """aggregate_scores:3 个 provider 聚合"""
    s1 = compute_score(_perfect_metrics("alpha"))
    s2 = compute_score(HealthMetrics("beta", total_calls=10, success_count=5, failure_count=5))
    s3 = compute_score(HealthMetrics("gamma", total_calls=10, success_count=0, failure_count=10, breaker_open=True))
    agg = aggregate_scores([s1, s2, s3])
    assert set(agg.keys()) == {"alpha", "beta", "gamma"}
    assert agg["alpha"].score == 100
    assert agg["gamma"].score == 0
    print(f"  ✓ test_aggregate_scores: keys={sorted(agg.keys())}")
    assert True


# ============ 测试 12 ============
def test_rank_providers_ordering():
    """rank_providers:按分降序,同分按字母"""
    s1 = HealthScore("charlie", 80, "good", [])
    s2 = HealthScore("alpha", 90, "excellent", [])
    s3 = HealthScore("bravo", 80, "good", [])
    s4 = HealthScore("delta", 70, "fair", [])
    # dict key 需与 value.provider 一致
    ranked = rank_providers({"charlie": s1, "alpha": s2, "bravo": s3, "delta": s4})
    names = [n for n, _ in ranked]
    assert names == ["alpha", "bravo", "charlie", "delta"], f"got {names}"
    # 验证分数正确
    assert ranked[0] == ("alpha", 90)
    assert ranked[-1] == ("delta", 70)
    print(f"  ✓ test_rank_providers: order={names}")
    assert True


# ============ 测试 13 ============
def test_should_circuit_break_threshold():
    """should_circuit_break:连续失败 >= threshold → True"""
    m1 = HealthMetrics("p", consecutive_failures=2)
    assert should_circuit_break(m1, threshold=3) is False
    m2 = HealthMetrics("p", consecutive_failures=3)
    assert should_circuit_break(m2, threshold=3) is True
    m3 = HealthMetrics("p", consecutive_failures=5, breaker_open=True)
    assert should_circuit_break(m3, threshold=3) is True
    # 自定义阈值
    m4 = HealthMetrics("p", consecutive_failures=5)
    assert should_circuit_break(m4, threshold=10) is False
    print("  ✓ test_should_circuit_break: threshold logic ok")
    assert True


# ============ 测试 14 ============
def test_recommend_picks_best():
    """recommend:选最高分"""
    scores = {
        "x": HealthScore("x", 60, "fair", []),
        "y": HealthScore("y", 90, "excellent", []),
        "z": HealthScore("z", 75, "good", []),
    }
    assert recommend(scores) == "y"
    print(f"  ✓ test_recommend_picks_best: {recommend(scores)}")
    assert True


# ============ 测试 15 ============
def test_recommend_tie_alphabetical():
    """recommend:同分 → 按字母序"""
    scores = {
        "zebra": HealthScore("zebra", 80, "good", []),
        "alpha": HealthScore("alpha", 80, "good", []),
        "mike":  HealthScore("mike", 80, "good", []),
    }
    assert recommend(scores) == "alpha"
    print(f"  ✓ test_recommend_tie: {recommend(scores)}")
    assert True


# ============ 测试 16 ============
def test_recommend_prefer_tier_filter():
    """recommend:prefer_tier=excellent 过滤"""
    scores = {
        "a": HealthScore("a", 95, "excellent", []),
        "b": HealthScore("b", 80, "good", []),
        "c": HealthScore("c", 92, "excellent", []),
    }
    # 不过滤 → 选 a(95)
    assert recommend(scores) == "a"
    # 过滤 excellent → 选 a(95)
    assert recommend(scores, prefer_tier="excellent") == "a"
    # 过滤 fair → 没有匹配 → None
    assert recommend(scores, prefer_tier="fair") is None
    print("  ✓ test_recommend_prefer_tier: filtering works")
    assert True


# ============ 测试 17 ============
def test_empty_metrics_no_error():
    """空 metrics(全 0)不报错,score 应接近 100(无扣分项)"""
    m = HealthMetrics(provider="empty")
    s = compute_score(m)
    # total=0 → failure_rate=0, success_rate=0 → 不触发 >0.99 加分
    # avg=0 → 不触发 latency 加分
    # consecutive_successes=0 → 不触发 >100
    assert s.score == 100, f"空 metrics 应=100, got {s.score}"
    assert s.tier == "excellent"
    assert s.reasons == []
    print(f"  ✓ test_empty_metrics: score={s.score}, reasons={s.reasons}")
    assert True


# ============ 测试 18 ============
def test_score_clamp_bounds():
    """score 必须 clamp 到 [0, 100]"""
    # 极端低分:扣 200,应 = 0
    m_low = HealthMetrics(
        provider="low", total_calls=10, success_count=0, failure_count=10,
        rate_limit_hits=10, consecutive_429s=10, consecutive_failures=10,
        avg_latency_ms=20000.0, p95_latency_ms=100000.0, breaker_open=False,
    )
    s_low = compute_score(m_low)
    assert 0 <= s_low.score <= 100
    assert s_low.score == 0

    # 极端高分(不可能 +13 也得 clamp 到 100)
    m_high = _perfect_metrics()  # +13,clamp 100
    s_high = compute_score(m_high)
    assert 0 <= s_high.score <= 100
    assert s_high.score == 100
    print(f"  ✓ test_score_clamp: [{s_low.score}, {s_high.score}]")
    assert True


# ============ 测试 19 ============
def test_reasons_nonempty_when_deductions():
    """有扣分/加分时 reasons 列表非空"""
    m = HealthMetrics(
        provider="r", total_calls=10, success_count=5, failure_count=5,
        rate_limit_hits=2, consecutive_429s=1, consecutive_failures=2,
        avg_latency_ms=6000.0, p95_latency_ms=20000.0,
    )
    s = compute_score(m)
    assert len(s.reasons) > 0, "应有 reasons"
    # 验证包含关键扣分项
    text = " | ".join(s.reasons)
    assert "rate_limit_hits" in text
    assert "consecutive_429s" in text
    assert "consecutive_failures" in text
    assert "failure_rate" in text
    assert "5000" in text
    assert "p95" in text
    print(f"  ✓ test_reasons: {len(s.reasons)} reasons, score={s.score}")
    assert True


# ============ 测试 20 ============
def test_json_serialization():
    """JSON 序列化:score_to_dict / metrics_to_dict"""
    m = HealthMetrics(
        provider="js", total_calls=10, success_count=9, failure_count=1,
        rate_limit_hits=1, avg_latency_ms=1500.0, p95_latency_ms=2000.0,
        last_error_type="timeout",
    )
    s = compute_score(m)
    md = metrics_to_dict(m)
    sd = score_to_dict(s)
    # 都可被 json.dumps
    json.dumps(md)
    json.dumps(sd)
    # 必含字段
    assert "provider" in md
    assert "breaker_open" in md
    assert "failure_rate" in md
    assert "consecutive_successes" in md
    assert "score" in sd
    assert "tier" in sd
    assert "reasons" in sd
    assert isinstance(sd["reasons"], list)
    # 验证 dataclass 自身的 to_dict 也可用
    assert m.to_dict()["provider"] == "js"
    assert s.to_dict()["tier"] == sd["tier"]
    print(f"  ✓ test_json_serialization: metrics={list(md.keys())[:5]}..., score={sd['score']}")
    assert True


# ============ 主入口 ============
if __name__ == "__main__":
    tests = [
        test_perfect_metrics_score_above_100_clamped,
        test_one_429_deduct_15,
        test_three_429_deduct_45,
        test_five_consecutive_429_underflow_to_zero,
        test_failure_rate_half_deduct_15,
        test_high_latency_over_5s_deduct_10,
        test_high_latency_over_10s_deduct_20,
        test_p95_outlier_deduct_5,
        test_breaker_open_score_zero,
        test_tier_boundaries,
        test_aggregate_scores_multi_provider,
        test_rank_providers_ordering,
        test_should_circuit_break_threshold,
        test_recommend_picks_best,
        test_recommend_tie_alphabetical,
        test_recommend_prefer_tier_filter,
        test_empty_metrics_no_error,
        test_score_clamp_bounds,
        test_reasons_nonempty_when_deductions,
        test_json_serialization,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
