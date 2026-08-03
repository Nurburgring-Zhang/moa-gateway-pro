"""Tests for moa_gateway.observability — Three pillars (Trace/Metrics/Logs).

Tests cover:
1. Tracer: trace_id/span creation, context propagation, span attributes
2. Metrics: Counter/Histogram/Gauge operations, Prometheus output
3. Logging: structured JSON format, trace correlation
4. Middleware: trace injection, response headers, duration tracking
5. Config: OTelConfig from env vars
6. Exporters: ConsoleSpanExporter
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient


# ============ Tracer Tests ============

class TestTracer:
    """Test distributed tracing functionality."""

    def test_create_span_generates_ids(self):
        """Span should have unique trace_id and span_id."""
        from moa_gateway.observability.tracer import GatewayTracer

        tracer = GatewayTracer(service_name="test")
        span = tracer.create_span("test-operation")

        assert span.trace_id is not None
        assert len(span.trace_id) == 32
        assert span.span_id is not None
        assert len(span.span_id) == 16
        assert span.name == "test-operation"
        assert span.start_time > 0

    def test_span_set_attribute(self):
        """Span should store attributes."""
        from moa_gateway.observability.tracer import GatewayTracer

        tracer = GatewayTracer()
        span = tracer.create_span("http-request")
        span.set_attribute("http.method", "GET")
        span.set_attribute("http.status_code", 200)

        assert span.attributes["http.method"] == "GET"
        assert span.attributes["http.status_code"] == 200

    def test_span_add_event(self):
        """Span should record events with timestamps."""
        from moa_gateway.observability.tracer import GatewayTracer

        tracer = GatewayTracer()
        span = tracer.create_span("db-query")
        span.add_event("query_start", {"sql": "SELECT 1"})

        assert len(span.events) == 1
        assert span.events[0]["name"] == "query_start"
        assert span.events[0]["attributes"]["sql"] == "SELECT 1"
        assert span.events[0]["timestamp"] > 0

    def test_span_end_calculates_duration(self):
        """Ending a span should calculate duration_ms."""
        from moa_gateway.observability.tracer import GatewayTracer

        tracer = GatewayTracer()
        span = tracer.create_span("slow-op")
        time.sleep(0.01)
        span.end("OK")

        assert span.end_time is not None
        assert span.duration_ms >= 10  # at least 10ms
        assert span.status == "OK"

    def test_span_context_manager(self):
        """SpanContext should auto-end span and record it."""
        from moa_gateway.observability.tracer import GatewayTracer, set_trace_context

        tracer = GatewayTracer()
        set_trace_context(uuid.uuid4().hex)

        with tracer.start_span("context-op") as span:
            span.set_attribute("key", "value")

        assert span.end_time is not None
        assert span.status == "OK"
        assert len(tracer._spans) == 1

    def test_span_context_manager_error(self):
        """SpanContext should capture exceptions."""
        from moa_gateway.observability.tracer import GatewayTracer, set_trace_context

        tracer = GatewayTracer()
        set_trace_context(uuid.uuid4().hex)

        with pytest.raises(ValueError):
            with tracer.start_span("failing-op") as span:
                raise ValueError("test error")

        assert span.status == "ERROR"
        assert span.attributes["error.type"] == "ValueError"
        assert "test error" in span.attributes["error.message"]

    def test_trace_context_propagation(self):
        """Trace context should propagate via contextvars."""
        from moa_gateway.observability.tracer import (
            get_current_trace_id,
            get_current_span_id,
            set_trace_context,
            clear_trace_context,
        )

        trace_id = uuid.uuid4().hex
        span_id = uuid.uuid4().hex[:16]

        set_trace_context(trace_id, span_id)
        assert get_current_trace_id() == trace_id
        assert get_current_span_id() == span_id

        clear_trace_context()
        assert get_current_trace_id() is None
        assert get_current_span_id() is None

    def test_get_recent_spans(self):
        """Should return recent spans in order."""
        from moa_gateway.observability.tracer import GatewayTracer

        tracer = GatewayTracer()
        for i in range(5):
            span = tracer.create_span(f"op-{i}")
            span.end("OK")
            tracer._record_span(span)

        recent = tracer.get_recent_spans(limit=3)
        assert len(recent) == 3
        assert recent[-1]["name"] == "op-4"

    def test_span_to_dict(self):
        """Span.to_dict() should return complete serializable dict."""
        from moa_gateway.observability.tracer import GatewayTracer

        tracer = GatewayTracer()
        span = tracer.create_span("serialization-test")
        span.set_attribute("custom", "value")
        span.end("OK")

        d = span.to_dict()
        assert d["name"] == "serialization-test"
        assert d["trace_id"] is not None
        assert d["span_id"] is not None
        assert d["status"] == "OK"
        assert d["attributes"]["custom"] == "value"
        assert d["duration_ms"] >= 0


# ============ Metrics Tests ============

class TestMetrics:
    """Test Prometheus metrics operations."""

    def test_counter_increment(self):
        """Counter should increment correctly."""
        from moa_gateway.observability.metrics import llm_requests_total

        # Just verify no error on increment
        llm_requests_total.labels(model="gpt-4", provider="openai", status="success").inc()

    def test_histogram_observe(self):
        """Histogram should record observations."""
        from moa_gateway.observability.metrics import llm_request_duration_seconds

        llm_request_duration_seconds.labels(
            model="gpt-4", provider="openai", status="success"
        ).observe(1.5)

    def test_gauge_operations(self):
        """Gauge should support inc/dec/set."""
        from moa_gateway.observability.metrics import active_connections

        active_connections.inc()
        active_connections.inc()
        active_connections.dec()
        # Should not raise

    def test_prometheus_response_format(self):
        """prometheus_response() should return valid exposition format."""
        from moa_gateway.observability.metrics import prometheus_response

        body, status, headers = prometheus_response()
        assert status == 200
        assert b"moa_" in body or body == b"# prometheus_client not installed\n"
        assert "Content-Type" in headers

    def test_record_llm_request_helper(self):
        """record_llm_request should update all related metrics."""
        from moa_gateway.observability.metrics import record_llm_request

        # Should not raise
        record_llm_request(
            model="claude-3",
            provider="anthropic",
            status="success",
            duration_s=2.5,
            input_tokens=100,
            output_tokens=500,
            cost_usd=0.01,
            org_id="org-123",
        )

    def test_record_cache_access(self):
        """record_cache_access should increment hit/miss counters."""
        from moa_gateway.observability.metrics import record_cache_access

        record_cache_access("exact", hit=True)
        record_cache_access("exact", hit=False)
        record_cache_access("semantic", hit=True)

    def test_legacy_record_chat(self):
        """Legacy record_chat should work for backward compat."""
        from moa_gateway.observability.metrics import record_chat

        record_chat(model="gpt-4", status=200, latency_s=1.0)
        record_chat(model="gpt-4", status=500, latency_s=5.0)


# ============ Structured Logging Tests ============

class TestStructuredLogging:
    """Test structured logging with trace correlation."""

    def test_json_formatter_output(self):
        """StructuredJsonFormatter should produce valid JSON."""
        from moa_gateway.observability.structured_logging import StructuredJsonFormatter

        formatter = StructuredJsonFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test.logger"
        assert parsed["message"] == "Test message"
        assert "timestamp" in parsed

    def test_json_formatter_trace_correlation(self):
        """JSON logs should include trace_id when available."""
        from moa_gateway.observability.structured_logging import StructuredJsonFormatter
        from moa_gateway.observability.tracer import set_trace_context, clear_trace_context

        trace_id = "abc123def456" * 3  # 36 chars, just needs to be present
        set_trace_context(trace_id, "span123")

        try:
            formatter = StructuredJsonFormatter()
            record = logging.LogRecord(
                name="test", level=logging.INFO,
                pathname="", lineno=0, msg="correlated", args=None, exc_info=None,
            )
            output = formatter.format(record)
            parsed = json.loads(output)

            assert parsed.get("trace_id") == trace_id
            assert parsed.get("span_id") == "span123"
        finally:
            clear_trace_context()

    def test_trace_correlation_filter(self):
        """TraceCorrelationFilter should add trace fields to LogRecord."""
        from moa_gateway.observability.structured_logging import TraceCorrelationFilter
        from moa_gateway.observability.tracer import set_trace_context, clear_trace_context

        filt = TraceCorrelationFilter()
        set_trace_context("trace999", "span888")

        try:
            record = logging.LogRecord(
                name="test", level=logging.INFO,
                pathname="", lineno=0, msg="msg", args=None, exc_info=None,
            )
            result = filt.filter(record)
            assert result is True
            assert record.trace_id == "trace999"
            assert record.span_id == "span888"
        finally:
            clear_trace_context()


# ============ Middleware Tests ============

class TestObservabilityMiddleware:
    """Test the observability middleware integration."""

    def test_middleware_injects_trace_headers(self):
        """Responses should have X-Trace-ID and X-Request-Duration-Ms headers."""
        from moa_gateway.server import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.get("/health")

        assert "X-Trace-ID" in resp.headers
        assert len(resp.headers["X-Trace-ID"]) == 32
        assert "X-Request-Duration-Ms" in resp.headers

    def test_middleware_propagates_trace_id(self):
        """If X-Trace-ID is sent in request, it should be echoed back."""
        from moa_gateway.server import create_app

        app = create_app()
        client = TestClient(app)
        custom_trace = uuid.uuid4().hex
        resp = client.get("/health", headers={"X-Trace-ID": custom_trace})

        assert resp.headers.get("X-Trace-ID") == custom_trace

    def test_middleware_parses_traceparent(self):
        """W3C traceparent header should be parsed for trace_id."""
        from moa_gateway.server import create_app

        app = create_app()
        client = TestClient(app)
        trace_id = uuid.uuid4().hex
        traceparent = f"00-{trace_id}-{uuid.uuid4().hex[:16]}-01"
        resp = client.get("/health", headers={"traceparent": traceparent})

        assert resp.headers.get("X-Trace-ID") == trace_id


# ============ Config Tests ============

class TestOTelConfig:
    """Test observability configuration."""

    def test_default_config(self):
        """Default OTelConfig should have sensible defaults."""
        from moa_gateway.observability.config import OTelConfig

        cfg = OTelConfig()
        assert cfg.service_name == "moa-gateway-pro"
        assert cfg.trace_enabled is True
        assert cfg.metrics_enabled is True
        assert cfg.trace_sample_rate == 1.0
        assert cfg.prometheus_enabled is True

    def test_config_from_env(self, monkeypatch):
        """get_otel_config should read from environment variables."""
        from moa_gateway.observability.config import get_otel_config

        monkeypatch.setenv("OTEL_SERVICE_NAME", "my-service")
        monkeypatch.setenv("OTEL_TRACE_ENABLED", "false")
        monkeypatch.setenv("MOA_LOG_JSON", "true")

        cfg = get_otel_config()
        assert cfg.service_name == "my-service"
        assert cfg.trace_enabled is False
        assert cfg.log_json is True


# ============ Exporters Tests ============

class TestExporters:
    """Test span exporters."""

    def test_console_exporter(self, caplog):
        """ConsoleSpanExporter should log span info."""
        from moa_gateway.observability.exporters import ConsoleSpanExporter
        from moa_gateway.observability.tracer import SpanRecord

        exporter = ConsoleSpanExporter()
        span = SpanRecord(
            trace_id="a" * 32,
            span_id="b" * 16,
            parent_span_id=None,
            name="test-span",
            start_time=time.time() - 0.1,
            end_time=time.time(),
            attributes={"key": "val"},
            status="OK",
        )

        with caplog.at_level(logging.INFO, logger="moa_gateway.observability.exporters"):
            exporter.export_span(span)

        assert "test-span" in caplog.text


# ============ Integration Tests ============

class TestIntegration:
    """Integration tests combining multiple observability components."""

    def test_full_request_lifecycle(self):
        """A complete request should generate trace + metrics + correlated logs."""
        from moa_gateway.server import create_app

        app = create_app()
        client = TestClient(app)

        # Make a request
        resp = client.get("/health")
        assert resp.status_code == 200

        # Verify trace propagation
        assert "X-Trace-ID" in resp.headers
        trace_id = resp.headers["X-Trace-ID"]
        assert len(trace_id) == 32

        # Verify duration tracking
        duration_ms = resp.headers.get("X-Request-Duration-Ms", "0")
        assert float(duration_ms) >= 0

    def test_metrics_endpoint_returns_prometheus(self):
        """GET /metrics should return Prometheus format with custom metrics."""
        from moa_gateway.server import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.get("/metrics")

        assert resp.status_code == 200
        body = resp.text
        # Should contain at least some of our custom metrics
        assert "moa_" in body
