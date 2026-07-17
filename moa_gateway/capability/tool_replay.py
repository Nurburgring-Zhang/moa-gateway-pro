"""moa_gateway.capability.tool_replay — Tool call 重放 (aggregator) + Tool choice 防循环

来源: 08 moa-server (tool_call 重放) + 防循环

提供:
- ToolCall: 单条 tool_call 数据类(id / name / arguments / source_proposal_idx)
- ReplayResult: 跨 proposals 重放后的聚合结果(tool_calls / aggregated_arguments /
  deduplicated_count / conflicts_resolved)
- extract_tool_calls: 从 proposal 文本中解析 <tool_use> 标签
- replay_tool_calls: 跨 proposals 合并 / 去重 / 冲突解决
- should_disable_tool_choice: 连续重复 tool → 建议关掉 tool_choice 防循环
- detect_tool_loop: 滑窗检测同 tool 重复
- format_tool_calls_for_aggregator: 把 ToolCall 列表格式化成 LLM aggregator prompt

与 Aggregator 集成示例:
    from moa_gateway.capability.tool_replay import (
        extract_tool_calls, replay_tool_calls,
        detect_tool_loop, should_disable_tool_choice,
        format_tool_calls_for_aggregator,
    )

    proposals = [
        '<tool_use name="search" id="c1">{"q": "x"}</tool_use>',
        '<tool_use name="search" id="c2">{"q": "x"}</tool_use>',
        '<tool_use name="search" id="c3">{"q": "x"}</tool_use>',
    ]
    result = replay_tool_calls(proposals)
    # result.deduplicated_count == 2  (3 个里 2 个被合并)

    if should_disable_tool_choice(call_count=5, last_n_calls=5, threshold=3):
        # 切到 tool_choice="none" 强制 LLM 出文本,跳出循环
        ...

    loop = detect_tool_loop(result.tool_calls, window=5)
    if loop is not None:
        logger.warning("tool loop detected: %s", loop.name)

设计约束:
- 真实正则解析:re.compile 单遍扫描 <tool_use> 块,JSON parse 失败时降级为空 dict
- 纯 stdlib + typing + dataclass,无第三方依赖
- 冲突解决:同 name 不同 args → 选出现次数最多的 args(JSON normalize 后 hash 相等
  视为同 args);并列时取首次出现
- aggregated_arguments 把所有 tool 的 args 合并成 {tool_name: args_dict} 的 dict
- 防循环算法:窗口内同一 (name, args_hash) 重复出现即视为 loop
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ToolCall",
    "ReplayResult",
    "extract_tool_calls",
    "replay_tool_calls",
    "should_disable_tool_choice",
    "detect_tool_loop",
    "format_tool_calls_for_aggregator",
    "hash_arguments",
    "DEFAULT_LOOP_THRESHOLD",
    "DEFAULT_LOOP_WINDOW",
]


# =============================================================================
# Constants
# =============================================================================


# 匹配 <tool_use name="X" id="Y">{"arg": "val"}</tool_use>
# 1) name 必填;id 可选(缺省时自动生成)
# 2) 内部 JSON body 用非贪婪 .*? 跨行匹配
_TOOL_USE_PATTERN = re.compile(
    r'<tool_use\s+name="(?P<name>[^"]+)"(?:\s+id="(?P<id>[^"]*)")?\s*>'
    r"(?P<body>.*?)"
    r"</tool_use>",
    re.DOTALL,
)

DEFAULT_LOOP_THRESHOLD: int = 3
"""连续重复 tool 多少次即建议关 tool_choice(防循环阈值)"""

DEFAULT_LOOP_WINDOW: int = 5
"""detect_tool_loop 的默认滑窗大小"""


# =============================================================================
# Helpers
# =============================================================================


def hash_arguments(arguments: Any) -> str:
    """对 arguments 做规范化后 SHA-256(用于去重 / 冲突比较)

    - 接受 dict / list / str / 任何可 JSON 序列化的对象
    - 先 json.dumps(sort_keys=True, separators=紧凑) 规范化
    - 返回 16 字符的 hex prefix

    Args:
        arguments: 任意 JSON-compatible 对象

    Returns:
        str: 16 字符 hex hash
    """
    if arguments is None:
        normalized = "null"
    else:
        try:
            normalized = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            # 不可序列化 → 用 repr 兜底
            normalized = repr(arguments)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _auto_id(idx: int) -> str:
    """缺省 id 时自动生成: call_<idx>"""
    return f"call_{idx}"


# =============================================================================
# Dataclasses
# =============================================================================


@dataclass
class ToolCall:
    """单条 tool_call

    Attributes:
        id: tool_call 唯一 id(extract 时若原文无 id 则自动 call_<idx>)
        name: function 名
        arguments: 解析后的 arguments dict(若解析失败则为 {})
        source_proposal_idx: 来自哪个 proposal(0-based);aggregator 重放时填充
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    source_proposal_idx: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """OpenAI 风格 dict: {id, type, function: {name, arguments(JSON str)}}"""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }

    def args_hash(self) -> str:
        return hash_arguments(self.arguments)


