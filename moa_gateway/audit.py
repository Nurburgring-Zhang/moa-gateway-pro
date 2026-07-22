"""moa_gateway.audit - Structured audit logging for compliance.

Provides:
- AuditEvent dataclass for structured audit records
- log_audit() to emit events to the audit logger
- audit_action() async helper for use inside route handlers
- setup_audit_logging() to configure file-based JSON audit output
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import Request

# Dedicated audit logger - separate from app logger
audit_logger = logging.getLogger("moa_gateway.audit")


class AuditEvent:
    """Structured audit log entry."""

    __slots__ = (
        "timestamp",
        "action",
        "actor_id",
        "actor_role",
        "resource",
        "resource_id",
        "detail",
        "result",
        "ip_address",
        "request_id",
    )

    def __init__(
        self,
        action: str,
        actor_id: str,
        actor_role: str,
        resource: str,
        resource_id: Optional[str] = None,
        detail: Optional[dict] = None,
        result: str = "success",
        ip_address: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        self.timestamp = time.time()
        self.action = action
        self.actor_id = actor_id
        self.actor_role = actor_role
        self.resource = resource
        self.resource_id = resource_id
        self.detail = detail or {}
        self.result = result
        self.ip_address = ip_address
        self.request_id = request_id

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "ts": self.timestamp,
            "action": self.action,
            "actor": {"id": self.actor_id, "role": self.actor_role},
            "resource": {"type": self.resource, "id": self.resource_id},
            "detail": self.detail,
            "result": self.result,
            "ip": self.ip_address,
            "request_id": self.request_id,
        }


def log_audit(event: AuditEvent) -> None:
    """Emit an audit event to the structured audit logger.

    Automatically redacts PII from audit entries before logging.
    """
    entry = event.to_dict()
    # PII redaction in audit logs
    try:
        from .compliance.pii_detector import pii_detector
        from .compliance.config import PII_LOG_REDACTION

        if PII_LOG_REDACTION:
            _redact_dict(entry, pii_detector)
    except ImportError:
        pass  # compliance module not available
    audit_logger.info(json.dumps(entry, ensure_ascii=False))


def _redact_dict(d: dict, detector) -> None:
    """Recursively redact PII in dict string values."""
    for key, value in d.items():
        if isinstance(value, str) and len(value) > 5:
            d[key] = detector.redact(value)
        elif isinstance(value, dict):
            _redact_dict(value, detector)


async def audit_action(
    request: Request,
    action: str,
    resource: str,
    resource_id: Optional[str] = None,
    detail: Optional[dict] = None,
    result: str = "success",
) -> None:
    """Shortcut to record an audit event from within a route handler.

    Extracts user info and IP from the request automatically.
    """
    user: dict = {}
    if hasattr(request, "state"):
        user = getattr(request.state, "user", None) or {}

    event = AuditEvent(
        action=action,
        actor_id=user.get("username", user.get("name", user.get("sub", "anonymous"))),
        actor_role=user.get("role", "unknown"),
        resource=resource,
        resource_id=resource_id,
        detail=detail,
        result=result,
        ip_address=request.client.host if request.client else None,
        request_id=getattr(request.state, "request_id", None)
        if hasattr(request, "state")
        else None,
    )
    log_audit(event)


def setup_audit_logging(log_path: str = "data/logs/audit.jsonl") -> None:
    """Configure the audit logger with a dedicated file handler.

    Called during app startup. Outputs one JSON line per audit event.
    """
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Avoid duplicate handlers on reload
    if audit_logger.handlers:
        return

    handler = logging.FileHandler(str(path), encoding="utf-8")
    handler.setLevel(logging.INFO)
    # Raw JSON lines - no formatter prefix needed
    handler.setFormatter(logging.Formatter("%(message)s"))
    audit_logger.addHandler(handler)
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = False  # Don't pollute main app log
