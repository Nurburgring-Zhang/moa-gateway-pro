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
    candidates: Optional[Any] = Field(None, description="候选答案列表")
    query: Optional[Any] = Field(None, description="查询文本 / 用户问题")
    reference_answer: Optional[Any] = Field(None, description="参考答案 (可选)")
    temperature: Optional[Any] = Field(None, description="采样温度 0-2")
class CreateMoaSimilarityRequest(_ModelBase):
    """Request body for POST /v1/moa/similarity."""
    candidate_a: Optional[Any] = Field(None, description="candidate_a 字段")
    candidate_b: Optional[Any] = Field(None, description="candidate_b 字段")
    model_id: Optional[Any] = Field(None, description="模型 ID (如 gpt-4o, deepseek-v3)")
    query: Optional[Any] = Field(None, description="查询文本 / 用户问题")
class CreateMoaFlaskRequest(_ModelBase):
    """Request body for POST /v1/moa/flask."""
    judge_model: Optional[Any] = Field(None, description="评审模型")
    query: Optional[Any] = Field(None, description="查询文本 / 用户问题")
    reference: Optional[Any] = Field(None, description="参考答案")
    response: Optional[Any] = Field(None, description="响应文本")
class CreateMoaBenchmarkRequest(_ModelBase):
    """Request body for POST /v1/moa/benchmark."""
    category: Optional[Any] = Field(None, description="类别")
    limit: Optional[Any] = Field(None, description="限制数量")
    presets: Optional[Any] = Field(None, description="预设列表")
class CreateMoaCostParetoRequest(_ModelBase):
    """Request body for POST /v1/moa/cost-pareto."""
    presets: Optional[Any] = Field(None, description="预设列表")
    prompts: Optional[Any] = Field(None, description="prompts 字段")
class UpdateMoaPromptsNameRequest(_ModelBase):
    """Request body for PUT /v1/moa/prompts/{name}."""
    content: Optional[Any] = Field(None, description="内容")
# ============ capability ============

class CreateSecretScanRequest(_ModelBase):
    """Request body for POST /v1/capability/secret-scan."""
    fail_on: Optional[Any] = Field(None, description="失败阈值")
    no_block: Optional[Any] = Field(None, description="是否不阻塞")
    path: Optional[Any] = Field(None, description="文件路径")
class CreateGroupThinkCheckRequest(_ModelBase):
    """Request body for POST /v1/capability/group-think-check."""
    block_threshold: Optional[Any] = Field(None, description="阻塞阈值")
    members: Optional[Any] = Field(None, description="成员列表")
    rounds: Optional[Any] = Field(None, description="轮次")
    session_id: Optional[Any] = Field(None, description="会话 ID")
    warn_threshold: Optional[Any] = Field(None, description="警告阈值")
class CreateEnsembleVoteRequest(_ModelBase):
    """Request body for POST /v1/capability/ensemble-vote."""
    method: Optional[Any] = Field(None, description="HTTP 方法 / 算法")
    votes: Optional[Any] = Field(None, description="投票列表")
class CreateShouldRebalanceRequest(_ModelBase):
    """Request body for POST /v1/capability/should-rebalance."""
    config: Optional[Dict[str, Any]] = None
    stats: Optional[Dict[str, Any]] = None

class CreateCostEstimateRequest(_ModelBase):
    """Request body for POST /v1/capability/cost-estimate."""
    channels: Optional[Any] = Field(None, description="channels 字段")
    format: Optional[Any] = Field(None, description="格式")
    include_fallback: Optional[Any] = Field(None, description="include_fallback 字段")
    input_tokens: Optional[Any] = Field(None, description="输入 token")
    output_tokens: Optional[Any] = Field(None, description="输出 token")
class CreateGateL0Request(_ModelBase):
    """Request body for POST /v1/capability/gate-l0."""
    query: Optional[Any] = Field(None, description="查询文本 / 用户问题")
class CreateScorePanelRequest(_ModelBase):
    """Request body for POST /v1/capability/score-panel."""
    answer: Optional[Any] = Field(None, description="answer 字段")
    query: Optional[Any] = Field(None, description="查询文本 / 用户问题")
class CreateCalculateMaxTokensRequest(_ModelBase):
    """Request body for POST /v1/capability/calculate-max-tokens."""
    input_tokens: Optional[Any] = Field(None, description="输入 token")
    model_id: Optional[Any] = Field(None, description="模型 ID (如 gpt-4o, deepseek-v3)")
    requested_output: Optional[Any] = Field(None, description="requested_output 字段")
    safety_margin: Optional[Any] = Field(None, description="safety_margin 字段")
