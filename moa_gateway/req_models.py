"""Pydantic request models for all endpoints.

Auto-generated from server.py endpoint scan. Each endpoint with body fields
has a corresponding *Request Pydantic BaseModel. Pass the model as the request
body in FastAPI to get automatic 422 validation + OpenAPI schema generation.

P1 fix (v3.1.0): every field was previously ``Any | None`` with
``extra="ignore"``, which meant ~80 POST endpoints performed zero field
validation. Field types/defaults below are derived from the actual endpoint
consumption (``body.get("x", default)`` / ``body["x"]`` usage in
``moa_gateway/routes/*.py``):

- Fields the endpoint uses directly (raises when missing/empty) are required.
- Fields read via ``body.get("x", default)`` are optional and carry the same
  default as the endpoint, so existing behaviour is preserved.
- Closed value sets guarded by ``if x not in (...): raise`` (or equivalent
  case-sensitive enum lookups that raise on unknown values) are ``Literal``.
- ``_ModelBase`` now uses ``extra="forbid"`` so unknown fields are rejected
  with 422 instead of being silently dropped.

Usage:
    from .req_models import MoAEvalRequest
    @app.post("/v1/moa/eval")
    async def moa_eval(req: MoAEvalRequest): ...
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
    # P1: reject unknown fields so request bodies are actually validated.
    model_config = ConfigDict(strict=False, extra="forbid", protected_namespaces=())


class CreateMoaEvalRequest(_ModelBase):
    """Request body for POST /v1/moa/eval."""

    query: str = Field(..., min_length=1, description="查询文本 / 用户问题 (缺失/空 → 400)")
    candidates: list[str] = Field(
        ..., min_length=1, description="候选模型 ID 列表 (非空 list, 缺失 → 400)"
    )
    reference_answer: str | None = Field(None, description="参考答案 (可选)")
    temperature: float | None = Field(None, ge=0, le=2, description="采样温度 0-2 (端点默认 0.3)")


class CreateMoaSimilarityRequest(_ModelBase):
    """Request body for POST /v1/moa/similarity."""

    candidate_a: str = Field(..., min_length=1, description="候选答案 A (缺失 → 400)")
    candidate_b: str = Field(..., min_length=1, description="候选答案 B (缺失 → 400)")
    model_id: str | None = Field(None, description="模型 ID (如 gpt-4o, deepseek-v3)")
    query: str = Field("", description="查询文本 / 用户问题")


class CreateMoaFlaskRequest(_ModelBase):
    """Request body for POST /v1/moa/flask."""

    query: str = Field(..., min_length=1, description="查询文本 / 用户问题 (缺失 → 400)")
    response: str = Field(..., min_length=1, description="响应文本 (缺失 → 400)")
    reference: str | None = Field(None, description="参考答案")
    judge_model: str | None = Field(None, description="评审模型")


class CreateMoaBenchmarkRequest(_ModelBase):
    """Request body for POST /v1/moa/benchmark."""

    category: str = Field("all", description="类别 ('all' 表示全部)")
    limit: int = Field(5, ge=0, description="限制数量")
    presets: list[str] = Field(
        default_factory=lambda: ["balanced", "chinese_battalion"],
        description="预设列表",
    )


class CreateMoaCostParetoRequest(_ModelBase):
    """Request body for POST /v1/moa/cost-pareto."""

    prompts: list[str] = Field(..., min_length=1, description="prompt 列表 (缺失/空 → 400)")
    presets: list[str] = Field(
        default_factory=lambda: ["fast", "balanced", "quality"],
        description="预设列表",
    )


class UpdateMoaPromptsNameRequest(_ModelBase):
    """Request body for PUT /v1/moa/prompts/{name}."""

    content: str = Field(
        ...,
        min_length=1,
        max_length=1024 * 1024,
        description="模板内容 (非空字符串, ≤1MB)",
    )


# ============ capability ============


class CreateSecretScanRequest(_ModelBase):
    """Request body for POST /v1/capability/secret-scan."""

    path: str = Field(".", description="文件路径")
    fail_on: int = Field(3, ge=0, description="失败阈值")
    no_block: bool = Field(False, description="是否不阻塞")


class CreateGroupThinkCheckRequest(_ModelBase):
    """Request body for POST /v1/capability/group-think-check."""

    session_id: str = Field("unknown", description="会话 ID")
    members: list[dict[str, Any]] = Field(
        default_factory=list,
        description="成员列表, 每项 {member_id, content, round}",
    )
    rounds: list[list[dict[str, Any]]] | None = Field(None, description="多轮成员列表 (可选)")
    warn_threshold: float = Field(0.4, ge=0, le=1, description="警告阈值")
    block_threshold: float = Field(0.7, ge=0, le=1, description="阻塞阈值")


class CreateEnsembleVoteRequest(_ModelBase):
    """Request body for POST /v1/capability/ensemble-vote."""

    votes: list[dict[str, Any]] = Field(
        default_factory=list,
        description="投票列表, 每项 {voter_id, candidate, confidence, reason}",
    )
    method: Literal["majority", "weighted", "borda", "approval"] = Field(
        "weighted", description="投票算法"
    )


class CreateShouldRebalanceRequest(_ModelBase):
    """Request body for POST /v1/capability/should-rebalance."""

    stats: dict[str, Any] = Field(
        default_factory=dict, description="每个模型的 TierStat 字典"
    )
    config: dict[str, Any] = Field(
        default_factory=dict, description="阈值配置 (high_threshold/low_threshold 等)"
    )


class CreateCostEstimateRequest(_ModelBase):
    """Request body for POST /v1/capability/cost-estimate."""

    input_tokens: int = Field(1000, ge=0, description="输入 token")
    output_tokens: int = Field(500, ge=0, description="输出 token")
    channels: list[dict[str, Any]] = Field(default_factory=list, description="Channel 列表")
    include_fallback: bool = Field(True, description="是否包含 fallback")
    format: str | None = Field(None, description="输出格式 ('report' 时附带报告)")


class CreateGateL0Request(_ModelBase):
    """Request body for POST /v1/capability/gate-l0."""

    query: str = Field("", description="查询文本 / 用户问题")


class CreateScorePanelRequest(_ModelBase):
    """Request body for POST /v1/capability/score-panel."""

    query: str = Field("", description="查询文本 / 用户问题")
    answer: str = Field("", description="待评分答案")


class CreateCalculateMaxTokensRequest(_ModelBase):
    """Request body for POST /v1/capability/calculate-max-tokens."""

    model_id: str = Field("gpt-4o", min_length=1, description="模型 ID (如 gpt-4o)")
    input_tokens: int = Field(1000, ge=0, description="输入 token")
    requested_output: int = Field(2000, ge=0, description="期望输出 token")
    safety_margin: float = Field(0.1, ge=0, description="安全边际比例")


class CreateEstimateCostRequest(_ModelBase):
    """Request body for POST /v1/capability/estimate-cost."""

    model_id: str = Field("gpt-4o", min_length=1, description="模型 ID (如 gpt-4o)")
    input_tokens: int = Field(1000, ge=0, description="输入 token")
    output_tokens: int = Field(500, ge=0, description="输出 token")


class CreateQuotaCheckRequest(_ModelBase):
    """Request body for POST /v1/capability/quota-check."""

    windows: list[dict[str, Any]] = Field(
        default_factory=list,
        description="窗口列表, 每项 {name, limit_tokens, used_history}",
    )
    last_updated: float = Field(default_factory=time.time, description="状态更新时间戳")
    requested: int = Field(0, ge=0, description="请求的资源量")
    burn_rate_per_hour: float = Field(1000.0, ge=0, description="每小时消耗速率")


class CreateQuotaRecordRequest(_ModelBase):
    """Request body for POST /v1/capability/quota-record."""

    windows: list[dict[str, Any]] = Field(
        default_factory=list,
        description="窗口列表, 每项 {name, limit_tokens, used_history}",
    )
    last_updated: float = Field(default_factory=time.time, description="状态更新时间戳")
    tokens: int = Field(0, ge=0, description="token 数量")
    at: float | None = Field(None, description="记录时间戳 (缺省取当前时间)")


class CreateMoaNLayerRequest(_ModelBase):
    """Request body for POST /v1/capability/moa-n-layer."""

    query: str = Field("", description="查询文本 / 用户问题")
    proposers: list[dict[str, Any]] = Field(
        default_factory=list,
        description="提议者列表, 每个含 model_id 和 system_prompt (非空 → 否则 400)",
    )
    aggregators: list[dict[str, Any]] = Field(
        default_factory=list,
        description="聚合者列表 (3-layer 模式需恰好 3 个)",
    )
    temperature: float = Field(0.6, ge=0, le=2, description="采样温度 0-2")
    max_total_tokens: int = Field(0, ge=0, description="最大总 token")


class CreateConvergentDetectRequest(_ModelBase):
    """Request body for POST /v1/capability/convergent-detect."""

    proposals: list[dict[str, Any]] = Field(
        default_factory=list,
        description="提案列表, 每项 {proposal_idx, author, text}",
    )
    min_support: int = Field(3, ge=1, description="最小支持数")
    viability_scores: dict[str, Any] = Field(
        default_factory=dict, description="viability 分数 {proposal_idx: score}"
    )


class CreateActionPolicyRequest(_ModelBase):
    """Request body for POST /v1/capability/action-policy."""

    command: str = Field("", description="命令")
    rules: list[dict[str, Any]] = Field(default_factory=list, description="PolicyRule 列表")


class CreateEmbeddingsRequest(_ModelBase):
    """Request body for POST /v1/capability/embeddings (and /v1/embeddings)."""

    input: str | list[str] = Field(
        default_factory=list, description="输入文本 (字符串或字符串列表)"
    )
    model: str = Field("mock-embedding-v1", description="模型")
    dim: int = Field(384, ge=1, description="向量维度")


class CreateSemanticSearchRequest(_ModelBase):
    """Request body for POST /v1/capability/semantic-search."""

    query: str = Field("", description="查询文本 / 用户问题")
    documents: list[str] = Field(default_factory=list, description="文档列表")
    top_k: int = Field(3, ge=1, description="Top K 结果数")
    dim: int = Field(384, ge=1, description="向量维度")


class CreatePromptFeaturesRequest(_ModelBase):
    """Request body for POST /v1/capability/prompt-features."""

    text: str = Field("", description="文本")


class CreateProviderHealthRequest(_ModelBase):
    """Request body for POST /v1/capability/provider-health."""

    providers: list[dict[str, Any]] = Field(
        default_factory=list, description="HealthMetrics 列表"
    )
    prefer_tier: str | None = Field(None, description="推荐时偏好的 tier")


class CreateContextCleanRequest(_ModelBase):
    """Request body for POST /v1/capability/context-clean."""

    messages: list[dict[str, Any]] = Field(
        default_factory=list, description="消息列表, 每项 {role, content}"
    )
    max_total_chars: int = Field(100000, ge=1, description="最大总字符数")


class CreateSelfHealRequest(_ModelBase):
    """Request body for POST /v1/capability/self-heal."""

    endpoints: list[dict[str, Any]] = Field(
        default_factory=list, description="EndpointState 列表"
    )
    action: Literal[
        "record_success", "record_failure", "check_recovery", "promote", "demote", "auto_balance"
    ] = Field("auto_balance", description="操作类型")
    endpoint_id: str | None = Field(
        None, description="端点 ID (record_*/check_recovery/promote/demote 必填)"
    )
    at: float | None = Field(None, description="事件时间戳")
    reason: str = Field("manual", description="promote/demote 原因")


class CreateMultiModeSynthRequest(_ModelBase):
    """Request body for POST /v1/capability/multi-mode-synth."""

    proposals: list[dict[str, Any]] = Field(
        default_factory=list,
        description="提案列表, 每项 {proposal_idx, author, text}",
    )
    mode: Literal[
        "classification", "integrated_synthesis", "final_selection", "cross_iteration"
    ] = Field("classification", description="合成模式")
    scores: dict[str, Any] | None = Field(
        None, description="final_selection 用分数 {proposal_idx: score}"
    )
    target_chars: int | None = Field(None, ge=1, description="integrated_synthesis 目标字符数")
    prev_proposals: list[dict[str, Any]] | None = Field(
        None, description="cross_iteration 上一轮提案"
    )
    curr_proposals: list[dict[str, Any]] | None = Field(
        None, description="cross_iteration 当前提案"
    )


class CreateConflictArbitrateRequest(_ModelBase):
    """Request body for POST /v1/capability/conflict-arbitrate."""

    options: list[dict[str, Any]] = Field(default_factory=list, description="ConflictOption 列表")
    fuse: bool = Field(False, description="是否使用 fuse 决策")
    query: str = Field("", description="查询文本 / 用户问题")


class CreateSectionViabilityRequest(_ModelBase):
    """Request body for POST /v1/capability/section-viability."""

    text: str = Field("", description="文本")
    proposal_idx: int = Field(0, ge=0, description="提案索引")


class CreateFeedbackIterRequest(_ModelBase):
    """Request body for POST /v1/capability/feedback-iter."""

    record: dict[str, Any] = Field(default_factory=dict, description="IterationRecord 字典")
    history_path: str = Field("", description="历史文件相对路径 (白名单目录内)")


class CreateStreamAggregateRequest(_ModelBase):
    """Request body for POST /v1/capability/stream-aggregate."""

    prompt: str = Field("", description="Prompt 文本")
    model: str = Field("mock-stream-v1", description="模型")
    fail_prob: float = Field(0.0, ge=0, le=1, description="模拟流失败概率 0-1")


class CreatePerProviderRlRequest(_ModelBase):
    """Request body for POST /v1/capability/per-provider-rl."""

    provider: str | None = Field(None, description="Provider 名称 (单 provider 模式)")
    action: Literal["check", "record", "mark_429", "status"] = Field("check", description="操作类型")
    limits: dict[str, Any] = Field(
        default_factory=dict, description="多 provider 限流配置 {provider: ProviderLimit}"
    )
    limit_config: dict[str, Any] = Field(
        default_factory=lambda: {
            "max_requests_per_minute": 60,
            "max_inputs_per_minute": 100000,
            "max_concurrent": 5,
        },
        description="单 provider 限流配置",
    )
    concurrent: int = Field(0, ge=0, description="当前并发数")
    at: float | None = Field(None, description="事件时间戳")
    request_count: int = Field(1, ge=0, description="record 的请求数")
    input_tokens: int = Field(0, ge=0, description="record 的输入 token")
    cooldown_seconds: float = Field(60.0, ge=0, description="mark_429 冷却秒数")


class CreateTierRecalibrateRequest(_ModelBase):
    """Request body for POST /v1/capability/tier-recalibrate."""

    tiers: list[dict[str, Any]] = Field(
        default_factory=list, description="TierMetrics 列表 (tier 字段大小写均可)"
    )


class CreateConsumptionIntelRequest(_ModelBase):
    """Request body for POST /v1/capability/consumption-intel."""

    context: dict[str, Any] = Field(
        default_factory=lambda: {"query": ""}, description="RequestContext 字典"
    )
    endpoints: list[dict[str, Any]] = Field(
        default_factory=list, description="EndpointSpec 列表"
    )


class CreateImportanceScoreRequest(_ModelBase):
    """Request body for POST /v1/capability/importance-score."""

    messages: list[dict[str, Any]] = Field(
        default_factory=list, description="消息列表, 每项 {role, content}"
    )
    top_k: int = Field(0, ge=0, description="Top K 结果数 (0 表示不选)")
    threshold: float = Field(0.5, ge=0, le=1, description="压缩判定阈值")


class CreateQuorumCheckRequest(_ModelBase):
    """Request body for POST /v1/capability/quorum-check."""

    participants: list[dict[str, Any]] = Field(
        default_factory=list, description="Participant 列表"
    )
    required: int = Field(3, ge=1, description="所需响应数")
    grace_seconds: float = Field(30.0, ge=0, description="宽限秒数")
    wait_for_laggards: bool = Field(True, description="是否等待落后者")
    at: float | None = Field(None, description="检查时间戳")
    force_close: bool | None = Field(None, description="是否强制关闭")
    judge_response: str | None = Field(None, description="评审响应 (LLM-as-Judge)")
    response_a: str | None = Field(None, description="响应 A (battle 模式)")
    response_b: str | None = Field(None, description="响应 B (battle 模式)")


class CreateModelEntryRequest(_ModelBase):
    """Request body for POST /v1/capability/model-entry."""

    models: list[dict[str, Any]] = Field(default_factory=list, description="ModelEntry 列表")
    filter: dict[str, Any] = Field(
        default_factory=dict, description="过滤条件 (capability/modality/min_context)"
    )
    sort: str = Field("", description="排序 (cost_asc/cost_desc/context_desc)")
    max_budget_input: float | None = Field(None, ge=0, description="输入预算上限")
    max_budget_output: float | None = Field(None, ge=0, description="输出预算上限")
    query_modalities: list[str] = Field(default_factory=list, description="查询模态列表")


class CreateToolReplayRequest(_ModelBase):
    """Request body for POST /v1/capability/tool-replay."""

    proposals: list[str] = Field(
        default_factory=list, description="含 <tool_use> 的提案文本列表"
    )
    recent_count: int | None = Field(None, ge=0, description="近期调用数 (缺省取全部)")
    window: int = Field(5, ge=1, description="循环检测窗口")


class CreateHookEventsRequest(_ModelBase):
    """Request body for POST /v1/capability/hook-events."""

    action: Literal["register", "trigger", "ralph_advance", "list_events"] = Field(
        "ralph_advance", description="操作类型"
    )
    event: Literal[
        "SessionStart",
        "UserPromptSubmit",
        "SessionEnd",
        "TeammateIdle",
        "TaskCompleted",
        "PreToolUse",
        "PermissionRequest",
        "PostToolUse",
        "PostToolUseFailure",
        "Stop",
        "StopFailure",
        "SubagentStop",
        "Notification",
        "PreCompact",
        "PostCompact",
        "PreResponse",
        "PostResponse",
        "ConfigChange",
        "WorktreeCreate",
        "WorktreeRemove",
        "PreCommit",
        "PostCommit",
        "FileWatch",
        "SkillActivate",
        "McpToolCall",
        "AgentSpawn",
        "AgentExit",
    ] | None = Field(None, description="Hook 事件名 (trigger 缺省 SessionStart)")
    session_id: str = Field("", description="会话 ID")
    timestamp: float = Field(0.0, description="timestamp 字段")
    data: dict[str, Any] = Field(default_factory=dict, description="事件数据")
    stage: str = Field("analyze", description="ralph_advance 当前阶段")
    max_iter: int = Field(5, ge=1, description="Ralph 循环最大迭代数")


class CreateMetaPromptRequest(_ModelBase):
    """Request body for POST /v1/capability/meta-prompt."""

    action: Literal["get_stages", "clash", "fuse"] = Field("get_stages", description="操作类型")
    query: str = Field("", description="查询文本 / 用户问题")
    role_a: str = Field("optimist", description="clash 角色 A")
    role_b: str = Field("pessimist", description="clash 角色 B")
    options: list[str] = Field(default_factory=list, description="fuse 选项列表")
    context: str | None = Field(None, description="fuse 上下文 (缺省用 query)")


class CreateTaskTreeRequest(_ModelBase):
    """Request body for POST /v1/capability/task-tree."""

    tasks: list[dict[str, Any]] = Field(default_factory=list, description="TaskSegment 列表")
    action: Literal[
        "ready", "cycles", "aggregates", "depth", "is_leaf", "is_root", "set_status"
    ] = Field("ready", description="操作类型")
    task_id: str = Field("", description="任务 ID")
    status: Literal["pending", "in_progress", "completed", "failed", "blocked"] = Field(
        "completed", description="set_status 目标状态"
    )


class CreateDistillRequest(_ModelBase):
    """Request body for POST /v1/capability/distill."""

    proposals: list[str] = Field(default_factory=list, description="提案文本列表")
    keep_ratio: float = Field(0.5, ge=0, le=1, description="保留比例 0-1")
    evaluations: list[dict[str, Any]] | None = Field(
        None, description="多评估者打分列表 (每项 {TQ, CO, ...})"
    )
    apply_bias_correction: bool | None = Field(None, description="是否应用偏差校正")


class CreateRerankRequest(_ModelBase):
    """Request body for POST /v1/capability/rerank."""

    query: str = Field("", description="查询文本 / 用户问题")
    documents: list[str] = Field(default_factory=list, description="文档列表")
    top_n: int = Field(10, ge=1, description="Top N 结果数")
    latency_budget_ms: float = Field(2000.0, ge=0, description="延迟预算 (ms)")
    stream_chunks: list[Any] | None = Field(None, description="流式 chunk 列表 (L-31 代理)")


class CreateGoalEvalRequest(_ModelBase):
    """Request body for POST /v1/capability/goal-eval."""

    goals: list[dict[str, Any]] = Field(default_factory=list, description="Goal 列表")
    output: str = Field("", description="输出")
    generate_ceiling: bool | None = Field(None, description="是否生成 Ceiling Report")
    claim: str | None = Field(None, description="声明")
    evidence: list[str] | None = Field(None, description="证据列表")
    baseline: str | None = Field(None, description="基线")
    gaps: list[str] | None = Field(None, description="差距列表")
    residual_risk: str | None = Field(None, description="剩余风险")


class CreateAutoConvergeRequest(_ModelBase):
    """Request body for POST /v1/capability/auto-converge."""

    state: dict[str, Any] | None = Field(None, description="ConvergenceState 字典")
    new_score: float | None = Field(None, description="新一轮分数 (与 state 配合)")
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="收敛配置 (stagnation_threshold/improvement_threshold/max_iterations)",
    )
    classify_events: int | None = Field(None, ge=0, description="待分级的事件数 (1/3/5/10)")
    history: list[float] | None = Field(None, description="分数历史 (停滞检测)")
    stagnation_threshold: int = Field(3, ge=1, description="停滞判定阈值")
    epsilon: float = Field(0.001, ge=0, description="停滞判定 epsilon")
    calibrate_score: float | None = Field(None, description="待校准分数")
    calibrate_samples: int = Field(0, ge=0, description="校准样本数")


class CreateSubagentCommsRequest(_ModelBase):
    """Request body for POST /v1/capability/subagent-comms."""

    action: Literal[
        "send",
        "broadcast",
        "reply",
        "inbox",
        "create_task",
        "update_status",
        "list_tasks",
        "get_task",
        "get_subtasks",
        "acquire",
        "release",
        "is_held",
    ] = Field("send", description="操作类型")
    session_id: str = Field("default", description="会话 ID")
    to_session: str | None = Field(None, description="目标 session ID (send 必填)")
    content: str = Field("", description="内容")
    kind: str = Field("send", description="消息类型")
    sessions: list[str] = Field(default_factory=list, description="broadcast 目标 session 列表")
    parent_msg_id: str | None = Field(None, description="父消息 ID (reply 必填)")
    title: str = Field("", description="任务标题 (create_task)")
    assignee: str | None = Field(None, description="任务负责人")
    parent: str | None = Field(None, description="父任务 ID (create_task)")
    task_id: str | None = Field(None, description="任务 ID (update_status/get_task 必填)")
    status: str | None = Field(
        None, description="任务状态 (update_status 缺省 pending; list_tasks 过滤)"
    )
    parent_task_id: str | None = Field(None, description="父任务 ID (get_subtasks 必填)")
    lock_id: str | None = Field(None, description="锁 ID (acquire/release/is_held 必填)")
    holder: str | None = Field(None, description="锁持有者 (缺省为 session_id)")
    timeout: float = Field(10.0, ge=0, description="锁超时秒数")


class CreateVersionRequest(_ModelBase):
    """Request body for POST /v1/capability/version."""

    action: Literal[
        "add", "get", "latest", "diff", "parse_rating", "parse_battle", "swap_battle"
    ] = Field("add", description="操作类型")
    proposal_id: str = Field("default", description="提案 ID")
    content: str = Field("", description="内容 (add)")
    parent: str | None = Field(None, description="父版本 ID")
    critique: str | None = Field(None, description="critique 字段")
    improvement: str | None = Field(None, description="improvement 字段")
    created_by: str = Field("system", description="创建者")
    v1: str | None = Field(None, description="版本 1 ID (diff 必填)")
    v2: str | None = Field(None, description="版本 2 ID (diff 必填)")
    judge_response: str = Field("", description="评审响应 (parse_rating/parse_battle)")
    judge_response_swapped: str | None = Field(
        None, description="位置交换后的评审响应 (swap_battle 第 2 轮)"
    )
    response_a: str = Field("", description="响应 A (swap_battle)")
    response_b: str = Field("", description="响应 B (swap_battle)")


class CreateConfigRequest(_ModelBase):
    """Request body for POST /v1/capability/config."""

    action: Literal["set", "get", "unset", "merge", "permission"] = Field(
        "get", description="操作类型"
    )
    key: str | None = Field(None, description="键 (set/get/unset 必填)")
    value: Any | None = Field(None, description="值 (set)")
    layer: str = Field(
        "user",
        description="配置层 (policy/user/project/local/plugin/skill/session/builtin, 大小写不敏感)",
    )
    explicit: bool = Field(True, description="是否显式设置")
    layers: dict[str, Any] = Field(default_factory=dict, description="merge 用层字典")
    mode: Literal["default", "accept_edits", "bypass_permissions", "plan", "bubble"] = Field(
        "default", description="Permission Mode (permission)"
    )


class CreateBubbleRequest(_ModelBase):
    """Request body for POST /v1/capability/bubble."""

    action: Literal[
        "escalate",
        "resolve",
        "pending",
        "resolved",
        "schedule",
        "should_continue",
        "recent",
        "clear",
    ] = Field("escalate", description="操作类型")
    parent_id: str = Field("default", description="父 ID")
    agent_id: str | None = Field(
        None, description="Agent ID (escalate/schedule/should_continue/recent/clear 必填)"
    )
    action_desc: str = Field("", description="action_desc 字段")
    reason: str = Field("", description="原因")
    request_id: str | None = Field(None, description="request_id 字段 (resolve 必填)")
    decision: Literal["allowed", "denied", "escalated"] = Field("allowed", description="decision 字段")
    event_id: str = Field("", description="事件 ID")
    event_type: Literal["trigger", "neutral", "terminal"] = Field("neutral", description="事件类型")
    payload: dict[str, Any] = Field(default_factory=dict, description="事件负载")
    timestamp: float = Field(default_factory=time.time, description="timestamp 字段")
    n: int = Field(10, ge=1, description="recent 返回条数")


class CreateRouteRequest(_ModelBase):
    """Request body for POST /v1/capability/route."""

    action: Literal["route_request", "auto_detect", "priority", "tools"] = Field(
        "route_request", description="操作类型"
    )
    task: str = Field("", description="task 字段")
    file_count: int = Field(0, ge=0, description="file_count 字段")
    single_domain: bool = Field(True, description="single_domain 字段")
    is_bugfix: bool = Field(False, description="is_bugfix 字段")
    is_docs: bool = Field(False, description="is_docs 字段")
    files: list[str] = Field(default_factory=list, description="文件列表 (auto_detect)")
    severity: str = Field(
        "normal", description="严重度 (critical/high/medium/low/backlog, 未知按 P2)"
    )
    tier: Literal["minimal", "standard", "thorough"] = Field("standard", description="层级 (tools)")


class CreateSessionLockRequest(_ModelBase):
    """Request body for POST /v1/capability/session-lock."""

    # NOTE: the endpoint's own fallback for a missing action is "acquire", which
    # is not a valid action and yields HTTP 400; ``None`` preserves that exact
    # behaviour while keeping the Literal honest.
    action: Literal[
        "try_acquire",
        "acquire_with_wait",
        "release",
        "get_state",
        "cleanup_expired",
        "register_mcp",
        "unregister_mcp",
        "invoke_mcp",
        "list_mcp",
        "get_mcp",
    ] | None = Field(None, description="操作类型")
    lock_id: str | None = Field(None, description="锁 ID (try_acquire/release 等必填)")
    session_id: str | None = Field(None, description="会话 ID (try_acquire/release 等必填)")
    ttl: float | None = Field(None, ge=0, description="锁 TTL 秒数")
    timeout: float = Field(10.0, ge=0, description="acquire_with_wait 超时秒数")
    retry_interval: float = Field(0.01, ge=0, description="acquire_with_wait 重试间隔")
    name: str | None = Field(None, description="MCP 工具名 (register/invoke/get 等必填)")
    description: str = Field("", description="MCP 工具描述 (register_mcp)")
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="MCP 工具参数 schema (register_mcp)"
    )
    kwargs: dict[str, Any] = Field(default_factory=dict, description="invoke_mcp 调用参数")
    returns: Any | None = Field(None, description="register_mcp mock handler 返回值")


class CreateFlaskRequest(_ModelBase):
    """Request body for POST /v1/capability/flask."""

    answer: str | None = Field(None, description="answer 字段 (FLASK 评分)")
    query: str = Field("", description="查询文本 / 用户问题")
    tasks: list[dict[str, Any]] | None = Field(
        None, description="任务列表, 每项 {title, description}"
    )


class CreateEloRequest(_ModelBase):
    """Request body for POST /v1/capability/elo."""

    action: Literal["record", "bootstrap_ci", "submit"] = Field("record", description="操作类型")
    k_factor: float = Field(4.0, gt=0, description="Elo K 因子")
    model_ids: list[str] = Field(default_factory=list, description="模型 ID 列表")
    matches: list[dict[str, Any]] = Field(
        default_factory=list, description="对局列表, 每项 {winner_id, loser_id, timestamp}"
    )
    ratings_before: list[dict[str, Any]] = Field(
        default_factory=list, description="bootstrap_ci 前置评分列表"
    )
    n_resamples: int = Field(1000, ge=1, description="bootstrap 重采样次数")
    ci: float = Field(0.95, gt=0, lt=1, description="置信区间水平")
    workers: list[str] = Field(
        default_factory=lambda: ["w1", "w2", "w3"], description="worker 列表 (submit)"
    )
    strategy: Literal["lottery", "shortest_queue"] = Field("shortest_queue", description="调度策略")


class CreateBrainstormRequest(_ModelBase):
    """Request body for POST /v1/capability/brainstorm."""

    action: Literal["ideas", "decide"] = Field("ideas", description="操作类型")
    topic: str = Field("", description="主题")
    detailed: bool | None = Field(None, description="ideas 是否返回详情")
    options: list[str] = Field(default_factory=list, description="decide 选项列表")


class CreateCrossIterRequest(_ModelBase):
    """Request body for POST /v1/capability/cross-iter."""

    action: Literal["convergence", "best_of_each", "adoption", "step5"] = Field(
        "step5", description="操作类型"
    )
    iters: list[dict[str, Any]] = Field(
        default_factory=list, description="IterationSnapshot 列表"
    )
    step5_mode: Literal["sintesis_central", "self_improve", "skip"] = Field(
        "sintesis_central", description="step5 模式"
    )


class CreateAuditRequest(_ModelBase):
    """Request body for POST /v1/capability/audit and /v1/capability/audit-cache."""

    # --- /v1/capability/audit (AuditGate) ---
    action_id: str = Field("a1", description="action_id 字段")
    action_data: dict[str, Any] = Field(default_factory=dict, description="动作数据")
    # --- /v1/capability/audit-cache (AuditCache) ---
    action: Literal["record", "query", "stats", "cleanup"] = Field(
        "record", description="audit-cache 操作类型"
    )
    event_id: str | None = Field(None, description="事件 ID (缺省自动生成)")
    timestamp: float = Field(0.0, description="事件时间戳")
    event_type: str = Field("generic", description="事件类型")
    actor: str = Field("anonymous", description="执行者")
    resource: str = Field("", description="资源")
    sub_action: str = Field("exec", description="子动作")
    outcome: str = Field("allow", description="结果")
    metadata: dict[str, Any] = Field(default_factory=dict, description="元数据")
    max_size: int = Field(10000, ge=1, description="缓存最大条数")
    ttl_seconds: int = Field(86400, ge=1, description="缓存 TTL 秒数")
    since: float | None = Field(None, description="query 起始时间戳")
    limit: int = Field(100, ge=1, description="query 返回上限")


class CreateInFlightRequest(_ModelBase):
    """Request body for POST /v1/capability/in-flight."""

    action: Literal["start", "complete", "in_flight", "transition", "merge"] = Field(
        "in_flight", description="操作类型"
    )
    state_dir: str = Field(".moai/state", description="状态目录")
    phase: Literal["analyze", "implement", "test", "review", "complete"] = Field(
        "analyze", description="阶段"
    )
    at: float | None = Field(None, description="事件时间戳")
    session_id: str | None = Field(None, description="会话 ID (complete/transition 必填)")
    checkpoints: list[dict[str, Any]] = Field(
        default_factory=list, description="merge 用团队检查点列表"
    )


class CreateMxRequest(_ModelBase):
    """Request body for POST /v1/capability/mx."""

    action: Literal["parse", "fanin", "cli"] = Field("parse", description="操作类型")
    text: str = Field("", description="文本")
    file_path: str = Field("f.py", description="文件路径")
    language: str = Field("python", description="语言")
    command: str = Field("list", description="mx CLI 命令")


class CreateTierPromoRequest(_ModelBase):
    """Request body for POST /v1/capability/tier-promo."""

    action: Literal["classify", "compute", "can_spawn", "cohabitation"] = Field(
        "classify", description="操作类型"
    )
    evidence: list[dict[str, Any]] = Field(default_factory=list, description="Evidence 列表")
    tier_1: int = Field(1, ge=0, description="tier_1 阈值")
    tier_2: int = Field(3, ge=0, description="tier_2 阈值")
    tier_3: int = Field(5, ge=0, description="tier_3 阈值")
    tier_4: int = Field(10, ge=0, description="tier_4 阈值")
    confidence_threshold: float = Field(0.70, ge=0, le=1, description="置信度阈值")
    count: int = Field(0, ge=0, description="compute 用证据数")
    confidence: float = Field(0.5, ge=0, le=1, description="compute 用置信度")
    parent_id: str = Field("p1", description="父 ID (can_spawn)")
    allowed_children: list[str] = Field(default_factory=list, description="允许的子代列表")
    child_id: str = Field("", description="child_id 字段")
    parent_a: str = Field("p1", description="parent_a 字段")
    children_a: list[str] = Field(default_factory=list, description="children_a 字段")
    parent_b: str = Field("p2", description="parent_b 字段")
    children_b: list[str] = Field(default_factory=list, description="children_b 字段")


class CreateArtifactRequest(_ModelBase):
    """Request body for POST /v1/capability/artifact."""

    action: Literal[
        "register", "list_by_type", "validate", "add_pane", "layout", "safe_layout"
    ] = Field("register", description="操作类型")
    id: str | None = Field(None, description="ID (register 必填)")
    name: str | None = Field(None, description="名称 (register 必填)")
    type: Literal["agent", "skill", "connector", "action", "experiment_plan"] | None = Field(
        None, description="类型 (register 必填; list_by_type/validate 缺省 agent)"
    )
    description: str = Field("", description="描述")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    inputs: dict[str, Any] = Field(default_factory=dict, description="输入 schema")
    outputs: dict[str, Any] = Field(default_factory=dict, description="输出 schema")
    dependencies: list[str] = Field(default_factory=list, description="依赖列表")
    created_at: float = Field(default_factory=time.time, description="创建时间戳")
    pane_id: str = Field("p1", description="面板 ID (add_pane)")
    command: str = Field("", description="命令 (add_pane)")
    cwd: str = Field(".", description="工作目录 (add_pane)")
    env_vars: dict[str, Any] = Field(default_factory=dict, description="环境变量 (add_pane)")
    max_visible: int = Field(3, ge=1, description="最大可见面板数")


class CreateFrozenRequest(_ModelBase):
    """Request body for POST /v1/capability/frozen."""

    action: Literal[
        "add", "is_frozen", "is_evolvable", "can_modify", "assert_modifiable", "list_sentinels"
    ] = Field("is_frozen", description="操作类型")
    path: str | None = Field(None, description="文件路径 (add/is_frozen 等必填)")
    zone: Literal[
        "frozen-canonical", "frozen-safety", "evolvable-tuning", "evolvable-experimental"
    ] | None = Field(None, description="zone 字段 (add/can_modify 必填)")
    sentinel: str = Field("", description="哨兵")
    reason: str = Field("", description="原因")
    added_at: float = Field(default_factory=time.time, description="添加时间")


class CreateTurboquantRequest(_ModelBase):
    """Request body for POST /v1/capability/turboquant."""

    messages: list[dict[str, Any]] = Field(
        default_factory=list, description="消息列表, 每项 {role, content, timestamp}"
    )
    level: str = Field("Q4", description="量化等级 (Q0/Q1/Q2/Q4/Q8, 大小写不敏感)")
    hard_cap: int = Field(60, ge=1, description="消息硬上限")
    preserve: int = Field(30, ge=0, description="保留消息数")
    action: Literal["should_compress", "apply"] = Field("apply", description="操作类型")


class CreateMoaEngineRequest(_ModelBase):
    """Request body for POST /v1/capability/moa-engine."""

    proposers: list[dict[str, Any]] = Field(
        default_factory=list,
        description="提议者列表, 每个含 model_id 和 system_prompt",
    )
    aggregator: dict[str, Any] | None = Field(
        None, description="聚合者 (model_id + synthesis_prompt)"
    )
    query: str = Field("", description="查询文本 / 用户问题")
    validate_only: bool | None = Field(None, description="仅校验不执行")


class CreateAcceptanceRequest(_ModelBase):
    """Request body for POST /v1/capability/acceptance."""

    action: Literal["add", "parse_ears", "validate_pattern", "get_tree"] = Field(
        "add", description="操作类型"
    )
    root_id: str = Field("root", description="root_id 字段")
    criteria: list[dict[str, Any]] = Field(
        default_factory=list, description="AcceptanceCriterion 列表 (add)"
    )
    text: str = Field("", description="文本 (parse_ears)")
    criterion: dict[str, Any] | None = Field(
        None, description="单个 AcceptanceCriterion (validate_pattern 必填)"
    )


class CreateLlmMergeRequest(_ModelBase):
    """Request body for POST /v1/capability/llm-merge."""

    action: Literal["merge", "fallback"] = Field("merge", description="操作类型")
    responses: list[dict[str, Any]] = Field(
        default_factory=list, description="LLMResponse 列表 (merge)"
    )
    strategy: str = Field(
        "concat",
        description="合并策略 (concat/dedup/vote/weighted/first_success, 大小写不敏感)",
    )
    providers: list[str] = Field(default_factory=list, description="Provider 列表 (fallback)")
    fail_at: list[str] = Field(default_factory=list, description="fallback 模拟失败的 provider")


class CreateGraceRequest(_ModelBase):
    """Request body for POST /v1/capability/grace."""

    action: Literal[
        "register", "record_pass", "record_fail", "should_block", "status", "warnings"
    ] = Field("should_block", description="操作类型")
    name: str = Field("default", description="名称 (register)")
    check_id: str | None = Field(
        None, description="检查 ID (record_pass/record_fail/should_block/status 必填)"
    )
    at: float | None = Field(None, description="事件时间戳")


class CreateRagSearchRequest(_ModelBase):
    """Request body for POST /v1/capability/rag-search."""

    query: str = Field("", description="查询文本 / 用户问题")
    corpus: list[str] = Field(default_factory=list, description="语料库 (字符串列表)")
    max_results: int = Field(3, ge=1, description="最大结果数")


class CreatePlanActRequest(_ModelBase):
    """Request body for POST /v1/capability/plan-act."""

    query: str = Field("", description="查询文本 / 用户问题")


class CreateChannelsRequest(_ModelBase):  # type: ignore[no-redef]
    """Request body for POST /v1/capability/channels."""

    action: Literal["classify_error", "chain_info", "execute"] = Field(
        "execute", description="操作类型"
    )
    query: str = Field("", description="查询文本 / 用户问题")
    error: str = Field("", description="error 字段 (classify_error)")
    enabled: list[str] = Field(
        default_factory=lambda: ["ch1", "ch2", "ch3"], description="启用的通道"
    )
    cli_latency_ms: int = Field(50, ge=0, description="cli_latency_ms 字段")
    api_latency_ms: int = Field(150, ge=0, description="api_latency_ms 字段")
    kwargs: dict[str, Any] = Field(default_factory=dict, description="execute 附加参数")


class CreateReferenceRouterRequest(_ModelBase):
    """Request body for POST /v1/capability/reference-router."""

    query: str = Field("", description="查询文本 / 用户问题")
    strategy: Literal["none", "shadow", "validate", "veto"] = Field("shadow", description="策略")
    main_model: str = Field("main", description="主模型")
    ref_model: str = Field("ref", description="参考模型")
    max_latency_ms: int = Field(5000, ge=1, description="最大延迟 (ms)")
    cost_ratio_cap: float = Field(2.0, gt=0, description="成本比上限")


class CreateCheckpointRequest(_ModelBase):
    """Request body for POST /v1/capability/checkpoint."""

    action: Literal["save", "load", "list", "delete", "cleanup"] = Field(
        "save", description="操作类型"
    )
    root_dir: str | None = Field(None, description="root_dir 字段 (必须在白名单内)")
    name: str = Field(
        "default", pattern=r"^[a-zA-Z0-9_-]{1,64}$", description="名称 ([a-zA-Z0-9_-]{1,64})"
    )
    max_keep: int = Field(10, ge=1, description="max_keep 字段")
    payload: dict[str, Any] = Field(default_factory=dict, description="save 负载")
    older_than_seconds: float | None = Field(None, ge=0, description="cleanup 年龄阈值 (秒)")
    raw_payload: str | None = Field(
        None,
        description="原始负载字符串 (兼容字段; 端点大小守卫读取的是无法声明的 _raw_payload)",
    )


class CreateCanaryRequest(_ModelBase):
    """Request body for POST /v1/capability/canary."""

    action: Literal["inject", "check"] = Field("inject", description="操作类型")
    strategy: Literal["suffix", "prefix", "invisible", "multi"] = Field(
        "suffix", description="注入策略"
    )
    prompt: str = Field("", description="Prompt 文本 (inject)")
    response: str = Field("", description="响应文本 (check)")
    canary: str = Field("", description="canary 字段 (check)")


class CreateWrapOutputRequest(_ModelBase):
    """Request body for POST /v1/capability/wrap-output."""

    action: Literal["wrap", "unwrap", "sanitize", "needs_wrapping"] = Field(
        "wrap", description="操作类型"
    )
    content: str = Field("", description="内容")
    source: str = Field("tool", description="source 字段")
    trust: Literal["trusted", "semi", "untrusted"] = Field("untrusted", description="trust 字段")
    max_length: int = Field(8192, ge=1, description="max_length 字段")
    wrapped: str = Field("", description="wrapped 字段 (unwrap)")
    aggressive: bool = Field(False, description="aggressive 字段 (sanitize)")


class CreateFuzzyDedupRequest(_ModelBase):
    """Request body for POST /v1/capability/fuzzy-dedup."""

    action: Literal["add", "check", "simhash"] = Field("check", description="操作类型")
    max_size: int = Field(10000, ge=1, description="最大大小")
    text: str = Field("", description="文本")
    metadata: Any | None = Field(None, description="元数据 (add)")
    threshold: float = Field(0.85, ge=0, le=1, description="相似度阈值 (check)")


class CreateInputFingerprintRequest(_ModelBase):
    """Request body for POST /v1/capability/input-fingerprint."""

    action: Literal["hash", "similar", "store"] = Field("hash", description="操作类型")
    text: str = Field("", description="文本")
    a: str = Field("", description="字符串 A (similar)")
    b: str = Field("", description="字符串 B (similar)")
    level: str = Field("normalized", description="比较层级 (similar)")
    max_size: int = Field(50000, ge=1, description="最大大小")
    metadata: Any | None = Field(None, description="元数据 (store)")
    collisions_with: str | None = Field(None, description="碰撞检测输入 (store)")
    min_levels: int = Field(2, ge=1, description="min_levels 字段")


class CreateToolScreeningRequest(_ModelBase):
    """Request body for POST /v1/capability/tool-screening."""

    tool_name: str = Field("unknown", min_length=1, description="工具名称")
    arguments: dict[str, Any] = Field(default_factory=dict, description="工具参数")


class CreateAnthropicCompatRequest(_ModelBase):
    """Request body for POST /v1/capability/anthropic-compat."""

    action: Literal[
        "parse",
        "format_response",
        "format_sse",
        "format_tool_use",
        "format_tool_result",
        "format_error",
    ] = Field("parse", description="操作类型")
    anthropic_request: dict[str, Any] = Field(default_factory=dict, description="parse 输入")
    chat_response: dict[str, Any] = Field(default_factory=dict, description="format_response 输入")
    delta: str = Field("", description="增量 (format_sse)")
    model: str = Field("unknown", description="模型")
    stop_reason: str | None = Field(None, description="停止原因")
    tool_id: str = Field("toolu_xxx", description="工具 ID (format_tool_use)")
    name: str = Field("tool", description="名称")
    input: dict[str, Any] = Field(default_factory=dict, description="工具输入 (format_tool_use)")
    tool_use_id: str = Field("toolu_xxx", description="工具使用 ID (format_tool_result)")
    content: str = Field("", description="内容 (format_tool_result)")
    is_error: bool = Field(False, description="是否错误")
    error_type: str = Field("api_error", description="错误类型 (format_error)")
    message: str = Field("", description="消息内容 (format_error)")


class CreateTokenBucketRequest(_ModelBase):
    """Request body for POST /v1/capability/token-bucket."""

    action: Literal["try_consume", "state", "cleanup"] = Field("try_consume", description="操作类型")
    capacity: int = Field(60, ge=1, description="容量")
    refill_rate: float = Field(1.0, gt=0, description="补充速率")
    key: str = Field("default", min_length=1, description="键")
    tokens: int = Field(1, ge=1, description="token 数量")


class CreateRequestDedupRequest(_ModelBase):
    """Request body for POST /v1/capability/request-dedup."""

    strategy: str = Field(
        "normalized", description="去重策略 (exact/normalized/semantic, 未知回退 normalized)"
    )
    ttl_seconds: int = Field(60, ge=1, description="ttl_seconds 字段")
    max_size: int = Field(10000, ge=1, description="最大大小")
    method: str = Field("POST", description="HTTP 方法")
    path: str = Field("/", description="请求路径")
    body: Any | None = Field(None, description="请求 body")
    source: str = Field("default", description="source 字段")
    action: Literal["check", "record", "stats", "cleanup"] = Field("check", description="操作类型")
    response: Any | None = Field(None, description="响应文本 (record)")


class CreateTraceRequest(_ModelBase):
    """Request body for POST /v1/capability/trace."""

    max_traces: int = Field(10000, ge=1, description="max_traces 字段")
    action: Literal["start", "span", "end", "get", "query", "parse_traceparent"] = Field(
        "start", description="操作类型"
    )
    traceparent: str | None = Field(None, description="Trace 上下文 (W3C traceparent)")
    trace_id: str | None = Field(None, description="追踪 ID (span/end/get 必填)")
    name: str = Field("child", description="名称 (span)")
    duration_ms: float = Field(0.0, ge=0, description="持续时间 (ms)")
    span_id: str = Field("", description="Span ID (end)")
    status: str = Field("ok", description="status 字段 (end/query)")
    error: str | None = Field(None, description="error 字段 (end)")
    since_ts: float | None = Field(None, description="query 起始时间戳")
    min_duration_ms: float | None = Field(None, ge=0, description="query 最小耗时过滤")
    limit: int = Field(100, ge=1, description="query 返回上限")


# ============ agent ============


class CreateAgentDispatchRequest(_ModelBase):
    """Request body for POST /v1/agent/dispatch."""

    service: str = Field(..., min_length=1, max_length=128, description="服务名 (缺失 → 422)")
    method: str = Field(..., min_length=1, max_length=128, description="方法名 (缺失 → 422)")
    payload: dict[str, Any] | None = Field(None, description="业务负载")


class CreateAgentDispatchBatchRequest(_ModelBase):
    """Request body for POST /v1/agent/dispatch_batch."""

    calls: list[dict[str, Any]] = Field(
        default_factory=list,
        description="批量调用列表, 每项 {service, method, payload}",
    )


class CreateAgentWorkflowRegisterRequest(_ModelBase):
    """Request body for POST /v1/agent/workflow/register."""

    name: str = Field(..., min_length=1, max_length=128, description="名称 (缺失 → 422)")
    description: str = Field("", description="描述")
    steps: list[dict[str, Any]] = Field(
        default_factory=list, description="步骤列表 (必须是 list)"
    )


class CreateAgentWorkflowRunRequest(_ModelBase):
    """Request body for POST /v1/agent/workflow/run."""

    name: str = Field(..., min_length=1, max_length=128, description="名称 (缺失 → 422)")
    input: dict[str, Any] | None = Field(None, description="输入")


class CreateAgentRunLoopRequest(_ModelBase):
    """Request body for POST /v1/agent/run-loop."""

    messages: list[dict[str, Any]] = Field(
        ..., min_length=1, description="Conversation messages (非空 list, 缺失 → 422)"
    )
    loop_name: Literal["react", "plan_execute"] = Field("react", description="Loop type")
    max_iterations: int = Field(10, ge=1, description="Max loop iterations")
    tools: list[str] = Field(default_factory=list, description="Tool names to enable")
    endpoint_id: str | None = Field(
        None, max_length=128,
        description="Optional model endpoint id to pin the loop to (v3.1.1); "
        "defaults to the pool's first endpoint",
    )


# ============ Model registry ============

# Maps endpoint path → Request model
ENDPOINT_MODELS: dict[str, type[BaseModel]] = {
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
    "/v1/embeddings": CreateEmbeddingsRequest,
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
    "/v1/capability/audit-cache": CreateAuditRequest,
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
    "/v1/agent/run-loop": CreateAgentRunLoopRequest,
}
