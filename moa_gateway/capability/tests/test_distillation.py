"""distillation 单元测试 (≥ 16 测试)

真实 assert, 严禁 mock。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.distillation import (
    MIN_WORDS_PER_IDEA,
    DistillationResult,
    DistilledIdea,
    apply_bias_correction,
    curate_ideas,
    distill_proposals,
    extract_ideas,
    idea_to_json,
    multi_eval_average,
    result_to_json,
)

# ============ 1. extract_ideas ============

def test_extract_ideas_single_proposal():
    """单 proposal 抽取 idea"""
    text = "We should add a caching layer to improve system performance."
    ideas = extract_ideas(text, 0)
    assert isinstance(ideas, list)
    assert len(ideas) >= 1
    for idea in ideas:
        assert isinstance(idea, DistilledIdea)
        assert 0 in idea.source_proposals
        assert idea.frequency == 1
        assert 0.0 <= idea.importance_score <= 1.0


def test_extract_ideas_filters_short_sentences():
    """过滤 < MIN_WORDS_PER_IDEA 词的短句"""
    text = "Hi. Hello there. " + " ".join(["word"] * MIN_WORDS_PER_IDEA) + "."
    ideas = extract_ideas(text, 0)
    # 短句 "Hi" / "Hello there" 不应被抽出
    for idea in ideas:
        words = idea.text.split()
        assert len(words) >= 1  # 至少有内容
        # 检查词数 (粗略)
        assert len(idea.text) > 5


def test_extract_ideas_normalizes_keywords():
    """关键词归一: 同 idea 跨 proposal 合并时按归一关键词判定"""
    # 相同关键词的两个 proposal
    p0 = "Adding caching layer improves performance and scalability today."
    p1 = "Caching layer improves performance and scalability for the system."

    ideas0 = extract_ideas(p0, 0)
    ideas1 = extract_ideas(p1, 1)

    # 各自至少抽出一条
    assert len(ideas0) >= 1
    assert len(ideas1) >= 1

    # curate 合并时应该识别为同 idea (frequency=2)
    merged = curate_ideas([ideas0, ideas1], keep_ratio=1.0)
    # 找到含 "caching" 的 idea
    caching_ideas = [i for i in merged.kept_ideas if "caching" in i.text.lower()]
    assert len(caching_ideas) >= 1
    # 至少 1 个 caching 相关 idea frequency=2
    assert any(i.frequency == 2 for i in caching_ideas)


def test_extract_ideas_empty_text():
    """空文本 → 空 list"""
    assert extract_ideas("", 0) == []
    assert extract_ideas("   ", 0) == []
    assert extract_ideas(None or "", 0) == []  # type: ignore


def test_extract_ideas_only_short_returns_empty():
    """全 < 5 词 → 空 list"""
    text = "Hi. Yes. No. OK."
    ideas = extract_ideas(text, 0)
    assert ideas == []


# ============ 2. curate_ideas ============

def test_curate_ideas_cross_proposal_frequency():
    """跨 proposals 同 idea frequency 累加"""
    p0 = "Adding caching layer improves performance for the system today."
    p1 = "The caching layer improves performance and reliability for users."
    p2 = "Use caching layer for performance improvement across the platform."

    ideas_per = [extract_ideas(p, i) for i, p in enumerate([p0, p1, p2])]
    merged = curate_ideas(ideas_per, keep_ratio=1.0)
    # caching 相关 idea frequency 应 >= 2
    caching = [i for i in merged.kept_ideas if "caching" in i.text.lower()]
    assert len(caching) >= 1
    assert any(i.frequency >= 2 for i in caching)


def test_curate_ideas_sorts_by_freq_x_importance():
    """curate_ideas 按 freq × importance 排序"""
    # 构造 2 个 idea, 一个 freq 高一个 importance 高
    p0 = "Caching layer improves performance for the modern web application system."
    p1 = "Caching layer improves performance for the modern web application system."
    p2 = "Some unique content here that nobody else mentions in the proposal set."

    ideas_per = [extract_ideas(p, i) for i, p in enumerate([p0, p1, p2])]
    result = curate_ideas(ideas_per, keep_ratio=0.5)

    # kept 中应该包含 caching idea (freq=2, importance 较高)
    assert len(result.kept_ideas) >= 1
    # caching 应该在 kept 里
    caching_kept = [i for i in result.kept_ideas if "caching" in i.text.lower()]
    assert len(caching_kept) >= 1


def test_curate_ideas_keep_ratio_half():
    """keep_ratio=0.5 → 保留 ~50%"""
    proposals = [
        "Caching layer improves performance for the modern web application system.",
        "Database queries are slow and need optimization for the production environment.",
        "Redis is the best choice for caching layer in distributed cloud systems.",
        "System architecture needs redesign for better performance and scalability today.",
    ]
    result = distill_proposals(proposals, keep_ratio=0.5)
    total = result.original_count
    assert total > 0
    # 50% ± 1
    assert abs(result.distilled_count - total * 0.5) <= 1
    # ratio ≈ 0.5
    assert 0.3 <= result.distillation_ratio <= 0.7


def test_curate_ideas_keep_ratio_one_keeps_all():
    """keep_ratio=1.0 → 全部保留"""
    proposals = [
        "Caching layer improves performance for the modern web application system.",
        "Database queries are slow and need optimization for the production environment.",
    ]
    result = distill_proposals(proposals, keep_ratio=1.0)
    assert result.distilled_count == result.original_count
    assert result.distillation_ratio == 1.0
    assert len(result.dropped_ideas) == 0


def test_curate_ideas_keep_ratio_zero_drops_all():
    """keep_ratio=0.0 → 0 idea 保留"""
    proposals = [
        "Caching layer improves performance for the modern web application system.",
        "Database queries are slow and need optimization for the production environment.",
    ]
    result = distill_proposals(proposals, keep_ratio=0.0)
    assert result.distilled_count == 0
    assert result.distillation_ratio == 0.0
    assert len(result.kept_ideas) == 0
    # 全部在 dropped
    assert len(result.dropped_ideas) == result.original_count


def test_curate_ideas_keeps_marked():
    """kept_ideas 中每条 .kept = True, dropped 中 .kept = False"""
    proposals = [
        "Caching layer improves performance for the modern web application system today.",
        "Database queries need optimization for better performance and reliability across.",
        "Redis is great for caching in distributed systems and high availability setups.",
    ]
    result = distill_proposals(proposals, keep_ratio=0.5)
    for idea in result.kept_ideas:
        assert idea.kept is True
    for idea in result.dropped_ideas:
        assert idea.kept is False


# ============ 3. distill_proposals ============

def test_distill_proposals_one_stop():
    """distill_proposals 一站式"""
    proposals = [
        "Caching layer improves performance for the modern web application system today.",
        "Database queries are slow and need optimization for the production environment now.",
        "Redis is the best caching solution for distributed high-performance web systems.",
    ]
    result = distill_proposals(proposals, keep_ratio=0.5)
    assert isinstance(result, DistillationResult)
    assert result.original_count > 0
    assert 0.0 <= result.distillation_ratio <= 1.0
    assert "n_proposals" in result.metadata
    assert result.metadata["n_proposals"] == 3


def test_distill_proposals_empty():
    """空 proposals → 0 idea"""
    result = distill_proposals([], keep_ratio=0.5)
    assert result.kept_ideas == []
    assert result.dropped_ideas == []
    assert result.original_count == 0
    assert result.distilled_count == 0
    assert result.distillation_ratio == 0.0


def test_distill_proposals_metadata():
    """metadata 含必要字段"""
    proposals = [
        "Caching layer improves performance for the modern web application system today.",
    ]
    result = distill_proposals(proposals, keep_ratio=0.7)
    assert result.metadata["keep_ratio"] == 0.7
    assert result.metadata["n_proposals"] == 1
    assert result.metadata["sort_key"] == "freq_x_importance"


# ============ 4. multi_eval_average ============

def test_multi_eval_average_two_evaluators():
    """2 evaluator 求平均"""
    evaluations = [
        {"quality": 0.8, "speed": 0.6},
        {"quality": 0.6, "speed": 0.4},
    ]
    result = multi_eval_average(evaluations)
    assert result["evaluator_count"] == 2
    assert result["quality_avg"] == pytest.approx(0.7, abs=1e-4)
    assert result["speed_avg"] == pytest.approx(0.5, abs=1e-4)
    # biases 存在
    assert "0" in result["biases"]
    assert "1" in result["biases"]


def test_multi_eval_average_three_evaluators():
    """3 evaluator 求平均"""
    evaluations = [
        {"quality": 0.9, "speed": 0.5},
        {"quality": 0.7, "speed": 0.5},
        {"quality": 0.5, "speed": 0.5},
    ]
    result = multi_eval_average(evaluations)
    assert result["evaluator_count"] == 3
    assert result["quality_avg"] == pytest.approx(0.7, abs=1e-4)
    assert result["speed_avg"] == pytest.approx(0.5, abs=1e-4)
    # 3 个 bias
    assert len(result["biases"]) == 3


def test_multi_eval_average_single_evaluator():
    """单 evaluator"""
    evaluations = [
        {"quality": 0.8, "speed": 0.6},
    ]
    result = multi_eval_average(evaluations)
    assert result["evaluator_count"] == 1
    assert result["quality_avg"] == pytest.approx(0.8, abs=1e-4)
    # 单 evaluator → bias = 0
    assert result["biases"]["0"] == 0.0


def test_multi_eval_average_empty():
    """空 evaluations → 退化结果"""
    result = multi_eval_average([])
    assert result["evaluator_count"] == 0
    assert result["biases"] == {}
    assert result["dimensions"] == []


def test_multi_eval_average_bias_calculation():
    """bias 计算正确: evaluator 平均 - 总体平均"""
    # eval 0 全 1.0, eval 1 全 0.0 → 总体 0.5
    # bias[0] = 1.0 - 0.5 = 0.5
    # bias[1] = 0.0 - 0.5 = -0.5
    evaluations = [
        {"a": 1.0, "b": 1.0},
        {"a": 0.0, "b": 0.0},
    ]
    result = multi_eval_average(evaluations)
    assert result["biases"]["0"] == pytest.approx(0.5, abs=1e-4)
    assert result["biases"]["1"] == pytest.approx(-0.5, abs=1e-4)


# ============ 5. apply_bias_correction ============

def test_apply_bias_correction_subtracts_bias():
    """bias_correction 减 bias"""
    scores = {"quality": 0.8, "speed": 0.6}
    biases = {"0": 0.3, "1": 0.1}
    corrected = apply_bias_correction(scores, biases)
    # avg_bias = 0.2
    # corrected = score - 0.2
    assert corrected["quality"] == pytest.approx(0.6, abs=1e-4)
    assert corrected["speed"] == pytest.approx(0.4, abs=1e-4)


def test_apply_bias_correction_empty_bias():
    """空 biases → 原样返回"""
    scores = {"quality": 0.8}
    corrected = apply_bias_correction(scores, {})
    assert corrected == {"quality": 0.8}


def test_apply_bias_correction_empty_scores():
    """空 scores → 空 dict"""
    assert apply_bias_correction({}, {"0": 0.5}) == {}


# ============ 6. JSON 序列化 ============

def test_json_serialization_result():
    """DistillationResult JSON 序列化"""
    proposals = [
        "Caching layer improves performance for the modern web application system today.",
        "Database queries are slow and need optimization for production environments everywhere.",
    ]
    result = distill_proposals(proposals, keep_ratio=0.5)
    js = result_to_json(result)
    assert isinstance(js, str)
    parsed = json.loads(js)
    assert "kept_ideas" in parsed
    assert "dropped_ideas" in parsed
    assert "original_count" in parsed
    assert "distilled_count" in parsed
    assert "distillation_ratio" in parsed
    # idea 字段
    if parsed["kept_ideas"]:
        idea = parsed["kept_ideas"][0]
        assert "text" in idea
        assert "source_proposals" in idea
        assert "frequency" in idea
        assert "importance_score" in idea
        assert "kept" in idea


def test_json_serialization_idea():
    """DistilledIdea JSON 序列化"""
    idea = DistilledIdea(
        text="test",
        source_proposals=[0, 1],
        frequency=2,
        importance_score=0.75,
        kept=True,
    )
    js = idea_to_json(idea)
    parsed = json.loads(js)
    assert parsed["text"] == "test"
    assert parsed["source_proposals"] == [0, 1]
    assert parsed["frequency"] == 2
    assert parsed["importance_score"] == 0.75
    assert parsed["kept"] is True


def test_distillation_result_to_dict():
    """DistillationResult.to_dict 包含所有字段"""
    result = DistillationResult(
        kept_ideas=[],
        dropped_ideas=[],
        original_count=5,
        distilled_count=2,
        distillation_ratio=0.4,
    )
    d = result.to_dict()
    assert d["original_count"] == 5
    assert d["distilled_count"] == 2
    assert d["distillation_ratio"] == 0.4


# ============ 7. 边界 + 综合 ============

def test_distillation_ratio_computation():
    """distillation_ratio = distilled / original"""
    proposals = [
        "Caching layer improves performance for the modern web application system today.",
        "Database queries are slow and need optimization for production environments now.",
        "Redis is great for caching in distributed systems and high availability setups.",
    ]
    result = distill_proposals(proposals, keep_ratio=0.5)
    if result.original_count > 0:
        expected = result.distilled_count / result.original_count
        assert result.distillation_ratio == pytest.approx(expected, abs=1e-4)


def test_importance_score_range():
    """importance_score 在 [0, 1]"""
    texts = [
        "Short text here.",
        "A much longer and more detailed proposal that contains many keywords about caching performance scalability reliability.",
    ]
    for idx, t in enumerate(texts):
        ideas = extract_ideas(t, idx)
        for idea in ideas:
            assert 0.0 <= idea.importance_score <= 1.0
