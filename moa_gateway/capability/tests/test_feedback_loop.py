"""feedback_loop 单元测试 (>= 20 测试)

真实 assert, 严禁 mock; 用 tmp_path 隔离文件 IO。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.feedback_loop import (
    CONVERGENCE_STD,
    JACCARD_CONVERGENCE_THRESHOLD,
    Feedback,
    IterationRecord,
    analyze_iteration,
    append_iteration,
    cross_iter_synthesize,
    detect_convergence,
    feedback_to_dict,
    format_next_iter_prompt,
    load_feedback,
    load_history,
    record_to_dict,
    save_feedback,
)

# ============ 辅助: 构造 IterationRecord ============

def make_record(
    iter_idx: int,
    proposals: list,
    panel_scores: dict,
    convergent: list = None,
    conflicts: list = None,
    selected: int = 0,
) -> IterationRecord:
    return IterationRecord(
        iter_idx=iter_idx,
        proposals=proposals,
        panel_scores=panel_scores,
        convergent_ideas=convergent or [],
        conflicts_resolved=conflicts or [],
        selected_proposal_idx=selected,
        timestamp=time.time(),
    )


# ============ 持久化: save/load 往返 ============

def test_save_and_load_feedback_roundtrip(tmp_path):
    """save_feedback → load_feedback 字段完全一致"""
    fb = Feedback(
        iter_idx=3,
        summary="iter 3 stable",
        strengths=["prop[0] score=45"],
        weaknesses=["prop[2] score=12"],
        next_iter_directives=["fix weak", "raise consensus"],
    )
    p = tmp_path / "feedback.json"
    save_feedback(str(p), fb)
    loaded = load_feedback(str(p))
    assert loaded.iter_idx == 3
    assert loaded.summary == "iter 3 stable"
    assert loaded.strengths == ["prop[0] score=45"]
    assert loaded.weaknesses == ["prop[2] score=12"]
    assert loaded.next_iter_directives == ["fix weak", "raise consensus"]


def test_load_feedback_missing_file_raises(tmp_path):
    """文件不存在 → FileNotFoundError"""
    p = tmp_path / "missing.json"
    with pytest.raises(FileNotFoundError):
        load_feedback(str(p))


# ============ 持久化: append + load_history ============

def test_append_iteration_multi_round(tmp_path):
    """append_iteration 累积多轮 + 顺序正确"""
    p = tmp_path / "history.json"
    r1 = make_record(1, ["A", "B"], {0: 35, 1: 40}, selected=1)
    f1 = Feedback(iter_idx=1, summary="s1", strengths=["p1"], weaknesses=[])
    append_iteration(str(p), r1, f1)

    r2 = make_record(2, ["C", "D"], {0: 42, 1: 38}, selected=0)
    f2 = Feedback(iter_idx=2, summary="s2", strengths=["p0"], weaknesses=[])
    append_iteration(str(p), r2, f2)

    r3 = make_record(3, ["E"], {0: 45}, selected=0)
    f3 = Feedback(iter_idx=3, summary="s3", strengths=["p0"], weaknesses=[])
    append_iteration(str(p), r3, f3)

    history = load_history(str(p))
    assert len(history) == 3
    assert [r.iter_idx for r in history] == [1, 2, 3]
    # latest_feedback 应为最后一次
    loaded_fb = load_feedback(str(p))
    assert loaded_fb.iter_idx == 3
    assert loaded_fb.summary == "s3"


def test_load_history_empty_file_returns_empty(tmp_path):
    """空路径 → [] (不抛错)"""
    p = tmp_path / "nope.json"
    assert load_history(str(p)) == []


def test_load_history_orders_by_iter_idx(tmp_path):
    """load_history 按 iter_idx 升序"""
    p = tmp_path / "hist.json"
    # 乱序追加
    for idx, fb_idx in [(2, 2), (1, 1), (3, 3)]:
        r = make_record(idx, [f"p{idx}"], {0: 30.0 + idx}, selected=0)
        f = Feedback(iter_idx=fb_idx, summary=f"s{fb_idx}")
        append_iteration(str(p), r, f)
    history = load_history(str(p))
    assert [r.iter_idx for r in history] == [1, 2, 3]


# ============ analyze_iteration: strengths/weaknesses 阈值 ============

def test_analyze_iteration_extracts_strengths_above_40():
    """strengths: panel_score >= 40"""
    rec = make_record(
        1,
        ["good idea A", "great idea B", "meh idea C", "bad idea D"],
        {0: 35.0, 1: 45.0, 2: 41.0, 3: 10.0},
        selected=1,
    )
    fb = analyze_iteration(rec)
    # strengths 应包含 1, 2 (>= 40)
    assert len(fb.strengths) == 2
    assert all("proposal[1]" in s or "proposal[2]" in s for s in fb.strengths)


def test_analyze_iteration_extracts_weaknesses_below_20():
    """weaknesses: panel_score < 20"""
    rec = make_record(
        1,
        ["good", "bad A", "bad B", "ok"],
        {0: 35.0, 1: 10.0, 2: 15.0, 3: 25.0},
        selected=0,
    )
    fb = analyze_iteration(rec)
    # weaknesses 应包含 1, 2 (< 20)
    assert len(fb.weaknesses) == 2
    assert all("proposal[1]" in w or "proposal[2]" in w for w in fb.weaknesses)


def test_analyze_iteration_threshold_boundary():
    """阈值边界: 40 算 strength, 20 不算 weakness"""
    rec = make_record(
        1,
        ["edge1", "edge2", "edge3"],
        {0: 40.0, 1: 20.0, 2: 39.9},
        selected=0,
    )
    fb = analyze_iteration(rec)
    # 40 → strength, 39.9 → 不是
    # 20 → 不是 weakness (严格 < 20), 39.9 → 不是 weakness
    assert len(fb.strengths) == 1
    assert "proposal[0]" in fb.strengths[0]
    assert len(fb.weaknesses) == 0


def test_analyze_iteration_generates_directives():
    """next_iter_directives 在有 weakness 时非空"""
    rec = make_record(
        1,
        ["good", "bad"],
        {0: 45.0, 1: 10.0},
        convergent=["theme X"],
        conflicts=["opt1 vs opt2"],
        selected=0,
    )
    fb = analyze_iteration(rec)
    assert len(fb.next_iter_directives) >= 1
    # 必含 "Improve weak proposals" 类指令
    assert any("weak" in d.lower() or "below" in d.lower() for d in fb.next_iter_directives)


def test_analyze_iteration_summary_contains_stats():
    """summary 应包含 mean / std / selected"""
    rec = make_record(
        2,
        ["A", "B", "C"],
        {0: 30.0, 1: 40.0, 2: 50.0},
        selected=2,
    )
    fb = analyze_iteration(rec)
    assert "Iter 2" in fb.summary
    assert "selected=2" in fb.summary
    assert "std=" in fb.summary
    assert "mean=" in fb.summary


# ============ format_next_iter_prompt ============

def test_format_next_iter_prompt_includes_feedback(tmp_path):
    """format_next_iter_prompt 拼装包含 strengths/weaknesses/directives"""
    p = tmp_path / "hist.json"
    rec = make_record(
        1,
        ["good X", "bad Y"],
        {0: 45.0, 1: 10.0},
        selected=0,
    )
    fb = analyze_iteration(rec)
    append_iteration(str(p), rec, fb)

    prompt = format_next_iter_prompt(str(p))
    assert "Previous iteration feedback:" in prompt
    assert "Strengths:" in prompt
    assert "Weaknesses:" in prompt
    assert "Directives for next iteration:" in prompt
    # 至少包含 strength/weakness 文本片段
    assert "proposal[0]" in prompt
    assert "proposal[1]" in prompt


def test_format_next_iter_prompt_empty_history(tmp_path):
    """空 history → 不崩, 含 fallback"""
    p = tmp_path / "empty.json"
    prompt = format_next_iter_prompt(str(p))
    assert "Previous iteration feedback:" in prompt
    assert "no prior iteration history" in prompt


# ============ detect_convergence ============

def test_detect_convergence_stable_returns_converged():
    """3 轮 top1 稳定 → converged=True, trend=stable"""
    history = [
        make_record(i, [f"p{i}"], {0: 40.0}, selected=0) for i in range(1, 4)
    ]
    result = detect_convergence(history, window=3)
    assert result["converged"] is True
    assert result["std"] < CONVERGENCE_STD
    assert result["trend"] in ("stable", "up", "down")
    assert result["samples"] == 3
    assert result["top1_scores"] == [40.0, 40.0, 40.0]


def test_detect_convergence_not_converged_with_volatile_scores():
    """分数波动大 → converged=False"""
    history = [
        make_record(1, ["a"], {0: 20.0}, selected=0),
        make_record(2, ["b"], {0: 45.0}, selected=0),
        make_record(3, ["c"], {0: 25.0}, selected=0),
        make_record(4, ["d"], {0: 50.0}, selected=0),
    ]
    result = detect_convergence(history, window=3)
    assert result["converged"] is False
    assert result["std"] > CONVERGENCE_STD
    assert result["samples"] == 3


def test_detect_convergence_trend_up():
    """trend=up: 后半均值高于前半"""
    history = [
        make_record(1, ["a"], {0: 20.0}, selected=0),
        make_record(2, ["b"], {0: 22.0}, selected=0),
        make_record(3, ["c"], {0: 40.0}, selected=0),
        make_record(4, ["d"], {0: 42.0}, selected=0),
    ]
    result = detect_convergence(history, window=4)
    assert result["trend"] == "up"


def test_detect_convergence_trend_down():
    """trend=down: 后半均值低于前半"""
    history = [
        make_record(1, ["a"], {0: 50.0}, selected=0),
        make_record(2, ["b"], {0: 48.0}, selected=0),
        make_record(3, ["c"], {0: 20.0}, selected=0),
        make_record(4, ["d"], {0: 22.0}, selected=0),
    ]
    result = detect_convergence(history, window=4)
    assert result["trend"] == "down"


def test_detect_convergence_trend_stable():
    """trend=stable: 分数基本不变"""
    history = [
        make_record(1, ["a"], {0: 35.0}, selected=0),
        make_record(2, ["b"], {0: 35.5}, selected=0),
        make_record(3, ["c"], {0: 34.5}, selected=0),
        make_record(4, ["d"], {0: 35.0}, selected=0),
    ]
    result = detect_convergence(history, window=4)
    assert result["trend"] == "stable"


def test_detect_convergence_empty_history():
    """空 history → converged=False, samples=0"""
    result = detect_convergence([], window=3)
    assert result["converged"] is False
    assert result["samples"] == 0
    assert result["top1_scores"] == []
    assert result["trend"] == "stable"


def test_detect_convergence_single_iter_does_not_crash():
    """单 iter 不崩, samples=1"""
    history = [make_record(1, ["a"], {0: 30.0}, selected=0)]
    result = detect_convergence(history, window=3)
    assert result["samples"] == 1
    assert result["top1_scores"] == [30.0]
    # 单 iter → 默认 trend=stable
    assert result["trend"] == "stable"


# ============ cross_iter_synthesize ============

def test_cross_iter_synthesize_convergence_high_jaccard():
    """关键词 Jaccard > 0.5 → convergence=True"""
    prev = make_record(
        1,
        ["we need caching layer with redis storage backend"],
        {0: 30.0},
        selected=0,
    )
    curr = make_record(
        2,
        ["redis caching backend is critical for performance layer"],
        {0: 35.0},
        selected=0,
    )
    result = cross_iter_synthesize(prev, curr)
    assert result["jaccard"] > 0.0
    # 高度相关主题, 共享 redis/caching/backend/layer/performance
    assert result["convergence"] is True


def test_cross_iter_synthesize_convergence_low_jaccard():
    """关键词 Jaccard ≤ 0.5 → convergence=False"""
    prev = make_record(
        1,
        ["alpha bravo charlie delta echo foxtrot"],
        {0: 30.0},
        selected=0,
    )
    curr = make_record(
        2,
        ["golf hotel india juliet kilo lima mike november oscar"],
        {0: 35.0},
        selected=0,
    )
    result = cross_iter_synthesize(prev, curr)
    assert result["jaccard"] <= JACCARD_CONVERGENCE_THRESHOLD
    assert result["convergence"] is False


def test_cross_iter_synthesize_best_of_each_picks_highest():
    """best_of_each 各取己方最高分"""
    prev = make_record(1, ["A", "B", "C"], {0: 20.0, 1: 35.0, 2: 25.0}, selected=1)
    curr = make_record(2, ["D", "E"], {0: 40.0, 1: 30.0}, selected=0)
    result = cross_iter_synthesize(prev, curr)
    assert result["prev_best"]["proposal_idx"] == 1
    assert result["prev_best"]["score"] == 35.0
    assert result["curr_best"]["proposal_idx"] == 0
    assert result["curr_best"]["score"] == 40.0


def test_cross_iter_synthesize_adoption_curr_higher():
    """curr > prev * 1.05 → adoption='curr'"""
    prev = make_record(1, ["A"], {0: 30.0}, selected=0)
    curr = make_record(2, ["B"], {0: 40.0}, selected=0)  # 40 > 30*1.05=31.5
    result = cross_iter_synthesize(prev, curr)
    assert result["recommended_adoption"] == "curr"
    assert result["score_delta"] == pytest.approx(10.0)


def test_cross_iter_synthesize_adoption_prev_higher():
    """prev > curr * 1.05 → adoption='prev'"""
    prev = make_record(1, ["A"], {0: 50.0}, selected=0)
    curr = make_record(2, ["B"], {0: 30.0}, selected=0)  # 50 > 30*1.05
    result = cross_iter_synthesize(prev, curr)
    assert result["recommended_adoption"] == "prev"
    assert result["score_delta"] == pytest.approx(-20.0)


def test_cross_iter_synthesize_adoption_either_close():
    """两轮分数接近 (差异 < 5%) → adoption='either'"""
    prev = make_record(1, ["A"], {0: 40.0}, selected=0)
    curr = make_record(2, ["B"], {0: 41.0}, selected=0)  # 41 < 40*1.05=42
    result = cross_iter_synthesize(prev, curr)
    assert result["recommended_adoption"] == "either"


# ============ 序列化 ============

def test_record_to_dict_has_all_fields():
    """record_to_dict 输出包含所有字段"""
    rec = make_record(1, ["A"], {0: 30.0}, convergent=["x"], conflicts=["y"], selected=0)
    d = record_to_dict(rec)
    assert d["iter_idx"] == 1
    assert d["proposals"] == ["A"]
    assert d["panel_scores"] == {0: 30.0}
    assert d["convergent_ideas"] == ["x"]
    assert d["conflicts_resolved"] == ["y"]
    assert d["selected_proposal_idx"] == 0
    assert "timestamp" in d


def test_feedback_to_dict_has_all_fields():
    """feedback_to_dict 输出包含所有字段"""
    fb = Feedback(
        iter_idx=2,
        summary="s",
        strengths=["a"],
        weaknesses=["b"],
        next_iter_directives=["c"],
    )
    d = feedback_to_dict(fb)
    assert d["iter_idx"] == 2
    assert d["summary"] == "s"
    assert d["strengths"] == ["a"]
    assert d["weaknesses"] == ["b"]
    assert d["next_iter_directives"] == ["c"]
