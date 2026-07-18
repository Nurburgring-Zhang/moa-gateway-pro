"""Pydantic request models for all endpoints.

Auto-generated from server.py endpoint scan. Each endpoint with body fields
has a corresponding *Request Pydantic BaseModel. Pass the model as the request
body in FastAPI to get automatic 422 validation + OpenAPI schema generation.

Usage:
    from .req_models import MoAEvalRequest
    @app.post("/v1/moa/eval")
    async def moa_eval(req: MoAEvalRequest): ...
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, ConfigDict

# ============ moa ============

class _DictLikeMixin:
    """Mixin that adds dict-like access to Pydantic models.

    Allows existing endpoint code that does body.get("key", default) and body["key"]
    to keep working after the body type changed from Dict to Pydantic model.
    """
    def __getitem__(self, key):
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key, default=None):
        if hasattr(self, key):
            val = getattr(self, key)
            if val is not None:
                return val
        return default


class _ModelBase(_DictLikeMixin, BaseModel):
    model_config = ConfigDict(strict=False, extra="ignore", protected_namespaces=())
    # Field type set to Any for flexibility — actual validation done by endpoint code



class _DictLikeMixin:
    """Mixin that adds dict-like access to Pydantic models.

    Allows existing endpoint code that does body.get("key", default) and body["key"]
    to keep working after the body type changed from Dict to Pydantic model.
    """
    def __getitem__(self, key):
        # Get attribute or raise KeyError (matching dict behavior)
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key, default=None):
        # Like dict.get, return default if attribute missing or None
        if hasattr(self, key):
            val = getattr(self, key)
            if val is not None:
                return val
        return default


class CreateMoaEvalRequest(_ModelBase):
    """Request body for POST /v1/moa/eval."""
    candidates: Optional[Any] = None
    query: Optional[Any] = None
    reference_answer: Optional[Any] = None
    temperature: Optional[Any] = None
class CreateMoaSimilarityRequest(_ModelBase):
    """Request body for POST /v1/moa/similarity."""
    candidate_a: Optional[Any] = None
    candidate_b: Optional[Any] = None
    model_id: Optional[Any] = None
    query: Optional[Any] = None
class CreateMoaFlaskRequest(_ModelBase):
    """Request body for POST /v1/moa/flask."""
    judge_model: Optional[Any] = None
    query: Optional[Any] = None
    reference: Optional[Any] = None
    response: Optional[Any] = None
class CreateMoaBenchmarkRequest(_ModelBase):
    """Request body for POST /v1/moa/benchmark."""
    category: Optional[Any] = None
    limit: Optional[Any] = None
    presets: Optional[Any] = None
class CreateMoaCostParetoRequest(_ModelBase):
    """Request body for POST /v1/moa/cost-pareto."""
    presets: Optional[Any] = None
    prompts: Optional[Any] = None
class UpdateMoaPromptsNameRequest(_ModelBase):
    """Request body for PUT /v1/moa/prompts/{name}."""
    content: Optional[Any] = None
# ============ capability ============

class CreateSecretScanRequest(_ModelBase):
    """Request body for POST /v1/capability/secret-scan."""
    fail_on: Optional[Any] = None
    no_block: Optional[Any] = None
    path: Optional[Any] = None
class CreateGroupThinkCheckRequest(_ModelBase):
    """Request body for POST /v1/capability/group-think-check."""
    block_threshold: Optional[Any] = None
    members: Optional[Any] = None
    rounds: Optional[Any] = None
    session_id: Optional[Any] = None
    warn_threshold: Optional[Any] = None
class CreateEnsembleVoteRequest(_ModelBase):
    """Request body for POST /v1/capability/ensemble-vote."""
    method: Optional[Any] = None
    votes: Optional[Any] = None
class CreateShouldRebalanceRequest(_ModelBase):
    """Request body for POST /v1/capability/should-rebalance."""
    config: Optional[Dict[str, Any]] = None
    stats: Optional[Dict[str, Any]] = None

class CreateCostEstimateRequest(_ModelBase):
    """Request body for POST /v1/capability/cost-estimate."""
    channels: Optional[Any] = None
    format: Optional[Any] = None
    include_fallback: Optional[Any] = None
    input_tokens: Optional[Any] = None
    output_tokens: Optional[Any] = None
class CreateGateL0Request(_ModelBase):
    """Request body for POST /v1/capability/gate-l0."""
    query: Optional[Any] = None
class CreateScorePanelRequest(_ModelBase):
    """Request body for POST /v1/capability/score-panel."""
    answer: Optional[Any] = None
    query: Optional[Any] = None
class CreateCalculateMaxTokensRequest(_ModelBase):
    """Request body for POST /v1/capability/calculate-max-tokens."""
    input_tokens: Optional[Any] = None
    model_id: Optional[Any] = None
    requested_output: Optional[Any] = None
    safety_margin: Optional[Any] = None
class CreateEstimateCostRequest(_ModelBase):
    """Request body for POST /v1/capability/estimate-cost."""
    input_tokens: Optional[Any] = None
    model_id: Optional[Any] = None
    output_tokens: Optional[Any] = None
class CreateQuotaCheckRequest(_ModelBase):
    """Request body for POST /v1/capability/quota-check."""
    burn_rate_per_hour: Optional[Any] = None
    last_updated: Optional[Any] = None
    requested: Optional[Any] = None
    windows: Optional[Any] = None
class CreateQuotaRecordRequest(_ModelBase):
    """Request body for POST /v1/capability/quota-record."""
    at: Optional[Any] = None
    last_updated: Optional[Any] = None
    tokens: Optional[Any] = None
    windows: Optional[Any] = None
class CreateMoaNLayerRequest(_ModelBase):
    """Request body for POST /v1/capability/moa-n-layer."""
    aggregators: Optional[Any] = None
    max_total_tokens: Optional[Any] = None
    proposers: Optional[Any] = None
    query: Optional[Any] = None
    temperature: Optional[Any] = None
class CreateConvergentDetectRequest(_ModelBase):
    """Request body for POST /v1/capability/convergent-detect."""
    min_support: Optional[Any] = None
    proposals: Optional[Any] = None
    viability_scores: Optional[Dict[str, Any]] = None

class CreateActionPolicyRequest(_ModelBase):
    """Request body for POST /v1/capability/action-policy."""
    command: Optional[Any] = None
    rules: Optional[Any] = None
class CreateEmbeddingsRequest(_ModelBase):
    """Request body for POST /v1/capability/embeddings."""
    dim: Optional[Any] = None
    input: Optional[Any] = None
    model: Optional[Any] = None
class CreateSemanticSearchRequest(_ModelBase):
    """Request body for POST /v1/capability/semantic-search."""
    dim: Optional[Any] = None
    documents: Optional[Any] = None
    query: Optional[Any] = None
    top_k: Optional[Any] = None
class CreatePromptFeaturesRequest(_ModelBase):
    """Request body for POST /v1/capability/prompt-features."""
    text: Optional[Any] = None
class CreateProviderHealthRequest(_ModelBase):
    """Request body for POST /v1/capability/provider-health."""
    prefer_tier: Optional[Any] = None
    providers: Optional[Any] = None
class CreateContextCleanRequest(_ModelBase):
    """Request body for POST /v1/capability/context-clean."""
    max_total_chars: Optional[Any] = None
    messages: Optional[Any] = None
class CreateSelfHealRequest(_ModelBase):
    """Request body for POST /v1/capability/self-heal."""
    action: Optional[Any] = None
    at: Optional[Any] = None
    endpoint_id: Optional[Any] = None
    endpoints: Optional[Any] = None
    reason: Optional[Any] = None
class CreateMultiModeSynthRequest(_ModelBase):
    """Request body for POST /v1/capability/multi-mode-synth."""
    curr_proposals: Optional[Any] = None
    mode: Optional[Any] = None
    prev_proposals: Optional[Any] = None
    proposals: Optional[Any] = None
    scores: Optional[Any] = None
    target_chars: Optional[Any] = None
class CreateConflictArbitrateRequest(_ModelBase):
    """Request body for POST /v1/capability/conflict-arbitrate."""
    fuse: Optional[Any] = None
    options: Optional[Any] = None
    query: Optional[Any] = None
class CreateSectionViabilityRequest(_ModelBase):
    """Request body for POST /v1/capability/section-viability."""
    proposal_idx: Optional[Any] = None
    text: Optional[Any] = None
class CreateFeedbackIterRequest(_ModelBase):
    """Request body for POST /v1/capability/feedback-iter."""
    history_path: Optional[Any] = None
    record: Optional[Dict[str, Any]] = None

class CreateStreamAggregateRequest(_ModelBase):
    """Request body for POST /v1/capability/stream-aggregate."""
    fail_prob: Optional[Any] = None
    model: Optional[Any] = None
    prompt: Optional[Any] = None
class CreatePerProviderRlRequest(_ModelBase):
    """Request body for POST /v1/capability/per-provider-rl."""
    action: Optional[Any] = None
    at: Optional[Any] = None
    concurrent: Optional[Any] = None
    cooldown_seconds: Optional[Any] = None
    input_tokens: Optional[Any] = None
    limit_config: Optional[Dict[str, Any]] = None
    limits: Optional[Dict[str, Any]] = None
    provider: Optional[Any] = None
    request_count: Optional[Any] = None
class CreateTierRecalibrateRequest(_ModelBase):
    """Request body for POST /v1/capability/tier-recalibrate."""
    tiers: Optional[Any] = None
class CreateConsumptionIntelRequest(_ModelBase):
    """Request body for POST /v1/capability/consumption-intel."""
    context: Optional[Dict[str, Any]] = None
    endpoints: Optional[Any] = None
class CreateImportanceScoreRequest(_ModelBase):
    """Request body for POST /v1/capability/importance-score."""
    messages: Optional[Any] = None
    threshold: Optional[Any] = None
    top_k: Optional[Any] = None
class CreateQuorumCheckRequest(_ModelBase):
    """Request body for POST /v1/capability/quorum-check."""
    at: Optional[Any] = None
    force_close: Optional[Any] = None
    grace_seconds: Optional[Any] = None
    judge_response: Optional[Any] = None
    participants: Optional[Any] = None
    required: Optional[Any] = None
    response_a: Optional[Any] = None
    response_b: Optional[Any] = None
    wait_for_laggards: Optional[Any] = None
class CreateModelEntryRequest(_ModelBase):
    """Request body for POST /v1/capability/model-entry."""
    filter: Optional[Dict[str, Any]] = None
    max_budget_input: Optional[Any] = None
    max_budget_output: Optional[Any] = None
    models: Optional[Any] = None
    query_modalities: Optional[Any] = None
    sort: Optional[Any] = None
class CreateToolReplayRequest(_ModelBase):
    """Request body for POST /v1/capability/tool-replay."""
    proposals: Optional[Any] = None
    recent_count: Optional[Any] = None
    window: Optional[Any] = None
class CreateHookEventsRequest(_ModelBase):
    """Request body for POST /v1/capability/hook-events."""
    action: Optional[Any] = None
    data: Optional[Dict[str, Any]] = None
    event: Optional[Any] = None
    max_iter: Optional[Any] = None
    session_id: Optional[Any] = None
    stage: Optional[Any] = None
    timestamp: Optional[Any] = None
class CreateMetaPromptRequest(_ModelBase):
    """Request body for POST /v1/capability/meta-prompt."""
    action: Optional[Any] = None
    context: Optional[Any] = None
    options: Optional[Any] = None
    query: Optional[Any] = None
    role_a: Optional[Any] = None
    role_b: Optional[Any] = None
class CreateTaskTreeRequest(_ModelBase):
    """Request body for POST /v1/capability/task-tree."""
    action: Optional[Any] = None
    status: Optional[Any] = None
    task_id: Optional[Any] = None
    tasks: Optional[Any] = None
class CreateDistillRequest(_ModelBase):
    """Request body for POST /v1/capability/distill."""
    apply_bias_correction: Optional[Any] = None
    evaluations: Optional[Any] = None
    keep_ratio: Optional[Any] = None
    proposals: Optional[Any] = None
class CreateRerankRequest(_ModelBase):
    """Request body for POST /v1/capability/rerank."""
    documents: Optional[Any] = None
    latency_budget_ms: Optional[Any] = None
    query: Optional[Any] = None
    stream_chunks: Optional[Any] = None
    top_n: Optional[Any] = None
class CreateGoalEvalRequest(_ModelBase):
    """Request body for POST /v1/capability/goal-eval."""
    baseline: Optional[Any] = None
    claim: Optional[Any] = None
    evidence: Optional[Any] = None
    gaps: Optional[Any] = None
    generate_ceiling: Optional[Any] = None
    goals: Optional[Any] = None
    output: Optional[Any] = None
    residual_risk: Optional[Any] = None
class CreateAutoConvergeRequest(_ModelBase):
    """Request body for POST /v1/capability/auto-converge."""
    calibrate_samples: Optional[Any] = None
    calibrate_score: Optional[Any] = None
    classify_events: Optional[Any] = None
    config: Optional[Dict[str, Any]] = None
    epsilon: Optional[Any] = None
    history: Optional[Any] = None
    new_score: Optional[Any] = None
    stagnation_threshold: Optional[Any] = None
    state: Optional[Any] = None
class CreateSubagentCommsRequest(_ModelBase):
    """Request body for POST /v1/capability/subagent-comms."""
    action: Optional[Any] = None
    assignee: Optional[Any] = None
    content: Optional[Any] = None
    holder: Optional[Any] = None
    kind: Optional[Any] = None
    lock_id: Optional[Any] = None
    parent: Optional[Any] = None
    parent_msg_id: Optional[Any] = None
    parent_task_id: Optional[Any] = None
    session_id: Optional[Any] = None
    sessions: Optional[Any] = None
    status: Optional[Any] = None
    task_id: Optional[Any] = None
    timeout: Optional[Any] = None
    title: Optional[Any] = None
    to_session: Optional[Any] = None
class CreateVersionRequest(_ModelBase):
    """Request body for POST /v1/capability/version."""
    action: Optional[Any] = None
    content: Optional[Any] = None
    created_by: Optional[Any] = None
    critique: Optional[Any] = None
    improvement: Optional[Any] = None
    judge_response: Optional[Any] = None
    parent: Optional[Any] = None
    proposal_id: Optional[Any] = None
    v1: Optional[Any] = None
    v2: Optional[Any] = None
class CreateConfigRequest(_ModelBase):
    """Request body for POST /v1/capability/config."""
    action: Optional[Any] = None
    explicit: Optional[Any] = None
    key: Optional[Any] = None
    layer: Optional[Any] = None
    layers: Optional[Dict[str, Any]] = None
    mode: Optional[Any] = None
    value: Optional[Any] = None
class CreateBubbleRequest(_ModelBase):
    """Request body for POST /v1/capability/bubble."""
    action: Optional[Any] = None
    action_desc: Optional[Any] = None
    agent_id: Optional[Any] = None
    decision: Optional[Any] = None
    event_id: Optional[Any] = None
    event_type: Optional[Any] = None
    n: Optional[Any] = None
    parent_id: Optional[Any] = None
    payload: Optional[Dict[str, Any]] = None
    reason: Optional[Any] = None
    request_id: Optional[Any] = None
    timestamp: Optional[Any] = None
class CreateRouteRequest(_ModelBase):
    """Request body for POST /v1/capability/route."""
    action: Optional[Any] = None
    file_count: Optional[Any] = None
    files: Optional[Any] = None
    is_bugfix: Optional[Any] = None
    is_docs: Optional[Any] = None
    severity: Optional[Any] = None
    single_domain: Optional[Any] = None
    task: Optional[Any] = None
    tier: Optional[Any] = None
class CreateSessionLockRequest(_ModelBase):
    """Request body for POST /v1/capability/session-lock."""
    action: Optional[Any] = None
    lock_id: Optional[Any] = None
    retry_interval: Optional[Any] = None
    session_id: Optional[Any] = None
    timeout: Optional[Any] = None
    ttl: Optional[Any] = None
class CreateFlaskRequest(_ModelBase):
    """Request body for POST /v1/capability/flask."""
    answer: Optional[Any] = None
    query: Optional[Any] = None
    tasks: Optional[Any] = None
class CreateEloRequest(_ModelBase):
    """Request body for POST /v1/capability/elo."""
    action: Optional[Any] = None
    ci: Optional[Any] = None
    k_factor: Optional[Any] = None
    matches: Optional[Any] = None
    model_ids: Optional[Any] = None
    n_resamples: Optional[Any] = None
    ratings_before: Optional[Any] = None
    strategy: Optional[Any] = None
    workers: Optional[Any] = None
class CreateBrainstormRequest(_ModelBase):
    """Request body for POST /v1/capability/brainstorm."""
    action: Optional[Any] = None
    detailed: Optional[Any] = None
    options: Optional[Any] = None
    topic: Optional[Any] = None
class CreateCrossIterRequest(_ModelBase):
    """Request body for POST /v1/capability/cross-iter."""
    action: Optional[Any] = None
    iters: Optional[Any] = None
    step5_mode: Optional[Any] = None
class CreateAuditRequest(_ModelBase):
    """Request body for POST /v1/capability/audit."""
    action_data: Optional[Dict[str, Any]] = None
    action_id: Optional[Any] = None
class CreateInFlightRequest(_ModelBase):
    """Request body for POST /v1/capability/in-flight."""
    action: Optional[Any] = None
    at: Optional[Any] = None
    checkpoints: Optional[Any] = None
    phase: Optional[Any] = None
    session_id: Optional[Any] = None
    state_dir: Optional[Any] = None
class CreateMxRequest(_ModelBase):
    """Request body for POST /v1/capability/mx."""
    action: Optional[Any] = None
    command: Optional[Any] = None
    file_path: Optional[Any] = None
    language: Optional[Any] = None
    text: Optional[Any] = None
class CreateTierPromoRequest(_ModelBase):
    """Request body for POST /v1/capability/tier-promo."""
    action: Optional[Any] = None
    allowed_children: Optional[Any] = None
    child_id: Optional[Any] = None
    children_a: Optional[Any] = None
    children_b: Optional[Any] = None
    confidence: Optional[Any] = None
    confidence_threshold: Optional[Any] = None
    count: Optional[Any] = None
    evidence: Optional[Any] = None
    parent_a: Optional[Any] = None
    parent_b: Optional[Any] = None
    parent_id: Optional[Any] = None
    tier_1: Optional[Any] = None
    tier_2: Optional[Any] = None
    tier_3: Optional[Any] = None
    tier_4: Optional[Any] = None
class CreateArtifactRequest(_ModelBase):
    """Request body for POST /v1/capability/artifact."""
    action: Optional[Any] = None
    command: Optional[Any] = None
    created_at: Optional[Any] = None
    cwd: Optional[Any] = None
    dependencies: Optional[Any] = None
    description: Optional[Any] = None
    env_vars: Optional[Dict[str, Any]] = None
    id: Optional[Any] = None
    inputs: Optional[Dict[str, Any]] = None
    max_visible: Optional[Any] = None
    name: Optional[Any] = None
    outputs: Optional[Dict[str, Any]] = None
    pane_id: Optional[Any] = None
    tags: Optional[Any] = None
    type: Optional[Any] = None
class CreateFrozenRequest(_ModelBase):
    """Request body for POST /v1/capability/frozen."""
    action: Optional[Any] = None
    added_at: Optional[Any] = None
    path: Optional[Any] = None
    reason: Optional[Any] = None
    sentinel: Optional[Any] = None
    zone: Optional[Any] = None
class CreateTurboquantRequest(_ModelBase):
    """Request body for POST /v1/capability/turboquant."""
    action: Optional[Any] = None
    hard_cap: Optional[Any] = None
    level: Optional[Any] = None
    messages: Optional[Any] = None
    preserve: Optional[Any] = None
class CreateMoaEngineRequest(_ModelBase):
    """Request body for POST /v1/capability/moa-engine."""
    aggregator: Optional[Any] = None
    proposers: Optional[Any] = None
    query: Optional[Any] = None
    validate_only: Optional[Any] = None
class CreateAcceptanceRequest(_ModelBase):
    """Request body for POST /v1/capability/acceptance."""
    action: Optional[Any] = None
    criteria: Optional[Any] = None
    criterion: Optional[Any] = None
    root_id: Optional[Any] = None
    text: Optional[Any] = None
class CreateLlmMergeRequest(_ModelBase):
    """Request body for POST /v1/capability/llm-merge."""
    action: Optional[Any] = None
    providers: Optional[Any] = None
    responses: Optional[Any] = None
    strategy: Optional[Any] = None
class CreateGraceRequest(_ModelBase):
    """Request body for POST /v1/capability/grace."""
    action: Optional[Any] = None
    at: Optional[Any] = None
    check_id: Optional[Any] = None
    name: Optional[Any] = None
class CreateRagSearchRequest(_ModelBase):
    """Request body for POST /v1/capability/rag-search."""
    corpus: Optional[Any] = None
    max_results: Optional[Any] = None
    query: Optional[Any] = None
class CreatePlanActRequest(_ModelBase):
    """Request body for POST /v1/capability/plan-act."""
    query: Optional[Any] = None
class CreateChannelsRequest(_ModelBase):
    """Request body for POST /v1/capability/channels."""
    action: Optional[Any] = None
    api_latency_ms: Optional[Any] = None
    cli_latency_ms: Optional[Any] = None
    enabled: Optional[Any] = None
    error: Optional[Any] = None
    kwargs: Optional[Dict[str, Any]] = None
    query: Optional[Any] = None
class CreateReferenceRouterRequest(_ModelBase):
    """Request body for POST /v1/capability/reference-router."""
    cost_ratio_cap: Optional[Any] = None
    main_model: Optional[Any] = None
    max_latency_ms: Optional[Any] = None
    query: Optional[Any] = None
    ref_model: Optional[Any] = None
    strategy: Optional[Any] = None
class CreateCheckpointRequest(_ModelBase):
    """Request body for POST /v1/capability/checkpoint."""
    _raw_payload: Optional[Any] = None
    action: Optional[Any] = None
    max_keep: Optional[Any] = None
    name: Optional[Any] = None
    older_than_seconds: Optional[Any] = None
    payload: Optional[Dict[str, Any]] = None
    root_dir: Optional[Any] = None
class CreateCanaryRequest(_ModelBase):
    """Request body for POST /v1/capability/canary."""
    action: Optional[Any] = None
    canary: Optional[Any] = None
    prompt: Optional[Any] = None
    response: Optional[Any] = None
    strategy: Optional[Any] = None
class CreateWrapOutputRequest(_ModelBase):
    """Request body for POST /v1/capability/wrap-output."""
    action: Optional[Any] = None
    aggressive: Optional[Any] = None
    content: Optional[Any] = None
    max_length: Optional[Any] = None
    source: Optional[Any] = None
    trust: Optional[Any] = None
    wrapped: Optional[Any] = None
class CreateFuzzyDedupRequest(_ModelBase):
    """Request body for POST /v1/capability/fuzzy-dedup."""
    action: Optional[Any] = None
    max_size: Optional[Any] = None
    metadata: Optional[Any] = None
    text: Optional[Any] = None
    threshold: Optional[Any] = None
class CreateInputFingerprintRequest(_ModelBase):
    """Request body for POST /v1/capability/input-fingerprint."""
    a: Optional[Any] = None
    action: Optional[Any] = None
    b: Optional[Any] = None
    collisions_with: Optional[Any] = None
    level: Optional[Any] = None
    max_size: Optional[Any] = None
    metadata: Optional[Any] = None
    min_levels: Optional[Any] = None
    text: Optional[Any] = None
class CreateToolScreeningRequest(_ModelBase):
    """Request body for POST /v1/capability/tool-screening."""
    arguments: Optional[Dict[str, Any]] = None
    tool_name: Optional[Any] = None
class CreateAnthropicCompatRequest(_ModelBase):
    """Request body for POST /v1/capability/anthropic-compat."""
    action: Optional[Any] = None
    anthropic_request: Optional[Dict[str, Any]] = None
    chat_response: Optional[Dict[str, Any]] = None
    content: Optional[Any] = None
    delta: Optional[Any] = None
    error_type: Optional[Any] = None
    input: Optional[Dict[str, Any]] = None
    is_error: Optional[Any] = None
    message: Optional[Any] = None
    model: Optional[Any] = None
    name: Optional[Any] = None
    stop_reason: Optional[Any] = None
    tool_id: Optional[Any] = None
    tool_use_id: Optional[Any] = None
class CreateTokenBucketRequest(_ModelBase):
    """Request body for POST /v1/capability/token-bucket."""
    action: Optional[Any] = None
    capacity: Optional[Any] = None
    key: Optional[Any] = None
    refill_rate: Optional[Any] = None
    tokens: Optional[Any] = None
class CreateRequestDedupRequest(_ModelBase):
    """Request body for POST /v1/capability/request-dedup."""
    action: Optional[Any] = None
    body: Optional[Any] = None
    max_size: Optional[Any] = None
    method: Optional[Any] = None
    path: Optional[Any] = None
    response: Optional[Any] = None
    source: Optional[Any] = None
    strategy: Optional[Any] = None
    ttl_seconds: Optional[Any] = None
class CreateTraceRequest(_ModelBase):
    """Request body for POST /v1/capability/trace."""
    action: Optional[Any] = None
    duration_ms: Optional[Any] = None
    error: Optional[Any] = None
    limit: Optional[Any] = None
    max_traces: Optional[Any] = None
    min_duration_ms: Optional[Any] = None
    name: Optional[Any] = None
    since_ts: Optional[Any] = None
    span_id: Optional[Any] = None
    status: Optional[Any] = None
    trace_id: Optional[Any] = None
    traceparent: Optional[Any] = None
# ============ agent ============

class CreateAgentDispatchRequest(_ModelBase):
    """Request body for POST /v1/agent/dispatch."""
    method: Optional[Any] = None
    payload: Optional[Any] = None
    service: Optional[Any] = None
class CreateAgentDispatchBatchRequest(_ModelBase):
    """Request body for POST /v1/agent/dispatch_batch."""
    calls: Optional[Any] = None
class CreateAgentWorkflowRegisterRequest(_ModelBase):
    """Request body for POST /v1/agent/workflow/register."""
    description: Optional[Any] = None
    name: Optional[Any] = None
    steps: Optional[Any] = None
class CreateAgentWorkflowRunRequest(_ModelBase):
    """Request body for POST /v1/agent/workflow/run."""
    input: Optional[Any] = None
    name: Optional[Any] = None
# ============ Model registry ============

# Maps endpoint path → Request model
ENDPOINT_MODELS: Dict[str, type[BaseModel]] = {
    "/v1/moa/eval": CreateMoaEvalRequest,
    "/v1/moa/similarity": CreateMoaSimilarityRequest,
    "/v1/moa/flask": CreateMoaFlaskRequest,
    "/v1/moa/benchmark": CreateMoaBenchmarkRequest,
    "/v1/moa/cost-pareto": CreateMoaCostParetoRequest,
    "/v1/moa/prompts/{name}": UpdateMoaPromptsNameRequest,
    "/v1/capability/secret-scan": CreateSecretScanRequest,
    "/v1/capability/group-think-check": CreateGroupThinkCheckRequest,
    "/v1/capability/ensemble-vote": CreateEnsembleVoteRequest,
    "/v1/capability/should-rebalance": CreateShouldRebalanceRequest,
    "/v1/capability/cost-estimate": CreateCostEstimateRequest,
    "/v1/capability/gate-l0": CreateGateL0Request,
    "/v1/capability/score-panel": CreateScorePanelRequest,
    "/v1/capability/calculate-max-tokens": CreateCalculateMaxTokensRequest,
    "/v1/capability/estimate-cost": CreateEstimateCostRequest,
    "/v1/capability/quota-check": CreateQuotaCheckRequest,
    "/v1/capability/quota-record": CreateQuotaRecordRequest,
    "/v1/capability/moa-n-layer": CreateMoaNLayerRequest,
    "/v1/capability/convergent-detect": CreateConvergentDetectRequest,
    "/v1/capability/action-policy": CreateActionPolicyRequest,
    "/v1/capability/embeddings": CreateEmbeddingsRequest,
    "/v1/capability/semantic-search": CreateSemanticSearchRequest,
    "/v1/capability/prompt-features": CreatePromptFeaturesRequest,
    "/v1/capability/provider-health": CreateProviderHealthRequest,
    "/v1/capability/context-clean": CreateContextCleanRequest,
    "/v1/capability/self-heal": CreateSelfHealRequest,
    "/v1/capability/multi-mode-synth": CreateMultiModeSynthRequest,
    "/v1/capability/conflict-arbitrate": CreateConflictArbitrateRequest,
    "/v1/capability/section-viability": CreateSectionViabilityRequest,
    "/v1/capability/feedback-iter": CreateFeedbackIterRequest,
    "/v1/capability/stream-aggregate": CreateStreamAggregateRequest,
    "/v1/capability/per-provider-rl": CreatePerProviderRlRequest,
    "/v1/capability/tier-recalibrate": CreateTierRecalibrateRequest,
    "/v1/capability/consumption-intel": CreateConsumptionIntelRequest,
    "/v1/capability/importance-score": CreateImportanceScoreRequest,
    "/v1/capability/quorum-check": CreateQuorumCheckRequest,
    "/v1/capability/model-entry": CreateModelEntryRequest,
    "/v1/capability/tool-replay": CreateToolReplayRequest,
    "/v1/capability/hook-events": CreateHookEventsRequest,
    "/v1/capability/meta-prompt": CreateMetaPromptRequest,
    "/v1/capability/task-tree": CreateTaskTreeRequest,
    "/v1/capability/distill": CreateDistillRequest,
    "/v1/capability/rerank": CreateRerankRequest,
    "/v1/capability/goal-eval": CreateGoalEvalRequest,
    "/v1/capability/auto-converge": CreateAutoConvergeRequest,
    "/v1/capability/subagent-comms": CreateSubagentCommsRequest,
    "/v1/capability/version": CreateVersionRequest,
    "/v1/capability/config": CreateConfigRequest,
    "/v1/capability/bubble": CreateBubbleRequest,
    "/v1/capability/route": CreateRouteRequest,
    "/v1/capability/session-lock": CreateSessionLockRequest,
    "/v1/capability/flask": CreateFlaskRequest,
    "/v1/capability/elo": CreateEloRequest,
    "/v1/capability/brainstorm": CreateBrainstormRequest,
    "/v1/capability/cross-iter": CreateCrossIterRequest,
    "/v1/capability/audit": CreateAuditRequest,
    "/v1/capability/in-flight": CreateInFlightRequest,
    "/v1/capability/mx": CreateMxRequest,
    "/v1/capability/tier-promo": CreateTierPromoRequest,
    "/v1/capability/artifact": CreateArtifactRequest,
    "/v1/capability/frozen": CreateFrozenRequest,
    "/v1/capability/turboquant": CreateTurboquantRequest,
    "/v1/capability/moa-engine": CreateMoaEngineRequest,
    "/v1/capability/acceptance": CreateAcceptanceRequest,
    "/v1/capability/llm-merge": CreateLlmMergeRequest,
    "/v1/capability/grace": CreateGraceRequest,
    "/v1/capability/rag-search": CreateRagSearchRequest,
    "/v1/capability/plan-act": CreatePlanActRequest,
    "/v1/capability/channels": CreateChannelsRequest,
    "/v1/capability/reference-router": CreateReferenceRouterRequest,
    "/v1/capability/checkpoint": CreateCheckpointRequest,
    "/v1/capability/canary": CreateCanaryRequest,
    "/v1/capability/wrap-output": CreateWrapOutputRequest,
    "/v1/capability/fuzzy-dedup": CreateFuzzyDedupRequest,
    "/v1/capability/input-fingerprint": CreateInputFingerprintRequest,
    "/v1/capability/tool-screening": CreateToolScreeningRequest,
    "/v1/capability/anthropic-compat": CreateAnthropicCompatRequest,
    "/v1/capability/token-bucket": CreateTokenBucketRequest,
    "/v1/capability/request-dedup": CreateRequestDedupRequest,
    "/v1/capability/trace": CreateTraceRequest,
    "/v1/agent/dispatch": CreateAgentDispatchRequest,
    "/v1/agent/dispatch_batch": CreateAgentDispatchBatchRequest,
    "/v1/agent/workflow/register": CreateAgentWorkflowRegisterRequest,
    "/v1/agent/workflow/run": CreateAgentWorkflowRunRequest,
}
