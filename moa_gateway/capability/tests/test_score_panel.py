"""score_panel 单元测试 (12+ 测试)

真实 assert, 严禁 mock。
"""
from __future__ import annotations
import pytest
import sys
from pathlib import Path

# 允许直接 import
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.score_panel import (
    DimensionScore,
    PanelScore,
    score_panel,
    score_technical_quality,
    score_completeness,
    score_applicability,
    score_specificity,
    score_insight,
    multi_eval_average,
    DEFAULT_WEIGHTS,
)


# ============ TQ 测试 ============

def test_score_technical_quality_long():
    """1000 字答案 → TQ 高分"""
    answer = "This is a comprehensive technical answer. " * 50  # ~1500 chars
    result = score_technical_quality(answer)
    assert isinstance(result, DimensionScore)
    assert result.name == "TQ"
    assert result.full_name == "Technical Quality"
    # 1000+ 字 + 高密度英文应有较高分
    assert result.score >= 60, f"expected >=60, got {result.score}"
    assert result.score <= 100


def test_score_technical_quality_short():
    """10 字答案 → TQ 低分"""
    answer = "Hi there."  # 9 chars
    result = score_technical_quality(answer)
    assert result.score < 50, f"expected <50, got {result.score}"
    assert result.score >= 0


def test_score_technical_quality_code_block():
    """含 ``` 块 → TQ +20"""
    plain = "Some text. " * 30  # ~360 chars, no code
    with_code = "Some text. " * 30 + "\n```python\nprint('hello')\n```\nMore text. " * 5

    s1 = score_technical_quality(plain)
    s2 = score_technical_quality(with_code)
    # code block 加成至少 +15 (考虑 base 相同 + 数字可能影响)
    assert s2.score - s1.score >= 15, (
        f"expected code block bonus >=15, got diff={s2.score - s1.score}"
    )


def test_score_technical_quality_with_numbers():
    """含数字 → TQ +n"""
    plain = "Lorem ipsum dolor sit amet. " * 20
    with_nums = "Lorem 123 ipsum 456 dolor 7890 sit amet. " * 20

    s1 = score_technical_quality(plain)
    s2 = score_technical_quality(with_nums)
    # 数字加分至少 +10
    assert s2.score > s1.score, (
        f"expected with_nums > plain, got {s2.score} vs {s1.score}"
    )
    # 而且 with_nums 应拿到数字 bonus
    assert any("number" in n.lower() for n in s2.notes), (
        f"expected number bonus note, got {s2.notes}"
    )


# ============ CO 测试 ============

def test_score_completeness_full():
    """答案覆盖所有 query 子问题 → CO 高"""
    query = "What is Python? How to install it? Why use virtualenv?"
    answer = (
        "Python is a high-level programming language known for readability. "
        "To install it, you can download from python.org and run the installer. "
        "Virtualenv helps isolate dependencies between projects, preventing version conflicts."
    )
    result = score_completeness(query, answer)
    assert result.name == "CO"
    # 三个子问题都覆盖
    assert result.score >= 70, f"expected >=70, got {result.score} (notes: {result.notes})"


def test_score_completeness_partial():
    """答案漏了子问题 → CO 中"""
    query = "What is Python? How to install it? Why use virtualenv?"
    answer = "Python is a programming language."  # 只回答了第一个
    result = score_completeness(query, answer)
    # 覆盖率 1/3 ≈ 33, 加 length_bonus 约 35
    assert 20 <= result.score < 75, f"expected 20-75, got {result.score}"


# ============ AP 测试 ============

def test_score_applicability_steps():
    """'Step 1, Step 2, Step 3' → AP 高"""
    answer = (
        "Here is how to set up the project:\n"
        "Step 1: Install dependencies using npm install.\n"
        "Step 2: Configure the environment variables.\n"
        "Step 3: Run the development server with npm run dev.\n"
        "You can also use yarn instead of npm if you prefer."
    )
    result = score_applicability(answer)
    assert result.name == "AP"
    # 步骤 + actionable 动词都有,应较高
    assert result.score >= 70, f"expected >=70, got {result.score} (notes: {result.notes})"
    assert any("step" in n.lower() for n in result.notes)


