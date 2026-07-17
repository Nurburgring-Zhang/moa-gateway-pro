"""cross_iter_synth 单元测试 (>= 16 测试)

真实 assert, 严禁 mock。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.cross_iter_synth import (
    JACCARD_CONVERGENCE_THRESHOLD,
    IterationSnapshot,
    Step5Mode,
    SynthesisMode,
    best_of_each_mode,
    convergence_mode,
    recommended_adoption_mode,
    result_to_dict,
    run_step5,
    snapshot_from_dict,
    snapshot_to_dict,
    step5_payload,
    step5_result_to_dict,
    synth_payload,
)

# ============ 辅助: 构造 IterationSnapshot ============

def make_snap(
    iter_idx: int,
    proposals: list,
    best_score: float,
    best_proposal_idx: int,
    summary: str = "",
) -> IterationSnapshot:
    return IterationSnapshot(
        iter_idx=iter_idx,
        proposals=proposals,
        best_score=best_score,
        best_proposal_idx=best_proposal_idx,
        summary=summary,
    )


# ============ 枚举测试 ============

def test_synthesis_mode_three_values():
    """3 个 SynthesisMode 枚举值"""
    assert SynthesisMode.CONVERGENCE.value == "convergence"
    assert SynthesisMode.BEST_OF_EACH.value == "best_of_each"
    assert SynthesisMode.RECOMMENDED_ADOPTION.value == "recommended_adoption"
    assert len(SynthesisMode) == 3


def test_step5_mode_three_values():
    """3 个 Step5Mode 枚举值"""
    assert Step5Mode.SINTESIS_CENTRAL.value == "sintesis_central"
    assert Step5Mode.SELF_IMPROVE.value == "self_improve"
    assert Step5Mode.SKIP.value == "skip"
    assert len(Step5Mode) == 3


# ============ convergence_mode 测试 ============

def test_convergence_mode_high_jaccard():
    """高 Jaccard: 三轮讨论相似主题, 视为 convergent"""
    # 使用相同的核心词, 让 Jaccard 远超 0.5
    s1 = make_snap(
        0,
        ["python caching async performance"],
        72.0, 0,
        "python caching async",
    )
    s2 = make_snap(
        1,
        ["python caching async performance"],
        78.0, 0,
        "python caching async",
    )
    s3 = make_snap(
        2,
        ["python caching async performance"],
        80.0, 0,
        "python caching async",
    )
    res = convergence_mode([s1, s2, s3])
    assert res.mode == SynthesisMode.CONVERGENCE
    assert 0 in res.sources and 1 in res.sources and 2 in res.sources
    assert "CONVERGENT" in res.output or "convergent" in res.output.lower()
    # 公共关键词应包含 python/caching/async/performance
    out_low = res.output.lower()
    assert "python" in out_low
    assert "caching" in out_low
    assert "async" in out_low
    assert res.confidence > JACCARD_CONVERGENCE_THRESHOLD


def test_convergence_mode_low_jaccard():
    """低 Jaccard: 三轮讨论不同主题, 视为 divergent"""
    s1 = make_snap(
        0,
        ["Database indexing strategy for relational queries"],
        60.0, 0,
        "Database indexing relational",
    )
    s2 = make_snap(
        1,
        ["GraphQL schema design for microservices"],
        65.0, 0,
        "GraphQL schema microservices",
    )
    s3 = make_snap(
        2,
        ["Machine learning model pruning quantization deployment"],
        70.0, 0,
        "Machine learning pruning quantization",
    )
    res = convergence_mode([s1, s2, s3])
    assert res.mode == SynthesisMode.CONVERGENCE
    assert "DIVERGENT" in res.output or "no shared keywords" in res.output
    assert res.confidence <= JACCARD_CONVERGENCE_THRESHOLD + 0.01


# ============ best_of_each_mode 测试 ============

def test_best_of_each_picks_each_iter_best():
    """每个 iter 取 best_proposal_idx 对应的文本"""
    s1 = make_snap(0, ["proposal A", "proposal B is best here", "proposal C"], 80.0, 1)
    s2 = make_snap(1, ["proposal D", "proposal E", "proposal F is top"], 90.0, 2)
    s3 = make_snap(2, ["proposal G is the winner", "proposal H"], 75.0, 0)
    res = best_of_each_mode([s1, s2, s3])
    assert res.mode == SynthesisMode.BEST_OF_EACH
    out = res.output
    assert "proposal B is best here" in out
    assert "proposal F is top" in out
    assert "proposal G is the winner" in out
    assert "score=80.00" in out
    assert "score=90.00" in out
    assert "score=75.00" in out
    # sources 应包含所有 iter_idx
    assert res.sources == [0, 1, 2]


# ============ recommended_adoption_mode 测试 ============

def test_recommended_adoption_curr_wins():
    """curr > prev * 1.05 → adopt curr"""
    prev = make_snap(0, ["p1", "p2"], 50.0, 0)
    curr = make_snap(1, ["c1", "c2"], 60.0, 1)  # 60 > 50 * 1.05 = 52.5
    res = recommended_adoption_mode(curr, prev)
    assert res.mode == SynthesisMode.RECOMMENDED_ADOPTION
    assert "curr" in res.output
    assert 0 in res.sources and 1 in res.sources
    assert res.confidence > 0
    assert "proposal[1]" in res.output  # curr.best_proposal_idx = 1


def test_recommended_adoption_prev_wins():
    """prev > curr * 1.05 → adopt prev"""
    prev = make_snap(0, ["p1 is better", "p2"], 80.0, 0)
    curr = make_snap(1, ["c1", "c2"], 50.0, 0)  # 80 > 50 * 1.05 = 52.5
    res = recommended_adoption_mode(curr, prev)
    assert res.mode == SynthesisMode.RECOMMENDED_ADOPTION
    assert "prev" in res.output
    assert "p1 is better" in res.output
    assert 0 in res.sources and 1 in res.sources


def test_recommended_adoption_tie():
    """差距 < 5% → either"""
    prev = make_snap(0, ["p1", "p2"], 70.0, 0)
    curr = make_snap(1, ["c1", "c2"], 71.0, 1)  # 71/70 = 1.014, < 1.05
    res = recommended_adoption_mode(curr, prev)
    assert res.mode == SynthesisMode.RECOMMENDED_ADOPTION
    assert "either" in res.output
    # 平局时 confidence 应较小
    assert res.confidence < 0.1


# ============ run_step5 测试 ============

def test_run_step5_sintesis_central():
    """SINTESIS_CENTRAL → 跑 convergence_mode"""
    s1 = make_snap(0, ["alpha beta gamma", "delta epsilon"], 70.0, 0, "alpha beta")
    s2 = make_snap(1, ["alpha beta zeta", "eta theta"], 75.0, 0, "alpha beta")
    res = run_step5([s1, s2], Step5Mode.SINTESIS_CENTRAL)
    assert res.mode == Step5Mode.SINTESIS_CENTRAL
    assert "convergence_mode" in res.action_taken
    assert "CONVERGENT" in res.output or "DIVERGENT" in res.output


def test_run_step5_self_improve():
    """SELF_IMPROVE → best_of_each + 改进建议"""
    s1 = make_snap(0, ["idea one", "idea two"], 60.0, 0)
    s2 = make_snap(1, ["idea three", "idea four"], 65.0, 1)
    res = run_step5([s1, s2], Step5Mode.SELF_IMPROVE)
    assert res.mode == Step5Mode.SELF_IMPROVE
    assert "best_of_each_mode" in res.action_taken
    assert "Improvement suggestions:" in res.output
    assert "iter0:" in res.output
    assert "iter1:" in res.output


def test_run_step5_skip():
    """SKIP → 仅返回 best_proposal (best_score 最高)"""
    s1 = make_snap(0, ["low score proposal"], 50.0, 0)
    s2 = make_snap(1, ["mid score proposal"], 70.0, 0)
    s3 = make_snap(2, ["top score proposal here"], 95.0, 0)
    res = run_step5([s1, s2, s3], Step5Mode.SKIP)
    assert res.mode == Step5Mode.SKIP
    assert "iter2" in res.output
    assert "top score proposal here" in res.output
    assert "95.00" in res.output or "score=95" in res.output
    assert "skipped" in res.action_taken


# ============ 边界测试 ============

def test_boundary_zero_iters_convergence():
    """0 iters → confidence=0, output 表示无 iter"""
    res = convergence_mode([])
    assert res.mode == SynthesisMode.CONVERGENCE
    assert res.sources == []
    assert res.confidence == 0.0
    assert "no iterations" in res.output.lower()


def test_boundary_zero_iters_best_of_each():
    """0 iters → best_of_each 应空输出"""
    res = best_of_each_mode([])
    assert res.sources == []
    assert res.confidence == 0.0
    assert "no iterations" in res.output.lower()


def test_boundary_zero_iters_step5_skip():
    """0 iters → SKIP 输出空标记"""
    res = run_step5([], Step5Mode.SKIP)
    assert res.mode == Step5Mode.SKIP
    assert "no iterations" in res.output.lower()
    assert "empty" in res.action_taken.lower() or "skipped" in res.action_taken.lower()


def test_boundary_one_iter_convergence():
    """1 iter → 单一 snapshot, confidence=1.0 (trivially convergent)"""
    s1 = make_snap(0, ["hello world unique words", "second proposal"], 50.0, 0, "summary text")
    res = convergence_mode([s1])
    assert res.mode == SynthesisMode.CONVERGENCE
    assert res.sources == [0]
    assert res.confidence == 1.0
    assert "Single iteration" in res.output


def test_boundary_one_iter_step5_sintesis_central():
    """1 iter → SINTESIS_CENTRAL 仍能跑"""
    s1 = make_snap(0, ["only iter content alpha beta"], 60.0, 0)
    res = run_step5([s1], Step5Mode.SINTESIS_CENTRAL)
    assert res.mode == Step5Mode.SINTESIS_CENTRAL
    assert "1 iteration" in res.action_taken or "convergence_mode" in res.action_taken
    assert res.output  # 非空


# ============ confidence 0-1 测试 ============

def test_confidence_in_unit_interval():
    """所有模式的 confidence 必须在 [0, 1]"""
    iters = [
        make_snap(i, [f"topic {i} content alpha beta {i*7}"], 60.0 + i * 5, 0)
        for i in range(5)
    ]
    c = convergence_mode(iters)
    b = best_of_each_mode(iters)
    r = recommended_adoption_mode(iters[-1], iters[-2])
    for res in (c, b, r):
        assert 0.0 <= res.confidence <= 1.0, f"confidence out of range: {res.confidence}"


# ============ sources iter indices 测试 ============

def test_sources_iter_indices_correct():
    """sources 必须准确反映引用的 iter_idx"""
    s0 = make_snap(0, ["a"], 10.0, 0)
    s3 = make_snap(3, ["b"], 20.0, 0)
    s7 = make_snap(7, ["c"], 30.0, 0)
    c = convergence_mode([s0, s3, s7])
    b = best_of_each_mode([s0, s3, s7])
    r = recommended_adoption_mode(s3, s0)
    assert c.sources == [0, 3, 7]
    assert b.sources == [0, 3, 7]
    assert r.sources == [0, 3]  # prev=0, curr=3
    # 顺序
    for res in (c, b, r):
        for idx in res.sources:
            assert isinstance(idx, int)
        # sources 应保持原始顺序
        assert res.sources == sorted(res.sources) or res.sources in ([0, 3], [0, 3, 7])


# ============ JSON 序列化测试 ============

def test_json_serialization_roundtrip():
    """完整 JSON 序列化往返"""
    s1 = make_snap(0, ["alpha beta", "gamma delta"], 60.0, 0, "summary one")
    s2 = make_snap(1, ["epsilon zeta", "eta theta"], 80.0, 1, "summary two")

    # snapshot 序列化
    s1_dict = snapshot_to_dict(s1)
    s1_back = snapshot_from_dict(s1_dict)
    assert s1_back.iter_idx == s1.iter_idx
    assert s1_back.proposals == s1.proposals
    assert s1_back.best_score == s1.best_score
    assert s1_back.best_proposal_idx == s1.best_proposal_idx
    assert s1_back.summary == s1.summary

    # SynthesisResult 序列化
    res = convergence_mode([s1, s2])
    payload = synth_payload(res)
    parsed = json.loads(payload)
    assert parsed["mode"] == "convergence"
    assert parsed["confidence"] == res.confidence
    assert parsed["sources"] == res.sources
    assert "output" in parsed

    # Step5Result 序列化
    step_res = run_step5([s1, s2], Step5Mode.SELF_IMPROVE)
    step_payload = step5_payload(step_res)
    parsed_step = json.loads(step_payload)
    assert parsed_step["mode"] == "self_improve"
    assert "output" in parsed_step
    assert "action_taken" in parsed_step

    # 完整 round-trip: result_to_dict 字段一致
    d = result_to_dict(res)
    assert d["mode"] == "convergence"
    d2 = step5_result_to_dict(step_res)
    assert d2["mode"] == "self_improve"
