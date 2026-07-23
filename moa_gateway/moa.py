"""moa_gateway.moa — MoA 编排引擎(完整对齐 Hermes v0.18 + OpenSquilla v0.5.0)

支持的 strategy:
- single   : 单模型直接调用(无协作)
- parallel : N 模型并行提案 + 1 聚合器综合(OpenSquilla 核心)
- compose  : N 模型分工扮演不同角色(可行性/性能/安全/UX)(OpenSquilla 风格)
- judge    : 单模型多轮自我反思(成本敏感场景)
- chain    : 多步 MoA 链(每步独立编排,前步输出喂入后步)
- pipeline : planner → generator → evaluator

借鉴:
- Hermes v0.18.0 MoA 模型委员会(参考模型不带 tool schema,聚合器拿全部)
- OpenSquilla v0.5.0「4 国产 + 1 聚合」国家队 preset
- 共识分数触发 critic 追轮(自创)
- 独立温度(reference / aggregator)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .config import MoAPresetConfig, ReferenceModelConfig, get_settings
from .model_pool import ModelEndpoint, ModelPool, ModelTier, get_model_pool
from .prompts import get_prompt
from .router import IntelligentRouter, get_router

logger = logging.getLogger(__name__)


@dataclass
class ReferenceResult:
    model_id: str
    content: str
    role: str = ""  # compose 模式下的角色
    tier: str = ""  # 模型 tier (standard/premium/...)
    success: bool = False
    error: str = ""
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0


@dataclass
class CriticResult:
    model_id: str
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    refined_content: str = ""
    success: bool = False
    error: str = ""
    latency_ms: float = 0.0
    cost: float = 0.0


@dataclass
class ChainStepResult:
    step: int
    strategy: str
    preset: str
    output: str
    cost: float = 0.0
    latency_ms: float = 0.0


@dataclass
class MoAResult:
    request_id: str
    query: str
    preset: str
    strategy: str
    references: list[ReferenceResult] = field(default_factory=list)
    critics: list[CriticResult] = field(default_factory=list)
    chain_steps: list[ChainStepResult] = field(default_factory=list)
    aggregated_content: str = ""
    final_content: str = ""
    aggregator_model: str = ""
    consensus_score: float = 0.0
    iterations: int = 1
    total_latency_ms: float = 0.0
    total_cost: float = 0.0
    fallback_used: bool = False
    pipeline_stages: list[dict[str, Any]] = field(default_factory=list)
    layers_count: int = 0
    layer_outputs: dict[str, Any] = field(default_factory=dict)
    winner_model: str = ""
    ranker_output: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "query": self.query,
            "preset": self.preset,
            "strategy": self.strategy,
            "references": [
                {
                    "model_id": r.model_id,
                    "role": r.role,
                    "success": r.success,
                    "latency_ms": round(r.latency_ms, 1),
                    "cost": round(r.cost, 6),
                    "tokens": r.prompt_tokens + r.completion_tokens,
                    "preview": r.content[:300] + ("..." if len(r.content) > 300 else ""),
                }
                for r in self.references
            ],
            "critics": [
                {
                    "model_id": c.model_id,
                    "success": c.success,
                    "issues_count": len(c.issues),
                    "suggestions_count": len(c.suggestions),
                    "latency_ms": round(c.latency_ms, 1),
                    "cost": round(c.cost, 6),
                }
                for c in self.critics
            ],
            "chain_steps": [
                {
                    "step": s.step,
                    "strategy": s.strategy,
                    "preset": s.preset,
                    "latency_ms": round(s.latency_ms, 1),
                    "cost": round(s.cost, 6),
                    "preview": (s.output or "")[:200]
                    + ("..." if len(s.output or "") > 200 else ""),
                }
                for s in self.chain_steps
            ],
            "aggregator_model": self.aggregator_model,
            "winner_model": self.winner_model,
            "ranker_output": self.ranker_output,
            "layers_count": self.layers_count,
            "layer_outputs": self.layer_outputs,
            "consensus_score": round(self.consensus_score, 3),
            "iterations": self.iterations,
            "total_latency_ms": round(self.total_latency_ms, 1),
            "total_cost": round(self.total_cost, 6),
            "fallback_used": self.fallback_used,
            "pipeline_stages": self.pipeline_stages,
            "final_content": self.final_content,
        }


# ========== Prompt 模板 ==========
SYSTEM_AGGREGATOR = """你是一个多模型答案的**聚合器**(aggregator)。你的任务是把多个独立模型的回答综合成一份最优的最终答案。

工作原则:
1. **分析**:逐个审视每个参考回答,识别其优势、不足、错误。
2. **去伪存真**:剔除明显错误或无依据的论断,保留有共识或可验证的内容。
3. **互补融合**:不同回答有不同的切入角度,把它们的长处融合,短处补全。
4. **明确裁决**:如果参考之间存在分歧,基于事实和逻辑做出明确选择,不要含糊。
5. **结构化输出**:最终答案要有清晰的结构(标题/列表/代码块),便于直接使用。
6. **不编造**:不要捏造参考回答里没有的事实;如果信息确实不足,坦诚说明。

输出格式:
- 先用 1-2 句话概述你的综合判断。
- 然后给出最终答案(可用 markdown)。
"""

SYSTEM_CRITIC = """你是一个**互审员**(critic)。你的任务是审查聚合后的答案,找出可能的问题并提出改进建议。

检查维度:
1. **事实性**:有无明显错误、过期信息、未经验证的论断?
2. **完整性**:是否遗漏了用户问题里的关键点?
3. **逻辑性**:推理是否连贯,有无自相矛盾?
4. **实用性**:对用户的实际操作有无指导价值?
5. **风险性**:有无可能误导用户、引发安全/法律/财务风险的表述?

输出格式(JSON):
{
  "issues": ["问题1", "问题2", ...],
  "suggestions": ["建议1", "建议2", ...],
  "verdict": "pass" | "needs_revision"
}
"""

# Compose 模式角色 prompt
COMPOSE_ROLES = {
    "feasibility": "你从**可行性**角度分析。关注:技术能不能实现、实现成本、依赖风险、技术债务、关键障碍。",
    "performance": "你从**性能**角度分析。关注:时间复杂度、空间复杂度、吞吐量、延迟、可扩展性瓶颈。",
    "security": "你从**安全性**角度分析。关注:认证授权、注入风险、数据泄露、攻击面、加密、合规。",
    "ux": "你从**用户体验**角度分析。关注:易用性、错误处理、文档、可访问性、上手成本。",
    "architecture": "你从**架构**角度分析。关注:模块划分、接口设计、依赖关系、演进路径。",
    "business": "你从**业务**角度分析。关注:ROI、商业价值、风险、市场、用户付费意愿。",
}

JUDGE_PROMPT = """你的任务是:对自己上一轮的回答做**多轮自我反思与修订**。

每轮反思请检查:
1. 我的回答是否准确?有无事实错误?
2. 是否遗漏了用户问题的关键点?
3. 推理是否连贯?有无自相矛盾?
4. 是否需要补充细节或代码示例?

如果当前回答已经很好,只输出:
VERDICT: PASS

