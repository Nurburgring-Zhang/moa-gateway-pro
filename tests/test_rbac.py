"""RBAC + Audit comprehensive test suite.

Tests:
1. Role-permission matrix correctness
2. Admin has all permissions
3. User cannot access admin operations
4. Readonly user minimal permissions
5. Operator has intermediate permissions
6. Permission check raises on unauthorized
7. Audit event serialization
8. Audit logging to file
9. User role update via storage
10. Role upgrade/downgrade
11. Invalid role rejection
12. check_permission_or_raise function
"""

from __future__ import annotations

import json

import pytest

from moa_gateway.audit import AuditEvent, log_audit, setup_audit_logging
from moa_gateway.rbac import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
    check_permission_or_raise,
    get_user_permissions,
    has_permission,
)

# ========== RBAC Permission Matrix Tests ==========


class TestRolePermissions:
    """Test role-permission mapping matrix."""

    def test_admin_has_all_permissions(self):
        """Admin role should have every defined permission."""
        admin_perms = ROLE_PERMISSIONS[Role.ADMIN]
        for perm in Permission:
            assert perm in admin_perms, f"Admin missing permission: {perm.value}"

    def test_readonly_minimal_permissions(self):
        """Readonly role should only have read:models and read:stats."""
        readonly_perms = ROLE_PERMISSIONS[Role.READONLY]
        assert readonly_perms == {Permission.READ_MODELS, Permission.READ_STATS}

    def test_user_cannot_write_endpoints(self):
        """User role should not have write:endpoints permission."""
        user_perms = ROLE_PERMISSIONS[Role.USER]
        assert Permission.WRITE_ENDPOINTS not in user_perms
        assert Permission.WRITE_MODELS not in user_perms
        assert Permission.WRITE_USERS not in user_perms
        assert Permission.ADMIN_SYSTEM not in user_perms
        assert Permission.ADMIN_RBAC not in user_perms

    def test_operator_has_operational_permissions(self):
        """Operator should have read/write for endpoints, keys, models."""
        op_perms = ROLE_PERMISSIONS[Role.OPERATOR]
        assert Permission.WRITE_ENDPOINTS in op_perms
        assert Permission.WRITE_KEYS in op_perms
        assert Permission.WRITE_MODELS in op_perms
        assert Permission.READ_LOGS in op_perms
        # But not admin-level
        assert Permission.ADMIN_SYSTEM not in op_perms
        assert Permission.ADMIN_RBAC not in op_perms
        assert Permission.WRITE_USERS not in op_perms

    def test_user_can_call_apis(self):
        """User role should be able to call chat/moa/agent."""
        user_perms = ROLE_PERMISSIONS[Role.USER]
        assert Permission.CALL_CHAT in user_perms
        assert Permission.CALL_MOA in user_perms
        assert Permission.CALL_AGENT in user_perms

    def test_readonly_cannot_call_apis(self):
        """Readonly role should not be able to call chat/moa/agent."""
        readonly_perms = ROLE_PERMISSIONS[Role.READONLY]
        assert Permission.CALL_CHAT not in readonly_perms
        assert Permission.CALL_MOA not in readonly_perms
        assert Permission.CALL_AGENT not in readonly_perms


# ========== Permission Check Function Tests ==========


class TestPermissionChecks:
    """Test has_permission and check_permission_or_raise."""

    def test_has_permission_admin(self):
        """Admin user should pass any permission check."""
        user = {"role": "admin", "username": "admin"}
        assert has_permission(user, Permission.ADMIN_SYSTEM) is True
        assert has_permission(user, Permission.WRITE_USERS) is True

    def test_has_permission_user_denied(self):
        """User role should fail admin permission checks."""
        user = {"role": "user", "username": "alice"}
        assert has_permission(user, Permission.ADMIN_SYSTEM) is False
        assert has_permission(user, Permission.WRITE_ENDPOINTS) is False

    def test_has_permission_invalid_role(self):
        """Invalid role string should result in no permissions."""
        user = {"role": "hacker", "username": "evil"}
        assert has_permission(user, Permission.READ_MODELS) is False

    def test_has_permission_missing_role_defaults_readonly(self):
        """Missing role field should default to readonly."""
        user = {"username": "noone"}
        # readonly has READ_MODELS
        assert has_permission(user, Permission.READ_MODELS) is True
        # readonly does NOT have CALL_CHAT
        assert has_permission(user, Permission.CALL_CHAT) is False

    def test_check_permission_or_raise_success(self):
        """Should not raise when user has permission."""
        user = {"role": "admin", "username": "admin"}
        # Should not raise
        check_permission_or_raise(user, Permission.ADMIN_RBAC)

    def test_check_permission_or_raise_denied(self):
        """Should raise HTTPException 403 when user lacks permission."""
        from fastapi import HTTPException

        user = {"role": "readonly", "username": "viewer"}
        with pytest.raises(HTTPException) as exc_info:
            check_permission_or_raise(user, Permission.WRITE_ENDPOINTS)
        assert exc_info.value.status_code == 403
        assert "write:endpoints" in exc_info.value.detail

    def test_get_user_permissions_returns_correct_set(self):
        """get_user_permissions should return the correct permission set."""
        perms = get_user_permissions("operator")
        assert Permission.READ_LOGS in perms
        assert Permission.ADMIN_SYSTEM not in perms