class CreateEstimateCostRequest(_ModelBase):
    """Request body for POST /v1/capability/estimate-cost."""
    input_tokens: Optional[Any] = Field(None, description="输入 token")
    model_id: Optional[Any] = Field(None, description="模型 ID (如 gpt-4o, deepseek-v3)")
    output_tokens: Optional[Any] = Field(None, description="输出 token")
class CreateQuotaCheckRequest(_ModelBase):
    """Request body for POST /v1/capability/quota-check."""
    burn_rate_per_hour: Optional[Any] = Field(None, description="burn_rate_per_hour 字段")
    last_updated: Optional[Any] = Field(None, description="last_updated 字段")
    requested: Optional[Any] = Field(None, description="请求的资源量")
    windows: Optional[Any] = Field(None, description="窗口列表")
class CreateQuotaRecordRequest(_ModelBase):
    """Request body for POST /v1/capability/quota-record."""
    at: Optional[Any] = Field(None, description="at 字段")
    last_updated: Optional[Any] = Field(None, description="last_updated 字段")
    tokens: Optional[Any] = Field(None, description="token 数量")
    windows: Optional[Any] = Field(None, description="窗口列表")
class CreateMoaNLayerRequest(_ModelBase):
    """Request body for POST /v1/capability/moa-n-layer."""
    aggregators: Optional[Any] = Field(None, description="聚合者列表 (3-layer 模式需 3 个)")
    max_total_tokens: Optional[Any] = Field(None, description="最大总 token")
    proposers: Optional[Any] = Field(None, description="提议者列表,每个含 model_id 和 system_prompt")
    query: Optional[Any] = Field(None, description="查询文本 / 用户问题")
    temperature: Optional[Any] = Field(None, description="采样温度 0-2")
class CreateConvergentDetectRequest(_ModelBase):
    """Request body for POST /v1/capability/convergent-detect."""
    min_support: Optional[Any] = Field(None, description="最小支持数")
    proposals: Optional[Any] = Field(None, description="proposals 字段")
    viability_scores: Optional[Dict[str, Any]] = None

class CreateActionPolicyRequest(_ModelBase):
    """Request body for POST /v1/capability/action-policy."""
    command: Optional[Any] = Field(None, description="命令")
    rules: Optional[Any] = Field(None, description="规则列表")
class CreateEmbeddingsRequest(_ModelBase):
    """Request body for POST /v1/capability/embeddings."""
    dim: Optional[Any] = Field(None, description="dim 字段")
    input: Optional[Any] = Field(None, description="输入")
    model: Optional[Any] = Field(None, description="模型")
class CreateSemanticSearchRequest(_ModelBase):
    """Request body for POST /v1/capability/semantic-search."""
    dim: Optional[Any] = Field(None, description="dim 字段")
    documents: Optional[Any] = Field(None, description="documents 字段")
    query: Optional[Any] = Field(None, description="查询文本 / 用户问题")
    top_k: Optional[Any] = Field(None, description="Top K 结果数")
class CreatePromptFeaturesRequest(_ModelBase):
    """Request body for POST /v1/capability/prompt-features."""
    text: Optional[Any] = Field(None, description="文本")
class CreateProviderHealthRequest(_ModelBase):
    """Request body for POST /v1/capability/provider-health."""
    prefer_tier: Optional[Any] = Field(None, description="prefer_tier 字段")
    providers: Optional[Any] = Field(None, description="Provider 列表")
class CreateContextCleanRequest(_ModelBase):
    """Request body for POST /v1/capability/context-clean."""
    max_total_chars: Optional[Any] = Field(None, description="max_total_chars 字段")
    messages: Optional[Any] = Field(None, description="消息列表")
class CreateSelfHealRequest(_ModelBase):
    """Request body for POST /v1/capability/self-heal."""
    action: Optional[Any] = Field(None, description="操作类型")
    at: Optional[Any] = Field(None, description="at 字段")
    endpoint_id: Optional[Any] = Field(None, description="端点 ID")
    endpoints: Optional[Any] = Field(None, description="endpoints 字段")
    reason: Optional[Any] = Field(None, description="原因")
class CreateMultiModeSynthRequest(_ModelBase):
    """Request body for POST /v1/capability/multi-mode-synth."""
    curr_proposals: Optional[Any] = Field(None, description="curr_proposals 字段")
    mode: Optional[Any] = Field(None, description="模式")
    prev_proposals: Optional[Any] = Field(None, description="prev_proposals 字段")
    proposals: Optional[Any] = Field(None, description="proposals 字段")
    scores: Optional[Any] = Field(None, description="scores 字段")
    target_chars: Optional[Any] = Field(None, description="target_chars 字段")
class CreateConflictArbitrateRequest(_ModelBase):
    """Request body for POST /v1/capability/conflict-arbitrate."""
    fuse: Optional[Any] = Field(None, description="fuse 字段")
    options: Optional[Any] = Field(None, description="选项列表")
    query: Optional[Any] = Field(None, description="查询文本 / 用户问题")
