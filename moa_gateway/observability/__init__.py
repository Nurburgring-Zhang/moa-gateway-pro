"""moa_gateway.observability — Full observability package.

Three pillars:
1. Traces — Distributed request tracing with span propagation
2. Metrics — Prometheus-format LLM business metrics
3. Logs — Structured JSON logging with trace correlation

Maintains full backward compatibility with the original observability.py exports.
"""
from __future__ import annotations

# ============ Legacy In-Memory Metrics ============
from ._legacy import JsonFormatter, Metrics

# ============ Config ============
from .config import OTelConfig, get_otel_config

# ============ Exporters ============
from .exporters import ConsoleSpanExporter, setup_exporters

# ============ Metrics (Prometheus) ============
from .metrics import (  # noqa: F401
    active_connections,
    active_streaming_connections,
    cache_hit_ratio,
    cache_hits_total,
    cache_misses_total,
    capability_calls_total,
    chat_latency_seconds,
    chat_requests_total,
    endpoint_health_gauge,
    llm_cost_dollars,
    llm_first_token_seconds,
    llm_request_duration_seconds,
    llm_requests_total,
    llm_tokens_per_request,
    llm_tokens_total,
    moa_execution_duration_seconds,
    moa_executions_total,
    prometheus_response,
    provider_circuit_breaker_state,
    provider_errors_total,
    provider_health,
    rate_limit_blocked_total,
    record_cache_access,
    record_capability,
    record_chat,
    record_llm_request,
    record_moa_exec,
    record_rate_limit_block,
)
from .metrics_collector import MetricsCollector  # noqa: F401

# ============ Middleware ============
from .middleware import ObservabilityMiddleware

# ============ Structured Logging (with trace correlation) ============
from .structured_logging import (
    StructuredJsonFormatter,
    TraceCorrelationFilter,
    setup_logging,
)

# ============ Test Reports (P1-6) ============
from .test_report import (  # noqa: F401
    ExecutionTrace,
    TestReport,
    TestReportGenerator,
    get_report_generator,
    init_report_generator,
)

# ============ Tracer ============
from .tracer import (
    GatewayTracer,
    SpanContext,
    SpanRecord,
    clear_trace_context,
    get_current_span_id,
    get_current_trace_id,
    get_tracer,
    set_trace_context,
    setup_tracer,
)


# ============ Package-level initialization ============
def init_observability(
    service_name: str = "moa-gateway-pro",
    log_level: str = "INFO",
    log_dir: str = "data/logs",
    log_json: bool = False,
    otlp_endpoint: str = None,  # type: ignore[assignment]
    console_export: bool = False,
):
    """Initialize the full observability stack.

    Call this once at application startup.
    """
    # Setup logging with trace correlation
    setup_logging(level=log_level, log_dir=log_dir, json_mode=log_json)

    # Setup tracer
    setup_tracer(service_name=service_name, otlp_endpoint=otlp_endpoint)

    # Setup exporters
    setup_exporters(console=console_export, otlp_endpoint=otlp_endpoint)


__all__ = [
    # Logging
    "setup_logging",
    "StructuredJsonFormatter",
    "TraceCorrelationFilter",
    # Tracer
    "GatewayTracer",
    "SpanRecord",
    "SpanContext",
    "get_current_trace_id",
    "get_current_span_id",
    "set_trace_context",
    "clear_trace_context",
    "setup_tracer",
    "get_tracer",
    # Metrics
    "llm_request_duration_seconds",
    "llm_requests_total",
    "llm_first_token_seconds",
    "llm_tokens_total",
    "llm_tokens_per_request",
    "llm_cost_dollars",
    "cache_hits_total",
    "cache_misses_total",
    "cache_hit_ratio",
    "active_connections",
    "active_streaming_connections",
    "provider_health",
    "provider_errors_total",
    "provider_circuit_breaker_state",
    "moa_executions_total",
    "moa_execution_duration_seconds",
    "rate_limit_blocked_total",
    # Legacy metrics
    "endpoint_health_gauge",
    "chat_requests_total",
    "chat_latency_seconds",
    "capability_calls_total",
    # Metric functions
    "prometheus_response",
    "record_llm_request",
    "record_cache_access",
    "record_chat",
    "record_capability",
    "record_rate_limit_block",
    "record_moa_exec",
    # Legacy
    "Metrics",
    "JsonFormatter",
    # Middleware
    "ObservabilityMiddleware",
    # Config
    "OTelConfig",
    "get_otel_config",
    # Exporters
    "setup_exporters",
    "ConsoleSpanExporter",
    # Init
    "init_observability",
    # Test Reports (P1-6)
    "ExecutionTrace",
    "TestReport",
    "TestReportGenerator",
    "MetricsCollector",
    "get_report_generator",
    "init_report_generator",
]
