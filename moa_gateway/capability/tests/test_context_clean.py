"""context_clean 真实测试 — 端到端验证(非 mock)"""
import sys
import copy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.context_clean import (
    Message, CleanStats,
    clean_messages, merge_consecutive_role, strip_orphan_tool,
    prepend_user_if_first_assistant, truncate_to_chars,
    to_openai_format, from_openai_format,
)


# ============ Stage 1 ============

def test_stage1_filter_empty_content():
    """Stage 1: content == '' 被过滤"""
    msgs = [
        Message("user", ""),
        Message("user", "hello"),
        Message("assistant", ""),
        Message("assistant", "ok"),
    ]
    cleaned, stats = clean_messages(msgs)
    contents = [m.content for m in cleaned]
    assert "" not in contents, f"empty content survived: {contents}"
    assert "hello" in contents
    assert "ok" in contents
    assert stats.original_count == 4
    print("  \u2713 test_stage1_filter_empty_content")
    assert True


# ============ Stage 2 ============

def test_stage2_merge_consecutive_user():
    """Stage 2: 连续 user 合并"""
    msgs = [
        Message("user", "first"),
        Message("user", "second"),
        Message("user", "third"),
        Message("assistant", "reply"),
    ]
    cleaned, stats = clean_messages(msgs)
    assert len(cleaned) == 2, f"expected 2, got {len(cleaned)}: {cleaned}"
    assert cleaned[0].role == "user"
    assert "first" in cleaned[0].content
    assert "second" in cleaned[0].content
    assert "third" in cleaned[0].content
    assert "\n\n" in cleaned[0].content
    assert stats.merged_pairs >= 2
    print("  \u2713 test_stage2_merge_consecutive_user")
    assert True


def test_stage2_no_merge_across_roles():
    """Stage 2: 跨角色不合并"""
    msgs = [
        Message("user", "hi"),
        Message("assistant", "hello"),
        Message("user", "how are you?"),
        Message("assistant", "fine"),
    ]
    cleaned, stats = clean_messages(msgs)
    assert len(cleaned) == 4, f"expected 4, got {len(cleaned)}: {cleaned}"
    assert stats.merged_pairs == 0
    print("  \u2713 test_stage2_no_merge_across_roles")
    assert True


# ============ Stage 3 ============

def test_stage3_multi_system_merged_to_head():
    """Stage 3: 多 system 合并到头"""
    msgs = [
        Message("user", "q1"),
        Message("system", "sys A"),
        Message("assistant", "a1"),
        Message("system", "sys B"),
        Message("user", "q2"),
    ]
    cleaned, stats = clean_messages(msgs)
    assert cleaned[0].role == "system"
    assert "sys A" in cleaned[0].content
    assert "sys B" in cleaned[0].content
    sys_indices = [i for i, m in enumerate(cleaned) if m.role == "system"]
    assert sys_indices == [0], f"system not at head: {sys_indices}"
    # 2 条 system 都被前置了
    assert stats.system_promoted == 2
    print("  \u2713 test_stage3_multi_system_merged_to_head")
    assert True


def test_stage3_system_in_middle_promoted_to_head():
    """Stage 3: 中段 system 移到头"""
    msgs = [
        Message("user", "u1"),
        Message("assistant", "a1"),
        Message("system", "late sys"),
        Message("user", "u2"),
    ]
    cleaned, stats = clean_messages(msgs)
    assert cleaned[0].role == "system"
    assert cleaned[0].content == "late sys"
    assert stats.system_promoted == 1
    print("  \u2713 test_stage3_system_in_middle_promoted_to_head")
    assert True


# ============ Stage 4 ============

def test_stage4_orphan_tool_message_removed():
    """Stage 4: 没有前一个 assistant tool_calls 的 tool 消息被剥"""
    msgs = [
        Message("user", "hi"),
        Message("tool", "result", tool_call_id="x"),
    ]
    cleaned, stats = clean_messages(msgs)
    roles = [m.role for m in cleaned]
    assert "tool" not in roles, f"orphan tool survived: {cleaned}"
    assert stats.orphans_removed == 1
    print("  \u2713 test_stage4_orphan_tool_message_removed")
    assert True


