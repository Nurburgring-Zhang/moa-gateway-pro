"""CapabilityDispatcher — wraps ALL 76 /v1/capability/* endpoints as service methods.

This service is special: it provides a single entry point for all capability
endpoints. Each ``call_<endpoint>`` method performs a REAL internal loopback
HTTP call into the gateway's own ``/v1/capability/<endpoint>`` route, so the
full request-validation + business logic + authorization of the actual
endpoint is executed — not a passthrough acknowledgement (audit F9 fix).

Loopback uses the same internal-auth mechanism as workflows/assistant
(``internal_callback``), so the call is authenticated exactly like any other
internal consumer.
"""

from __future__ import annotations

from typing import Any

from .base import ServiceBase, ServiceMethod, dispatch_ctx


def _build_capability_map() -> dict[str, dict[str, Any]]:
    """Map capability endpoint name → (module, function, input_keys)."""
    # 73 capability modules — each has at least one function that the server.py
    # endpoint calls. We expose them all via this dispatcher.
    caps = {
        "secret_scan": ("secret_scan", ["scan_path"]),
        "group_think_check": ("moaflow", ["group_think_verdict"]),
        "ensemble_vote": ("consensus", ["ensemble_vote"]),
        "should_rebalance": ("consensus", ["should_rebalance"]),
        "cost_estimate": ("cost_estimator", ["estimate_cost"]),
        "gate_l0": ("gate_l0", ["gate"]),
        "score_panel": ("score_panel", ["score_panel"]),
        "models": ("model_context_db", ["list_models"]),
        "calculate_max_tokens": ("model_context_db", ["calculate_max_tokens"]),
        "estimate_cost": ("model_context_db", ["estimate_cost"]),
        "quota_check": ("rate_quota", ["check_quota"]),
        "quota_record": ("rate_quota", ["record_usage"]),
        "moa_n_layer": ("n_layer_moa", ["run_three_layer_moa"]),
        "convergent_detect": ("convergent_detector", ["convergent_summary", "extract_ideas"]),
        "action_policy": ("action_policy", ["evaluate"]),
        "embeddings": ("embedding", ["batch_embed", "MockEmbeddingProvider"]),
        "semantic_search": ("embedding", ["semantic_search"]),
        "prompt_features": ("prompt_features", ["extract_features"]),
        "provider_health": ("provider_health", ["aggregate"]),
        "context_clean": ("context_clean", ["clean"]),
        "self_heal": (
            "self_heal",
            [
                "record_failure",
                "record_success",
                "promote",
                "demote",
                "auto_balance",
                "check_recovery",
            ],
        ),
        "multi_mode_synth": ("multi_mode_synth", ["synthesize"]),
        "conflict_arbitrate": ("conflict_arbiter", ["arbitrate_conflicts"]),
        "section_viability": ("section_viability", ["evaluate_sections"]),
        "feedback_iter": ("feedback_loop", ["add_record", "update_record", "list_records"]),
        "stream_aggregate": ("streaming_agg", ["aggregate_stream"]),
        "per_provider_rl": (
            "per_provider_rl",
            ["check_limit", "record_request", "mark_429", "get_status"],
        ),
        "tier_recalibrate": ("tier_recalibrate", ["recalibrate"]),
        "consumption_intel": ("consumption_intel", ["analyze"]),
        "importance_score": ("importance", ["score_messages"]),
        "quorum_check": ("quorum", ["check_quorum"]),
        "model_entry": ("model_entry", ["query_models", "add_model"]),
        "tool_replay": ("tool_replay", ["replay"]),
        "hook_events": ("hook_events", ["list_events", "register", "trigger", "ralph_advance"]),
        "meta_prompt": ("meta_prompt", ["get_stages", "clash", "fuse"]),
        "task_tree": (
            "task_tree",
            [
                "ready_tasks",
                "detect_cycles",
                "aggregate",
                "depth",
                "is_leaf",
                "is_root",
                "set_status",
            ],
        ),
        "distill": ("distillation", ["distill"]),
        "rerank": ("rerank", ["rerank"]),
        "goal_eval": ("goal_eval", ["evaluate"]),
        "auto_converge": ("auto_converge", ["check_convergence"]),
        "subagent_comms": (
            "subagent_comms",
            ["send_message", "broadcast", "inbox", "create_task", "list_tasks"],
        ),
        "version": (
            "versioning",
            ["add", "get", "latest", "parse_rating", "parse_battle", "swap_positions_battle"],
        ),
        "config": (
            "config_stack",
            ["get_value", "set_value", "unset_value", "merge_layers", "permission_check"],
        ),
        "bubble": (
            "bubble_mode",
            ["escalate", "pending", "resolved", "schedule_event", "should_continue", "recent"],
        ),
        "worktree": ("worktree", ["snapshot", "list_worktrees", "diff_snapshots"]),
        "route": ("routing", ["route", "priority", "tools", "route_request"]),
        "session_lock": (
            "session_lock",
            [
                "try_acquire",
                "release",
                "get_state",
                "acquire_with_wait",
                "register_mcp",
                "invoke_mcp",
                "list_mcp",
            ],
        ),
        "flask": ("flask_score", ["score_flask"]),
        "elo": ("elo_ranking", ["record_match", "get_ranked", "submit_workers"]),
        "brainstorm": ("brainstorm", ["ideas", "decide"]),
        "cross_iter": (
            "cross_iter_synth",
            ["analyze_convergence", "best_of_each", "adoption_rate", "step5_review"],
        ),
        "audit": ("action_audit", ["record", "query", "stats"]),
        "audit_cache": ("audit_cache", ["record", "query", "stats"]),
        "in_flight": ("in_flight", ["in_flight", "start", "complete", "transition", "merge"]),
        "mx": ("mx_annot", ["parse", "fanin", "cli"]),
        "tier_promo": ("tier_promo", ["classify", "compute", "can_spawn", "cohabitation"]),
        "artifact": (
            "artifact",
            ["register", "list_by_type", "validate", "add_pane", "layout", "safe_layout"],
        ),
        "frozen": (
            "frozen_zone",
            [
                "is_frozen",
                "add",
                "is_evolvable",
                "can_modify",
                "assert_modifiable",
                "list_sentinels",
            ],
        ),
        "turboquant": ("turboquant", ["should_compress", "apply"]),
        "moa_engine": ("moa_engine", ["run_moa", "validate_moa"]),
        "acceptance": ("acceptance", ["validate_pattern", "add", "parse_ears", "get_tree"]),
        "llm_merge": ("llm_merge", ["merge", "fallback"]),
        "grace": ("grace_window", ["register", "should_block", "status", "warnings"]),
        "rag_search": ("rag_search", ["search"]),
        "plan_act": ("plan_act", ["plan_and_act"]),
        "channels": ("channels", ["ChannelChain", "classify_error"]),
        "reference_router": ("reference_router", ["ReferenceRouter"]),
        "checkpoint": ("checkpoint", ["save", "load", "list_all", "delete"]),
        "canary": ("prompt_canary", ["inject", "check"]),
        "wrap_output": ("output_wrapping", ["wrap", "sanitize", "needs_wrapping", "unwrap"]),
        "fuzzy_dedup": ("fuzzy_dedup", ["add", "check", "simhash"]),
        "input_fingerprint": ("input_fingerprint", ["hash_text", "similar", "store"]),
        "tool_screening": ("tool_screening", ["screen_input"]),
        "anthropic_compat": (
            "anthropic_compat",
            [
                "parse_request",
                "format_sse",
                "format_response",
                "format_tool_use",
                "format_tool_result",
                "format_error",
            ],
        ),
        "token_bucket": ("token_bucket", ["try_consume", "get_state", "cleanup"]),
        "request_dedup": ("request_dedup", ["check", "record", "stats"]),
        "trace": ("trace", ["start", "end", "span", "parse_traceparent", "query"]),
    }
    return caps  # type: ignore[return-value]


