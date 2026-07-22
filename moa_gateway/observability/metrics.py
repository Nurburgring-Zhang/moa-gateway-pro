"""Enhanced Prometheus metrics — LLM business metrics.

Extends existing prometheus_client usage with LLM-specific metrics:
- Request latency by model/provider
- Token usage tracking
- Cost tracking
- Cache performance
- Provider health
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        REGISTRY,
        Counter,
        Gauge,
        Histogram,
        Info,
        generate_latest,
    )
    _PROM_OK = True
except ImportError:
    _PROM_OK = False

    class _Dummy:
        def labels(self, **kw):
            return self
        def inc(self, n=1):
            pass
        def observe(self, v):
            pass
        def set(self, v):
            pass
        def info(self, d):
            pass

    Counter = Histogram = Gauge = Info = lambda *a, **kw: _Dummy()
    def generate_latest(x): return b""
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"
    REGISTRY = None


# ============ Service Info ============
gateway_info = Info(
    "moa_gateway",
    "MOA Gateway service information",
)

# ============ LLM Request Metrics ============
llm_request_duration_seconds = Histogram(
    "moa_llm_request_duration_seconds",
    "LLM request latency in seconds",
    ["model", "provider", "status"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

llm_requests_total = Counter(
    "moa_llm_requests_total",
    "Total LLM API requests",
    ["model", "provider", "status"],
)

llm_first_token_seconds = Histogram(
    "moa_llm_first_token_seconds",
    "Time to first token (streaming)",
    ["model", "provider"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# ============ Token Metrics ============
llm_tokens_total = Counter(
    "moa_llm_tokens_total",
    "Total tokens processed",
    ["model", "direction"],  # direction: input/output/total
)

llm_tokens_per_request = Histogram(
    "moa_llm_tokens_per_request",
    "Tokens per request",
    ["model", "direction"],
    buckets=(10, 50, 100, 250, 500, 1000, 2000, 4000, 8000, 16000, 32000),
)

# ============ Cost Metrics ============
llm_cost_dollars = Counter(
    "moa_llm_cost_dollars_total",
    "Total cost in USD",
    ["model", "org_id"],
)

# ============ Cache Metrics ============
cache_hits_total = Counter(
    "moa_cache_hits_total",
    "Cache hit count",
    ["layer"],  # exact/semantic/redis
)

cache_misses_total = Counter(
    "moa_cache_misses_total",
    "Cache miss count",
    ["layer"],
)

cache_hit_ratio = Gauge(
    "moa_cache_hit_ratio",
    "Cache hit ratio (0-1)",
    ["layer"],
)

# ============ Connection Metrics ============
active_connections = Gauge(
    "moa_active_connections",
    "Currently active connections",
)

active_streaming_connections = Gauge(
    "moa_active_streaming_connections",
    "Currently active streaming connections",
)

# ============ Provider Health Metrics ============
provider_health = Gauge(
    "moa_provider_health",
    "Provider health status (1=healthy, 0=unhealthy, 0.5=degraded)",
    ["provider", "endpoint_id"],
)

provider_errors_total = Counter(
    "moa_provider_errors_total",
    "Provider errors by type",
    ["provider", "error_type"],
)

provider_circuit_breaker_state = Gauge(
    "moa_provider_circuit_breaker",
    "Circuit breaker state (0=closed, 1=open, 0.5=half-open)",
    ["provider"],
)

# ============ MoA Execution Metrics ============
moa_executions_total = Counter(
    "moa_executions_total",
    "Total MoA (Mixture of Agents) executions",
    ["preset", "status"],
)

moa_execution_duration_seconds = Histogram(
    "moa_execution_duration_seconds",
    "MoA execution duration",
    ["preset"],
    buckets=(1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

# ============ Rate Limiting Metrics ============
rate_limit_blocked_total = Counter(
    "moa_rate_limit_blocked_total",
    "Requests blocked by rate limiting",
    ["reason"],  # rpm/tpm/concurrent
)

# ============ Legacy Compatibility ============
# These maintain backward compat with existing observability.py exports
chat_requests_total = Counter(
    "moa_chat_requests_total",
    "Total chat completion requests (legacy)",
    ["model", "status"],
)

chat_latency_seconds = Histogram(
    "moa_chat_latency_seconds",
    "Chat completion latency (legacy)",
    ["model"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

endpoint_health_gauge = Gauge(
    "moa_endpoint_health",
    "Endpoint health (1=healthy, 0=unhealthy, 0.5=in_breaker)",
    ["endpoint_id"],
)

capability_calls_total = Counter(
    "moa_capability_calls_total",
    "Total capability endpoint calls",
    ["capability", "status"],
)


# ============ Metric Collection ============
def prometheus_response():
    """Generate Prometheus exposition format response."""
    if not _PROM_OK:
        return b"# prometheus_client not installed\n", 200, {"Content-Type": CONTENT_TYPE_LATEST}
    return generate_latest(REGISTRY), 200, {"Content-Type": CONTENT_TYPE_LATEST}


# ============ Helper Functions ============
def record_llm_request(model: str, provider: str, status: str, duration_s: float,
                       input_tokens: int = 0, output_tokens: int = 0,
                       cost_usd: float = 0.0, org_id: str = "default"):
    """Record a complete LLM request with all metrics."""
    status_label = "success" if status == "success" else "error"
    llm_request_duration_seconds.labels(model=model, provider=provider, status=status_label).observe(duration_s)
    llm_requests_total.labels(model=model, provider=provider, status=status_label).inc()

    if input_tokens > 0:
        llm_tokens_total.labels(model=model, direction="input").inc(input_tokens)
        llm_tokens_per_request.labels(model=model, direction="input").observe(input_tokens)
    if output_tokens > 0:
        llm_tokens_total.labels(model=model, direction="output").inc(output_tokens)
        llm_tokens_per_request.labels(model=model, direction="output").observe(output_tokens)

    if cost_usd > 0:
        llm_cost_dollars.labels(model=model, org_id=org_id).inc(cost_usd)

    # Legacy compat
    sl = "2xx" if status_label == "success" else "5xx"
    chat_requests_total.labels(model=model, status=sl).inc()
    chat_latency_seconds.labels(model=model).observe(duration_s)


def record_cache_access(layer: str, hit: bool):
    """Record cache access."""
    if hit:
        cache_hits_total.labels(layer=layer).inc()
    else:
        cache_misses_total.labels(layer=layer).inc()


def record_chat(model: str, status: int, latency_s: float):
    """Legacy: Record a chat completion for Prometheus."""
    status_label = "2xx" if 200 <= status < 300 else ("4xx" if 400 <= status < 500 else "5xx")
    chat_requests_total.labels(model=model, status=status_label).inc()
    chat_latency_seconds.labels(model=model).observe(latency_s)


def record_capability(name: str, status: int):
    """Legacy: Record a capability call."""
    status_label = "ok" if 200 <= status < 300 else "err"
    capability_calls_total.labels(capability=name, status=status_label).inc()


def record_rate_limit_block(reason: str = "rpm"):
    """Record rate limit block."""
    rate_limit_blocked_total.labels(reason=reason).inc()


def record_moa_exec(preset: str):
    """Legacy: Record MoA execution."""
    moa_executions_total.labels(preset=preset, status="ok").inc()