# ========== Audit Logging Tests ==========


class TestAuditLogging:
    """Test structured audit event creation and logging."""

    def test_audit_event_serialization(self):
        """AuditEvent.to_dict() should produce valid JSON-serializable dict."""
        event = AuditEvent(
            action="login",
            actor_id="admin",
            actor_role="admin",
            resource="auth",
            resource_id="admin",
            detail={"ip": "127.0.0.1"},
            result="success",
            ip_address="127.0.0.1",
            request_id="req-123",
        )
        d = event.to_dict()
        assert d["action"] == "login"
        assert d["actor"]["id"] == "admin"
        assert d["actor"]["role"] == "admin"
        assert d["resource"]["type"] == "auth"
        assert d["resource"]["id"] == "admin"
        assert d["detail"]["ip"] == "127.0.0.1"
        assert d["result"] == "success"
        assert d["ip"] == "127.0.0.1"
        assert d["request_id"] == "req-123"
        assert isinstance(d["ts"], float)
        # Must be JSON-serializable
        json_str = json.dumps(d)
        assert "login" in json_str

    def test_audit_event_defaults(self):
        """AuditEvent with minimal args should have sensible defaults."""
        event = AuditEvent(
            action="test",
            actor_id="user1",
            actor_role="user",
            resource="test_resource",
        )
        d = event.to_dict()
        assert d["resource"]["id"] is None
        assert d["detail"] == {}
        assert d["result"] == "success"
        assert d["ip"] is None
        assert d["request_id"] is None

    def test_audit_logging_to_file(self, tmp_path):
        """setup_audit_logging should create file and log events."""
        import logging

        log_file = tmp_path / "audit.jsonl"
        # Reset audit logger handlers for isolation
        audit_log = logging.getLogger("moa_gateway.audit")
        audit_log.handlers.clear()

        setup_audit_logging(str(log_file))

        event = AuditEvent(
            action="test_action",
            actor_id="test_user",
            actor_role="admin",
            resource="test",
        )
        log_audit(event)

        # Force flush
        for handler in audit_log.handlers:
            handler.flush()

        content = log_file.read_text(encoding="utf-8").strip()
        assert content  # Non-empty
        # JSONL: may contain an audit_signing_init marker line + event lines
        records = [json.loads(line) for line in content.splitlines() if line.strip()]
        parsed = next(r for r in records if r.get("action") == "test_action")
        assert parsed["actor"]["id"] == "test_user"

        # Cleanup
        audit_log.handlers.clear()


# ========== Storage RBAC Methods Tests ==========


class TestStorageRBAC:
    """Test storage-level user/role management."""

    def test_create_and_list_users(self, storage_instance):
        """Should create users and list them."""
        storage = storage_instance
        # Bootstrap creates admin already
        users = storage.list_admin_users()
        assert len(users) >= 1
        admin_user = next((u for u in users if u["username"] == "admin"), None)
        assert admin_user is not None
        assert admin_user["role"] == "admin"

    def test_create_user_with_role(self, storage_instance):
        """Should create a user with a specific role."""
        storage = storage_instance
        user = storage.create_admin_user("operator1", "StrongPass123!", "operator")
        assert user is not None
        assert user["username"] == "operator1"
        assert user["role"] == "operator"

    def test_update_user_role(self, storage_instance):
        """Should update user role successfully."""
        storage = storage_instance
        user = storage.create_admin_user("bob", "BobPass456!", "user")
        assert user is not None
        ok = storage.update_user_role(user["id"], "operator")
        assert ok is True
        updated = storage.get_admin_user(user["id"])
        assert updated["role"] == "operator"

    def test_update_role_invalid_rejects(self, storage_instance):
        """Should reject invalid role values."""
        storage = storage_instance
        user = storage.create_admin_user("charlie", "CharliePass!", "user")
        with pytest.raises(ValueError, match="Invalid role"):
            storage.update_user_role(user["id"], "superadmin")

    def test_create_duplicate_user_returns_none(self, storage_instance):
        """Should return None when creating duplicate username."""
        storage = storage_instance
        # admin already exists from bootstrap
        result = storage.create_admin_user("admin", "AnotherPass!", "user")
        assert result is None

    def test_delete_user(self, storage_instance):
        """Should delete a user."""
        storage = storage_instance
        user = storage.create_admin_user("deleteme", "Pass123!", "readonly")
        assert user is not None
        ok = storage.delete_admin_user(user["id"])
        assert ok is True
        assert storage.get_admin_user(user["id"]) is None