# Capabilities gated by require_admin on the real endpoint. Internal loopback
# authenticates with the gateway key (api-key level); these additionally need
# an admin role, which we only satisfy if the dispatch CALLER is admin/operator.
_ADMIN_CAPABILITIES = {"feedback_iter", "worktree", "checkpoint"}

# GET (rather than POST) capability endpoints.
_GET_CAPABILITIES = {"models"}


class CapabilityDispatcher(ServiceBase):
    """Service that exposes ALL 76 /v1/capability/* endpoints as service methods.

    Method naming convention: `call_<endpoint>` — e.g. `call_group_think_check`,
    `call_moa_n_layer`, etc.

    Every method executes the REAL endpoint via an authenticated internal
    loopback HTTP request (audit F9 fix), so behaviour is identical to calling
    the endpoint directly.
    """

    name = "capability"
    description = "All 76 capabilities as a single service. Method: call_<endpoint>"

    def _register_methods(self):
        caps = _build_capability_map()
        for endpoint_name, (_module, _funcs) in caps.items():
            method_name = f"call_{endpoint_name.replace('-', '_')}"
            url_path = "/v1/capability/" + endpoint_name.replace("_", "-")
            http_method = "GET" if endpoint_name in _GET_CAPABILITIES else "POST"
            needs_admin = endpoint_name in _ADMIN_CAPABILITIES
            self._methods[method_name] = ServiceMethod(
                name=method_name,
                description=(
                    f"Execute the real {http_method} /v1/capability/{endpoint_name.replace('_', '-')}"
                    f" endpoint via authenticated internal loopback"
                    + (" [admin only]" if needs_admin else "")
                ),
                func=self._make_caller(endpoint_name, url_path, http_method, needs_admin),
                is_async=True,
                input_required=["body"],
                input_optional=[],
                status="implemented",
            )

    def _make_caller(self, endpoint: str, url_path: str, http_method: str, needs_admin: bool):
        """Return an async function that REALLY executes the endpoint.

        The call loops back over HTTP into this gateway with internal auth,
        executing the endpoint's genuine validation + logic. Failures surface
        the endpoint's true HTTP status and error body — never a fake success.
        """

        async def caller(body):
            body = body if isinstance(body, dict) else {}

            # Admin-gated capabilities: only honour them if the dispatch caller
            # is admin/operator (role threaded via dispatch_ctx, audit F9).
            if needs_admin:
                role = (dispatch_ctx.get() or {}).get("role", "")
                if role not in ("admin", "operator"):
                    raise PermissionError(
                        f"capability '{endpoint}' requires admin/operator role "
                        f"(caller role={role or 'unknown'})"
                    )

            import httpx

            from ..internal_callback import internal_auth_headers, internal_gateway_url

            url = internal_gateway_url() + url_path
            headers = dict(internal_auth_headers())

            # Admin endpoints need a JWT rather than a gateway API key. Mint an
            # internal admin JWT (same-trust-domain loopback, audit F9).
            if needs_admin:
                from ..auth import create_jwt_token

                headers["Authorization"] = f"Bearer {create_jwt_token('dispatcher', 'admin')}"

            async with httpx.AsyncClient(timeout=60.0) as client:
                if http_method == "GET":
                    resp = await client.get(url, headers=headers, params=body)
                else:
                    resp = await client.post(url, headers=headers, json=body)

            if resp.status_code >= 400:
                raise RuntimeError(
                    f"capability '{endpoint}' returned HTTP {resp.status_code}: "
                    f"{resp.text[:300]}"
                )
            try:
                return resp.json()
            except ValueError:
                return {"raw": resp.text}

        return caller