class CreateSectionViabilityRequest(_ModelBase):
    """Request body for POST /v1/capability/section-viability."""
    proposal_idx: Optional[Any] = Field(None, description="proposal_idx 字段")
    text: Optional[Any] = Field(None, description="文本")
class CreateFeedbackIterRequest(_ModelBase):
    """Request body for POST /v1/capability/feedback-iter."""
    history_path: Optional[Any] = Field(None, description="history_path 字段")
    record: Optional[Dict[str, Any]] = None

class CreateStreamAggregateRequest(_ModelBase):
    """Request body for POST /v1/capability/stream-aggregate."""
    fail_prob: Optional[Any] = Field(None, description="fail_prob 字段")
    model: Optional[Any] = Field(None, description="模型")
    prompt: Optional[Any] = Field(None, description="Prompt 文本")
class CreatePerProviderRlRequest(_ModelBase):
    """Request body for POST /v1/capability/per-provider-rl."""
    action: Optional[Any] = Field(None, description="操作类型")
    at: Optional[Any] = Field(None, description="at 字段")
    concurrent: Optional[Any] = Field(None, description="concurrent 字段")
    cooldown_seconds: Optional[Any] = Field(None, description="cooldown_seconds 字段")
    input_tokens: Optional[Any] = Field(None, description="输入 token")
    limit_config: Optional[Dict[str, Any]] = None
    limits: Optional[Dict[str, Any]] = None
    provider: Optional[Any] = Field(None, description="Provider 名称")
    request_count: Optional[Any] = Field(None, description="request_count 字段")
class CreateTierRecalibrateRequest(_ModelBase):
    """Request body for POST /v1/capability/tier-recalibrate."""
    tiers: Optional[Any] = Field(None, description="tiers 字段")
class CreateConsumptionIntelRequest(_ModelBase):
    """Request body for POST /v1/capability/consumption-intel."""
    context: Optional[Dict[str, Any]] = None
    endpoints: Optional[Any] = Field(None, description="endpoints 字段")
class CreateImportanceScoreRequest(_ModelBase):
    """Request body for POST /v1/capability/importance-score."""
    messages: Optional[Any] = Field(None, description="消息列表")
    threshold: Optional[Any] = Field(None, description="阈值")
    top_k: Optional[Any] = Field(None, description="Top K 结果数")
class CreateQuorumCheckRequest(_ModelBase):
    """Request body for POST /v1/capability/quorum-check."""
    at: Optional[Any] = Field(None, description="at 字段")
    force_close: Optional[Any] = Field(None, description="force_close 字段")
    grace_seconds: Optional[Any] = Field(None, description="grace_seconds 字段")
    judge_response: Optional[Any] = Field(None, description="评审响应")
    participants: Optional[Any] = Field(None, description="participants 字段")
    required: Optional[Any] = Field(None, description="required 字段")
    response_a: Optional[Any] = Field(None, description="响应 A")
    response_b: Optional[Any] = Field(None, description="响应 B")
    wait_for_laggards: Optional[Any] = Field(None, description="wait_for_laggards 字段")
class CreateModelEntryRequest(_ModelBase):
    """Request body for POST /v1/capability/model-entry."""
    filter: Optional[Dict[str, Any]] = None
    max_budget_input: Optional[Any] = Field(None, description="max_budget_input 字段")
    max_budget_output: Optional[Any] = Field(None, description="max_budget_output 字段")
    models: Optional[Any] = Field(None, description="models 字段")
    query_modalities: Optional[Any] = Field(None, description="query_modalities 字段")
    sort: Optional[Any] = Field(None, description="排序")
class CreateToolReplayRequest(_ModelBase):
    """Request body for POST /v1/capability/tool-replay."""
    proposals: Optional[Any] = Field(None, description="proposals 字段")
    recent_count: Optional[Any] = Field(None, description="recent_count 字段")
    window: Optional[Any] = Field(None, description="window 字段")
class CreateHookEventsRequest(_ModelBase):
    """Request body for POST /v1/capability/hook-events."""
    action: Optional[Any] = Field(None, description="操作类型")
    data: Optional[Dict[str, Any]] = None
    event: Optional[Any] = Field(None, description="事件")
    max_iter: Optional[Any] = Field(None, description="max_iter 字段")
    session_id: Optional[Any] = Field(None, description="会话 ID")
    stage: Optional[Any] = Field(None, description="stage 字段")
    timestamp: Optional[Any] = Field(None, description="timestamp 字段")
class CreateMetaPromptRequest(_ModelBase):
    """Request body for POST /v1/capability/meta-prompt."""
    action: Optional[Any] = Field(None, description="操作类型")
    context: Optional[Any] = Field(None, description="上下文")
    options: Optional[Any] = Field(None, description="选项列表")
    query: Optional[Any] = Field(None, description="查询文本 / 用户问题")
    role_a: Optional[Any] = Field(None, description="role_a 字段")
    role_b: Optional[Any] = Field(None, description="role_b 字段")
