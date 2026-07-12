"""consensus 真实测试(非 mock)"""
import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.consensus import (
    Vote, ConsensusResult, TierStat,
    ensemble_vote, should_rebalance, rebalance_endpoints,
)


def test_ensemble_vote_majority():
    """4 票 A,1 票 B → winner=A, agreement=0.8"""
    votes = [
        Vote("v1", "A", 0.9, ""),
        Vote("v2", "A", 0.8, ""),
        Vote("v3", "A", 0.7, ""),
        Vote("v4", "A", 0.6, ""),
        Vote("v5", "B", 0.5, ""),
    ]
    r = ensemble_vote(votes, method="majority")
    assert r.winner == "A", f"got {r.winner}"
    assert abs(r.agreement_ratio - 0.8) < 1e-9, f"got {r.agreement_ratio}"
    assert r.method == "majority"
    print(f"  ✓ test_ensemble_vote_majority: winner={r.winner}, agreement={r.agreement_ratio}")
    return True


def test_ensemble_vote_weighted():
    """weighted:1 票 B (conf=1.0) 应胜过 4 票 A (conf=0.1)"""
    votes = [
        Vote("v1", "A", 0.1, ""),
        Vote("v2", "A", 0.1, ""),
        Vote("v3", "A", 0.1, ""),
        Vote("v4", "A", 0.1, ""),
        Vote("v5", "B", 1.0, ""),
    ]
    r = ensemble_vote(votes, method="weighted")
    assert r.winner == "B", f"got {r.winner}, scores distribution"
    # B conf=1.0 vs A total=0.4 → B wins
    assert r.agreement_ratio > 0.5
    print(f"  ✓ test_ensemble_vote_weighted: winner={r.winner}, agreement={r.agreement_ratio:.3f}")
    return True


def test_ensemble_vote_borda():
    """borda:candidate X 出现 5 次,Y 出现 2 次 → X 胜(高频 candidate 优先)"""
    votes = [
        Vote("v1", "X", 0.5, ""),
        Vote("v2", "X", 0.5, ""),
        Vote("v3", "X", 0.5, ""),
        Vote("v4", "X", 0.5, ""),
        Vote("v5", "X", 0.5, ""),
        Vote("v6", "Y", 0.99, ""),  # 虽然 conf 高,但频次低
        Vote("v7", "Y", 0.99, ""),
    ]
    r = ensemble_vote(votes, method="borda")
    assert r.winner == "X", f"got {r.winner}"
    assert r.method == "borda"
    print(f"  ✓ test_ensemble_vote_borda: winner={r.winner}, agreement={r.agreement_ratio:.3f}")
    return True


def test_ensemble_vote_weighted_high_conf_wins():
    """weighted 高 confidence 必胜(更明确场景)"""
    votes = [
        Vote("v1", "A", 0.2, ""),
        Vote("v2", "A", 0.3, ""),
        Vote("v3", "B", 0.95, ""),
    ]
    r = ensemble_vote(votes, method="weighted")
    assert r.winner == "B", f"got {r.winner}"
    print(f"  ✓ test_ensemble_vote_weighted_high_conf_wins: winner={r.winner}")
    return True


def test_ensemble_vote_approval():
    """approval:同一 voter 批准多个 candidate,该 voter 投给每个 candidate 的 conf 都计分

    voter v1 批准 A(conf=0.9) 和 B(conf=0.6) → 视作 v1 批准 A 加 0.9,批准 B 加 0.6
    voter v2 批准 A(conf=0.8) → 视作 v2 批准 A 加 0.8
    A 总分 = 0.9 + 0.8 = 1.7, 归一化(除以 voter 数=2) = 0.85
    B 总分 = 0.6, 归一化 = 0.3
    A 胜
    """
    votes = [
        Vote("v1", "A", 0.9, ""),
        Vote("v1", "B", 0.6, ""),  # 同一 voter 批准多个
        Vote("v2", "A", 0.8, ""),
    ]
    r = ensemble_vote(votes, method="approval")
    assert r.winner == "A", f"got {r.winner}"
    assert r.method == "approval"
    print(f"  ✓ test_ensemble_vote_approval: winner={r.winner}, agreement={r.agreement_ratio:.3f}")
    return True


