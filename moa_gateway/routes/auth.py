"""Authentication endpoints — /api/auth/*."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth import create_jwt_token, require_admin
from ..storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


def get_client_ip(request: Request) -> str:
    """Extract client IP from Request, prefer X-Forwarded-For, fallback to client.host"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/api/auth/login")
async def login(req: LoginRequest, client_ip: str = Depends(get_client_ip)):
    # Round-1 (P1-9): IP-based rate limit to prevent brute force
    storage = get_storage()
    with storage.conn() as c:
        row = c.execute(
            "SELECT count, window_start FROM login_attempts WHERE ip = ?",
            (client_ip,),
        ).fetchone()
        now = time.time()
        if row and (now - float(row["window_start"])) < 60.0:
            if int(row["count"]) >= 10:
                raise HTTPException(429, "too many login attempts, try again later")
            c.execute(
                "UPDATE login_attempts SET count = count + 1 WHERE ip = ?",
                (client_ip,),
            )
        else:
            c.execute(
                "INSERT OR REPLACE INTO login_attempts (ip, count, window_start) "
                "VALUES (?, 1, ?)",
                (client_ip, now),
            )
    from ..storage import async_bcrypt_verify

    with storage.conn() as c:
        row = c.execute(
            "SELECT * FROM admin_users WHERE username = ?", (req.username,)
        ).fetchone()
    if not row:
        raise HTTPException(401, "Invalid username or password")
    ok = await async_bcrypt_verify(req.password, row["password_hash"])
    if not ok:
        raise HTTPException(401, "Invalid username or password")
    with storage.conn() as c:
        c.execute(
            "UPDATE admin_users SET last_login = ? WHERE id = ?", (time.time(), row["id"])
        )
    from ..config import get_settings as _gs

    settings = _gs()
    must_change = (
        req.username == settings.auth.admin_username
        and req.password == settings.auth.admin_password
    )
    token = create_jwt_token(row["username"], row["role"])
    # Audit: successful login
    from ..audit import AuditEvent, log_audit
    log_audit(AuditEvent(
        action="login",
        actor_id=row["username"],
        actor_role=row["role"],
        resource="auth",
        resource_id=row["username"],
        detail={"ip": client_ip},
        result="success",
        ip_address=client_ip,
    ))
    return {
        "token": token,
        "user": {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "must_change_password": must_change,
        },
    }


@router.post("/api/auth/change-password")
async def change_password(
    req: ChangePasswordRequest, admin: dict[str, Any] = Depends(require_admin)
):
    storage = get_storage()
    if not storage.verify_admin(admin["sub"], req.old_password):
        raise HTTPException(400, "old password incorrect")
    ok = await asyncio.to_thread(storage.change_admin_password, admin["sub"], req.new_password)
    if not ok:
        raise HTTPException(500, "change password failed")
    return {"ok": True}


@router.get("/api/auth/me")
async def me(admin: dict[str, Any] = Depends(require_admin)):
    return admin
