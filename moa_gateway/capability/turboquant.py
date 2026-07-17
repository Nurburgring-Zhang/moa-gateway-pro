"""TurboQuant 压缩 (Q8/Q4/Q2/Q1/Q0) + 5 级量化 + 60 msg HARD CAP + 30 msg PRESERVE
(来自 01 gateswarm-router)

真实量化压缩,非 mock。5 个 bit-width 等级 (Q0..Q8),基于 content hash
做按字符截断的"指纹"压缩,不可逆但保身份。

- Q0 (1 bit)  : content 哈希第 1 字符 (信息最少)
- Q1 (2 bit)  : 前 2 字符
- Q2 (3 bit)  : 前 3 字符
- Q4 (5 bit)  : 前 5 字符 (默认)
- Q8 (8 bit+) : 完整内容 (不压)

HARD CAP / PRESERVE:
- hard_cap = 60 : 超过 60 条触发压缩
- preserve = 30 : 压缩后保留前 30 条原样,其余用 level 量化

结构不变量:
- system message 始终在头
- user/assistant 交替顺序保留
- 末尾 finish marker 不被压缩
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum

__all__ = [
    "QuantLevel",
    "Message",
    "TurboQuantConfig",
    "LEVEL_CHARS",
    "compress_message",
    "should_compress",
    "apply_turboquant",
    "extract_system_messages",
    "is_finish_marker",
    "messages_to_json",
    "messages_from_json",
]


# ============ QuantLevel 枚举 ============

class QuantLevel(Enum):
    """5 级量化等级 — bit-width 命名但实现为 content hash 前 N 字符

    Q0 = 1 bit-equivalent (1 char)
    Q1 = 2 bit-equivalent (2 chars)
    Q2 = 3 bit-equivalent (3 chars)
    Q4 = 5 bit-equivalent (5 chars)  <- 默认
    Q8 = 8+ bit-equivalent (full content, no compression)
    """
    Q0 = ("q0", 1)
    Q1 = ("q1", 2)
    Q2 = ("q2", 3)
    Q4 = ("q4", 5)
    Q8 = ("q8", 0)  # 0 = 不截断 (full)

    def __init__(self, tag: str, char_count: int) -> None:
        self.tag = tag
        self.char_count = char_count

    @property
    def is_lossless(self) -> bool:
        """是否无损 (Q8 即无损)"""
        return self.char_count == 0

    @property
    def bit_equivalent(self) -> int:
        """等价 bit 数 (按 base-16 字符数 × 4 估算)"""
        if self.char_count == 0:
            return 8
        return self.char_count * 4


# 等级 → 截断字符数 映射 (Q8 = 0 表示完整)
LEVEL_CHARS: dict = {
    QuantLevel.Q0: 1,
    QuantLevel.Q1: 2,
    QuantLevel.Q2: 3,
    QuantLevel.Q4: 5,
    QuantLevel.Q8: 0,
}


# ============ Message & Config ============

_VALID_ROLES = frozenset({"system", "user", "assistant", "tool"})


@dataclass
class Message:
    """单条对话消息 (与 importance.py 同形,字段更精简)"""
    role: str
    content: str
    timestamp: float

    def __post_init__(self) -> None:
        if self.role not in _VALID_ROLES:
            raise ValueError(
                f"role must be one of {sorted(_VALID_ROLES)}, got {self.role!r}"
            )
        if not isinstance(self.content, str):
            raise TypeError(
                f"content must be str, got {type(self.content).__name__}"
            )
        if not isinstance(self.timestamp, (int, float)):
            raise TypeError(
                f"timestamp must be numeric, got {type(self.timestamp).__name__}"
            )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TurboQuantConfig:
    """TurboQuant 压缩配置

    - hard_cap : 触发压缩的条数阈值 (默认 60)
    - preserve : 压缩后保留前 N 条原样 (默认 30)
    - level    : 量化等级 (默认 Q4)
    """
    hard_cap: int = 60
    preserve: int = 30
    level: QuantLevel = QuantLevel.Q4

    def __post_init__(self) -> None:
        if self.hard_cap <= 0:
            raise ValueError(
                f"hard_cap must be > 0, got {self.hard_cap}"
            )
        if self.preserve < 0:
            raise ValueError(
                f"preserve must be >= 0, got {self.preserve}"
            )
        if self.preserve > self.hard_cap:
            raise ValueError(
                f"preserve ({self.preserve}) must be <= hard_cap ({self.hard_cap})"
            )
        if not isinstance(self.level, QuantLevel):
            raise TypeError(
                f"level must be QuantLevel, got {type(self.level).__name__}"
            )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["level"] = self.level.name
        return d


# ============ 压缩函数 ============

_FINISH_MARKERS = frozenset({
    "<FINISH>",
    "<END>",
    "<DONE>",
    "<FINISH_TURN>",
    "<TURN_END>",
})


def _content_fingerprint(content: str, char_count: int) -> str:
    """content → 稳定指纹 (sha256 hex 前 char_count 字符)

    Args:
        content: 原始内容
        char_count: 取前几位;0 表示不截断

    Returns:
        hex 字符串;若 char_count==0 则返回原 content
    """
    if char_count == 0:
        return content
    if not content:
        return ""
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return h[:char_count]


def compress_message(msg: Message, level: QuantLevel) -> Message:
    """对单条消息做 TurboQuant 压缩

    - Q8 (char_count=0) : 完整保留,只把 content 加 [Q8] 前缀标记
    - Q0..Q4           : content 替换为 hex 指纹

    Args:
        msg: 原始 Message
        level: 量化等级

    Returns:
        新的 Message (原对象不被修改)
    """
    if not isinstance(level, QuantLevel):
        raise TypeError(
            f"level must be QuantLevel, got {type(level).__name__}"
        )

    char_count = LEVEL_CHARS[level]
    if char_count == 0:
        # Q8: 不压缩,只加无损标记前缀 (用 system role 之外的安全形式)
        new_content = f"[Q8] {msg.content}"
    else:
        new_content = f"[{level.tag}:{_content_fingerprint(msg.content, char_count)}]"

    return Message(
        role=msg.role,
        content=new_content,
        timestamp=msg.timestamp,
    )


def is_finish_marker(msg: Message) -> bool:
    """判断是否为 finish marker (任意 role, content 是已知结束标签)"""
    if not msg.content:
        return False
    stripped = msg.content.strip()
    return stripped in _FINISH_MARKERS


def should_compress(
    messages: list[Message],
    config: TurboQuantConfig,
) -> bool:
    """压缩决策: len(messages) > hard_cap → True

    - messages 为空 → False
    - len <= hard_cap → False
    - len > hard_cap → True
    """
    if not isinstance(config, TurboQuantConfig):
        raise TypeError(
            f"config must be TurboQuantConfig, got {type(config).__name__}"
        )
    if not messages:
        return False
    return len(messages) > config.hard_cap


def extract_system_messages(messages: list[Message]) -> list[Message]:
    """抽取列表头部的 system messages (连续 0 条或多条)"""
    out: list[Message] = []
    for m in messages:
        if m.role == "system":
            out.append(m)
        else:
            break
    return out


def apply_turboquant(
    messages: list[Message],
    config: TurboQuantConfig,
) -> list[Message]:
    """应用 TurboQuant 压缩

    行为:
    1. 抽取头部 system messages,原样保留在最前
    2. 抽出末尾 finish marker (如有) 保留在最后
    3. 中间部分:
       - 不应压缩: 原样保留
       - 应压缩  : 前 config.preserve 条原样保留,其余用 level 量化
    4. 重组: [system...] + [preserved...] + [quantized...] + [finish_marker]

    结构不变量:
    - system message 始终在头
    - user/assistant 交替顺序保留 (对原列表中连续 user/assistant 而言)
    - 末尾 finish marker 不被压缩

    Args:
        messages: 原始消息列表
        config: TurboQuantConfig

    Returns:
        新列表 (原 messages 不被修改)
    """
    if not isinstance(config, TurboQuantConfig):
        raise TypeError(
            f"config must be TurboQuantConfig, got {type(config).__name__}"
        )
    if not messages:
        return []

    # 1) 抽取头部 system
    system_msgs = extract_system_messages(messages)
    body = messages[len(system_msgs):]

    # 2) 抽出末尾 finish marker
    finish_msg: Message | None = None
    if body and is_finish_marker(body[-1]):
        finish_msg = body[-1]
        body = body[:-1]

    # 3) 决策: 是否需要压缩 body
    if not should_compress(body, config) and finish_msg is None:
        # body 自身不需要压,且没有 finish marker — 但要保留 system 在头
        return list(system_msgs) + list(body)
    if not should_compress(body, config):
        # 不需要压 body,但要保留 finish marker 在尾
        return list(system_msgs) + list(body) + [finish_msg]  # type: ignore[list-item]

    # 4) 需要压缩 — body 前 preserve 条原样,其余量化
    preserve_n = min(config.preserve, len(body))
    preserved = list(body[:preserve_n])
    to_quantize = body[preserve_n:]

    quantized = [compress_message(m, config.level) for m in to_quantize]

    result: list[Message] = []
    result.extend(system_msgs)
    result.extend(preserved)
    result.extend(quantized)
    if finish_msg is not None:
        result.append(finish_msg)
    return result


# ============ JSON 序列化 ============

def messages_to_json(messages: list[Message]) -> str:
    """Message 列表 → JSON 字符串"""
    return json.dumps(
        [m.to_dict() for m in messages],
        ensure_ascii=False,
        indent=2,
    )


def messages_from_json(text: str) -> list[Message]:
    """JSON 字符串 → Message 列表"""
    raw = json.loads(text)
    return [
        Message(
            role=str(item["role"]),
            content=str(item["content"]),
            timestamp=float(item["timestamp"]),
        )
        for item in raw
    ]


def config_to_json(config: TurboQuantConfig) -> str:
    """TurboQuantConfig → JSON 字符串"""
    return json.dumps(config.to_dict(), ensure_ascii=False, indent=2)


def config_from_json(text: str) -> TurboQuantConfig:
    """JSON 字符串 → TurboQuantConfig"""
    raw = json.loads(text)
    level_name = raw.get("level", "Q4")
    return TurboQuantConfig(
        hard_cap=int(raw["hard_cap"]),
        preserve=int(raw["preserve"]),
        level=QuantLevel[level_name] if isinstance(level_name, str) else level_name,
    )
