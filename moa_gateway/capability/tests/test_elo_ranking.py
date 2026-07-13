"""elo_ranking 真实测试(非 mock)"""
import sys
import json
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.elo_ranking import (
    MatchResult, EloRating, EloLeaderboard,
    bootstrap_ci, WorkerPool, to_json,
)


# ============ Elo 基础 ============
def test_add_model_default_1500():
    """add_model 默认 rating = 1500"""
    lb = EloLeaderboard()
    lb.add_model("gpt-4")
    assert lb.get_rating("gpt-4") == 1500.0, f"got {lb.get_rating('gpt-4')}"
    print(f"  ✓ test_add_model_default_1500: gpt-4=1500.0")
    return True


def test_record_match_winner_gains():
    """record_match 一局 → winner 涨分"""
    lb = EloLeaderboard(k_factor=4.0)
    lb.add_model("A")
    lb.add_model("B")
    before_w = lb.get_rating("A")
    lb.record_match("A", "B", timestamp=1.0)
    after_w = lb.get_rating("A")
    assert after_w > before_w, f"expected winner gain: {before_w} → {after_w}"
    # 平局期望 E = 0.5, 涨 K*(1-0.5) = 2.0
    assert abs(after_w - (before_w + 2.0)) < 1e-9, f"got {after_w}"
    print(f"  ✓ test_record_match_winner_gains: {before_w} → {after_w} (+2.0)")
    return True


def test_record_match_loser_drops():
    """record_match 一局 → loser 跌分"""
    lb = EloLeaderboard(k_factor=4.0)
    lb.add_model("A")
    lb.add_model("B")
    before_l = lb.get_rating("B")
    lb.record_match("A", "B", timestamp=1.0)
    after_l = lb.get_rating("B")
    assert after_l < before_l, f"expected loser drop: {before_l} → {after_l}"
    # 平局期望 E = 0.5, 跌 K*(0-0.5) = -2.0
    assert abs(after_l - (before_l - 2.0)) < 1e-9, f"got {after_l}"
    print(f"  ✓ test_record_match_loser_drops: {before_l} → {after_l} (-2.0)")
    return True


def test_k4_max_change():
    """K=4 时一局最大变化 ≤ 4(强反差期望下趋近 4)"""
    # 当 expected≈0 (对手远强) 且 actual=1 (你赢) → delta ≈ K
    # 用极大 rating 差使 expected 真正趋近 0
    lb = EloLeaderboard(k_factor=4.0)
    lb.add_model("weak", initial_rating=0.0)
    lb.add_model("strong", initial_rating=4000.0)
    # expected of weak = 1 / (1 + 10^(4000/400)) = 1 / (1 + 10^10) ≈ 1e-10
    before_weak = lb.get_rating("weak")
    before_strong = lb.get_rating("strong")
    lb.record_match("weak", "strong", timestamp=1.0)
    delta_weak = lb.get_rating("weak") - before_weak
    delta_strong = lb.get_rating("strong") - before_strong
    # weak 涨分(≈ 4)
    assert abs(delta_weak - 4.0) < 1e-6, f"expected weak delta≈4.0, got {delta_weak}"
    # strong 跌分(≈ -4,绝对值 ≈ 4)
    assert abs(delta_strong + 4.0) < 1e-6, f"expected strong delta≈-4.0, got {delta_strong}"
    # 验证守恒:涨 + 跌 = 0
    assert abs(delta_weak + delta_strong) < 1e-6, f"non-conservative: {delta_weak} + {delta_strong}"
    print(f"  ✓ test_k4_max_change: weak {delta_weak:+.10f}, strong {delta_strong:+.10f}")
    return True


def test_ranked_ordering():
    """ranked 按 rating 降序"""
    lb = EloLeaderboard(k_factor=8.0)
    lb.add_model("A")
    lb.add_model("B")
    lb.add_model("C")
    # A 赢 B、C赢 B、A赢 C
    lb.record_match("A", "B")
    lb.record_match("C", "B")
    lb.record_match("A", "C")
    ranked = lb.ranked()
    ratings = [r.rating for r in ranked]
    # 降序
    for i in range(len(ratings) - 1):
        assert ratings[i] >= ratings[i + 1], f"not sorted: {ratings}"
    # A 应排第一(赢最多)
    assert ranked[0].model_id == "A", f"expected A first, got {ranked[0].model_id}"
    # B 应排最后(输最多)
    assert ranked[-1].model_id == "B", f"expected B last, got {ranked[-1].model_id}"
    print(f"  ✓ test_ranked_ordering: {[r.model_id for r in ranked]}")
    return True