class CreateTaskTreeRequest(_ModelBase):
    """Request body for POST /v1/capability/task-tree."""
    action: Optional[Any] = Field(None, description="操作类型")
    status: Optional[Any] = Field(None, description="status 字段")
    task_id: Optional[Any] = Field(None, description="任务 ID")
    tasks: Optional[Any] = Field(None, description="任务列表")
class CreateDistillRequest(_ModelBase):
    """Request body for POST /v1/capability/distill."""
    apply_bias_correction: Optional[Any] = Field(None, description="apply_bias_correction 字段")
    evaluations: Optional[Any] = Field(None, description="evaluations 字段")
    keep_ratio: Optional[Any] = Field(None, description="keep_ratio 字段")
    proposals: Optional[Any] = Field(None, description="proposals 字段")
class CreateRerankRequest(_ModelBase):
    """Request body for POST /v1/capability/rerank."""
    documents: Optional[Any] = Field(None, description="documents 字段")
    latency_budget_ms: Optional[Any] = Field(None, description="latency_budget_ms 字段")
    query: Optional[Any] = Field(None, description="查询文本 / 用户问题")
    stream_chunks: Optional[Any] = Field(None, description="stream_chunks 字段")
    top_n: Optional[Any] = Field(None, description="Top N 结果数")
class CreateGoalEvalRequest(_ModelBase):
    """Request body for POST /v1/capability/goal-eval."""
    baseline: Optional[Any] = Field(None, description="基线")
    claim: Optional[Any] = Field(None, description="声明")
    evidence: Optional[Any] = Field(None, description="证据")
    gaps: Optional[Any] = Field(None, description="差距")
    generate_ceiling: Optional[Any] = Field(None, description="generate_ceiling 字段")
    goals: Optional[Any] = Field(None, description="目标列表")
    output: Optional[Any] = Field(None, description="输出")
    residual_risk: Optional[Any] = Field(None, description="剩余风险")
class CreateAutoConvergeRequest(_ModelBase):
    """Request body for POST /v1/capability/auto-converge."""
    calibrate_samples: Optional[Any] = Field(None, description="calibrate_samples 字段")
    calibrate_score: Optional[Any] = Field(None, description="calibrate_score 字段")
    classify_events: Optional[Any] = Field(None, description="classify_events 字段")
    config: Optional[Dict[str, Any]] = None
    epsilon: Optional[Any] = Field(None, description="epsilon 字段")
    history: Optional[Any] = Field(None, description="history 字段")
    new_score: Optional[Any] = Field(None, description="new_score 字段")
    stagnation_threshold: Optional[Any] = Field(None, description="stagnation_threshold 字段")
    state: Optional[Any] = Field(None, description="状态")
class CreateSubagentCommsRequest(_ModelBase):
    """Request body for POST /v1/capability/subagent-comms."""
    action: Optional[Any] = Field(None, description="操作类型")
    assignee: Optional[Any] = Field(None, description="assignee 字段")
    content: Optional[Any] = Field(None, description="内容")
    holder: Optional[Any] = Field(None, description="holder 字段")
    kind: Optional[Any] = Field(None, description="kind 字段")
    lock_id: Optional[Any] = Field(None, description="锁 ID")
    parent: Optional[Any] = Field(None, description="parent 字段")
    parent_msg_id: Optional[Any] = Field(None, description="parent_msg_id 字段")
    parent_task_id: Optional[Any] = Field(None, description="parent_task_id 字段")
    session_id: Optional[Any] = Field(None, description="会话 ID")
    sessions: Optional[Any] = Field(None, description="sessions 字段")
    status: Optional[Any] = Field(None, description="status 字段")
    task_id: Optional[Any] = Field(None, description="任务 ID")
    timeout: Optional[Any] = Field(None, description="timeout 字段")
    title: Optional[Any] = Field(None, description="title 字段")
    to_session: Optional[Any] = Field(None, description="目标 session ID")
class CreateVersionRequest(_ModelBase):
    """Request body for POST /v1/capability/version."""
    action: Optional[Any] = Field(None, description="操作类型")
    content: Optional[Any] = Field(None, description="内容")
    created_by: Optional[Any] = Field(None, description="创建者")
    critique: Optional[Any] = Field(None, description="critique 字段")
    improvement: Optional[Any] = Field(None, description="improvement 字段")
    judge_response: Optional[Any] = Field(None, description="评审响应")
    parent: Optional[Any] = Field(None, description="parent 字段")
    proposal_id: Optional[Any] = Field(None, description="提案 ID")
    v1: Optional[Any] = Field(None, description="v1 字段")
    v2: Optional[Any] = Field(None, description="v2 字段")