def test_entropy_calculation():
    """信息熵:均匀分布 = 1.0(归一化后)"""
    # 3 candidate 各 1 票 → 均匀 → entropy = 1.0 (log 3 / log 3 = 1)
    votes = [
        Vote("v1", "A", 0.5, ""),
        Vote("v2", "B", 0.5, ""),
        Vote("v3", "C", 0.5, ""),
    ]
    r = ensemble_vote(votes, method="majority")
    assert abs(r.entropy - 1.0) < 1e-9, f"expected 1.0, got {r.entropy}"

    # 全投 A → entropy = 0
    votes2 = [Vote(f"v{i}", "A", 0.5, "") for i in range(5)]
    r2 = ensemble_vote(votes2, method="majority")
    assert abs(r2.entropy - 0.0) < 1e-9, f"expected 0.0, got {r2.entropy}"
    print(f"  ✓ test_entropy_calculation: uniform={r.entropy:.3f}, unanimous={r2.entropy:.3f}")
    return True


def test_should_rebalance_high_tier():
    """高 tier 过载 → True"""
    stats = {
        "free": TierStat("free", 2, 10, 10, 100.0, 0.0, 10, 0),
        "premium": TierStat("premium", 2, 100, 100, 200.0, 0.01, 100, 0),
        # premium: util = (2-0)/2 = 1.0 > 0.8 → 触发
    }
    cfg = {"high_utilization_threshold": 0.8}
    assert should_rebalance(stats, cfg) is True
    print(f"  ✓ test_should_rebalance_high_tier: True (premium util=1.0)")
    return True


def test_should_rebalance_low_tier():
    """低 tier 闲置 → True"""
    stats = {
        "free": TierStat("free", 5, 1, 5, 100.0, 0.0, 5, 4),
        # free: util = (5-4)/5 = 0.2 → 触发(默认 low_threshold=0.2,严格小于)
        "premium": TierStat("premium", 3, 50, 50, 200.0, 0.01, 50, 1),
        # premium: util = 2/3 ≈ 0.67, 不触发
    }
    cfg = {"low_utilization_threshold": 0.2}
    assert should_rebalance(stats, cfg) is True
    print(f"  ✓ test_should_rebalance_low_tier: True (free util=0.2)")
    return True


def test_should_rebalance_balanced():
    """所有 tier 平衡 → False"""
    stats = {
        "free": TierStat("free", 3, 10, 10, 100.0, 0.0, 10, 1),
        # util = 2/3 ≈ 0.67
        "lite": TierStat("lite", 3, 10, 10, 100.0, 0.0, 10, 1),
        "standard": TierStat("standard", 3, 10, 10, 100.0, 0.005, 10, 1),
        "premium": TierStat("premium", 3, 10, 10, 200.0, 0.01, 10, 1),
        # util = 2/3 ≈ 0.67, 都平衡
    }
    cfg = {"high_utilization_threshold": 0.8, "low_utilization_threshold": 0.2}
    assert should_rebalance(stats, cfg) is False
    print(f"  ✓ test_should_rebalance_balanced: False")
    return True


