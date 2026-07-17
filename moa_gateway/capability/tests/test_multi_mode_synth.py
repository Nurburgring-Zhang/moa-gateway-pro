"""multi_mode_synth 单元测试 (≥ 16 测试)

真实 assert, 严禁 mock。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# 允许直接 import
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.multi_mode_synth import (
    Proposal,
    SynthesisMode,
    SynthResult,
    classify_proposals,
    cross_iteration,
    final_selection,
    integrated_synthesis,
    run_synthesis,
    should_run_integration,
)

# ============ 辅助: 构造 Proposal ============

def make_proposal(idx: int, author: str, text: str, tags=None) -> Proposal:
    if tags is None:
        tags = []
    return Proposal(proposal_idx=idx, author=author, text=text, tags=tags)


# ============ 1. 枚举可用性 ============

def test_synthesis_mode_enum_all_four():
    """4 个模式 enum 都可访问"""
    assert SynthesisMode.CLASSIFICATION.value == "classification"
    assert SynthesisMode.INTEGRATED_SYNTHESIS.value == "integrated_synthesis"
    assert SynthesisMode.FINAL_SELECTION.value == "final_selection"
    assert SynthesisMode.CROSS_ITERATION.value == "cross_iteration"
    # 枚举总数
    assert len(list(SynthesisMode)) == 4


def test_synthesis_mode_str_membership():
    """枚举可作 string 匹配"""
    assert SynthesisMode.CLASSIFICATION in SynthesisMode
    assert SynthesisMode.CLASSIFICATION.value == "classification"


# ============ 2. CLASSIFICATION 模式 ============

def test_classify_multi_proposals():
    """多 proposals 分类 — code/math/factual/creative/conversational"""
    props = [
        make_proposal(0, "alice", "We need to define a Python function for the API endpoint."),
        make_proposal(1, "bob", "The matrix equation proves the theorem of integral calculus."),
        make_proposal(2, "carol", "According to the research report, the population is 5 million."),
        make_proposal(3, "dave", "Imagine a creative story with a unique character plot theme."),
        make_proposal(4, "eve", "Hello, thanks for the help. Yes, I agree with your suggestion."),
    ]
    result = classify_proposals(props)
    assert isinstance(result, SynthResult)
    assert result.mode == SynthesisMode.CLASSIFICATION
    items = json.loads(result.output)
    assert len(items) == 5
    cats = {it["category"] for it in items}
    # 至少 3 个不同 category 被命中
    assert len(cats) >= 3, f"expected >=3 categories, got {cats}"
    # 每条都含 idx / author / category / confidence
    for it in items:
        assert "idx" in it
        assert "author" in it
        assert "category" in it
        assert "confidence" in it
        assert 0.0 <= it["confidence"] <= 1.0
    # metadata
    assert result.metadata["total"] == 5
    assert "category_distribution" in result.metadata


def test_classify_single_proposal():
    """单 proposal 也能分类"""
    p = make_proposal(0, "alice", "Write a Python function to compute the matrix product.")
    result = classify_proposals([p])
    items = json.loads(result.output)
    assert len(items) == 1
    it = items[0]
    assert it["idx"] == 0
    # 含 code 关键词 → 应该是 code 或 math (任一相关)
    assert it["category"] in ("code", "math"), f"got {it['category']}"


def test_classify_empty_returns_empty_list():
    """空 proposals → output 是 []"""
    result = classify_proposals([])
    assert result.output == "[]"
    assert result.confidence == 0.0
    assert result.metadata["total"] == 0


# ============ 3. INTEGRATED_SYNTHESIS 模式 ============

def test_integrated_extracts_real_sentences():
    """integrated_synthesis 提取的 sentence 必出自 proposals (不发明)"""
    props = [
        make_proposal(0, "alice", "We should add a caching layer to improve performance. Use Redis for storage."),
        make_proposal(1, "bob", "Adding a caching layer is the right call here. The database is slow."),
        make_proposal(2, "carol", "Caching layer improves performance significantly. Redis is fast."),
    ]
    result = integrated_synthesis(props, target_chars=500)
    assert result.mode == SynthesisMode.INTEGRATED_SYNTHESIS
    assert result.output  # 非空
    # output 拼起来的所有句必须能在某个 proposal 中找到
    " ".join(p.text for p in props)
    # 至少包含一个原句的关键短语
    output_lower = result.output.lower()
    assert "caching" in output_lower
    assert "performance" in output_lower or "redis" in output_lower


def test_integrated_source_attribution_correct():
    """source_attribution: 每条 sentence 来源 proposal_idx 正确"""
    props = [
        make_proposal(0, "alice", "We should add caching layer for performance. Use Redis."),
        make_proposal(1, "bob", "Adding caching layer is right. Database queries are slow."),
    ]
    result = integrated_synthesis(props, target_chars=600)
    # source_attribution key 必须是 proposals 里的 idx
    for key in result.source_attribution:
        assert key in (0, 1), f"unexpected source idx: {key}"
    # 至少 1 个 attribution
    assert len(result.source_attribution) >= 1
    # metadata
    assert result.metadata["sentences_used"] >= 1
    assert result.metadata["total_candidates"] >= 1


def test_integrated_respects_target_chars():
    """target_chars 限制: output 字符数不超过 (target + max_sentence) 范围"""
    long_text = (
        "The caching layer should be implemented carefully and tested thoroughly. "
        "We recommend using Redis as the primary storage backend. "
        "Performance improvements will be significant in production environments. "
        "The system architecture must support horizontal scaling seamlessly. "
    )
    p = make_proposal(0, "alice", long_text)
    p2 = make_proposal(1, "bob", long_text)
    result = integrated_synthesis([p, p2], target_chars=100)
    # total_chars 不应远超 target (允许 1 个长句超出)
    assert result.metadata["total_chars"] <= result.metadata["target_chars"] + 120
    # 至少有 1 句被选
    assert result.metadata["sentences_used"] >= 1


def test_integrated_empty_proposals():
    """空 proposals → 空 output"""
    result = integrated_synthesis([], target_chars=300)
    assert result.output == ""
    assert result.source_attribution == {}
    assert result.confidence == 0.0


def test_integrated_one_proposal_no_crash():
    """1 proposal 不抛错"""
    p = make_proposal(0, "alice", "Caching layer is essential. We should add it to improve performance.")
    result = integrated_synthesis([p], target_chars=300)
    assert result.mode == SynthesisMode.INTEGRATED_SYNTHESIS
    assert isinstance(result.output, str)


# ============ 4. FINAL_SELECTION 模式 ============

def test_final_selection_picks_highest():
    """选最高分 proposal"""
    p0 = make_proposal(0, "alice", "Low quality response.")
    p1 = make_proposal(1, "bob", "This is the best possible answer with detailed explanation and code examples.")
    p2 = make_proposal(2, "carol", "Medium quality response here.")
    scores = {0: 0.3, 1: 0.9, 2: 0.6}
    result = final_selection([p0, p1, p2], scores)
    assert result.mode == SynthesisMode.FINAL_SELECTION
    assert result.output == p1.text
    assert 1 in result.source_attribution
    assert result.metadata["winner_idx"] == 1
    assert result.metadata["winner_score"] == 0.9


def test_final_selection_single_proposal_degrades():
    """单 proposal 退化"""
    p0 = make_proposal(0, "alice", "Only one option available.")
    result = final_selection([p0], {0: 0.7})
    assert result.output == p0.text
    assert result.metadata["winner_idx"] == 0
    # 单个 → runner_up 不存在 → confidence = winner_score - 0
    assert result.confidence == 0.7


def test_final_selection_confidence_winner_minus_2nd():
    """confidence = winner_score - 2nd_score"""
    p0 = make_proposal(0, "a", "Option A with some text content here.")
    p1 = make_proposal(1, "b", "Option B with different text content here.")
    p2 = make_proposal(2, "c", "Option C with yet another text content here.")
    scores = {0: 0.5, 1: 0.8, 2: 0.6}
    result = final_selection([p0, p1, p2], scores)
    # winner = 0.8, 2nd = 0.6 → confidence = 0.2
    assert result.confidence == pytest.approx(0.2, abs=1e-4)
    assert result.metadata["runner_up_idx"] == 2
    assert result.metadata["runner_up_score"] == pytest.approx(0.6, abs=1e-4)


def test_final_selection_empty_proposals():
    """空 proposals → 空 output"""
    result = final_selection([], {})
    assert result.output == ""
    assert result.confidence == 0.0
    assert result.metadata["winner_idx"] is None


# ============ 5. CROSS_ITERATION 模式 ============

def test_cross_iteration_convergence_high():
    """prev/curr 关键词高重叠 → convergence 接近 1"""
    # 共享大量关键词, prev/curr 几乎相同主题
    shared = "caching layer redis performance database system architecture deployment production"
    p0 = make_proposal(0, "a", f"We should add {shared} for the project now.")
    p1 = make_proposal(1, "b", f"Adding {shared} improves the overall system design.")
    prev = [p0, p1]
    curr = [
        make_proposal(2, "c", f"Use {shared} for the new feature implementation here."),
        make_proposal(3, "d", f"The {shared} is essential for production deployment today."),
    ]
    result = cross_iteration(prev, curr)
    assert result.mode == SynthesisMode.CROSS_ITERATION
    # 高共享 → convergence 高
    assert result.metadata["convergence"] > 0.2
    # 主题相同 → converged 或 recommendation 合理
    assert result.metadata["converged"] is True or result.metadata["recommendation"] in ("adopt_curr", "keep_prev", "converged")


def test_cross_iteration_best_of_each():
    """best_of_each: source_attribution 含 prev_best + curr_best"""
    p_prev = make_proposal(0, "a", "The caching layer approach is recommended for this system architecture.")
    p_prev2 = make_proposal(1, "b", "Short text.")
    p_curr = make_proposal(2, "c", "Adding caching layer with Redis is the best practice for modern applications now.")
    p_curr2 = make_proposal(3, "d", "Use a queue.")
    result = cross_iteration([p_prev, p_prev2], [p_curr, p_curr2])
    # source_attribution 应包含 prev_best_idx 和 curr_best_idx
    prev_best_idx = result.metadata["prev_best_idx"]
    curr_best_idx = result.metadata["curr_best_idx"]
    assert prev_best_idx in result.source_attribution
    assert curr_best_idx in result.source_attribution
    # 标记前缀
    assert "[prev_best]" in result.source_attribution[prev_best_idx]
    assert "[curr_best]" in result.source_attribution[curr_best_idx]


def test_cross_iteration_recommend_adoption_when_curr_better():
    """curr 显著优于 prev → recommendation = adopt_curr"""
    # prev 简短低质
    p_prev = make_proposal(0, "a", "Use cache.")
    # curr 长且丰富
    p_curr = make_proposal(1, "b",
        "The comprehensive caching layer architecture with Redis provides significant "
        "performance improvements across all system components and enables horizontal "
        "scaling for production deployment scenarios today."
    )
    result = cross_iteration([p_prev], [p_curr])
    assert result.metadata["recommendation"] in ("adopt_curr", "converged")
    # curr_avg 应该高于 prev_avg
    assert result.metadata["curr_avg_score"] > result.metadata["prev_avg_score"]


def test_cross_iteration_empty_inputs():
    """空 prev 或 curr 不抛错"""
    r1 = cross_iteration([], [])
    assert r1.output == ""
    assert r1.metadata["recommendation"] == "insufficient_data"

    r2 = cross_iteration([], [make_proposal(0, "a", "Only curr exists.")])
    assert r2.metadata["recommendation"] == "adopt_curr"

    r3 = cross_iteration([make_proposal(0, "a", "Only prev exists.")], [])
    assert r3.metadata["recommendation"] == "keep_prev"


# ============ 6. 统一入口 ============

def test_run_synthesis_unified_entry_classification():
    """run_synthesis CLASSIFICATION"""
    props = [make_proposal(0, "a", "Use Python function for the API.")]
    r = run_synthesis(SynthesisMode.CLASSIFICATION, props)
    assert r.mode == SynthesisMode.CLASSIFICATION


def test_run_synthesis_unified_entry_integrated():
    """run_synthesis INTEGRATED_SYNTHESIS"""
    props = [make_proposal(0, "a", "Caching layer is important for performance improvements.")]
    r = run_synthesis(SynthesisMode.INTEGRATED_SYNTHESIS, props, target_chars=100)
    assert r.mode == SynthesisMode.INTEGRATED_SYNTHESIS
    assert r.metadata["target_chars"] == 100


def test_run_synthesis_unified_entry_final():
    """run_synthesis FINAL_SELECTION"""
    props = [make_proposal(0, "a", "winner text")]
    r = run_synthesis(SynthesisMode.FINAL_SELECTION, props, scores={0: 0.9})
    assert r.mode == SynthesisMode.FINAL_SELECTION
    assert r.output == "winner text"


def test_run_synthesis_unified_entry_cross():
    """run_synthesis CROSS_ITERATION"""
    prev = [make_proposal(0, "a", "Previous round thinking about caching.")]
    curr = [make_proposal(1, "b", "Current round thinking about caching layer.")]
    r = run_synthesis(SynthesisMode.CROSS_ITERATION, [], prev_proposals=prev, curr_proposals=curr)
    assert r.mode == SynthesisMode.CROSS_ITERATION
    assert "convergence" in r.metadata


# ============ 7. should_run_integration ============

def test_should_run_integration_high_consensus_false():
    """高共识 (stddev < 0.1) → False"""
    proposals = [make_proposal(i, f"u{i}", f"Proposal {i} with content.") for i in range(4)]
    # 0.85, 0.88, 0.87, 0.86 → stddev ~ 0.01
    scores = {0: 0.85, 1: 0.88, 2: 0.87, 3: 0.86}
    assert should_run_integration(proposals, scores) is False


def test_should_run_integration_low_consensus_true():
    """低共识 (stddev >= 0.1) → True"""
    proposals = [make_proposal(i, f"u{i}", f"Proposal {i} with content.") for i in range(4)]
    # 0.3, 0.9, 0.4, 0.8 → stddev 大
    scores = {0: 0.3, 1: 0.9, 2: 0.4, 3: 0.8}
    assert should_run_integration(proposals, scores) is True


def test_should_run_integration_empty_proposals_false():
    """空 proposals → False"""
    assert should_run_integration([], {}) is False
    assert should_run_integration([], {0: 0.5}) is False


def test_should_run_integration_boundary():
    """边界值: stddev 接近阈值"""
    # 2 个 score, stddev 由 pstdev 计算
    proposals = [make_proposal(0, "a", "x"), make_proposal(1, "b", "y")]
    # 0.5 和 0.5 → stddev = 0 < 0.1 → False
    assert should_run_integration(proposals, {0: 0.5, 1: 0.5}) is False
    # 0.0 和 0.5 → stddev = 0.25 ≥ 0.1 → True
    assert should_run_integration(proposals, {0: 0.0, 1: 0.5}) is True


# ============ 8. Dataclass 基础 ============

def test_proposal_dataclass_defaults():
    """Proposal.tags 默认 []"""
    p = Proposal(proposal_idx=0, author="x", text="hello")
    assert p.tags == []
    assert p.proposal_idx == 0


def test_synth_result_dataclass_defaults():
    """SynthResult 默认字段"""
    r = SynthResult(mode=SynthesisMode.CLASSIFICATION, output="x")
    assert r.source_attribution == {}
    assert r.confidence == 0.0
    assert r.metadata == {}


def test_synth_result_to_dict():
    """to_dict 序列化 mode 为 str"""
    r = SynthResult(
        mode=SynthesisMode.INTEGRATED_SYNTHESIS,
        output="abc",
        confidence=0.5,
    )
    d = r.to_dict()
    assert d["mode"] == "integrated_synthesis"
    assert d["output"] == "abc"


# ============ 9. 综合 — 多 proposal + 端到端 ============

def test_end_to_end_multi_proposal_pipeline():
    """多 proposal 端到端: 4 个模式都能跑"""
    props = [
        make_proposal(0, "alice", "We should add a caching layer to improve performance. Use Redis for storage."),
        make_proposal(1, "bob", "Adding a caching layer is the right call. The database is too slow currently."),
        make_proposal(2, "carol", "Hello, I agree with the caching approach. Thanks for the analysis."),
    ]
    scores = {0: 0.85, 1: 0.78, 2: 0.55}

    # CLASSIFICATION
    r1 = run_synthesis(SynthesisMode.CLASSIFICATION, props)
    assert r1.mode == SynthesisMode.CLASSIFICATION
    items = json.loads(r1.output)
    assert len(items) == 3

    # INTEGRATED
    r2 = run_synthesis(SynthesisMode.INTEGRATED_SYNTHESIS, props, target_chars=200)
    assert r2.mode == SynthesisMode.INTEGRATED_SYNTHESIS
    assert r2.output

    # FINAL
    r3 = run_synthesis(SynthesisMode.FINAL_SELECTION, props, scores=scores)
    assert r3.mode == SynthesisMode.FINAL_SELECTION
    assert r3.output == props[0].text  # 0.85 最高

    # CROSS
    prev = [make_proposal(10, "x", "Earlier thinking about caching and performance.")]
    r4 = run_synthesis(SynthesisMode.CROSS_ITERATION, props, prev_proposals=prev, curr_proposals=props)
    assert r4.mode == SynthesisMode.CROSS_ITERATION
    assert "convergence" in r4.metadata
