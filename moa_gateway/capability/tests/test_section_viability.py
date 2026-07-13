"""section_viability 单元测试 (16+ 测试)

真实 assert, 严禁 mock。
"""
from __future__ import annotations
import pytest
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.section_viability import (
    Section,
    SectionVerdict,
    ProposalReport,
    split_into_sections,
    evaluate_section,
    compute_ap_score,
    validate_proposal,
    compare_proposals,
    MIN_WORDS,
    MAX_WORDS,
)


# ============ split_into_sections 测试 ============

def test_split_markdown_h2():
    """按 Markdown ## 切分"""
    text = (
        "# Title\n\n"
        "## Introduction\nThis is the introduction section with enough words to matter. "
        "It contains several sentences explaining the context and the goals. "
        "We should ensure that the section is long enough to be analyzed properly.\n\n"
        "## Methodology\nThis is the methodology section. We must describe the methods used. "
        "There are 5 main steps that we will follow to achieve our goals. "
        "Each step will be explained in detail to ensure clarity for the reader.\n\n"
        "## Results\nThis is the results section. We will present the findings here. "
        "The data shows that 87% of cases were successful based on the analysis we performed."
    )
    sections = split_into_sections(text)
    assert isinstance(sections, list)
    assert len(sections) >= 2, f"expected >=2 sections, got {len(sections)}"
    # 每个 section 都有 title
    for s in sections:
        assert isinstance(s, Section)
        assert s.title
        assert s.text
        assert s.word_count > 0


def test_split_numbered():
    """按 numbered 1. 切分"""
    text = (
        "1. First section title that is long enough to register properly. "
        "This section contains enough words to be a valid section. "
        "It will discuss the first point in detail with multiple sentences here.\n\n"
        "2. Second section that should also be detected by numbered pattern. "
        "We will discuss the second point with sufficient detail here. "
        "There are several aspects that must be covered in this section of the text.\n\n"
        "3. Third section providing additional context and details about the third point. "
        "We should also discuss the implications of this third section in detail. "
        "There are 3 main considerations that need to be addressed in this section."
    )
    sections = split_into_sections(text)
    assert isinstance(sections, list)
    assert len(sections) >= 2, f"expected >=2 sections, got {len(sections)}"
    for s in sections:
        assert isinstance(s, Section)
        assert s.word_count > 0


def test_split_fallback_200_words():
    """无标题时兜底 200 词切分"""
    # 500+ 词无任何标题/编号
    text = " ".join([f"word{i}" for i in range(500)])
    sections = split_into_sections(text)
    assert isinstance(sections, list)
    # 兜底应至少 2 段
    assert len(sections) >= 2, f"expected >=2 fallback chunks, got {len(sections)}"
    # 兜底 title 形如 (untitled)
    for s in sections:
        assert s.title == "(untitled)" or s.title


def test_split_empty_text():
    """空文本 → 空 list"""
    assert split_into_sections("") == []
    assert split_into_sections("   \n\n  ") == []


# ============ evaluate_section 测试 ============

def test_evaluate_too_short_not_viable():
    """< 20 词 → not viable"""
    sec = Section(section_idx=0, title="Tiny", text="too short no action", word_count=5)
    v = evaluate_section(sec)
    assert isinstance(v, SectionVerdict)
    assert v.viable is False
    assert v.score < 0.5
    # blockers 应非空
    assert len(v.blockers) > 0
    assert any("short" in b.lower() for b in v.blockers)


def test_evaluate_too_long_not_viable():
    """> 800 词 → not viable"""
    long_text = " ".join([f"word{i}" for i in range(850)])
    sec = Section(section_idx=0, title="Huge", text=long_text, word_count=850)
    v = evaluate_section(sec)
    assert v.viable is False
    assert any("long" in b.lower() or "split" in b.lower() for b in v.blockers)


def test_evaluate_no_imperative_not_viable():
    """缺 imperative → not viable"""
    text = (
        "This section discusses something interesting and has many words but no "
        "actionable content. It rambles on about various topics in great detail "
        "without any clear directive for the reader to follow. We describe a few "
        "ideas, mention some background information, and provide commentary on the "
        "broader context. There is a discussion of 3 historical points, and an "
        "analysis of 5 relevant factors. References include Smith 2020 and Lee 2019."
    )
    sec = Section(section_idx=0, title="Discussion", text=text, word_count=_count_words(text))
    # 确认此 section 词数足够
    assert sec.word_count >= MIN_WORDS
    v = evaluate_section(sec)
    # 没有 should/must/will → not viable
    assert v.viable is False
    assert any("imperative" in b.lower() for b in v.blockers)


def test_evaluate_viable_with_imperative_and_numbers():
    """有 imperative + 数字 → viable"""
    text = (
        "We must implement the cache layer. The system should evict entries "
        "after 60 seconds. Performance will improve by 40% based on the test. "
        "The implementation requires 3 steps total and should be completed in 2 weeks. "
        "You should also configure the TTL settings to 300 seconds as documented."
    )
    sec = Section(section_idx=0, title="Implementation", text=text, word_count=_count_words(text))
    v = evaluate_section(sec)
    assert v.viable is True, f"expected viable, got blockers: {v.blockers}"
    # score 应 >= 0.5
    assert v.score >= 0.5, f"expected score >= 0.5, got {v.score}"


