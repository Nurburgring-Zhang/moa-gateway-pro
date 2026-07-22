"""7 阶段消息清洗 (来自 01 gateswarm-router)

真实实现,非 mock。Stage 顺序基于 OpenAI / Anthropic messages 协议的最佳实践:
  1. 空 content 过滤 → 减少噪声
  2. 合并同角色连续 → 减少 token 开销(同 user 合并可避免模型把第二条当作新轮次)
  3. system 前置 → 协议硬性要求:system 必须在 messages[0]
  4. 剥离 orphan tool → tool message 必须有对应 assistant tool_calls
  5. 剥离 orphan tool_calls → assistant tool_calls 必须有对应 tool 响应
  6. 合成 user 注入 → 大多数 LLM 不接受 messages[0] 是 assistant
  7. 截断超长 → 保护 context window
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from typing import Literal

Role = Literal["system", "user", "assistant", "tool"]


# ============ DataClass ============


@dataclass
class Message:
    """OpenAI 风格消息"""

    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict] | None = None  # 仅 assistant 可能持有

    def to_dict(self) -> dict:
        d: dict = {"role": self.role, "content": self.content}
        if self.name is not None:
            d["name"] = self.name
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_calls is not None:
            d["tool_calls"] = copy.deepcopy(self.tool_calls)
        return d


@dataclass
class CleanStats:
    """清洗统计"""

    original_count: int = 0
    cleaned_count: int = 0
    merged_pairs: int = 0
    orphans_removed: int = 0
    system_injected: bool = False
    system_promoted: int = 0  # 从中段被前置的 system 数量
    tool_calls_stripped: int = 0
    truncated: bool = False
    chars_before: int = 0
    chars_after: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ============ 辅助工具 ============


def _content_len(m: Message) -> int:
    return len(m.content or "")


def _total_chars(messages: list[Message]) -> int:
    return sum(_content_len(m) for m in messages)


# ============ Stage 1: 空消息过滤 ============


def _filter_empty(messages: list[Message]) -> list[Message]:
    """去掉 content 空字符串的消息;None content 一律视为空"""
    out: list[Message] = []
    for m in messages:
        if m.content is None:
            continue
        if isinstance(m.content, str) and m.content == "":
            continue
        out.append(m)
    return out


# ============ Stage 2: 合并同角色连续 ============


def merge_consecutive_role(messages: list[Message], role: Role) -> list[Message]:
    """合并连续同 role 的消息:content 用 \\n\\n 连接;首条保留 name / tool_call_id / tool_calls"""
    out: list[Message] = []
    for m in messages:
        if m.role != role:
            out.append(m)
            continue
        if out and out[-1].role == role:
            prev = out[-1]
            sep = "\n\n"
            merged_content = (prev.content or "") + sep + (m.content or "")
            new_name = prev.name if prev.name is not None else m.name
            new_tool_call_id = (
                prev.tool_call_id if prev.tool_call_id is not None else m.tool_call_id
            )
            new_tool_calls = prev.tool_calls if prev.tool_calls is not None else m.tool_calls
            out[-1] = Message(
                role=role,
                content=merged_content,
                name=new_name,
                tool_call_id=new_tool_call_id,
                tool_calls=copy.deepcopy(new_tool_calls) if new_tool_calls is not None else None,
            )
        else:
            out.append(m)
    return out


# ============ Stage 3: system 前置 ============


def _promote_systems_to_front(messages: list[Message]) -> tuple[list[Message], int]:
    """把所有 system 消息移动到 messages[0],并合并为单条
    返回 (新列表, 被前置的 system 数量):
      - 0 条 system → 0
      - 1 条 system 但原本不在头部 → 1
      - ≥2 条 system → 总数(因为全部从非头部移到头部)
    """
    systems: list[Message] = [m for m in messages if m.role == "system"]
    others: list[Message] = [m for m in messages if m.role != "system"]
    if not systems:
        return messages, 0
    # 原本 system 已经在头部(index 0 且没有非 system 在它前面)→ 视为未移动
    first_sys_idx = next((i for i, m in enumerate(messages) if m.role == "system"), -1)
    already_at_head = first_sys_idx == 0
    promoted = 0 if already_at_head else len(systems)
    if len(systems) > 1:
        merged_system = merge_consecutive_role(systems, "system")
        return merged_system + others, promoted
    return systems + others, promoted


# ============ Stage 4: 剥离 orphan tool message ============


def _strip_orphan_tool_messages(messages: list[Message]) -> tuple[list[Message], int]:
    """tool message 必须有前一个 assistant with tool_calls(且 tool_call_id 匹配)
    无主 tool message 直接丢弃"""
    out: list[Message] = []
    removed = 0
    for _i, m in enumerate(messages):
        if m.role != "tool":
            out.append(m)
            continue
        # 向前找最近一个 assistant
        parent_assistant: Message | None = None
        for j in range(len(out) - 1, -1, -1):
            if out[j].role == "assistant":
                parent_assistant = out[j]
                break
        if parent_assistant is None or not parent_assistant.tool_calls:
            removed += 1
            continue
        # 检查 tool_call_id 匹配(若 tool_call_id 给出)
        if m.tool_call_id is not None:
            ids = {
                tc.get("id") for tc in (parent_assistant.tool_calls or []) if isinstance(tc, dict)
            }
            if m.tool_call_id not in ids:
                removed += 1
                continue
        out.append(m)
    return out, removed


# ============ Stage 5: 剥离 orphan tool_calls ============


def _strip_orphan_tool_calls(messages: list[Message]) -> tuple[list[Message], int]:
    """assistant 带 tool_calls 但后续没有对应 tool 响应 → 剥掉 tool_calls(保留消息本身)"""
    out: list[Message] = []
    stripped_total = 0
    for i, m in enumerate(messages):
        if m.role != "assistant" or not m.tool_calls:
            out.append(m)
            continue
        # 看后续是否有匹配的 tool 消息
        needed = {tc.get("id") for tc in m.tool_calls if isinstance(tc, dict) and tc.get("id")}
        if not needed:
            out.append(m)
            continue
        answered: set = set()
        for nxt in messages[i + 1 :]:
            if nxt.role == "tool" and nxt.tool_call_id in needed:
                answered.add(nxt.tool_call_id)
                if answered == needed:
                    break
        if answered != needed:
            new_m = Message(
                role=m.role,
                content=m.content,
                name=m.name,
                tool_call_id=m.tool_call_id,
                tool_calls=None,
            )
            stripped_total += len(needed - answered)
            out.append(new_m)
        else:
            out.append(m)
    return out, stripped_total


# ============ Stage 4+5 合并入口 ============


def strip_orphan_tool(messages: list[Message]) -> list[Message]:
    """先剥 orphan tool message,再剥 orphan tool_calls"""
    after_tool, _ = _strip_orphan_tool_messages(messages)
    after_calls, _ = _strip_orphan_tool_calls(after_tool)
    return after_calls


# ============ Stage 6: 合成 user 注入 ============


def prepend_user_if_first_assistant(messages: list[Message]) -> tuple[list[Message], bool]:
    """若 messages[0] 是 assistant,前置一个空 user '...';否则不动"""
    if not messages:
        return messages, False
    if messages[0].role == "assistant":
        prepend = Message(role="user", content="...")
        return [prepend] + messages, True
    return messages, False


# ============ Stage 7: 截断超长 ============


def truncate_to_chars(messages: list[Message], max_chars: int) -> tuple[list[Message], bool]:
    """从尾部开始累加,超 max_chars 的消息被截掉;system 永不丢"""
    if max_chars <= 0:
        return [], True
    systems = [m for m in messages if m.role == "system"]
    non_system = [m for m in messages if m.role != "system"]
    sys_chars = _total_chars(systems)
    remaining = max_chars - sys_chars
    if remaining < 0:
        # system 本身超限 → 仍保留,但不丢
        return messages, True
    kept: list[Message] = []
    used = 0
    truncated = False
    for m in non_system:
        c = _content_len(m)
        if used + c > remaining:
            truncated = True
            break
        kept.append(m)
        used += c
    return systems + kept, truncated


# ============ 7 阶段主入口 ============


def clean_messages(
    messages: list[Message],
    max_total_chars: int = 100000,
) -> tuple[list[Message], CleanStats]:
    """7 阶段消息清洗;返回 (cleaned, stats)"""
    stats = CleanStats()
    stats.original_count = len(messages)
    stats.chars_before = _total_chars(messages)

    # Stage 1: 空消息
    msgs = _filter_empty(messages)

    # Stage 2: 合并同角色(user / assistant / system / tool)连续
    before_pairs = sum(1 for i in range(1, len(msgs)) if msgs[i].role == msgs[i - 1].role)
    msgs = merge_consecutive_role(msgs, "user")
    msgs = merge_consecutive_role(msgs, "assistant")
    msgs = merge_consecutive_role(msgs, "system")
    msgs = merge_consecutive_role(msgs, "tool")
    after_pairs = sum(1 for i in range(1, len(msgs)) if msgs[i].role == msgs[i - 1].role)
    stats.merged_pairs = before_pairs - after_pairs

    # Stage 6: 首条 assistant 注入 user(必须在 Stage 3 之前,
    # 因为 Stage 3 之后 messages[0] 永远是 system,Stage 6 永远不触发)
    msgs, injected = prepend_user_if_first_assistant(msgs)
    stats.system_injected = injected

    # Stage 3: system 前置
    msgs, promoted = _promote_systems_to_front(msgs)
    stats.system_promoted = promoted

    # Stage 4: 剥 orphan tool message
    msgs, removed_tool = _strip_orphan_tool_messages(msgs)
    # Stage 5: 剥 orphan tool_calls
    msgs, stripped_calls = _strip_orphan_tool_calls(msgs)
    stats.orphans_removed = removed_tool
    stats.tool_calls_stripped = stripped_calls

    # Stage 7: 截断超长
    msgs, truncated = truncate_to_chars(msgs, max_total_chars)
    stats.truncated = truncated

    stats.cleaned_count = len(msgs)
    stats.chars_after = _total_chars(msgs)
    return msgs, stats


# ============ OpenAI 兼容转换 ============


def to_openai_format(messages: list[Message]) -> list[dict]:
    """转 OpenAI messages JSON 格式"""
    return [m.to_dict() for m in messages]


def from_openai_format(data: list[dict]) -> list[Message]:
    """反向解析;role 非法值抛 ValueError"""
    allowed = {"system", "user", "assistant", "tool"}
    out: list[Message] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError(f"message must be dict, got {type(item).__name__}")
        role = item.get("role")
        if role not in allowed:
            raise ValueError(f"invalid role: {role!r}")
        content = item.get("content", "")
        if content is None:
            content = ""
        out.append(
            Message(
                role=role,  # type: ignore[arg-type]
                content=content if isinstance(content, str) else str(content),
                name=item.get("name"),
                tool_call_id=item.get("tool_call_id"),
                tool_calls=copy.deepcopy(item.get("tool_calls"))
                if item.get("tool_calls") is not None
                else None,
            )
        )
    return out


# ============ 自检 ============

if __name__ == "__main__":
    sample = [
        Message("assistant", "hi"),
        Message("user", "hello"),
        Message("user", "world"),
        Message("system", "be kind"),
        Message("tool", "result", tool_call_id="x"),
        Message(
            "assistant",
            "done",
            tool_calls=[
                {"id": "x", "type": "function", "function": {"name": "f", "arguments": "{}"}}
            ],
        ),
        Message(""),
    ]
    cleaned, st = clean_messages(sample)
    print("cleaned:", cleaned)
    print("stats:", st)
