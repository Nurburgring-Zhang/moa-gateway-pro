"""Capability endpoints — /v1/capability/* (Waves 1-13).

All capability endpoints are stateless utility functions exposed via the gateway.
Each implements a specific AI orchestration primitive.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_admin, require_api_key
from ..storage import get_storage
from ..req_models import *  # noqa: F403,F401
from .._helpers import err_500

logger = logging.getLogger(__name__)

router = APIRouter(tags=["capability"])

# ========== v1.5 Capability Endpoints (从 10 项目迁移) ==========
@router.post("/v1/capability/secret-scan")
async def capability_secret_scan(
    body: CreateSecretScanRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """9 类硬编码密钥扫描 + 3 层豁免 (来自 moa-skill + moat-ops-auditor)
    Body: {"path": "./", "fail_on": 3, "no_block": false}
    """
    from ..capability.secret_scan import scan_path, should_block

    p = Path(body.get("path", "."))
    if not p.exists():
        raise HTTPException(400, f"path not found: {p}")
    result = scan_path(p)
    blocked = should_block(result, body.get("fail_on", 3)) and not body.get("no_block", False)
    return {**result.to_dict(), "blocked": blocked}

@router.post("/v1/capability/group-think-check")
async def capability_group_think_check(
    body: CreateGroupThinkCheckRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """3 反群体思维纪律栈判定(来自 moa-skill 核心创新)
    Body: {
        "session_id": "...",
        "members": [{"member_id": "...", "content": "...", "round": 0}],
        "rounds": [[...]],  # 可选,多轮
        "warn_threshold": 0.4,
        "block_threshold": 0.7,
    }
    """
    from ..capability.moaflow import MemberResponse, group_think_verdict

    members = [MemberResponse(**m) for m in body.get("members", [])]
    rounds = None
    if body.get("rounds"):
        rounds = [[MemberResponse(**m) for m in r] for r in body["rounds"]]
    v = group_think_verdict(
        session_id=body.get("session_id", "unknown"),
        members=members,
        rounds=rounds,
        warn_threshold=body.get("warn_threshold", 0.4),
        block_threshold=body.get("block_threshold", 0.7),
    )
    return v.to_dict()

@router.post("/v1/capability/ensemble-vote")
async def capability_ensemble_vote(
    body: CreateEnsembleVoteRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """集成投票器(来自 01 GateSwarm — 4 种算法:majority/weighted/borda/approval)
    Body: {
        "votes": [{"voter_id": "...", "candidate": "...", "confidence": 0.9, "reason": "..."}],
        "method": "weighted"
    }
    """
    from ..capability.consensus import Vote, ensemble_vote

    votes = [Vote(**v) for v in body.get("votes", [])]
    result = ensemble_vote(votes, method=body.get("method", "weighted"))
    return result.to_dict()

@router.post("/v1/capability/should-rebalance")
async def capability_should_rebalance(
    body: CreateShouldRebalanceRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Tier 边界再训练(来自 01 GateSwarm)
    Body: {
        "stats": {"deepseek-v3": {"tier": "standard", "endpoint_count": 1, "success_count": 100, ...}},
        "config": {"high_threshold": 0.8, "low_threshold": 0.2, ...}
    }
    """
    from ..capability.consensus import TierStat, should_rebalance

    stats = {k: TierStat(**v) for k, v in body.get("stats", {}).items()}
    return {"should_rebalance": should_rebalance(stats, body.get("config", {}))}

