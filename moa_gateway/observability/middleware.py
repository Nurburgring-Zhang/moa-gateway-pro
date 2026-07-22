"""FastAPI Observability Middleware.

Automatically:
- Generates/propagates trace_id for every request
- Records request duration metrics
- Tracks active connections
- Injects trace headers into responses
- Correlates logs with traces
"""
from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .metrics import (
    active_connections,
    llm_request_duration_seconds,
    llm_requests_total,
)
from .tracer import clear_trace_context, get_tracer, set_trace_context

logger = logging.getLogger(__name__)


def _extract_trace_id(request: Request) -> str:
    """Extract trace_id from headers or generate new one."""
    # Check X-Trace-ID header first
    trace_id = request.headers.get("X-Trace-ID")
    if trace_id:
        return trace_id

    # Check W3C traceparent header: 00-<trace_id>-<parent_id>-<flags>
    traceparent = request.headers.get("traceparent", "")
    if traceparent:
        parts = traceparent.split("-")
        if len(parts) >= 3 and len(parts[1]) == 32:
            return parts[1]

    # Generate new trace_id
    return uuid.uuid4().hex


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Middleware that instruments every HTTP request with tracing and metrics."""

    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = _extract_trace_id(request)
        span_id = uuid.uuid4().hex[:16]

        # Set trace context for log correlation
        set_trace_context(trace_id, span_id)

        # Store in request state for downstream access
        request.state.trace_id = trace_id
        request.state.span_id = span_id

        # Track active connections
        active_connections.inc()
        start_time = time.time()

        # Create span via gateway tracer
        tracer = get_tracer()
        span = tracer.create_span(
            name=f"{request.method} {request.url.path}",
            trace_id=trace_id,
            parent_span_id=None,
        )
        span.set_attribute("http.method", request.method)
        span.set_attribute("http.url", str(request.url))
        span.set_attribute("http.target", request.url.path)
        span.set_attribute("http.client_ip", request.client.host if request.client else "unknown")
        span.set_attribute("http.user_agent", request.headers.get("user-agent", ""))

        try:
            response = await call_next(request)
            duration = time.time() - start_time

            # Complete span
            span.set_attribute("http.status_code", response.status_code)
            status = "OK" if response.status_code < 400 else "ERROR"
            span.end(status)
            tracer._record_span(span)

            # Record metrics for API paths
            if request.url.path.startswith(("/v1/", "/api/", "/chat/")):
                status_label = "success" if response.status_code < 400 else "error"
                llm_requests_total.labels(
                    model="gateway", provider="self", status=status_label
                ).inc()
                llm_request_duration_seconds.labels(
                    model="gateway", provider="self", status=status_label
                ).observe(duration)

            # Inject trace response headers
            response.headers["X-Trace-ID"] = trace_id
            response.headers["X-Span-ID"] = span_id
            response.headers["X-Request-Duration-Ms"] = f"{duration * 1000:.1f}"

            return response

        except Exception as exc:
            duration = time.time() - start_time
            span.set_attribute("error", True)
            span.set_attribute("error.type", type(exc).__name__)
            span.set_attribute("error.message", str(exc))
            span.end("ERROR")
            tracer._record_span(span)

            logger.error(
                "Request failed: %s %s [trace=%s] error=%s duration=%.3fs",
                request.method, request.url.path, trace_id, exc, duration,
            )
            raise

        finally:
            active_connections.dec()
            clear_trace_context()