def test_score_applicability_no_steps():
    """没 step → AP 低"""
    answer = "It is a tool for developers that does many things."
    result = score_applicability(answer)
    # 没步骤,没 actionable 动词 → 较低
    assert result.score < 60, f"expected <60, got {result.score}"
    assert any("no step" in n.lower() for n in result.notes)


# ============ SE 测试 ============

def test_score_specificity_urls():
    """含 3 个 URL → SE 高"""
    answer = (
        "See these resources:\n"
        "1. https://docs.python.org/3/tutorial/\n"
        "2. https://realpython.com/start-here/\n"
        "3. https://github.com/python/cpython\n"
        "Year 2024 version 3.12 release notes available."
    )
    result = score_specificity(answer)
    assert result.name == "SE"
    # 3 URLs (30) + year (5) + version (3) + base 45 = ~83
    assert result.score >= 70, f"expected >=70, got {result.score} (notes: {result.notes})"
    assert any("url" in n.lower() for n in result.notes)


def test_score_specificity_no_evidence():
    """没数字/引用 → SE 低"""
    answer = "This is a vague answer with no specific evidence at all just words."
    result = score_specificity(answer)
    # base 45 (因为 > 200 chars? 这里 60 chars 算 < 200 → base 35)
    assert result.score < 60, f"expected <60, got {result.score}"


# ============ IN 测试 ============

def test_score_insight_contrarian():
    """含 'however' + 'actually' → IN 高"""
    answer = (
        "Many people think Python is slow, however it is actually fast enough for most tasks. "
        "Specifically, the PyPy interpreter can dramatically improve performance. "
        "Notably, the GIL has been a contentious issue, but Python 3.12 introduced improvements."
    )
    result = score_insight(query="Is Python fast?", answer=answer)
    assert result.name == "IN"
    # 多个转折词 + insight 标记 + 长度 → 较高
    assert result.score >= 70, f"expected >=70, got {result.score} (notes: {result.notes})"
    assert any("transition" in n.lower() for n in result.notes)


# ============ PanelScore 综合测试 ============

def test_score_panel_verdict_excellent():
    """全高分 → verdict=excellent"""
    query = "What is Python? How to install it?"
    answer = (
        "Python is a versatile programming language.\n\n"
        "Step 1: Download from https://python.org/downloads\n"
        "Step 2: Run the installer for version 3.12.1 (released in 2023).\n"
        "Step 3: Verify with `python --version` in your terminal.\n\n"
        "However, you should actually use pyenv for version management. "
        "Specifically, virtualenv helps isolate dependencies. "
        "Notably, Python 3.12 brought a 5% performance improvement."
    )
    result = score_panel(query, answer)
    assert isinstance(result, PanelScore)
    # overall 应 >= 70(可能 excellent 或 good)
    assert result.overall >= 70, f"expected overall >=70, got {result.overall}"
    assert result.verdict in ("excellent", "good")
    # 5 维都应有评分
    assert 0 <= result.tq.score <= 100
    assert 0 <= result.co.score <= 100
    assert 0 <= result.ap.score <= 100
    assert 0 <= result.se.score <= 100
    assert 0 <= result.in_.score <= 100
    # feedback 应有内容
    assert len(result.feedback) > 0
    # to_dict 可序列化
    d = result.to_dict()
    assert "tq" in d
    assert "in" in d  # 键名 in_ 转为 in
    assert d["in"]["name"] == "IN"