@router.post("/v1/capability/cost-estimate")
async def capability_cost_estimate(
    body: CreateCostEstimateRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """dry-run 成本估算(来自 05 moa-skill)
    Body: {
        "input_tokens": 1000,
        "output_tokens": 500,
        "channels": [{"name": "deepseek-v3", "cost_per_1k_input": 0.0005, ...}],
        "include_fallback": true
    }
    """
    from ..capability.cost_estimator import Channel, estimate_moa_cost, format_report

    channels = [Channel(**c) for c in body.get("channels", [])]
    est = estimate_moa_cost(
        input_tokens=body.get("input_tokens", 1000),
        output_tokens=body.get("output_tokens", 500),
        channels=channels,
        include_fallback=body.get("include_fallback", True),
    )
    if body.get("format") == "report":
        return {"report": format_report(est), "estimate": est.to_dict()}
    return est.to_dict()

@router.post("/v1/capability/gate-l0")
async def capability_gate_l0(
    body: CreateGateL0Request,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """L0 闸门(来自 05 moa-skill)— 判断是否需要启 MoA
    Body: {"query": "2+3"} or {"query": "design a distributed system"}
    """
    from ..capability.gate_l0 import gate

    return gate(body.get("query", "")).to_dict()

@router.post("/v1/capability/score-panel")
async def capability_score_panel(
    body: CreateScorePanelRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """5 维评分(来自 09 opencode-moa — TQ/CO/AP/SE/IN)
    Body: {"query": "...", "answer": "..."}
    """
    from ..capability.score_panel import score_panel

    return score_panel(
        query=body.get("query", ""),
        answer=body.get("answer", ""),
    ).to_dict()

@router.get("/v1/capability/models")
async def capability_models(
    provider: str | None = None,
    supports_tools: bool | None = None,
    supports_vision: bool | None = None,
    min_context: int = 0,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """列模型(来自 10 Verdex — 41 真实模型)
    Query params: provider, supports_tools, supports_vision, min_context
    """
    from ..capability.model_context_db import list_models

    models = list_models(
        provider=provider,
        supports_tools=supports_tools if supports_tools else None,
        supports_vision=supports_vision if supports_vision else None,
        min_context=min_context,
    )
    return {"count": len(models), "models": [m.to_dict() for m in models]}

@router.post("/v1/capability/calculate-max-tokens")
async def capability_calculate_max_tokens(
    body: CreateCalculateMaxTokensRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """根据模型 context window 智能调整 max_tokens
    Body: {"model_id": "gpt-4o", "input_tokens": 1000, "requested_output": 2000, "safety_margin": 0.1}
    """
    from ..capability.model_context_db import calculate_max_tokens

    return {
        "model_id": body.get("model_id"),
        "max_tokens": calculate_max_tokens(
            body.get("model_id", "gpt-4o"),
            body.get("input_tokens", 1000),
            body.get("requested_output", 2000),
            body.get("safety_margin", 0.1),
        ),
    }

@router.post("/v1/capability/estimate-cost")
async def capability_estimate_cost(
    body: CreateEstimateCostRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """估算单模型成本
    Body: {"model_id": "gpt-4o", "input_tokens": 1000, "output_tokens": 500}
    """
    from ..capability.model_context_db import estimate_cost

    return estimate_cost(
        body.get("model_id", "gpt-4o"),
        body.get("input_tokens", 1000),
        body.get("output_tokens", 500),
    )

# ========== v1.5.1 Capability Endpoints — Wave 1 (HIGH 优先级) ==========
@router.post("/v1/capability/quota-check")
async def capability_quota_check(
    body: CreateQuotaCheckRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """R-08 多窗口配额检查 (5h/weekly/monthly + ETA)
    Body: {"windows":[{"name":"5h","limit_tokens":100000,"used_history":[[t1,n1],...]},...],"requested":1000}
    """
    from ..capability.rate_quota import QuotaState, QuotaWindow, check_available, eta_exhaustion

    windows = {w["name"]: QuotaWindow(**w) for w in body.get("windows", [])}
    state = QuotaState(windows=windows, last_updated=body.get("last_updated", time.time()))
    requested = body.get("requested", 0)
    ok, reason = check_available(state, requested)
    result = {
        "available": ok,
        "reason": reason,
        "eta_hours": {
            name: eta_exhaustion(state, body.get("burn_rate_per_hour", 1000.0), name)
            for name in windows
        },
    }
    return result

@router.post("/v1/capability/quota-record")
async def capability_quota_record(
    body: CreateQuotaRecordRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """R-08 记录 quota usage + 返回新 state"""
    from ..capability.rate_quota import QuotaState, QuotaWindow, record_usage

    windows = {w["name"]: QuotaWindow(**w) for w in body.get("windows", [])}
    state = QuotaState(windows=windows, last_updated=body.get("last_updated", time.time()))
    record_usage(state, body.get("tokens", 0), body.get("at"))
    return {
        "windows": {name: w.__dict__ for name, w in state.windows.items()},
        "last_updated": state.last_updated,
    }

@router.post("/v1/capability/moa-n-layer")
async def capability_moa_n_layer(
    body: CreateMoaNLayerRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """M-02 多层 MoA (3-layer 默认,真实跑通)
    Body: {"query":"...","proposers":[{"name":"a","model_id":"gpt-4o"}],"aggregators":[...]}
    """
    from ..capability.n_layer_moa import (
        Aggregator,
        Proposer,
        run_three_layer_moa,
    )

    # Round-1: 输入校验 (避免 500 包 422)
    raw_query = body.get("query", "")
    if not isinstance(raw_query, str):
        raise HTTPException(422, f"query must be a string, got {type(raw_query).__name__}")
    try:
        proposers = [Proposer(**p) for p in body.get("proposers", [])]
    except (TypeError, KeyError, ValueError) as e:
        raise HTTPException(422, f"invalid proposer: {e}") from e
    try:
        aggregators = [Aggregator(**a) for a in body.get("aggregators", [])]
    except (TypeError, KeyError, ValueError) as e:
        raise HTTPException(422, f"invalid aggregator: {e}") from e
    # 修 v1.6.6: 校验前移,避免 500 包 400
    if not proposers:
        raise HTTPException(400, "proposers must be non-empty")
    if len(aggregators) != 3:
        raise HTTPException(
            400, f"3-layer MoA needs exactly 3 aggregators, got {len(aggregators)}"
        )
    try:
        result = await run_three_layer_moa(
            body.get("query", ""),
            proposers=proposers,
            aggregators=aggregators,
            temperature=body.get("temperature", 0.6),
            max_total_tokens=body.get("max_total_tokens", 0),
        )
    except HTTPException:
        raise

    except Exception as e:
        raise err_500(e, "MoA run failed")
    return result

@router.post("/v1/capability/convergent-detect")
async def capability_convergent_detect(
    body: CreateConvergentDetectRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """M-16 跨提案 CONVERGENT 想法检测 + M-17 冲突仲裁
    Body: {"proposals":[{"proposal_idx":0,"author":"a","text":"..."}],"viability_scores":{0:0.8}}
    """
    from ..capability.convergent_detector import (
        Proposal,
        convergent_summary,
        extract_ideas,
    )

    proposals = [Proposal(**p) for p in body.get("proposals", [])]
    for p in proposals:
        if not p.ideas:
            p.ideas = extract_ideas(p.text, p.proposal_idx)
    summary = convergent_summary(proposals, min_support=body.get("min_support", 3))
    viability = body.get("viability_scores", {})
    if viability:
        from ..capability.convergent_detector import arbitrate_conflicts

        summary["arbitrations"] = [
            {"option_a": c.option_a, "option_b": c.option_b, "winner": w, "confidence": conf}
            for c, w, conf in arbitrate_conflicts(summary["conflicts"], viability)
        ]
    return summary

@router.post("/v1/capability/action-policy")
async def capability_action_policy(
    body: CreateActionPolicyRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-31 Action Policy (Allow/Deny/AdminReview) + A-32 Bypass Defense
    Body: {"command":"rm -rf /tmp/foo","rules":[{...PolicyRule...}]}
    """
    from ..capability.action_policy import (
        ActionPolicy,
        PolicyRule,
        pre_action_check,
    )

    rules = [PolicyRule(**r) for r in body.get("rules", [])]
    policy = ActionPolicy(rules)
    verdict = pre_action_check(body.get("command", ""), policy)
    return verdict.__dict__

@router.post("/v1/capability/embeddings")
async def capability_embeddings(
    body: CreateEmbeddingsRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """L-36 Embedding 端点 (OpenAI 兼容 /v1/embeddings 接口)
    Body: {"input":["text1","text2"], "model":"mock", "dim":384}
    Returns: {"data":[{"index":0,"embedding":[...]},...], "model":"mock", "dim":384}
    """
    from ..capability.embedding import MockEmbeddingProvider

    inputs = body.get("input", [])
    if isinstance(inputs, str):
        inputs = [inputs]
    dim = body.get("dim", 384)
    model = body.get("model", "mock-embedding-v1")
    provider = MockEmbeddingProvider(model=model, dim=dim)
    vectors = provider.embed(inputs)
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)
        ],
        "model": model,
        "dim": dim,
        "usage": {
            "prompt_tokens": sum(len(t.split()) for t in inputs),
            "total_tokens": sum(len(t.split()) for t in inputs),
        },
    }

@router.post("/v1/capability/semantic-search")
async def capability_semantic_search(
    body: CreateSemanticSearchRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """L-36 语义搜索 (端到端: embed query + 搜 index)
    Body: {"query":"...","documents":["a","b","c"],"top_k":3,"dim":384}
    """
    from ..capability.embedding import (
        EmbeddingIndex,
        MockEmbeddingProvider,
        batch_embed,
    )

    dim = body.get("dim", 384)
    docs = body.get("documents", [])
    provider = MockEmbeddingProvider(model="mock-embedding-v1", dim=dim)
    vectors = batch_embed(docs, dim=dim)
    index = EmbeddingIndex(model="mock-embedding-v1", dim=dim)
    for doc, vec in zip(docs, vectors):
        index.add(doc, vec)
    query_vec = provider.embed([body.get("query", "")])[0]
    results = index.search(query_vec, top_k=body.get("top_k", 3))
    return {
        "query": body.get("query", ""),
        "results": [
            {"rank": i + 1, "score": s, "text": t} for i, (idx, s, t) in enumerate(results)
        ],
    }

# ========== v1.5.2 Capability Endpoints — Wave 2 (HIGH 优先级) ==========
@router.post("/v1/capability/prompt-features")
async def capability_prompt_features(
    body: CreatePromptFeaturesRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """R-02 25 维 prompt 特征提取 + 域判 + complexity/urgency/pro_model
    Body: {"text": "..."}
    """
    from ..capability.prompt_features import (
        complexity_score,
        domain_classify,
        extract_features,
        should_use_pro_model,
        urgency_score,
    )

    text = body.get("text", "")
    feats = extract_features(text)
    return {
        "features": feats.__dict__,
        "domain": domain_classify(feats),
        "complexity": complexity_score(feats),
        "urgency": urgency_score(feats),
        "use_pro_model": should_use_pro_model(feats),
    }

@router.post("/v1/capability/provider-health")
async def capability_provider_health(
    body: CreateProviderHealthRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """R-07 提供者健康评分 (0-100) + tier 等级 + 排名 + 推荐
    Body: {"providers": [{"provider": "deepseek-v3", "total_calls": 100, ...HealthMetrics...}]}
    """
    from ..capability.provider_health import (
        HealthMetrics,
        aggregate_scores,
        compute_score,
        rank_providers,
        recommend,
    )

    metrics_list = [HealthMetrics(**m) for m in body.get("providers", [])]
    scores = {m.provider: compute_score(m) for m in metrics_list}
    agg = aggregate_scores(list(scores.values()))
    ranked = rank_providers(scores)
    return {
        "scores": {
            k: {"score": v.score, "tier": v.tier, "reasons": v.reasons}
            for k, v in scores.items()
        },
        "ranked": [{"provider": p, "score": s} for p, s in ranked],
        "recommend": recommend(scores, body.get("prefer_tier")),
    }

@router.post("/v1/capability/context-clean")
async def capability_context_clean(
    body: CreateContextCleanRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """R-11 7 阶段消息清洗
    Body: {"messages":[{"role":"user","content":"..."},...],"max_total_chars":100000}
    """
    from ..capability.context_clean import (
        Message,
        clean_messages,
        to_openai_format,
    )

    msgs = [Message(**m) for m in body.get("messages", [])]
    cleaned, stats = clean_messages(msgs, max_total_chars=body.get("max_total_chars", 100000))
    return {
        "messages": to_openai_format(cleaned),
        "stats": stats.__dict__,
    }

@router.post("/v1/capability/self-heal")
async def capability_self_heal(
    body: CreateSelfHealRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """R-15 自愈 tier 重新平衡 (action: record_success/record_failure/check_recovery/promote/demote/auto_balance)
    Body: {"endpoints":[{...EndpointState...}],"action":"record_success","endpoint_id":"ep1","at":123.0}
    """
    from ..capability.self_heal import (
        EndpointState,
        HealState,
        auto_balance,
        check_recovery,
        demote,
        get_available_endpoints,
        promote,
        record_failure,
        record_success,
        state_to_dict,
    )

    endpoints = {e["endpoint_id"]: EndpointState(**e) for e in body.get("endpoints", [])}
    state = HealState(endpoints=endpoints)
    action = body.get("action", "auto_balance")
    at = body.get("at")
    result_actions = []
    try:
        if action == "record_success":
            result_actions = [record_success(state, body["endpoint_id"], at)]
        elif action == "record_failure":
            result_actions = [record_failure(state, body["endpoint_id"], at)]
        elif action == "check_recovery":
            result_actions = [check_recovery(state, body["endpoint_id"], at)]
        elif action == "promote":
            result_actions = [
                promote(state, body["endpoint_id"], body.get("reason", "manual"), at)
            ]
        elif action == "demote":
            result_actions = [
                demote(state, body["endpoint_id"], body.get("reason", "manual"), at)
            ]
        elif action == "auto_balance":
            result_actions = auto_balance(state, at)
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # 修 38: 让 4xx 直接返回(不被包 500)

    except Exception as e:
        raise err_500(e, "self_heal action failed:")
    return {
        "actions": [a.__dict__ for a in result_actions],
        "state": state_to_dict(state),
        "available_endpoints": get_available_endpoints(state),
    }

@router.post("/v1/capability/multi-mode-synth")
async def capability_multi_mode_synth(
    body: CreateMultiModeSynthRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """M-14 多模式综合器 (4 模式: classification / integrated_synthesis / final_selection / cross_iteration)
    Body: {"mode":"classification","proposals":[{"proposal_idx":0,"author":"a","text":"..."}],...}
    """
    from ..capability.multi_mode_synth import (
        Proposal,
        run_synthesis,
    )

    proposals = [Proposal(**p) for p in body.get("proposals", [])]
    mode = body.get("mode", "classification")
    kwargs = {}
    if "scores" in body:
        kwargs["scores"] = body["scores"]
    if "target_chars" in body:
        kwargs["target_chars"] = body["target_chars"]
    if "prev_proposals" in body and "curr_proposals" in body:
        kwargs["prev_proposals"] = [Proposal(**p) for p in body["prev_proposals"]]
        kwargs["curr_proposals"] = [Proposal(**p) for p in body["curr_proposals"]]
    try:
        result = run_synthesis(mode, proposals, **kwargs)
    except HTTPException:
        raise  # patch v1.6.6: pass through 4xx

    except Exception as e:
        raise err_500(e, "synthesis failed:")
    return {
        "mode": result.mode.value,
        "output": result.output,
        "source_attribution": {str(k): v for k, v in result.source_attribution.items()},
        "confidence": result.confidence,
        "metadata": result.metadata,
    }

# ========== v1.5.3 Capability Endpoints — Wave 3 (HIGH 优先级) ==========
@router.post("/v1/capability/conflict-arbitrate")
async def capability_conflict_arbitrate(
    body: CreateConflictArbitrateRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """M-17 CONFLICTING 选择仲裁 (4 维: viability/support/empirical/compilable)
    Body: {"options":[{...ConflictOption...}],"fuse":false,"query":""}
    """
    from ..capability.conflict_arbiter import (
        ConflictOption,
        arbitrate,
        fuse_decision,
    )

    options = [ConflictOption(**o) for o in body.get("options", [])]
    if body.get("fuse", False):
        verdict = fuse_decision(options, body.get("query", ""))
    else:
        verdict = arbitrate(options)
    return {
        "winner_option_id": verdict.winner_option_id,
        "runner_up_id": verdict.runner_up_id,
        "confidence": verdict.confidence,
        "rationale": verdict.rationale,
        "voting_breakdown": verdict.voting_breakdown,
    }

@router.post("/v1/capability/section-viability")
async def capability_section_viability(
    body: CreateSectionViabilityRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """M-18 Per-section viability (复杂提案分节验证)
    Body: {"text":"...","proposal_idx":0}
    """
    from ..capability.section_viability import (
        validate_proposal,
    )

    text = body.get("text", "")
    proposal_idx = body.get("proposal_idx", 0)
    report = validate_proposal(text, proposal_idx)
    return {
        "proposal_idx": report.proposal_idx,
        "total_sections": report.total_sections,
        "viable_sections": report.viable_sections,
        "failing_sections": report.failing_sections,
        "ap_score": report.ap_score,
        "verdicts": [v.__dict__ for v in report.verdicts],
    }

@router.post("/v1/capability/feedback-iter")
async def capability_feedback_iter(
    body: CreateFeedbackIterRequest,
    admin: dict[str, Any] = Depends(require_admin),  # 修 P0-6: 必须 admin 防 path traversal
):
    """M-19 Feedback-aware iteration (跨迭代知识传递)
    Body: {"record":{...IterationRecord...},"history_path":""}

    修 P0-6 (security):
    - 改用 require_admin(不再是 require_api_key),防任意文件读/写
    - history_path 强制在白名单目录(DATA_DIR/feedback/),防 path traversal
    """
    from ..capability.feedback_loop import (
        IterationRecord,
        analyze_iteration,
        detect_convergence,
        format_next_iter_prompt,
        load_history,
        save_feedback,
    )

    rec = IterationRecord(**body.get("record", {}))
    feedback = analyze_iteration(rec)
    # 修 P0-6: history_path 强制在白名单目录
    _allowed_dir = os.path.abspath(
        os.path.join(get_storage().__class__.instance.__doc__ and "data" or "data", "feedback")
    )
    # 简化:用相对路径
    _allowed_dir = os.path.abspath("./data/feedback")
    os.makedirs(_allowed_dir, exist_ok=True)
    raw_path = body.get("history_path", "")
    history_path = ""
    if raw_path:
        # 拒绝绝对路径或 .. 遍历
        abs_path = (
            os.path.abspath(raw_path)
            if os.path.isabs(raw_path)
            else os.path.abspath(os.path.join(_allowed_dir, raw_path))
        )
        if not abs_path.startswith(_allowed_dir + os.sep) and abs_path != _allowed_dir:
            raise HTTPException(400, f"history_path not in allowlist: {abs_path}")
        history_path = abs_path
    if history_path:
        try:
            save_feedback(history_path, feedback)
        except HTTPException:
            raise  # patch v1.6.6: pass through 4xx

        except Exception as e:
            raise err_500(e, "save_feedback failed:")
    history = load_history(history_path) if history_path else []
    conv = (
        detect_convergence(history)
        if history
        else {"converged": False, "std": 0.0, "trend": "stable"}
    )
    prompt = format_next_iter_prompt(history_path) if history_path else ""
    return {
        "feedback": feedback.__dict__,
        "convergence": conv,
        "next_iter_prompt": prompt,
    }

@router.post("/v1/capability/stream-aggregate")
async def capability_stream_aggregate(
    body: CreateStreamAggregateRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """M-06 Aggregator 流式 + 非流式 fallback
    Body: {"prompt":"...","model":"mock-stream-v1","fail_prob":0.0,"use_fallback":true}
    """
    from ..capability.streaming_agg import (
        MockStreamingProvider,
        aggregate_with_fallback,
    )

    provider = MockStreamingProvider(
        fail_prob=body.get("fail_prob", 0.0),
    )
    try:
        result = await aggregate_with_fallback(
            provider,
            body.get("prompt", ""),
            body.get("model", "mock-stream-v1"),
        )
    except HTTPException:
        raise  # patch v1.6.6: pass through 4xx

    except Exception as e:
        raise err_500(e, "stream aggregate failed:")
    return {
        "full_content": result.full_content,
        "tool_calls": result.tool_calls,
        "finish_reason": result.finish_reason,
        "total_chunks": result.total_chunks,
        "streaming_succeeded": result.streaming_succeeded,
        "chunks_preview": [
            {"idx": c.chunk_idx, "type": c.delta_type, "content_preview": c.content[:40]}
            for c in result.chunks[:5]
        ],
    }

@router.post("/v1/capability/per-provider-rl")
async def capability_per_provider_rl(
    body: CreatePerProviderRlRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """R-17 Per-provider 限流 (RPM/IPM/并发 + 429 cooldown)
    Body: {"provider":"deepseek-v3","action":"check|record|mark_429|acquire","concurrent":0,"at":null}
    """
    from ..capability.per_provider_rl import (
        MultiProviderLimiter,
        ProviderLimit,
    )

    # 单 provider 模式: limits 转 {provider: ProviderLimit}
    limits_data = body.get("limits", {})
    if not limits_data and "provider" in body:
        limits_data = {
            body["provider"]: body.get(
                "limit_config",
                {
                    "max_requests_per_minute": 60,
                    "max_inputs_per_minute": 100000,
                    "max_concurrent": 5,
                },
            )
        }
    limits = {k: ProviderLimit(**v) for k, v in limits_data.items()}
    mpl = MultiProviderLimiter(limits)
    action = body.get("action", "check")
    provider = body.get("provider", next(iter(limits.keys())) if limits else "")
    result = {}
    try:
        if action == "check":
            decision = mpl.check(
                provider, concurrent_now=body.get("concurrent", 0), at=body.get("at")
            )
            result = decision.__dict__
        elif action == "record":
            mpl.record(
                provider,
                body.get("request_count", 1),
                body.get("input_tokens", 0),
                body.get("at"),
            )
            result = {"recorded": True, "provider": provider}
        elif action == "mark_429":
            # 修 v1.6.6: MultiProviderLimiter 用 _limiters (private),改用 _get
            limiter = mpl._get(provider)
            limiter.mark_429(body.get("cooldown_seconds", 60.0), at=body.get("at"))
            result = {"marked_429": True, "provider": provider}
        elif action == "status":
            # 修 v1.6.6: 用 _get 而非 .limiters
            limiter = mpl._get(provider)
            result = {
                "current_rpm": limiter._current_rpm(body.get("at")),
                "current_ipm": limiter._current_ipm(body.get("at")),
                "in_cooldown": limiter.is_in_cooldown(body.get("at")),
            }
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # 修 38: 让 4xx 直接返回(不被包 500)

    except Exception as e:
        raise err_500(e, "per_provider_rl action failed:")
    return result

# ========== v1.5.4 Capability Endpoints — Wave 4 (HIGH 优先级) ==========
@router.post("/v1/capability/tier-recalibrate")
async def capability_tier_recalibrate(
    body: CreateTierRecalibrateRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """R-04 Tier 边界动态重校准 (网格搜索阈值 + demote/promote)
    Body: {"tiers":[{"tier":"standard","p50_latency_ms":800,"p95_latency_ms":1500,...}]}
    """
    from ..capability.tier_recalibrate import (
        TierMetrics,
        recalibrate,
        should_retrain,
    )

    # tier 字段自动转小写以匹配 enum
    tier_map = {
        "FREE": "free",
        "LITE": "lite",
        "STANDARD": "standard",
        "PREMIUM": "premium",
        "FLAGSHIP": "flagship",
    }
    for t in body.get("tiers", []):
        if isinstance(t.get("tier"), str):
            t["tier"] = tier_map.get(t["tier"].upper(), t["tier"].lower())
    metrics = [TierMetrics(**m) for m in body.get("tiers", [])]
    plans = recalibrate(metrics)
    return {
        "plans": [
            {
                "old_tier": p.old_tier.value if hasattr(p.old_tier, "value") else p.old_tier,
                "new_tier": p.new_tier.value if hasattr(p.new_tier, "value") else p.new_tier,
                "reason": p.reason,
                "score_change": p.score_change,
                "expected_improvement": p.expected_improvement,
            }
            for p in plans
        ],
        "should_retrain": should_retrain(plans),
        "plan_count": len(plans),
    }

@router.post("/v1/capability/consumption-intel")
async def capability_consumption_intel(
    body: CreateConsumptionIntelRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """R-06 消费智能引擎 (静态优先 + 动态 fallback + vision 降级)
    Body: {"context":{...RequestContext...},"endpoints":[{...EndpointSpec...}]}
    """
    from ..capability.consumption_intel import (
        EndpointSpec,
        RequestContext,
        select_endpoint,
    )

    ctx = RequestContext(**body.get("context", {"query": ""}))
    endpoints = [EndpointSpec(**e) for e in body.get("endpoints", [])]
    decision = select_endpoint(ctx, endpoints)
    return {
        "selected_endpoint_id": decision.selected_endpoint_id,
        "fallback_chain": decision.fallback_chain,
        "vision_degraded_to": decision.vision_degraded_to,
        "reason": decision.reason,
        "estimated_cost_usd": decision.estimated_cost_usd,
    }

@router.post("/v1/capability/importance-score")
async def capability_importance_score(
    body: CreateImportanceScoreRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """R-13 重要性评分 (5 维加权 + top-k + 压缩决策)
    Body: {"messages":[{...Message...}],"top_k":3}
    """
    from ..capability.importance import (
        Message,
        score_messages,
        select_top_k,
        should_compress,
    )

    msgs = [Message(**m) for m in body.get("messages", [])]
    scores = score_messages(msgs)
    top_k = body.get("top_k", 0)
    return {
        "scores": [
            {"message_idx": s.message_idx, "score": s.score, "reasons": s.reasons}
            for s in scores
        ],
        "top_k_indices": select_top_k(scores, top_k) if top_k else [],
        "should_compress": should_compress(scores, body.get("threshold", 0.5)),
    }

@router.post("/v1/capability/quorum-check")
async def capability_quorum_check(
    body: CreateQuorumCheckRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """R-20 Quorum 宽限窗 (30s 宽限 + LLM-as-Judge 评分)
    Body: {"participants":[{...Participant...}],"required":3,"grace_seconds":30,"at":100.0}
    """
    from ..capability.quorum import (
        Participant,
        QuorumConfig,
        check_quorum,
        force_close,
        parse_battle,
        parse_rating,
        should_wait,
        swap_positions_battle,
    )

    config = QuorumConfig(
        required=body.get("required", 3),
        grace_seconds=body.get("grace_seconds", 30.0),
        wait_for_laggards=body.get("wait_for_laggards", True),
    )
    participants = [Participant(**p) for p in body.get("participants", [])]
    at = body.get("at")
    status = check_quorum(participants, config, at=at)
    result = {
        "status": {
            "reached": status.reached,
            "reached_at": status.reached_at,
            "responded_count": status.responded_count,
            "missing": status.missing,
            "within_grace": status.within_grace,
        },
        "should_wait": should_wait(status, config, at=at),
    }
    if body.get("force_close"):
        responded, dropped = force_close(participants, config, at=at)
        result["force_close"] = {
            "responded": [p.participant_id for p in responded],
            "dropped": dropped,
        }
    if body.get("judge_response"):
        jr = body["judge_response"]
        if "response_a" in body and "response_b" in body:
            result["battle"] = {
                "winner": parse_battle(jr)[0],
                "swap_consistent": swap_positions_battle(
                    body["response_a"],
                    body["response_b"],
                    lambda r: parse_battle(r),
                )
                == "consistent",
            }
        else:
            result["rating"] = parse_rating(jr)
    return result

@router.post("/v1/capability/model-entry")
async def capability_model_entry(
    body: CreateModelEntryRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """L-29 Provider 状态 12 字段 ModelEntry (capability check + filter + sort + budget)
    Body: {"models":[{...ModelEntry...}],"filter":{},"sort":"cost_asc","max_budget_input":0.01}
    """
    from ..capability.model_entry import (
        Modality,
        ModelEntry,
        filter_by_capability,
        filter_by_min_context,
        filter_by_modality,
        find_within_budget,
        multimodal_score,
        sort_by_context,
        sort_by_cost,
    )

    # modalities 元素转 Modality enum 实例 (value 已是 'TEXT'/'IMAGE' 大写)
    for m in body.get("models", []):
        if "modalities" in m:
            m["modalities"] = [
                Modality(x.upper()) if isinstance(x, str) else x for x in m["modalities"]
            ]
    models = [ModelEntry(**m) for m in body.get("models", [])]
    result_models = models
    flt = body.get("filter", {})
    if "capability" in flt:
        result_models = filter_by_capability(
            result_models, flt["capability"], flt.get("value", True)
        )
    if "modality" in flt:
        result_models = filter_by_modality(result_models, Modality(flt["modality"].upper()))
    if "min_context" in flt:
        result_models = filter_by_min_context(result_models, flt["min_context"])
    if "max_budget_input" in body or "max_budget_output" in body:
        result_models = find_within_budget(
            result_models,
            body.get("max_budget_input"),
            body.get("max_budget_output"),
        )
    sort = body.get("sort", "")
    if sort == "cost_asc":
        result_models = sort_by_cost(result_models, ascending=True)
    elif sort == "cost_desc":
        result_models = sort_by_cost(result_models, ascending=False)
    elif sort == "context_desc":
        result_models = sort_by_context(result_models, descending=True)
    query_modalities = [Modality(m.upper()) for m in body.get("query_modalities", [])]
    return {
        "models": [m.__dict__ for m in result_models],
        "count": len(result_models),
        "multimodal_scores": {
            m.model_id: multimodal_score(m, query_modalities) for m in result_models
        }
        if query_modalities
        else {},
    }

# ========== v1.5.5 Capability Endpoints — Wave 5 (HIGH 优先级) ==========
@router.post("/v1/capability/tool-replay")
async def capability_tool_replay(
    body: CreateToolReplayRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """M-07 Tool call 重放 + M-09 Tool choice 防循环
    Body: {"proposals":[{...proposal with <tool_use>...}],"window":5}
    """
    from ..capability.tool_replay import (
        detect_tool_loop,
        extract_tool_calls,
        format_tool_calls_for_aggregator,
        replay_tool_calls,
        should_disable_tool_choice,
    )

    proposals = body.get("proposals", [])
    # extract
    all_calls = []
    for i, p in enumerate(proposals):
        all_calls.extend(extract_tool_calls(p, i))
    # replay
    replay = replay_tool_calls(proposals, source_indices=list(range(len(proposals))))
    # 防循环
    disable = should_disable_tool_choice(
        len(all_calls), body.get("recent_count", len(all_calls))
    )
    loop = detect_tool_loop(all_calls, window=body.get("window", 5))
    formatted = format_tool_calls_for_aggregator(replay.tool_calls)
    return {
        "tool_calls": [tc.__dict__ for tc in replay.tool_calls],
        "deduplicated_count": replay.deduplicated_count,
        "conflicts_resolved": replay.conflicts_resolved,
        "should_disable_tool_choice": disable,
        "detected_loop": loop.__dict__ if loop else None,
        "aggregator_format": formatted,
    }

@router.post("/v1/capability/hook-events")
async def capability_hook_events(
    body: CreateHookEventsRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-02 27 Hook 事件注册 + A-10 4 阶段 Ralph 反馈循环
    Body: {"action":"trigger|ralph_advance","event":"PostToolUse","data":{},"session_id":"s1"}
    """
    from ..capability.hook_events import (
        RALPH_CYCLE,
        HookContext,
        HookEvent,
        HookRegistry,
    )

    # 全局 registry (in-memory)
    if not hasattr(capability_hook_events, "_registry"):
        capability_hook_events._registry = HookRegistry()
    reg = capability_hook_events._registry
    action = body.get("action", "ralph_advance")
    result = {}
    if action == "register":
        # 需 callback 不能从 body 拿,只返回 event list
        result = {
            "registered_event": body.get("event"),
            "total_handlers": len(reg.list_handlers()),
        }
    elif action == "trigger":
        event_name = body.get("event", "SessionStart")
        try:
            event = HookEvent(event_name)
        except ValueError:
            raise HTTPException(400, f"unknown event: {event_name}")
        ctx = HookContext(
            event=event,
            session_id=body.get("session_id", ""),
            timestamp=body.get("timestamp", 0.0),
            data=body.get("data", {}),
        )
        triggered = reg.trigger(event, ctx.__dict__)
        result = {"triggered_count": len(triggered)}
    elif action == "ralph_advance":
        stage = body.get("stage", "analyze")
        data = body.get("data", {})
        if not hasattr(capability_hook_events, "_ralph"):
            capability_hook_events._ralph = RALPH_CYCLE(max_iter=body.get("max_iter", 5))
        cycle = capability_hook_events._ralph
        next_stage = cycle.advance(data)
        result = {
            "current_stage": stage,
            "next_stage": next_stage,
            "iteration": cycle.iteration,
            "terminated": cycle.terminated,
        }
    elif action == "list_events":
        result = {"events": [e.value for e in HookEvent], "count": len(HookEvent)}
    else:
        raise HTTPException(400, f"unknown action: {action}")
    return result

@router.post("/v1/capability/meta-prompt")
async def capability_meta_prompt(
    body: CreateMetaPromptRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """M-22 3 阶段元 Prompt 协议 + M-23 认知摩擦对抗 + M-26 冲突消解
    Body: {"query":"...","action":"get_stages|clash|fuse","options":[...]}
    """
    from ..capability.meta_prompt import (
        cognitively_clash,
        fuse_decision,
        get_stage_prompts,
    )

    action = body.get("action", "get_stages")
    query = body.get("query", "")
    if action == "get_stages":
        stages = get_stage_prompts(query)
        return {
            "stages": [s.__dict__ for s in stages],
            "count": len(stages),
        }
    elif action == "clash":
        role_a = body.get("role_a", "optimist")
        role_b = body.get("role_b", "pessimist")
        a, b = cognitively_clash(role_a, role_b, query)
        return {"role_a_prompt": a, "role_b_prompt": b}
    elif action == "fuse":
        options = body.get("options", [])
        winner = fuse_decision(options, body.get("context", query))
        return {"winner": winner, "options_count": len(options)}
    else:
        raise HTTPException(400, f"unknown action: {action}")

@router.post("/v1/capability/task-tree")
async def capability_task_tree(
    body: CreateTaskTreeRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-18 Task Tree (TaskSegment) + A-34 Task 分解树
    Body: {"tasks":[{...TaskSegment...}],"action":"add|ready|cycles|aggregates|depth","task_id":""}
    """
    from ..capability.task_tree import (
        TaskSegment,
        TaskStatus,
        TaskTree,
        compute_aggregates,
        depth,
        detect_cycles,
        is_leaf,
        is_root,
        tree_from_dict,
        tree_to_dict,
    )
    from ..capability.task_tree import (
        get_ready_tasks as _get_ready,
    )

    # 构建/恢复 tree
    tasks_data = body.get("tasks", [])
    # 补 status 缺省值
    for t in tasks_data:
        if "status" not in t:
            t["status"] = "pending"
    tree = None
    if tasks_data:
        # 尝试 tree_from_dict 格式(若有完整 fields)
        try:
            tree = tree_from_dict({"tasks": tasks_data})
        except Exception:
            pass
        if tree is None:
            # 兜底:从 list 手动构建
            root_id = next(
                (t["id"] for t in tasks_data if t.get("parent_id") is None), tasks_data[0]["id"]
            )
            tree = TaskTree(root_id=root_id)
            # 先 add root
            root_data = next(t for t in tasks_data if t["id"] == root_id)
            root_seg = {k: v for k, v in root_data.items() if k != "children_ids"}
            tree.add_task(TaskSegment(**root_seg))
            for t in tasks_data:
                if t["id"] == root_id:
                    continue
                seg_data = {k: v for k, v in t.items() if k != "children_ids"}
                try:
                    tree.add_task(TaskSegment(**seg_data))
                except ValueError:
                    # 重复 id 跳过
                    pass
    if tree is None:
        tree = TaskTree(root_id="root")
        tree.add_task(
            TaskSegment(id="root", title="root", description="root", status="pending")
        )
    # 修 v1.6.6: 删除 buggy "else: tree = TaskTree(root_id='root')" 覆盖代码
    action = body.get("action", "ready")
    task_id = body.get("task_id", "")
    result = {}
    if action == "ready":
        result = {"ready_tasks": _get_ready(tree)}
    elif action == "cycles":
        cycles = detect_cycles(tree)
        result = {"cycles": cycles, "has_cycle": len(cycles) > 0}
    elif action == "aggregates":
        result = compute_aggregates(tree, task_id) if task_id else {}
    elif action == "depth":
        result = {"task_id": task_id, "depth": depth(tree, task_id) if task_id else -1}
    elif action == "is_leaf":
        result = {"task_id": task_id, "is_leaf": is_leaf(tree, task_id) if task_id else False}
    elif action == "is_root":
        result = {"task_id": task_id, "is_root": is_root(tree, task_id) if task_id else False}
    elif action == "set_status":
        new_status = body.get("status", "completed")
        try:
            status_enum = TaskStatus(new_status)
        except ValueError:
            raise HTTPException(400, f"unknown status: {new_status}")
        tree.set_status(task_id, status_enum)
        result = {"set": True, "task_id": task_id, "status": new_status}
    else:
        raise HTTPException(400, f"unknown action: {action}")
    result["tree"] = tree_to_dict(tree)
    return result

@router.post("/v1/capability/distill")
async def capability_distill(
    body: CreateDistillRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """M-15 Integrated synthesis + M-51 Multi-eval consensus averaging
    Body: {"proposals":["..."],"keep_ratio":0.5,"evaluations":[{"TQ":40,"CO":35,...}]}
    """
    from ..capability.distillation import (
        apply_bias_correction,
        distill_proposals,
        multi_eval_average,
    )

    proposals = body.get("proposals", [])
    keep_ratio = body.get("keep_ratio", 0.5)
    distillation = distill_proposals(proposals, keep_ratio=keep_ratio)
    result = {
        "distillation": {
            "kept_count": distillation.distilled_count,
            "dropped_count": len(distillation.dropped_ideas),
            "original_count": distillation.original_count,
            "ratio": distillation.distillation_ratio,
            "kept_ideas": [i.__dict__ for i in distillation.kept_ideas],
        },
    }
    if "evaluations" in body:
        evals = body["evaluations"]
        avg = multi_eval_average(evals)
        biases = avg.pop("biases", {})
        result["multi_eval"] = {
            "averages": avg,
            "biases": biases,
        }
        if body.get("apply_bias_correction") and biases:
            result["corrected"] = {
                dim: apply_bias_correction({dim: scores}, {dim: biases.get(dim, 0)}).get(dim, 0)
                for dim, scores in avg.items()
            }
    return result

# ========== v1.5.6 Capability Endpoints — Wave 6 (HIGH 优先级) ==========
@router.post("/v1/capability/rerank")
async def capability_rerank(
    body: CreateRerankRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """L-37 Cohere Rerank v4 (latency-bounded) + L-31 Stream delta 完整代理
    Body: {"query":"...","documents":["d1","d2"],"top_n":3,"latency_budget_ms":2000}
    """
    from ..capability.rerank import (
        format_for_openai,
        rerank_with_budget,
        stream_delta_proxy,
    )

    query = body.get("query", "")
    documents = body.get("documents", [])
    top_n = body.get("top_n", 10)
    budget = body.get("latency_budget_ms", 2000.0)
    result = rerank_with_budget(query, documents, top_n=top_n, latency_budget_ms=budget)
    result_data = {
        "query": result.query,
        "candidates": [c.__dict__ for c in result.candidates],
        "latency_ms": result.latency_ms,
        "truncated": result.truncated,
    }
    if body.get("stream_chunks"):
        proxy = stream_delta_proxy(body["stream_chunks"])
        result_data["stream_proxy"] = proxy
        result_data["openai_format"] = format_for_openai(proxy)
    return result_data

@router.post("/v1/capability/goal-eval")
async def capability_goal_eval(
    body: CreateGoalEvalRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-12 2-tier 目标求值 + A-13 5-section Ceiling Report
    Body: {"goals":[{...Goal...}],"output":"...","generate_ceiling":true}
    """
    from ..capability.goal_eval import (
        Goal,
        evaluate_goal,
        generate_ceiling_report,
    )

    tier_map = {1: "mechanical", 2: "model_declared"}
    goals = []
    for g in body.get("goals", []):
        tier_str = g.get("tier", "mechanical")
        if isinstance(tier_str, int):
            tier_str = tier_map.get(tier_str, "mechanical")
        # 修 v1.6.6: Goal 实际 fields 是 (id, description, tier, criteria, evaluator_fn)
        # 之前 server.py 用 (name, description, tier, metric, target, current) → TypeError
        goal_id = g.get("id") or g.get("name") or "goal_" + str(len(goals))
        description = g.get("description", "")
        criteria = g.get("criteria") or g.get("metric", "default")
        goals.append(
            Goal(
                id=goal_id,
                description=description,
                tier=tier_str,
                criteria=criteria,
            )
        )
    output = body.get("output", "")
    results = [evaluate_goal(g, output).__dict__ for g in goals]
    ceiling = None
    if body.get("generate_ceiling"):
        # 修 v1.6.6: baseline/residual_risk 不能为空,默认占位
        cr = generate_ceiling_report(
            claim=body.get("claim") or "unspecified",
            evidence=body.get("evidence") or [],
            baseline=body.get("baseline") or "(no baseline provided)",
            gaps=body.get("gaps") or [],
            residual_risk=body.get("residual_risk") or "unknown",
        )
        ceiling = cr.__dict__
    return {"results": results, "ceiling_report": ceiling}

@router.post("/v1/capability/auto-converge")
async def capability_auto_converge(
    body: CreateAutoConvergeRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-15 Auto-converge + A-14 Tier classification 1/3/5/10
    Body: {"state":{...},"config":{...},"new_score":0.85,"classify_events":5}
    """
    from ..capability.auto_converge import (
        ConvergenceConfig,
        ConvergenceState,
        calibrate_confidence,
        check_convergence,
        classify_tier,
        detect_stagnation,
    )

    result = {}
    if "state" in body and "new_score" in body:
        state_data = body["state"]
        state = ConvergenceState(
            iteration=state_data.get("iteration", 0),
            best_score_history=state_data.get("best_score_history", []),
            stagnation_count=state_data.get("stagnation_count", 0),
            converged=state_data.get("converged", False),
        )
        cfg = ConvergenceConfig(
            stagnation_threshold=body.get("config", {}).get("stagnation_threshold", 3),
            improvement_threshold=body.get("config", {}).get("improvement_threshold", 0.001),
            max_iterations=body.get("config", {}).get("max_iterations", 10),
        )
        new_state = check_convergence(state, cfg, body["new_score"])
        result["new_state"] = new_state.__dict__
    if "classify_events" in body:
        result["classified_tier"] = classify_tier(body["classify_events"])
    if "history" in body:
        result["stagnant"] = detect_stagnation(
            body["history"],
            threshold=body.get("stagnation_threshold", 3),
            epsilon=body.get("epsilon", 0.001),
        )
    if "calibrate_score" in body:
        result["calibrated"] = calibrate_confidence(
            body["calibrate_score"],
            body.get("calibrate_samples", 0),
        )
    return result

@router.post("/v1/capability/subagent-comms")
async def capability_subagent_comms(
    body: CreateSubagentCommsRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-38 Subagent 通信 (SendMessage/TaskCreate) + A-22 Advisory lock
    Body: {"action":"send|broadcast|reply|create_task|update_status|acquire|release","session_id":"s1",...}
    """
    from ..capability.subagent_comms import AdvisoryLock, SubagentHub, TaskBoard

    action = body.get("action", "send")
    session_id = body.get("session_id", "default")
    result = {}
    try:
        if action in ("send", "broadcast", "reply", "inbox"):
            if not hasattr(capability_subagent_comms, "_hubs"):
                capability_subagent_comms._hubs = {}
            hubs = capability_subagent_comms._hubs
            if session_id not in hubs:
                hubs[session_id] = SubagentHub(session_id)
            hub = hubs[session_id]
            if action == "send":
                msg = hub.send_message(
                    body["to_session"], body.get("content", ""), body.get("kind", "send")
                )
                result = {"message": msg.__dict__}
            elif action == "broadcast":
                msgs = hub.broadcast(body.get("sessions", []), body.get("content", ""))
                result = {"messages": [m.__dict__ for m in msgs]}
            elif action == "reply":
                msg = hub.reply(body["parent_msg_id"], body.get("content", ""))
                result = {"message": msg.__dict__}
            elif action == "inbox":
                result = {"messages": [m.__dict__ for m in hub.inbox()]}
        elif action in (
            "create_task",
            "update_status",
            "list_tasks",
            "get_task",
            "get_subtasks",
        ):
            if not hasattr(capability_subagent_comms, "_boards"):
                capability_subagent_comms._boards = {}
            boards = capability_subagent_comms._boards
            if session_id not in boards:
                boards[session_id] = TaskBoard(session_id)
            board = boards[session_id]
            if action == "create_task":
                task_id = board.create_task(
                    body.get("title", ""),
                    assignee=body.get("assignee"),
                    parent=body.get("parent"),
                )
                result = {"task_id": task_id}
            elif action == "update_status":
                board.update_status(body["task_id"], body.get("status", "pending"))
                result = {"updated": True}
            elif action == "list_tasks":
                tasks = board.list_tasks(
                    status=body.get("status"), assignee=body.get("assignee")
                )
                result = {"tasks": [t.__dict__ for t in tasks]}
            elif action == "get_task":
                t = board.get_task(body["task_id"])
                result = {"task": t.__dict__ if t else None}
            elif action == "get_subtasks":
                tasks = board.get_subtasks(body["parent_task_id"])
                result = {"tasks": [t.__dict__ for t in tasks]}
        elif action in ("acquire", "release", "is_held"):
            lock_id = body["lock_id"]
            if not hasattr(capability_subagent_comms, "_locks"):
                capability_subagent_comms._locks = {}
            locks = capability_subagent_comms._locks
            if lock_id not in locks:
                locks[lock_id] = AdvisoryLock(
                    lock_id, body.get("holder", session_id), body.get("timeout", 10.0)
                )
            lock = locks[lock_id]
            if action == "acquire":
                result = {"acquired": lock.acquire()}
            elif action == "release":
                lock.release()
                result = {"released": True}
            elif action == "is_held":
                result = {"held": lock.is_held()}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # patch v1.6.6: pass through 4xx

    except Exception as e:
        raise err_500(e, "subagent_comms failed:")
    return result

@router.post("/v1/capability/version")
async def capability_version(
    body: CreateVersionRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """M-35 方案版本化 (v1→v2) + M-27 LLM-as-Judge 单答评分
    Body: {"action":"add|get|latest|diff|parse_rating|parse_battle","proposal_id":"p1",...}
    """
    from ..capability.versioning import (
        VersionStore,
        diff_versions,
        parse_battle,
        parse_rating,
    )

    if not hasattr(capability_version, "_stores"):
        capability_version._stores = {}
    stores = capability_version._stores
    action = body.get("action", "add")
    result = {}
    try:
        if action in ("add", "get", "latest", "diff"):
            proposal_id = body.get("proposal_id", "default")
            if proposal_id not in stores:
                stores[proposal_id] = VersionStore()
            store = stores[proposal_id]
            if action == "add":
                vid = store.add_version(
                    proposal_id,
                    body.get("content", ""),
                    parent=body.get("parent"),
                    critique=body.get("critique"),
                    improvement=body.get("improvement"),
                    created_by=body.get("created_by", "system"),
                )
                result = {"version_id": vid}
            elif action == "get":
                chain = store.get_chain(proposal_id)
                result = {"chain": [v.__dict__ for v in chain.versions]}
            elif action == "latest":
                v = store.latest(proposal_id)
                result = {"version": v.__dict__ if v else None}
            elif action == "diff":
                v1 = store.get_version(proposal_id, body["v1"])
                v2 = store.get_version(proposal_id, body["v2"])
                if v1 and v2:
                    result = {"diff": diff_versions(v1, v2)}
        elif action == "parse_rating":
            result = {"rating": parse_rating(body.get("judge_response", ""))}
        elif action == "parse_battle":
            w, c = parse_battle(body.get("judge_response", ""))
            result = {"winner": w, "confidence": c}
        elif action == "swap_battle":
            # 2 轮位置交换
            def judge(r, _a=body.get("response_a", ""), _b=body.get("response_b", "")):
                return parse_battle(r)[0]

            # round 1
            r1 = judge(body.get("judge_response", ""))
            # round 2 swap
            r2 = (
                parse_battle(body.get("judge_response_swapped", ""))[0]
                if body.get("judge_response_swapped")
                else r1
            )
            result = {"round1": r1, "round2": r2, "consistent": r1 == r2}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # 修 38: 让 4xx 直接返回(不被包 500)

    except Exception as e:
        raise err_500(e, "version action failed:")
    return result

# ========== v1.5.7 Capability Endpoints — Wave 7 (HIGH 优先级) ==========
@router.post("/v1/capability/config")
async def capability_config(
    body: CreateConfigRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-03 8 层配置合并栈 + A-05 5 个 Permission Mode
    Body: {"action":"get|set|unset|merge","key":"model","value":"gpt-4o","layer":"user",...}
    """
    from ..capability.config_stack import (
        ConfigLayer,
        ConfigStack,
        PermissionMode,
        merge_layers,
    )

    # 全局 stack
    if not hasattr(capability_config, "_stack"):
        capability_config._stack = ConfigStack()
    stack = capability_config._stack
    action = body.get("action", "get")
    result = {}
    try:
        if action == "set":
            layer = ConfigLayer[body.get("layer", "user").upper()]
            stack.set(
                body["key"], body.get("value"), layer, explicit=body.get("explicit", True)
            )
            result = {"set": True, "key": body["key"], "layer": body.get("layer", "user")}
        elif action == "get":
            val, layer = stack.get_with_source(body["key"])
            result = {"value": val, "source_layer": layer.name if layer else None}
        elif action == "unset":
            count = stack.unset(
                body["key"],
                layer=ConfigLayer[body["layer"].upper()] if body.get("layer") else None,
            )
            result = {"unset_count": count}
        elif action == "merge":
            layers_data = {ConfigLayer[k.upper()]: v for k, v in body.get("layers", {}).items()}
            merged = merge_layers(layers_data)
            # 写入 stack
            for k, v in merged.items():
                stack.set(k, v, ConfigLayer.USER, explicit=True)
            result = {"merged": merged}
        elif action == "permission":
            # 5 permission mode 演示
            mode = PermissionMode(body.get("mode", "default"))
            result = {"mode": mode.value}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # 修 38: 让 4xx 直接返回(不被包 500)

    except Exception as e:
        raise err_500(e, "config action failed:")
    return result

@router.post("/v1/capability/bubble")
async def capability_bubble(
    body: CreateBubbleRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-06 Bubble Mode (parent escalate) + A-26 Event scheduling
    Body: {"action":"escalate|resolve|pending|should_continue","parent_id":"p1",...}
    """
    from ..capability.bubble_mode import (
        BubbleManager,
        BubbleStatus,
        Event,
        EventScheduler,
        EventType,
    )

    if not hasattr(capability_bubble, "_managers"):
        capability_bubble._managers = {}
    action = body.get("action", "escalate")
    result = {}
    try:
        if action in ("escalate", "resolve", "pending", "resolved"):
            parent_id = body.get("parent_id", "default")
            if parent_id not in capability_bubble._managers:
                capability_bubble._managers[parent_id] = BubbleManager(parent_id)
            mgr = capability_bubble._managers[parent_id]
            if action == "escalate":
                req_id = mgr.escalate(
                    body["agent_id"], body.get("action_desc", ""), body.get("reason", "")
                )
                result = {"request_id": req_id}
            elif action == "resolve":
                ok = mgr.resolve(
                    body["request_id"], BubbleStatus(body.get("decision", "allowed"))
                )
                result = {"resolved": ok}
            elif action == "pending":
                pending = mgr.get_pending()
                result = {"pending": [r.__dict__ for r in pending], "count": len(pending)}
            elif action == "resolved":
                resolved = mgr.get_resolved()
                result = {"resolved": [r.__dict__ for r in resolved], "count": len(resolved)}
        elif action in ("schedule", "should_continue", "recent", "clear"):
            if not hasattr(capability_bubble, "_scheduler"):
                capability_bubble._scheduler = EventScheduler()
            sched = capability_bubble._scheduler
            if action == "schedule":
                ev = Event(
                    event_id=body.get("event_id", ""),
                    event_type=EventType(body.get("event_type", "neutral")),
                    agent_id=body["agent_id"],
                    payload=body.get("payload", {}),
                    timestamp=body.get("timestamp", time.time()),
                )
                eid = sched.schedule(ev)
                result = {"event_id": eid}
            elif action == "should_continue":
                result = {"should_continue": sched.should_continue(body["agent_id"])}
            elif action == "recent":
                events = sched.recent_events(body["agent_id"], n=body.get("n", 10))
                result = {"events": [e.__dict__ for e in events]}
            elif action == "clear":
                count = sched.clear(body["agent_id"])
                result = {"cleared": count}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # 修 38: 让 4xx 直接返回(不被包 500)

    except Exception as e:
        raise err_500(e, "bubble action failed:")
    return result

@router.post("/v1/capability/worktree")
async def capability_worktree(
    body: dict[str, Any],
    admin: dict[str, Any] = Depends(require_admin),  # 修 P0-5: 必须 admin,防任意 cwd git
):
    """A-42 Worktree 隔离基元 + A-43 Worktree Snapshot/Diff
    Body: {"action":"snapshot|is_clean|diff","repo_path":"D:\\MoA Gateway Pro",...}

    修 P0-5 (security):
    - 改用 require_admin(不是 require_api_key)
    - repo_path 强制白名单(仅允许 server cwd 或 ~/.moa-gateway/)
    - subprocess.run 不传 env,默认 inherit 但去掉危险 env vars
    """
    from ..capability.worktree import (
        WorktreeManager,
        diff_snapshots,
        is_clean,
        snapshot,
    )

    # 修 P0-5: repo_path 白名单
    _allowed_roots = (
        os.path.abspath("."),
        os.path.abspath(os.path.expanduser("~/.moa-gateway")),
    )

    def _validate_repo_path(p: str) -> str:
        if not p:
            return _allowed_roots[0]
        abs_p = os.path.abspath(p)
        if not any(abs_p == r or abs_p.startswith(r + os.sep) for r in _allowed_roots):
            raise HTTPException(400, f"repo_path not in allowlist: {abs_p}")
        return abs_p

    action = body.get("action", "snapshot")
    result = {}
    try:
        if action == "snapshot":
            repo_path = _validate_repo_path(body.get("repo_path", "."))
            snap = snapshot(repo_path)
            result = {
                "commit_sha": snap.commit_sha,
                "branch": snap.branch,
                "tracked_files_count": len(snap.tracked_files),
                "porcelain_status_count": len(snap.porcelain_status),
                "is_clean": is_clean(snap),
                "timestamp": snap.timestamp,
            }
        elif action == "list":
            repo_path = _validate_repo_path(body.get("repo_path", "."))
            mgr = WorktreeManager(repo_path)
            wts = mgr.list_worktrees()
            result = {"worktrees": [w.__dict__ for w in wts]}
        elif action == "diff":
            p1 = _validate_repo_path(body.get("repo_path1", "."))
            p2 = _validate_repo_path(body.get("repo_path2", "."))
            snap1 = snapshot(p1)
            snap2 = snapshot(p2)
            result = {"diff": diff_snapshots(snap1, snap2)}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # 修 38: 让 4xx 直接返回(不被包 500)

    except Exception as e:
        raise err_500(e, "worktree action failed:")
    return result

@router.post("/v1/capability/route")
async def capability_route(
    body: CreateRouteRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-45 Harness Routing 3 档 + A-46 Auto-Detection Rules
    Body: {"action":"route_request|auto_detect|priority|tools","task":"fix bug",...}
    """
    from ..capability.routing import (
        HarnessTier,
        auto_detect_tier,
        priority_from_severity,
        route_request,
        tools_for_tier,
    )

    action = body.get("action", "route_request")
    result = {}
    try:
        if action == "route_request":
            config = route_request(
                task=body.get("task", ""),
                file_count=body.get("file_count", 0),
                single_domain=body.get("single_domain", True),
                is_bugfix=body.get("is_bugfix", False),
                is_docs=body.get("is_docs", False),
            )
            result = {
                "tier": config.tier.value,
                "priority": config.priority.name,
                "tools": config.tools,
                "max_iterations": config.max_iterations,
            }
        elif action == "auto_detect":
            tier = auto_detect_tier(body.get("task", ""), body.get("files", []))
            result = {"tier": tier.value}
        elif action == "priority":
            pri = priority_from_severity(body.get("severity", "normal"))
            result = {"priority": pri.name}
        elif action == "tools":
            tier = HarnessTier(body.get("tier", "standard"))
            result = {"tools": tools_for_tier(tier)}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # 修 38: 让 4xx 直接返回(不被包 500)

    except Exception as e:
        raise err_500(e, "route action failed:")
    return result

@router.post("/v1/capability/session-lock")
async def capability_session_lock(
    body: CreateSessionLockRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-22 Multi-session 协调 (advisory lock) + A-20 MCP 工具注册
    Body: {"action":"acquire|release|get_state|register_mcp|invoke_mcp",...}
    """
    from ..capability.session_lock import (
        MCPRegistry,
        MCPTool,
        SessionLockManager,
    )

    if not hasattr(capability_session_lock, "_mgr"):
        capability_session_lock._mgr = SessionLockManager()
    if not hasattr(capability_session_lock, "_mcp"):
        capability_session_lock._mcp = MCPRegistry()
    mgr = capability_session_lock._mgr
    mcp = capability_session_lock._mcp
    action = body.get("action", "acquire")
    result = {}
    try:
        if action in (
            "try_acquire",
            "acquire_with_wait",
            "release",
            "get_state",
            "cleanup_expired",
        ):
            if action == "try_acquire":
                ok = mgr.try_acquire(body["lock_id"], body["session_id"], ttl=body.get("ttl"))
                result = {"acquired": ok}
            elif action == "acquire_with_wait":
                ok = mgr.acquire_with_wait(
                    body["lock_id"],
                    body["session_id"],
                    timeout=body.get("timeout", 10.0),
                    retry_interval=body.get("retry_interval", 0.01),
                )
                result = {"acquired": ok}
            elif action == "release":
                ok = mgr.release(body["lock_id"], body["session_id"])
                result = {"released": ok}
            elif action == "get_state":
                lock = mgr.get_lock_state(body["lock_id"])
                result = {"lock": lock.__dict__ if lock else None}
            elif action == "cleanup_expired":
                mgr.cleanup_expired()
                result = {"cleaned": True}
        elif action in ("register_mcp", "unregister_mcp", "invoke_mcp", "list_mcp", "get_mcp"):
            if action == "register_mcp":

                def handler(**kwargs):
                    return body.get("returns", f"executed {body['name']} with {kwargs}")

                tool = MCPTool(
                    name=body["name"],
                    description=body.get("description", ""),
                    parameters=body.get("parameters", {}),
                    handler=handler,
                )
                mcp.register(tool)
                result = {"registered": body["name"]}
            elif action == "unregister_mcp":
                mcp.unregister(body["name"])
                result = {"unregistered": body["name"]}
            elif action == "invoke_mcp":
                out = mcp.invoke(body["name"], **body.get("kwargs", {}))
                result = {"output": out}
            elif action == "list_mcp":
                result = {"tools": mcp.list_tools()}
            elif action == "get_mcp":
                t = mcp.get_tool(body["name"])
                result = {
                    "tool": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    }
                    if t
                    else None
                }
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # 修 38: 让 4xx 直接返回(不被包 500)

    except Exception as e:
        raise err_500(e, "session_lock action failed:")
    return result

# ========== v1.5.8 Capability Endpoints — Wave 8 (HIGH 优先级) ==========
@router.post("/v1/capability/flask")
async def capability_flask(
    body: CreateFlaskRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """M-29 FLASK 12 维技能评分 + M-34 Task 分解树 (高内聚低耦合)
    Body: {"answer":"...","query":"...","tasks":[{title,description}...]}
    """
    from ..capability.flask_score import score_flask, summary_report

    result = {}
    if body.get("answer"):
        flask = score_flask(body["answer"], body.get("query", ""))
        result["flask"] = {
            "total_score": flask.total_score,
            "dimension_scores": {d.name: s for d, s in flask.dimension_scores.items()},
            "weak": [d.name for d in flask.weak_dimensions],
            "strong": [d.name for d in flask.strong_dimensions],
            "summary": summary_report(flask),
        }
    if body.get("tasks"):
        # 简化:用 score_flask 也给每个 task title 评分
        scores = []
        for t in body["tasks"]:
            text = f"{t.get('title', '')} {t.get('description', '')}"
            f = score_flask(text)
            scores.append(
                {
                    "title": t.get("title", ""),
                    "total": f.total_score,
                    "weak": [d.name for d in f.weak_dimensions],
                }
            )
        result["task_scores"] = scores
    return result

@router.post("/v1/capability/elo")
async def capability_elo(
    body: CreateEloRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """M-30 Elo ranking + Bootstrap CI + Worker 调度
    Body: {"action":"record|ranked|bootstrap_ci|submit","matches":[{winner,loser}],...}
    """
    from ..capability.elo_ranking import (
        EloLeaderboard,
        EloRating,
        MatchResult,
        WorkerPool,
        bootstrap_ci,
    )

    action = body.get("action", "record")
    result = {}
    try:
        if action == "record":
            lb = EloLeaderboard(k_factor=body.get("k_factor", 4.0))
            for mid in body.get("model_ids", []):
                lb.add_model(mid)
            matches = [MatchResult(**m) for m in body.get("matches", [])]
            for m in matches:
                lb.record_match(m.winner_id, m.loser_id, m.timestamp)
            ranked = lb.ranked()
            result["ranked"] = [
                {"model_id": r.model_id, "rating": r.rating, "matches": r.matches_played}
                for r in ranked
            ]
        elif action == "bootstrap_ci":
            lb = EloLeaderboard()
            for r in body.get("ratings_before", []):
                lb.add_model(r["model_id"], r.get("rating", 1500.0))
            matches = [MatchResult(**m) for m in body.get("matches", [])]
            for m in matches:
                lb.record_match(m.winner_id, m.loser_id, m.timestamp)
            ratings_before = [
                EloRating(
                    model_id=r["model_id"],
                    rating=r["rating"],
                    matches_played=r.get("matches", 0),
                )
                for r in lb.ranked()
            ]
            ci = bootstrap_ci(
                ratings_before,
                matches,
                n_resamples=body.get("n_resamples", 1000),
                ci=body.get("ci", 0.95),
            )
            result["bootstrap_ci"] = {k: {"low": v[0], "high": v[1]} for k, v in ci.items()}
        elif action == "submit":
            pool = WorkerPool(body.get("workers", ["w1", "w2", "w3"]))
            pool.set_strategy(body.get("strategy", "shortest_queue"))
            # 真 submit 任务要 callable,这里返回调度决策
            loads = pool.worker_loads()
            result["loads"] = loads
            result["strategy"] = body.get("strategy", "shortest_queue")
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # 修 38: 让 4xx 直接返回(不被包 500)

    except Exception as e:
        raise err_500(e, "elo action failed:")
    return result

@router.post("/v1/capability/brainstorm")
async def capability_brainstorm(
    body: CreateBrainstormRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """M-45 5 发散人格头脑风暴 + M-47 Decide 模式 advocate_<选项>
    Body: {"action":"ideas|decide","topic":"...","options":[...]}
    """
    from ..capability.brainstorm import BrainstormSession, DecideMode

    action = body.get("action", "ideas")
    topic = body.get("topic", "")
    result = {}
    try:
        if action == "ideas":
            session = BrainstormSession(topic)
            ideas = (
                session.generate_ideas_detailed()
                if body.get("detailed")
                else session.generate_ideas()
            )
            result["ideas"] = (
                ideas if isinstance(ideas, dict) else {k: v.__dict__ for k, v in ideas.items()}
            )
        elif action == "decide":
            options = body.get("options", [])
            dm = DecideMode(topic, options)
            result["advocates"] = dm.generate_advocates()
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # 修 38: 让 4xx 直接返回(不被包 500)

    except Exception as e:
        raise err_500(e, "brainstorm action failed:")
    return result

@router.post("/v1/capability/cross-iter")
async def capability_cross_iter(
    body: CreateCrossIterRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """M-50 Cross-iteration synthesis + M-52 Step-5 三种模式
    Body: {"action":"convergence|best_of_each|adoption|step5","iters":[{...}],...}
    """
    from ..capability.cross_iter_synth import (
        IterationSnapshot,
        Step5Mode,
        best_of_each_mode,
        convergence_mode,
        recommended_adoption_mode,
        run_step5,
    )

    action = body.get("action", "step5")
    iters = [IterationSnapshot(**i) for i in body.get("iters", [])]
    result = {}
    try:
        if action == "convergence":
            r = convergence_mode(iters)
            result = {
                "output": r.output,
                "sources": r.sources,
                "confidence": r.confidence,
                "mode": r.mode.value,
            }
        elif action == "best_of_each":
            r = best_of_each_mode(iters)
            result = {
                "output": r.output,
                "sources": r.sources,
                "confidence": r.confidence,
                "mode": r.mode.value,
            }
        elif action == "adoption":
            if len(iters) < 2:
                raise HTTPException(400, "adoption 需要至少 2 iter")
            r = recommended_adoption_mode(iters[-1], iters[-2])
            result = {
                "output": r.output,
                "sources": r.sources,
                "confidence": r.confidence,
                "mode": r.mode.value,
            }
        elif action == "step5":
            mode = Step5Mode(body.get("step5_mode", "sintesis_central"))
            r = run_step5(iters, mode)
            result = {"mode": r.mode.value, "output": r.output, "action_taken": r.action_taken}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # 修 38: 让 4xx 直接返回(不被包 500)

    except Exception as e:
        raise err_500(e, "cross_iter action failed:")
    return result

@router.post("/v1/capability/audit")
async def capability_audit(
    body: CreateAuditRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-31 Action Policy 增强 + A-35 Audit Gate (5 步协议)
    Body: {"action_id":"...","action_data":{"action":"read"},"policy_fn":null}
    """
    from ..capability.action_audit import AuditGate

    if not hasattr(capability_audit, "_gate"):
        capability_audit._gate = AuditGate()
    gate = capability_audit._gate
    action_id = body.get("action_id", "a1")
    action_data = body.get("action_data", {})
    try:
        log = gate.audit(action_id, action_data)
        return log.__dict__
    except HTTPException:
        raise  # patch v1.6.6: pass through 4xx

    except Exception as e:
        raise err_500(e, "audit failed:")

# ========== v1.5.9 Capability Endpoints — Wave 9 (HIGH 优先级) ==========
@router.post("/v1/capability/in-flight")
async def capability_in_flight(
    body: CreateInFlightRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-24 In-Flight Transition 检测 + A-25 Team Checkpoint Merge
    Body: {"action":"start|complete|in_flight|transition|merge_checkpoint|merge","session_id":"s1",...}
    """
    from ..capability.in_flight import (
        Checkpoint,
        InFlightDetector,
        Phase,
        TeamCheckpointMerger,
    )

    if not hasattr(capability_in_flight, "_detector"):
        capability_in_flight._detector = InFlightDetector(
            state_dir=body.get("state_dir", ".moai/state")
        )
    detector = capability_in_flight._detector
    action = body.get("action", "in_flight")
    result = {}
    try:
        if action == "start":
            sid = detector.record_start(Phase(body.get("phase", "analyze")), at=body.get("at"))
            result = {"session_id": sid}
        elif action == "complete":
            detector.record_complete(
                body["session_id"], Phase(body.get("phase", "analyze")), at=body.get("at")
            )
            result = {"completed": True}
        elif action == "in_flight":
            states = detector.detect_in_flight(at=body.get("at"))
            result = {"in_flight": [s.__dict__ for s in states], "count": len(states)}
        elif action == "transition":
            next_phase = detector.detect_phase_transition(body["session_id"])
            result = {"next_phase": next_phase.value if next_phase else None}
        elif action == "merge":
            if not hasattr(capability_in_flight, "_merger"):
                capability_in_flight._merger = TeamCheckpointMerger()
            merger = capability_in_flight._merger
            for ckpt in body.get("checkpoints", []):
                merger.add_checkpoint(
                    Checkpoint(
                        session_id=ckpt.get("session_id", "s1"),
                        phase=Phase(ckpt.get("phase", "analyze")),
                        data=ckpt.get("data", {}),
                        timestamp=ckpt.get("timestamp", time.time()),
                    )
                )
            result = {"merged": merger.merge()}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # 修 38: 让 4xx 直接返回(不被包 500)

    except Exception as e:
        raise err_500(e, "in_flight action failed:")
    return result

@router.post("/v1/capability/mx")
async def capability_mx(
    body: CreateMxRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-39 MX 注解系统 + A-40 fan-in + A-44 mx CLI
    Body: {"action":"parse|fanin|cli","text":"...","command":"list","file_path":"f.py"}
    """
    from ..capability.mx_annot import (
        compute_fanin,
        mx_cli,
        parse_mx_annotations,
    )

    action = body.get("action", "parse")
    result = {}
    try:
        if action == "parse":
            anns = parse_mx_annotations(
                body.get("text", ""),
                body.get("file_path", "f.py"),
                body.get("language", "python"),
            )
            result = {"annotations": [a.to_dict() for a in anns], "count": len(anns)}
        elif action == "fanin":
            anns = parse_mx_annotations(
                body.get("text", ""),
                body.get("file_path", "f.py"),
                body.get("language", "python"),
            )
            result = {"fanin": compute_fanin(anns)}
        elif action == "cli":
            anns = parse_mx_annotations(
                body.get("text", ""),
                body.get("file_path", "f.py"),
                body.get("language", "python"),
            )
            result = {"output": mx_cli(anns, body.get("command", "list"))}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # 修 38: 让 4xx 直接返回(不被包 500)

    except Exception as e:
        raise err_500(e, "mx action failed:")
    return result

@router.post("/v1/capability/tier-promo")
async def capability_tier_promo(
    body: CreateTierPromoRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-48 Tier Promotion (1/3/5/10 + confidence<0.70) + A-49 Sub-agent Boundary
    Body: {"action":"classify|record|can_spawn|cohabitation","evidence":[{...}],"weights":[...]}
    """
    from moa_gateway.capability.tier_promo import (
        Evidence,
        PromotionConfig,
        SubAgentBoundary,
        classify_tier_from_evidence,
        compute_tier,
    )

    action = body.get("action", "classify")
    result = {}
    try:
        if action == "classify":
            evidence = [Evidence(**e) for e in body.get("evidence", [])]
            cfg = PromotionConfig(
                tier_1_threshold=body.get("tier_1", 1),
                tier_2_threshold=body.get("tier_2", 3),
                tier_3_threshold=body.get("tier_3", 5),
                tier_4_threshold=body.get("tier_4", 10),
                confidence_threshold=body.get("confidence_threshold", 0.70),
            )
            tier = classify_tier_from_evidence(evidence, cfg)
            result = {"tier": tier.name, "evidence_count": len(evidence)}
        elif action == "compute":
            cfg = PromotionConfig()
            tier = compute_tier(
                body.get("count", 0),
                body.get("confidence", 0.5),
                cfg,
            )
            result = {"tier": tier.name}
        elif action == "can_spawn":
            boundary = SubAgentBoundary(
                body.get("parent_id", "p1"),
                body.get("allowed_children", []),
            )
            result = {"can_spawn": boundary.can_spawn(body.get("child_id", ""))}
        elif action == "cohabitation":
            b1 = SubAgentBoundary(body.get("parent_a", "p1"), body.get("children_a", []))
            b2 = SubAgentBoundary(body.get("parent_b", "p2"), body.get("children_b", []))
            result = {"cohabitation_safe": b1.cohabitation_check(body.get("parent_b", "p2"))}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # 修 38: 让 4xx 直接返回(不被包 500)

    except Exception as e:
        raise err_500(e, "tier_promo action failed:")
    return result

@router.post("/v1/capability/artifact")
async def capability_artifact(
    body: CreateArtifactRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-21 Artifact Schema 统一 + A-50 Tmux 面板编排 (CG mode)
    Body: {"action":"register|list_by_type|validate|add_pane|layout|safe_layout",...}
    """
    from ..capability.artifact import (
        Artifact,
        ArtifactType,
        SchemaRegistry,
        TmuxOrchestrator,
        TmuxPane,
    )

    if not hasattr(capability_artifact, "_registry"):
        capability_artifact._registry = SchemaRegistry()
    if not hasattr(capability_artifact, "_orchestrator"):
        capability_artifact._orchestrator = TmuxOrchestrator(
            max_visible=body.get("max_visible", 3)
        )
    reg = capability_artifact._registry
    orch = capability_artifact._orchestrator
    action = body.get("action", "register")
    result = {}
    try:
        if action == "register":
            artifact = Artifact(
                id=body["id"],
                name=body["name"],
                type=ArtifactType(body["type"]),
                description=body.get("description", ""),
                tags=body.get("tags", []),
                inputs=body.get("inputs", {}),
                outputs=body.get("outputs", {}),
                dependencies=body.get("dependencies", []),
                created_at=body.get("created_at", time.time()),
            )
            reg.register(artifact)
            result = {"registered": artifact.id}
        elif action == "list_by_type":
            t = ArtifactType(body.get("type", "agent"))
            arts = reg.list_by_type(t)
            result = {"artifacts": [a.to_dict() for a in arts]}
        elif action == "validate":
            artifact = Artifact(
                id=body.get("id", "test"),
                name=body.get("name", "test"),
                type=ArtifactType(body.get("type", "agent")),
                description=body.get("description", ""),
            )
            missing = reg.validate(artifact)
            result = {"missing_fields": missing, "valid": len(missing) == 0}
        elif action == "add_pane":
            pane = TmuxPane(
                pane_id=body.get("pane_id", "p1"),
                command=body.get("command", ""),
                cwd=body.get("cwd", "."),
                env_vars=body.get("env_vars", {}),
            )
            orch.add_pane(pane)
            result = {"added": pane.pane_id}
        elif action == "layout":
            result = {"panes": [p.__dict__ for p in orch.layout()]}
        elif action == "safe_layout":
            result = {"panes": [p.__dict__ for p in orch.safe_layout()]}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # 修 38: 让 4xx 直接返回(不被包 500)

    except Exception as e:
        raise err_500(e, "artifact action failed:")
    return result

@router.post("/v1/capability/frozen")
async def capability_frozen(
    body: CreateFrozenRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-19 Frozen Zone 4-enum + A-34 HARNESS_FROZEN_* 8 sentinels
    Body: {"action":"add|is_frozen|is_evolvable|can_modify|assert_modifiable","path":"/foo",...}
    """
    from ..capability.frozen_zone import (
        ALL_HARNESS_FROZEN_SENTINELS,
        FrozenEntry,
        FrozenRegistry,
        FrozenZoneError,
        Zone,
        assert_modifiable,
        can_modify,
    )

    if not hasattr(capability_frozen, "_registry"):
        capability_frozen._registry = FrozenRegistry()
    reg = capability_frozen._registry
    action = body.get("action", "is_frozen")
    result = {}
    try:
        if action == "add":
            entry = FrozenEntry(
                path=body["path"],
                zone=Zone(body["zone"]) if isinstance(body["zone"], str) else body["zone"],
                sentinel=body.get("sentinel", ""),
                reason=body.get("reason", ""),
                added_at=body.get("added_at", time.time()),
            )
            reg.add(entry)
            result = {"added": entry.path}
        elif action == "is_frozen":
            result = {"is_frozen": reg.is_frozen(body["path"])}
        elif action == "is_evolvable":
            result = {"is_evolvable": reg.is_evolvable(body["path"])}
        elif action == "can_modify":
            zone = Zone(body["zone"]) if isinstance(body["zone"], str) else body["zone"]
            result = {"can_modify": can_modify(body["path"], zone)}
        elif action == "assert_modifiable":
            try:
                assert_modifiable(body["path"], reg)
                result = {"modifiable": True}
            except FrozenZoneError as e:
                result = {
                    "modifiable": False,
                    "error": str(e),
                    "path": e.path,
                    "sentinel": e.sentinel,
                }
        elif action == "list_sentinels":
            result = {
                "sentinels": ALL_HARNESS_FROZEN_SENTINELS,
                "count": len(ALL_HARNESS_FROZEN_SENTINELS),
            }
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # 修 38: 让 4xx 直接返回(不被包 500)

    except Exception as e:
        raise err_500(e, "frozen action failed:")
    return result

# ========== v1.5.10 Capability Endpoints — Wave 10 (HIGH 优先级) ==========
@router.post("/v1/capability/turboquant")
async def capability_turboquant(
    body: CreateTurboquantRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """R-12 TurboQuant 5 级量化 (Q0/Q1/Q2/Q4/Q8) + 60 msg HARD CAP + 30 PRESERVE
    Body: {"action":"should_compress|apply","messages":[{role,content,timestamp}],"level":"Q4","hard_cap":60,"preserve":30}
    """
    from ..capability.turboquant import (
        Message,
        QuantLevel,
        TurboQuantConfig,
        apply_turboquant,
        should_compress,
    )

    msgs = [Message(**m) for m in body.get("messages", [])]
    level = QuantLevel[body.get("level", "Q4").upper()]
    cfg = TurboQuantConfig(
        hard_cap=body.get("hard_cap", 60),
        preserve=body.get("preserve", 30),
        level=level,
    )
    action = body.get("action", "apply")
    result = {}
    try:
        if action == "should_compress":
            result = {"should_compress": should_compress(msgs, cfg), "count": len(msgs)}
        elif action == "apply":
            compressed = apply_turboquant(msgs, cfg)
            result = {
                "compressed": [m.__dict__ for m in compressed],
                "original_count": len(msgs),
                "compressed_count": len(compressed),
            }
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # patch v1.6.6: pass through 4xx

    except Exception as e:
        raise err_500(e, "turboquant failed:")
    return result

@router.post("/v1/capability/moa-engine")
async def capability_moa_engine(
    body: CreateMoaEngineRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """M-01 MoA 引擎核心 (3 proposer + 1 aggregator) + M-05 协同
    Body: {"proposers":[{...}],"aggregator":{...},"query":"...","validate_only":false}
    """
    from ..capability.moa_engine import (
        Aggregator,
        Proposer,
        run_moa,
        validate_moa,
    )

    proposers = [Proposer(**p) for p in body.get("proposers", [])]
    aggregator = Aggregator(**body["aggregator"]) if body.get("aggregator") else None
    errors = validate_moa(proposers, aggregator)
    result = {"validation_errors": errors}
    if body.get("validate_only"):
        return result
    if errors:
        raise HTTPException(400, f"MoA config invalid: {errors}")
    # 简单 provider_fn:返回 mock 答案 (sync,call_proposer 内部会 asyncio.to_thread 包装)
    query = body.get("query", "")

    def mock_provider(actor, prompt):
        return (f"[{type(actor).__name__}:{actor.model_id}] response to: {prompt[:50]}", 100)

    try:
        moa_result = await run_moa(query, proposers, aggregator, mock_provider)
        result["moa_result"] = {
            "query": moa_result.query,
            "proposals": [p.__dict__ for p in moa_result.proposals],
            "aggregated": moa_result.aggregated,
            "total_tokens": moa_result.total_tokens,
            "total_latency_ms": moa_result.total_latency_ms,
        }
    except HTTPException:
        raise  # patch v1.6.6: pass through 4xx

    except Exception as e:
        raise err_500(e, "MoA run failed")
    return result

@router.post("/v1/capability/acceptance")
async def capability_acceptance(
    body: CreateAcceptanceRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-17 Acceptance Tree (Given/When/Then) + A-16 EARS/GEARS 5+6 模式
    Body: {"action":"add|parse_ears|validate_pattern|get_tree","criteria":[{...}],"text":"..."}
    """
    from ..capability.acceptance import (
        AcceptanceCriterion,
        AcceptanceTree,
        parse_ears,
        validate_pattern,
    )

    if not hasattr(capability_acceptance, "_trees"):
        capability_acceptance._trees = {}
    trees = capability_acceptance._trees
    action = body.get("action", "add")
    result = {}
    try:
        if action == "add":
            root_id = body.get("root_id", "root")
            if root_id not in trees:
                trees[root_id] = AcceptanceTree(root_id)
            tree = trees[root_id]
            for c in body.get("criteria", []):
                tree.add_criterion(AcceptanceCriterion(**c))
            result = {
                "root_id": root_id,
                "tree": {
                    "criteria_count": len(tree._criteria),
                },
            }
        elif action == "parse_ears":
            criteria = parse_ears(body.get("text", ""))
            result = {"criteria": [c.__dict__ for c in criteria], "count": len(criteria)}
        elif action == "validate_pattern":
            ac = AcceptanceCriterion(**body["criterion"])
            result = {"pattern": validate_pattern(ac)}
        elif action == "get_tree":
            root_id = body.get("root_id", "root")
            tree = trees.get(root_id)
            if tree is None:
                result = {"error": "tree not found"}
            else:
                result = {"criteria": {k: v.__dict__ for k, v in tree._criteria.items()}}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # patch v1.6.6: pass through 4xx

    except Exception as e:
        raise err_500(e, "acceptance failed:")
    return result

@router.post("/v1/capability/llm-merge")
async def capability_llm_merge(
    body: CreateLlmMergeRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """L-32 LLM 响应合并 (5 strategy) + L-33 LLM 降级 chain
    Body: {"action":"merge|fallback","responses":[{...}],"strategy":"concat","providers":["a","b"]}
    """
    from ..capability.llm_merge import (
        AllProvidersFailedError,
        FallbackChain,
        LLMResponse,
        MergeStrategy,
        merge_responses,
    )

    action = body.get("action", "merge")
    result = {}
    try:
        if action == "merge":
            responses = [LLMResponse(**r) for r in body.get("responses", [])]
            strategy = MergeStrategy[body.get("strategy", "concat").upper()]
            merged = merge_responses(responses, strategy)
            result = {
                "text": merged.text,
                "sources": merged.sources,
                "strategy": merged.strategy.value,
                "total_tokens": merged.total_tokens,
                "total_cost_usd": merged.total_cost_usd,
                "confidence": merged.confidence,
            }
        elif action == "fallback":
            providers = body.get("providers", [])
            chain = FallbackChain(providers)

            def call_fn(provider):
                # 简单 provider:基于 provider 名 返不同响应
                fail_at = body.get("fail_at", [])
                if provider in fail_at:
                    raise RuntimeError(f"provider {provider} failed")
                return LLMResponse(
                    source=provider,
                    text=f"ok from {provider}",
                    tokens=100,
                    latency_ms=200.0,
                    cost_usd=0.001,
                    confidence=0.9,
                )

            try:
                resp = chain.execute(call_fn)
                result = {"response": resp.__dict__}
            except AllProvidersFailedError as e:
                result = {"error": "all_failed", "providers": e.providers, "errors": e.errors}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # patch v1.6.6: pass through 4xx

    except Exception as e:
        raise err_500(e, "llm_merge failed:")
    return result

@router.post("/v1/capability/grace")
async def capability_grace(
    body: CreateGraceRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-37 7-day Grace Window (FAIL 7 天仅警告不阻塞)
    Body: {"action":"register|record_pass|record_fail|should_block|status","name":"...","at":...}
    """
    from ..capability.grace_window import (
        CheckRegistry,
        grace_status,
    )

    if not hasattr(capability_grace, "_registry"):
        capability_grace._registry = CheckRegistry()
    reg = capability_grace._registry
    action = body.get("action", "should_block")
    result = {}
    try:
        if action == "register":
            cid = reg.register(body.get("name", "default"))
            result = {"check_id": cid}
        elif action == "record_pass":
            reg.record_pass(body["check_id"])
            result = {"passed": True}
        elif action == "record_fail":
            reg.record_fail(body["check_id"], at=body.get("at"))
            result = {"failed": True}
        elif action == "should_block":
            result = {"should_block": reg.should_block(body["check_id"], at=body.get("at"))}
        elif action == "status":
            result = {"status": grace_status(body["check_id"], reg, at=body.get("at"))}
        elif action == "warnings":
            warnings = reg.get_warnings()
            result = {"warnings": [w.__dict__ for w in warnings], "count": len(warnings)}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise  # patch v1.6.6: pass through 4xx

    except Exception as e:
        raise err_500(e, "grace failed:")
    return result

# ========== Wave 11 Capability Endpoints (5 new) ==========

@router.post("/v1/capability/rag-search")
async def capability_rag_search(
    body: CreateRagSearchRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """R-09: 关键词重叠 RAG 检索 — 24h TTL 缓存, max_results 默认 3"""
    from ..capability.rag_search import rag_search

    try:
        query = body.get("query", "")
        corpus = body.get("corpus", [])
        max_results = int(body.get("max_results", 3))
        if not isinstance(corpus, list):
            raise HTTPException(400, "corpus must be a list")
        results = rag_search(query, corpus, max_results=max_results)
        return {"results": results, "count": len(results), "query": query}
    except HTTPException:
        raise

    except Exception as e:
        raise err_500(e, "rag_search failed:")

@router.post("/v1/capability/plan-act")
async def capability_plan_act(
    body: CreatePlanActRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """R-10: Plan/Act 模式解析 — 24+14 关键词 + 11+8 正则 → confidence"""
    from ..capability.plan_act import classify_mode

    try:
        query = body.get("query", "")
        result = classify_mode(query)
        return result
    except HTTPException:
        raise

    except Exception as e:
        raise err_500(e, "plan_act failed:")

@router.post("/v1/capability/channels")
async def capability_channels(
    body: CreateChannelsRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """R-23: CH1/CH2/CH3 三通道 fallback — subagent → CLI → API"""
    from ..capability.channels import (
        APIChannel,
        ChannelChain,
        ChannelType,
        CLIChannel,
        SubagentChannel,
        classify_error,
    )

    try:
        action = body.get("action", "execute")
        query = body.get("query", "")
        if action == "classify_error":
            exc = body.get("error", "")
            return {"classification": classify_error(exc)}
        elif action == "chain_info":
            return {
                "channels": [c.value for c in ChannelType],
                "order": ["ch1", "ch2", "ch3"],
                "fallback": "stop on first success",
            }
        elif action == "execute":
            enabled = body.get("enabled", ["ch1", "ch2", "ch3"])
            chs = []
            if "ch1" in enabled:
                chs.append(SubagentChannel())
            if "ch2" in enabled:
                chs.append(CLIChannel(sleep_ms=body.get("cli_latency_ms", 50)))
            if "ch3" in enabled:
                chs.append(APIChannel(sleep_ms=body.get("api_latency_ms", 150)))
            chain = ChannelChain(chs)
            result = await chain.execute(query, **body.get("kwargs", {}))
            return result
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise

    except Exception as e:
        raise err_500(e, "channels failed:")

@router.post("/v1/capability/reference-router")
async def capability_reference_router(
    body: CreateReferenceRouterRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """M-11: Reference 模型分流 — SHADOW/VALIDATE/VETO 4 策略"""
    from ..capability.reference_router import (
        ReferenceConfig,
        RefStrategy,
        route_with_reference,
    )

    try:
        query = body.get("query", "")
        strategy = body.get("strategy", "shadow")
        try:
            strat = RefStrategy(strategy)
        except ValueError:
            raise HTTPException(400, f"unknown strategy: {strategy}")
        cfg = ReferenceConfig(
            main_model=body.get("main_model", "main"),
            ref_model=body.get("ref_model", "ref"),
            strategy=strat,
            max_latency_ms=int(body.get("max_latency_ms", 5000)),
            cost_ratio_cap=float(body.get("cost_ratio_cap", 2.0)),
        )
        result = await route_with_reference(query, cfg)
        return result
    except HTTPException:
        raise

    except Exception as e:
        raise err_500(e, "reference_router failed:")

@router.post("/v1/capability/checkpoint")
async def capability_checkpoint(
    body: CreateCheckpointRequest,
    admin: dict[str, Any] = Depends(require_admin),  # 修 P0-4: 必须 admin,防任意文件写 RCE
):
    """A-23: 原子写 checkpoint 存储 — temp+rename, fsync, thread-safe

    修 P0-4 (security RCE):
    - 改用 require_admin(不再是 require_api_key)
    - 删 atomic_write action(给 API key 用户一份远程文件写原语 = RCE 风险)
    - root_dir 强制在白名单内(server cwd 或 ~/.moa-gateway/checkpoints)
    - name 严格限制 [a-zA-Z0-9_-]{1,64}
    """
    import re as _re

    from ..capability.checkpoint import CheckpointStore

    try:
        action = body.get("action", "save")
        # 修 P0-4: 强制白名单 root_dir(默认在 server cwd 内的安全路径)
        _allowed_roots = (
            os.path.abspath("./.moai/checkpoints"),
            os.path.abspath(os.path.expanduser("~/.moa-gateway/checkpoints")),
        )
        root = body.get("root_dir", _allowed_roots[0])
        root_abs = os.path.abspath(root)
        if not any(root_abs == r or root_abs.startswith(r + os.sep) for r in _allowed_roots):
            raise HTTPException(400, f"root_dir not in allowlist: {root_abs}")
        # 修 P0-4: name 严格白名单
        name = body.get("name", "default")
        if not _re.fullmatch(r"[a-zA-Z0-9_\-]{1,64}", name):
            raise HTTPException(400, "name must match [a-zA-Z0-9_-]{1,64}")
        store = CheckpointStore(root_dir=root_abs, max_keep=int(body.get("max_keep", 10)))
        if action == "save":
            payload = body.get("payload", {})
            # 修 P0-4: payload 也限大小(防内存炸弹)
            _raw = body.get("_raw_payload", "")
            if isinstance(_raw, str) and len(_raw) > 1024 * 1024:  # 1MB
                raise HTTPException(400, "payload too large (>1MB)")
            path = store.save(name, payload)
            return {"saved": True, "path": path, "name": name}
        elif action == "load":
            data = store.load(name)
            return {"name": name, "data": data, "found": data is not None}
        elif action == "list":
            items = store.list()
            return {"items": items, "count": len(items)}
        elif action == "delete":
            ok = store.delete(name)
            return {"deleted": ok, "name": name}
        elif action == "cleanup":
            older = body.get("older_than_seconds")
            removed = store.cleanup(older_than_seconds=older)
            return {"removed": removed, "older_than_seconds": older}
        else:
            # 修 P0-4: 删除 atomic_write action(那是 RCE 入口)
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise

    except Exception as e:
        raise err_500(e, "checkpoint failed:")

# ========== Wave 12 Capability Endpoints (5 new) ==========

@router.post("/v1/capability/audit")
async def capability_audit_cache(
    body: CreateAuditRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """A-36: 24h 审计缓存 — LRU + TTL"""
    from ..capability.audit_cache import AuditCache, AuditEvent

    try:
        action = body.get("action", "record")
        if action == "record":
            from uuid import uuid4

            ev = AuditEvent(
                event_id=body.get("event_id") or str(uuid4()),
                timestamp=body.get("timestamp", 0.0),
                event_type=body.get("event_type", "generic"),
                actor=body.get("actor", "anonymous"),
                resource=body.get("resource", ""),
                action=body.get("sub_action", "exec"),
                outcome=body.get("outcome", "allow"),
                metadata=body.get("metadata", {}),
            )
            # 模块级单例(避免每次请求 new 一个)
            if not hasattr(capability_audit_cache, "_cache"):
                capability_audit_cache._cache = AuditCache(
                    max_size=int(body.get("max_size", 10000)),
                    ttl_seconds=int(body.get("ttl_seconds", 86400)),
                )
            cache = capability_audit_cache._cache
            eid = cache.record(ev)
            return {"recorded": True, "event_id": eid}
        elif action == "query":
            cache = getattr(capability_audit_cache, "_cache", None)
            if not cache:
                return {"items": [], "count": 0}
            items = cache.query(
                event_type=body.get("event_type"),
                actor=body.get("actor"),
                since=body.get("since"),
                limit=int(body.get("limit", 100)),
            )
            return {"items": [e.__dict__ for e in items], "count": len(items)}
        elif action == "stats":
            cache = getattr(capability_audit_cache, "_cache", None)
            if not cache:
                return {"stats": {}, "count": 0}
            return {"stats": cache.stats(), "count": cache.count()}
        elif action == "cleanup":
            cache = getattr(capability_audit_cache, "_cache", None)
            if not cache:
                return {"removed": 0}
            removed = cache.cleanup()
            return {"removed": removed, "count": cache.count()}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise

    except Exception as e:
        raise err_500(e, "audit failed:")

@router.post("/v1/capability/canary")
async def capability_canary(
    body: CreateCanaryRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """S-36: Prompt Injection 金丝雀 — 4 策略注入 + 检测泄露"""
    from ..capability.prompt_canary import (
        CanaryDetector,
        CanaryStrategy,
    )

    try:
        action = body.get("action", "inject")
        if action == "inject":
            strat_name = body.get("strategy", "suffix")
            try:
                strategy = CanaryStrategy(strat_name)
            except ValueError:
                raise HTTPException(400, f"unknown strategy: {strat_name}")
            prompt = body.get("prompt", "")
            det = CanaryDetector(strategy=strategy)
            new_prompt, canary = det.inject(prompt)
            return {"prompt": new_prompt, "canary": canary, "strategy": strategy.value}
        elif action == "check":
            response = body.get("response", "")
            canary = body.get("canary", "")
            det = CanaryDetector()
            result = det.check(response, canary)
            result["classification"] = det.classify_response(response, canary)
            return result
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise

    except Exception as e:
        raise err_500(e, "canary failed:")

@router.post("/v1/capability/wrap-output")
async def capability_wrap_output(
    body: CreateWrapOutputRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """S-38: Output Wrapping — untrusted_tool_output 标签"""
    from ..capability.output_wrapping import (
        TrustLevel,
        needs_wrapping,
        sanitize_for_prompt,
        unwrap_output,
        wrap_output,
    )

    try:
        action = body.get("action", "wrap")
        if action == "wrap":
            content = body.get("content", "")
            source = body.get("source", "tool")
            trust_name = body.get("trust", "untrusted")
            try:
                trust = TrustLevel(trust_name)
            except ValueError:
                raise HTTPException(400, f"unknown trust: {trust_name}")
            wrapped = wrap_output(
                content, source, trust, max_length=int(body.get("max_length", 8192))
            )
            return {"wrapped": wrapped, "needs_wrapping": needs_wrapping(content)}
        elif action == "unwrap":
            wrapped = body.get("wrapped", "")
            data = unwrap_output(wrapped)
            if data is None:
                raise HTTPException(400, "invalid wrapped format")
            return data
        elif action == "sanitize":
            content = body.get("content", "")
            aggressive = body.get("aggressive", False)
            return {"sanitized": sanitize_for_prompt(content, aggressive=aggressive)}
        elif action == "needs_wrapping":
            return {"needs_wrapping": needs_wrapping(body.get("content", ""))}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise

    except Exception as e:
        raise err_500(e, "wrap_output failed:")

@router.post("/v1/capability/fuzzy-dedup")
async def capability_fuzzy_dedup(
    body: CreateFuzzyDedupRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """F-30: Fuzzy Dedup 指纹 — simhash + 暴力去重"""
    from ..capability.fuzzy_dedup import FuzzyDedupIndex, simhash

    try:
        action = body.get("action", "check")
        if not hasattr(capability_fuzzy_dedup, "_index"):
            capability_fuzzy_dedup._index = FuzzyDedupIndex(
                max_size=int(body.get("max_size", 10000))
            )
        idx = capability_fuzzy_dedup._index
        if action == "add":
            text = body.get("text", "")
            eid = idx.add(text, metadata=body.get("metadata"))
            h = simhash(text)
            return {"id": eid, "hash": h, "size": idx.size()}
        elif action == "check":
            text = body.get("text", "")
            threshold = float(body.get("threshold", 0.85))
            dups = idx.find_duplicates(text, threshold=threshold)
            return {
                "duplicates": [
                    {"id": did, "similarity": sim, "metadata": md} for did, sim, md in dups
                ],
                "count": len(dups),
                "size": idx.size(),
            }
        elif action == "simhash":
            return {"hash": simhash(body.get("text", ""))}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise

    except Exception as e:
        raise err_500(e, "fuzzy_dedup failed:")

@router.post("/v1/capability/input-fingerprint")
async def capability_input_fingerprint(
    body: CreateInputFingerprintRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """I-15: Input Fingerprint — 4 层 hash 指纹 + 碰撞检测"""
    from ..capability.input_fingerprint import (
        FingerprintStore,
        InputFingerprint,
    )

    try:
        action = body.get("action", "hash")
        if action == "hash":
            text = body.get("text", "")
            fp = InputFingerprint(text)
            return {"attrs": fp.attrs, "to_dict": fp.to_dict()}
        elif action == "similar":
            a = InputFingerprint(body.get("a", ""))
            b = InputFingerprint(body.get("b", ""))
            level = body.get("level", "normalized")
            return {"similarity": a.similar_to(b, level=level)}
        elif action == "store":
            if not hasattr(capability_input_fingerprint, "_store"):
                capability_input_fingerprint._store = FingerprintStore(
                    max_size=int(body.get("max_size", 50000))
                )
            store = capability_input_fingerprint._store
            if "text" in body:
                fp = store.add(body["text"], metadata=body.get("metadata"))
                return {"added": True, "attrs": fp.attrs, "size": store.size()}
            elif "collisions_with" in body:
                min_levels = int(body.get("min_levels", 2))
                collisions = store.find_collisions(
                    body["collisions_with"], min_levels=min_levels
                )
                return {"collisions": len(collisions), "size": store.size()}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise

    except Exception as e:
        raise err_500(e, "input_fingerprint failed:")

# ========== Wave 13 Capability Endpoints (5 new) ==========

@router.post("/v1/capability/tool-screening")
async def capability_tool_screening(
    body: CreateToolScreeningRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """S-37/A-37: 9 段 Tool Input 风险检测"""
    from ..capability.tool_screening import ToolScreener

    try:
        tool_name = body.get("tool_name", "unknown")
        arguments = body.get("arguments", {})
        screener = ToolScreener()
        findings = screener.screen(tool_name, arguments)
        return {
            "findings": [f.__dict__ for f in findings],
            "count": len(findings),
            "risk_level": screener.classify(findings).value,
            "should_block": screener.should_block(findings),
        }
    except HTTPException:
        raise

    except Exception as e:
        raise err_500(e, "tool_screening failed:")

@router.post("/v1/capability/anthropic-compat")
async def capability_anthropic_compat(
    body: CreateAnthropicCompatRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """L-04~L-15: Anthropic Messages API 兼容层"""
    from ..capability.anthropic_compat import (
        format_anthropic_error,
        format_anthropic_response,
        format_anthropic_sse_chunk,
        format_anthropic_tool_result,
        format_anthropic_tool_use,
        parse_anthropic_request,
    )

    try:
        action = body.get("action", "parse")
        if action == "parse":
            parsed = parse_anthropic_request(body.get("anthropic_request", {}))
            return parsed
        elif action == "format_response":
            return format_anthropic_response(body.get("chat_response", {}))
        elif action == "format_sse":
            return {
                "sse": format_anthropic_sse_chunk(
                    body.get("delta", ""), body.get("model", "unknown"), body.get("stop_reason")
                )
            }
        elif action == "format_tool_use":
            return format_anthropic_tool_use(
                body.get("tool_id", "toolu_xxx"),
                body.get("name", "tool"),
                body.get("input", {}),
            )
        elif action == "format_tool_result":
            return format_anthropic_tool_result(
                body.get("tool_use_id", "toolu_xxx"),
                body.get("content", ""),
                body.get("is_error", False),
            )
        elif action == "format_error":
            return format_anthropic_error(
                body.get("error_type", "api_error"), body.get("message", "")
            )
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise

    except Exception as e:
        raise err_500(e, "anthropic_compat failed:")

@router.post("/v1/capability/token-bucket")
async def capability_token_bucket(
    body: CreateTokenBucketRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """F-32/R-17: Token Bucket 限流算法 (lazy refill)"""
    from ..capability.token_bucket import MultiKeyTokenBucket

    try:
        action = body.get("action", "try_consume")
        if action == "try_consume":
            if not hasattr(capability_token_bucket, "_bucket"):
                capability_token_bucket._bucket = MultiKeyTokenBucket(
                    default_capacity=int(body.get("capacity", 60)),
                    default_refill_rate=float(body.get("refill_rate", 1.0)),
                )
            bucket = capability_token_bucket._bucket
            key = body.get("key", "default")
            tokens = int(body.get("tokens", 1))
            allowed = bucket.try_consume(key, tokens)
            state = bucket.all_states()
            return {
                "allowed": allowed,
                "key": key,
                "state": state.get(key, {}),
                "size": bucket.size(),
            }
        elif action == "state":
            if hasattr(capability_token_bucket, "_bucket"):
                return {
                    "states": capability_token_bucket._bucket.all_states(),
                    "size": capability_token_bucket._bucket.size(),
                }
            return {"states": {}, "size": 0}
        elif action == "cleanup":
            if hasattr(capability_token_bucket, "_bucket"):
                removed = capability_token_bucket._bucket.cleanup_inactive()
                return {"removed": removed, "size": capability_token_bucket._bucket.size()}
            return {"removed": 0, "size": 0}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise

    except Exception as e:
        raise err_500(e, "token_bucket failed:")

@router.post("/v1/capability/request-dedup")
async def capability_request_dedup(
    body: CreateRequestDedupRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """I-13/A-23: Request Dedup 重复请求检测"""
    from ..capability.request_dedup import (
        RequestDedupIndex,
    )

    try:
        if not hasattr(capability_request_dedup, "_index"):
            from ..capability.request_dedup import DedupStrategy as _DS

            strat_name = body.get("strategy", "normalized")
            try:
                strategy = _DS(strat_name)
            except ValueError:
                strategy = _DS.NORMALIZED
            capability_request_dedup._index = RequestDedupIndex(
                strategy=strategy,
                ttl_seconds=int(body.get("ttl_seconds", 60)),
                max_size=int(body.get("max_size", 10000)),
            )
        idx = capability_request_dedup._index
        method = body.get("method", "POST")
        path = body.get("path", "/")
        req_body = body.get("body")
        source = body.get("source", "default")
        action = body.get("action", "check")
        if action == "check":
            existing = idx.check(method, path, req_body, source)
            if existing is not None:
                return {
                    "is_duplicate": True,
                    "entry": existing.__dict__,
                    "has_cached_response": existing.response is not None,
                }
            return {"is_duplicate": False, "size": idx.size()}
        elif action == "record":
            resp = body.get("response")
            entry = idx.record(method, path, req_body, source, response=resp)
            return {"recorded": True, "entry": entry.__dict__, "size": idx.size()}
        elif action == "stats":
            return {"stats": idx.stats(), "size": idx.size()}
        elif action == "cleanup":
            removed = idx.cleanup()
            return {"removed": removed, "size": idx.size()}
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise

    except Exception as e:
        raise err_500(e, "request_dedup failed:")

@router.post("/v1/capability/trace")
async def capability_trace(
    body: CreateTraceRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """T-13/L-32: W3C Trace Propagation 链路追踪"""
    from ..capability.trace import (
        TraceCollector,
        format_traceparent,
        new_span,
        parse_traceparent,
    )

    try:
        if not hasattr(capability_trace, "_collector"):
            capability_trace._collector = TraceCollector(
                max_traces=int(body.get("max_traces", 10000))
            )
        collector = capability_trace._collector
        action = body.get("action", "start")
        if action == "start":
            traceparent = body.get("traceparent")
            ctx = collector.start_trace(traceparent)
            return {
                "trace_id": ctx.trace_id,
                "span_id": ctx.span_id,
                "traceparent": format_traceparent(ctx),
            }
        elif action == "span":
            trace_id = body.get("trace_id")
            if not trace_id:
                raise HTTPException(400, "trace_id required")
            # get parent ctx
            existing = collector.get_trace(trace_id)
            if not existing:
                raise HTTPException(404, f"trace {trace_id} not found")
            from ..capability.trace import TraceContext

            parent = TraceContext(
                trace_id=trace_id,
                parent_span_id=None,
                span_id=existing.get("span_id", ""),
                start_ts=existing.get("start_ts", 0.0),
                tags={},
                baggage={},
            )
            child = new_span(parent, body.get("name", "child"))
            collector.record_span(
                child, body.get("name", "child"), duration_ms=float(body.get("duration_ms", 0))
            )
            return {"trace_id": child.trace_id, "span_id": child.span_id}
        elif action == "end":
            trace_id = body.get("trace_id")
            if not trace_id:
                raise HTTPException(400, "trace_id required")
            from ..capability.trace import TraceContext

            ctx = TraceContext(
                trace_id=trace_id,
                parent_span_id=None,
                span_id=body.get("span_id", ""),
                start_ts=0.0,
                tags={},
                baggage={},
            )
            collector.end_trace(ctx, status=body.get("status", "ok"), error=body.get("error"))
            return {"ended": True, "trace_id": trace_id}
        elif action == "get":
            trace_id = body.get("trace_id")
            if not trace_id:
                raise HTTPException(400, "trace_id required")
            return collector.get_trace(trace_id) or {}
        elif action == "query":
            return {
                "traces": collector.query(
                    since_ts=body.get("since_ts"),
                    min_duration_ms=body.get("min_duration_ms"),
                    status=body.get("status"),
                    limit=int(body.get("limit", 100)),
                ),
                "stats": collector.stats(),
            }
        elif action == "parse_traceparent":
            tp = body.get("traceparent", "")
            ctx = parse_traceparent(tp)
            if ctx is None:
                return {"parsed": None}
            return {
                "parsed": {
                    "trace_id": ctx.trace_id,
                    "span_id": ctx.span_id,
                    "traceparent": format_traceparent(ctx),
                }
            }
        else:
            raise HTTPException(400, f"unknown action: {action}")
    except HTTPException:
        raise

    except Exception as e:
        raise err_500(e, "trace failed:")
