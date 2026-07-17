"""goal_eval 单元测试 (16+ 测试)

真实 assert, 严禁 mock。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# 允许直接 import
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.goal_eval import (
    CeilingReport,
    Goal,
    GoalResult,
    GoalTier,
    compute_completeness_score,
    evaluate_goal,
    evaluate_goals,
    evaluate_tier1,
    evaluate_tier2,
    generate_ceiling_report,
)

# ============ GoalTier 测试 ============

def test_goal_tier_has_two_values():
    """GoalTier 有 2 个值"""
    values = {t.value for t in GoalTier}
    assert values == {"mechanical", "model_declared"}, f"unexpected tier values: {values}"
    assert len(GoalTier) == 2


def test_goal_tier_str_enum():
    """GoalTier 是 str 枚举,可比较"""
    assert GoalTier.MECHANICAL == "mechanical"
    assert GoalTier.MODEL_DECLARED == "model_declared"
    assert GoalTier("mechanical") == GoalTier.MECHANICAL


# ============ Tier 1 机械求值测试 ============

def test_evaluate_tier1_keyword_contains_match():
    """Tier 1 contains 关键字匹配"""
    goal = Goal(
        id="g1",
        description="should mention Python",
        tier=GoalTier.MECHANICAL,
        criteria="contains: Python",
    )
    result = evaluate_tier1(goal, "Python is a great language")
    assert isinstance(result, GoalResult)
    assert result.achieved is True
    assert result.score == 1.0
    assert result.tier == GoalTier.MECHANICAL
    assert len(result.evidence) > 0


def test_evaluate_tier1_no_match_not_achieved():
    """Tier 1 不匹配 → achieved=False"""
    goal = Goal(
        id="g2",
        description="should mention Python",
        tier=GoalTier.MECHANICAL,
        criteria="contains: Python",
    )
    result = evaluate_tier1(goal, "Ruby is a great language")
    assert result.achieved is False
    assert result.score == 0.0


def test_evaluate_tier1_equals_rule():
    """Tier 1 equals 规则"""
    goal = Goal(
        id="g3",
        description="exact match",
        tier=GoalTier.MECHANICAL,
        criteria="equals: hello world",
    )
    r1 = evaluate_tier1(goal, "hello world")
    r2 = evaluate_tier1(goal, "Hello World")  # 区分大小写
    assert r1.achieved is True
    assert r2.achieved is False


def test_evaluate_tier1_len_rule():
    """Tier 1 len 规则"""
    goal = Goal(
        id="g4",
        description="output length >= 5",
        tier=GoalTier.MECHANICAL,
        criteria="len >= 5",
    )
    r1 = evaluate_tier1(goal, "abc")
    r2 = evaluate_tier1(goal, "abcdef")
    assert r1.achieved is False
    assert r2.achieved is True


def test_evaluate_tier1_regex_rule():
    """Tier 1 regex 规则"""
    goal = Goal(
        id="g5",
        description="starts with digit",
        tier=GoalTier.MECHANICAL,
        criteria=r"regex: ^\d+",
    )
    r1 = evaluate_tier1(goal, "123 abc")
    r2 = evaluate_tier1(goal, "abc 123")
    assert r1.achieved is True
    assert r2.achieved is False


# ============ Tier 2 模型声明求值测试 ============

def test_evaluate_tier2_keyword_overlap_partial():
    """Tier 2 部分关键词重叠"""
    goal = Goal(
        id="g6",
        description="discusses Python and Django",
        tier=GoalTier.MODEL_DECLARED,
        criteria="python django web framework",
    )
    result = evaluate_tier2(goal, "Python is great for web development")
    assert isinstance(result, GoalResult)
    assert result.tier == GoalTier.MODEL_DECLARED
    # 关键词:python, django, web, framework
    # output 含 python, web → 2/4 = 0.5
    assert 0.0 < result.score <= 1.0
    assert 0.4 <= result.score <= 0.6, f"expected ~0.5, got {result.score}"


def test_evaluate_tier2_perfect_match_score_1():
    """Tier 2 完美匹配 → score=1"""
    goal = Goal(
        id="g7",
        description="all keywords present",
        tier=GoalTier.MODEL_DECLARED,
        criteria="python django web framework",
    )
    result = evaluate_tier2(goal, "python django web framework and more")
    assert result.score == 1.0
    assert result.achieved is True


def test_evaluate_tier2_no_overlap_score_0():
    """Tier 2 无 overlap → score=0"""
    goal = Goal(
        id="g8",
        description="no overlap",
        tier=GoalTier.MODEL_DECLARED,
        criteria="python django web framework",
    )
    result = evaluate_tier2(goal, "completely unrelated content here")
    assert result.score == 0.0
    assert result.achieved is False


def test_evaluate_tier2_with_model_call():
    """Tier 2 提供 model_call → 调用并解析"""
    goal = Goal(
        id="g9",
        description="model-declared",
        tier=GoalTier.MODEL_DECLARED,
        criteria="anything",
    )

    def fake_model(criteria, output):
        return {"achieved": True, "score": 0.95, "evidence": ["model says yes"]}

    result = evaluate_tier2(goal, "output", model_call=fake_model)
    assert result.achieved is True
    assert result.score == 0.95
    # 证据应包含 model_call 标记
    assert any("model" in e.lower() for e in result.evidence)


def test_evaluate_tier2_model_call_error_falls_back():
    """Tier 2 model_call 抛异常 → fallback 启发式"""
    goal = Goal(
        id="g10",
        description="fallback test",
        tier=GoalTier.MODEL_DECLARED,
        criteria="python",
    )

    def broken_model(criteria, output):
        raise RuntimeError("model down")

    result = evaluate_tier2(goal, "I love python", model_call=broken_model)
    # fallback: score > 0
    assert result.score > 0
    # 证据应包含错误信息
    assert any("error" in e.lower() or "fallback" in e.lower() for e in result.evidence)


# ============ 主入口路由测试 ============

def test_evaluate_goal_routes_tier1():
    """evaluate_goal 路由 tier1"""
    goal = Goal(
        id="g11",
        description="routing test",
        tier=GoalTier.MECHANICAL,
        criteria="contains: hello",
    )
    result = evaluate_goal(goal, "say hello to world")
    assert result.achieved is True
    assert result.tier == GoalTier.MECHANICAL


def test_evaluate_goal_routes_tier2():
    """evaluate_goal 路由 tier2"""
    goal = Goal(
        id="g12",
        description="routing test 2",
        tier=GoalTier.MODEL_DECLARED,
        criteria="alpha beta gamma",
    )
    result = evaluate_goal(goal, "alpha beta gamma delta")
    assert result.score == 1.0
    assert result.tier == GoalTier.MODEL_DECLARED


# ============ Ceiling Report 测试 ============

def test_generate_ceiling_report_five_sections():
    """5 section ceiling report 生成"""
    report = generate_ceiling_report(
        claim="We can reduce latency by 30%",
        evidence=["benchmark A", "benchmark B", "benchmark C"],
        baseline="Current p99 latency is 200ms",
        gaps=["tested only on small datasets", "no load testing"],
        residual_risk="Single region deployment may behave differently",
    )
    assert isinstance(report, CeilingReport)
    assert report.claim == "We can reduce latency by 30%"
    assert len(report.evidence) == 3
    assert report.baseline == "Current p99 latency is 200ms"
    assert len(report.gaps) == 2
    assert "Single region" in report.residual_risk


def test_generate_ceiling_report_missing_claim_raises():
    """缺 claim → 抛 ValueError"""
    with pytest.raises(ValueError):
        generate_ceiling_report(
            claim="",
            evidence=["x"],
            baseline="y",
            gaps=["z"],
            residual_risk="r",
        )


def test_generate_ceiling_report_missing_baseline_raises():
    """缺 baseline → 抛 ValueError"""
    with pytest.raises(ValueError):
        generate_ceiling_report(
            claim="c",
            evidence=["x"],
            baseline="",
            gaps=["z"],
            residual_risk="r",
        )


def test_generate_ceiling_report_missing_risk_raises():
    """缺 residual_risk → 抛 ValueError"""
    with pytest.raises(ValueError):
        generate_ceiling_report(
            claim="c",
            evidence=["x"],
            baseline="y",
            gaps=["z"],
            residual_risk="",
        )


def test_completeness_score_full():
    """完整 5 section → completeness=1.0"""
    report = generate_ceiling_report(
        claim="claim",
        evidence=["e1", "e2"],
        baseline="base",
        gaps=["g1"],
        residual_risk="risk",
    )
    assert compute_completeness_score(report) == 1.0


def test_completeness_score_empty_evidence_lowers():
    """缺 evidence → 扣分"""
    report = generate_ceiling_report(
        claim="claim",
        evidence=[],          # 空 evidence
        baseline="base",
        gaps=["g1"],
        residual_risk="risk",
    )
    score = compute_completeness_score(report)
    assert 0.0 < score < 1.0, f"expected <1, got {score}"
    assert score <= 0.8


def test_completeness_score_empty_gaps_lowers():
    """缺 gaps → 扣分"""
    report = generate_ceiling_report(
        claim="claim",
        evidence=["e1"],
        baseline="base",
        gaps=[],              # 空 gaps
        residual_risk="risk",
    )
    score = compute_completeness_score(report)
    assert 0.0 < score < 1.0


# ============ 批量求值测试 ============

def test_evaluate_goals_empty():
    """0 goals → []"""
    result = evaluate_goals([], "any output")
    assert result == []


def test_evaluate_goals_batch_mixed_tiers():
    """批量求值:混合 tier"""
    goals = [
        Goal(id="a", description="", tier=GoalTier.MECHANICAL, criteria="contains: hello"),
        Goal(id="b", description="", tier=GoalTier.MODEL_DECLARED, criteria="python"),
        Goal(id="c", description="", tier=GoalTier.MECHANICAL, criteria="suffix: world"),
    ]
    output = "hello python world"
    results = evaluate_goals(goals, output)
    assert len(results) == 3
    assert results[0].achieved is True   # hello in output
    assert results[1].achieved is True   # python overlap
    assert results[2].achieved is True   # suffix world
    # tier 区分
    assert results[0].tier == GoalTier.MECHANICAL
    assert results[1].tier == GoalTier.MODEL_DECLARED
    assert results[2].tier == GoalTier.MECHANICAL


# ============ JSON 序列化测试 ============

def test_goal_result_json_serializable():
    """GoalResult 可 JSON 序列化"""
    goal = Goal(
        id="g_js",
        description="",
        tier=GoalTier.MECHANICAL,
        criteria="contains: x",
    )
    result = evaluate_tier1(goal, "xyz")
    d = result.to_dict()
    json_str = json.dumps(d)
    assert "g_js" in json_str
    assert "mechanical" in json_str


def test_ceiling_report_json_serializable():
    """CeilingReport 可 JSON 序列化"""
    report = generate_ceiling_report(
        claim="c", evidence=["e"], baseline="b", gaps=["g"], residual_risk="r"
    )
    json_str = json.dumps(report.to_dict())
    loaded = json.loads(json_str)
    assert loaded["claim"] == "c"
    assert loaded["evidence"] == ["e"]
    assert loaded["baseline"] == "b"
    assert loaded["gaps"] == ["g"]
    assert loaded["residual_risk"] == "r"


# ============ Tier 区分测试 ============

def test_tier1_uses_evaluator_fn():
    """Tier 1 自定义 evaluator_fn 优先"""
    def custom_ev(output, criteria):
        return {"achieved": True, "score": 0.77, "evidence": ["custom"]}

    goal = Goal(
        id="g_custom1",
        description="",
        tier=GoalTier.MECHANICAL,
        criteria="contains: nope",
        evaluator_fn=custom_ev,
    )
    # 即便 criteria 不匹配,custom 应胜出
    result = evaluate_tier1(goal, "anything")
    assert result.achieved is True
    assert result.score == 0.77
    assert "custom" in result.evidence


def test_tier2_uses_evaluator_fn():
    """Tier 2 自定义 evaluator_fn 优先"""
    def custom_ev(output, criteria):
        return (False, 0.2, ["tier2 custom"])

    goal = Goal(
        id="g_custom2",
        description="",
        tier=GoalTier.MODEL_DECLARED,
        criteria="anything",
        evaluator_fn=custom_ev,
    )
    result = evaluate_tier2(goal, "output")
    assert result.achieved is False
    assert result.score == 0.2


def test_evaluate_tier1_wrong_tier_raises():
    """Tier 1 对错误 tier 抛 ValueError"""
    goal = Goal(
        id="g_err",
        description="",
        tier=GoalTier.MODEL_DECLARED,
        criteria="contains: x",
    )
    with pytest.raises(ValueError):
        evaluate_tier1(goal, "x")


def test_evaluate_tier2_wrong_tier_raises():
    """Tier 2 对错误 tier 抛 ValueError"""
    goal = Goal(
        id="g_err2",
        description="",
        tier=GoalTier.MECHANICAL,
        criteria="python",
    )
    with pytest.raises(ValueError):
        evaluate_tier2(goal, "python")
