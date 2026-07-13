"""turboquant 单元测试 (19 测试,真实 assert,严禁 mock)

覆盖:
- 5 个 QuantLevel 枚举
- Q0/Q1/Q2/Q4/Q8 各等级压缩
- compress_message 单条
- should_compress 边界 (<=, >)
- apply_turboquant 触发/不触发
- apply_turboquant preserve 30
- system message 在头
- structure 不变量 (user/assistant 交替 / finish marker 在尾)
- level 字段
- JSON 序列化 roundtrip
- Config 校验
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.turboquant import (
    QuantLevel,
    Message,
    TurboQuantConfig,
    LEVEL_CHARS,
    compress_message,
    should_compress,
    apply_turboquant,
    extract_system_messages,
    is_finish_marker,
    messages_to_json,
    messages_from_json,
    config_to_json,
    config_from_json,
)


# ============ 辅助构造 ============

def make_msg(
    role: str = "user",
    content: str = "hello",
    timestamp: float = 0.0,
) -> Message:
    return Message(role=role, content=content, timestamp=timestamp)


def _hex_chars_only(s: str) -> bool:
    """是否只含 hex 字符"""
    return bool(re.fullmatch(r"[0-9a-fA-F]+", s))


# ============ 1) QuantLevel 枚举 (5 个) ============

def test_quantlevel_count_is_five():
    """QuantLevel 必须是 5 个值"""
    levels = list(QuantLevel)
    assert len(levels) == 5, f"expected 5 levels, got {len(levels)}"


def test_quantlevel_members():
    """枚举成员精确匹配 Q0/Q1/Q2/Q4/Q8"""
    names = {l.name for l in QuantLevel}
    assert names == {"Q0", "Q1", "Q2", "Q4", "Q8"}


def test_quantlevel_char_count_mapping():
    """LEVEL_CHARS 映射正确 (Q0=1, Q1=2, Q2=3, Q4=5, Q8=0)"""
    assert LEVEL_CHARS[QuantLevel.Q0] == 1
    assert LEVEL_CHARS[QuantLevel.Q1] == 2
    assert LEVEL_CHARS[QuantLevel.Q2] == 3
    assert LEVEL_CHARS[QuantLevel.Q4] == 5
    assert LEVEL_CHARS[QuantLevel.Q8] == 0


def test_quantlevel_q8_lossless():
    """Q8 是无损的 (char_count=0)"""
    assert QuantLevel.Q8.is_lossless is True
    assert QuantLevel.Q0.is_lossless is False
    assert QuantLevel.Q1.is_lossless is False
    assert QuantLevel.Q2.is_lossless is False
    assert QuantLevel.Q4.is_lossless is False


def test_quantlevel_bit_equivalent():
    """bit_equivalent 数值正确"""
    assert QuantLevel.Q0.bit_equivalent == 4
    assert QuantLevel.Q1.bit_equivalent == 8
    assert QuantLevel.Q2.bit_equivalent == 12
    assert QuantLevel.Q4.bit_equivalent == 20
    assert QuantLevel.Q8.bit_equivalent == 8


# ============ 2) compress_message 各等级 ============

def test_compress_q0_one_char():
    """Q0 → content 变为 1 个 hex 字符 (包在 [q0:..] 里)"""
    msg = make_msg(content="hello world " * 50)
    out = compress_message(msg, QuantLevel.Q0)
    assert out.role == msg.role
    assert out.timestamp == msg.timestamp
    # 格式: [q0:X]
    m = re.fullmatch(r"\[q0:([0-9a-f])\]", out.content)
    assert m is not None, f"Q0 content not in expected form: {out.content!r}"
    assert _hex_chars_only(m.group(1))
    assert len(m.group(1)) == 1


def test_compress_q1_two_chars():
    """Q1 → 2 个 hex 字符"""
    msg = make_msg(content="the quick brown fox jumps over the lazy dog")
    out = compress_message(msg, QuantLevel.Q1)
    m = re.fullmatch(r"\[q1:([0-9a-f]{2})\]", out.content)
    assert m is not None, f"Q1 content not in expected form: {out.content!r}"
    assert len(m.group(1)) == 2


def test_compress_q2_three_chars():
    """Q2 → 3 个 hex 字符"""
    msg = make_msg(content="alpha beta gamma delta epsilon")
    out = compress_message(msg, QuantLevel.Q2)
    m = re.fullmatch(r"\[q2:([0-9a-f]{3})\]", out.content)
    assert m is not None, f"Q2 content not in expected form: {out.content!r}"
    assert len(m.group(1)) == 3


def test_compress_q4_five_chars():
    """Q4 → 5 个 hex 字符"""
    msg = make_msg(content="one two three four five six seven eight")
    out = compress_message(msg, QuantLevel.Q4)
    m = re.fullmatch(r"\[q4:([0-9a-f]{5})\]", out.content)
    assert m is not None, f"Q4 content not in expected form: {out.content!r}"
    assert len(m.group(1)) == 5


def test_compress_q8_lossless():
    """Q8 → 完整保留 content (加 [Q8] 前缀)"""
    original = "this is the entire original message body that must not be truncated"
    msg = make_msg(content=original)
    out = compress_message(msg, QuantLevel.Q8)
    assert out.content == f"[Q8] {original}", (
        f"Q8 should preserve content, got {out.content!r}"
    )
    # 压缩后长度 ≥ 原长度 (因为加了前缀)
    assert len(out.content) >= len(original)


def test_compress_deterministic_same_content():
    """同 content + 同 level → 同指纹 (sha256 决定性)"""
    a = compress_message(make_msg(content="abc"), QuantLevel.Q4)
    b = compress_message(make_msg(content="abc"), QuantLevel.Q4)
    assert a.content == b.content


def test_compress_different_content_different_fingerprint():
    """不同 content → 不同指纹"""
    a = compress_message(make_msg(content="alpha"), QuantLevel.Q4)
    b = compress_message(make_msg(content="beta"), QuantLevel.Q4)
    assert a.content != b.content


# ============ 3) should_compress 决策 ============

def test_should_compress_below_cap_returns_false():
    """len <= hard_cap → False"""
    msgs = [make_msg(content=f"msg-{i}") for i in range(30)]
    cfg = TurboQuantConfig(hard_cap=60, preserve=30)
    assert should_compress(msgs, cfg) is False


def test_should_compress_above_cap_returns_true():
    """len > hard_cap → True"""
    msgs = [make_msg(content=f"msg-{i}") for i in range(61)]
    cfg = TurboQuantConfig(hard_cap=60, preserve=30)
    assert should_compress(msgs, cfg) is True


def test_should_compress_empty_returns_false():
    """空列表 → False (没东西可压)"""
    cfg = TurboQuantConfig()
    assert should_compress([], cfg) is False


# ============ 4) apply_turboquant 行为 ============

def test_apply_no_compress_under_cap():
    """60 条及以下 → 不压缩,长度不变"""
    msgs = [make_msg(role="user" if i % 2 == 0 else "assistant",
                     content=f"msg-{i}", timestamp=float(i))
            for i in range(30)]
    cfg = TurboQuantConfig(hard_cap=60, preserve=30, level=QuantLevel.Q4)
    out = apply_turboquant(msgs, cfg)
    assert len(out) == len(msgs)
    # 内容应当原样保留
    assert [m.content for m in out] == [m.content for m in msgs]


def test_apply_compress_over_cap():
    """61+ 条 → 压缩,总长 = system + preserve + (n-preserve) + finish"""
    msgs = [make_msg(role="user" if i % 2 == 0 else "assistant",
                     content=f"long body content message {i}", timestamp=float(i))
            for i in range(65)]
    cfg = TurboQuantConfig(hard_cap=60, preserve=30, level=QuantLevel.Q4)
    out = apply_turboquant(msgs, cfg)
    # 长度不变 (替换不删)
    assert len(out) == 65
    # 前 30 条原样
    for i in range(30):
        assert out[i].content == msgs[i].content, f"msg {i} should be preserved"
    # 后 35 条被压缩 (格式为 [q4:xxxxx])
    for i in range(30, 65):
        assert re.fullmatch(r"\[q4:[0-9a-f]{5}\]", out[i].content), (
            f"msg {i} should be Q4-compressed, got {out[i].content!r}"
        )


def test_apply_preserve_30_default():
    """默认 preserve=30 — 前 30 条原样"""
    msgs = [make_msg(content=f"keep-me-{i}", timestamp=float(i))
            for i in range(70)]
    cfg = TurboQuantConfig()  # 默认 hard_cap=60, preserve=30, level=Q4
    out = apply_turboquant(msgs, cfg)
    assert len(out) == 70
    for i in range(30):
        assert out[i].content == f"keep-me-{i}"
    # 30..70 被压缩
    for i in range(30, 70):
        assert out[i].content.startswith("[q4:")


# ============ 5) 结构不变量 ============

def test_system_message_stays_at_head():
    """system message 始终在头"""
    msgs = [make_msg(role="system", content="You are a helpful assistant.", timestamp=0.0)]
    msgs.append(make_msg(role="system", content="additional system context", timestamp=0.1))
    for i in range(65):
        msgs.append(make_msg(role="user", content=f"u{i}", timestamp=float(i)))
    cfg = TurboQuantConfig(hard_cap=60, preserve=30, level=QuantLevel.Q4)
    out = apply_turboquant(msgs, cfg)
    # 前 2 条必须是 system 且内容原样
    assert out[0].role == "system"
    assert out[0].content == "You are a helpful assistant."
    assert out[1].role == "system"
    assert out[1].content == "additional system context"
    # 第 3 条起才可能是 user/assistant
    for i in range(2, len(out)):
        assert out[i].role in ("user", "assistant", "tool")


def test_structure_role_alternation_preserved():
    """user/assistant 交替顺序在原列表中保持不变"""
    msgs = [make_msg(role="user" if i % 2 == 0 else "assistant",
                     content=f"msg-{i}", timestamp=float(i))
            for i in range(65)]
    cfg = TurboQuantConfig(hard_cap=60, preserve=30, level=QuantLevel.Q4)
    out = apply_turboquant(msgs, cfg)
    # 第 0 条是 user (原列表 idx 0),后面 user/assistant 交替
    roles_out = [m.role for m in out]
    roles_orig = [m.role for m in msgs]
    assert roles_out == roles_orig, (
        f"role order changed!\norig: {roles_orig}\nout:  {roles_out}"
    )


def test_finish_marker_at_tail_not_compressed():
    """末尾 finish marker 不被压缩"""
    msgs = [make_msg(role="user" if i % 2 == 0 else "assistant",
                     content=f"msg-{i}", timestamp=float(i))
            for i in range(64)]
    msgs.append(make_msg(role="assistant", content="<FINISH>", timestamp=64.0))
    cfg = TurboQuantConfig(hard_cap=60, preserve=30, level=QuantLevel.Q4)
    out = apply_turboquant(msgs, cfg)
    # 末条必须是 <FINISH> 原样
    assert out[-1].content == "<FINISH>"
    assert out[-1].role == "assistant"


# ============ 6) Config & level 字段 ============

def test_config_defaults():
    """Config 默认值: hard_cap=60, preserve=30, level=Q4"""
    cfg = TurboQuantConfig()
    assert cfg.hard_cap == 60
    assert cfg.preserve == 30
    assert cfg.level == QuantLevel.Q4


def test_config_level_field_in_result():
    """压缩结果可访问 level 字段"""
    cfg = TurboQuantConfig(level=QuantLevel.Q2)
    assert cfg.level is QuantLevel.Q2
    assert cfg.level.name == "Q2"


def test_config_invalid_preserve_raises():
    """preserve > hard_cap 应抛 ValueError"""
    import pytest
    with pytest.raises(ValueError):
        TurboQuantConfig(hard_cap=10, preserve=20)


def test_config_invalid_hard_cap_raises():
    """hard_cap <= 0 应抛 ValueError"""
    import pytest
    with pytest.raises(ValueError):
        TurboQuantConfig(hard_cap=0, preserve=0)


# ============ 7) JSON 序列化 ============

def test_messages_json_roundtrip():
    """Message 列表 JSON 往返一致"""
    msgs = [
        make_msg(role="system", content="sys", timestamp=0.0),
        make_msg(role="user", content="hi", timestamp=1.0),
        make_msg(role="assistant", content="hello", timestamp=2.0),
    ]
    text = messages_to_json(msgs)
    assert isinstance(text, str)
    parsed = json.loads(text)
    assert len(parsed) == 3
    restored = messages_from_json(text)
    assert len(restored) == 3
    for orig, back in zip(msgs, restored):
        assert orig.role == back.role
        assert orig.content == back.content
        assert orig.timestamp == back.timestamp


def test_config_json_roundtrip():
    """Config JSON 往返一致"""
    cfg = TurboQuantConfig(hard_cap=80, preserve=20, level=QuantLevel.Q2)
    text = config_to_json(cfg)
    parsed = json.loads(text)
    assert parsed["hard_cap"] == 80
    assert parsed["preserve"] == 20
    assert parsed["level"] == "Q2"
    restored = config_from_json(text)
    assert restored.hard_cap == 80
    assert restored.preserve == 20
    assert restored.level == QuantLevel.Q2