def test_get_rating_unknown_zero():
    """get_rating 未知 model_id → 0.0"""
    lb = EloLeaderboard()
    assert lb.get_rating("ghost") == 0.0
    lb.add_model("real")
    assert lb.get_rating("real") == 1500.0
    assert lb.get_rating("another_unknown") == 0.0
    print(f"  ✓ test_get_rating_unknown_zero: ghost=0.0, real=1500.0")
    return True


def test_match_self_error():
    """winner == loser 应抛错"""
    lb = EloLeaderboard()
    lb.add_model("A")
    lb.add_model("B")
    try:
        lb.record_match("A", "A")
        assert False, "should have raised"
    except ValueError:
        pass
    print(f"  ✓ test_match_self_error: ValueError raised")
    return True


# ============ Bootstrap CI ============
def test_bootstrap_ci_n_resamples():
    """bootstrap_ci 重采样 1000 次 → 区间合理 + 优势方 CI 上界应占优"""
    base = [EloRating("A", 1500.0), EloRating("B", 1500.0)]
    # 5 局 A 赢 4 局,显著优势
    matches = [
        MatchResult("A", "B", 1.0),
        MatchResult("A", "B", 2.0),
        MatchResult("A", "B", 3.0),
        MatchResult("A", "B", 4.0),
        MatchResult("B", "A", 5.0),
    ]
    ci = bootstrap_ci(base, matches, n_resamples=1000, ci=0.95, seed=42)
    assert "A" in ci, f"missing A: {list(ci.keys())}"
    assert "B" in ci, f"missing B: {list(ci.keys())}"
    lo_a, hi_a = ci["A"]
    lo_b, hi_b = ci["B"]
    assert lo_a <= hi_a, f"A CI invalid: {lo_a} > {hi_a}"
    assert lo_b <= hi_b, f"B CI invalid: {lo_b} > {hi_b}"
    # A 占优 → A 的中位数应明显高于 B 的中位数
    mid_a = (lo_a + hi_a) / 2
    mid_b = (lo_b + hi_b) / 2
    assert mid_a > mid_b + 1.0, f"A should dominate: mid_a={mid_a}, mid_b={mid_b}"
    # CI 应有合理宽度(反映不确定性)
    assert (hi_a - lo_a) > 0.5, f"A CI too narrow: {hi_a - lo_a}"
    print(f"  ✓ test_bootstrap_ci_n_resamples: A={lo_a:.2f}..{hi_a:.2f}, B={lo_b:.2f}..{hi_b:.2f}")
    return True


def test_bootstrap_ci_95pct_coverage():
    """bootstrap_ci 95%:重采样分布的范围合理,low < high"""
    base = [EloRating("X", 1500.0), EloRating("Y", 1500.0), EloRating("Z", 1500.0)]
    # X 完胜 Y 和 Z
    matches = []
    for i in range(20):
        matches.append(MatchResult("X", "Y", float(i)))
        matches.append(MatchResult("X", "Z", float(i)))
    ci = bootstrap_ci(base, matches, n_resamples=500, ci=0.95, seed=7)
    for mid in ("X", "Y", "Z"):
        assert mid in ci
        lo, hi = ci[mid]
        assert lo < hi, f"{mid} CI should have width, got {lo}..{hi}"
        # X 应远高于 1500,Y 和 Z 应低于 1500
    mid_x = sum(ci["X"]) / 2
    mid_y = sum(ci["Y"]) / 2
    mid_z = sum(ci["Z"]) / 2
    assert mid_x > 1510, f"X should be much higher, mid={mid_x}"
    assert mid_y < 1490, f"Y should be much lower, mid={mid_y}"
    assert mid_z < 1490, f"Z should be much lower, mid={mid_z}"
    print(f"  ✓ test_bootstrap_ci_95pct_coverage: X≈{mid_x:.1f}, Y≈{mid_y:.1f}, Z≈{mid_z:.1f}")
    return True