def test_stage4_normal_tool_call_sequence_kept():
    """Stage 4: 正常 tool_call 序列保留"""
    tool_calls = [{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    msgs = [
        Message("user", "do it"),
        Message("assistant", "calling", tool_calls=copy.deepcopy(tool_calls)),
        Message("tool", "tool result", tool_call_id="call_1"),
        Message("assistant", "done"),
    ]
    cleaned, stats = clean_messages(msgs)
    assert len(cleaned) == 4, f"expected 4, got {len(cleaned)}: {cleaned}"
    assert stats.orphans_removed == 0
    print("  \u2713 test_stage4_normal_tool_call_sequence_kept")
    assert True


# ============ Stage 5 ============

def test_stage5_orphan_tool_calls_stripped():
    """Stage 5: assistant tool_calls 无对应 tool 响应 → 剥 tool_calls(消息保留)"""
    tool_calls = [{"id": "call_99", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    msgs = [
        Message("user", "hi"),
        Message("assistant", "calling", tool_calls=copy.deepcopy(tool_calls)),
        Message("user", "anything else"),
    ]
    cleaned, stats = clean_messages(msgs)
    asst = [m for m in cleaned if m.role == "assistant"]
    assert len(asst) == 1
    assert asst[0].tool_calls is None, f"orphan tool_calls not stripped: {asst[0].tool_calls}"
    assert stats.tool_calls_stripped >= 1
    print("  \u2713 test_stage5_orphan_tool_calls_stripped")
    assert True


# ============ Stage 6 ============

def test_stage6_first_assistant_gets_user_injected():
    """Stage 6: 首条是 assistant → 前置 user '..."""
    msgs = [
        Message("assistant", "hi there"),
        Message("user", "hi back"),
    ]
    cleaned, stats = clean_messages(msgs)
    assert cleaned[0].role == "user"
    assert cleaned[0].content == "..."
    assert cleaned[1].role == "assistant"
    assert stats.system_injected is True
    print("  \u2713 test_stage6_first_assistant_gets_user_injected")
    assert True


def test_stage6_first_user_kept():
    """Stage 6: 首条是 user → 不注入"""
    msgs = [
        Message("user", "hello"),
        Message("assistant", "hi"),
    ]
    cleaned, stats = clean_messages(msgs)
    assert cleaned[0].role == "user"
    assert cleaned[0].content == "hello"
    assert stats.system_injected is False
    print("  \u2713 test_stage6_first_user_kept")
    assert True


# ============ Stage 7 ============

def test_stage7_truncate_long():
    """Stage 7: 总 chars 超过 max → 截尾部 messages"""
    msgs = [
        Message("system", "sys"),
        Message("user", "a" * 50),
        Message("assistant", "b" * 50),
        Message("user", "c" * 50),
    ]
    cleaned, stats = clean_messages(msgs, max_total_chars=120)
    assert stats.truncated is True
    assert cleaned[0].role == "system"  # system 永远保留
    total = sum(len(m.content) for m in cleaned)
    assert total <= 120, f"total {total} > 120"
    print("  \u2713 test_stage7_truncate_long")
    assert True


# ============ 7 阶段联动 ============

def test_all_seven_stages_with_stats():
    """7 阶段全开 + stats 验证
    设计:首条是 assistant → Stage 6 注入 user '...';
    Stage 3 把 system 提到最前(此时 user 注入的 '...' 仍是 cleaned[0]);
    期望:cleaned[0]='user/...', cleaned[1]='system(merged)'
    """
    tool_calls = [{"id": "abc", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    msgs = [
        Message("assistant", "x"),               # 触发 Stage 6 user 注入
        Message("", ""),                          # Stage 1 删
        Message("user", "u1"),
        Message("user", "u2"),                    # Stage 2 合并
        Message("system", "sys1"),
        Message("assistant", "calling", tool_calls=copy.deepcopy(tool_calls)),
        Message("tool", "result", tool_call_id="abc"),   # Stage 4 保留
        Message("system", "sys2"),                # Stage 3 前置
        Message("user", "u3"),
    ]
    cleaned, stats = clean_messages(msgs, max_total_chars=10000)
    assert stats.original_count == 9
    # 顺序:Stage 6 先 inject 'user/...',Stage 3 再把 system 提到 user 之后? 不,system 提到 cleaned[0]
    # 实际执行顺序(我重排过):先 Stage 6(在 Stage 3 前)→ inject user;再 Stage 3 → system 提到最前
    # 所以 cleaned[0]=system(merged),cleaned[1]=user(...)
    assert cleaned[0].role == "system", f"expected system at head, got {cleaned[0].role}"
    assert "sys1" in cleaned[0].content and "sys2" in cleaned[0].content
    # system_injected 应该是 True(Stage 6 在 Stage 3 前执行)
    assert stats.system_injected is True
    # 找到 injected user '...'(应紧跟 system 之后)
    injected = [m for m in cleaned if m.role == "user" and m.content == "..."]
    assert len(injected) == 1
    assert cleaned.index(injected[0]) == 1
    # 没有空 content
    assert all(m.content != "" for m in cleaned)
    # 至少合并了一对 user
    assert stats.merged_pairs >= 1
    # system 全部前置(原本中段有 2 条 system)
    assert stats.system_promoted == 2
    # tool 序列完整保留
    tool_msgs = [m for m in cleaned if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id == "abc"
    assert stats.orphans_removed == 0
    print("  \u2713 test_all_seven_stages_with_stats")
    assert True


# ============ 独立辅助函数 ============

def test_merge_consecutive_role_independent():
    """merge_consecutive_role 独立使用"""
    msgs = [
        Message("user", "a"),
        Message("user", "b"),
        Message("assistant", "x"),
        Message("user", "c"),
        Message("user", "d"),
    ]
    merged = merge_consecutive_role(msgs, "user")
    assert len(merged) == 3
    assert merged[0].role == "user" and "a" in merged[0].content and "b" in merged[0].content
    assert merged[1].role == "assistant"
    assert merged[2].role == "user" and "c" in merged[2].content and "d" in merged[2].content
    print("  \u2713 test_merge_consecutive_role_independent")
    assert True


def test_strip_orphan_tool_independent():
    """strip_orphan_tool 独立使用
    1) orphan tool message(无前 assistant tool_calls)→ 整条丢
    2) orphan tool_calls(assistant 后无 tool 响应)→ 仅清空 tool_calls 字段,消息保留
    """
    tool_calls = [{"id": "t1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    msgs = [
        Message("user", "q"),
        Message("tool", "orphan result", tool_call_id="orphan"),  # orphan tool msg → 丢
        Message("assistant", "call", tool_calls=copy.deepcopy(tool_calls)),  # orphan tool_calls → 剥字段
    ]
    cleaned = strip_orphan_tool(msgs)
    # user 保留;tool 丢;assistant 保留但 tool_calls 应为 None
    assert len(cleaned) == 2, f"expected 2 (user + assistant), got {len(cleaned)}: {cleaned}"
    assert cleaned[0].role == "user"
    assert cleaned[1].role == "assistant"
    assert cleaned[1].tool_calls is None, f"orphan tool_calls not stripped: {cleaned[1].tool_calls}"
    print("  \u2713 test_strip_orphan_tool_independent")
    assert True


def test_prepend_user_if_first_assistant_independent():
    """prepend_user_if_first_assistant 独立使用"""
    msgs1 = [Message("assistant", "hi")]
    out1, injected1 = prepend_user_if_first_assistant(msgs1)
    assert injected1 is True
    assert out1[0].role == "user" and out1[0].content == "..."

    msgs2 = [Message("user", "hi")]
    out2, injected2 = prepend_user_if_first_assistant(msgs2)
    assert injected2 is False
    assert out2 == msgs2

    msgs3: list = []
    out3, injected3 = prepend_user_if_first_assistant(msgs3)
    assert injected3 is False
    assert out3 == []
    print("  \u2713 test_prepend_user_if_first_assistant_independent")
    assert True


def test_truncate_to_chars_independent():
    """truncate_to_chars 独立使用"""
    msgs = [
        Message("system", "s" * 10),
        Message("user", "u" * 30),
        Message("assistant", "a" * 30),
    ]
    out, truncated = truncate_to_chars(msgs, max_chars=50)
    assert truncated is True
    assert out[0].role == "system"
    total = sum(len(m.content) for m in out)
    assert total <= 50
    print("  \u2713 test_truncate_to_chars_independent")
    assert True


# ============ OpenAI 格式往返 ============

def test_openai_format_roundtrip():
    """to_openai_format / from_openai_format 往返一致"""
    tool_calls = [{"id": "x", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    msgs = [
        Message("system", "be kind"),
        Message("user", "hi", name="alice"),
        Message("assistant", "calling", tool_calls=copy.deepcopy(tool_calls)),
        Message("tool", "result", tool_call_id="x"),
    ]
    data = to_openai_format(msgs)
    assert isinstance(data, list)
    assert all(isinstance(d, dict) for d in data)
    assert data[0] == {"role": "system", "content": "be kind"}
    assert data[1]["name"] == "alice"
    assert data[2]["tool_calls"] == tool_calls
    # 往返
    restored = from_openai_format(data)
    assert len(restored) == len(msgs)
    assert restored[0].role == "system" and restored[0].content == "be kind"
    assert restored[1].name == "alice"
    assert restored[2].tool_calls == tool_calls
    assert restored[3].tool_call_id == "x"
    print("  \u2713 test_openai_format_roundtrip")
    assert True


# ============ 边界 ============

def test_empty_list_returns_empty():
    """空 messages → 空 + stats 0"""
    cleaned, stats = clean_messages([])
    assert cleaned == []
    assert stats.original_count == 0
    assert stats.cleaned_count == 0
    assert stats.merged_pairs == 0
    assert stats.orphans_removed == 0
    assert stats.system_injected is False
    assert stats.truncated is False
    print("  \u2713 test_empty_list_returns_empty")
    assert True


def test_single_system_preserved():
    """单 system 消息保留"""
    msgs = [Message("system", "you are helpful")]
    cleaned, stats = clean_messages(msgs)
    assert len(cleaned) == 1
    assert cleaned[0].role == "system"
    assert cleaned[0].content == "you are helpful"
    assert stats.system_promoted == 0
    print("  \u2713 test_single_system_preserved")
    assert True
