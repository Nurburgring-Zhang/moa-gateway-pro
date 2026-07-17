"""moa_engine — MoA 引擎核心 (M-01: 基础引擎 + M-05: 3 proposer + 1 aggregator 协同)

来源: 02 MoA-together-ai (Mixture-of-Agents 论文核心算法)
论文: "Mixture-of-Agents: Enhancing LLM Capabilities through Collective
Intelligence" — Wang et al., Together AI, 2024.

核心实现 (M-01 + M-05):
  1. Proposer / Aggregator / ProposerResult / MoAResult 四个 dataclass
  2. call_proposer(): 单个 proposer 调用, 算 latency
  3. call_aggregator(): 把多份 proposals 喂给 aggregator 合成
  4. run_moa(): 完整 pipeline — 并行 call_proposer + call_aggregator
  5. validate_moa(): 配置校验 (≥1 proposer + 1 aggregator)
  6. JSON 序列化 (to_dict / from_dict / to_json / from_json)

设计要点:
  - provider_fn 注入: provider_fn(proposer_or_agg, prompt) -> (text, tokens)
    不直接耦合 moa_gateway.providers, 保持 engine 独立可测。
  - run_moa 内部用 asyncio.gather 并行调所有 proposers
  - total_tokens = sum(proposals.tokens) + aggregator.tokens
  - total_latency_ms = max(proposals.latency) + aggregator.latency
    (并行部分用 max; 顺序的聚合阶段单独算)
  - 模仿 n_layer_moa.py 风格 (dataclass 优先, 显式 logger, 不依赖外部状态)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ============ 错误类型 ============

class MoAEngineError(Exception):
    """MoA 引擎通用错误(配置非法/运行失败)"""


class ProposerCallError(MoAEngineError):
    """单个 proposer 调用失败"""


# ============ 数据模型 ============

@dataclass
class Proposer:
    """提议者:独立生成一份回答"""
    model_id: str
    system_prompt: str = ""
    temperature: float = 0.7


@dataclass
class Aggregator:
    """聚合者:把多份 proposals 合成 1 个最终输出"""
    model_id: str
    synthesis_prompt: str = ""


@dataclass
class ProposerResult:
    """单个 proposer 的返回"""
    model_id: str
    text: str
    latency_ms: float
    tokens: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MoAResult:
    """一次完整 MoA 调用的结果"""
    query: str
    proposals: list[ProposerResult] = field(default_factory=list)
    aggregated: str = ""
    total_tokens: int = 0
    total_latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "proposals": [p.to_dict() for p in self.proposals],
            "aggregated": self.aggregated,
            "total_tokens": self.total_tokens,
            "total_latency_ms": self.total_latency_ms,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @staticmethod
    def from_dict(d: dict) -> MoAResult:
        proposals_raw = d.get("proposals", []) or []
        proposals = [
            ProposerResult(
                model_id=str(p.get("model_id", "")),
                text=str(p.get("text", "")),
                latency_ms=float(p.get("latency_ms", 0.0)),
                tokens=int(p.get("tokens", 0)),
            )
            for p in proposals_raw
        ]
        return MoAResult(
            query=str(d.get("query", "")),
            proposals=proposals,
            aggregated=str(d.get("aggregated", "")),
            total_tokens=int(d.get("total_tokens", 0)),
            total_latency_ms=float(d.get("total_latency_ms", 0.0)),
        )

    @staticmethod
    def from_json(s: str) -> MoAResult:
        return MoAResult.from_dict(json.loads(s))


# provider_fn 签名: (proposer_or_aggregator, prompt) -> (text, tokens)
# proposer_or_aggregator 是 Proposer 或 Aggregator
# prompt 是已经拼好的字符串(由 caller 准备好)
ProviderFn = Callable[[Any, str], tuple[str, int]]


# ============ 配置校验 ============

def validate_moa(
    proposers: list[Proposer],
    aggregator: Aggregator | None,
) -> list[str]:
    """校验 MoA 配置

    Returns:
        缺失/非法项的描述列表; 空列表 = 配置合法
    """
    errors: list[str] = []
    if not proposers:
        errors.append("proposers: must have at least 1 proposer (got 0)")
    else:
        for i, p in enumerate(proposers):
            if not p.model_id or not isinstance(p.model_id, str):
                errors.append(f"proposers[{i}].model_id: must be non-empty string")
            if not (0.0 <= float(p.temperature) <= 2.0):
                errors.append(
                    f"proposers[{i}].temperature: must be in [0, 2], got {p.temperature}"
                )
    if aggregator is None:
        errors.append("aggregator: must be provided (got None)")
    elif not aggregator.model_id or not isinstance(aggregator.model_id, str):
        errors.append("aggregator.model_id: must be non-empty string")
    return errors


# ============ 内部:prompt 拼装 ============

def _build_proposer_prompt(query: str, proposer: Proposer) -> str:
    """给 proposer 的完整 prompt(含 system + user)"""
    parts: list[str] = []
    if proposer.system_prompt:
        parts.append(f"[SYSTEM]\n{proposer.system_prompt}\n")
    parts.append(f"[USER]\n{query}")
    return "\n".join(parts)


def _build_aggregator_prompt(
    query: str,
    aggregator: Aggregator,
    proposals: list[ProposerResult],
) -> str:
    """给 aggregator 的完整 prompt(含 synthesis + proposals)"""
    parts: list[str] = []
    if aggregator.synthesis_prompt:
        parts.append(f"[SYSTEM]\n{aggregator.synthesis_prompt}\n")
    body_parts = [f"[USER] 用户问题:\n{query}\n"]
    body_parts.append("\n以下是多个独立模型的回答,请综合它们生成一个更高质量的最终答案:\n")
    for i, p in enumerate(proposals, 1):
        body_parts.append(f"\n【Proposer #{i} (model={p.model_id})】\n{p.text}\n")
    parts.append("".join(body_parts))
    return "\n".join(parts)


# ============ 核心: 单个 proposer 调用 ============

async def call_proposer(
    proposer: Proposer,
    query: str,
    provider_fn: ProviderFn,
) -> ProposerResult:
    """调 1 个 proposer, 算 latency

    Args:
        proposer: Proposer dataclass
        query: 用户问题
        provider_fn: (proposer, prompt) -> (text, tokens)

    Returns:
        ProposerResult
    """
    prompt = _build_proposer_prompt(query, proposer)
    t0 = time.perf_counter()
    try:
        text, tokens = await asyncio.to_thread(provider_fn, proposer, prompt)
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        raise ProposerCallError(
            f"proposer {proposer.model_id} failed: {type(e).__name__}: {e}"
        ) from e
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return ProposerResult(
        model_id=proposer.model_id,
        text=str(text or ""),
        latency_ms=float(elapsed_ms),
        tokens=int(tokens or 0),
    )


# ============ 核心: aggregator 调用 ============

async def call_aggregator(
    aggregator: Aggregator,
    query: str,
    proposals: list[ProposerResult],
    provider_fn: ProviderFn,
) -> tuple[str, int, float]:
    """调 1 个 aggregator 合成所有 proposals

    Returns:
        (aggregated_text, tokens, latency_ms)
    """
    if not proposals:
        # 没 proposals, 直接给空
        return "", 0, 0.0
    prompt = _build_aggregator_prompt(query, aggregator, proposals)
    t0 = time.perf_counter()
    try:
        text, tokens = await asyncio.to_thread(provider_fn, aggregator, prompt)
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        raise MoAEngineError(
            f"aggregator {aggregator.model_id} failed: {type(e).__name__}: {e}"
        ) from e
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return str(text or ""), int(tokens or 0), float(elapsed_ms)


# ============ 核心: 完整 pipeline ============

async def run_moa(
    query: str,
    proposers: list[Proposer],
    aggregator: Aggregator,
    provider_fn: ProviderFn,
) -> MoAResult:
    """跑 1 次 MoA: 并行调所有 proposers, 再用 aggregator 合成

    Args:
        query: 用户问题
        proposers: ≥1 个 proposers
        aggregator: 1 个 aggregator
        provider_fn: provider 注入

    Returns:
        MoAResult

    Raises:
        MoAEngineError: 配置非法或 provider 全失败
    """
    # 校验
    errs = validate_moa(proposers, aggregator)
    if errs:
        raise MoAEngineError("invalid MoA config: " + "; ".join(errs))

    # Step 1: 并行跑 proposers
    tasks = [call_proposer(p, query, provider_fn) for p in proposers]
    proposals: list[ProposerResult] = await asyncio.gather(*tasks)

    # Step 2: aggregator 合成
    aggregated_text, agg_tokens, agg_latency_ms = await call_aggregator(
        aggregator, query, proposals, provider_fn
    )

    # Step 3: 累计 tokens / latency
    total_tokens = sum(p.tokens for p in proposals) + agg_tokens
    # latency: 并行部分用 max + aggregator 顺序时间
    proposer_max_latency = max((p.latency_ms for p in proposals), default=0.0)
    total_latency_ms = proposer_max_latency + agg_latency_ms

    return MoAResult(
        query=query,
        proposals=proposals,
        aggregated=aggregated_text,
        total_tokens=int(total_tokens),
        total_latency_ms=float(total_latency_ms),
    )


# ============ 工厂: 标准 3+1 配置 ============

def default_three_proposers() -> list[Proposer]:
    """论文主配置: 3 个 proposer, 多样化 system prompt 鼓励独立思考"""
    return [
        Proposer(
            model_id="proposer-A",
            system_prompt="你是一位严谨的分析师。请给出结构化、有依据的回答。",
            temperature=0.7,
        ),
        Proposer(
            model_id="proposer-B",
            system_prompt="你是一位创意思考者。请给出多角度、有想象力的回答。",
            temperature=0.8,
        ),
        Proposer(
            model_id="proposer-C",
            system_prompt="你是一位实用主义者。请给出可操作、简洁的回答。",
            temperature=0.6,
        ),
    ]


def default_aggregator() -> Aggregator:
    """标准 aggregator: 融合多份独立回答"""
    return Aggregator(
        model_id="aggregator-main",
        synthesis_prompt=(
            "你是综合者。下方有多个独立模型对同一问题的回答, "
            "请保留各回答中的有效信息, 去除冗余, 输出一份更高质量的最终答案。"
        ),
    )


__all__ = [
    "Proposer", "Aggregator", "ProposerResult", "MoAResult",
    "MoAEngineError", "ProposerCallError",
    "call_proposer", "call_aggregator", "run_moa", "validate_moa",
    "default_three_proposers", "default_aggregator",
]
