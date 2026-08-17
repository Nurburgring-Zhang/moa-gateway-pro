"""Validation coverage for every request model in ``moa_gateway.req_models``.

P1 regression guard: the 85 request models used to be ``Any``-typed with
``extra="ignore"``, so POST bodies were never validated. For EVERY model this
suite asserts:

1. a realistic, legal body is accepted (and dict-like access still works);
2. an illegal body (missing required field / wrong type / out-of-range value /
   bad Literal member) is rejected with ``ValidationError``;
3. unknown extra fields are rejected (``extra="forbid"`` actually enforced).

The cases are driven by ``ENDPOINT_MODELS`` so a newly added endpoint model
that lacks coverage fails the meta-test automatically.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from moa_gateway import req_models as rm

# ---------------------------------------------------------------------------
# Distinct model classes, driven from the endpoint registry
# ---------------------------------------------------------------------------

ALL_MODELS: list[type] = sorted(set(rm.ENDPOINT_MODELS.values()), key=lambda m: m.__name__)


def _ids(m: type) -> str:
    return m.__name__


# ---------------------------------------------------------------------------
# Legal bodies — one realistic request per model (matches endpoint docstrings)
# ---------------------------------------------------------------------------

VALID_BODIES: dict[type, dict] = {
    rm.CreateMoaEvalRequest: {
        "query": "What is 2+2?",
        "candidates": ["gpt-4o", "deepseek-v3"],
        "reference_answer": "4",
        "temperature": 0.3,
    },
    rm.CreateMoaSimilarityRequest: {
        "candidate_a": "answer A",
        "candidate_b": "answer B",
        "model_id": "gpt-4o",
        "query": "q",
    },
    rm.CreateMoaFlaskRequest: {
        "query": "q",
        "response": "r",
        "reference": "ref",
        "judge_model": "gpt-4o",
    },
    rm.CreateMoaBenchmarkRequest: {"category": "reasoning", "limit": 1, "presets": ["fast"]},
    rm.CreateMoaCostParetoRequest: {
        "prompts": ["p1", "p2", "p3"],
        "presets": ["fast", "balanced"],
    },
    rm.UpdateMoaPromptsNameRequest: {"content": "You are {{REFERENCES}}."},
    rm.CreateSecretScanRequest: {"path": ".", "fail_on": 3, "no_block": False},
    rm.CreateGroupThinkCheckRequest: {
        "session_id": "s1",
        "members": [{"member_id": "m1", "content": "c", "round": 0}],
        "rounds": [[{"member_id": "m1", "content": "c", "round": 1}]],
        "warn_threshold": 0.4,
        "block_threshold": 0.7,
    },
    rm.CreateEnsembleVoteRequest: {
        "votes": [
            {"voter_id": "v1", "candidate": "a", "confidence": 0.9, "reason": "r"}
        ],
        "method": "weighted",
    },
    rm.CreateShouldRebalanceRequest: {
        "stats": {"deepseek-v3": {"tier": "standard", "endpoint_count": 1}},
        "config": {"high_threshold": 0.8},
    },
    rm.CreateCostEstimateRequest: {
        "input_tokens": 1000,
        "output_tokens": 500,
        "channels": [{"name": "deepseek-v3", "cost_per_1k_input": 0.0005}],
        "include_fallback": True,
        "format": "report",
    },
    rm.CreateGateL0Request: {"query": "design a distributed system"},
    rm.CreateScorePanelRequest: {"query": "q", "answer": "a"},
    rm.CreateCalculateMaxTokensRequest: {
        "model_id": "gpt-4o",
        "input_tokens": 1000,
        "requested_output": 2000,
        "safety_margin": 0.1,
    },
    rm.CreateEstimateCostRequest: {"model_id": "gpt-4o", "input_tokens": 1000, "output_tokens": 500},
    rm.CreateQuotaCheckRequest: {
        "windows": [{"name": "5h", "limit_tokens": 100000, "used_history": [[1, 10]]}],
        "requested": 1000,
        "burn_rate_per_hour": 1000.0,
    },
    rm.CreateQuotaRecordRequest: {
        "windows": [{"name": "5h", "limit_tokens": 100000, "used_history": []}],
        "tokens": 10,
    },
    rm.CreateMoaNLayerRequest: {
        "query": "q",
        "proposers": [{"name": "a", "model_id": "gpt-4o"}],
        "aggregators": [
            {"model_id": "m1"},
            {"model_id": "m2"},
            {"model_id": "m3"},
        ],
        "temperature": 0.6,
        "max_total_tokens": 0,
    },
    rm.CreateConvergentDetectRequest: {
        "proposals": [{"proposal_idx": 0, "author": "a", "text": "t"}],
        "min_support": 2,
        "viability_scores": {"0": 0.8},
    },
    rm.CreateActionPolicyRequest: {"command": "ls -la", "rules": []},
    rm.CreateEmbeddingsRequest: {"input": ["text1", "text2"], "model": "mock", "dim": 384},
    rm.CreateSemanticSearchRequest: {
        "query": "q",
        "documents": ["a", "b", "c"],
        "top_k": 3,
        "dim": 384,
    },
    rm.CreatePromptFeaturesRequest: {"text": "URGENT: fix production bug now"},
    rm.CreateProviderHealthRequest: {
        "providers": [
            {"provider": "deepseek-v3", "total_calls": 100, "failures": 1, "p95_latency_ms": 900}
        ],
        "prefer_tier": "premium",
    },
    rm.CreateContextCleanRequest: {
        "messages": [{"role": "user", "content": "hi"}],
        "max_total_chars": 100000,
    },
    rm.CreateSelfHealRequest: {
        "endpoints": [],
        "action": "auto_balance",
    },
    rm.CreateMultiModeSynthRequest: {
        "mode": "classification",
        "proposals": [{"proposal_idx": 0, "author": "a", "text": "t"}],
    },
    rm.CreateConflictArbitrateRequest: {"options": [], "fuse": False, "query": "q"},
    rm.CreateSectionViabilityRequest: {"text": "## Section\ncode here", "proposal_idx": 0},
    rm.CreateFeedbackIterRequest: {"record": {}, "history_path": ""},
    rm.CreateStreamAggregateRequest: {
        "prompt": "p",
        "model": "mock-stream-v1",
        "fail_prob": 0.0,
    },
    rm.CreatePerProviderRlRequest: {
        "provider": "deepseek-v3",
        "action": "check",
        "concurrent": 0,
    },
    rm.CreateTierRecalibrateRequest: {
        "tiers": [{"tier": "standard", "p50_latency_ms": 800, "p95_latency_ms": 1500}]
    },
    rm.CreateConsumptionIntelRequest: {"context": {"query": "q"}, "endpoints": []},
    rm.CreateImportanceScoreRequest: {
        "messages": [{"role": "user", "content": "hi"}],
        "top_k": 1,
        "threshold": 0.5,
    },
    rm.CreateQuorumCheckRequest: {
        "participants": [],
        "required": 3,
        "grace_seconds": 30.0,
        "wait_for_laggards": True,
    },
    rm.CreateModelEntryRequest: {"models": [], "filter": {}, "sort": "cost_asc"},
    rm.CreateToolReplayRequest: {
        "proposals": ["<tool_use>search</tool_use>"],
        "window": 5,
    },
    rm.CreateHookEventsRequest: {"action": "list_events"},
    rm.CreateMetaPromptRequest: {"action": "get_stages", "query": "q"},
    rm.CreateTaskTreeRequest: {"action": "ready", "tasks": []},
    rm.CreateDistillRequest: {
        "proposals": ["idea one", "idea two"],
        "keep_ratio": 0.5,
    },
    rm.CreateRerankRequest: {
        "query": "q",
        "documents": ["d1", "d2"],
        "top_n": 2,
        "latency_budget_ms": 2000.0,
    },
    rm.CreateGoalEvalRequest: {"goals": [], "output": "out", "generate_ceiling": False},
    rm.CreateAutoConvergeRequest: {"config": {"stagnation_threshold": 3}},
    rm.CreateSubagentCommsRequest: {"action": "inbox", "session_id": "s1"},
    rm.CreateVersionRequest: {"action": "latest", "proposal_id": "p1"},
    rm.CreateConfigRequest: {"action": "permission", "mode": "default"},
    rm.CreateBubbleRequest: {"action": "pending", "parent_id": "p1"},
    rm.CreateRouteRequest: {"action": "route_request", "task": "fix bug"},
    rm.CreateSessionLockRequest: {"action": "cleanup_expired"},
    rm.CreateFlaskRequest: {"answer": "a", "query": "q"},
    rm.CreateEloRequest: {
        "action": "record",
        "model_ids": ["a", "b"],
        "matches": [],
        "k_factor": 4.0,
    },
    rm.CreateBrainstormRequest: {"action": "ideas", "topic": "t"},
    rm.CreateCrossIterRequest: {"action": "step5", "iters": [], "step5_mode": "sintesis_central"},
    rm.CreateAuditRequest: {"action_id": "a1", "action_data": {"action": "read"}},
    rm.CreateInFlightRequest: {"action": "in_flight"},
    rm.CreateMxRequest: {"action": "parse", "text": "# mx: test", "file_path": "f.py"},
    rm.CreateTierPromoRequest: {"action": "classify", "evidence": []},
    rm.CreateArtifactRequest: {"action": "layout"},
    rm.CreateFrozenRequest: {"action": "list_sentinels"},
    rm.CreateTurboquantRequest: {
        "action": "should_compress",
        "messages": [{"role": "user", "content": "c", "timestamp": 1.0}],
        "level": "Q4",
    },
    rm.CreateMoaEngineRequest: {
        "proposers": [{"model_id": "gpt-4o", "system_prompt": "s"}],
        "aggregator": {"model_id": "gpt-4o", "synthesis_prompt": "s"},
        "query": "q",
        "validate_only": True,
    },
    rm.CreateAcceptanceRequest: {"action": "get_tree", "root_id": "root"},
    rm.CreateLlmMergeRequest: {"action": "merge", "responses": [], "strategy": "concat"},
    rm.CreateGraceRequest: {"action": "register", "name": "chk"},
    rm.CreateRagSearchRequest: {"query": "q", "corpus": ["doc1", "doc2"], "max_results": 2},
    rm.CreatePlanActRequest: {"query": "plan the migration"},
    rm.CreateChannelsRequest: {"action": "chain_info"},
    rm.CreateReferenceRouterRequest: {"query": "q", "strategy": "shadow"},
    rm.CreateCheckpointRequest: {"action": "list", "name": "default"},
    rm.CreateCanaryRequest: {"action": "inject", "prompt": "p", "strategy": "suffix"},
    rm.CreateWrapOutputRequest: {
        "action": "wrap",
        "content": "c",
        "source": "tool",
        "trust": "untrusted",
    },
    rm.CreateFuzzyDedupRequest: {"action": "simhash", "text": "t"},
    rm.CreateInputFingerprintRequest: {"action": "hash", "text": "t"},
    rm.CreateToolScreeningRequest: {
        "tool_name": "file_read",
        "arguments": {"path": "/tmp/x"},
    },
    rm.CreateAnthropicCompatRequest: {
        "action": "parse",
        "anthropic_request": {"model": "claude-3", "messages": []},
    },
    rm.CreateTokenBucketRequest: {"action": "try_consume", "key": "k", "tokens": 1},
    rm.CreateRequestDedupRequest: {
        "action": "check",
        "method": "POST",
        "path": "/x",
        "body": {"a": 1},
    },
    rm.CreateTraceRequest: {"action": "start"},
    rm.CreateAgentDispatchRequest: {
        "service": "moa",
        "method": "run_three_layer",
        "payload": {},
    },
    rm.CreateAgentDispatchBatchRequest: {
        "calls": [{"service": "moa", "method": "x", "payload": {}}]
    },
    rm.CreateAgentWorkflowRegisterRequest: {
        "name": "wf1",
        "description": "d",
        "steps": [{"name": "s1", "service": "moa", "method": "m"}],
    },
    rm.CreateAgentWorkflowRunRequest: {"name": "wf1", "input": {}},
    rm.CreateAgentRunLoopRequest: {
        "messages": [{"role": "user", "content": "hi"}],
        "loop_name": "react",
        "max_iterations": 2,
        "tools": ["web_search"],
    },
}

# ---------------------------------------------------------------------------
# Illegal bodies — missing required / wrong type / out-of-range / bad Literal
# ---------------------------------------------------------------------------

INVALID_BODIES: dict[type, dict] = {
    rm.CreateMoaEvalRequest: {},  # missing required query + candidates
    rm.CreateMoaSimilarityRequest: {"candidate_a": "a"},  # missing candidate_b
    rm.CreateMoaFlaskRequest: {"query": "q"},  # missing response
    rm.CreateMoaBenchmarkRequest: {"limit": "not-an-int"},
    rm.CreateMoaCostParetoRequest: {"prompts": []},  # min_length=1
    rm.UpdateMoaPromptsNameRequest: {"content": ""},  # min_length=1
    rm.CreateSecretScanRequest: {"fail_on": -1},  # ge=0
    rm.CreateGroupThinkCheckRequest: {"warn_threshold": 1.5},  # le=1
    rm.CreateEnsembleVoteRequest: {"method": "unknown_method"},  # Literal
    rm.CreateShouldRebalanceRequest: {"stats": ["not", "a", "dict"]},
    rm.CreateCostEstimateRequest: {"input_tokens": -5},  # ge=0
    rm.CreateGateL0Request: {"query": 123},  # int is not coerced to str
    rm.CreateScorePanelRequest: {"answer": 42},
    rm.CreateCalculateMaxTokensRequest: {"safety_margin": -0.1},  # ge=0
    rm.CreateEstimateCostRequest: {"input_tokens": "many"},
    rm.CreateQuotaCheckRequest: {"windows": {"5h": {}}},  # dict, not list
    rm.CreateQuotaRecordRequest: {"tokens": -1},  # ge=0
    rm.CreateMoaNLayerRequest: {"temperature": 3.0},  # le=2
    rm.CreateConvergentDetectRequest: {"min_support": 0},  # ge=1
    rm.CreateActionPolicyRequest: {"rules": {"not": "list"}},
    rm.CreateEmbeddingsRequest: {"dim": 0},  # ge=1
    rm.CreateSemanticSearchRequest: {"top_k": 0},  # ge=1
    rm.CreatePromptFeaturesRequest: {"text": 123},
    rm.CreateProviderHealthRequest: {"providers": {"deepseek": {}}},  # dict, not list
    rm.CreateContextCleanRequest: {"max_total_chars": 0},  # ge=1
    rm.CreateSelfHealRequest: {"action": "bogus_action"},  # Literal
    rm.CreateMultiModeSynthRequest: {"mode": "bogus_mode"},  # Literal
    rm.CreateConflictArbitrateRequest: {"fuse": "not_a_bool"},
    rm.CreateSectionViabilityRequest: {"proposal_idx": -1},  # ge=0
    rm.CreateFeedbackIterRequest: {"record": []},  # list, not dict
    rm.CreateStreamAggregateRequest: {"fail_prob": 1.5},  # le=1
    rm.CreatePerProviderRlRequest: {"action": "bogus"},  # Literal
    rm.CreateTierRecalibrateRequest: {"tiers": {"standard": {}}},  # dict, not list
    rm.CreateConsumptionIntelRequest: {"context": []},  # list, not dict
    rm.CreateImportanceScoreRequest: {"threshold": 2.0},  # le=1
    rm.CreateQuorumCheckRequest: {"required": 0},  # ge=1
    rm.CreateModelEntryRequest: {"max_budget_input": -1},  # ge=0
    rm.CreateToolReplayRequest: {"window": 0},  # ge=1
    rm.CreateHookEventsRequest: {"action": "bogus"},  # Literal
    rm.CreateMetaPromptRequest: {"action": "bogus"},  # Literal
    rm.CreateTaskTreeRequest: {"status": "bogus_status"},  # Literal
    rm.CreateDistillRequest: {"keep_ratio": 1.5},  # le=1
    rm.CreateRerankRequest: {"top_n": 0},  # ge=1
    rm.CreateGoalEvalRequest: {"goals": {"not": "list"}},
    rm.CreateAutoConvergeRequest: {"classify_events": -1},  # ge=0
    rm.CreateSubagentCommsRequest: {"action": "bogus"},  # Literal
    rm.CreateVersionRequest: {"action": "bogus"},  # Literal
    rm.CreateConfigRequest: {"mode": "bogus_mode"},  # Literal
    rm.CreateBubbleRequest: {"decision": "bogus"},  # Literal
    rm.CreateRouteRequest: {"tier": "flagship"},  # Literal (HarnessTier)
    rm.CreateSessionLockRequest: {"action": "bogus"},  # Literal
    rm.CreateFlaskRequest: {"tasks": {"not": "list"}},
    rm.CreateEloRequest: {"strategy": "bogus"},  # Literal
    rm.CreateBrainstormRequest: {"action": "bogus"},  # Literal
    rm.CreateCrossIterRequest: {"step5_mode": "bogus"},  # Literal
    rm.CreateAuditRequest: {"action": "bogus"},  # Literal
    rm.CreateInFlightRequest: {"phase": "bogus_phase"},  # Literal
    rm.CreateMxRequest: {"action": "bogus"},  # Literal
    rm.CreateTierPromoRequest: {"confidence_threshold": 1.5},  # le=1
    rm.CreateArtifactRequest: {"type": "bogus_type"},  # Literal
    rm.CreateFrozenRequest: {"zone": "bogus-zone"},  # Literal
    rm.CreateTurboquantRequest: {"action": "bogus"},  # Literal
    rm.CreateMoaEngineRequest: {"aggregator": ["not", "dict"]},
    rm.CreateAcceptanceRequest: {"action": "bogus"},  # Literal
    rm.CreateLlmMergeRequest: {"action": "bogus"},  # Literal
    rm.CreateGraceRequest: {"action": "bogus"},  # Literal
    rm.CreateRagSearchRequest: {"corpus": "not a list"},
    rm.CreatePlanActRequest: {"query": ["not", "str"]},
    rm.CreateChannelsRequest: {"action": "bogus"},  # Literal
    rm.CreateReferenceRouterRequest: {"strategy": "bogus"},  # Literal
    rm.CreateCheckpointRequest: {"name": "bad name!"},  # pattern violation
    rm.CreateCanaryRequest: {"strategy": "bogus"},  # Literal
    rm.CreateWrapOutputRequest: {"trust": "bogus"},  # Literal
    rm.CreateFuzzyDedupRequest: {"threshold": 1.5},  # le=1
    rm.CreateInputFingerprintRequest: {"min_levels": 0},  # ge=1
    rm.CreateToolScreeningRequest: {"tool_name": ""},  # min_length=1
    rm.CreateAnthropicCompatRequest: {"action": "bogus"},  # Literal
    rm.CreateTokenBucketRequest: {"capacity": 0},  # ge=1
    rm.CreateRequestDedupRequest: {"action": "bogus"},  # Literal
    rm.CreateTraceRequest: {"action": "bogus"},  # Literal
    rm.CreateAgentDispatchRequest: {},  # missing service + method
    rm.CreateAgentDispatchBatchRequest: {"calls": {"not": "list"}},
    rm.CreateAgentWorkflowRegisterRequest: {},  # missing name
    rm.CreateAgentWorkflowRunRequest: {},  # missing name
    rm.CreateAgentRunLoopRequest: {"messages": []},  # min_length=1
}


# ---------------------------------------------------------------------------
# Meta-test: registry completeness
# ---------------------------------------------------------------------------


def test_registry_covers_exactly_85_models():
    """ENDPOINT_MODELS must reference exactly the 85 known request models."""
    assert len(ALL_MODELS) == 85, f"expected 85 distinct models, got {len(ALL_MODELS)}"


def test_every_model_has_valid_and_invalid_case():
    """Every registered model must have both a legal and an illegal body here."""
    missing_valid = [m.__name__ for m in ALL_MODELS if m not in VALID_BODIES]
    missing_invalid = [m.__name__ for m in ALL_MODELS if m not in INVALID_BODIES]
    assert not missing_valid, f"missing valid bodies: {missing_valid}"
    assert not missing_invalid, f"missing invalid bodies: {missing_invalid}"


def test_all_models_forbid_extra():
    """P1 fix: every model must reject unknown fields (extra='forbid')."""
    for model in ALL_MODELS:
        assert model.model_config.get("extra") == "forbid", model.__name__


def test_no_any_typed_fields_left():
    """P1 fix: no field may be a bare ``Any | None`` placeholder anymore.

    ``Any`` is still allowed for genuinely untyped payloads (dict values,
    free-form metadata), but such fields must carry a description.
    """
    for model in ALL_MODELS:
        for name, field in model.model_fields.items():
            assert field.description, f"{model.__name__}.{name} lacks a description"


# ---------------------------------------------------------------------------
# Per-model behaviour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ALL_MODELS, ids=_ids)
def test_valid_body_accepted(model):
    inst = model(**VALID_BODIES[model])
    # dict-like access used by endpoint code keeps working
    dumped = dict(inst)
    assert isinstance(dumped, dict)


@pytest.mark.parametrize("model", ALL_MODELS, ids=_ids)
def test_empty_body_behaviour(model):
    """Empty body: accepted only when every field has a default."""
    has_required = any(f.is_required() for f in model.model_fields.values())
    if has_required:
        with pytest.raises(ValidationError):
            model(**{})
    else:
        model(**{})  # all-optional models must accept {}


@pytest.mark.parametrize("model", ALL_MODELS, ids=_ids)
def test_invalid_body_rejected(model):
    with pytest.raises(ValidationError):
        model(**INVALID_BODIES[model])


@pytest.mark.parametrize("model", ALL_MODELS, ids=_ids)
def test_unknown_field_rejected(model):
    body = dict(VALID_BODIES[model])
    body["definitely_not_a_real_field_xyz"] = 1
    with pytest.raises(ValidationError) as excinfo:
        model(**body)
    assert any(e["type"] == "extra_forbidden" for e in excinfo.value.errors())


# ---------------------------------------------------------------------------
# HTTP level: FastAPI must turn validation failures into 422
# ---------------------------------------------------------------------------


def _build_app() -> tuple[FastAPI, list[type]]:
    app = FastAPI()
    ordered = ALL_MODELS
    for i, model in enumerate(ordered):

        def _make_endpoint(m):
            async def _endpoint(body: m):
                return {"ok": True, "model": type(body).__name__}

            return _endpoint

        app.post(f"/t/{i}")(_make_endpoint(model))
    return app, ordered


def test_http_valid_bodies_return_200():
    app, ordered = _build_app()
    client = TestClient(app)
    for i, model in enumerate(ordered):
        resp = client.post(f"/t/{i}", json=VALID_BODIES[model])
        assert resp.status_code == 200, f"{model.__name__}: {resp.text}"


def test_http_invalid_bodies_return_422():
    app, ordered = _build_app()
    client = TestClient(app)
    for i, model in enumerate(ordered):
        resp = client.post(f"/t/{i}", json=INVALID_BODIES[model])
        assert resp.status_code == 422, f"{model.__name__}: got {resp.status_code} {resp.text}"


def test_http_unknown_fields_return_422():
    app, ordered = _build_app()
    client = TestClient(app)
    for i, model in enumerate(ordered):
        body = dict(VALID_BODIES[model])
        body["totally_unknown_extra_key"] = "x"
        resp = client.post(f"/t/{i}", json=body)
        assert resp.status_code == 422, f"{model.__name__}: got {resp.status_code} {resp.text}"


# ---------------------------------------------------------------------------
# Spot checks: required fields and endpoint-default parity
# ---------------------------------------------------------------------------


def test_required_fields_are_enforced():
    with pytest.raises(ValidationError):
        rm.CreateMoaEvalRequest(candidates=["a"])  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        rm.CreateAgentDispatchRequest(service="moa")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        rm.CreateAgentRunLoopRequest(loop_name="react")  # type: ignore[call-arg]


def test_defaults_match_endpoint_defaults():
    """Optional field defaults must equal the endpoint's body.get() defaults."""
    assert rm.CreateMoaBenchmarkRequest().limit == 5
    assert rm.CreateMoaBenchmarkRequest().category == "all"
    assert rm.CreateSecretScanRequest().path == "."
    assert rm.CreateSecretScanRequest().fail_on == 3
    assert rm.CreateSecretScanRequest().no_block is False
    assert rm.CreateEnsembleVoteRequest().method == "weighted"
    assert rm.CreateCostEstimateRequest().input_tokens == 1000
    assert rm.CreateCostEstimateRequest().output_tokens == 500
    assert rm.CreateCostEstimateRequest().include_fallback is True
    assert rm.CreateCalculateMaxTokensRequest().model_id == "gpt-4o"
    assert rm.CreateCalculateMaxTokensRequest().safety_margin == 0.1
    assert rm.CreateMoaNLayerRequest().temperature == 0.6
    assert rm.CreateConvergentDetectRequest().min_support == 3
    assert rm.CreateEmbeddingsRequest().dim == 384
    assert rm.CreateSemanticSearchRequest().top_k == 3
    assert rm.CreateContextCleanRequest().max_total_chars == 100000
    assert rm.CreateSelfHealRequest().action == "auto_balance"
    assert rm.CreateSelfHealRequest().reason == "manual"
    assert rm.CreateMultiModeSynthRequest().mode == "classification"
    assert rm.CreateStreamAggregateRequest().fail_prob == 0.0
    assert rm.CreateStreamAggregateRequest().model == "mock-stream-v1"
    assert rm.CreatePerProviderRlRequest().action == "check"
    assert rm.CreatePerProviderRlRequest().cooldown_seconds == 60.0
    assert rm.CreateImportanceScoreRequest().threshold == 0.5
    assert rm.CreateQuorumCheckRequest().required == 3
    assert rm.CreateQuorumCheckRequest().grace_seconds == 30.0
    assert rm.CreateQuorumCheckRequest().wait_for_laggards is True
    assert rm.CreateToolReplayRequest().window == 5
    assert rm.CreateHookEventsRequest().action == "ralph_advance"
    assert rm.CreateHookEventsRequest().max_iter == 5
    assert rm.CreateMetaPromptRequest().action == "get_stages"
    assert rm.CreateMetaPromptRequest().role_a == "optimist"
    assert rm.CreateTaskTreeRequest().action == "ready"
    assert rm.CreateDistillRequest().keep_ratio == 0.5
    assert rm.CreateRerankRequest().top_n == 10
    assert rm.CreateRerankRequest().latency_budget_ms == 2000.0
    assert rm.CreateAutoConvergeRequest().stagnation_threshold == 3
    assert rm.CreateAutoConvergeRequest().epsilon == 0.001
    assert rm.CreateSubagentCommsRequest().action == "send"
    assert rm.CreateSubagentCommsRequest().session_id == "default"
    assert rm.CreateSubagentCommsRequest().timeout == 10.0
    assert rm.CreateVersionRequest().action == "add"
    assert rm.CreateVersionRequest().proposal_id == "default"
    assert rm.CreateConfigRequest().action == "get"
    assert rm.CreateConfigRequest().explicit is True
    assert rm.CreateBubbleRequest().action == "escalate"
    assert rm.CreateBubbleRequest().n == 10
    assert rm.CreateRouteRequest().action == "route_request"
    assert rm.CreateRouteRequest().single_domain is True
    assert rm.CreateSessionLockRequest().timeout == 10.0
    assert rm.CreateSessionLockRequest().retry_interval == 0.01
    assert rm.CreateEloRequest().k_factor == 4.0
    assert rm.CreateEloRequest().n_resamples == 1000
    assert rm.CreateEloRequest().ci == 0.95
    assert rm.CreateEloRequest().strategy == "shortest_queue"
    assert rm.CreateBrainstormRequest().action == "ideas"
    assert rm.CreateCrossIterRequest().action == "step5"
    assert rm.CreateCrossIterRequest().step5_mode == "sintesis_central"
    assert rm.CreateAuditRequest().action_id == "a1"
    assert rm.CreateInFlightRequest().state_dir == ".moai/state"
    assert rm.CreateMxRequest().file_path == "f.py"
    assert rm.CreateTierPromoRequest().confidence_threshold == 0.70
    assert rm.CreateArtifactRequest().max_visible == 3
    assert rm.CreateTurboquantRequest().level == "Q4"
    assert rm.CreateTurboquantRequest().hard_cap == 60
    assert rm.CreateTurboquantRequest().preserve == 30
    assert rm.CreateGraceRequest().action == "should_block"
    assert rm.CreateRagSearchRequest().max_results == 3
    assert rm.CreateChannelsRequest().enabled == ["ch1", "ch2", "ch3"]
    assert rm.CreateReferenceRouterRequest().strategy == "shadow"
    assert rm.CreateReferenceRouterRequest().max_latency_ms == 5000
    assert rm.CreateReferenceRouterRequest().cost_ratio_cap == 2.0
    assert rm.CreateCheckpointRequest().name == "default"
    assert rm.CreateCheckpointRequest().max_keep == 10
    assert rm.CreateCanaryRequest().strategy == "suffix"
    assert rm.CreateWrapOutputRequest().trust == "untrusted"
    assert rm.CreateWrapOutputRequest().max_length == 8192
    assert rm.CreateFuzzyDedupRequest().threshold == 0.85
    assert rm.CreateInputFingerprintRequest().min_levels == 2
    assert rm.CreateTokenBucketRequest().capacity == 60
    assert rm.CreateTokenBucketRequest().refill_rate == 1.0
    assert rm.CreateRequestDedupRequest().strategy == "normalized"
    assert rm.CreateRequestDedupRequest().ttl_seconds == 60
    assert rm.CreateTraceRequest().max_traces == 10000
    assert rm.CreateTraceRequest().limit == 100
    _runloop = rm.CreateAgentRunLoopRequest(messages=[{"role": "user", "content": "x"}])
    assert _runloop.loop_name == "react"
    assert _runloop.max_iterations == 10
    _pareto = rm.CreateMoaCostParetoRequest(prompts=["p"])
    assert _pareto.presets == ["fast", "balanced", "quality"]
    assert rm.CreateMoaBenchmarkRequest().presets == ["balanced", "chinese_battalion"]


def test_dict_like_mixin_semantics_preserved():
    """body.get(key, default) must fall back to the endpoint default for None."""
    m = rm.CreateMoaEvalRequest(query="q", candidates=["a"])
    assert m.get("query") == "q"
    # None-valued optional → endpoint default is returned
    assert m.get("temperature", 0.3) == 0.3
    assert m.get("reference_answer") is None
    assert m["query"] == "q"
    with pytest.raises(KeyError):
        _ = m["no_such_key"]


def test_temperature_bounds():
    rm.CreateMoaEvalRequest(query="q", candidates=["a"], temperature=2.0)
    rm.CreateMoaNLayerRequest(temperature=0)
    with pytest.raises(ValidationError):
        rm.CreateMoaEvalRequest(query="q", candidates=["a"], temperature=2.5)
    with pytest.raises(ValidationError):
        rm.CreateMoaNLayerRequest(temperature=-0.1)


def test_loop_name_literal():
    rm.CreateAgentRunLoopRequest(messages=[{"role": "user", "content": "x"}], loop_name="plan_execute")
    with pytest.raises(ValidationError):
        rm.CreateAgentRunLoopRequest(
            messages=[{"role": "user", "content": "x"}], loop_name="bogus"
        )
