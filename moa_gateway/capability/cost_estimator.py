"""cost_estimator — MoA dry-run 成本估算(防 dry-run 少报 subagent+api fallback bug)

来源: 05 moa-skill (dry-run 成本估算)

真实实现:
- 精确按 channel 价格表 + token 数计算 USD
- 考虑 reference 数量 + aggregator 链(参考输出→聚合输入→聚合输出)
- fallback multiplier 1.5x(主通道失败重试到次通道)
- reliability 加权 confidence(高可靠 → 高 confidence)
- preset 库(standard / fast / premium)可一键 dry-run

非 mock,所有计算为确定性的数学公式。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict


# ============ Dataclasses ============

@dataclass
class Channel:
    """调用通道(单一 LLM API endpoint)"""
    name: str
    cost_per_1k_input: float
    cost_per_1k_output: float
    avg_latency_ms: float
    reliability: float  # 0-1

    def __post_init__(self) -> None:
        if not (0.0 <= self.reliability <= 1.0):
            raise ValueError(f"reliability must be 0-1, got {self.reliability}")
        if self.cost_per_1k_input < 0 or self.cost_per_1k_output < 0:
            raise ValueError("cost must be non-negative")


@dataclass
class CostEstimate:
    """单次 MoA 调用的成本估算"""
    total_cost_usd: float
    breakdown: Dict[str, float]  # "deepseek-v3": 0.001, "aggregator": 0.002
    multiplier: float  # cost multiplier(fallback / retry)
    confidence: float  # 0-1
    notes: List[str]  # "8 references × deepseek-v3" 等

    def to_dict(self) -> Dict:
        return {
            "total_cost_usd": round(self.total_cost_usd, 6),
            "breakdown": {k: round(v, 6) for k, v in self.breakdown.items()},
            "multiplier": round(self.multiplier, 4),
            "confidence": round(self.confidence, 4),
            "notes": list(self.notes),
        }


# ============ 真实预设 channel(测试用) ============

DEEPSEEK = Channel("deepseek-v3", 0.0005, 0.001, 800, 0.95)
GLM = Channel("glm-4-plus", 0.0007, 0.0007, 1200, 0.92)
MOONSHOT = Channel("moonshot-v1-8k", 0.001, 0.002, 1000, 0.90)
QWEN = Channel("qwen-plus", 0.0004, 0.0012, 900, 0.93)
GPT_MINI = Channel("gpt-4o-mini", 0.00015, 0.0006, 600, 0.97)
CLAUDE_HAIKU = Channel("claude-haiku", 0.0008, 0.004, 700, 0.94)

# 名称 → channel lookup
CHANNEL_REGISTRY: Dict[str, Channel] = {
    c.name: c for c in (DEEPSEEK, GLM, MOONSHOT, QWEN, GPT_MINI, CLAUDE_HAIKU)
}

# 预置 preset(用于 dry_run_preset)
PRESETS: Dict[str, Dict] = {
    "fast": {
        "name": "fast",
        "reference_models": ["gpt-4o-mini"],
        "aggregator": "gpt-4o-mini",
        "reference_count": 2,
        "description": "fast preset — 单 ref 廉价模型,适合低复杂度任务",
    },
    "balanced": {
        "name": "balanced",
        "reference_models": ["deepseek-v3", "glm-4-plus"],
        "aggregator": "deepseek-v3",
        "reference_count": 4,
        "description": "balanced preset — 2 廉价 ref × 2 副本,deepseek 聚合",
    },
    "premium": {
        "name": "premium",
        "reference_models": ["claude-haiku", "moonshot-v1-8k", "glm-4-plus"],
        "aggregator": "claude-haiku",
        "reference_count": 6,
        "description": "premium preset — 3 多样 ref × 2 副本,claude 聚合",
    },
}


# ============ Core API ============

def estimate_moa_cost(
    input_tokens: int,
    output_tokens: int,
    channels: List[Channel],
    preset_name: str = "balanced",
    include_fallback: bool = True,
    retry_factor: float = 1.0,
) -> CostEstimate:
    """估算 MoA 调用的成本。

    真实逻辑:
    - 每个 channel 担任 1 个 reference(若需 N 副本,调用方传 N 个相同 Channel)
    - reference cost = sum((input_tokens/1000 * cost_in) + (output_tokens/1000 * cost_out)) over channels
    - aggregator:channels[0] 同时担任 aggregator
    - aggregator input = sum of all reference outputs(全部送入聚合器)
    - aggregator output = output_tokens
    - multiplier = 1.5 if include_fallback else 1.0;再乘 retry_factor
    - confidence = 0.6 * min_reliability + 0.4 * avg_reliability;fallback 时再 ×0.9
    """
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("tokens must be non-negative")
    if not channels:
        raise ValueError("channels list must not be empty")

    breakdown: Dict[str, float] = {}
    notes: List[str] = []

    # --- Reference cost:每个 channel 跑一次 reference ---
    for ch in channels:
        in_cost = (input_tokens / 1000.0) * ch.cost_per_1k_input
        out_cost = (output_tokens / 1000.0) * ch.cost_per_1k_output
        ch_cost = in_cost + out_cost
        key = f"ref:{ch.name}"
        breakdown[key] = breakdown.get(key, 0.0) + ch_cost
        notes.append(
            f"ref {ch.name}: {input_tokens}in + {output_tokens}out = ${ch_cost:.6f}"
        )

    # --- Aggregator cost:取第一个 channel 作为 aggregator ---
    agg_channel = channels[0]
    total_ref_output_tokens = output_tokens * len(channels)
    agg_in_cost = (total_ref_output_tokens / 1000.0) * agg_channel.cost_per_1k_input
    agg_out_cost = (output_tokens / 1000.0) * agg_channel.cost_per_1k_output
    agg_cost = agg_in_cost + agg_out_cost
    breakdown[f"aggregator:{agg_channel.name}"] = agg_cost
    notes.append(
        f"aggregator {agg_channel.name}: "
        f"{total_ref_output_tokens}in (ref outputs) + {output_tokens}out = ${agg_cost:.6f}"
    )

    raw_total = sum(breakdown.values())

    # --- Multiplier(fallback + retry)---
    multiplier = 1.0
    if include_fallback:
        multiplier *= 1.5
        notes.append("fallback multiplier: 1.5x (primary→secondary retry overhead)")
    if retry_factor > 1.0:
        multiplier *= retry_factor
        notes.append(f"retry multiplier: {retry_factor:.2f}x")

    total_cost = raw_total * multiplier

    # --- Confidence:基于最低 reliability + 平均 reliability ---
    min_rel = min(ch.reliability for ch in channels)
    avg_rel = sum(ch.reliability for ch in channels) / len(channels)
    base_conf = min_rel * 0.6 + avg_rel * 0.4
    if include_fallback:
        base_conf *= 0.9
    confidence = max(0.0, min(1.0, base_conf))

    notes.insert(0, f"{len(channels)} reference(s) × preset '{preset_name}'")

    return CostEstimate(
        total_cost_usd=total_cost,
        breakdown=breakdown,
        multiplier=multiplier,
        confidence=confidence,
        notes=notes,
    )


def dry_run_preset(
    preset: Dict,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    include_fallback: bool = True,
) -> CostEstimate:
    """对一个 preset 做 dry-run 估算。

    preset 格式:
        {
            "reference_models": ["deepseek-v3", "glm-4-plus"],
            "aggregator": "deepseek-v3",
            "reference_count": 4,  # 每个 ref 模型的副本数
        }

    真实逻辑:把每个 ref 模型扩成 N 个 Channel(模拟 N 副本并行),
    第一个 channel 兼作 aggregator(按 spec:`channels[0]` 充当 agg)。
    """
    ref_names: List[str] = list(preset.get("reference_models", []))
    agg_name: Optional[str] = preset.get("aggregator")
    ref_count: int = int(preset.get("reference_count", 1))

    if not ref_names:
        raise ValueError("preset must define reference_models")
    if agg_name is None:
        agg_name = ref_names[0]
    if agg_name not in CHANNEL_REGISTRY:
        raise ValueError(f"unknown aggregator model: {agg_name}")
    for n in ref_names:
        if n not in CHANNEL_REGISTRY:
            raise ValueError(f"unknown model in preset: {n}")

    # 构造 channels 列表:aggregator 在前(ref_count 副本),refs 在后
    channels: List[Channel] = []
    agg_ch = CHANNEL_REGISTRY[agg_name]
    for _ in range(ref_count):
        channels.append(agg_ch)  # aggregator 副本(也会被算 ref cost)
    for name in ref_names:
        ch = CHANNEL_REGISTRY[name]
        if name == agg_name:
            continue  # 已经加过
        for _ in range(ref_count):
            channels.append(ch)

    estimate = estimate_moa_cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        channels=channels,
        preset_name=str(preset.get("name", "custom")),
        include_fallback=include_fallback,
    )

    # 重写 breakdown + notes 使其更可读(按 preset 视角)
    new_breakdown: Dict[str, float] = {}
    for name in ref_names:
        ch = CHANNEL_REGISTRY[name]
        per_model = (input_tokens / 1000.0) * ch.cost_per_1k_input
        per_model += (output_tokens / 1000.0) * ch.cost_per_1k_output
        per_model *= ref_count
        new_breakdown[f"ref:{name} (×{ref_count})"] = per_model

    # aggregator cost 独立
    total_ref_output = output_tokens * sum(ref_count for _ in ref_names)
    agg_in = (total_ref_output / 1000.0) * agg_ch.cost_per_1k_input
    agg_out = (output_tokens / 1000.0) * agg_ch.cost_per_1k_output
    new_breakdown[f"aggregator:{agg_name}"] = agg_in + agg_out

    new_notes: List[str] = [
        f"preset dry-run '{preset.get('name', 'custom')}': "
        f"{len(ref_names)} unique ref × {ref_count} copies = "
        f"{len(ref_names) * ref_count} ref calls",
    ]
    for name in ref_names:
        ch = CHANNEL_REGISTRY[name]
        per_model = (input_tokens / 1000.0) * ch.cost_per_1k_input
        per_model += (output_tokens / 1000.0) * ch.cost_per_1k_output
        per_model *= ref_count
        new_notes.append(
            f"  {name} (×{ref_count}): "
            f"{input_tokens * ref_count}in + {output_tokens * ref_count}out = ${per_model:.6f}"
        )
    new_notes.append(
        f"aggregator {agg_name}: {total_ref_output}in + {output_tokens}out = "
        f"${new_breakdown[f'aggregator:{agg_name}']:.6f}"
    )
    if include_fallback:
        new_notes.append("fallback multiplier: 1.5x")

    raw_total = sum(new_breakdown.values())
    final_total = raw_total * estimate.multiplier

    return CostEstimate(
        total_cost_usd=final_total,
        breakdown=new_breakdown,
        multiplier=estimate.multiplier,
        confidence=estimate.confidence,
        notes=new_notes,
    )


def compare_presets(
    presets: List[Dict],
    input_tokens: int = 1000,
    output_tokens: int = 500,
    include_fallback: bool = True,
) -> List[CostEstimate]:
    """比较多个 preset 的成本(返回排序结果,lowest first)。"""
    results: List[CostEstimate] = []
    for p in presets:
        est = dry_run_preset(
            p,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            include_fallback=include_fallback,
        )
        results.append(est)
    results.sort(key=lambda e: e.total_cost_usd)
    return results


def format_report(estimate: CostEstimate) -> str:
    """人类可读的成本报告。"""
    lines: List[str] = []
    lines.append("=== MoA Cost Estimate ===")
    lines.append(
        f"Total: ${estimate.total_cost_usd:.4f} "
        f"(multiplier {estimate.multiplier:.2f}x)"
    )
    lines.append(f"Confidence: {estimate.confidence:.2f}")
    lines.append("")
    lines.append("Breakdown:")
    for name, cost in sorted(estimate.breakdown.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {name:<40} ${cost:.4f}")
    if estimate.notes:
        lines.append("")
        lines.append("Notes:")
        for n in estimate.notes:
            lines.append(f"  - {n}")
    return "\n".join(lines)


__all__ = [
    "Channel",
    "CostEstimate",
    "DEEPSEEK",
    "GLM",
    "MOONSHOT",
    "QWEN",
    "GPT_MINI",
    "CLAUDE_HAIKU",
    "CHANNEL_REGISTRY",
    "PRESETS",
    "estimate_moa_cost",
    "dry_run_preset",
    "compare_presets",
    "format_report",
]
