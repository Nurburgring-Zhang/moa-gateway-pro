"""重要性评分 (radius 0-1) + 上下文压缩决策 (来自 01 gateswarm-router)

真实启发式,非 mock。基于 5 维加权评分,用于 TurboQuant 压缩前的
消息重要性判定,支持 radius 模式保留最近上下文。

维度说明:
- recency   (0.30): 距 current_idx 越近越高 (线性衰减)
- tool_result (0.25): is_tool_result → +0.25
- tool_calls (0.20): has_tool_calls → +0.20
- decision (0.15): is_decision → +0.15
- system (0.10): system role → +0.10
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from typing import List


__all__ = [
    "Message",
    "ImportanceScore",
    "WEIGHTS",
    "score_message",
    "score_messages",
    "select_top_k",
    "should_compress",
    "select_within_radius",
]


# ============ 默认权重 (5 维,和=1.00) ============

WEIGHTS: dict = {
    "recency": 0.30,
    "tool_result": 0.25,
    "tool_calls": 0.20,
    "decision": 0.15,
    "system": 0.10,
}


# ============ Dataclass 定义 ============

_VALID_ROLES = frozenset({"system", "user", "assistant", "tool"})


@dataclass
class Message:
    """单条对话消息"""
    role: str
    content: str
    timestamp: float
    is_tool_result: bool = False
    has_tool_calls: bool = False
    is_decision: bool = False

    def __post_init__(self) -> None:
        if self.role not in _VALID_ROLES:
            raise ValueError(
                f"role must be one of {sorted(_VALID_ROLES)}, got {self.role!r}"
            )
        if not isinstance(self.content, str):
            raise TypeError(f"content must be str, got {type(self.content).__name__}")
        if not isinstance(self.timestamp, (int, float)):
            raise TypeError(
                f"timestamp must be numeric, got {type(self.timestamp).__name__}"
            )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ImportanceScore:
    """单条消息的重要性评分"""
    message_idx: int
    score: float          # 0-1
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ============ 辅助函数 ============

def _clip_unit(x: float) -> float:
    """clamp 到 [0, 1]"""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def _recency_factor(current_idx: int, target_idx: int) -> float:
    """recency 子分 (0-1): 距 current_idx 越近越高

    - current_idx == target_idx → 1.0
    - |distance|=1 → 0.75
    - |distance|=2 → 0.55
    - |distance|=3 → 0.40
    - 距离继续增加时按 0.40 / (1 + (d-3)*0.5) 衰减,下限 0.05
    - 负距离 (target_idx < 0) → 0
    """
    if target_idx < 0:
        return 0.0
    distance = abs(current_idx - target_idx)
    if distance == 0:
        return 1.0
    if distance == 1:
        return 0.75
    if distance == 2:
        return 0.55
    if distance == 3:
        return 0.40
    # 长尾衰减
    return max(0.05, 0.40 / (1.0 + (distance - 3) * 0.5))


# ============ 核心评分函数 ============

def score_message(
    msg: Message,
    all_messages: List[Message],
    current_idx: int,
) -> ImportanceScore:
    """对单条消息做 5 维加权评分

    Args:
        msg: 目标消息
        all_messages: 全部消息列表 (用于 timestamp 归一化等)
        current_idx: 当前关注的位置 (用于 recency)

    Returns:
        ImportanceScore (score 范围 [0, 1])
    """
    reasons: List[str] = []

    # 1) recency — 找 msg 在 all_messages 中的真实 idx
    target_idx = -1
    for i, m in enumerate(all_messages):
        if m is msg:
            target_idx = i
            break
    if target_idx < 0:
        # 单独传入,按 current_idx 处理 (recency=1.0)
        recency = 1.0 if current_idx >= 0 else 0.0
    else:
        recency = _recency_factor(current_idx, target_idx)
    if recency >= 0.5:
        reasons.append(f"recency_high(dist~{abs(current_idx - target_idx)})")
    elif recency <= 0.15:
        reasons.append("recency_low")

    # 2) tool_result
    tool_result_part = WEIGHTS["tool_result"] if msg.is_tool_result else 0.0
    if msg.is_tool_result:
        reasons.append("is_tool_result(+0.25)")

    # 3) tool_calls
    tool_calls_part = WEIGHTS["tool_calls"] if msg.has_tool_calls else 0.0
    if msg.has_tool_calls:
        reasons.append("has_tool_calls(+0.20)")

    # 4) decision
    decision_part = WEIGHTS["decision"] if msg.is_decision else 0.0
    if msg.is_decision:
        reasons.append("is_decision(+0.15)")

    # 5) system
    system_part = WEIGHTS["system"] if msg.role == "system" else 0.0
    if msg.role == "system":
        reasons.append("system_role(+0.10)")

    # 加权
    raw = (
        WEIGHTS["recency"] * recency
        + tool_result_part
        + tool_calls_part
        + decision_part
        + system_part
    )
    final = _clip_unit(raw)

    return ImportanceScore(
        message_idx=target_idx if target_idx >= 0 else current_idx,
        score=final,
        reasons=reasons,
    )


def score_messages(messages: List[Message]) -> List[ImportanceScore]:
    """批量评分 — 以列表最后一条作为 current_idx 锚点"""
    n = len(messages)
    if n == 0:
        return []
    current_idx = n - 1
    return [score_message(m, messages, current_idx) for m in messages]


# ============ 选择 / 决策函数 ============

def select_top_k(scores: List[ImportanceScore], k: int) -> List[int]:
    """返回 top-k message indices (按 score 降序)

    - k <= 0 → []
    - k >= len(scores) → 全部
    - 分数相同时,保持原顺序 (stable)
    """
    if k <= 0:
        return []
    if not scores:
        return []
    if k >= len(scores):
        return [s.message_idx for s in scores]

    # 用 enumerate 提供稳定排序的次要键
    indexed = list(enumerate(scores))
    indexed.sort(key=lambda pair: (-pair[1].score, pair[0]))
    return [pair[1].message_idx for pair in indexed[:k]]


def should_compress(
    scores: List[ImportanceScore],
    threshold: float = 0.5,
) -> bool:
    """压缩决策: 所有 score < threshold → True (可压)

    - scores 为空 → True (无可保留,默认可压)
    - 至少 1 个 score >= threshold → False
    """
    if not scores:
        return True
    return all(s.score < threshold for s in scores)


def select_within_radius(
    scores: List[ImportanceScore],
    current_idx: int,
    radius: int = 3,
) -> List[int]:
    """返回 current_idx ± radius 内的 message indices (含端点)

    边界处理: 超界 idx 自动裁剪到 [0, len(scores)-1]
    """
    if not scores or radius < 0:
        return []
    n = len(scores)
    lo = max(0, current_idx - radius)
    hi = min(n - 1, current_idx + radius)
    if lo > hi:
        return []
    return [i for i in range(lo, hi + 1)]


# ============ JSON 序列化 ============

def scores_to_json(scores: List[ImportanceScore]) -> str:
    """批量序列化为 JSON 字符串"""
    return json.dumps(
        [s.to_dict() for s in scores],
        ensure_ascii=False,
        indent=2,
    )


def scores_from_json(text: str) -> List[ImportanceScore]:
    """从 JSON 字符串还原"""
    raw = json.loads(text)
    return [
        ImportanceScore(
            message_idx=int(item["message_idx"]),
            score=float(item["score"]),
            reasons=list(item.get("reasons", [])),
        )
        for item in raw
    ]
