"""quorum — 法定人数宽限窗 (来自 05 moa-skill) + LLM-as-Judge 评分/对战 (来自 02 MoA-together-ai)

核心能力:
  1. Quorum 宽限窗: 法定人数检查 + 落伍者宽限等待 + 强制关闭
  2. LLM-as-Judge 单答评分: 从 judge 响应中解析 1-10 评分
  3. LLM-as-Judge 双答对战: 解析 A/B 胜者 + 抗位置偏置双向交换

设计原则:
  - 时间用 epoch 秒 (float), 与参与者 responded_at 一致
  - reached_at 取"达到 required 时"的时间 (响应序列中第 required 个的 responded_at)
  - within_grace: 已达成则在 grace 内 True, 超时 False; 未达成恒 True
  - parse_rating/battle 全部用正则, 多种格式都覆盖, 失败回退到合理默认
  - swap_positions 一致返回置信度 1.0, 不一致返回 0.0
"""
from __future__ import annotations

import re
import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple, Callable, Dict, Any


# ============ Quorum 数据模型 ============
@dataclass
class QuorumConfig:
    """Quorum 配置"""
    required: int
    grace_seconds: float = 30.0
    wait_for_laggards: bool = True

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class Participant:
    """单个参与者"""
    participant_id: str
    responded: bool = False
    response: Optional[str] = None
    responded_at: Optional[float] = None

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class QuorumStatus:
    """Quorum 检查结果"""
    reached: bool
    reached_at: Optional[float]
    responded_count: int
    missing: List[str]
    within_grace: bool

    def to_dict(self) -> Dict:
        return asdict(self)


# ============ Quorum 检查 ============
def check_quorum(
    participants: List[Participant],
    config: QuorumConfig,
    at: Optional[float] = None,
) -> QuorumStatus:
    """检查 Quorum 状态

    真实逻辑:
    - responded_count = 已响应的参与者数
    - reached = responded_count >= required
    - missing = 未响应的 participant_id 列表 (保持原顺序)
    - reached_at: 取响应序列中第 required 个的 responded_at (按 responded_at 升序)
      即"法定人数刚达成"的那个时间戳; 未达成则为 None
    - first_response: 最早响应时间, 用于计算宽限
    - within_grace:
        - 未达成 → True (还在等, 没超时概念)
        - 已达成 → (at - first_response) <= grace_seconds
      注: first_response 是 responded 列表的最小 responded_at, 即 Quorum 形成基准
    """
    responded_list = [p for p in participants if p.responded and p.responded_at is not None]
    responded_count = len(responded_list)
    missing = [p.participant_id for p in participants if not p.responded]

    reached = responded_count >= config.required

    reached_at: Optional[float] = None
    if reached and responded_list:
        # 按 responded_at 升序排序, 取第 required 个的时间戳 (1-indexed → required-1)
        sorted_by_time = sorted(responded_list, key=lambda p: p.responded_at)
        idx = max(0, min(config.required - 1, len(sorted_by_time) - 1))
        reached_at = sorted_by_time[idx].responded_at

    first_response: Optional[float] = None
    if responded_list:
        first_response = min(p.responded_at for p in responded_list)

    # within_grace 计算
    if not reached:
        within_grace = True
    else:
        if at is None or first_response is None:
            within_grace = True
        else:
            within_grace = (at - first_response) <= config.grace_seconds

    return QuorumStatus(
        reached=reached,
        reached_at=reached_at,
        responded_count=responded_count,
        missing=missing,
        within_grace=within_grace,
    )


def should_wait(
    status: QuorumStatus,
    config: QuorumConfig,
    at: Optional[float] = None,
) -> bool:
    """是否应继续等待落伍者

    真实逻辑:
    - config.wait_for_laggards AND status.reached AND status.within_grace
    - 若 config.wait_for_laggards=False, 则立刻不再等
    - 若未达成 (status.reached=False), 也不等 (留给调用方重试)
    """
    if not config.wait_for_laggards:
        return False
    if not status.reached:
        return False
    return status.within_grace