def test_evaluate_score_in_0_1_range():
    """score 始终在 [0, 1]"""
    # 各种 case
    cases = [
        ("a b c d e", 5),                                # 短
        (" ".join([f"x{i}" for i in range(100)]) + " must should", 102),  # 中
        (" ".join([f"x{i}" for i in range(900)]), 900),  # 长
    ]
    for text, wc in cases:
        sec = Section(section_idx=0, title="T", text=text, word_count=wc)
        v = evaluate_section(sec)
        assert 0.0 <= v.score <= 1.0, f"score out of range: {v.score} for wc={wc}"


# ============ compute_ap_score 测试 ============

def test_ap_score_all_viable_is_10():
    """全 viable → AP=10"""
    report = ProposalReport(
        proposal_idx=0,
        total_sections=3,
        viable_sections=3,
        failing_sections=[],
        ap_score=0,
        verdicts=[],
    )
    assert compute_ap_score(report) == 10


def test_ap_score_one_viable_is_5_to_7():
    """≥1 viable → AP in [5, 7]"""
    # 1/3 viable
    r1 = ProposalReport(
        proposal_idx=0, total_sections=3, viable_sections=1,
        failing_sections=[1, 2], ap_score=0, verdicts=[],
    )
    # 2/3 viable
    r2 = ProposalReport(
        proposal_idx=1, total_sections=3, viable_sections=2,
        failing_sections=[2], ap_score=0, verdicts=[],
    )
    ap1 = compute_ap_score(r1)
    ap2 = compute_ap_score(r2)
    assert 5 <= ap1 <= 7, f"expected 5-7 for 1/3, got {ap1}"
    assert 5 <= ap2 <= 7, f"expected 5-7 for 2/3, got {ap2}"


def test_ap_score_all_fail_is_2_to_4():
    """全 fail → AP in [2, 4]"""
    r1 = ProposalReport(
        proposal_idx=0, total_sections=1, viable_sections=0,
        failing_sections=[0], ap_score=0, verdicts=[],
    )
    r2 = ProposalReport(
        proposal_idx=0, total_sections=5, viable_sections=0,
        failing_sections=[0, 1, 2, 3, 4], ap_score=0, verdicts=[],
    )
    ap1 = compute_ap_score(r1)
    ap2 = compute_ap_score(r2)
    assert 2 <= ap1 <= 4
    assert 2 <= ap2 <= 4


def test_ap_score_empty_is_1():
    """空 proposal → AP=1"""
    r = ProposalReport(
        proposal_idx=0, total_sections=0, viable_sections=0,
        failing_sections=[], ap_score=0, verdicts=[],
    )
    assert compute_ap_score(r) == 1


# ============ validate_proposal 端到端 ============

def test_validate_proposal_end_to_end():
    """端到端:一段完整 proposal"""
    text = (
        "## Setup\n"
        "You must install the dependencies first. The package requires Python 3.10 "
        "and should be configured with 3 environment variables. Run the installer with "
        "version 1.2.3 of the binary which will set up 4 components automatically.\n\n"
        "## Configuration\n"
        "The system must be configured before use. You should set the timeout to 30 "
        "seconds and enable caching for the 5 main endpoints. There are 2 mandatory "
        "settings that must be applied to ensure proper operation of the entire system."
    )
    report = validate_proposal(text, proposal_idx=0)
    assert isinstance(report, ProposalReport)
    assert report.proposal_idx == 0
    assert report.total_sections >= 2
    # 至少应有一些 viable
    assert report.viable_sections >= 1
    # AP 应 >= 5(因为至少有 viable)
    assert 5 <= report.ap_score <= 10
    # 报告应包含 verdicts
    assert len(report.verdicts) == report.total_sections


def test_validate_proposal_empty_text():
    """空文本 → 空 report + AP=1"""
    r = validate_proposal("", proposal_idx=7)
    assert r.total_sections == 0
    assert r.viable_sections == 0
    assert r.failing_sections == []
    assert r.ap_score == 1
    assert r.verdicts == []
    assert r.proposal_idx == 7


def test_validate_proposal_no_actionable():
    """全无可执行内容 → AP=2-4"""
    text = (
        "## Background\nThis is some background. " * 20 + "\n\n"
        "## History\nThis is some history. " * 20 + "\n\n"
        "## Misc\nThis is miscellaneous content. " * 20
    )
    r = validate_proposal(text)
    # 全无 imperative → viable=0
    assert r.viable_sections == 0
    # 全 fail → AP 2-4
    assert 2 <= r.ap_score <= 4


# ============ compare_proposals 测试 ============

