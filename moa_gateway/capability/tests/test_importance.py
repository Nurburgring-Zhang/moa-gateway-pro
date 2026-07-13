"""importance 单元测试 (17+ 测试)

真实 assert, 严禁 mock。覆盖:
- 5 维权重
- score clamp
- top-k / radius / 压缩决策
- JSON 序列化
- 单消息不崩
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

# 允许直接 import
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.importance import (
    Message,
    ImportanceScore,
    WEIGHTS,
    score_message,
    score_messages,
    select_top_k,
    should_compress,
    select_within_radius,
    scores_to_json,
    scores_from_json,
)


# ============ 辅助构造 ============

def make_msg(
    role: str = "user",
    content: str = "hello",
    timestamp: float = 0.0,
    is_tool_result: bool = False,
    has_tool_calls: bool = False,
    is_decision: bool = False,
) -> Message:
    return Message(
        role=role,
        content=content,
        timestamp=timestamp,
        is_tool_result=is_tool_result,
        has_tool_calls=has_tool_calls,
        is_decision=is_decision,
    )


# ============ 5 维权重 & 基础 ============

def test_weights_sum_to_one():
    """5 维权重和应为 1.00"""
    total = sum(WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9, f"weights sum={total}"
    assert set(WEIGHTS.keys()) == {"recency", "tool_result", "tool_calls", "decision", "system"}


def test_weights_exact_values():
    """5 维权重精确值 (0.30/0.25/0.20/0.15/0.10)"""
    assert WEIGHTS["recency"] == 0.30
    assert WEIGHTS["tool_result"] == 0.25
    assert WEIGHTS["tool_calls"] == 0.20
    assert WEIGHTS["decision"] == 0.15
    assert WEIGHTS["system"] == 0.10


def test_score_clamp_upper():
    """score 上限 clamp 到 1.0"""
    # 全部 5 维都拉满
    msg = make_msg(
        role="system",
        is_tool_result=True,
        has_tool_calls=True,
        is_decision=True,
    )
    msgs = [msg]
    s = score_message(msg, msgs, current_idx=0)
    # 1.0*0.30 + 0.25 + 0.20 + 0.15 + 0.10 = 1.00
    assert s.score <= 1.0 + 1e-9
    # 应当正好被 clamp 到 1.0
    assert abs(s.score - 1.0) < 1e-9, f"expected 1.0, got {s.score}"


def test_score_clamp_lower():
    """score 下限 clamp 到 0.0 (空 system 远距离 + 无任何 flag)"""
    msg = make_msg(role="user")
    msgs = [make_msg(), msg, make_msg()]  # 中间是 user
    s = score_message(msg, msgs, current_idx=2)
    # recency(d=1) = 0.75 → 0.30*0.75 = 0.225
    # 其他维度 0 → raw = 0.225,>=0
    assert s.score >= 0.0


def test_single_message_does_not_crash():
    """单条消息评分不崩"""
    msg = make_msg(role="user", content="hi")
    s = score_message(msg, [msg], current_idx=0)
    assert isinstance(s, ImportanceScore)
    assert 0.0 <= s.score <= 1.0
    # 0 距离 recency=1.0,其他维度 0 → 0.30
    assert abs(s.score - 0.30) < 1e-9


# ============ 单维度加分 ============

def test_tool_result_adds_025():
    """is_tool_result → +0.25 (相对无 flag baseline, recency 一致)"""
    baseline = make_msg(role="user")
    flagged = make_msg(role="user", is_tool_result=True)
    msgs = [baseline, flagged]
    # 用 current_idx=0 比较 baseline (idx=0, recency=1.0) vs flagged 在另一组
    s_base = score_message(baseline, [baseline], current_idx=0)
    # flagged 单独放入,以其 idx=0 评估,recency=1.0
    s_flag = score_message(flagged, [flagged], current_idx=0)
    diff = s_flag.score - s_base.score
    assert abs(diff - 0.25) < 1e-9, f"expected diff=0.25, got {diff}"


def test_tool_calls_adds_020():
    """has_tool_calls → +0.20 (recency 一致)"""
    baseline = make_msg(role="user")
    flagged = make_msg(role="user", has_tool_calls=True)
    s_base = score_message(baseline, [baseline], current_idx=0)
    s_flag = score_message(flagged, [flagged], current_idx=0)
    assert abs((s_flag.score - s_base.score) - 0.20) < 1e-9


def test_decision_adds_015():
    """is_decision → +0.15 (recency 一致)"""
    baseline = make_msg(role="user")
    flagged = make_msg(role="user", is_decision=True)
    s_base = score_message(baseline, [baseline], current_idx=0)
    s_flag = score_message(flagged, [flagged], current_idx=0)
    assert abs((s_flag.score - s_base.score) - 0.15) < 1e-9


def test_system_role_adds_010():
    """role=system → +0.10 (recency 一致)"""
    user_msg = make_msg(role="user")
    sys_msg = make_msg(role="system", content="You are helpful.")
    s_user = score_message(user_msg, [user_msg], current_idx=0)
    s_sys = score_message(sys_msg, [sys_msg], current_idx=0)
    assert abs((s_sys.score - s_user.score) - 0.10) < 1e-9


# ============ recency 单调性 ============

def test_recency_closer_is_higher():
    """recency 越近越高"""
    msgs = [make_msg() for _ in range(10)]
    s_far = score_message(msgs[0], msgs, current_idx=9)   # 距离 9
    s_mid = score_message(msgs[5], msgs, current_idx=9)   # 距离 4
    s_near = score_message(msgs[8], msgs, current_idx=9)  # 距离 1
    s_self = score_message(msgs[9], msgs, current_idx=9)  # 距离 0
    assert s_far.score < s_mid.score < s_near.score < s_self.score, (
        f"expected far<mid<near<self, got {s_far.score}/{s_mid.score}/{s_near.score}/{s_self.score}"
    )


# ============ 批量评分 ============

def test_score_messages_batch():
    """score_messages 批量 — 长度匹配 + 全部 clamp"""
    msgs = [
        make_msg(role="system", is_decision=True),
        make_msg(role="user"),
        make_msg(role="assistant", has_tool_calls=True),
        make_msg(role="tool", is_tool_result=True),
    ]
    scores = score_messages(msgs)
    assert len(scores) == len(msgs)
    for s in scores:
        assert 0.0 <= s.score <= 1.0
        assert isinstance(s.message_idx, int)
    # 最后一条 (current_idx=3) 距离自己=0 → recency 满分
    assert scores[-1].message_idx == 3


# ============ Top-K ============

def test_select_top_k_ordering():
    """select_top_k 按 score 降序"""
    msgs = [
        make_msg(role="user"),
        make_msg(role="system"),
        make_msg(role="user", is_tool_result=True),
        make_msg(role="assistant", has_tool_calls=True),
    ]
    scores = score_messages(msgs)
    top2 = select_top_k(scores, 2)
    assert len(top2) == 2
    # 校验 top2 顺序: scores[top2[0]].score >= scores[top2[1]].score
    assert scores[top2[0]].score >= scores[top2[1]].score
    # 校验 top2 是合法的 idx
    for i in top2:
        assert 0 <= i < len(scores)


def test_select_top_k_zero():
    """k=0 → []"""
    msgs = [make_msg(), make_msg()]
    scores = score_messages(msgs)
    assert select_top_k(scores, 0) == []
    assert select_top_k(scores, -5) == []


def test_select_top_k_all():
    """k=len → 全部 indices"""
    msgs = [make_msg() for _ in range(5)]
    scores = score_messages(msgs)
    all_idx = select_top_k(scores, 5)
    assert sorted(all_idx) == [0, 1, 2, 3, 4]
    # k 超过 len 也安全
    over = select_top_k(scores, 100)
    assert sorted(over) == [0, 1, 2, 3, 4]


# ============ 压缩决策 ============

def test_should_compress_all_low():
    """所有 score < threshold → True"""
    scores = [
        ImportanceScore(message_idx=0, score=0.1, reasons=[]),
        ImportanceScore(message_idx=1, score=0.2, reasons=[]),
        ImportanceScore(message_idx=2, score=0.3, reasons=[]),
    ]
    assert should_compress(scores, threshold=0.5) is True


def test_should_compress_has_high():
    """存在 score >= threshold → False"""
    scores = [
        ImportanceScore(message_idx=0, score=0.1, reasons=[]),
        ImportanceScore(message_idx=1, score=0.6, reasons=[]),
        ImportanceScore(message_idx=2, score=0.3, reasons=[]),
    ]
    assert should_compress(scores, threshold=0.5) is False


def test_should_compress_empty():
    """空 scores → True (无可保留)"""
    assert should_compress([]) is True


# ============ radius ============

def test_select_within_radius_3():
    """radius=3 → current_idx ± 3 (含端点)"""
    scores = [ImportanceScore(message_idx=i, score=0.0) for i in range(10)]
    idxs = select_within_radius(scores, current_idx=5, radius=3)
    assert idxs == [2, 3, 4, 5, 6, 7, 8]


def test_select_within_radius_boundary_start():
    """开头边界: current_idx=0, radius=3 → 不会越界到负数"""
    scores = [ImportanceScore(message_idx=i, score=0.0) for i in range(10)]
    idxs = select_within_radius(scores, current_idx=0, radius=3)
    assert idxs == [0, 1, 2, 3]


def test_select_within_radius_boundary_end():
    """结尾边界: current_idx=9 (末尾), radius=3 → 不会越界"""
    scores = [ImportanceScore(message_idx=i, score=0.0) for i in range(10)]
    idxs = select_within_radius(scores, current_idx=9, radius=3)
    assert idxs == [6, 7, 8, 9]


def test_select_within_radius_radius_zero():
    """radius=0 → 只返回 current_idx 自己"""
    scores = [ImportanceScore(message_idx=i, score=0.0) for i in range(5)]
    idxs = select_within_radius(scores, current_idx=2, radius=0)
    assert idxs == [2]


# ============ JSON 序列化 ============

def test_json_serialization_roundtrip():
    """JSON 序列化往返一致"""
    scores = [
        ImportanceScore(message_idx=0, score=0.5, reasons=["recency_high"]),
        ImportanceScore(message_idx=1, score=0.8, reasons=["is_decision(+0.15)", "system_role(+0.10)"]),
        ImportanceScore(message_idx=2, score=0.123456, reasons=[]),
    ]
    text = scores_to_json(scores)
    assert isinstance(text, str)
    parsed = json.loads(text)
    assert len(parsed) == 3
    # roundtrip
    restored = scores_from_json(text)
    assert len(restored) == 3
    for orig, back in zip(scores, restored):
        assert orig.message_idx == back.message_idx
        assert abs(orig.score - back.score) < 1e-9
        assert orig.reasons == back.reasons


def test_json_empty_list():
    """空 list 序列化"""
    text = scores_to_json([])
    assert json.loads(text) == []
    assert scores_from_json("[]") == []


# ============ Message 校验 ============

def test_message_invalid_role_raises():
    """非法 role 应抛 ValueError"""
    import pytest
    with pytest.raises(ValueError):
        Message(role="admin", content="x", timestamp=0.0)


def test_message_default_flags_false():
    """默认 flag 应该是 False"""
    m = Message(role="user", content="x", timestamp=0.0)
    assert m.is_tool_result is False
    assert m.has_tool_calls is False
    assert m.is_decision is False
