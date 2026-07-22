"""Structured logging with trace correlation.

Features:
- JSON structured log format
- Automatic trace_id/span_id injection
- Request context enrichment
- Configurable output (stdout/file)
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import time
from pathlib import Path
from typing import Any

from .tracer import get_current_span_id, get_current_trace_id


class StructuredJsonFormatter(logging.Formatter):
    """JSON structured log formatter with trace correlation."""

    def format(self, record: logging.LogRecord) -> str:
        trace_id = get_current_trace_id()
        span_id = get_current_span_id()

        log_entry: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Trace correlation
        if trace_id:
            log_entry["trace_id"] = trace_id
        if span_id:
            log_entry["span_id"] = span_id

        # Exception info
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Extra fields from LogRecord
        for key in ("request_id", "model", "provider", "preset",
                    "latency_ms", "cost", "status_code", "method",
                    "path", "client_ip", "org_id"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val

        # Custom extra fields
        if hasattr(record, "extra_fields") and record.extra_fields:
            log_entry.update(record.extra_fields)

        return json.dumps(log_entry, ensure_ascii=False)


class TraceCorrelationFilter(logging.Filter):
    """Filter that adds trace context to all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_current_trace_id() or "-"
        record.span_id = get_current_span_id() or "-"
        return True


def setup_logging(
    level: str = "INFO",
    log_dir: str = "data/logs",
    json_mode: bool = False,
) -> None:
    """Initialize structured logging system.

    Args:
        level: Log level (DEBUG/INFO/WARNING/ERROR)
        log_dir: Directory for log files
        json_mode: Use JSON format if True
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear existing handlers
    for h in list(root.handlers):
        root.removeHandler(h)

    # Formatter selection
    if json_mode:
        fmt = StructuredJsonFormatter()
    else:
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s [trace=%(trace_id)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(TraceCorrelationFilter())
    root.addHandler(console)

    # File handler
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_path / "moa_gateway.log",
        maxBytes=50 * 1024 * 1024,  # 50MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.addFilter(TraceCorrelationFilter())
    root.addHandler(file_handler)

    # Suppress noisy loggers
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Structured logging initialized: level=%s, json=%s, dir=%s",
        level, json_mode, log_dir,
    )
