"""conflict_arbiter 单元测试 (18 测试)

真实 assert, 严禁 mock。
"""
from __future__ import annotations
import pytest
import sys
from pathlib import Path

# 允许直接 import
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.conflict_arbiter import (
    ConflictOption,
    ConflictVerdict,
    build_conflict_from_proposals,
    score_option,
    arbitrate,
    fuse_decision,
    option_to_dict,
    verdict_to_dict,
    WEIGHT_VIABILITY,
    WEIGHT_SUPPORT,
    WEIGHT_EMPIRICAL,
    WEIGHT_COMPILABLE,
    EMPIRICAL_SATURATION,
    JACCARD_THRESHOLD,
)


# ============ 辅助 ============

def make_option(
    oid: str,
    desc: str,
    support: List[int],
    viab: Dict[int, float],
    compilable=None,
    empirical: int = 0,
) -> ConflictOption:
    return ConflictOption(
        option_id=oid,
        description=desc,
        supporting_proposals=support,
        viability_scores=viab,
        command_compilable=compilable,
        empirical_evidence_count=empirical,
    )


# ============ build_conflict_from_proposals 测试 ============

def test_build_conflict_keywords_partition():
    """按关键词把 proposals 划入 A/B"""
    proposals = [
        "We should use Redis for caching layer",  # 0 → A
        "MongoDB would be better for our needs",  # 1 → B
        "Postgres handles JSON well enough",      # 2 → B
        "Use a caching layer with Redis",         # 3 → A
    ]
    a, b = build_conflict_from_proposals(
        proposals,
        option_a_keywords=["redis", "caching"],
        option_b_keywords=["mongodb", "postgres"],
        option_a_label="Use Redis",
        option_b_label="Use MongoDB",
    )
    assert a.option_id == "A"
    assert b.option_id == "B"
    assert a.supporting_proposals == [0, 3]
    assert b.supporting_proposals == [1, 2]


def test_build_conflict_no_match():
    """无关键词命中 → 两边都空"""
    proposals = ["Hello world", "Generic text"]
    a, b = build_conflict_from_proposals(
        proposals,
        option_a_keywords=["alpha"],
        option_b_keywords=["beta"],
        option_a_label="A",
        option_b_label="B",
    )
    assert a.supporting_proposals == []
    assert b.supporting_proposals == []


# ============ 评分权重测试 ============

def test_weights_sum_to_one():
    """4 维权重总和 = 1.0"""
    assert round(WEIGHT_VIABILITY + WEIGHT_SUPPORT + WEIGHT_EMPIRICAL + WEIGHT_COMPILABLE, 6) == 1.0


def test_weight_viability_is_040():
    assert WEIGHT_VIABILITY == 0.40


def test_weight_support_is_025():
    assert WEIGHT_SUPPORT == 0.25


def test_weight_empirical_is_020():
    assert WEIGHT_EMPIRICAL == 0.20


def test_weight_compilable_is_015():
    assert WEIGHT_COMPILABLE == 0.15


# ============ 4 维分数测试 ============

def test_empirical_3_evidence_is_full():
    """empirical_evidence=3 → 1.0 (饱和)"""
    opt = make_option("A", "x", [0], {0: 0.5}, empirical=3)
    sc = score_option(opt, total_proposals=1)
    assert sc["empirical"] == 1.0


def test_empirical_1_evidence_is_one_third():
    """empirical_evidence=1 → 0.3333"""
    opt = make_option("A", "x", [0], {0: 0.5}, empirical=1)
    sc = score_option(opt, total_proposals=1)
    assert sc["empirical"] == pytest.approx(1 / 3, abs=1e-4)


def test_compilable_true_is_one():
    opt = make_option("A", "x", [0], {0: 0.5}, compilable=True)
    sc = score_option(opt, total_proposals=1)
    assert sc["compilable"] == 1.0


def test_compilable_none_is_half():
    opt = make_option("A", "x", [0], {0: 0.5}, compilable=None)
    sc = score_option(opt, total_proposals=1)
    assert sc["compilable"] == 0.5


def test_compilable_false_is_zero():
    opt = make_option("A", "x", [0], {0: 0.5}, compilable=False)
    sc = score_option(opt, total_proposals=1)
    assert sc["compilable"] == 0.0


def test_score_full_breakdown():
    """验证完整 4 维分数按权重计算"""
    opt = make_option("A", "x", [0, 1, 2, 3, 4], {0: 0.6, 1: 0.8}, compilable=True, empirical=2)
    sc = score_option(opt, total_proposals=5)
    # viability = mean(0.6, 0.8) = 0.7 (只算有 viability 的 supporting)
    assert sc["viability"] == 0.7
    # support = 5/5 = 1.0
    assert sc["support"] == 1.0
    # empirical = 2/3 = 0.6667
    assert sc["empirical"] == pytest.approx(2 / 3, abs=1e-4)
    # compilable = 1.0
    assert sc["compilable"] == 1.0
    # total = 0.7*0.4 + 1.0*0.25 + 0.6667*0.2 + 1.0*0.15
    expected = 0.7 * 0.4 + 1.0 * 0.25 + (2 / 3) * 0.2 + 1.0 * 0.15
    assert sc["total"] == pytest.approx(expected, abs=1e-4)