def test_score_panel_verdict_poor():
    """全低分 → verdict=poor"""
    query = "Explain quantum computing, machine learning, and cryptography in detail."
    answer = "It is hard."  # 完全没覆盖
    result = score_panel(query, answer)
    assert result.verdict in ("poor", "fair"), f"expected poor/fair, got {result.verdict}"
    assert result.overall < 60, f"expected <60, got {result.overall}"
    # 至少有一个 feedback 项
    assert len(result.feedback) > 0


# ============ Multi-eval averaging 测试 ============

def test_multi_eval_average():
    """3 个 PanelScore → avg 维度"""
    s1 = score_panel("What is X?", "X is a thing. Step 1: learn it.")
    s2 = score_panel("What is X?", "X is a concept. Step 1: read docs. Step 2: practice.")
    s3 = score_panel("What is X?", "X is complicated. Step 1: research. Step 2: implement. https://example.com")

    avg = multi_eval_average([s1, s2, s3])
    assert isinstance(avg, PanelScore)

    # 各维度均值应在 min-max 之间
    tq_vals = [s1.tq.score, s2.tq.score, s3.tq.score]
    expected_tq = sum(tq_vals) / 3
    assert abs(avg.tq.score - expected_tq) < 0.5, (
        f"expected TQ avg {expected_tq}, got {avg.tq.score}"
    )

    co_vals = [s1.co.score, s2.co.score, s3.co.score]
    expected_co = sum(co_vals) / 3
    assert abs(avg.co.score - expected_co) < 0.5

    # overall 应重新计算
    assert 0 <= avg.overall <= 100
    # verdict 应已设置
    assert avg.verdict in ("excellent", "good", "fair", "poor")
    # feedback 包含 averaging 提示
    assert any("averag" in fb.lower() for fb in avg.feedback)


def test_multi_eval_average_handles_different_weights():
    """自定义 weights"""
    # 用不同 rubric 各评一次
    custom_rubric = {"TQ": 0.5, "CO": 0.3, "AP": 0.1, "SE": 0.05, "IN": 0.05}
    answer = (
        "Python is a language. Step 1: install. Step 2: code. "
        "See https://python.org for version 3.12.1 released in 2023."
    )
    query = "What is Python?"

    s_default = score_panel(query, answer)
    s_custom = score_panel(query, answer, rubric=custom_rubric)

    avg = multi_eval_average([s_default, s_custom])

    # 权重应被合并
    assert "TQ" in avg.weights
    assert abs(sum(avg.weights.values()) - 1.0) < 0.01, (
        f"weights not normalized: {avg.weights}"
    )
    # TQ 权重 = (0.25 + 0.5) / 2 / total
    assert avg.weights["TQ"] > 0.3  # 因为 TQ 偏高
    # verdict 仍合法
    assert avg.verdict in ("excellent", "good", "fair", "poor")


# ============ 边界测试 (额外,确保 robust) ============

def test_score_panel_empty_answer():
    """空答案 → 不崩溃 + 低分"""
    result = score_panel("What is X?", "")
    assert result.overall < 30
    assert result.verdict in ("poor", "fair")


def test_score_panel_no_query():
    """无 query → 不崩溃"""
    result = score_panel("", "Some answer text with content.")
    assert isinstance(result, PanelScore)
    assert 0 <= result.overall <= 100


def test_multi_eval_average_single():
    """单个 PanelScore → 原样返回"""
    s = score_panel("Q?", "A.")
    avg = multi_eval_average([s])
    assert avg.tq.score == s.tq.score
    assert avg.co.score == s.co.score


def test_multi_eval_average_empty_raises():
    """空列表 → 抛 ValueError"""
    with pytest.raises(ValueError):
        multi_eval_average([])


def test_score_panel_to_dict_serializable():
    """to_dict 输出可被 JSON 序列化"""
    import json
    result = score_panel("Q?", "A with code ```python\nprint(1)\n``` and https://example.com")
    d = result.to_dict()
    # 应能 JSON 序列化
    json_str = json.dumps(d)
    assert "TQ" in json_str
    assert "overall" in json_str