class CreateConfigRequest(_ModelBase):
    """Request body for POST /v1/capability/config."""
    action: Optional[Any] = Field(None, description="操作类型")
    explicit: Optional[Any] = Field(None, description="是否显式")
    key: Optional[Any] = Field(None, description="键")
    layer: Optional[Any] = Field(None, description="层级")
    layers: Optional[Dict[str, Any]] = None
    mode: Optional[Any] = Field(None, description="模式")
    value: Optional[Any] = Field(None, description="值")
class CreateBubbleRequest(_ModelBase):
    """Request body for POST /v1/capability/bubble."""
    action: Optional[Any] = Field(None, description="操作类型")
    action_desc: Optional[Any] = Field(None, description="action_desc 字段")
    agent_id: Optional[Any] = Field(None, description="Agent ID")
    decision: Optional[Any] = Field(None, description="decision 字段")
    event_id: Optional[Any] = Field(None, description="事件 ID")
    event_type: Optional[Any] = Field(None, description="事件类型")
    n: Optional[Any] = Field(None, description="n 字段")
    parent_id: Optional[Any] = Field(None, description="父 ID")
    payload: Optional[Dict[str, Any]] = None
    reason: Optional[Any] = Field(None, description="原因")
    request_id: Optional[Any] = Field(None, description="request_id 字段")
    timestamp: Optional[Any] = Field(None, description="timestamp 字段")
class CreateRouteRequest(_ModelBase):
    """Request body for POST /v1/capability/route."""
    action: Optional[Any] = Field(None, description="操作类型")
    file_count: Optional[Any] = Field(None, description="file_count 字段")
    files: Optional[Any] = Field(None, description="files 字段")
    is_bugfix: Optional[Any] = Field(None, description="is_bugfix 字段")
    is_docs: Optional[Any] = Field(None, description="is_docs 字段")
    severity: Optional[Any] = Field(None, description="severity 字段")
    single_domain: Optional[Any] = Field(None, description="single_domain 字段")
    task: Optional[Any] = Field(None, description="task 字段")
    tier: Optional[Any] = Field(None, description="层级 (free/lite/standard/premium/flagship)")
class CreateSessionLockRequest(_ModelBase):
    """Request body for POST /v1/capability/session-lock."""
    action: Optional[Any] = Field(None, description="操作类型")
    lock_id: Optional[Any] = Field(None, description="锁 ID")
    retry_interval: Optional[Any] = Field(None, description="retry_interval 字段")
    session_id: Optional[Any] = Field(None, description="会话 ID")
    timeout: Optional[Any] = Field(None, description="timeout 字段")
    ttl: Optional[Any] = Field(None, description="ttl 字段")
class CreateFlaskRequest(_ModelBase):
    """Request body for POST /v1/capability/flask."""
    answer: Optional[Any] = Field(None, description="answer 字段")
    query: Optional[Any] = Field(None, description="查询文本 / 用户问题")
    tasks: Optional[Any] = Field(None, description="任务列表")
class CreateEloRequest(_ModelBase):
    """Request body for POST /v1/capability/elo."""
    action: Optional[Any] = Field(None, description="操作类型")
    ci: Optional[Any] = Field(None, description="ci 字段")
    k_factor: Optional[Any] = Field(None, description="k_factor 字段")
    matches: Optional[Any] = Field(None, description="matches 字段")
    model_ids: Optional[Any] = Field(None, description="模型 ID 列表")
    n_resamples: Optional[Any] = Field(None, description="n_resamples 字段")
    ratings_before: Optional[Any] = Field(None, description="ratings_before 字段")
    strategy: Optional[Any] = Field(None, description="策略")
    workers: Optional[Any] = Field(None, description="workers 字段")
class CreateBrainstormRequest(_ModelBase):
    """Request body for POST /v1/capability/brainstorm."""
    action: Optional[Any] = Field(None, description="操作类型")
    detailed: Optional[Any] = Field(None, description="detailed 字段")
    options: Optional[Any] = Field(None, description="选项列表")
    topic: Optional[Any] = Field(None, description="主题")
class CreateCrossIterRequest(_ModelBase):
    """Request body for POST /v1/capability/cross-iter."""
    action: Optional[Any] = Field(None, description="操作类型")
    iters: Optional[Any] = Field(None, description="iters 字段")
    step5_mode: Optional[Any] = Field(None, description="step5_mode 字段")
class CreateAuditRequest(_ModelBase):
    """Request body for POST /v1/capability/audit."""
    action_data: Optional[Dict[str, Any]] = None
    action_id: Optional[Any] = Field(None, description="action_id 字段")