# ============ arbitrate 测试 ============

def test_arbitrate_single_option():
    """单 option 仲裁: 该 option 胜出, runner_up=None"""
    opt = make_option("A", "only", [0], {0: 0.5})
    v = arbitrate([opt], total_proposals=1)
    assert isinstance(v, ConflictVerdict)
    assert v.winner_option_id == "A"
    assert v.runner_up_id is None
    assert v.confidence == 0.0


def test_arbitrate_two_options_picks_higher():
    """2 options 选总分最高"""
    a = make_option("A", "low", [0], {0: 0.3})
    b = make_option("B", "high", [0, 1, 2], {0: 0.9, 1: 0.9, 2: 0.9}, empirical=3, compilable=True)
    v = arbitrate([a, b], total_proposals=3)
    assert v.winner_option_id == "B"
    assert v.runner_up_id == "A"
    # 验证 rationale 非空
    assert v.rationale != ""
    assert "B" in v.rationale


def test_arbitrate_confidence_formula():
    """confidence = (winner - 2nd) / winner"""
    a = make_option("A", "x", [0], {0: 0.5})
    b = make_option("B", "y", [0], {0: 0.2})
    v = arbitrate([a, b], total_proposals=1)
    assert v.winner_option_id == "A"
    sc_a = v.voting_breakdown["scores"]["A"]["total"]
    sc_b = v.voting_breakdown["scores"]["B"]["total"]
    expected = (sc_a - sc_b) / sc_a
    assert v.confidence == pytest.approx(expected, abs=1e-4)


def test_arbitrate_tie_breaks_on_support():
    """全 viability=0 → tie, 选 support 多的"""
    a = make_option("A", "few", [0, 1], {0: 0.0, 1: 0.0})
    b = make_option("B", "many", [0, 1, 2, 3, 4], {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0})
    v = arbitrate([a, b], total_proposals=5)
    assert v.winner_option_id == "B"  # support 多的胜
    assert v.rationale != ""


# ============ fuse_decision 测试 ============

def test_fuse_decision_picks_highest_coherence_x_viability():
    """fuse 选 logical_coherence × viability 最高"""
    # A: 高 viability, 低 coherence (supporting 之间关键词不重叠)
    a = make_option(
        "A", "high_v", [0, 1], {0: 0.9, 1: 0.9},
    )
    # B: 中 viability, 高 coherence
    b = make_option(
        "B", "mid_v", [0, 1], {0: 0.5, 1: 0.5},
    )
    v = fuse_decision([a, b], query="test query", total_proposals=2)
    # 无 proposals 上下文时, logical_coherence 都为 1.0
    # A viability 0.9 > B viability 0.5 → A 胜
    assert v.winner_option_id == "A"
    assert "A" in v.rationale
    assert "复合分" in v.rationale or "composite" in v.rationale.lower() or "熔铸" in v.rationale


def test_fuse_decision_rationale_describes_debate():
    """fuse rationale 描述辩论过程"""
    a = make_option("A", "alpha", [0], {0: 0.7})
    b = make_option("B", "beta", [0], {0: 0.4})
    v = fuse_decision([a, b], query="my question")
    # rationale 应包含: 胜出选项 + 复合分 + 内部一致性
    assert v.winner_option_id == "A"
    assert "复合分" in v.rationale
    assert "logical_coherence" in v.rationale or "一致性" in v.rationale
    # strongest_proposal 应在 breakdown 中
    assert v.voting_breakdown["mode"] == "fuse"
    assert "strongest_proposal" in v.voting_breakdown
    assert v.voting_breakdown["strongest_proposal"]["A"] == 0


# ============ 序列化测试 ============

def test_json_serialization():
    """option / verdict JSON 序列化"""
    opt = make_option("A", "x", [0, 1], {0: 0.5}, compilable=True, empirical=2)
    d = option_to_dict(opt)
    assert d["option_id"] == "A"
    assert d["supporting_proposals"] == [0, 1]
    assert d["viability_scores"] == {0: 0.5}
    assert d["command_compilable"] is True
    assert d["empirical_evidence_count"] == 2

    a = make_option("A", "alpha", [0], {0: 0.8})
    b = make_option("B", "beta", [0], {0: 0.3})
    v = arbitrate([a, b], total_proposals=1)
    vd = verdict_to_dict(v)
    assert vd["winner_option_id"] == "A"
    assert vd["runner_up_id"] == "B"
    assert isinstance(vd["confidence"], float)
    assert 0.0 <= vd["confidence"] <= 1.0
    assert isinstance(vd["rationale"], str)
    assert isinstance(vd["voting_breakdown"], dict)
    assert "scores" in vd["voting_breakdown"]
    assert "A" in vd["voting_breakdown"]["scores"]


# ============ 边界 & 错误处理 ============

def test_arbitrate_empty_raises():
    """空 options 抛 ValueError"""
    with pytest.raises(ValueError):
        arbitrate([], total_proposals=1)


def test_fuse_decision_empty_raises():
    with pytest.raises(ValueError):
        fuse_decision([], query="x")
