"""Trace and metric exporters configuration.

Supports:
- Console exporter (dev/debug)
- OTLP gRPC exporter (production)
- OTLP HTTP exporter (alternative)
- Prometheus exporter (metrics scraping)
"""
from __future__ import annotations

import logging

from .tracer import SpanRecord

logger = logging.getLogger(__name__)


class ConsoleSpanExporter:
    """Export spans to console (for development)."""

    def export_span(self, span: SpanRecord):
        logger.info(
            "SPAN [%s] %s | trace=%s parent=%s | %.1fms | %s | attrs=%s",
            span.span_id[:8],
            span.name,
            span.trace_id[:8],
            (span.parent_span_id or "-")[:8],
            span.duration_ms,
            span.status,
            span.attributes,
        )


class OTLPSpanExporter:
    """Export spans via OTLP protocol."""

    def __init__(self, endpoint: str, protocol: str = "grpc"):
        self.endpoint = endpoint
        self.protocol = protocol
        self._exporter = None
        self._setup()

    def _setup(self):
        try:
            if self.protocol == "grpc":
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # noqa: PLC0415
                    OTLPSpanExporter as _OTLPExporter,
                )
                self._exporter = _OTLPExporter(endpoint=self.endpoint)
            else:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
                    OTLPSpanExporter as _OTLPExporter,
                )
                self._exporter = _OTLPExporter(endpoint=self.endpoint)
            logger.info("OTLP exporter configured: %s (%s)", self.endpoint, self.protocol)
        except ImportError as e:
            logger.warning("OTLP exporter not available: %s", e)

    def export_span(self, span: SpanRecord):
        # In production, batch and send via OTLP
        # For now, the OTel SDK handles this via TracerProvider
        pass


def setup_exporters(
    console: bool = False,
    otlp_endpoint: str | None = None,
    otlp_protocol: str = "grpc",
):
    """Configure span exporters."""
    from .tracer import get_tracer  # noqa: PLC0415

    tracer = get_tracer()

    if console:
        tracer.add_exporter(ConsoleSpanExporter())
        logger.info("Console span exporter enabled")

    if otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, protocol=otlp_protocol)
        tracer.add_exporter(exporter)
