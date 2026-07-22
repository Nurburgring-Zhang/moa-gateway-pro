"""moa_gateway.capability.model_entry — Provider 状态(12 字段 ModelEntry)

来源: 01 gateswarm-router (12 字段 ModelEntry)

提供:
- Modality enum: TEXT / IMAGE / AUDIO / VIDEO / EMBEDDING
- ModelEntry dataclass: 12 字段完整模型描述(provider/family/context/cost/capability)
- CapabilityCheck dataclass: 从 ModelEntry 派生的能力查询结果
- get_capability: 从 ModelEntry 提取 CapabilityCheck
- filter_by_capability: 按 capability 字段过滤
- filter_by_modality: 按模态过滤
- filter_by_min_context: 按最小 context 过滤
- sort_by_cost / sort_by_context: 排序工具
- find_within_budget: 预算匹配(input/output cost 双向)
- multimodal_score: 多模态匹配评分(0-1)
- to_json / from_json: JSON 序列化

设计:
- 12 字段 ModelEntry 是 Provider 状态层的最小数据单元
- 路由层(gateswarm-router)用 ModelEntry 列表做 capability-aware 选模
- 字段定义采用公开行业数据,默认值保守(全部 False / 0.0)
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "Modality",
    "ModelEntry",
    "CapabilityCheck",
    "get_capability",
    "filter_by_capability",
    "filter_by_modality",
    "filter_by_min_context",
    "sort_by_cost",
    "sort_by_context",
    "find_within_budget",
    "multimodal_score",
    "to_json",
    "from_json",
]


class Modality(str, Enum):
    """模态类型

    str-mixin 让 Modality.TEXT == "TEXT" 序列化时直接是字符串。
    """

    TEXT = "TEXT"
    IMAGE = "IMAGE"
    AUDIO = "AUDIO"
    VIDEO = "VIDEO"
    EMBEDDING = "EMBEDDING"


@dataclass
class ModelEntry:
    """12 字段 ModelEntry — Provider 状态层最小数据单元

    字段语义:
    - model_id: 唯一 id (如 "gpt-4o" / "deepseek-v3")
    - provider: 提供方 (openai / anthropic / deepseek ...)
    - family: 家族 (gpt-4 / claude-3 / deepseek ...)
    - context_window: 总 context 大小 (tokens)
    - max_output: 单次输出上限 (tokens)
    - modalities: 支持的模态列表
    - supports_tools: 是否支持 function/tool call
    - supports_vision: 是否支持图像理解
    - supports_reasoning: 是否支持推理 (chain-of-thought / o1-style)
    - supports_streaming: 是否支持 SSE 流式输出
    - input_cost_per_1k: 输入 USD / 1k tokens
    - output_cost_per_1k: 输出 USD / 1k tokens
    """

    model_id: str
    provider: str
    family: str
    context_window: int
    max_output: int
    modalities: list[Modality]
    supports_tools: bool
    supports_vision: bool
    supports_reasoning: bool
    supports_streaming: bool
    input_cost_per_1k: float
    output_cost_per_1k: float

    def __post_init__(self) -> None:
        # 基础校验
        if not self.model_id or not isinstance(self.model_id, str):
            raise ValueError(f"model_id must be non-empty str, got {self.model_id!r}")
        if not isinstance(self.provider, str):
            raise ValueError(f"provider must be str, got {type(self.provider).__name__}")
        if not isinstance(self.family, str):
            raise ValueError(f"family must be str, got {type(self.family).__name__}")
        if not isinstance(self.context_window, int) or self.context_window < 0:
            raise ValueError(f"context_window must be int >= 0, got {self.context_window!r}")
        if not isinstance(self.max_output, int) or self.max_output < 0:
            raise ValueError(f"max_output must be int >= 0, got {self.max_output!r}")
        if self.max_output > self.context_window:
            raise ValueError(
                f"max_output ({self.max_output}) cannot exceed context_window "
                f"({self.context_window})"
            )
        if not isinstance(self.modalities, list):
            raise ValueError(f"modalities must be list, got {type(self.modalities).__name__}")
        for m in self.modalities:
            if not isinstance(m, Modality):
                raise ValueError(f"modalities entries must be Modality, got {type(m).__name__}")
        for bname in (
            "supports_tools",
            "supports_vision",
            "supports_reasoning",
            "supports_streaming",
        ):
            if not isinstance(getattr(self, bname), bool):
                raise ValueError(f"{bname} must be bool")
        if self.input_cost_per_1k < 0.0:
            raise ValueError(f"input_cost_per_1k must be >= 0, got {self.input_cost_per_1k}")
        if self.output_cost_per_1k < 0.0:
            raise ValueError(f"output_cost_per_1k must be >= 0, got {self.output_cost_per_1k}")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["modalities"] = [m.value if isinstance(m, Modality) else str(m) for m in self.modalities]
        return d


@dataclass
class CapabilityCheck:
    """从 ModelEntry 派生的能力查询结果

    - 5 bool 字段对应 supports_* 直通
    - compatible_modalities: 字符串列表(便于跨层传递,无需 Modality 依赖)
    """

    supports_text: bool
    supports_vision: bool
    supports_tools: bool
    supports_streaming: bool
    supports_reasoning: bool
    compatible_modalities: list[str]


# =============================================================================
# 能力提取
# =============================================================================


def get_capability(entry: ModelEntry) -> CapabilityCheck:
    """从 ModelEntry 提取 CapabilityCheck

    - supports_text: TEXT 在 modalities 列表中
    - supports_vision: IMAGE 在 modalities 列表中(且 supports_vision 标志位 True)
    - supports_tools / streaming / reasoning: 直通对应 bool 字段
    - compatible_modalities: modalities 列表的字符串值
    """
    modalities_str = [m.value if isinstance(m, Modality) else str(m) for m in entry.modalities]
    return CapabilityCheck(
        supports_text=Modality.TEXT in entry.modalities,
        supports_vision=entry.supports_vision and Modality.IMAGE in entry.modalities,
        supports_tools=entry.supports_tools,
        supports_streaming=entry.supports_streaming,
        supports_reasoning=entry.supports_reasoning,
        compatible_modalities=modalities_str,
    )


# =============================================================================
# 过滤
# =============================================================================

# 合法 capability 字段名(用于 filter_by_capability)
_CAPABILITY_FIELDS = {
    "supports_tools",
    "supports_vision",
    "supports_reasoning",
    "supports_streaming",
}


def filter_by_capability(
    entries: list[ModelEntry],
    capability: str,
    value: bool = True,
) -> list[ModelEntry]:
    """按 capability 字段过滤

    Args:
        entries: ModelEntry 列表
        capability: 字段名, 必须是 supports_tools / supports_vision /
            supports_reasoning / supports_streaming 之一
        value: 目标 bool 值(默认 True = 只要支持的)

    Returns:
        过滤后保留原顺序的列表
    """
    if not entries:
        return []
    if capability not in _CAPABILITY_FIELDS:
        raise ValueError(
            f"capability must be one of {sorted(_CAPABILITY_FIELDS)}, got {capability!r}"
        )
    return [e for e in entries if getattr(e, capability) == value]


def filter_by_modality(
    entries: list[ModelEntry],
    modality: Modality,
) -> list[ModelEntry]:
    """按模态过滤 — 保留含目标 modality 的 entry"""
    if not entries:
        return []
    if not isinstance(modality, Modality):
        raise ValueError(f"modality must be Modality enum, got {type(modality).__name__}")
    return [e for e in entries if modality in e.modalities]


def filter_by_min_context(
    entries: list[ModelEntry],
    min_context: int,
) -> list[ModelEntry]:
    """按最小 context window 过滤(>= min_context)"""
    if not entries:
        return []
    if min_context < 0:
        raise ValueError(f"min_context must be >= 0, got {min_context}")
    return [e for e in entries if e.context_window >= min_context]


# =============================================================================
# 排序
# =============================================================================


def sort_by_cost(
    entries: list[ModelEntry],
    ascending: bool = True,
    cost_field: str = "input_cost_per_1k",
) -> list[ModelEntry]:
    """按 cost 排序

    Args:
        entries: ModelEntry 列表
        ascending: True 升序(便宜在前), False 降序
        cost_field: "input_cost_per_1k" 或 "output_cost_per_1k"
    """
    if not entries:
        return []
    if cost_field not in ("input_cost_per_1k", "output_cost_per_1k"):
        raise ValueError(
            f"cost_field must be input_cost_per_1k or output_cost_per_1k, got {cost_field!r}"
        )
    return sorted(entries, key=lambda e: getattr(e, cost_field), reverse=not ascending)


def sort_by_context(
    entries: list[ModelEntry],
    descending: bool = True,
) -> list[ModelEntry]:
    """按 context_window 排序

    Args:
        entries: ModelEntry 列表
        descending: True 降序(大 context 在前,默认), False 升序
    """
    if not entries:
        return []
    return sorted(entries, key=lambda e: e.context_window, reverse=descending)


# =============================================================================
# 预算匹配
# =============================================================================


def find_within_budget(
    entries: list[ModelEntry],
    max_input_cost: float | None = None,
    max_output_cost: float | None = None,
) -> list[ModelEntry]:
    """预算匹配 — 找出 input/output cost 都在阈值下的 entry

    Args:
        entries: ModelEntry 列表
        max_input_cost: input 成本上限 (USD/1k), None = 不限
        max_output_cost: output 成本上限 (USD/1k), None = 不限

    Returns:
        同时满足两条件的 entry 列表(原顺序)
        - 全 None → 返回原 entries 副本
    """
    if not entries:
        return []
    result: list[ModelEntry] = []
    for e in entries:
        if max_input_cost is not None and e.input_cost_per_1k > max_input_cost:
            continue
        if max_output_cost is not None and e.output_cost_per_1k > max_output_cost:
            continue
        result.append(e)
    return result


# =============================================================================
# 多模态评分
# =============================================================================


def multimodal_score(
    entry: ModelEntry,
    query_modalities: list[Modality],
) -> float:
    """多模态匹配评分 — 0-1,表示 entry 覆盖 query_modalities 的比例

    算法:
        matched = |entry.modalities ∩ query|
        score = matched / |query|
    边界:
        - query_modalities 为空 → 0.0(无需求 = 不匹配,避免平凡 1.0)
        - entry 没有任何匹配 → 0.0
    """
    if not query_modalities:
        return 0.0
    set(query_modalities)
    matched = sum(1 for m in query_modalities if m in entry.modalities)
    return matched / len(query_modalities)


# =============================================================================
# JSON 序列化
# =============================================================================


def to_json(entries: list[ModelEntry], indent: int | None = 2) -> str:
    """ModelEntry 列表 → JSON 字符串

    Modality 自动展开为字符串值。
    """
    payload = [e.to_dict() for e in entries]
    return json.dumps(payload, indent=indent, ensure_ascii=False)


def from_json(data: str) -> list[ModelEntry]:
    """JSON 字符串 → ModelEntry 列表

    modalities 字段从字符串列表还原为 Modality 枚举。
    """
    raw = json.loads(data)
    if not isinstance(raw, list):
        raise ValueError(f"JSON payload must be a list, got {type(raw).__name__}")
    out: list[ModelEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"each entry must be a dict, got {type(item).__name__}")
        modalities_raw = item.get("modalities", [])
        modalities = []
        for m in modalities_raw:
            if isinstance(m, Modality):
                modalities.append(m)
            elif isinstance(m, str):
                modalities.append(Modality(m))
            else:
                raise ValueError(f"modality entry must be str or Modality, got {type(m).__name__}")
        item = dict(item)
        item["modalities"] = modalities
        out.append(ModelEntry(**item))
    return out