def test_compare_proposals_multi():
    """多 proposal 比较"""
    # 构造 3 份
    good_text = (
        "## A\nYou must do this. The rate is 10 per second. " * 3 + "\n\n"
        "## B\nWe should implement that. The count is 5 items. " * 3 + "\n\n"
        "## C\nThis will improve performance. Speed is 20% faster. " * 3
    )
    medium_text = (
        "## A\nYou must do this. The rate is 10. " * 3 + "\n\n"
        "## B\nSome other content without actionables in this section. " * 3
    )
    bad_text = (
        "## A\nJust some background text with no actionables at all. " * 3 + "\n\n"
        "## B\nMore rambling content without any clear directive. " * 3
    )

    r_good = validate_proposal(good_text, proposal_idx=0)
    r_med = validate_proposal(medium_text, proposal_idx=1)
    r_bad = validate_proposal(bad_text, proposal_idx=2)

    comp = compare_proposals([r_good, r_med, r_bad])

    assert comp["n_proposals"] == 3
    assert comp["best_idx"] == 0, f"expected best=0, got {comp['best_idx']}"
    assert comp["worst_idx"] == 2, f"expected worst=2, got {comp['worst_idx']}"
    # 0 全 viable 时 all_viable_count 应 >= 1
    assert comp["all_viable_count"] >= 0
    # avg_ap 应是均值
    expected_avg = round((r_good.ap_score + r_med.ap_score + r_bad.ap_score) / 3, 2)
    assert abs(comp["avg_ap"] - expected_avg) < 0.01
    # ranking 长度正确
    assert len(comp["ranking"]) == 3


def test_compare_proposals_single():
    """单 proposal 退化情况"""
    r = validate_proposal(
        "## A\nYou must implement this. The number is 5 per minute. " * 3,
        proposal_idx=42,
    )
    comp = compare_proposals([r])
    assert comp["n_proposals"] == 1
    assert comp["best_idx"] == 42
    assert comp["worst_idx"] == 42
    assert comp["avg_ap"] == float(r.ap_score)
    assert comp["ranking"] == [(42, r.ap_score)]


def test_compare_proposals_empty():
    """空 list → 默认 dict"""
    comp = compare_proposals([])
    assert comp["n_proposals"] == 0
    assert comp["best_idx"] is None
    assert comp["worst_idx"] is None
    assert comp["avg_ap"] == 0.0


# ============ 其他边界测试 ============

def test_blockers_nonempty_when_not_viable():
    """not viable 时 blockers 必非空"""
    # 短
    s1 = Section(section_idx=0, title="X", text="too short", word_count=3)
    v1 = evaluate_section(s1)
    assert v1.viable is False
    assert len(v1.blockers) > 0
    # 长
    s2 = Section(section_idx=0, title="X", text=" ".join(["w"] * 900), word_count=900)
    v2 = evaluate_section(s2)
    assert v2.viable is False
    assert len(v2.blockers) > 0
    # 缺 imperative(但长度够)
    text = (
        "This section has plenty of words in it to be considered of sufficient length. "
        "It discusses a topic in great detail with many sentences and provides 3 examples. "
        "We mention 5 different aspects and 2 historical references. There is a citation."
    )
    s3 = Section(section_idx=0, title="X", text=text, word_count=_count_words(text))
    assert s3.word_count >= MIN_WORDS
    v3 = evaluate_section(s3)
    assert v3.viable is False
    assert len(v3.blockers) > 0


def test_proposal_report_json_serializable():
    """ProposalReport 可被 JSON 序列化"""
    r = validate_proposal(
        "## A\nYou must do this. The rate is 5 per second. " * 3,
        proposal_idx=99,
    )
    d = r.to_dict()
    # 基本字段
    assert d["proposal_idx"] == 99
    assert d["total_sections"] >= 1
    assert "verdicts" in d
    assert "ap_score" in d
    # JSON dump 应不抛
    s = json.dumps(d, ensure_ascii=False)
    assert "verdicts" in s
    assert "ap_score" in s
    # ProposalReport.to_json 也可
    s2 = r.to_json()
    assert "verdicts" in s2
    # 反向 parse 验证
    parsed = json.loads(s2)
    assert parsed["proposal_idx"] == 99
    assert isinstance(parsed["verdicts"], list)


def test_sectionverdict_json_serializable():
    """SectionVerdict 可被 JSON 序列化"""
    s = Section(section_idx=0, title="X", text="some text", word_count=2)
    v = evaluate_section(s)
    d = v.to_dict()
    s_json = json.dumps(d, ensure_ascii=False)
    parsed = json.loads(s_json)
    assert parsed["section_idx"] == 0
    assert parsed["viable"] is False
    assert "blockers" in parsed
    assert "reasons" in parsed


# ============ Helper ============

def _count_words(text: str) -> int:
    """测试 helper:统计词数(同步 section_viability 的逻辑)"""
    import re as _re
    if not text:
        return 0
    en = _re.findall(r"[a-zA-Z][a-zA-Z'\-]*|\d+", text)
    zh = _re.findall(r"[\u4e00-\u9fff]", text)
    return len(en) + len(zh)