def test_rebalance_endpoints_demote():
    """高 tier 过载 → 至少 1 个 premium endpoint 被下沉"""
    endpoints = [
        {"id": "p1", "tier": "premium", "success_rate": 0.99, "avg_latency_ms": 100, "avg_cost": 0.01},
        {"id": "p2", "tier": "premium", "success_rate": 0.99, "avg_latency_ms": 100, "avg_cost": 0.01},
        {"id": "p3", "tier": "premium", "success_rate": 0.50, "avg_latency_ms": 2000, "avg_cost": 0.05},
        {"id": "p4", "tier": "premium", "success_rate": 0.50, "avg_latency_ms": 2000, "avg_cost": 0.05},
        {"id": "p5", "tier": "premium", "success_rate": 0.40, "avg_latency_ms": 3000, "avg_cost": 0.10},
    ]
    stats = {
        "premium": TierStat("premium", 5, 100, 100, 500.0, 0.01, 100, 0),  # util=1.0
    }
    cfg = {"high_utilization_threshold": 0.8}
    result = rebalance_endpoints(endpoints, stats, cfg)
    demoted = [ep for ep in result if ep.get("rebalance_action") == "demoted"]
    assert len(demoted) >= 1, f"expected ≥1 demoted, got {len(demoted)}: {[(e['id'], e.get('tier')) for e in result]}"
    # 被下沉的应该是价值分最低的(p5)
    assert demoted[0]["id"] == "p5", f"expected p5 demoted (lowest score), got {demoted[0]['id']}"
    assert demoted[0]["tier"] == "standard", f"expected standard, got {demoted[0]['tier']}"
    print(f"  ✓ test_rebalance_endpoints_demote: {len(demoted)} demoted, p5→{demoted[0]['tier']}")
    return True


def test_rebalance_endpoints_promote():
    """低 tier 闲置 → 至少 1 个 free endpoint 被上浮"""
    endpoints = [
        {"id": "f1", "tier": "free", "success_rate": 0.99, "avg_latency_ms": 100, "avg_cost": 0.0},
        {"id": "f2", "tier": "free", "success_rate": 0.99, "avg_latency_ms": 100, "avg_cost": 0.0},
        {"id": "f3", "tier": "free", "success_rate": 0.50, "avg_latency_ms": 500, "avg_cost": 0.0},
        {"id": "f4", "tier": "free", "success_rate": 0.40, "avg_latency_ms": 600, "avg_cost": 0.0},
        {"id": "f5", "tier": "free", "success_rate": 0.30, "avg_latency_ms": 700, "avg_cost": 0.0},
    ]
    stats = {
        "free": TierStat("free", 5, 1, 5, 200.0, 0.0, 5, 5),  # util = (5-5)/5 = 0.0 < 0.2 → 触发
    }
    cfg = {"low_utilization_threshold": 0.2}
    result = rebalance_endpoints(endpoints, stats, cfg)
    promoted = [ep for ep in result if ep.get("rebalance_action") == "promoted"]
    assert len(promoted) >= 1, f"expected ≥1 promoted, got {len(promoted)}: {[(e['id'], e.get('tier')) for e in result]}"
    # 被上浮的应该是价值分最高的(f1)
    assert promoted[0]["id"] == "f1", f"expected f1 promoted (highest score), got {promoted[0]['id']}"
    assert promoted[0]["tier"] == "lite", f"expected lite, got {promoted[0]['tier']}"
    print(f"  ✓ test_rebalance_endpoints_promote: {len(promoted)} promoted, f1→{promoted[0]['tier']}")
    return True


def test_consensus_with_no_votes():
    """0 票 → winner=None, score=0"""
    r = ensemble_vote([], method="majority")
    assert r.winner is None, f"got {r.winner}"
    assert r.score == 0.0
    assert r.agreement_ratio == 0.0
    assert r.entropy == 0.0
    assert len(r.votes) == 0
    print(f"  ✓ test_consensus_with_no_votes: winner=None, score=0")
    return True


def test_consensus_with_tie():
    """平局(2 票 A,2 票 B)→ majority 下 winner 可能是 A 或 B,但 agreement_ratio=0.5"""
    votes = [
        Vote("v1", "A", 0.5, ""),
        Vote("v2", "A", 0.5, ""),
        Vote("v3", "B", 0.5, ""),
        Vote("v4", "B", 0.5, ""),
    ]
    r = ensemble_vote(votes, method="majority")
    assert r.winner in ("A", "B"), f"got {r.winner}"
    assert abs(r.agreement_ratio - 0.5) < 1e-9, f"got {r.agreement_ratio}"
    # 熵应该 = 1.0(均匀二分布)
    assert abs(r.entropy - 1.0) < 1e-9, f"got {r.entropy}"
    # score = 0.5 * (1 - 1.0) = 0.0(平局共识度低)
    assert abs(r.score - 0.0) < 1e-9, f"got {r.score}"
    print(f"  ✓ test_consensus_with_tie: winner={r.winner}, agreement={r.agreement_ratio}, entropy={r.entropy:.2f}")
    return True