class CreateInFlightRequest(_ModelBase):
    """Request body for POST /v1/capability/in-flight."""
    action: Optional[Any] = Field(None, description="操作类型")
    at: Optional[Any] = Field(None, description="at 字段")
    checkpoints: Optional[Any] = Field(None, description="检查点列表")
    phase: Optional[Any] = Field(None, description="阶段")
    session_id: Optional[Any] = Field(None, description="会话 ID")
    state_dir: Optional[Any] = Field(None, description="state_dir 字段")
class CreateMxRequest(_ModelBase):
    """Request body for POST /v1/capability/mx."""
    action: Optional[Any] = Field(None, description="操作类型")
    command: Optional[Any] = Field(None, description="命令")
    file_path: Optional[Any] = Field(None, description="文件路径")
    language: Optional[Any] = Field(None, description="语言")
    text: Optional[Any] = Field(None, description="文本")
class CreateTierPromoRequest(_ModelBase):
    """Request body for POST /v1/capability/tier-promo."""
    action: Optional[Any] = Field(None, description="操作类型")
    allowed_children: Optional[Any] = Field(None, description="allowed_children 字段")
    child_id: Optional[Any] = Field(None, description="child_id 字段")
    children_a: Optional[Any] = Field(None, description="children_a 字段")
    children_b: Optional[Any] = Field(None, description="children_b 字段")
    confidence: Optional[Any] = Field(None, description="confidence 字段")
    confidence_threshold: Optional[Any] = Field(None, description="confidence_threshold 字段")
    count: Optional[Any] = Field(None, description="count 字段")
    evidence: Optional[Any] = Field(None, description="证据")
    parent_a: Optional[Any] = Field(None, description="parent_a 字段")
    parent_b: Optional[Any] = Field(None, description="parent_b 字段")
    parent_id: Optional[Any] = Field(None, description="父 ID")
    tier_1: Optional[Any] = Field(None, description="tier_1 字段")
    tier_2: Optional[Any] = Field(None, description="tier_2 字段")
    tier_3: Optional[Any] = Field(None, description="tier_3 字段")
    tier_4: Optional[Any] = Field(None, description="tier_4 字段")
class CreateArtifactRequest(_ModelBase):
    """Request body for POST /v1/capability/artifact."""
    action: Optional[Any] = Field(None, description="操作类型")
    command: Optional[Any] = Field(None, description="命令")
    created_at: Optional[Any] = Field(None, description="created_at 字段")
    cwd: Optional[Any] = Field(None, description="工作目录")
    dependencies: Optional[Any] = Field(None, description="dependencies 字段")
    description: Optional[Any] = Field(None, description="描述")
    env_vars: Optional[Dict[str, Any]] = None
    id: Optional[Any] = Field(None, description="ID")
    inputs: Optional[Dict[str, Any]] = None
    max_visible: Optional[Any] = Field(None, description="max_visible 字段")
    name: Optional[Any] = Field(None, description="名称")
    outputs: Optional[Dict[str, Any]] = None
    pane_id: Optional[Any] = Field(None, description="面板 ID")
    tags: Optional[Any] = Field(None, description="标签列表")
    type: Optional[Any] = Field(None, description="type 字段")
class CreateFrozenRequest(_ModelBase):
    """Request body for POST /v1/capability/frozen."""
    action: Optional[Any] = Field(None, description="操作类型")
    added_at: Optional[Any] = Field(None, description="添加时间")
    path: Optional[Any] = Field(None, description="文件路径")
    reason: Optional[Any] = Field(None, description="原因")
    sentinel: Optional[Any] = Field(None, description="哨兵")
    zone: Optional[Any] = Field(None, description="zone 字段")
class CreateTurboquantRequest(_ModelBase):
    """Request body for POST /v1/capability/turboquant."""
    action: Optional[Any] = Field(None, description="操作类型")
    hard_cap: Optional[Any] = Field(None, description="hard_cap 字段")
    level: Optional[Any] = Field(None, description="等级 / 严重度")
    messages: Optional[Any] = Field(None, description="消息列表")
    preserve: Optional[Any] = Field(None, description="preserve 字段")
class CreateMoaEngineRequest(_ModelBase):
    """Request body for POST /v1/capability/moa-engine."""
    aggregator: Optional[Any] = Field(None, description="聚合者 (model_id + synthesis_prompt)")
    proposers: Optional[Any] = Field(None, description="提议者列表,每个含 model_id 和 system_prompt")
    query: Optional[Any] = Field(None, description="查询文本 / 用户问题")
    validate_only: Optional[Any] = Field(None, description="validate_only 字段")
class CreateAcceptanceRequest(_ModelBase):
    """Request body for POST /v1/capability/acceptance."""
    action: Optional[Any] = Field(None, description="操作类型")
    criteria: Optional[Any] = Field(None, description="标准列表")
    criterion: Optional[Any] = Field(None, description="评估标准")
    root_id: Optional[Any] = Field(None, description="root_id 字段")
    text: Optional[Any] = Field(None, description="文本")