@dataclass
class ReplayResult:
    """跨 proposals 重放后的聚合结果

    Attributes:
        tool_calls: 去重 + 冲突解决后的最终 tool_call 列表
        aggregated_arguments: {tool_name: merged_args_dict} 形式的合并 args
        deduplicated_count: 被去重 / 合并掉的 tool_call 数
                            (= 输入总数 - tool_calls 数)
        conflicts_resolved: 同 name 不同 args 的冲突解决次数
                            (每个 name 只算 1 次)
    """

    tool_calls: list[ToolCall] = field(default_factory=list)
    aggregated_arguments: dict[str, Any] = field(default_factory=dict)
    deduplicated_count: int = 0
    conflicts_resolved: int = 0


# =============================================================================
# Extract
# =============================================================================


def extract_tool_calls(proposal_text: str, proposal_idx: int = 0) -> list[ToolCall]:
    """从 proposal 文本中解析 <tool_use> 标签

    支持形式:
        <tool_use name="search" id="call_1">{"q": "weather"}</tool_use>
        <tool_use name="search">{"q": "weather"}</tool_use>   # id 缺省 → 自动

    Args:
        proposal_text: 单个 proposal 的完整文本
        proposal_idx: 来自哪个 proposal(填到 ToolCall.source_proposal_idx)

    Returns:
        List[ToolCall];若没有匹配或文本为空 → []
    """
    if not proposal_text:
        return []

    out: list[ToolCall] = []
    for m in _TOOL_USE_PATTERN.finditer(proposal_text):
        name = m.group("name").strip()
        raw_id = m.group("id")
        body = m.group("body").strip()

        if not name:
            logger.warning("extract_tool_calls: skip empty name (idx=%d)", proposal_idx)
            continue

        # arguments JSON 解析;失败 → 空 dict(不抛)
        args: dict[str, Any] = {}
        if body:
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    args = parsed
                else:
                    logger.warning(
                        "extract_tool_calls: body is %s, not dict; fallback {} "
                        "(name=%s, idx=%d)",
                        type(parsed).__name__, name, proposal_idx,
                    )
            except json.JSONDecodeError as e:
                logger.warning(
                    "extract_tool_calls: JSON parse error for %s (idx=%d): %s",
                    name, proposal_idx, e,
                )

        tc_id = (raw_id or "").strip() or _auto_id(len(out))
        out.append(
            ToolCall(
                id=tc_id,
                name=name,
                arguments=args,
                source_proposal_idx=proposal_idx,
            )
        )

    return out


# =============================================================================
# Replay (cross-proposal merge / dedup / conflict resolve)
# =============================================================================


def replay_tool_calls(
    proposals: list[str],
    source_indices: list[int] | None = None,
) -> ReplayResult:
    """跨 proposals 收集 tool_calls → 去重 + 冲突解决

    规则:
    1. 对每个 proposal 调 extract_tool_calls(若 source_indices 提供则用其值)
    2. 同 name + 同 args(按 hash_arguments) → 合并到同一条 ToolCall(id 保留首次)
    3. 同 name + 不同 args(冲突)→ 选出现次数最多的 args;并列时取首次
    4. aggregated_arguments = {tool_name: resolved_args_dict}
    5. deduplicated_count = 输入总数 - 输出 tool_calls 数
    6. conflicts_resolved = 有过冲突的 name 数(每个 name 只算 1 次)

    Args:
        proposals: 各 proposal 的原始文本
        source_indices: 与 proposals 等长的可选 idx 列表;若提供则用其值,
                        否则用 enumerate 序号

    Returns:
        ReplayResult
    """
    all_calls: list[ToolCall] = []
    for i, text in enumerate(proposals):
        idx = source_indices[i] if source_indices is not None and i < len(source_indices) else i
        all_calls.extend(extract_tool_calls(text, proposal_idx=idx))

    if not all_calls:
        return ReplayResult(
            tool_calls=[],
            aggregated_arguments={},
            deduplicated_count=0,
            conflicts_resolved=0,
        )

    total_input = len(all_calls)

    # ---- 按 name 分组 ----
    by_name: dict[str, list[ToolCall]] = {}
    for tc in all_calls:
        by_name.setdefault(tc.name, []).append(tc)

    merged: list[ToolCall] = []
    conflicts_resolved = 0
    aggregated: dict[str, Any] = {}

    for name, group in by_name.items():
        # ---- 同 name 内按 args_hash 计数 ----
        hash_counter: Counter[str] = Counter()
        hash_to_first: dict[str, ToolCall] = {}
        for tc in group:
            h = tc.args_hash()
            hash_counter[h] += 1
            if h not in hash_to_first:
                hash_to_first[h] = tc

        distinct_hashes = list(hash_counter.keys())

        if len(distinct_hashes) == 1:
            # ---- 唯一 args → 取该 args 的代表(用首次出现的 id)----
            winner = hash_to_first[distinct_hashes[0]]
            # 不修改 id,代表的就是首次
            merged.append(
                ToolCall(
                    id=winner.id,
                    name=name,
                    arguments=dict(winner.arguments),
                    source_proposal_idx=winner.source_proposal_idx,
                )
            )
            aggregated[name] = dict(winner.arguments)
        else:
            # ---- 冲突:选最频繁;并列时取首次出现(hash 字典序最早)----
            conflicts_resolved += 1
            max_count = max(hash_counter.values())
            top_hashes = [h for h, c in hash_counter.items() if c == max_count]
            top_hashes.sort()  # 稳定 tiebreak
            winner_hash = top_hashes[0]
            winner = hash_to_first[winner_hash]
            merged.append(
                ToolCall(
                    id=winner.id,
                    name=name,
                    arguments=dict(winner.arguments),
                    source_proposal_idx=winner.source_proposal_idx,
                )
            )
            aggregated[name] = dict(winner.arguments)
            logger.info(
                "replay_tool_calls: resolved conflict for %s "
                "(%d distinct args, winner count=%d/%d)",
                name, len(distinct_hashes), max_count, len(group),
            )

    deduplicated_count = total_input - len(merged)
    return ReplayResult(
        tool_calls=merged,
        aggregated_arguments=aggregated,
        deduplicated_count=deduplicated_count,
        conflicts_resolved=conflicts_resolved,
    )


