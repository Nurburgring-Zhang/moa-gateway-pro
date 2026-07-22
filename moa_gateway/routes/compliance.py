"""Compliance API endpoints — SOC2 technical controls."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..audit import audit_action
from ..auth import require_admin
from ..compliance import (
    GDPRManager,
    KeyRotationManager,
    SecurityBaselineChecker,
    DataRetentionManager,
    pii_detector,
)
from ..rbac import Permission, check_permission_or_raise

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/compliance", tags=["compliance"])

# Singleton instances
_gdpr_manager = GDPRManager()
_key_manager = KeyRotationManager()
_retention_manager = DataRetentionManager()
_baseline_checker = SecurityBaselineChecker()


# ========== Security Baseline ==========
@router.get("/baseline")
async def run_baseline_check(
    request: Request, admin: dict[str, Any] = Depends(require_admin)
):
    """Run security configuration baseline check."""
    check_permission_or_raise(admin, Permission.READ_LOGS)
    result = _baseline_checker.summary()
    await audit_action(request, "baseline_check", "compliance", detail={"passed": result["passed"]})
    return result


# ========== GDPR Endpoints ==========
class GDPRDeleteRequest(BaseModel):
    user_id: str = Field(..., description="User ID to delete data for")
    categories: list[str] = Field(default=["all"], description="Data categories to delete")


@router.post("/gdpr/delete")
async def create_gdpr_deletion(
    req: GDPRDeleteRequest, request: Request, admin: dict[str, Any] = Depends(require_admin)
):
    """Create a GDPR data deletion request (right to be forgotten)."""
    check_permission_or_raise(admin, Permission.WRITE_USERS)
    deletion_req = await _gdpr_manager.create_deletion_request(req.user_id, req.categories)
    # Auto-process immediately
    result = await _gdpr_manager.process_deletion(deletion_req.request_id)
    await audit_action(
        request, "gdpr_delete", "compliance",
        resource_id=deletion_req.request_id,
        detail={"user_id": req.user_id},
    )
    return {
        "request_id": deletion_req.request_id,
        "status": result.get("status", "pending"),
        "deleted": result.get("deleted", {}),
    }


@router.get("/gdpr/status/{request_id}")
async def get_gdpr_status(
    request_id: str, admin: dict[str, Any] = Depends(require_admin)
):
    """Query GDPR deletion request status."""
    status = _gdpr_manager.get_request_status(request_id)
    if not status:
        raise HTTPException(404, "Deletion request not found")
    return status


class GDPRExportRequest(BaseModel):
    user_id: str = Field(..., description="User ID to export data for")


@router.post("/gdpr/export")
async def export_user_data(
    req: GDPRExportRequest, request: Request, admin: dict[str, Any] = Depends(require_admin)
):
    """Export user data (data portability right)."""
    check_permission_or_raise(admin, Permission.READ_USERS)
    data = await _gdpr_manager.export_user_data(req.user_id)
    await audit_action(
        request, "gdpr_export", "compliance",
        detail={"user_id": req.user_id},
    )
    return data


# ========== Data Retention ==========
@router.get("/retention")
async def get_retention_policies(admin: dict[str, Any] = Depends(require_admin)):
    """View data retention policies."""
    return {"policies": _retention_manager.get_policy_status()}


@router.post("/retention/cleanup")
async def trigger_retention_cleanup(
    request: Request, admin: dict[str, Any] = Depends(require_admin)
):
    """Manually trigger data retention cleanup."""
    check_permission_or_raise(admin, Permission.ADMIN_SYSTEM)
    results = await _retention_manager.run_cleanup()
    await audit_action(request, "retention_cleanup", "compliance", detail=results)
    return {"status": "completed", "results": results}


# ========== Key Rotation ==========
@router.get("/key-rotation/status")
async def key_rotation_status(admin: dict[str, Any] = Depends(require_admin)):
    """Get key rotation status."""
    return _key_manager.get_status()


@router.post("/key-rotation/rotate")
async def trigger_key_rotation(
    request: Request, admin: dict[str, Any] = Depends(require_admin)
):
    """Manually trigger key rotation."""
    check_permission_or_raise(admin, Permission.ADMIN_RBAC)
    new_key = _key_manager.generate_key("api")
    await audit_action(
        request, "key_rotation", "compliance",
        detail={"new_key_id": new_key.key_id},
    )
    return {
        "status": "rotated",
        "new_key_id": new_key.key_id,
        "expires_at": new_key.expires_at,
    }


# ========== PII Detection ==========
class PIIScanRequest(BaseModel):
    text: str = Field(..., description="Text to scan for PII")


@router.post("/pii/scan")
async def scan_for_pii(req: PIIScanRequest, admin: dict[str, Any] = Depends(require_admin)):
    """Scan text for PII and return detected matches."""
    matches = pii_detector.detect(req.text)
    return {
        "has_pii": len(matches) > 0,
        "match_count": len(matches),
        "matches": [
            {"type": m.type, "masked": m.masked, "start": m.start, "end": m.end}
            for m in matches
        ],
    }


@router.post("/pii/redact")
async def redact_pii(req: PIIScanRequest, admin: dict[str, Any] = Depends(require_admin)):
    """Redact PII from text."""
    return {"original_length": len(req.text), "redacted": pii_detector.redact(req.text)}