def test_borda_three_candidates_ranking():
    """borda 三候选:rank 1=Z(4 票),rank 2=Y(3 票),rank 3=X(1 票)"""
    votes = [
        Vote("v1", "Z", 0.5, ""),
        Vote("v2", "Z", 0.5, ""),
        Vote("v3", "Z", 0.5, ""),
        Vote("v4", "Z", 0.5, ""),
        Vote("v5", "Y", 0.5, ""),
        Vote("v6", "Y", 0.5, ""),
        Vote("v7", "Y", 0.5, ""),
        Vote("v8", "X", 0.5, ""),
    ]
    r = ensemble_vote(votes, method="borda")
    assert r.winner == "Z"
    # Z rank 1 → 2 分,Y rank 2 → 1 分,X rank 3 → 0 分,总 3 分
    # agreement = 2/3
    assert abs(r.agreement_ratio - 2 / 3) < 1e-9, f"got {r.agreement_ratio}"
    print(f"  ✓ test_borda_three_candidates_ranking: winner={r.winner}, agreement={r.agreement_ratio:.3f}")
    return True


def test_majority_all_same_candidate_high_score():
    """全票一致 → score 高,entropy=0"""
    votes = [Vote(f"v{i}", "A", 0.9, "") for i in range(10)]
    r = ensemble_vote(votes, method="majority")
    assert r.winner == "A"
    assert r.agreement_ratio == 1.0
    assert r.entropy == 0.0
    assert r.score > 0.9
    print(f"  ✓ test_majority_all_same_candidate_high_score: score={r.score:.3f}")
    return True


def test_rebalance_endpoints_no_change_when_balanced():
    """平衡时 endpoint tier 不变"""
    endpoints = [
        {"id": "p1", "tier": "premium", "success_rate": 0.9, "avg_latency_ms": 100, "avg_cost": 0.01},
        {"id": "f1", "tier": "free", "success_rate": 0.9, "avg_latency_ms": 100, "avg_cost": 0.0},
    ]
    stats = {
        "premium": TierStat("premium", 2, 50, 50, 100.0, 0.01, 50, 1),  # util=0.5
        "free": TierStat("free", 2, 50, 50, 100.0, 0.0, 50, 1),  # util=0.5
    }
    cfg = {"high_utilization_threshold": 0.8, "low_utilization_threshold": 0.2}
    result = rebalance_endpoints(endpoints, stats, cfg)
    for ep in result:
        assert "rebalance_action" not in ep, f"unexpected action on {ep['id']}: {ep.get('rebalance_action')}"
    print(f"  ✓ test_rebalance_endpoints_no_change_when_balanced: all unchanged")
    return True


if __name__ == "__main__":
    tests = [
        test_ensemble_vote_majority,
        test_ensemble_vote_weighted,
        test_ensemble_vote_weighted_high_conf_wins,
        test_ensemble_vote_borda,
        test_borda_three_candidates_ranking,
        test_ensemble_vote_approval,
        test_entropy_calculation,
        test_should_rebalance_high_tier,
        test_should_rebalance_low_tier,
        test_should_rebalance_balanced,
        test_rebalance_endpoints_demote,
        test_rebalance_endpoints_promote,
        test_rebalance_endpoints_no_change_when_balanced,
        test_consensus_with_no_votes,
        test_consensus_with_tie,
        test_majority_all_same_candidate_high_score,
    ]
    print(f"=== consensus 端到端测试 ({len(tests)} 项) ===")
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