def test_bootstrap_ci_seed_reproducible():
    """相同 seed → 相同结果(顺序无关、可重放)"""
    base = [EloRating("A", 1500.0), EloRating("B", 1500.0)]
    matches = [
        MatchResult("A", "B", 1.0),
        MatchResult("B", "A", 2.0),
        MatchResult("A", "B", 3.0),
    ]
    ci1 = bootstrap_ci(base, matches, n_resamples=200, ci=0.95, seed=123)
    ci2 = bootstrap_ci(base, matches, n_resamples=200, ci=0.95, seed=123)
    assert ci1 == ci2, f"same seed should give same result: {ci1} vs {ci2}"
    print(f"  ✓ test_bootstrap_ci_seed_reproducible: identical with seed=123")
    return True


def test_bootstrap_ci_zero_matches():
    """bootstrap_ci 0 matches → 所有 model 的 CI 退化为 (base, base)"""
    base = [EloRating("A", 1500.0), EloRating("B", 1600.0)]
    ci = bootstrap_ci(base, [], n_resamples=1000, ci=0.95, seed=0)
    assert ci["A"] == (1500.0, 1500.0), f"got {ci['A']}"
    assert ci["B"] == (1600.0, 1600.0), f"got {ci['B']}"
    print(f"  ✓ test_bootstrap_ci_zero_matches: degenerate to point estimates")
    return True


# ============ WorkerPool 基础 ============
def test_workerpool_init():
    """WorkerPool 初始化:workers, 默认 strategy=lottery, loads 全 0"""
    wp = WorkerPool(["w1", "w2", "w3"])
    assert wp.workers() == ["w1", "w2", "w3"]
    assert wp.get_strategy() == "lottery"
    loads = wp.worker_loads()
    assert loads == {"w1": 0, "w2": 0, "w3": 0}, f"got {loads}"
    wp.shutdown()
    print(f"  ✓ test_workerpool_init: 3 workers, strategy=lottery, loads=0")
    return True


def test_workerpool_lottery_random():
    """lottery 策略:submit 100 次 → 应至少 2 个不同 worker 被选中"""
    wp = WorkerPool(["w1", "w2", "w3"], max_jobs_per_worker=8)

    def noop():
        return 1

    selected = []
    # 直接读 _pick_worker 验证 lottery 行为
    for _ in range(100):
        w = wp._pick_worker()
        selected.append(w)
    unique = set(selected)
    assert len(unique) >= 2, f"lottery should hit multiple workers, got {unique}"
    wp.shutdown()
    print(f"  ✓ test_workerpool_lottery_random: hit {len(unique)}/3 workers in 100 picks")
    return True


def test_workerpool_shortest_queue_pick():
    """shortest_queue 策略:给 w1 加重 → 下次应选 w2 或 w3"""
    wp = WorkerPool(["w1", "w2", "w3"], max_jobs_per_worker=8)
    wp.set_strategy("shortest_queue")

    # 手动增加 w1 的 load
    with wp._lock:
        wp._loads["w1"] = 5
    # 选 → 应避开 w1
    picked = wp._pick_worker()
    assert picked in ("w2", "w3"), f"should pick non-w1, got {picked}"

    # 把 w2 也加到 5,w3 = 0 → 选 w3
    with wp._lock:
        wp._loads["w2"] = 5
    picked2 = wp._pick_worker()
    assert picked2 == "w3", f"should pick w3 (only one with 0), got {picked2}"
    wp.shutdown()
    print(f"  ✓ test_workerpool_shortest_queue_pick: avoided busy, picked min-load")
    return True


def test_workerpool_loads():
    """worker_loads 反映当前活跃 job 数(用 barrier 等所有 job 进入)"""
    wp = WorkerPool(["w1", "w2"], max_jobs_per_worker=4)

    entered = threading.Semaphore(0)
    release = threading.Event()

    def slow():
        entered.release()
        release.wait(timeout=5.0)
        return "done"

    # 提交 4 个 job;lottery 模式随机分配
    futs = [wp.submit(slow) for _ in range(4)]
    # 等所有 4 个 job 都已进入(被 worker 接受)
    for _ in range(4):
        entered.acquire(timeout=2.0)
    # 短暂等待 _loads 同步
    time.sleep(0.05)
    loads = wp.worker_loads()
    total = sum(loads.values())
    assert total == 4, f"expected 4 active jobs, got {loads}"

    release.set()
    for f in futs:
        f.result(timeout=5.0)
    # 完成后 loads 应归零
    loads_after = wp.worker_loads()
    assert sum(loads_after.values()) == 0, f"loads should be 0 after, got {loads_after}"
    wp.shutdown()
    print(f"  ✓ test_workerpool_loads: 4 jobs in flight, sum={total}, then 0")
    return True


