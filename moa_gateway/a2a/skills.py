"""moa_gateway.a2a.skills — the five A2A skills (real internal calls only).

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT license):
  - source: src/lib/a2a/skills/{smartRouting,providerDiscovery,healthReport,
    quotaManagement,costAnalysis}.ts and src/lib/a2a/taskExecution.ts
    (A2A_SKILL_HANDLERS registry + executeA2ATaskWithState lifecycle).

OmniRoute ships 5 A2A skills that call the OmniRoute pipeline; this port keeps
the same skill semantics but each skill calls THIS gateway's real internals
(in-process, no self-HTTP):

  OmniRoute skill         ->  moa-gateway-pro skill      -> real call target
  smart-routing           ->  chat-completion            ->  IntelligentRouter + ModelPool.call (chat pipeline)
  provider-discovery      ->  model-list                 ->  ModelPool runtime endpoints + snapshot
  health-report           ->  health                     ->  ha.health_checker + health.HealthChecker + pool snapshot
  quota-management        ->  cache-insight              ->  CacheManager.get_stats + layer sizes
  cost-analysis           ->  routing-advice             ->  IntelligentRouter decision + lazy routing_strategies engine

PII / credential discipline (OmniRoute hard rule #20 + error-sanitization):
  PII redaction of USER PAYLOADS stays opt-in and is never applied silently to
  proxy traffic here. The complementary hard rule for the A2A surface is that
  OUTBOUND A2A artifacts/metadata must never carry gateway credentials: every
  skill result passes through ``sanitize_outbound()`` which strips secret-like
  keys (api_key*/password/token/authorization, Redis URL userinfo) before the
  payload is serialized into a task artifact or JSON-RPC response.
"""

from __future__ import annotations

import importlib
import logging
import re
import time
from typing import Any, Awaitable, Callable

from .task_manager import A2ATask

logger = logging.getLogger(__name__)


class SkillExecutionError(Exception):
    """Domain-level skill failure (model not found, no capacity, ...)."""


SkillResult = dict[str, Any]  # {"artifacts": [...], "metadata": {...}}
SkillHandler = Callable[[A2ATask], Awaitable[SkillResult]]

# ============ Outbound credential scrubbing (PII hard rule) ============

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|secret|password|passwd|token|credential|authorization)",
    re.IGNORECASE,
)

# Telemetry keys that merely CONTAIN a secret-like substring ("...tokens") but
# are plain usage counters — never redact real telemetry data. Every other
# secret-like key stays redacted (zero-leak discipline is unchanged).
_NON_SECRET_KEYS = {"prompt_tokens", "completion_tokens", "total_tokens"}


