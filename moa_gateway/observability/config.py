"""Observability configuration."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OTelConfig:
    """OpenTelemetry configuration."""
    service_name: str = "moa-gateway-pro"
    service_version: str = "1.8.1"
    environment: str = "development"

    # Tracing
    trace_enabled: bool = True
    trace_sample_rate: float = 1.0  # 1.0 = sample everything
    otlp_endpoint: str | None = None  # e.g. "http://localhost:4317"
    otlp_protocol: str = "grpc"  # "grpc" or "http/protobuf"

    # Metrics
    metrics_enabled: bool = True
    metrics_port: int = 9090
    prometheus_enabled: bool = True

    # Logging
    log_json: bool = False
    log_level: str = "INFO"
    log_correlation: bool = True  # inject trace_id into logs

    # Export
    console_export: bool = False  # for dev debugging


def get_otel_config() -> OTelConfig:
    """Build OTelConfig from environment variables."""
    import os  # noqa: PLC0415
    return OTelConfig(
        service_name=os.getenv("OTEL_SERVICE_NAME", "moa-gateway-pro"),
        service_version=os.getenv("MOA_VERSION", "1.8.1"),
        environment=os.getenv("MOA_ENV", "development"),
        trace_enabled=os.getenv("OTEL_TRACE_ENABLED", "true").lower() == "true",
        trace_sample_rate=float(os.getenv("OTEL_TRACE_SAMPLE_RATE", "1.0")),
        otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
        otlp_protocol=os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc"),
        metrics_enabled=os.getenv("OTEL_METRICS_ENABLED", "true").lower() == "true",
        prometheus_enabled=os.getenv("OTEL_PROMETHEUS_ENABLED", "true").lower() == "true",
        log_json=os.getenv("MOA_LOG_JSON", "false").lower() == "true",
        log_level=os.getenv("MOA_LOG_LEVEL", "INFO"),
        log_correlation=os.getenv("OTEL_LOG_CORRELATION", "true").lower() == "true",
        console_export=os.getenv("OTEL_CONSOLE_EXPORT", "false").lower() == "true",
    )