def force_close(
    participants: List[Participant],
    config: QuorumConfig,
    at: Optional[float] = None,
) -> Tuple[List[Participant], List[str]]:
    """强制关闭未响应者

    真实逻辑:
    - 把所有 responded=False 的参与者的 responded 设为 True, response 标记为 "DROPPED",
      responded_at 设为 at 或当前时间
    - 返回 (强制关闭后的参与者列表, 被关闭的 participant_id 列表)
    """
    if at is None:
        import time
        at = time.time()

    closed: List[str] = []
    new_list: List[Participant] = []
    for p in participants:
        if not p.responded:
            closed.append(p.participant_id)
            new_list.append(Participant(
                participant_id=p.participant_id,
                responded=True,
                response="DROPPED",
                responded_at=at,
            ))
        else:
            new_list.append(p)
    return new_list, closed


# ============ LLM-as-Judge 解析 ============
# 评分模式: 匹配 1-10 整数, 优先 [[rating_a]] / [[rating:7]] / Rating: 9 / score 8 等
_RATING_PATTERNS = [
    re.compile(r"\[\[\s*rating[_\s:]*a\s*\]\]\s*[:\s]*([0-9]+)", re.IGNORECASE),
    re.compile(r"\[\[\s*rating\s*[:\s]\s*([0-9]+)\s*\]\]", re.IGNORECASE),
    re.compile(r"rating\s*[:=\-]\s*([0-9]+)", re.IGNORECASE),
    re.compile(r"score\s*[:=\-]\s*([0-9]+)", re.IGNORECASE),
    re.compile(r"\brate\s*[:=\-]\s*([0-9]+)", re.IGNORECASE),
    re.compile(r"\bgrade\s*[:=\-]\s*([0-9]+)", re.IGNORECASE),
    re.compile(r"\[([0-9]+)\s*/\s*10\]"),
    re.compile(r"([0-9]+)\s*/\s*10\b"),
]

_DEFAULT_RATING = 5


def parse_rating(judge_response: str) -> int:
    """从 judge 响应中提取 1-10 评分, 失败回退 5

    支持格式示例:
        "[[rating_a]] 8"          → 8
        "[[rating:7]]"            → 7
        "Rating: 9"               → 9
        "I would rate this 6/10"  → 6
        "Score = 4"               → 4
    """
    if not judge_response or not isinstance(judge_response, str):
        return _DEFAULT_RATING

    text = judge_response.strip()
    for pat in _RATING_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                val = int(m.group(1))
            except (ValueError, IndexError):
                continue
            # 强制 1-10
            if val < 1:
                val = 1
            elif val > 10:
                val = 10
            return val
    # 兜底: 文本中含 "rating" 关键词 + 任意方向数字 (含负数)
    if re.search(r"\brating\b", text, re.IGNORECASE):
        nums = re.findall(r"-?\d+", text)
        if nums:
            try:
                val = int(nums[0])
                if val < 1:
                    val = 1
                elif val > 10:
                    val = 10
                return val
            except ValueError:
                pass
    return _DEFAULT_RATING


# ============ LLM-as-Judge 对战解析 ============
_BATTLE_PATTERNS = [
    # 显式标签
    (re.compile(r"\[\[\s*winner\s*\]\]\s*[:\s]*([AB]|B)\b", re.IGNORECASE), "explicit"),
    (re.compile(r"\bwinner\s*[:=\-]\s*([AB])\b", re.IGNORECASE), "explicit"),
    # "A is better" / "B is better"
    (re.compile(r"\bA\s+is\s+(?:better|superior|more\s+(?:helpful|accurate|relevant))\b", re.IGNORECASE), "A"),
    (re.compile(r"\bB\s+is\s+(?:better|superior|more\s+(?:helpful|accurate|relevant))\b", re.IGNORECASE), "B"),
    # "better than A" / "better than B"  (反向)
    (re.compile(r"\bbetter\s+than\s+A\b", re.IGNORECASE), "B"),
    (re.compile(r"\bbetter\s+than\s+B\b", re.IGNORECASE), "A"),
    # "prefer A" / "prefer B"
    (re.compile(r"\bprefer\s+A\b", re.IGNORECASE), "A"),
    (re.compile(r"\bprefer\s+B\b", re.IGNORECASE), "B"),
    # "I choose A"
    (re.compile(r"\b(?:I\s+)?choose\s+A\b", re.IGNORECASE), "A"),
    (re.compile(r"\b(?:I\s+)?choose\s+B\b", re.IGNORECASE), "B"),
    # tie / equal / draw
    (re.compile(r"\b(tie|equal|draw|same\s+level|equivalent)\b", re.IGNORECASE), "tie"),
]