# =============================================================================
# Loop Detection
# =============================================================================


def should_disable_tool_choice(
    call_count: int,
    last_n_calls: int,
    threshold: int = DEFAULT_LOOP_THRESHOLD,
) -> bool:
    """判断是否应关掉 tool_choice(防循环)

    语义:
    - 当 call_count >= threshold 且 last_n_calls == call_count(说明最近 N 次全在调工具)
      → 返回 True(切到 tool_choice="none" 强制 LLM 出文本跳出循环)
    - call_count < threshold → False(还没到临界,放行)
    - last_n_calls != call_count → 说明最近 N 次并非全工具调用 → False

    设计意图:这个函数回答"刚刚是不是一直在调工具而不回答文本?"

    Args:
        call_count: 当前总 tool_call 次数
        last_n_calls: 末尾最近 N 次调用里属于 tool 的次数
                      (一般由上层从 history 数出来)
        threshold: 触发阈值,默认 3

    Returns:
        bool: True → 建议 tool_choice="none"
    """
    if call_count < threshold:
        return False
    if last_n_calls < threshold:
        return False
    # 满足:call_count >= threshold 且 last_n_calls == call_count
    # 即"从某个时点开始一直调 tool"
    return last_n_calls == call_count


def detect_tool_loop(
    tool_calls: list[ToolCall],
    window: int = DEFAULT_LOOP_WINDOW,
) -> ToolCall | None:
    """滑窗检测同一 tool 重复出现

    语义:
    - 取末尾 window 条 tool_calls
    - 若窗口内同一 (name, args_hash) 出现 >= 2 次 → 返回首次出现的 ToolCall
    - 否则 → None(没检测到 loop)

    Args:
        tool_calls: 历史 tool_call 列表(有序)
        window: 滑窗大小,默认 5

    Returns:
        被 loop 的 ToolCall(首次出现那条)或 None
    """
    if window < 2 or not tool_calls:
        return None

    tail = tool_calls[-window:]
    seen: dict[str, ToolCall] = {}
    counts: Counter[str] = Counter()
    for tc in tail:
        key = f"{tc.name}|{tc.args_hash()}"
        counts[key] += 1
        if key not in seen:
            seen[key] = tc

    for key, c in counts.items():
        if c >= 2:
            return seen[key]
    return None


# =============================================================================
# Formatting
# =============================================================================


def format_tool_calls_for_aggregator(tool_calls: list[ToolCall]) -> str:
    """把 ToolCall 列表格式化成 LLM aggregator 友好的字符串

    输出格式(每个 tool 一段):
        [1] tool=search  id=call_1  args={"q": "weather"}
        [2] tool=calc    id=call_2  args={"expr": "1+1"}

    末尾追加 1 行 summary:
        total=N  unique_args=M

    Args:
        tool_calls: ToolCall 列表

    Returns:
        格式化字符串(空列表时返回 "total=0  unique_args=0")
    """
    if not tool_calls:
        return "total=0  unique_args=0"

    lines: list[str] = []
    for i, tc in enumerate(tool_calls, start=1):
        args_json = json.dumps(tc.arguments, ensure_ascii=False, sort_keys=True)
        lines.append(
            f"[{i}] tool={tc.name}  id={tc.id}  args={args_json}"
        )

    unique_args = len({tc.args_hash() for tc in tool_calls})
    lines.append(f"total={len(tool_calls)}  unique_args={unique_args}")
    return "\n".join(lines)