def sanitize_outbound(value: Any) -> Any:
    """Recursively strip secret-like fields from an outbound A2A payload.

    This never mutates user request bodies in transit (OmniRoute opt-in PII
    discipline); it only guarantees the gateway's own credentials cannot ride
    along in skill-produced artifacts/metadata/error text.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in _NON_SECRET_KEYS:
                out[k] = sanitize_outbound(v)
            elif isinstance(k, str) and _SECRET_KEY_RE.search(k):
                out[k] = "[REDACTED]"
            else:
                out[k] = sanitize_outbound(v)
        return out
    if isinstance(value, list):
        return [sanitize_outbound(v) for v in value]
    return value


def scrub_url_credentials(url: str) -> str:
    """Remove ``user:pass@`` userinfo from a URL (e.g. redis://u:p@host)."""
    if not isinstance(url, str) or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest.split("/", 1)[0]:
        hostpart = rest.split("/", 1)
        host = hostpart[0].rsplit("@", 1)[1]
        tail = "/" + hostpart[1] if len(hostpart) > 1 else ""
        return f"{scheme}://[REDACTED]@{host}{tail}"
    return url


def _last_user_text(task: A2ATask) -> str:
    """Last message content of the task (query for routing/chat skills)."""
    messages = task.input.get("messages") or []
    for m in reversed(messages):
        content = m.get("content") if isinstance(m, dict) else None
        if isinstance(content, str) and content.strip():
            return content
    return ""


def _all_message_dicts(task: A2ATask) -> list[dict[str, Any]]:
    return [
        {"role": m.get("role", "user"), "content": m.get("content", "")}
        for m in (task.input.get("messages") or [])
        if isinstance(m, dict) and isinstance(m.get("content"), str)
    ]


# ============ Skill 1: chat-completion (real chat pipeline) ============


async def execute_chat_completion(task: A2ATask) -> SkillResult:
    """Run the task's messages through the gateway's real chat pipeline.

    Same internal path as POST /v1/chat/completions (routes/chat.py):
    cache lookup -> IntelligentRouter model selection (model="auto") ->
    ModelPool.call() -> cache store. No provider traffic is faked here; an
    endpoint without a real key is served by the gateway's explicit
    MockProvider (D6 policy) and labeled provider="mock".
    """
    from .._helpers import format_chat_response
    from ..cache.manager import get_cache_manager
    from ..model_pool import get_model_pool
    from ..router import get_router

    meta = task.input.get("metadata") or {}
    model_id = str(meta.get("model") or "auto").strip() or "auto"
    temperature = float(meta.get("temperature", 0.6))
    max_tokens = int(meta.get("max_tokens", 4096))
    messages = _all_message_dicts(task)
    if not messages:
        raise SkillExecutionError("chat-completion requires at least one message")

    pool = get_model_pool()
    routing_info: dict[str, Any] | None = None
    if model_id == "auto":
        decision = get_router().route(messages[-1].get("content", ""), allow_tier_fallback=True)
        routing_info = decision.to_dict()
        if not decision.primary:
            raise SkillExecutionError("no available model")
        model_id = decision.primary.id
    elif model_id not in pool.endpoints:
        raise SkillExecutionError(f"model '{model_id}' not found")

    cache_mgr = get_cache_manager()
    cache_layer: str | None = None
    if cache_mgr.enabled:
        cached = await cache_mgr.get(
            messages, model_id, temperature=temperature, max_tokens=max_tokens
        )
        if cached:
            cache_layer = cached["layer"]
            body = cached["response"]
            content = ""
            if isinstance(body, dict):
                try:
                    content = body["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError):
                    content = str(body.get("content", ""))
            return {
                "artifacts": [{"type": "text", "content": content}],
                "metadata": {
                    "model": model_id,
                    "cached": True,
                    "cache_layer": cache_layer,
                    "routing": routing_info,
                },
            }

    t0 = time.time()
    try:
        resp = await pool.call(
            model_id,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            max_retries=3,
        )
    except Exception as e:  # ProviderError & co — surfaced as a real failure
        raise SkillExecutionError(f"model call failed: {e}") from e
    latency_ms = (time.time() - t0) * 1000

    request_id = "a2a-" + task.id[:12]
    body = format_chat_response(
        request_id, model_id, resp.content, resp.prompt_tokens, resp.completion_tokens
    )
    if cache_mgr.enabled:
        await cache_mgr.set(
            messages,
            model_id,
            body,
            temperature=temperature,
            max_tokens=max_tokens,
            mock=(resp.provider == "mock"),
        )
    return {
        "artifacts": [{"type": "text", "content": resp.content}],
        "metadata": {
            "model": model_id,
            "provider": resp.provider,
            "finish_reason": resp.finish_reason,
            "prompt_tokens": resp.prompt_tokens,
            "completion_tokens": resp.completion_tokens,
            "cost": round(resp.cost, 6),
            "latency_ms": round(latency_ms, 2),
            "cached": False,
            "routing": routing_info,
        },
    }


# ============ Skill 2: model-list (real runtime model pool) ============


async def execute_model_list(task: A2ATask) -> SkillResult:
    """List the gateway's real runtime model pool (ModelPool endpoints)."""
    from ..model_pool import get_model_pool

    pool = get_model_pool()
    snapshot = pool.snapshot()
    endpoints: list[dict[str, Any]] = []
    for ep in pool.endpoints.values():
        endpoints.append(
            {
                "id": ep.id,
                "provider": ep.config.provider,
                "model": ep.config.model,
                "tier": ep.config.tier,
                "enabled": ep.config.enabled,
                "health_status": ep.health_status,
                "available": ep.is_available,
                "total_calls": ep.total_calls,
                "total_failures": ep.total_failures,
            }
        )
    endpoints.sort(key=lambda e: e["id"])
    presets = ["auto", "fast", "balanced", "quality", "pipeline"]
    text_lines = [
        f"Model pool: {snapshot['total']} endpoint(s), "
        f"{snapshot['enabled']} enabled, {snapshot['healthy']} healthy.",
        "Presets (MoA aliases): " + ", ".join(presets),
        "Endpoints:",
    ]
    text_lines += [
        f"- {e['id']} ({e['provider']}/{e['model']}, tier={e['tier']}, "
        f"enabled={e['enabled']}, health={e['health_status']})"
        for e in endpoints
    ]
    return {
        "artifacts": [{"type": "text", "content": "\n".join(text_lines)}],
        "metadata": {
            "snapshot": snapshot,
            "endpoints": endpoints,
            "presets": presets,
        },
    }


# ============ Skill 3: health (real health modules) ============


async def execute_health(task: A2ATask) -> SkillResult:
    """Structured gateway health report (OmniRoute health-report port).

    Real sources: ha.health_checker (liveness/readiness probes),
    health.HealthChecker (per-endpoint probe state) and ModelPool.snapshot().
    """
    from ..ha import health_checker as ha_checker
    from ..health import get_health_checker
    from ..model_pool import get_model_pool

    pool = get_model_pool()
    snapshot = pool.snapshot()
    liveness = await ha_checker.liveness()
    readiness = await ha_checker.readiness()
    endpoint_health = get_health_checker().get_summary()

    degraded = [
        e.id
        for e in pool.endpoints.values()
        if e.health_status in ("unhealthy", "dead")
    ]
    readiness_status = str(readiness.get("status", "unknown"))
    if snapshot["total"] == 0 or readiness_status == "unhealthy":
        status = "unhealthy"
    elif degraded or readiness_status == "degraded":
        status = "degraded"
    elif readiness_status == "healthy":
        status = "healthy"
    else:
        # not_ready / starting / unknown are genuine runtime states — the
        # report must reflect reality, never a prettied-up constant.
        status = readiness_status

    lines = [
        f"Health report: {status}",
        f"Endpoints healthy: {snapshot['healthy']}/{snapshot['total']} "
        f"(enabled={snapshot['enabled']}, in_breaker={snapshot['in_breaker']})",
        f"Readiness: {readiness.get('status', 'unknown')}",
        f"Uptime: {liveness.get('uptime_seconds', 0)}s (pid {liveness.get('pid')})",
        f"Tracked endpoint probes: {endpoint_health.get('total_tracked', 0)}",
    ]
    if degraded:
        lines.append("Degraded endpoints: " + ", ".join(sorted(degraded)))
    else:
        lines.append("No degraded endpoints.")
    return {
        "artifacts": [{"type": "text", "content": "\n".join(lines)}],
        "metadata": {
            "status": status,
            "liveness": liveness,
            "readiness": readiness,
            "pool_snapshot": snapshot,
            "endpoint_health": endpoint_health,
            "degraded_endpoints": sorted(degraded),
        },
    }


# ============ Skill 4: routing-advice (real routing engine) ============

_ROUTING_STRATEGIES_MODULE = "moa_gateway.routing_strategies"


def _introspect_strategy_engine(mod: Any) -> dict[str, Any]:
    """Extract the real strategy catalog from the routing_strategies package.

    The M1 engine (parallel delivery) exposes a strategy registry; try the
    documented entry points in order. On success this returns REAL engine
    data; anything unparseable degrades to a structured status instead of
    invented data.
    """
    info: dict[str, Any] = {"module": _ROUTING_STRATEGIES_MODULE, "status": "ready"}
    registry = None
    for attr in ("get_registry", "registry", "list_strategies"):
        candidate = getattr(mod, attr, None)
        if candidate is None:
            continue
        try:
            registry = candidate() if callable(candidate) else candidate
        except Exception as e:  # real error surfaced, never faked
            info["status"] = "invocation_failed"
            info["detail"] = f"{attr}(): {e}"
            return info
        if registry:
            break
    if registry is None:
        for attr in ("REGISTRY", "STRATEGIES", "STRATEGY_REGISTRY"):
            candidate = getattr(mod, attr, None)
            if candidate:
                registry = candidate
                break
    if registry is None:
        info["status"] = "api_not_recognized"
        info["detail"] = "no registry entry point found on the module"
        return info
    try:
        if isinstance(registry, dict):
            names = sorted(registry.keys())
        else:
            names = sorted(getattr(s, "name", str(s)) for s in registry)
        info["strategies"] = [str(n) for n in names]
        info["count"] = len(names)
    except Exception as e:
        info["status"] = "invocation_failed"
        info["detail"] = f"registry parse: {e}"
    return info


async def execute_routing_advice(task: A2ATask) -> SkillResult:
    """Real routing advice: IntelligentRouter decision + strategy engine.

    The IntelligentRouter call is always real. The OmniRoute-style strategy
    engine lives in ``moa_gateway.routing_strategies`` (M1, parallel
    delivery): it is imported lazily via importlib, and when the package is
    not importable yet the skill returns a structured ``module_not_ready``
    status — never fabricated strategy data.
    """
    from ..config import get_settings
    from ..router import get_router

    query = _last_user_text(task)
    messages = _all_message_dicts(task)
    context = messages[:-1] if len(messages) > 1 else None

    router_inst = get_router()
    decision = router_inst.route(query, context=context, allow_tier_fallback=True)
    complexity = router_inst.evaluate_complexity(query, context)
    advice = decision.to_dict()
    advice["recommendation"] = (
        f"route to '{decision.primary.id}'" if decision.primary else "no available model"
    )

    meta = task.input.get("metadata") or {}
    requested_strategy = meta.get("strategy") or get_settings().routing_strategies.default_strategy

    try:
        mod = importlib.import_module(_ROUTING_STRATEGIES_MODULE)
    except ImportError as e:
        engine_info: dict[str, Any] = {
            "module": _ROUTING_STRATEGIES_MODULE,
            "status": "module_not_ready",
            "detail": str(e) or "module not importable",
            "requested_strategy": requested_strategy,
        }
        logger.info("routing-advice: %s not ready (%s)", _ROUTING_STRATEGIES_MODULE, e)
    else:
        engine_info = _introspect_strategy_engine(mod)
        engine_info["requested_strategy"] = requested_strategy
        # If the engine exposes an endpoint ranker, run it for real on the
        # current pool (best-effort: any mismatch is reported, never faked).
        ranker = None
        for attr in ("rank_endpoints", "score_endpoints", "apply_strategy"):
            cand = getattr(mod, attr, None)
            if callable(cand):
                ranker = (attr, cand)
                break
        if ranker is not None:
            attr_name, fn = ranker
            try:
                from ..model_pool import get_model_pool

                ranked = fn(requested_strategy, get_model_pool().endpoints)
                engine_info["ranker"] = attr_name
                engine_info["ranked"] = sanitize_outbound(ranked)
            except Exception as e:
                engine_info["ranker"] = attr_name
                engine_info["ranker_error"] = str(e)

    return {
        "artifacts": [
            {
                "type": "text",
                "content": (
                    f"Routing advice: complexity={complexity.value}, "
                    f"tier={decision.tier.value}, "
                    f"primary={decision.primary.id if decision.primary else 'none'}, "
                    f"estimated_cost=${advice['estimated_cost']}. {advice['recommendation']}."
                ),
            }
        ],
        "metadata": {
            "router_decision": advice,
            "complexity": complexity.value,
            "strategy_engine": engine_info,
        },
    }


# ============ Skill 5: cache-insight (real cache manager stats) ============


async def execute_cache_insight(task: A2ATask) -> SkillResult:
    """Real cache telemetry via CacheManager.get_stats() + layer sizes."""
    from ..cache.manager import get_cache_manager

    mgr = get_cache_manager()
    stats = mgr.get_stats()
    config = mgr.get_config()
    l1_size = await mgr.l1.size()
    l2_size = await mgr.l2.size()
    l3_available = bool(getattr(mgr.l3, "is_available", False))
    redis_url = config.get("redis_url") or ""

    layers = {
        "l1_exact": {"entries": l1_size, "max_size": config.get("exact_max_size")},
        "l2_semantic": {
            "entries": l2_size,
            "max_size": config.get("semantic_max_size"),
            "similarity_threshold": config.get("similarity_threshold"),
        },
        "l3_redis": {"available": l3_available, "url": scrub_url_credentials(redis_url)},
    }
    lines = [
        f"Cache insight: enabled={mgr.enabled}, "
        f"hit_rate={stats.get('hit_rate_pct', 0)}% "
        f"({stats.get('total_hits', 0)} hits / {stats.get('total_misses', 0)} misses "
        f"of {stats.get('total_requests', 0)} lookups).",
        f"Avg lookup latency: {stats.get('avg_lookup_latency_ms', 0)}ms; "
        f"uptime {stats.get('uptime_seconds', 0)}s.",
        f"Layers: L1 exact={l1_size} entries, L2 semantic={l2_size} entries, "
        f"L3 redis={'up' if l3_available else 'down/unconfigured'}.",
        "Hits by layer: "
        + (
            ", ".join(f"{k}={v}" for k, v in sorted(stats.get("hits_by_layer", {}).items()))
            or "none"
        ),
    ]
    return {
        "artifacts": [{"type": "text", "content": "\n".join(lines)}],
        "metadata": {
            "enabled": mgr.enabled,
            "stats": stats,
            "layers": layers,
        },
    }


# ============ Registry (OmniRoute A2A_SKILL_HANDLERS port) ============


class SkillSpec:
    """Skill definition: card metadata + real handler."""

    def __init__(
        self,
        skill_id: str,
        name: str,
        description: str,
        tags: list[str],
        examples: list[str],
        handler: SkillHandler,
    ):
        self.id = skill_id
        self.name = name
        self.description = description
        self.tags = tags
        self.examples = examples
        self.handler = handler

    def to_card(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "examples": list(self.examples),
        }


SKILL_REGISTRY: dict[str, SkillSpec] = {}


def _register(spec: SkillSpec) -> None:
    SKILL_REGISTRY[spec.id] = spec


_register(
    SkillSpec(
        "chat-completion",
        "Chat Completion",
        "Run messages through the gateway's real chat pipeline: complexity "
        "routing (IntelligentRouter) or an explicitly named endpoint, "
        "ModelPool.call with fallback chain, response cache included.",
        ["chat", "llm", "routing", "completion"],
        [
            "Answer this question using the best available model",
            "Run this prompt on endpoint gpt-4o-mini",
        ],
        execute_chat_completion,
    )
)
_register(
    SkillSpec(
        "model-list",
        "Model Pool Discovery",
        "Discover the gateway's real runtime model pool: every configured "
        "endpoint with provider, model, tier, health and availability, plus "
        "MoA preset aliases.",
        ["models", "discovery", "providers", "health"],
        ["Which models are available?", "List healthy endpoints"],
        execute_model_list,
    )
)
_register(
    SkillSpec(
        "health",
        "Health Report",
        "Summarize gateway health for orchestration: liveness/readiness "
        "probes, model-pool snapshot, per-endpoint probe statistics and "
        "degraded endpoint list.",
        ["health", "monitoring", "resilience", "telemetry"],
        ["Is everything healthy?", "Report degraded endpoints"],
        execute_health,
    )
)
_register(
    SkillSpec(
        "routing-advice",
        "Routing Advice",
        "Real routing advice from the gateway's routing engine: complexity "
        "assessment, tier/endpoint selection with cost estimate, and the "
        "OmniRoute-style strategy engine catalog when available.",
        ["routing", "optimization", "strategy", "cost"],
        [
            "Which endpoint should handle this coding task?",
            "Advise a model for a complex architecture question",
        ],
        execute_routing_advice,
    )
)
_register(
    SkillSpec(
        "cache-insight",
        "Cache Insight",
        "Real response-cache telemetry: hit/miss counters, hit rate, "
        "per-layer hit distribution, lookup latency and live layer sizes "
        "(L1 exact, L2 semantic, L3 Redis).",
        ["cache", "telemetry", "monitoring", "performance"],
        ["What is the cache hit rate?", "How many entries in each cache layer?"],
        execute_cache_insight,
    )
)
