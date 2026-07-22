"""moa_gateway.rbac - Role-Based Access Control (RBAC) permission system.

Provides:
- Role enum (admin / operator / user / readonly)
- Permission enum (fine-grained operation scopes)
- Role-Permission matrix
- require_permission() decorator for endpoint-level access control
- has_permission() helper for programmatic checks
"""

from __future__ import annotations

import logging
from enum import Enum
from functools import wraps
from typing import Set

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


class Role(str, Enum):
    """System roles ordered by privilege level."""

    ADMIN = "admin"  # Full system control
    OPERATOR = "operator"  # Operations: manage endpoints/keys/view logs
    USER = "user"  # Normal usage: call APIs
    READONLY = "readonly"  # Read-only: view models/stats


class Permission(str, Enum):
    """Fine-grained operation permissions (scope-style)."""

    # Chat/model invocations
    CALL_CHAT = "call:chat"
    CALL_MOA = "call:moa"
    CALL_AGENT = "call:agent"

    # Model management
    READ_MODELS = "read:models"
    WRITE_MODELS = "write:models"

    # API Key management
    READ_KEYS = "read:keys"
    WRITE_KEYS = "write:keys"

    # Endpoint management
    READ_ENDPOINTS = "read:endpoints"
    WRITE_ENDPOINTS = "write:endpoints"

    # User management
    READ_USERS = "read:users"
    WRITE_USERS = "write:users"

    # Logs & statistics
    READ_LOGS = "read:logs"
    READ_STATS = "read:stats"

    # System administration
    ADMIN_SYSTEM = "admin:system"
    ADMIN_RBAC = "admin:rbac"


# Role -> Permission mapping matrix
ROLE_PERMISSIONS: dict[Role, Set[Permission]] = {
    Role.ADMIN: set(Permission),  # All permissions
    Role.OPERATOR: {
        Permission.CALL_CHAT,
        Permission.CALL_MOA,
        Permission.CALL_AGENT,
        Permission.READ_MODELS,
        Permission.WRITE_MODELS,
        Permission.READ_KEYS,
        Permission.WRITE_KEYS,
        Permission.READ_ENDPOINTS,
        Permission.WRITE_ENDPOINTS,
        Permission.READ_LOGS,
        Permission.READ_STATS,
        Permission.READ_USERS,
    },
    Role.USER: {
        Permission.CALL_CHAT,
        Permission.CALL_MOA,
        Permission.CALL_AGENT,
        Permission.READ_MODELS,
        Permission.READ_KEYS,  # Own keys only
        Permission.READ_STATS,
    },
    Role.READONLY: {
        Permission.READ_MODELS,
        Permission.READ_STATS,
    },
}


def get_user_permissions(role: str) -> Set[Permission]:
    """Get the set of permissions for a given role string."""
    try:
        r = Role(role)
    except ValueError:
        return set()
    return ROLE_PERMISSIONS.get(r, set())


def has_permission(user: dict, permission: Permission) -> bool:
    """Check if a user dict has a specific permission based on their role."""
    role_str = user.get("role", "readonly")
    return permission in get_user_permissions(role_str)


def require_permission(*permissions: Permission):
    """Endpoint-level permission check decorator.

    Usage:
        @router.get("/api/endpoints")
        @require_permission(Permission.READ_ENDPOINTS)
        async def list_endpoints(request: Request, ...):
            ...
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract request from args/kwargs
            request: Request | None = kwargs.get("request")
            if request is None:
                for arg in args:
                    if isinstance(arg, Request):
                        request = arg
                        break

            if not request or not hasattr(request, "state"):
                raise HTTPException(status_code=403, detail="Permission denied")

            user = getattr(request.state, "user", None)
            if not user:
                raise HTTPException(status_code=401, detail="Not authenticated")

            user_role = user.get("role", "readonly")
            user_perms = get_user_permissions(user_role)

            missing = [p for p in permissions if p not in user_perms]
            if missing:
                logger.warning(
                    "Permission denied: user=%s role=%s missing=%s",
                    user.get("username", user.get("name", "unknown")),
                    user_role,
                    [p.value for p in missing],
                )
                raise HTTPException(
                    status_code=403,
                    detail=f"Permission denied: requires {missing[0].value}",
                )

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def check_permission_or_raise(user: dict, permission: Permission) -> None:
    """Inline permission check - raises HTTPException if denied.

    For use inside route handlers where decorator pattern is not convenient.
    """
    if not has_permission(user, permission):
        raise HTTPException(
            status_code=403,
            detail=f"Permission denied: requires {permission.value}",
        )
