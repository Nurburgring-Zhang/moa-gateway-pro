"""Distributed tracing — OpenTelemetry-based with lightweight fallback.

Provides:
- OTel TracerProvider setup with OTLP/Console exporters
- Lightweight fallback tracer when OTel SDK not available
- Context propagation via contextvars
- Span creation with attributes and events
"""
from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ============ Context Variables ============
_current_trace_id: ContextVar[str | None] = ContextVar("current_trace_id", default=None)
_current_span_id: ContextVar[str | None] = ContextVar("current_span_id", default=None)


def get_current_trace_id() -> str | None:
    """Get current trace_id from context."""
    return _current_trace_id.get()


def get_current_span_id() -> str | None:
    """Get current span_id from context."""
    return _current_span_id.get()


def set_trace_context(trace_id: str, span_id: str = None):
    """Set trace context."""
    _current_trace_id.set(trace_id)
    if span_id:
        _current_span_id.set(span_id)


def clear_trace_context():
    """Clear trace context."""
    _current_trace_id.set(None)
    _current_span_id.set(None)


# ============ Span Model ============
@dataclass
class SpanRecord:
    """Recorded span data."""
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    start_time: float
    end_time: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    status: str = "OK"

    def set_attribute(self, key: str, value: Any):
        self.attributes[key] = value

    def add_event(self, name: str, attributes: dict = None):
        self.events.append({
            "name": name,
            "timestamp": time.time(),
            "attributes": attributes or {},
        })

    def end(self, status: str = "OK"):
        self.end_time = time.time()
        self.status = status

    @property
    def duration_ms(self) -> float:
        if self.end_time and self.start_time:
            return (self.end_time - self.start_time) * 1000
        return 0.0

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "events": self.events,
            "status": self.status,
        }


# ============ Span Context Manager ============
class SpanContext:
    """Context manager for spans."""

    def __init__(self, tracer: GatewayTracer, name: str, attributes: dict = None):
        self._tracer = tracer
        self._name = name
        self._attributes = attributes or {}
        self._span: SpanRecord | None = None
        self._prev_span_id: str | None = None

    def __enter__(self) -> SpanRecord:
        trace_id = get_current_trace_id() or uuid.uuid4().hex
        parent_span_id = get_current_span_id()
        span_id = uuid.uuid4().hex[:16]

        self._span = SpanRecord(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            name=self._name,
            start_time=time.time(),
            attributes=dict(self._attributes),
        )
        self._prev_span_id = parent_span_id
        _current_trace_id.set(trace_id)
        _current_span_id.set(span_id)
        return self._span

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._span:
            if exc_type:
                self._span.end("ERROR")
                self._span.set_attribute("error.type", exc_type.__name__)
                self._span.set_attribute("error.message", str(exc_val))
            else:
                self._span.end("OK")
            self._tracer._record_span(self._span)
        _current_span_id.set(self._prev_span_id)
        return False


# ============ Gateway Tracer ============
class GatewayTracer:
    """Lightweight tracer compatible with OTel concepts."""

    def __init__(self, service_name: str = "moa-gateway-pro", max_spans: int = 10000):
        self.service_name = service_name
        self._max_spans = max_spans
        self._spans: list[SpanRecord] = []
        self._exporters: list = []

    def start_span(self, name: str, attributes: dict = None) -> SpanContext:
        """Create a new span context manager."""
        return SpanContext(self, name, attributes)

    def create_span(self, name: str, trace_id: str = None, parent_span_id: str = None) -> SpanRecord:
        """Create a span directly (without context manager)."""
        tid = trace_id or get_current_trace_id() or uuid.uuid4().hex
        span = SpanRecord(
            trace_id=tid,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=parent_span_id or get_current_span_id(),
            name=name,
            start_time=time.time(),
        )
        return span

    def _record_span(self, span: SpanRecord):
        """Record a completed span."""
        if len(self._spans) >= self._max_spans:
            self._spans = self._spans[self._max_spans // 2:]
        self._spans.append(span)
        for exporter in self._exporters:
            try:
                exporter.export_span(span)
            except Exception as e:
                logger.warning("Span export failed: %s", e)

    def get_recent_spans(self, limit: int = 100) -> list[dict]:
        """Get recent spans."""
        return [s.to_dict() for s in self._spans[-limit:]]

    def add_exporter(self, exporter):
        """Add a span exporter."""
        self._exporters.append(exporter)


# ============ OTel Integration ============
_otel_tracer = None
_gateway_tracer: GatewayTracer | None = None


def setup_tracer(service_name: str = "moa-gateway-pro", otlp_endpoint: str = None) -> GatewayTracer:
    """Initialize the tracing system."""
    global _otel_tracer, _gateway_tracer  # noqa: PLW0603

    _gateway_tracer = GatewayTracer(service_name=service_name)

    # Try OTel SDK
    try:
        from opentelemetry import trace  # noqa: PLC0415
        from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
        from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # noqa: PLC0415
                    OTLPSpanExporter,
                )
                otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
                provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
                logger.info("OTLP trace exporter configured: %s", otlp_endpoint)
            except Exception as e:
                logger.warning("OTLP exporter setup failed: %s", e)

        trace.set_tracer_provider(provider)
        _otel_tracer = trace.get_tracer(service_name)
        logger.info("OpenTelemetry tracer initialized for service: %s", service_name)
    except ImportError:
        logger.info("OpenTelemetry SDK not available, using lightweight tracer")

    return _gateway_tracer


def get_tracer() -> GatewayTracer:
    """Get the global gateway tracer instance."""
    global _gateway_tracer  # noqa: PLW0603
    if _gateway_tracer is None:
        _gateway_tracer = GatewayTracer()
    return _gateway_tracer