class CreateLlmMergeRequest(_ModelBase):
    """Request body for POST /v1/capability/llm-merge."""
    action: Optional[Any] = Field(None, description="操作类型")
    providers: Optional[Any] = Field(None, description="Provider 列表")
    responses: Optional[Any] = Field(None, description="响应列表")
    strategy: Optional[Any] = Field(None, description="策略")
class CreateGraceRequest(_ModelBase):
    """Request body for POST /v1/capability/grace."""
    action: Optional[Any] = Field(None, description="操作类型")
    at: Optional[Any] = Field(None, description="at 字段")
    check_id: Optional[Any] = Field(None, description="检查 ID")
    name: Optional[Any] = Field(None, description="名称")
class CreateRagSearchRequest(_ModelBase):
    """Request body for POST /v1/capability/rag-search."""
    corpus: Optional[Any] = Field(None, description="语料库")
    max_results: Optional[Any] = Field(None, description="最大结果数")
    query: Optional[Any] = Field(None, description="查询文本 / 用户问题")
class CreatePlanActRequest(_ModelBase):
    """Request body for POST /v1/capability/plan-act."""
    query: Optional[Any] = Field(None, description="查询文本 / 用户问题")
class CreateChannelsRequest(_ModelBase):
    """Request body for POST /v1/capability/channels."""
    action: Optional[Any] = Field(None, description="操作类型")
    api_latency_ms: Optional[Any] = Field(None, description="api_latency_ms 字段")
    cli_latency_ms: Optional[Any] = Field(None, description="cli_latency_ms 字段")
    enabled: Optional[Any] = Field(None, description="是否启用")
    error: Optional[Any] = Field(None, description="error 字段")
    kwargs: Optional[Dict[str, Any]] = None
    query: Optional[Any] = Field(None, description="查询文本 / 用户问题")
class CreateReferenceRouterRequest(_ModelBase):
    """Request body for POST /v1/capability/reference-router."""
    cost_ratio_cap: Optional[Any] = Field(None, description="成本比上限")
    main_model: Optional[Any] = Field(None, description="主模型")
    max_latency_ms: Optional[Any] = Field(None, description="最大延迟 (ms)")
    query: Optional[Any] = Field(None, description="查询文本 / 用户问题")
    ref_model: Optional[Any] = Field(None, description="参考模型")
    strategy: Optional[Any] = Field(None, description="策略")
class CreateCheckpointRequest(_ModelBase):
    """Request body for POST /v1/capability/checkpoint."""
    raw_payload: Optional[Any] = Field(None, description="原始负载")
    action: Optional[Any] = Field(None, description="操作类型")
    max_keep: Optional[Any] = Field(None, description="max_keep 字段")
    name: Optional[Any] = Field(None, description="名称")
    older_than_seconds: Optional[Any] = Field(None, description="older_than_seconds 字段")
    payload: Optional[Dict[str, Any]] = None
    root_dir: Optional[Any] = Field(None, description="root_dir 字段")
class CreateCanaryRequest(_ModelBase):
    """Request body for POST /v1/capability/canary."""
    action: Optional[Any] = Field(None, description="操作类型")
    canary: Optional[Any] = Field(None, description="canary 字段")
    prompt: Optional[Any] = Field(None, description="Prompt 文本")
    response: Optional[Any] = Field(None, description="响应文本")
    strategy: Optional[Any] = Field(None, description="策略")
class CreateWrapOutputRequest(_ModelBase):
    """Request body for POST /v1/capability/wrap-output."""
    action: Optional[Any] = Field(None, description="操作类型")
    aggressive: Optional[Any] = Field(None, description="aggressive 字段")
    content: Optional[Any] = Field(None, description="内容")
    max_length: Optional[Any] = Field(None, description="max_length 字段")
    source: Optional[Any] = Field(None, description="source 字段")
    trust: Optional[Any] = Field(None, description="trust 字段")
    wrapped: Optional[Any] = Field(None, description="wrapped 字段")
class CreateFuzzyDedupRequest(_ModelBase):
    """Request body for POST /v1/capability/fuzzy-dedup."""
    action: Optional[Any] = Field(None, description="操作类型")
    max_size: Optional[Any] = Field(None, description="最大大小")
    metadata: Optional[Any] = Field(None, description="元数据")
    text: Optional[Any] = Field(None, description="文本")
    threshold: Optional[Any] = Field(None, description="阈值")
