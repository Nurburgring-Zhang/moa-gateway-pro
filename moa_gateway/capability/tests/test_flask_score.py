"""flask_score 单元测试 (16+ 测试)

真实 assert, 严禁 mock。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 允许直接 import
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.flask_score import (
    FlaskDimension,
    FlaskScore,
    TaskNode,
    TaskTree,
    analyze_dimensions,
    build_task_tree,
    flask_to_json,
    score_flask,
    summary_report,
    tree_cohesion_coupling,
)

# ============ 1. 12 FlaskDimension 枚举 ============

def test_flask_dimension_has_twelve():
    """枚举应包含 12 个维度"""
    assert len(FlaskDimension) == 12


def test_flask_dimension_names():
    """12 维名称一一对应"""
    names = {d.value for d in FlaskDimension}
    expected = {
        "ROBUSTNESS", "CORRECTNESS", "EFFICIENCY", "FACTUALITY",
        "RELEVANCE", "COHERENCE", "CREATIVITY", "HELPFULNESS",
        "HARM_PREVENTION", "HARMLESSNESS", "CONSISTENCY", "COMPLETENESS",
    }
    assert names == expected


def test_flask_dimension_iteration_order():
    """枚举声明顺序稳定"""
    order = [d.value for d in FlaskDimension]
    assert order[0] == "ROBUSTNESS"
    assert order[-1] == "COMPLETENESS"


# ============ 2. score_flask 基本 ============

def test_score_flask_basic_returns_flask_score():
    """score_flask 应返回 FlaskScore 实例"""
    result = score_flask("Hello world.", query="hi")
    assert isinstance(result, FlaskScore)
    assert isinstance(result.total_score, float)
    assert 0.0 <= result.total_score <= 5.0


def test_score_flask_all_dimensions_scored():
    """12 维都被打分"""
    result = score_flask("Some answer text here.")
    assert len(result.dimension_scores) == 12
    for dim, score in result.dimension_scores.items():
        assert isinstance(dim, FlaskDimension)
        assert isinstance(score, int)


# ============ 3. ROBUSTNESS 启发式 ============

def test_robustness_with_keyword_scores_5():
    """含 'handle' / 'try' / 'fallback' → 5"""
    text = "We need to handle the error with a try/except fallback mechanism."
    result = score_flask(text)
    assert result.dimension_scores[FlaskDimension.ROBUSTNESS] == 5


def test_robustness_without_keyword_scores_3():
    """不含 robustness 关键词 → 3"""
    text = "Just a simple statement with nothing technical."
    result = score_flask(text)
    assert result.dimension_scores[FlaskDimension.ROBUSTNESS] == 3


# ============ 4. CORRECTNESS 启发式 ============

def test_correctness_with_numbers_and_citation():
    """含数字 + 引用 → 5"""
    text = "According to study [1], 75% of users prefer option A in 2024."
    result = score_flask(text)
    assert result.dimension_scores[FlaskDimension.CORRECTNESS] == 5


def test_correctness_short_no_evidence_low():
    """极短且无证据 → 低分 (<=3)"""
    text = "ok"
    result = score_flask(text)
    assert result.dimension_scores[FlaskDimension.CORRECTNESS] <= 3


# ============ 5. EFFICIENCY 长度 ============

def test_efficiency_short_is_5():
    """长度 < 200 → 5"""
    text = "Brief answer."
    result = score_flask(text)
    assert result.dimension_scores[FlaskDimension.EFFICIENCY] == 5


def test_efficiency_long_is_4():
    """长度 >= 200 → 4"""
    text = "x" * 250
    result = score_flask(text)
    assert result.dimension_scores[FlaskDimension.EFFICIENCY] == 4


# ============ 6. FACTUALITY URL ============

def test_factuality_with_url_scores_5():
    """含 URL → 5"""
    text = "See https://example.com/docs for the full reference and citation [1]."
    result = score_flask(text)
    assert result.dimension_scores[FlaskDimension.FACTUALITY] == 5


def test_factuality_no_url_scores_3():
    """无 URL/citation → 3"""
    text = "Just a plain text answer without any sources or references."
    result = score_flask(text)
    assert result.dimension_scores[FlaskDimension.FACTUALITY] == 3


# ============ 7. RELEVANCE 关键词比例 ============

def test_relevance_high_overlap_scores_5():
    """query 关键词在 answer 中 > 50% → 5"""
    query = "python flask routing"
    answer = "Python is great. Flask handles routing via decorators. The routing system is simple."
    result = score_flask(answer, query=query)
    assert result.dimension_scores[FlaskDimension.RELEVANCE] == 5


def test_relevance_no_overlap_low():
    """query 关键词在 answer 中几乎无 → <=3"""
    query = "kubernetes ingress controller"
    answer = "Cats sleep all day and are very fluffy."
    result = score_flask(answer, query=query)
    assert result.dimension_scores[FlaskDimension.RELEVANCE] <= 3


# ============ 8. HARM_PREVENTION 检测危险词 ============

def test_harm_prevention_detects_dangerous():
    """含 'rm -rf' → 1"""
    text = "You should run rm -rf /tmp to clean up."
    result = score_flask(text)
    assert result.dimension_scores[FlaskDimension.HARM_PREVENTION] == 1
    assert FlaskDimension.HARM_PREVENTION in result.weak_dimensions


def test_harm_prevention_clean_scores_5():
    """无危险词 → 5"""
    text = "Just review the file before deletion."
    result = score_flask(text)
    assert result.dimension_scores[FlaskDimension.HARM_PREVENTION] == 5


# ============ 9. HARMLESSNESS 检测 hate ============

def test_harmlessness_detects_hate():
    """含 hate/violence → 1"""
    text = "We should attack and kill them all with violence."
    result = score_flask(text)
    assert result.dimension_scores[FlaskDimension.HARMLESSNESS] == 1
    assert FlaskDimension.HARMLESSNESS in result.weak_dimensions


def test_harmlessness_clean_scores_5():
    """无 hate/violence → 5"""
    text = "Please be kind and respectful to everyone in the discussion."
    result = score_flask(text)
    assert result.dimension_scores[FlaskDimension.HARMLESSNESS] == 5


# ============ 10. total_score 是 mean ============

def test_total_score_is_mean_of_dimensions():
    """total = 12 维均值"""
    result = score_flask("ok")
    expected = sum(result.dimension_scores.values()) / 12
    assert abs(result.total_score - round(expected, 3)) < 0.01


# ============ 11. 1-5 范围 ============

def test_all_scores_in_1_to_5_range():
    """每个维度评分都在 [1, 5]"""
    cases = [
        "",
        "x",
        "rm -rf /",
        "https://example.com " * 50,
        "summary: " * 100,
    ]
    for text in cases:
        result = score_flask(text)
        for dim, sc in result.dimension_scores.items():
            assert 1 <= sc <= 5, f"{dim.value}={sc} for text={text!r}"


# ============ 12. analyze_dimensions weak/strong ============

def test_analyze_dimensions_weak_strong_split():
    """构造 FlaskScore,验证 weak/strong 分类"""
    scores = {
        FlaskDimension.ROBUSTNESS: 2,    # weak
        FlaskDimension.CORRECTNESS: 4,   # strong
        FlaskDimension.EFFICIENCY: 3,    # neutral
        FlaskDimension.FACTUALITY: 5,    # strong
    }
    fs = FlaskScore(
        total_score=0.0,
        dimension_scores=scores,
    )
    weak, strong = analyze_dimensions(fs)
    assert FlaskDimension.ROBUSTNESS in weak
    assert FlaskDimension.CORRECTNESS in strong
    assert FlaskDimension.FACTUALITY in strong
    assert FlaskDimension.EFFICIENCY not in weak
    assert FlaskDimension.EFFICIENCY not in strong


# ============ 13. weak_dimensions 排序 (enum 声明序) ============

def test_weak_dimensions_sorted_by_enum_order():
    """weak 列表按 enum 声明顺序"""
    result = score_flask("rm -rf / and kill them with violence and hate.")
    # 至少有 HARM_PREVENTION 和 HARMLESSNESS
    assert FlaskDimension.HARM_PREVENTION in result.weak_dimensions
    assert FlaskDimension.HARMLESSNESS in result.weak_dimensions
    # 检查是 enum 顺序
    enum_order = {d: i for i, d in enumerate(FlaskDimension)}
    for a, b in zip(result.weak_dimensions, result.weak_dimensions[1:], strict=False):
        assert enum_order[a] < enum_order[b]


# ============ 14. strong_dimensions 排序 ============

def test_strong_dimensions_sorted_by_enum_order():
    """strong 列表按 enum 声明顺序"""
    text = (
        "https://example.com summary conclusion step example novel innovative "
        "handle try fallback rm -rf kill violence hate"
    )
    result = score_flask(text)
    enum_order = {d: i for i, d in enumerate(FlaskDimension)}
    for a, b in zip(result.strong_dimensions, result.strong_dimensions[1:], strict=False):
        assert enum_order[a] < enum_order[b]


# ============ 15. summary_report 含 total ============

def test_summary_report_contains_total():
    """summary_report 应包含 total_score"""
    result = score_flask("hello world summary")
    report = summary_report(result)
    assert "FLASK" in report
    assert f"{result.total_score:.2f}" in report
    assert isinstance(report, str)
    assert len(report) > 10


# ============ 16. JSON 序列化 ============

def test_flask_to_json_serializable():
    """FlaskScore 可被 JSON 序列化"""
    result = score_flask("summary: " * 30)
    raw = flask_to_json(result)
    assert isinstance(raw, str)
    parsed = json.loads(raw)
    assert "total_score" in parsed
    assert "dimension_scores" in parsed
    assert len(parsed["dimension_scores"]) == 12
    # 枚举键 → 字符串
    for v in parsed["dimension_scores"]:
        assert isinstance(v, str)


# ============ 17. 边界: 空 answer ============

def test_empty_answer_does_not_crash():
    """空 answer 不会抛异常"""
    result = score_flask("", query="anything")
    assert isinstance(result, FlaskScore)
    assert 0.0 <= result.total_score <= 5.0
    # 至少 12 个维度都被打分
    assert len(result.dimension_scores) == 12
    for sc in result.dimension_scores.values():
        assert 1 <= sc <= 5


# ============ 18. M-34 Task 分解树 ============

def test_build_task_tree_basic():
    """build_task_tree 构造简单树"""
    children = [
        TaskNode(name="setup environment", keywords=["setup", "environment"]),
        TaskNode(name="run tests", keywords=["run", "tests"]),
    ]
    tree = build_task_tree("deploy service", children)
    assert isinstance(tree, TaskTree)
    assert tree.root.name == "deploy service"
    assert len(tree.root.children) == 2
    assert 0.0 <= tree.cohesion <= 1.0
    assert 0.0 <= tree.coupling <= 1.0


def test_tree_cohesion_coupling_returns_tuple():
    """tree_cohesion_coupling 返回 (cohesion, coupling)"""
    children = [TaskNode(name="alpha beta"), TaskNode(name="gamma delta")]
    tree = build_task_tree("root", children)
    coh, coup = tree_cohesion_coupling(tree)
    assert isinstance(coh, float)
    assert isinstance(coup, float)
    assert coh == tree.cohesion
    assert coup == tree.coupling