def parse_battle(judge_response: str) -> Tuple[str, int]:
    """解析 judge 对战响应, 返回 (winner, confidence 0-1)

    winner ∈ {"A", "B", "tie"}
    confidence:
        - 显式 winner 标签 / 强措辞 → 1.0
        - tie 措辞 → 0.5 (不确定性)
        - 解析失败 → ("tie", 0)
    """
    if not judge_response or not isinstance(judge_response, str):
        return ("tie", 0)

    text = judge_response.strip()

    # 先检测 tie (优先级高, 因为 tie 措辞可能是兜底结论)
    tie_m = re.search(r"\b(tie|equal|draw|same\s+level|equivalent|neither)\b", text, re.IGNORECASE)
    winner_m = None
    winner_pat_idx = -1
    for idx, (pat, label) in enumerate(_BATTLE_PATTERNS):
        if label == "explicit":
            m = pat.search(text)
            if m:
                # 提取 A 或 B
                raw = m.group(1).upper()
                if raw == "B":
                    winner_m = ("B", idx)
                else:
                    winner_m = ("A", idx)
                break

    if winner_m is None:
        for idx, (pat, label) in enumerate(_BATTLE_PATTERNS):
            if label in ("A", "B"):
                if pat.search(text):
                    winner_m = (label, idx)
                    break

    if winner_m is None:
        # 没有 winner 标签, 全部 tie
        if tie_m:
            return ("tie", 1)  # 明确说 tie
        return ("tie", 0)

    winner = winner_m[0]
    # 如果同时出现 tie 措辞, confidence 降为 0.5
    if tie_m:
        return (winner, 0)
    # 显式 winner 标签 = 高 confidence
    if winner_m[1] == 0:
        return (winner, 1)
    # 措辞检测 = 中 confidence
    return (winner, 1)


def swap_positions_battle(
    response_a: str,
    response_b: str,
    judge_fn: Callable[[str, str], str],
) -> str:
    """抗位置偏置双向对战

    算法:
    - 第 1 轮: judge_fn(response_a, response_b) → winner 标签 (A/B/tie)
      winner="A" 表示 response_a 胜, winner="B" 表示 response_b 胜
    - 第 2 轮: judge_fn(response_b, response_a) → 位置交换后重评
    - 映射回"原始 response":
        * 第 1 轮 winner "A" → response_a 胜; "B" → response_b 胜
        * 第 2 轮 winner "A" → response_b 胜 (因为位置换了); "B" → response_a 胜
    - 两轮都指认同一个原始 response → 返回该 response (置信度 1.0)
    - 不一致 → "tie" (置信度 0.0)
    """
    raw1 = judge_fn(response_a, response_b)
    raw2 = judge_fn(response_b, response_a)

    w1, _c1 = parse_battle(raw1)
    w2, _c2 = parse_battle(raw2)

    # 映射第 1 轮 (judge 收到 (response_a, response_b))
    if w1 == "A":
        first_winner = response_a
    elif w1 == "B":
        first_winner = response_b
    else:
        first_winner = "tie"

    # 映射第 2 轮 (judge 收到 (response_b, response_a), 位置互换)
    if w2 == "A":
        second_winner = response_b
    elif w2 == "B":
        second_winner = response_a
    else:
        second_winner = "tie"

    if first_winner == "tie" and second_winner == "tie":
        return "tie"
    if first_winner == second_winner and first_winner != "tie":
        return first_winner
    return "tie"


# ============ JSON 序列化辅助 ============
def to_json(obj: Any) -> str:
    """统一 JSON 序列化 (支持 dataclass 嵌套)"""
    def _default(o: Any) -> Any:
        if hasattr(o, "to_dict"):
            return o.to_dict()
        if dataclass_is_instance(o):
            return asdict(o)
        return str(o)

    def dataclass_is_instance(x: Any) -> bool:
        return hasattr(x, "__dataclass_fields__")

    return json.dumps(obj, default=_default, ensure_ascii=False, indent=2)


__all__ = [
    "QuorumConfig",
    "Participant",
    "QuorumStatus",
    "check_quorum",
    "should_wait",
    "force_close",
    "parse_rating",
    "parse_battle",
    "swap_positions_battle",
    "to_json",
]