class CreateInputFingerprintRequest(_ModelBase):
    """Request body for POST /v1/capability/input-fingerprint."""
    a: Optional[Any] = Field(None, description="字符串 A")
    action: Optional[Any] = Field(None, description="操作类型")
    b: Optional[Any] = Field(None, description="字符串 B")
    collisions_with: Optional[Any] = Field(None, description="collisions_with 字段")
    level: Optional[Any] = Field(None, description="等级 / 严重度")
    max_size: Optional[Any] = Field(None, description="最大大小")
    metadata: Optional[Any] = Field(None, description="元数据")
    min_levels: Optional[Any] = Field(None, description="min_levels 字段")
    text: Optional[Any] = Field(None, description="文本")
class CreateToolScreeningRequest(_ModelBase):
    """Request body for POST /v1/capability/tool-screening."""
    arguments: Optional[Dict[str, Any]] = None
    tool_name: Optional[Any] = Field(None, description="工具名称")
class CreateAnthropicCompatRequest(_ModelBase):
    """Request body for POST /v1/capability/anthropic-compat."""
    action: Optional[Any] = Field(None, description="操作类型")
    anthropic_request: Optional[Dict[str, Any]] = None
    chat_response: Optional[Dict[str, Any]] = None
    content: Optional[Any] = Field(None, description="内容")
    delta: Optional[Any] = Field(None, description="增量")
    error_type: Optional[Any] = Field(None, description="错误类型")
    input: Optional[Dict[str, Any]] = None
    is_error: Optional[Any] = Field(None, description="是否错误")
    message: Optional[Any] = Field(None, description="消息内容")
    model: Optional[Any] = Field(None, description="模型")
    name: Optional[Any] = Field(None, description="名称")
    stop_reason: Optional[Any] = Field(None, description="停止原因")
    tool_id: Optional[Any] = Field(None, description="工具 ID")
    tool_use_id: Optional[Any] = Field(None, description="工具使用 ID")
class CreateTokenBucketRequest(_ModelBase):
    """Request body for POST /v1/capability/token-bucket."""
    action: Optional[Any] = Field(None, description="操作类型")
    capacity: Optional[Any] = Field(None, description="容量")
    key: Optional[Any] = Field(None, description="键")
    refill_rate: Optional[Any] = Field(None, description="补充速率")
    tokens: Optional[Any] = Field(None, description="token 数量")
class CreateRequestDedupRequest(_ModelBase):
    """Request body for POST /v1/capability/request-dedup."""
    action: Optional[Any] = Field(None, description="操作类型")
    body: Optional[Any] = Field(None, description="body 字段")
    max_size: Optional[Any] = Field(None, description="最大大小")
    method: Optional[Any] = Field(None, description="HTTP 方法 / 算法")
    path: Optional[Any] = Field(None, description="文件路径")
    response: Optional[Any] = Field(None, description="响应文本")
    source: Optional[Any] = Field(None, description="source 字段")
    strategy: Optional[Any] = Field(None, description="策略")
    ttl_seconds: Optional[Any] = Field(None, description="ttl_seconds 字段")
class CreateTraceRequest(_ModelBase):
    """Request body for POST /v1/capability/trace."""
    action: Optional[Any] = Field(None, description="操作类型")
    duration_ms: Optional[Any] = Field(None, description="持续时间 (ms)")
    error: Optional[Any] = Field(None, description="error 字段")
    limit: Optional[Any] = Field(None, description="限制数量")
    max_traces: Optional[Any] = Field(None, description="max_traces 字段")
    min_duration_ms: Optional[Any] = Field(None, description="min_duration_ms 字段")
    name: Optional[Any] = Field(None, description="名称")
    since_ts: Optional[Any] = Field(None, description="since_ts 字段")
    span_id: Optional[Any] = Field(None, description="Span ID")
    status: Optional[Any] = Field(None, description="status 字段")
    trace_id: Optional[Any] = Field(None, description="追踪 ID")
    traceparent: Optional[Any] = Field(None, description="Trace 上下文")
# ============ agent ============

class CreateAgentDispatchRequest(_ModelBase):
    """Request body for POST /v1/agent/dispatch."""
    method: Optional[Any] = Field(None, description="HTTP 方法 / 算法")
    payload: Optional[Any] = Field(None, description="业务负载")
    service: Optional[Any] = Field(None, description="service 字段")
class CreateAgentDispatchBatchRequest(_ModelBase):
    """Request body for POST /v1/agent/dispatch_batch."""
    calls: Optional[Any] = Field(None, description="calls 字段")
class CreateAgentWorkflowRegisterRequest(_ModelBase):
    """Request body for POST /v1/agent/workflow/register."""
    description: Optional[Any] = Field(None, description="描述")
    name: Optional[Any] = Field(None, description="名称")
    steps: Optional[Any] = Field(None, description="步骤列表")
class CreateAgentWorkflowRunRequest(_ModelBase):
    """Request body for POST /v1/agent/workflow/run."""
    input: Optional[Any] = Field(None, description="输入")
    name: Optional[Any] = Field(None, description="名称")
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