def test_workerpool_concurrent():
    """多 worker 并发执行:10 个 job 在 2 worker 上都能完成"""
    wp = WorkerPool(["a", "b"], max_jobs_per_worker=8)
    wp.set_strategy("lottery")

    def task(x):
        time.sleep(0.01)
        return x * 2

    futs = [wp.submit(task, i) for i in range(20)]
    results = sorted([f.result(timeout=5.0) for f in futs])
    expected = sorted([i * 2 for i in range(20)])
    assert results == expected, f"got {results}"
    wp.shutdown()
    print(f"  ✓ test_workerpool_concurrent: 20 jobs done, results correct")
    return True


def test_workerpool_set_strategy_invalid():
    """set_strategy 非法值 → ValueError"""
    wp = WorkerPool(["w1"])
    try:
        wp.set_strategy("random")
        assert False, "should have raised"
    except ValueError:
        pass
    wp.shutdown()
    print(f"  ✓ test_workerpool_set_strategy_invalid: ValueError raised")
    return True


# ============ 边界 ============
def test_zero_models():
    """0 models:ranked 空,get_rating 0"""
    lb = EloLeaderboard()
    assert len(lb.ranked()) == 0
    assert lb.get_rating("anything") == 0.0
    print(f"  ✓ test_zero_models: empty leaderboard")
    return True


def test_zero_matches():
    """0 matches → 所有 rating 保持 initial"""
    lb = EloLeaderboard()
    lb.add_model("A", initial_rating=2000.0)
    lb.add_model("B", initial_rating=1000.0)
    # 不 record_match
    assert lb.get_rating("A") == 2000.0
    assert lb.get_rating("B") == 1000.0
    assert len(lb.ranked()) == 2
    print(f"  ✓ test_zero_matches: A=2000, B=1000 unchanged")
    return True


# ============ JSON 序列化 ============
def test_json_serialization():
    """MatchResult / EloRating / Leaderboard / WorkerPool 都能 to_json"""
    lb = EloLeaderboard(k_factor=4.0)
    lb.add_model("A", initial_rating=1500.0)
    lb.add_model("B", initial_rating=1500.0)
    lb.record_match("A", "B", timestamp=1.5)

    # EloRating
    s1 = to_json(EloRating("X", 1600.0, 3))
    d1 = json.loads(s1)
    assert d1 == {"model_id": "X", "rating": 1600.0, "matches_played": 3}

    # MatchResult
    s2 = to_json(MatchResult("A", "B", 1.5))
    d2 = json.loads(s2)
    assert d2 == {"winner_id": "A", "loser_id": "B", "timestamp": 1.5}

    # Leaderboard
    s3 = to_json(lb)
    d3 = json.loads(s3)
    assert d3["k_factor"] == 4.0
    assert "A" in d3["ratings"] and "B" in d3["ratings"]
    assert d3["ratings"]["A"]["matches_played"] == 1

    # WorkerPool
    wp = WorkerPool(["w1", "w2"])
    s4 = to_json(wp)
    d4 = json.loads(s4)
    assert d4["strategy"] == "lottery"
    assert d4["workers"] == ["w1", "w2"]
    assert d4["loads"] == {"w1": 0, "w2": 0}
    wp.shutdown()
    print(f"  ✓ test_json_serialization: all 4 types serialize cleanly")
    return True


# ============ Main ============
if __name__ == "__main__":
    tests = [
        test_add_model_default_1500,
        test_record_match_winner_gains,
        test_record_match_loser_drops,
        test_k4_max_change,
        test_ranked_ordering,
        test_get_rating_unknown_zero,
        test_match_self_error,
        test_bootstrap_ci_n_resamples,
        test_bootstrap_ci_95pct_coverage,
        test_bootstrap_ci_seed_reproducible,
        test_bootstrap_ci_zero_matches,
        test_workerpool_init,
        test_workerpool_lottery_random,
        test_workerpool_shortest_queue_pick,
        test_workerpool_loads,
        test_workerpool_concurrent,
        test_workerpool_set_strategy_invalid,
        test_zero_models,
        test_zero_matches,
        test_json_serialization,
    ]
    print(f"=== elo_ranking 端到端测试 ({len(tests)} 项) ===")
    passed = 0
    failed = []
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
            failed.append(t.__name__)
    print(f"\n=== 结果: {passed}/{len(tests)} 通过 ===")
    if failed:
        print(f"失败: {failed}")
        sys.exit(1)