否则输出修订后的完整答案(直接给最终版,不要解释修改过程)。"""


class MoAOrchestrator:
    """MoA 编排引擎"""

    def __init__(
        self, model_pool: ModelPool | None = None, router: IntelligentRouter | None = None
    ):
        self.pool = model_pool or get_model_pool()
        self.router = router or get_router()
        self.settings = get_settings()

    async def execute(
        self,
        query: str,
        context: list[dict] | None = None,
        tools: list[dict] | None = None,
        preset: str | None = None,
        strategy: str | None = None,
        reference_count: int | None = None,
        aggregator: str | None = None,
        critic_rounds: int | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
    ) -> MoAResult:
        """统一执行入口,根据 preset/strategy 分发"""
        request_id = "moa_" + uuid.uuid4().hex[:12]
        start = time.time()
        result = MoAResult(request_id=request_id, query=query, preset="", strategy="")

        moa_cfg = self.settings.moa
        preset_name = preset or moa_cfg.default_preset or "balanced"
        preset_cfg = moa_cfg.presets.get(preset_name)
        if not preset_cfg:
            preset_cfg = moa_cfg.presets.get("balanced") or list(moa_cfg.presets.values())[0]
            preset_name = "balanced"
        result.preset = preset_name

        strat = strategy or preset_cfg.strategy or "parallel"
        if not moa_cfg.enabled and strat != "single":
            strat = "single"
        result.strategy = strat

        # 用户传入的 reference_count / aggregator 覆盖 preset
        ref_n = reference_count or preset_cfg.reference_count or moa_cfg.reference_models
        agg_id = aggregator or preset_cfg.aggregator
        rounds = critic_rounds if critic_rounds is not None else preset_cfg.critic_rounds

        # 独立温度(用户传入的 temperature 作为 reference_temperature 覆盖)
        ref_temp = temperature if temperature is not None else preset_cfg.reference_temperature
        agg_temp = preset_cfg.aggregator_temperature
        max_tok = max_tokens or preset_cfg.max_tokens or 4096

        # 构造消息
        if not context:
            context = []
        if not context or context[-1].get("role") != "user":
            messages = context + [{"role": "user", "content": query}]
        else:
            messages = context.copy()
            messages[-1] = {"role": "user", "content": query}

        # 分发到具体 strategy
        try:
            if strat == "single" or (preset_cfg.reference_models == [] and ref_n <= 1):
                await self._run_single(result, messages, tools, agg_id, ref_temp, max_tok, start)
            elif strat == "compose":
                await self._run_compose(
                    result, messages, tools, preset_cfg, ref_temp, agg_temp, max_tok, start
                )
            elif strat == "judge":
                await self._run_judge(
                    result, messages, tools, preset_cfg, agg_id, ref_temp, max_tok, start
                )
            elif strat == "chain":
                await self._run_chain(
                    result, messages, tools, preset_cfg, ref_temp, agg_temp, max_tok, start
                )
            elif strat == "pipeline":
                await self._run_pipeline(
                    result, messages, tools, preset_cfg, ref_temp, max_tok, start
                )
            elif strat == "layered":
                # Together AI MoA 真多层 L1→L2→L3
                await self._run_layered(
                    result, messages, tools, preset_cfg, agg_id, ref_temp, agg_temp, max_tok, start
                )
            elif strat == "single_proposer":
                # 同模型高温多次采样(论文:Single-Proposer setting)
                await self._run_single_proposer(
                    result, messages, tools, preset_cfg, agg_id, ref_temp, max_tok, start
                )
            elif strat == "ranker":
                # LLM Ranker baseline(论文 Figure 4:让 aggregator 选而不是生成)
                await self._run_ranker(
                    result, messages, tools, preset_cfg, agg_id, ref_temp, max_tok, start
                )
            elif strat in (
                "cost_first",
                "latency_first",
                "diversity_moa",
                "capability_aware",
                "adaptive_ensemble",
            ):
                # Task #45: Strategy-based model selection
                await self._run_strategy_based(
                    result, messages, tools, preset_cfg, strat,
                    ref_n, agg_id, ref_temp, agg_temp, max_tok, start,
                )
            else:  # parallel
                await self._run_parallel(
                    result,
                    messages,
                    tools,
                    preset_cfg,
                    ref_n,
                    agg_id,
                    rounds,
                    ref_temp,
                    agg_temp,
                    max_tok,
                    start,
                )
        except Exception as e:
            logger.exception("MoA execute failed: %s", e)
            raise RuntimeError(f"MoA execute failed: {e}") from e

        result.total_latency_ms = (time.time() - start) * 1000
        logger.info(
            "MoA[%s] strategy=%s preset=%s done in %.0fms cost=$%.4f",
            request_id,
            result.strategy,
            result.preset,
            result.total_latency_ms,
            result.total_cost,
        )
        return result

    # ========== single ==========
    async def _run_single(
        self,
        result: MoAResult,
        messages,
        tools,
        agg_id: str | None,
        temperature: float,
        max_tokens: int,
        start: float,
    ):
        result.strategy = "single"
        ep = None
        if agg_id and self.pool.endpoints.get(agg_id):
            ep = self.pool.endpoints[agg_id]
        if not ep:
            d = self.router.route(messages[-1].get("content", ""))
            ep = d.primary
        if not ep:
            raise RuntimeError("no available model for single")
        result.aggregator_model = ep.id
        resp = await self._call_with_fallback(ep, messages, tools, temperature, max_tokens)
        result.aggregated_content = resp["content"]
        result.final_content = resp["content"]
        result.total_cost = resp["cost"]
        result.fallback_used = resp.get("fallback_used", False)
        if resp.get("used_model_id"):
            result.aggregator_model = resp["used_model_id"]
        result.references = [
            ReferenceResult(
                model_id=ep.id,
                content=resp["content"],
                success=True,
                prompt_tokens=resp.get("prompt_tokens", 0),
                completion_tokens=resp.get("completion_tokens", 0),
                cost=resp["cost"],
                latency_ms=resp.get("latency_ms", 0),
            )
        ]

    # ========== parallel (OpenSquilla 核心) ==========
    async def _run_parallel(
        self,
        result: MoAResult,
        messages,
        tools,
        preset_cfg: MoAPresetConfig,
        reference_count: int,
        aggregator_id: str | None,
        critic_rounds: int,
        ref_temp: float,
        agg_temp: float,
        max_tokens: int,
        start: float,
    ):
        # 选参考模型(显式 > 动态)
        ref_endpoints, aggregator_ep = self._resolve_models(
            preset_cfg, reference_count, aggregator_id
        )
        if not aggregator_ep:
            aggregator_ep = ref_endpoints[0] if ref_endpoints else None
        if not aggregator_ep:
            raise RuntimeError("no available model for MoA")
        result.aggregator_model = aggregator_ep.id

        # 2) 参考模型并行(不带 tool schema)
        ref_results = await self._run_references(ref_endpoints, messages, ref_temp, max_tokens)
        result.references = ref_results
        ok_count = sum(1 for r in ref_results if r.success)
        if ok_count == 0:
            ref_results = [
                ReferenceResult(
                    model_id="system",
                    content="[所有参考模型均调用失败,请基于通用知识直接回答用户问题]",
                    success=True,
                )
            ]
            result.references = ref_results

        # 3) 聚合
        agg_messages = self._build_aggregator_messages(messages, ref_results)
        agg_resp = await self._call_with_fallback(
            aggregator_ep, agg_messages, tools, agg_temp, max_tokens
        )
        result.aggregated_content = agg_resp["content"]
        result.total_cost += agg_resp["cost"]
        result.fallback_used = agg_resp.get("fallback_used", False)
        if agg_resp.get("used_model_id"):
            result.aggregator_model = agg_resp["used_model_id"]

        # 4) 共识分
        consensus = self._calculate_consensus(ref_results)
        result.consensus_score = consensus

        # 5) 互审多轮(共识低时自动追轮)
        MAX_EXTRA_ROUNDS = 2
        if critic_rounds > 0 and consensus < self.settings.moa.consensus_threshold:
            critic_rounds = critic_rounds + MAX_EXTRA_ROUNDS

        current_content = result.aggregated_content
        for r in range(critic_rounds):
            critic = await self._run_critic(
                current_content, ref_results, aggregator_ep, agg_temp, max_tokens
            )
            result.critics.append(critic)
            result.total_cost += critic.cost
            if not critic.success or not critic.issues:
                break
            refine_messages = agg_messages + [
                {"role": "assistant", "content": current_content},
                {
                    "role": "user",
                    "content": (
                        "你的初稿收到以下评审意见:\n\n"
                        "**问题**:\n"
                        + "\n".join(f"- {i}" for i in critic.issues)
                        + "\n\n**建议**:\n"
                        + "\n".join(f"- {s}" for s in critic.suggestions)
                        + "\n\n请基于这些反馈修订答案,保持原结构同时解决以上问题。"
                    ),
                },
            ]
            try:
                refined = await self._call_with_fallback(
                    aggregator_ep, refine_messages, tools, agg_temp, max_tokens
                )
                current_content = refined["content"]
                result.total_cost += refined["cost"]
                result.iterations = r + 2
                if refined.get("used_model_id"):
                    result.aggregator_model = refined["used_model_id"]
            except Exception as e:
                logger.warning("refine round %d failed: %s", r + 1, e)
                break

        result.final_content = current_content

    # ========== compose(多角度分工) ==========
    async def _run_compose(
        self,
        result: MoAResult,
        messages,
        tools,
        preset_cfg: MoAPresetConfig,
        ref_temp: float,
        agg_temp: float,
        max_tokens: int,
        start: float,
    ):
        """Compose:每个参考模型扮演一个角色(aspect)
        比 parallel 更适合"多角度决策"场景。
        """
        result.strategy = "compose"
        explicit_refs = preset_cfg.reference_models
        roles_used: list[str] = []
        ref_endpoints: list[ModelEndpoint] = []
        ref_roles: dict[str, str] = {}  # model_id -> role

        if explicit_refs and any(r.role for r in explicit_refs):
            # 显式列表里配了 role
            for ref_cfg in explicit_refs:
                ep = self._pick_endpoint_for_ref(ref_cfg)
                if ep:
                    ref_endpoints.append(ep)
                    role = ref_cfg.role or "general"
                    ref_roles[ep.id] = role
                    roles_used.append(role)
            # 补足到 reference_count
            needed = max(0, preset_cfg.reference_count - len(ref_endpoints))
            if needed:
                fallback_refs = self.pool.select_many(
                    ModelTier(preset_cfg.tier),
                    needed,
                    prefer_diversity=True,
                    exclude_ids=[e.id for e in ref_endpoints],
                )
                for ep in fallback_refs:
                    ref_endpoints.append(ep)
                    ref_roles[ep.id] = "general"
        else:
            # 自动分配 4 个角色:feasibility / performance / security / ux
            role_pool = ["feasibility", "performance", "security", "ux"]
            ref_endpoints = self.pool.select_many(
                ModelTier(preset_cfg.tier), preset_cfg.reference_count, prefer_diversity=True
            )
            for i, ep in enumerate(ref_endpoints):
                role = role_pool[i % len(role_pool)]
                ref_roles[ep.id] = role
                roles_used.append(role)

        if not ref_endpoints:
            raise RuntimeError("no available model for compose")

        # aggregator
        aggregator = (
            self.pool.endpoints.get(preset_cfg.aggregator) if preset_cfg.aggregator else None
        )
        if not aggregator:
            aggregator = self.pool.select_one(ModelTier(preset_cfg.aggregator_tier))
        if not aggregator:
            aggregator = ref_endpoints[0]
        result.aggregator_model = aggregator.id

        # 用标准 _call_one_ref:每条 messages 临时塞 role system prompt
        tasks = []
        for ep in ref_endpoints:
            role = ref_roles.get(ep.id, "general")
            role_prompt = get_prompt(f"compose_{role}")
            role_messages = [{"role": "system", "content": role_prompt}] + list(messages)
            tasks.append(
                self._call_one_ref_with_messages(ep, role_messages, ref_temp, max_tokens, role)
            )
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        ref_results = []
        for ep, r in zip(ref_endpoints, responses, strict=False):
            if isinstance(r, Exception):
                ref_results.append(
                    ReferenceResult(
                        model_id=ep.id,
                        content="",
                        role=ref_roles.get(ep.id, ""),
                        success=False,
                        error=str(r),
                    )
                )
            else:
                rr: ReferenceResult = r
                rr.role = ref_roles.get(ep.id, "")
                ref_results.append(rr)
        result.references = ref_results

        agg_messages = self._build_compose_aggregator_messages(messages, ref_results, roles_used)
        agg_resp = await self._call_with_fallback(
            aggregator, agg_messages, tools, agg_temp, max_tokens
        )
        result.aggregated_content = agg_resp["content"]
        result.final_content = agg_resp["content"]
        result.total_cost += agg_resp["cost"]
        result.fallback_used = agg_resp.get("fallback_used", False)
        if agg_resp.get("used_model_id"):
            result.aggregator_model = agg_resp["used_model_id"]
        result.consensus_score = self._calculate_consensus(ref_results)
        result.metadata = {
            "roles_used": roles_used,
            "compose_strategy": "multi-aspect",
            "role_distribution": {x.model_id: x.role for x in ref_results},
        }

    def _build_compose_aggregator_messages(self, original, ref_results, roles_used):
        messages = [m for m in original if m.get("role") == "system"]
        messages.append(
            {
                "role": "system",
                "content": (
                    "你是一个聚合器。下面的多个模型从不同角度(角色)分析了用户问题:"
                    + "、".join(roles_used)
                    + "。请综合所有角度,形成一个完整的、有层次的最终答案。"
                    "对于不同角度的冲突,基于事实做明确裁决。"
                ),
            }
        )
        # 参考回答
        ref_text = "\n\n".join(
            [
                f"【{r.role or 'general'} 视角 - 模型 {r.model_id}】\n{r.content}"
                if r.success
                else f"【{r.role} 视角 - 模型 {r.model_id}】失败: {r.error}"
                for r in ref_results
            ]
        )
        user_q = next((m.get("content", "") for m in original if m.get("role") == "user"), "")
        messages.append(
            {"role": "user", "content": f"用户问题:\n{user_q}\n\n多角度分析:\n{ref_text}"}
        )
        return messages

    # ========== judge(单模型多轮反思) ==========
    async def _run_judge(
        self,
        result: MoAResult,
        messages,
        tools,
        preset_cfg: MoAPresetConfig,
        agg_id: str | None,
        temperature: float,
        max_tokens: int,
        start: float,
    ):
        """Judge:单模型生成 + 多轮自我反思修订
        成本敏感场景 — 用一个模型完成多角度协作。
        """
        result.strategy = "judge"
        ep = None
        if agg_id and self.pool.endpoints.get(agg_id):
            ep = self.pool.endpoints[agg_id]
        if not ep:
            ep = self.pool.select_one(ModelTier(preset_cfg.aggregator_tier))
        if not ep:
            raise RuntimeError("no available model for judge")
        result.aggregator_model = ep.id

        # 第一轮:正常回答
        resp1 = await self._call_with_fallback(ep, messages, tools, temperature, max_tokens)
        current = resp1["content"]
        result.total_cost += resp1["cost"]
        result.fallback_used = resp1.get("fallback_used", False)
        result.iterations = 1

        # 多轮反思(最多 critic_rounds 轮)
        for r in range(preset_cfg.critic_rounds):
            critique_msgs = messages + [
                {"role": "assistant", "content": current},
                {"role": "user", "content": get_prompt("judge_reflection")},
            ]
            try:
                resp2 = await self._call_with_fallback(
                    ep, critique_msgs, tools, max(0.2, temperature - 0.2), max_tokens
                )
                result.total_cost += resp2["cost"]
                new_content = resp2["content"].strip()
                # 检查是否 pass
                if "VERDICT: PASS" in new_content.upper():
                    break
                current = new_content
                result.iterations = r + 2
                result.critics.append(
                    CriticResult(
                        model_id=ep.id,
                        success=True,
                        refined_content=new_content,
                        latency_ms=resp2.get("latency_ms", 0),
                        cost=resp2["cost"],
                    )
                )
            except Exception as e:
                logger.warning("judge round %d failed: %s", r + 1, e)
                break

        result.aggregated_content = current
        result.final_content = current

    # ========== chain(链式 MoA) ==========
    async def _run_chain(
        self,
        result: MoAResult,
        messages,
        tools,
        preset_cfg: MoAPresetConfig,
        ref_temp: float,
        agg_temp: float,
        max_tokens: int,
        start: float,
    ):
        """Chain:多步 MoA 串行,前步输出喂入后步
        适合"先调研 → 再分析 → 再综述"这类长链路任务。
        """
        result.strategy = "chain"
        steps = preset_cfg.stages or []
        if not steps:
            # 默认三步:调研 → 分析 → 综述
            from .config import MoAStageConfig

            steps = [
                MoAStageConfig(name="research", tier="standard"),
                MoAStageConfig(name="analyze", tier="premium"),
                MoAStageConfig(name="summarize", tier="premium"),
            ]
        current_messages = list(messages)
        # 用独立的 settings 副本,绝不污染全局(安全:并发请求安全)
        from copy import deepcopy

        sub_settings = deepcopy(self.settings)
        for idx, stage in enumerate(steps):
            step_result = ChainStepResult(
                step=idx + 1, strategy="chain", preset=stage.name, output="", cost=0
            )
            # 加载此 step 的角色 prompt(可被用户自定义)
            role_prompt = get_prompt(f"chain_{stage.name}")
            sub_preset = MoAPresetConfig(
                enabled=True,
                strategy="parallel",
                reference_count=3,
                tier=stage.tier,
                aggregator_tier=stage.tier,
                critic_rounds=0,
                reference_temperature=ref_temp,
                aggregator_temperature=agg_temp,
                max_tokens=max_tokens,
            )
            sub_settings.moa.presets["__chain_step__"] = sub_preset
            # 复用本 orchestrator 的 pool/router,只换 settings
            sub_orch = MoAOrchestrator(model_pool=self.pool, router=self.router)
            sub_orch.settings = sub_settings
            try:
                sub_q = current_messages[-1].get("content", "") if current_messages else ""
                sub_context = current_messages[:-1] if len(current_messages) > 1 else []
                # 把 step 角色 prompt 作为 system message 注入(让模型知道当前 step 的角色)
                role_msg = [{"role": "system", "content": role_prompt}]
                full_context = role_msg + sub_context
                sub_res = await sub_orch.execute(
                    query=sub_q,
                    context=full_context,
                    tools=tools,
                    preset="__chain_step__",
                    critic_rounds=0,
                )
                content = sub_res.final_content or sub_res.aggregated_content
                step_result.output = content
                step_result.cost = sub_res.total_cost
                step_result.latency_ms = sub_res.total_latency_ms
                result.total_cost += sub_res.total_cost
                current_messages = current_messages + [{"role": "assistant", "content": content}]
            finally:
                # 清理 sub_settings 上的临时 preset(隔离作用域,不会污染全局)
                sub_settings.moa.presets.pop("__chain_step__", None)
            result.chain_steps.append(step_result)

        result.aggregated_content = result.chain_steps[-1].output if result.chain_steps else ""
        result.final_content = result.aggregated_content
        result.aggregator_model = "chain"

    # ========== pipeline ==========
    async def _run_pipeline(
        self,
        result: MoAResult,
        messages,
        tools,
        preset_cfg: MoAPresetConfig,
        temperature: float,
        max_tokens: int,
        start: float,
    ):
        result.strategy = "pipeline"
        stages = preset_cfg.stages or []
        if not stages:
            from .config import MoAStageConfig

            stages = [
                MoAStageConfig(name="planner", tier="premium"),
                MoAStageConfig(name="generator", tier="standard"),
                MoAStageConfig(name="evaluator", tier="premium"),
            ]
        current_messages = list(messages)
        for stage in stages:
            tier = self._to_tier(stage.tier)
            ep = self.pool.select_one(tier)
            if not ep:
                continue
            stage_messages = list(current_messages)
            if stage.name == "planner":
                stage_messages = list(messages) + [
                    {
                        "role": "user",
                        "content": "请把用户需求细化成可执行的任务规格(spec)。"
                        "包含:目标、关键步骤、输出格式、约束、验收标准。",
                    }
                ]
            elif stage.name == "evaluator":
                stage_messages = list(current_messages) + [
                    {
                        "role": "user",
                        "content": "请评审上一阶段的输出,若有问题请给出修订版,否则输出'PASS'。",
                    }
                ]
            resp = await self._call_with_fallback(
                ep, stage_messages, tools, temperature, max_tokens
            )
            content = resp["content"]
            result.pipeline_stages.append(
                {
                    "stage": stage.name,
                    "model": resp.get("used_model_id", ep.id),
                    "content_preview": content[:400] + ("..." if len(content) > 400 else ""),
                    "latency_ms": resp.get("latency_ms", 0),
                    "cost": resp["cost"],
                }
            )
            result.total_cost += resp["cost"]
            current_messages = list(current_messages) + [{"role": "assistant", "content": content}]
            if stage.name == "evaluator" and content.strip().upper().startswith("PASS"):
                break
        result.aggregated_content = current_messages[-1].get("content", "")
        result.final_content = result.aggregated_content
        result.aggregator_model = "pipeline"

    # ========== 模型解析(显式 > 动态) ==========
    def _resolve_models(
        self, preset_cfg: MoAPresetConfig, ref_count: int, aggregator_id: str | None
    ) -> tuple[list[ModelEndpoint], ModelEndpoint | None]:
        """从 preset_cfg 解析参考模型和聚合器。
        优先用显式列表,否则动态选。"""
        ref_endpoints: list[ModelEndpoint] = []
        # 显式列表
        for ref_cfg in preset_cfg.reference_models:
            ep = self._pick_endpoint_for_ref(ref_cfg)
            if ep:
                ref_endpoints.append(ep)
        # 数量不足时动态补
        if len(ref_endpoints) < ref_count:
            d = self.router.route_for_moa(
                query="placeholder",
                reference_count=ref_count - len(ref_endpoints),
                aggregator_tier=ModelTier(preset_cfg.aggregator_tier),
            )
            for ep in d[0]:
                if ep and ep.id not in [e.id for e in ref_endpoints]:
                    ref_endpoints.append(ep)
        # aggregator
        aggregator = None
        if aggregator_id and self.pool.endpoints.get(aggregator_id):
            aggregator = self.pool.endpoints[aggregator_id]
        if not aggregator:
            aggregator = self.pool.select_one(ModelTier(preset_cfg.aggregator_tier))
        return ref_endpoints, aggregator

    async def _run_strategy_based(
        self,
        result: MoAResult,
        messages,
        tools,
        preset_cfg: MoAPresetConfig,
        strategy_name: str,
        ref_n: int,
        aggregator_id: str | None,
        ref_temp: float,
        agg_temp: float,
        max_tokens: int,
        start: float,
    ):
        """Task #45: Run MOA with a pluggable strategy for model selection."""
        from .moa_strategies import get_strategy, build_candidates
        from .benchmark import get_benchmark_engine, get_capability_probe
        from .health import get_health_checker

        strat = get_strategy(strategy_name)
        if strat is None:
            result.strategy = "parallel"
            await self._run_parallel(
                result, messages, tools, preset_cfg,
                ref_n, aggregator_id, preset_cfg.critic_rounds,
                ref_temp, agg_temp, max_tokens, start,
            )
            return

        result.strategy = strategy_name

        bench = get_benchmark_engine()
        cap = get_capability_probe()
        hc = get_health_checker()

        candidates = build_candidates(
            model_pool=self.pool,
            benchmark_engine=bench,
            capability_probe=cap,
            health_checker=hc,
        )

        if not candidates:
            raise RuntimeError(f"no candidates available for strategy '{strategy_name}'")

        # Determine context for capability_aware strategy
        context = {}
        if strategy_name == "capability_aware":
            query_text = messages[-1].get("content", "").lower() if messages else ""
            if any(kw in query_text for kw in ("code", "function", "python", "javascript")):
                context["task_type"] = "code_generation"
            elif any(kw in query_text for kw in ("poem", "story", "creative", "write a")):
                context["task_type"] = "creative_writing"
            elif any(kw in query_text for kw in ("json", "data", "analyze", "summary")):
                context["task_type"] = "data_analysis"
            elif any(kw in query_text for kw in ("translate", "french", "spanish", "japanese")):
                context["task_type"] = "multilingual"
            else:
                context["task_type"] = "reasoning"

        selected_ids = strat.select_models(candidates, context=context, n=ref_n or 3)

        ref_endpoints = []
        for eid in selected_ids:
            ep = self.pool.endpoints.get(eid)
            if ep:
                ref_endpoints.append(ep)

        if not ref_endpoints:
            ref_endpoints, _ = self._resolve_models(preset_cfg, ref_n, aggregator_id)

        if not ref_endpoints:
            raise RuntimeError(f"strategy '{strategy_name}' selected no models")

        aggregator_ep = None
        if aggregator_id and self.pool.endpoints.get(aggregator_id):
            aggregator_ep = self.pool.endpoints[aggregator_id]
        if not aggregator_ep:
            aggregator_ep = ref_endpoints[0]
        result.aggregator_model = aggregator_ep.id

        ref_results = await self._run_references(ref_endpoints, messages, ref_temp, max_tokens)
        result.references = ref_results

        ok_count = sum(1 for r in ref_results if r.success)
        if ok_count == 0:
            ref_results = [
                ReferenceResult(
                    model_id="system",
                    content="[all reference models failed, answer based on general knowledge]",
                    success=True,
                )
            ]
            result.references = ref_results

        # Build aligned response and endpoint_id lists (only successful results)
        ref_pairs = [(r.model_id, r.content) for r in ref_results if r.success]
        ref_contents = [c for _, c in ref_pairs]
        ref_endpoint_ids = [eid for eid, _ in ref_pairs]
        selected_candidates = [c for c in candidates if c.endpoint_id in selected_ids]

        try:
            strat_aggregated = strat.aggregate(ref_contents, selected_candidates, selected_ids=ref_endpoint_ids)
        except Exception:
            strat_aggregated = ""

        if strat_aggregated and strat_aggregated.strip():
            result.aggregated_content = strat_aggregated
        else:
            agg_messages = self._build_aggregator_messages(messages, ref_results)
            agg_resp = await self._call_with_fallback(
                aggregator_ep, agg_messages, tools, agg_temp, max_tokens
            )
            result.aggregated_content = agg_resp["content"]
            result.total_cost += agg_resp["cost"]
            if agg_resp.get("used_model_id"):
                result.aggregator_model = agg_resp["used_model_id"]
            result.fallback_used = agg_resp.get("fallback_used", False)

        consensus = self._calculate_consensus(ref_results)
        result.consensus_score = consensus

        if strategy_name == "adaptive_ensemble" and hasattr(strat, "update_weights"):
            for r in ref_results:
                qs = 1.0 if r.success else 0.0
                if r.success and len(r.content) > 200:
                    qs = 1.2
                strat.update_weights(r.model_id, r.success, qs)

        result.final_content = result.aggregated_content
        result.total_cost += sum(r.cost for r in ref_results)

    def _pick_endpoint_for_ref(self, ref_cfg: ReferenceModelConfig) -> ModelEndpoint | None:
        """根据 ReferenceModelConfig 选一个 endpoint"""
        if ref_cfg.id and self.pool.endpoints.get(ref_cfg.id):
            return self.pool.endpoints[ref_cfg.id]
        # 按 provider/model 模糊匹配
        if ref_cfg.provider or ref_cfg.model:
            for ep in self.pool.endpoints.values():
                if not ep.is_available:
                    continue
                if ref_cfg.provider and ep.config.provider != ref_cfg.provider:
                    continue
                if ref_cfg.model and ep.config.model != ref_cfg.model:
                    continue
                return ep
        # 没有约束,返回 None 让上层走动态
        return None

    # ========== 通用调用 ==========
    async def _run_references(self, refs, messages, temperature, max_tokens):
        """并行调用多个参考模型(默认 messages)"""
        tasks = [
            self._call_one_ref_with_messages(ep, messages, temperature, max_tokens, role="")
            for ep in refs
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out = []
        for ep, r in zip(refs, results, strict=False):
            if isinstance(r, Exception):
                out.append(
                    ReferenceResult(
                        model_id=ep.id, content="", success=False, error=str(r), role=""
                    )
                )
            else:
                out.append(r)
        return out

    async def _call_one_ref(self, ep, messages, temperature, max_tokens):
        return await self._call_one_ref_with_messages(
            ep, messages, temperature, max_tokens, role=""
        )

    async def _call_one_ref_with_messages(
        self, ep, messages, temperature, max_tokens, role: str = ""
    ) -> ReferenceResult:
        """调用单个参考模型 + 自动超时 + 异常处理(返回 ReferenceResult)"""
        start = time.time()
        try:
            resp = await asyncio.wait_for(
                self.pool.call(
                    ep.id,
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=None,
                    max_retries=2,
                ),
                timeout=self.settings.moa.reference_timeout,
            )
            return ReferenceResult(
                model_id=ep.id,
                content=resp.content,
                success=True,
                role=role,
                latency_ms=(time.time() - start) * 1000,
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                cost=resp.cost,
            )
        except asyncio.TimeoutError:
            return ReferenceResult(
                model_id=ep.id,
                content="",
                success=False,
                role=role,
                error=f"timeout after {self.settings.moa.reference_timeout}s",
            )
        except Exception as e:
            return ReferenceResult(
                model_id=ep.id, content="", success=False, role=role, error=str(e)
            )

    def _build_aggregator_messages(self, original, ref_results):
        messages = [m for m in original if m.get("role") == "system"]
        # 自定义 prompt(用户可热更)
        sys_prompt = get_prompt("aggregator")
        messages.append({"role": "system", "content": sys_prompt})
        user_q = next((m.get("content", "") for m in original if m.get("role") == "user"), "")
        ref_text = "\n\n".join(
            [
                f"【参考 {i + 1} — 模型: {r.model_id}】\n{r.content}"
                if r.success
                else f"【参考 {i + 1} — 模型: {r.model_id}】(调用失败: {r.error})"
                for i, r in enumerate(ref_results)
            ]
        )
        messages.append(
            {
                "role": "user",
                "content": f"# 原始问题\n{user_q}\n\n# 多模型参考回答\n{ref_text}\n\n# 你的任务\n请综合以上参考,给出最终答案。在最终答案开头用一句话说明你的综合判断依据。",
            }
        )
        return messages

    async def _call_with_fallback(self, ep, messages, tools, temperature, max_tokens):
        chain = [ep] + self.pool.get_fallback_chain(ep.id, 3)
        last_err = None
        for attempt, cur in enumerate(chain):
            if not cur.is_available:
                continue
            try:
                resp = await asyncio.wait_for(
                    self.pool.call(
                        cur.id,
                        messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        tools=tools,
                        max_retries=1,
                    ),
                    timeout=self.settings.moa.aggregator_timeout,
                )
                return {
                    "content": resp.content,
                    "cost": resp.cost,
                    "latency_ms": resp.latency_ms,
                    "used_model_id": cur.id,
                    "fallback_used": (cur.id != ep.id),
                    "prompt_tokens": resp.prompt_tokens,
                    "completion_tokens": resp.completion_tokens,
                }
            except Exception as e:
                last_err = e
                logger.warning("call %s failed: %s", cur.id, e)
                await asyncio.sleep(min(2**attempt, 8))
        raise RuntimeError(f"all fallbacks failed: {last_err}") from last_err

    async def _run_critic(
        self, current_content, ref_results, aggregator_ep, temperature, max_tokens
    ):
        start = time.time()
        critic_pool = [
            e for e in self.pool.endpoints.values() if e.is_available and e.id != aggregator_ep.id
        ]
        if not critic_pool:
            critic_pool = [aggregator_ep]
        critic_pool.sort(key=lambda e: -e.tier.rank)
        critic_ep = next((e for e in critic_pool if e.tier.rank >= 2), critic_pool[0])
        critic_messages = [
            {"role": "system", "content": get_prompt("critic")},
            {
                "role": "user",
                "content": (
                    f"# 待审查的答案\n{current_content}\n\n"
                    f"# 多模型参考(原始)\n"
                    + "\n\n".join(
                        f"【{r.model_id}】\n{r.content[:800]}" for r in ref_results if r.success
                    )
                    + "\n\n请按系统要求输出 JSON 评审结果。"
                ),
            },
        ]
        try:
            resp = await asyncio.wait_for(
                self.pool.call(
                    critic_ep.id, critic_messages, temperature=0.3, max_tokens=1500, max_retries=1
                ),
                timeout=self.settings.moa.aggregator_timeout,
            )
            content = resp.content.strip()
            issues, suggestions = self._parse_critic_output(content)
            return CriticResult(
                model_id=critic_ep.id,
                issues=issues,
                suggestions=suggestions,
                refined_content=content,
                success=True,
                latency_ms=(time.time() - start) * 1000,
                cost=resp.cost,
            )
        except Exception as e:
            return CriticResult(
                model_id=critic_ep.id,
                success=False,
                error=str(e),
                latency_ms=(time.time() - start) * 1000,
            )

    @staticmethod
    def _parse_critic_output(content):
        issues, suggestions = [], []
        m = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
        if not m:
            m = re.search(r"(\{[^{}]*\"issues\".*?\})", content, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(1))
                if isinstance(obj.get("issues"), list):
                    issues = [str(x) for x in obj["issues"]]
                if isinstance(obj.get("suggestions"), list):
                    suggestions = [str(x) for x in obj["suggestions"]]
                return issues, suggestions
            except Exception:
                pass
        for line in content.splitlines():
            ls = line.strip()
            if not ls:
                continue
            if ls.startswith(("- ", "* ", "• ")):
                item = ls[2:].strip()
                low = item.lower()
                if any(k in low for k in ("建议", "suggest", "应当", "应该", "可以")):
                    suggestions.append(item)
                else:
                    issues.append(item)
            elif ls.startswith(("问题", "Issue")):
                issues.append(ls)
            elif ls.startswith(("建议", "Suggest")):
                suggestions.append(ls)
        return issues, suggestions

    def _calculate_consensus(self, ref_results):
        valid = [r for r in ref_results if r.success and r.content]
        if len(valid) < 2:
            return 0.5
        kw_lists = [self._extract_keywords(r.content) for r in valid]
        total, count = 0.0, 0
        for i in range(len(kw_lists)):
            for j in range(i + 1, len(kw_lists)):
                a, b = kw_lists[i], kw_lists[j]
                if not a or not b:
                    continue
                inter = len(a & b)
                union = len(a | b)
                if union > 0:
                    total += inter / union
                    count += 1
        return total / count if count > 0 else 0.5

    @staticmethod
    def _extract_keywords(text):
        words = re.findall(r"[一-鿿]+|[a-zA-Z][a-zA-Z0-9_]+", text)
        return {w.lower() for w in words if len(w) > 1}

    @staticmethod
    def _to_tier(t):
        try:
            return ModelTier(t)
        except Exception:
            return ModelTier.STANDARD

    # ========== 评估端点(横向对比) ==========
    async def evaluate(
        self,
        query: str,
        candidates: list[str],
        reference_answer: str | None = None,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        """让 critic 模型给一组候选答案打分(0-100),返回 JSON 对比"""
        if not self.pool.endpoints:
            raise RuntimeError("no available models")
        # 优先用 aggregator_tier 的模型做 judge
        judge_ep = self.pool.select_one(ModelTier.PREMIUM)
        if not judge_ep:
            judge_ep = list(self.pool.endpoints.values())[0]

        results = []
        for cand_id in candidates:
            cand = self.pool.endpoints.get(cand_id)
            if not cand:
                continue
            # 直接调出答案
            try:
                resp = await self.pool.call(
                    cand_id,
                    [{"role": "user", "content": query}],
                    temperature=temperature,
                    max_tokens=2048,
                )
                results.append(
                    {
                        "model": cand_id,
                        "answer": resp.content,
                        "cost": resp.cost,
                        "latency_ms": resp.latency_ms,
                        "tokens": resp.total_tokens,
                    }
                )
            except Exception as e:
                results.append({"model": cand_id, "error": str(e)})

        # 让 judge 评分
        eval_prompt = """请对以下模型对同一问题的回答做横向对比评分。
评分维度(各 0-25,合计 0-100):
- 准确性:事实是否正确,有无错误?
- 完整性:是否覆盖了用户问题的所有关键点?
- 逻辑性:推理是否清晰连贯?
- 实用性:对用户有无实际帮助?

参考标准答案:
{}

各模型回答:
{}

请以 JSON 格式输出评分,格式:
```json
{{
  "scores": [
    {{"model": "模型ID", "accuracy": 0-25, "completeness": 0-25, "logic": 0-25, "practicality": 0-25, "total": 0-100, "comment": "简短评语"}},
    ...
  ],
  "winner": "最佳模型ID",
  "ranking": ["模型1", "模型2", ...]
}}
```""".format(
            reference_answer or "(无)",
            "\n\n".join(
                f"【{r['model']}】\n{r.get('answer', r.get('error', ''))}" for r in results
            ),
        )

        judge_resp = await self.pool.call(
            judge_ep.id,
            [{"role": "user", "content": eval_prompt}],
            temperature=0.2,
            max_tokens=2000,
        )
        scores_text = judge_resp.content
        # 解析 JSON
        scores_json = None
        m = re.search(r"```json\s*(\{.*?\})\s*```", scores_text, re.DOTALL)
        if m:
            with contextlib.suppress(Exception):
                scores_json = json.loads(m.group(1))
        return {
            "judge_model": judge_ep.id,
            "judge_cost": judge_resp.cost,
            "candidates": results,
            "scores": scores_json,
            "scores_raw": scores_text,
        }

    # ========== Layered MoA(论文 §2.2:多层 L1→L2→L3 真分层架构) ==========
    async def _run_layered(
        self,
        result: MoAResult,
        messages,
        tools,
        preset_cfg: MoAPresetConfig,
        aggregator_id: str | None,
        ref_temp: float,
        agg_temp: float,
        max_tokens: int,
        start: float,
    ):
        """Layered MoA:Together AI 论文核心架构。
        L1: 并行调用 reference_models,得到 n1 个回答
        L2: 并行调用第二层模型(可与 L1 不同),每个接收 L1 全部输出作为辅助信息
        L3: 单个 aggregator 接收 L2 全部输出,生成最终答案
        层数可配置 preset_cfg.layer_count(默认 3)。
        """
        result.strategy = "layered"
        layer_count = getattr(preset_cfg, "layer_count", 3) or 3
        layer_count = max(2, min(layer_count, 6))  # 2-6 层

        # L1: 选参考模型
        l1_eps, _ = self._resolve_models(preset_cfg, preset_cfg.reference_count, aggregator_id)
        if not l1_eps:
            raise RuntimeError("no available model for layered L1")

        prev_results: list[ReferenceResult] = []
        prev_layers_outputs: list[list[ReferenceResult]] = []  # 每层输出
        current_eps = l1_eps

        for layer_idx in range(1, layer_count + 1):
            is_final = layer_idx == layer_count
            layer_outputs: list[ReferenceResult] = []

            if is_final and aggregator_id and self.pool.endpoints.get(aggregator_id):
                # 最后一层用显式 aggregator(单个)
                current_eps = [self.pool.endpoints[aggregator_id]]
            elif is_final:
                # 最后一层从前面层选最强的做 aggregator
                tier_order = {"premium": 3, "standard": 2, "economy": 1, "free": 0}
                current_eps.sort(
                    key=lambda e: (
                        -tier_order.get(
                            e.tier.value if hasattr(e.tier, "value") else str(e.tier), 0
                        )
                    )
                )
                current_eps = current_eps[:1]

            # 并行调用当前层所有模型
            tasks = []
            for ep in current_eps:
                if layer_idx == 1:
                    # L1: 直接用原 messages
                    layer_msgs = messages
                else:
                    # L2+: 把所有前层输出当作 auxiliary information 拼接
                    # (论文公式 y_i = ⊕[A_{i,j}(x_i)] + x_1)
                    aux_lines = []
                    for prev_layer_idx, prev_outs in enumerate(prev_layers_outputs, 1):
                        for r in prev_outs:
                            if r.success:
                                aux_lines.append(
                                    f"【L{prev_layer_idx} - {r.model_id}】\n{r.content}"
                                )
                    aux_text = "\n\n".join(aux_lines) if aux_lines else ""
                    # 注入 system prompt 引导
                    synth_prompt = get_prompt("aggregator")
                    user_q = next(
                        (m.get("content", "") for m in messages if m.get("role") == "user"), ""
                    )
                    layer_msgs = [
                        {"role": "system", "content": synth_prompt},
                        {
                            "role": "user",
                            "content": f"用户问题:\n{user_q}\n\n"
                            f"前面 {layer_idx - 1} 层所有模型的回答:\n{aux_text}\n\n"
                            f"请综合这些回答,生成你的 L{layer_idx} 层回答。",
                        },
                    ]

                tasks.append(
                    self._call_one_ref_with_messages(
                        ep,
                        layer_msgs,
                        agg_temp if is_final else ref_temp,
                        max_tokens,
                        role=f"L{layer_idx}",
                    )
                )

            responses = await asyncio.gather(*tasks, return_exceptions=True)
            for ep, r in zip(current_eps, responses, strict=False):
                if isinstance(r, Exception):
                    layer_outputs.append(
                        ReferenceResult(
                            model_id=ep.id,
                            content="",
                            success=False,
                            error=str(r),
                            role=f"L{layer_idx}",
                            tier=str(ep.tier.value),
                        )
                    )
                else:
                    # r is ReferenceResult
                    layer_outputs.append(
                        ReferenceResult(
                            model_id=ep.id,
                            content=r.content,
                            success=True,
                            prompt_tokens=getattr(r, "prompt_tokens", 0),
                            completion_tokens=getattr(r, "completion_tokens", 0),
                            cost=getattr(r, "cost", 0.0),
                            latency_ms=getattr(r, "latency_ms", 0.0),
                            role=f"L{layer_idx}",
                            tier=str(ep.tier.value),
                        )
                    )

            prev_results.extend(layer_outputs)
            prev_layers_outputs.append(layer_outputs)
            result.total_cost += sum(r.cost for r in layer_outputs)
            result.references.extend(layer_outputs)

            # 记录每层输出
            if not hasattr(result, "_layer_outputs"):
                result._layer_outputs = {}
            result.layer_outputs[f"L{layer_idx}"] = [
                {"model": r.model_id, "content": r.content[:500], "cost": r.cost}
                for r in layer_outputs
            ]

            # 下一层模型选择:复用 L1 模型(reuse allowed,论文允许)
            # 取 L1 的同一批模型(避免无限扩展)
            if not is_final and layer_idx == 1:
                current_eps = l1_eps  # L2 用 L1 同批模型
            # 最后层之后退出循环

        # 最终答案 = 最后一层(L_layer_count)第一个模型输出
        if prev_layers_outputs and prev_layers_outputs[-1]:
            final = prev_layers_outputs[-1][0]
            result.final_content = final.content
            result.aggregated_content = final.content
            result.aggregator_model = final.model_id
            result.fallback_used = False

        result.layers_count = layer_count
        logger.info(
            "Layered MoA done: %d layers, %d total refs, $%.4f",
            layer_count,
            len(prev_results),
            result.total_cost,
        )

    # ========== Single-Proposer(论文 §3.3 Table 3) ==========
    async def _run_single_proposer(
        self,
        result: MoAResult,
        messages,
        tools,
        preset_cfg: MoAPresetConfig,
        aggregator_id: str | None,
        ref_temp: float,
        max_tokens: int,
        start: float,
    ):
        """Single-Proposer setting:同一个模型高温采样多次(reference_count 次)
        论文发现:Multi-Proposer > Single-Proposer > Single。
        """
        result.strategy = "single_proposer"
        # 选单一 proposer
        if preset_cfg.reference_models and len(preset_cfg.reference_models) == 1:
            proposer_ep = self._pick_endpoint_for_ref(preset_cfg.reference_models[0])
        else:
            # 自动选:取 tier 最高的
            tier_order = {"premium": 3, "standard": 2, "economy": 1, "free": 0}
            pool_sorted = sorted(
                [e for e in self.pool.endpoints.values() if e.is_available],
                key=lambda e: (
                    -tier_order.get(e.tier.value if hasattr(e.tier, "value") else str(e.tier), 0)
                ),
            )
            proposer_ep = pool_sorted[0] if pool_sorted else None
        if not proposer_ep:
            raise RuntimeError("no available model for single_proposer")

        n_samples = preset_cfg.reference_count
        # 论文用温度 0.7
        sample_temp = max(ref_temp, 0.7)
        tasks = [
            self._call_one_ref_with_messages(
                proposer_ep, messages, sample_temp, max_tokens, role="single_proposer"
            )
            for _ in range(n_samples)
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        ref_results = []
        for i, r in enumerate(responses):
            if isinstance(r, Exception):
                ref_results.append(
                    ReferenceResult(
                        model_id=proposer_ep.id,
                        content="",
                        success=False,
                        error=str(r),
                        role=f"sample{i + 1}",
                    )
                )
            else:
                ref_results.append(
                    ReferenceResult(
                        model_id=proposer_ep.id,
                        content=r.content,
                        success=True,
                        prompt_tokens=getattr(r, "prompt_tokens", 0),
                        completion_tokens=getattr(r, "completion_tokens", 0),
                        cost=getattr(r, "cost", 0.0),
                        latency_ms=getattr(r, "latency_ms", 0.0),
                        role=f"sample{i + 1}",
                        tier=str(proposer_ep.tier.value),
                    )
                )
        result.references.extend(ref_results)
        result.total_cost += sum(r.cost for r in ref_results)

        # 复用 _run_parallel 后续逻辑:聚合器接收 N 个 sample
        # 但实际 Single-Proposer 论文也用 aggregator 聚合多个采样
        agg_ep = self.pool.endpoints.get(aggregator_id) if aggregator_id else None
        if not agg_ep:
            agg_ep = self.pool.select_one(ModelTier(preset_cfg.aggregator_tier))
        if not agg_ep:
            agg_ep = proposer_ep
        result.aggregator_model = agg_ep.id

        # 聚合(same prompt as parallel)
        agg_messages = self._build_aggregator_messages(messages, ref_results)
        agg_resp = await self._call_with_fallback(
            agg_ep, agg_messages, tools, preset_cfg.aggregator_temperature, max_tokens
        )
        result.final_content = agg_resp["content"]
        result.aggregated_content = agg_resp["content"]
        result.total_cost += agg_resp["cost"]
        if agg_resp.get("used_model_id"):
            result.aggregator_model = agg_resp["used_model_id"]
        result.fallback_used = agg_resp.get("fallback_used", False)

    # ========== LLM Ranker baseline(论文 §3.3 Figure 4) ==========
    async def _run_ranker(
        self,
        result: MoAResult,
        messages,
        tools,
        preset_cfg: MoAPresetConfig,
        aggregator_id: str | None,
        ref_temp: float,
        max_tokens: int,
        start: float,
    ):
        """LLM Ranker baseline(论文 §3.3 Figure 4):
        并行调用参考模型,然后让一个 strong aggregator **选择最佳**而不是生成新答案。
        用于对照 MoA 是否真的"合成"还是只是"选最佳"。
        """
        result.strategy = "ranker"
        # 并行调用所有 reference
        ref_eps, _ = self._resolve_models(preset_cfg, preset_cfg.reference_count, aggregator_id)
        if not ref_eps:
            raise RuntimeError("no available model for ranker")
        tasks = [
            self._call_one_ref_with_messages(
                ep, messages, ref_temp, max_tokens, role="ranker_candidate"
            )
            for ep in ref_eps
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        ref_results = []
        for ep, r in zip(ref_eps, responses, strict=False):
            if isinstance(r, Exception):
                ref_results.append(
                    ReferenceResult(
                        model_id=ep.id,
                        content="",
                        success=False,
                        error=str(r),
                        role="ranker_candidate",
                    )
                )
            else:
                ref_results.append(
                    ReferenceResult(
                        model_id=ep.id,
                        content=r.content,
                        success=True,
                        prompt_tokens=getattr(r, "prompt_tokens", 0),
                        completion_tokens=getattr(r, "completion_tokens", 0),
                        cost=getattr(r, "cost", 0.0),
                        latency_ms=getattr(r, "latency_ms", 0.0),
                        role="ranker_candidate",
                        tier=str(ep.tier.value),
                    )
                )
        result.references.extend(ref_results)
        result.total_cost += sum(r.cost for r in ref_results)

        # Ranker prompt:让 aggregator **选择**而非合成
        agg_ep = self.pool.endpoints.get(aggregator_id) if aggregator_id else None
        if not agg_ep:
            agg_ep = self.pool.select_one(ModelTier(preset_cfg.aggregator_tier))
        if not agg_ep:
            agg_ep = ref_eps[0]
        result.aggregator_model = agg_ep.id

        user_q = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
        cands_text = "\n\n".join(
            f"=== Candidate {i + 1} (model {r.model_id}) ===\n{r.content}"
            for i, r in enumerate(ref_results)
            if r.success
        )
        ranker_prompt = f"""You are a ranker. Your task is to PICK the best response from the candidates below,
NOT to generate a new response.

# Original question
{user_q}

# Candidates
{cands_text}

# Output JSON
{{
  "ranking": ["candidate_id_1", "candidate_id_2", ...],
  "winner": "best_candidate_id",
  "reasoning": "why the winner is best (1-2 sentences)"
}}

Ranking should be from best to worst. "winner" must equal ranking[0].
"""
        ranker_resp = await self._call_with_fallback(
            agg_ep, [{"role": "user", "content": ranker_prompt}], tools, 0.2, max_tokens
        )
        result.total_cost += ranker_resp["cost"]
        if ranker_resp.get("used_model_id"):
            result.aggregator_model = ranker_resp["used_model_id"]

        # 解析 ranking JSON
        scores_json = None
        m = re.search(r"```json\s*(\{.*?\})\s*```", ranker_resp["content"], re.DOTALL)
        if m:
            with contextlib.suppress(Exception):
                scores_json = json.loads(m.group(1))
        else:
            m2 = re.search(r"\{[\s\S]*\}", ranker_resp["content"])
            if m2:
                with contextlib.suppress(Exception):
                    scores_json = json.loads(m2.group(0))

        # 找出 winner 对应的内容
        winner_idx = -1
        if scores_json and "winner" in scores_json:
            try:
                winner_idx = int(str(scores_json["winner"]).split("_")[-1]) - 1
            except Exception:
                winner_idx = -1

        if 0 <= winner_idx < len(ref_results) and ref_results[winner_idx].success:
            result.final_content = ref_results[winner_idx].content
            result.aggregated_content = ref_results[winner_idx].content
            result.winner_model = ref_results[winner_idx].model_id
        elif ref_results and ref_results[0].success:
            result.final_content = ref_results[0].content
            result.aggregated_content = ref_results[0].content
            result.winner_model = ref_results[0].model_id

        result.ranker_output = scores_json or {"raw": ranker_resp["content"]}

    # ========== 相似度评分(论文 §3.3 Spearman correlation) ==========
    async def compute_similarity(
        self, query: str, candidate_a: str, candidate_b: str, model_id: str | None = None
    ) -> dict[str, Any]:
        """计算两个候选答案之间的多维度相似度(BLEU n-gram / Levenshtein / TF-IDF)
        用于论文 §3.3 Figure 4 的 Spearman correlation 分析。
        """
        try:
            import math
            from collections import Counter

            def tokenize(text):
                # 中英文混合分词:简单的字符级 + 词级
                text = text.lower().strip()
                # 英文按空格,中文按字符
                tokens = []
                cur = ""
                for ch in text:
                    if ch.isspace():
                        if cur:
                            tokens.append(cur)
                            cur = ""
                    elif "\u4e00" <= ch <= "\u9fff":
                        if cur:
                            tokens.append(cur)
                            cur = ""
                        tokens.append(ch)
                    else:
                        cur += ch
                if cur:
                    tokens.append(cur)
                return tokens

            def bleu_ngram(ref_tokens, hyp_tokens, n):
                ref_ngrams = Counter(
                    tuple(ref_tokens[i : i + n]) for i in range(len(ref_tokens) - n + 1)
                )
                hyp_ngrams = Counter(
                    tuple(hyp_tokens[i : i + n]) for i in range(len(hyp_tokens) - n + 1)
                )
                if not hyp_ngrams:
                    return 0.0
                clipped = {
                    ngram: min(count, ref_ngrams.get(ngram, 0))
                    for ngram, count in hyp_ngrams.items()
                }
                precision = sum(clipped.values()) / max(1, sum(hyp_ngrams.values()))
                # brevity penalty
                bp = min(1.0, math.exp(1 - len(ref_tokens) / max(1, len(hyp_tokens))))
                return bp * precision

            def levenshtein(s1, s2):
                if len(s1) < len(s2):
                    return levenshtein(s2, s1)
                if not s2:
                    return len(s1)
                prev = list(range(len(s2) + 1))
                for i, c1 in enumerate(s1):
                    cur = [i + 1]
                    for j, c2 in enumerate(s2):
                        ins = prev[j + 1] + 1
                        dele = prev[j] + 1
                        sub = prev[j] + (c1 != c2)
                        cur.append(min(ins, dele, sub))
                    prev = cur
                return prev[-1]

            def tfidf_cosine(a_tokens, b_tokens, vocab):
                def tf(tokens):
                    c = Counter(tokens)
                    return {t: c[t] / max(1, len(tokens)) for t in set(tokens)}

                ta, tb = tf(a_tokens), tf(b_tokens)
                dot = sum(ta.get(t, 0) * tb.get(t, 0) for t in vocab)
                na = math.sqrt(sum(v * v for v in ta.values()))
                nb = math.sqrt(sum(v * v for v in tb.values()))
                if na == 0 or nb == 0:
                    return 0.0
                return dot / (na * nb)

            tokens_a = tokenize(candidate_a)
            tokens_b = tokenize(candidate_b)

            bleu3 = bleu_ngram(tokens_a, tokens_b, 3)
            bleu4 = bleu_ngram(tokens_a, tokens_b, 4)
            bleu5 = bleu_ngram(tokens_a, tokens_b, 5)
            lev_dist = levenshtein(candidate_a, candidate_b)
            lev_sim = 1.0 - lev_dist / max(len(candidate_a), len(candidate_b), 1)
            vocab = set(tokens_a) | set(tokens_b)
            tfidf_sim = tfidf_cosine(tokens_a, tokens_b, vocab)

            # 让 LLM 做语义相似度评分(可选)
            semantic = None
            if model_id and self.pool.endpoints.get(model_id):
                self.pool.endpoints[model_id]
                judge_p = f"""比较以下两个回答对问题 "{query}" 的语义相似度(0-1)。
回答 A:
{candidate_a[:1500]}

回答 B:
{candidate_b[:1500]}

只输出 JSON: {{"score": 0.85, "reason": "..."}}"""
                try:
                    resp = await asyncio.wait_for(
                        self.pool.call(
                            model_id,
                            [{"role": "user", "content": judge_p}],
                            temperature=0.1,
                            max_tokens=200,
                        ),
                        timeout=20,
                    )
                    m = re.search(r"\{[\s\S]*?\}", resp.content)
                    if m:
                        try:
                            d = json.loads(m.group(0))
                            semantic = d.get("score")
                        except Exception:
                            pass
                except Exception:
                    pass

            return {
                "bleu3": round(bleu3, 4),
                "bleu4": round(bleu4, 4),
                "bleu5": round(bleu5, 4),
                "levenshtein_similarity": round(lev_sim, 4),
                "tfidf_cosine": round(tfidf_sim, 4),
                "semantic_score": semantic,
                "len_a": len(candidate_a),
                "len_b": len(candidate_b),
            }
        except Exception as e:
            return {"error": str(e)}

    # ========== FLASK 多维评分(论文 §3.2 FLASK 12 skill-specific scores) ==========
    FLASK_RUBRIC = [
        ("robustness", "面对对抗输入/异常是否仍然稳定"),
        ("correctness", "事实和逻辑是否正确"),
        ("efficiency", "是否简洁、不啰嗦,直奔主题"),
        ("factuality", "事实是否准确、是否有幻觉"),
        ("commonsense", "是否符合常识"),
        ("insightfulness", "是否有深刻洞见,而不是泛泛而谈"),
        ("completeness", "是否覆盖了所有关键点"),
        ("metacognition", "是否意识到自己的局限和不确定性"),
        ("conciseness", "是否精炼,无冗余"),
        ("readability", "排版/语法/可读性"),
        ("harmlessness", "是否安全、无害"),
        ("overall", "综合总体评分"),
    ]

    async def flask_score(
        self,
        query: str,
        response: str,
        reference: str | None = None,
        judge_model: str | None = None,
    ) -> dict[str, Any]:
        """FLASK-style 多维评分(论文 §3.2):12 维评分
        用一个 judge 模型分别对每个维度打分(1-5)。
        返回 dict: {dimension: {score: int, reason: str}}
        """
        # 选 judge 模型
        if judge_model and self.pool.endpoints.get(judge_model):
            judge_ep = self.pool.endpoints[judge_model]
        else:
            tier_order = {"premium": 3, "standard": 2, "economy": 1, "free": 0}
            pool_sorted = sorted(
                [e for e in self.pool.endpoints.values() if e.is_available],
                key=lambda e: (
                    -tier_order.get(e.tier.value if hasattr(e.tier, "value") else str(e.tier), 0)
                ),
            )
            judge_ep = pool_sorted[0] if pool_sorted else None
        if not judge_ep:
            return {"error": "no judge model available"}

        # 用 LLM 一次性评 12 维
        rubric_text = "\n".join(
            f"{i + 1}. **{name}** — {desc}: 1(差) ~ 5(优秀)"
            for i, (name, desc) in enumerate(self.FLASK_RUBRIC)
        )
        prompt = f"""你是 FLASK 风格的多维评分员。对以下模型回答,按 12 个维度打分(1-5)。

# 问题
{query}

# 模型回答
{response[:3000]}

{"# 参考回答" + chr(10) + reference[:2000] if reference else ""}

# 评分维度
{rubric_text}

# 输出 JSON(每个维度给出 score + 一句话 reason)
```json
{{
  "robustness": {{"score": 4, "reason": "..."}},
  "correctness": {{"score": 5, "reason": "..."}},
  ...
  "overall": {{"score": 4, "reason": "..."}}
}}
```

只输出 JSON。
"""
        try:
            resp = await asyncio.wait_for(
                self.pool.call(
                    judge_ep.id,
                    [{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=2500,
                ),
                timeout=60,
            )
            scores_raw = resp.content
            scores = {}
            m = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", scores_raw)
            if m:
                with contextlib.suppress(Exception):
                    scores = json.loads(m.group(1))
            if not scores:
                m2 = re.search(r"\{[\s\S]*\}", scores_raw)
                if m2:
                    with contextlib.suppress(Exception):
                        scores = json.loads(m2.group(0))

            # 归一化为 0-100
            normalized = {}
            total = 0
            n = 0
            for dim, _ in self.FLASK_RUBRIC:
                v = scores.get(dim, {})
                if isinstance(v, dict):
                    s = v.get("score")
                    reason = v.get("reason", "")
                else:
                    s = v
                    reason = ""
                if isinstance(s, (int, float)):
                    normalized[dim] = {
                        "score_1_5": s,
                        "score_0_100": round(s * 20, 1),
                        "reason": reason,
                    }
                    total += s
                    n += 1
            avg = round(total / n, 2) if n else 0
            return {
                "judge_model": judge_ep.id,
                "judge_cost": resp.cost,
                "scores": normalized,
                "average_1_5": avg,
                "average_0_100": round(avg * 20, 1),
                "raw_response": scores_raw[:500],
            }
        except Exception as e:
            return {"error": str(e), "judge_model": judge_ep.id if judge_ep else None}


_moa: MoAOrchestrator | None = None


def get_moa() -> MoAOrchestrator:
    global _moa
    if _moa is None:
        _moa = MoAOrchestrator()
    return _moa
