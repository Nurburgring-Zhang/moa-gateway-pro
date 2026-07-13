"""n_layer_moa — 多层 MoA (N-layer Mixture-of-Agents)

来源: 02 MoA-together-ai 论文核心 (Together AI, 2024)
论文: "Mixture-of-Agents: Enhancing LLM Capabilities through Collective
Intelligence" — L1 → L2 → L3 真分层架构。

核心实现:
  1. Proposer / Aggregator / LayerResult / MoAConfig 四个 dataclass
  2. synthesize_layer(): 并行调 proposers, 1 个 aggregator 合成
  3. run_n_layer_moa(): 跑 N 层, 每层把上轮 aggregated 喂回下层
  4. run_three_layer_moa(): 3 层特殊 case
  5. SSE 失败 → 自动回非流式 (模仿 moa-server 行为)
  6. 单 proposer 失败 → fallback 标记, 其他继续; 全失败抛 MoARunError
  7. max_total_tokens 预算控制, 触发立即停

非 mock, 所有调用真走 provider 抽象; provider 跑不通时仍走通整个 pipeline,
proposal 文本中包含 "MOCK:" 标记 (来自 MockProvider 自动降级)。
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Callable, Any

from moa_gateway.providers import build_provider, Provider, ChatRequest, ChatResponse, ProviderError

logger = logging.getLogger(__name__)


# ============ 错误类型 ============

class MoARunError(Exception):
    """MoA 运行期错误(全 layer proposer 都失败 / 配置非法等)"""


class BudgetExceededError(Exception):
    """预算耗尽(中途停止)"""


# ============ 数据模型 ============

@dataclass
class Proposer:
    """提议者:每层并行生成多份独立回答"""
    name: str
    model_id: str
    system_prompt: str = ""


@dataclass
class Aggregator:
    """聚合者:把同一层所有 proposals + (可选) prev_aggregated 合成 1 个 output"""
    name: str
    model_id: str
    synthesis_prompt: str = ""


@dataclass
class LayerResult:
    """一层的结果"""
    layer_idx: int
    proposals: List[str] = field(default_factory=list)
    aggregated: str = ""
    references: List[str] = field(default_factory=list)  # 该层喂进 aggregator 的 reference 文本(同 proposals + prev)

    def to_dict(self) -> Dict:
        return {
            "layer_idx": self.layer_idx,
            "proposals": list(self.proposals),
            "aggregated": self.aggregated,
            "references": list(self.references),
        }

    @staticmethod
    def from_dict(d: Dict) -> "LayerResult":
        return LayerResult(
            layer_idx=int(d.get("layer_idx", 0)),
            proposals=list(d.get("proposals", []) or []),
            aggregated=str(d.get("aggregated", "") or ""),
            references=list(d.get("references", []) or []),
        )


@dataclass
class MoAConfig:
    """MoA 配置"""
    num_layers: int = 3
    proposers_per_layer: int = 3
    temperature: float = 0.6
    max_total_tokens: int = 0  # 0 = 无限

    def __post_init__(self):
        if self.num_layers < 1:
            raise ValueError(f"num_layers must be >= 1, got {self.num_layers}")
        if self.proposers_per_layer < 1:
            raise ValueError(
                f"proposers_per_layer must be >= 1, got {self.proposers_per_layer}"
            )
        if not (0.0 <= float(self.temperature) <= 2.0):
            raise ValueError(f"temperature must be in [0, 2], got {self.temperature}")
        if self.max_total_tokens < 0:
            raise ValueError(f"max_total_tokens must be >= 0, got {self.max_total_tokens}")


# ============ 内部工具 ============

def _format_proposals_for_aggregator(proposals: List[str], prev_aggregated: Optional[str] = None) -> str:
    """把 proposals 拼成给 aggregator 的 reference 文本"""
    parts: List[str] = []
    if prev_aggregated:
        parts.append(f"【上一轮聚合输出】\n{prev_aggregated}\n")
    for i, p in enumerate(proposals, 1):
        parts.append(f"【Proposer #{i}】\n{p}\n")
    return "\n".join(parts).strip()


def _build_proposer_messages(query: str, proposer: Proposer, prev_aggregated: Optional[str] = None) -> List[Dict]:
    """proposer 的 messages — L1 不带 prev; L2+ 带 prev_aggregated 作为参考"""
    msgs: List[Dict] = []
    if proposer.system_prompt:
        msgs.append({"role": "system", "content": proposer.system_prompt})
    user = query
    if prev_aggregated:
        user = (
            f"用户问题:\n{query}\n\n"
            f"上一轮其他模型的综合输出(参考):\n{prev_aggregated}\n\n"
            f"请基于以上信息,给出你的独立回答。"
        )
    msgs.append({"role": "user", "content": user})
    return msgs


def _build_aggregator_messages(query: str, aggregator: Aggregator, references: str) -> List[Dict]:
    """aggregator 的 messages — 把 proposals + (可选) prev 合成"""
    msgs: List[Dict] = []
    if aggregator.synthesis_prompt:
        msgs.append({"role": "system", "content": aggregator.synthesis_prompt})
    user = (
        f"用户问题:\n{query}\n\n"
        f"多个独立模型的回答(可能含上一轮聚合):\n{references}\n\n"
        f"请综合所有信息,生成一个更高质量的回答。"
    )
    msgs.append({"role": "user", "content": user})
    return msgs


# ============ 单个 provider 调用 (SSE → 非流式 fallback) ============

async def _call_provider(
    provider: Provider,
    model_id: str,
    messages: List[Dict],
    temperature: float,
    max_tokens: int = 2048,
    prefer_stream: bool = True,
) -> ChatResponse:
    """调一次 provider, 优先 SSE, 失败回非流式 (模仿 moa-server 行为)

    返回 ChatResponse。失败抛 ProviderError。
    """
    req = ChatRequest(
        model=model_id,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=prefer_stream,
    )
    if prefer_stream and hasattr(provider, "chat_stream"):
        try:
            parts: List[str] = []
            async for chunk in provider.chat_stream(req):
                if chunk:
                    parts.append(chunk)
            if not parts:
                # SSE 拿到 0 chunk 也 fallback
                raise ProviderError("SSE returned 0 chunks", provider=provider.__class__.__name__)
            content = "".join(parts)
            # 模拟 token 计数 (无 API 返回时)
            pt = max(1, sum(len(m.get("content", "")) for m in messages) // 2)
            ct = max(1, len(content) // 2)
            return ChatResponse(
                content=content,
                finish_reason="stop",
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=pt + ct,
                model=model_id,
                provider=provider.__class__.__name__,
                cost=0.0,
            )
        except Exception as e:
            logger.warning("[n_layer_moa] SSE failed, fallback to non-stream: %s", e)
            # 自动回非流式
            req.stream = False
            return await provider.chat(req)
    # 非流式直调
    return await provider.chat(req)


# ============ 单个 proposer 调 (带 fallback 标记) ============

async def _run_proposer(
    proposer: Proposer,
    query: str,
    prev_aggregated: Optional[str],
    provider: Provider,
    temperature: float,
    max_tokens: int,
) -> str:
    """跑一个 proposer; 失败/异常返回 fallback 标记"""
    msgs = _build_proposer_messages(query, proposer, prev_aggregated)
    try:
        resp = await _call_provider(
            provider, proposer.model_id, msgs,
            temperature=temperature, max_tokens=max_tokens, prefer_stream=True,
        )
        content = (resp.content or "").strip()
        if not content:
            return f"[fallback:{proposer.name}] empty response"
        return content
    except Exception as e:
        logger.warning("[n_layer_moa] proposer %s failed: %s", proposer.name, e)
        return f"[fallback:{proposer.name}] {type(e).__name__}: {e}"


# ============ 核心: 合成一层 ============

async def synthesize_layer(
    proposers: List[Proposer],
    query: str,
    layer_idx: int,
    prev_aggregated: Optional[str] = None,
    aggregator: Optional[Aggregator] = None,
    providers_registry: Optional[Dict[str, Provider]] = None,
    temperature: float = 0.6,
    max_tokens: int = 2048,
    max_total_tokens: Optional[int] = None,
    tokens_used: int = 0,
) -> LayerResult:
    """合成 1 层 MoA: 并行跑所有 proposers, 然后用 aggregator 合成 1 个 output

    Args:
        proposers: 该层的 proposers
        query: 用户原始 query
        layer_idx: 层号 (1-indexed)
        prev_aggregated: 上一层的 aggregated (L2+ 喂回)
        aggregator: 该层用的 aggregator; None 表示用 proposers[0] 兼任
        providers_registry: {model_id: Provider}; None 表示自动 build
        temperature: 透传
        max_tokens: 单次调用最大 token
        max_total_tokens: 跨层总预算上限
        tokens_used: 此前累计已用 token

    Returns:
        LayerResult
    """
    if not proposers:
        raise ValueError("proposers must be non-empty")

    # 预算检查
    if max_total_tokens is not None and max_total_tokens > 0 and tokens_used >= max_total_tokens:
        raise BudgetExceededError(
            f"budget exhausted before layer {layer_idx}: used {tokens_used} >= {max_total_tokens}"
        )

    # 准备 providers (允许外部注入, 测试场景常用)
    if providers_registry is None:
        providers_registry = {}

    def _get_provider(model_id: str) -> Provider:
        if model_id in providers_registry:
            return providers_registry[model_id]
        return build_provider(model_id, model=model_id, api_key="")

    # 并行跑所有 proposers
    tasks = []
    for p in proposers:
        prov = _get_provider(p.model_id)
        tasks.append(_run_proposer(p, query, prev_aggregated, prov, temperature, max_tokens))
    raw = await asyncio.gather(*tasks, return_exceptions=False)

    # 失败回退: 单个 fail 用 fallback 标记; 全 fail 抛 MoARunError
    proposals: List[str] = []
    for p, r in zip(proposers, raw):
        if isinstance(r, Exception):
            proposals.append(f"[fallback:{p.name}] {type(r).__name__}: {r}")
        else:
            proposals.append(str(r))
    non_fallback = [x for x in proposals if not x.startswith("[fallback:")]
    if not non_fallback:
        raise MoARunError(
            f"layer {layer_idx}: all {len(proposers)} proposers failed"
        )

    # aggregator 合成
    if aggregator is None:
        # 默认用 proposers[0] 兼任
        aggregator = Aggregator(
            name=f"{proposers[0].name}-agg",
            model_id=proposers[0].model_id,
            synthesis_prompt="你是综合者。请融合以下多份独立回答,给出一份最优最终答案。",
        )

    references_text = _format_proposals_for_aggregator(proposals, prev_aggregated)
    agg_msgs = _build_aggregator_messages(query, aggregator, references_text)
    agg_prov = _get_provider(aggregator.model_id)
    try:
        agg_resp = await _call_provider(
            agg_prov, aggregator.model_id, agg_msgs,
            temperature=temperature, max_tokens=max_tokens, prefer_stream=True,
        )
        aggregated = (agg_resp.content or "").strip()
        if not aggregated:
            # fallback: 直接拼 proposals
            aggregated = "\n\n---\n\n".join(non_fallback)
    except Exception as e:
        logger.warning("[n_layer_moa] aggregator %s failed, fallback to concat: %s", aggregator.name, e)
        aggregated = "\n\n---\n\n".join(non_fallback)

    return LayerResult(
        layer_idx=layer_idx,
        proposals=proposals,
        aggregated=aggregated,
        references=[references_text],  # 整层喂给 aggregator 的 reference 块
    )


# ============ 核心: 跑 N 层 ============

async def run_n_layer_moa(
    query: str,
    config: MoAConfig,
    proposers: List[Proposer],
    aggregator: Optional[Aggregator] = None,
    providers_registry: Optional[Dict[str, Provider]] = None,
    max_total_tokens: int = 0,
) -> List[LayerResult]:
    """跑 N 层 MoA pipeline

    Args:
        query: 用户原始 query
        config: MoAConfig
        proposers: 所有 proposers (每层复用同一批, 论文允许)
        aggregator: 每层用的 aggregator; None 表示用 proposers[0] 兼任
        providers_registry: {model_id: Provider} 注入
        max_total_tokens: 全局预算(0=无限), 覆盖 config.max_total_tokens

    Returns:
        List[LayerResult], 长度 = config.num_layers
        最终输出 = results[-1].aggregated
    """
    if not proposers:
        raise ValueError("proposers must be non-empty")
    if config.proposers_per_layer > len(proposers):
        # 不阻断, 仅用实际数量
        pass

    budget = max_total_tokens or config.max_total_tokens
    tokens_used = 0
    results: List[LayerResult] = []
    prev_agg: Optional[str] = None

    for i in range(1, config.num_layers + 1):
        # 选该层用哪批 proposers (循环复用)
        layer_proposers = proposers[: config.proposers_per_layer]
        try:
            layer_res = await synthesize_layer(
                proposers=layer_proposers,
                query=query,
                layer_idx=i,
                prev_aggregated=prev_agg,
                aggregator=aggregator,
                providers_registry=providers_registry,
                temperature=config.temperature,
                max_total_tokens=budget,
                tokens_used=tokens_used,
            )
        except BudgetExceededError:
            # 中途停 — 把已跑的返回
            logger.info("[n_layer_moa] budget exhausted at layer %d, stopping", i)
            break
        results.append(layer_res)
        prev_agg = layer_res.aggregated
        # 估算 token 消耗: 用所有 proposals + aggregated 的字符数 // 2
        # 真实场景可读 resp.total_tokens, 这里做粗略估计
        layer_chars = sum(len(p) for p in layer_res.proposals) + len(layer_res.aggregated)
        tokens_used += max(1, layer_chars // 2)
        if budget and budget > 0 and tokens_used >= budget:
            logger.info("[n_layer_moa] budget hit after layer %d (%d/%d tokens)", i, tokens_used, budget)
            break

    if not results:
        raise MoARunError("no layer completed (all skipped by budget)")

    return results


# ============ 3 层特殊 case ============

async def run_three_layer_moa(
    query: str,
    proposers: List[Proposer],
    aggregators: List[Aggregator],
    providers_registry: Optional[Dict[str, Provider]] = None,
    temperature: float = 0.6,
    max_total_tokens: int = 0,
) -> Dict:
    """3 层 MoA (论文主架构):每层用不同 aggregator

    Args:
        query: 用户 query
        proposers: L1 用的 proposers (L2/L3 复用同批)
        aggregators: 长度 3, 顺序对应 L1, L2, L3
        providers_registry: provider 注入
        temperature: 透传
        max_total_tokens: 0=无限

    Returns:
        {
          "layers": [LayerResult, LayerResult, LayerResult],
          "final_output": str,        # L3.aggregated
          "tokens_used": int,
          "layer_outputs": [str, str, str],   # 每层 aggregated
        }
    """
    if len(aggregators) != 3:
        raise ValueError(f"3-layer MoA needs exactly 3 aggregators, got {len(aggregators)}")
    if not proposers:
        raise ValueError("proposers must be non-empty")

    results: List[LayerResult] = []
    prev_agg: Optional[str] = None
    tokens_used = 0
    for i, agg in enumerate(aggregators, 1):
        try:
            layer_res = await synthesize_layer(
                proposers=proposers,
                query=query,
                layer_idx=i,
                prev_aggregated=prev_agg,
                aggregator=agg,
                providers_registry=providers_registry,
                temperature=temperature,
                max_total_tokens=max_total_tokens,
                tokens_used=tokens_used,
            )
        except BudgetExceededError:
            logger.info("[n_layer_moa:3layer] budget exhausted at layer %d", i)
            break
        results.append(layer_res)
        prev_agg = layer_res.aggregated
        layer_chars = sum(len(p) for p in layer_res.proposals) + len(layer_res.aggregated)
        tokens_used += max(1, layer_chars // 2)
        if max_total_tokens and tokens_used >= max_total_tokens:
            logger.info("[n_layer_moa:3layer] budget hit after layer %d", i)
            break

    if not results:
        raise MoARunError("3-layer MoA: no layer completed")

    return {
        "layers": results,
        "final_output": results[-1].aggregated,
        "tokens_used": tokens_used,
        "layer_outputs": [r.aggregated for r in results],
    }


__all__ = [
    "Proposer", "Aggregator", "LayerResult", "MoAConfig",
    "MoARunError", "BudgetExceededError",
    "synthesize_layer", "run_n_layer_moa", "run_three_layer_moa",
]
