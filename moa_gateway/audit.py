"""moa_gateway.audit - Structured audit logging for compliance.

Provides:
- AuditEvent dataclass for structured audit records
- log_audit() to emit events to the audit logger
- audit_action() async helper for use inside route handlers
- setup_audit_logging() to configure file-based JSON audit output
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import Request

# Dedicated audit logger - separate from app logger
audit_logger = logging.getLogger("moa_gateway.audit")

# HMAC signing chain state
_audit_secret: str | None = None
_prev_signature: str = ""


def init_audit_signing(secret: str) -> None:
    """Initialize HMAC signing for audit logs.

    Once initialized, every audit record is signed with HMAC-SHA256,
    forming a tamper-evident chain: each record's signature includes
    the previous record's signature.
    """
    global _audit_secret, _prev_signature
    _audit_secret = secret
    _prev_signature = ""


def compute_signature(
    payload: dict[str, Any], secret: str, prev_sig: str = ""
) -> str:
    """Compute HMAC-SHA256 signature for an audit record.

    Forms a chain: each record's signature includes the previous
    record's signature, making tampering detectable across the chain.
    """
    # Exclude sig and prev_sig from the signed payload
    sign_payload = {k: v for k, v in payload.items() if k not in ("sig", "prev_sig")}
    message = json.dumps(sign_payload, sort_keys=True, ensure_ascii=False) + prev_sig
    return hmac.new(
        secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_signature(
    payload: dict[str, Any], secret: str, sig: str, prev_sig: str = ""
) -> bool:
    """Verify the HMAC signature of an audit record."""
    expected = compute_signature(payload, secret, prev_sig)
    return hmac.compare_digest(expected, sig)


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
        resource_id: str | None = None,
        detail: dict | None = None,
        result: str = "success",
        ip_address: str | None = None,
        request_id: str | None = None,
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
    If HMAC signing is initialized, appends a tamper-evident signature
    chain to each record.
    """
    global _prev_signature
    entry = event.to_dict()
    # PII redaction in audit logs
    try:
        from .compliance.config import PII_LOG_REDACTION
        from .compliance.pii_detector import pii_detector

        if PII_LOG_REDACTION:
            _redact_dict(entry, pii_detector)
    except ImportError:
        pass  # compliance module not available

    # HMAC signature chain
    if _audit_secret:
        entry["prev_sig"] = _prev_signature
        entry["sig"] = compute_signature(entry, _audit_secret, _prev_signature)
        _prev_signature = entry["sig"]

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
    resource_id: str | None = None,
    detail: dict | None = None,
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
    Also initializes HMAC signing if a secret is available.
    """
    global _audit_secret
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Initialize HMAC signing secret (decoupled from handler setup so a
    # retry after config becomes available can still activate the chain)
    if _audit_secret is None:
        secret = os.environ.get("AUDIT_SIGNING_SECRET", "")
        if not secret:
            # Reuse JWT secret as audit signing secret
            try:
                from .config import get_settings

                secret = get_settings().auth.jwt_secret
            except Exception as e:
                logging.getLogger(__name__).warning(
                    "audit signing disabled: settings unavailable (%s)", e
                )
        if secret:
            init_audit_signing(secret)

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

    if _audit_secret:
        audit_logger.info(
            json.dumps({"ts": time.time(), "action": "audit_signing_init", "result": "success"})
        )
