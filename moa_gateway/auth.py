"""moa_gateway.auth — 鉴权(API Key + WebUI JWT)"""

from __future__ import annotations

import logging
import re as _re
import time
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPBearer
from jose import JWTError, jwt

from .config import get_settings
from .storage import get_storage

logger = logging.getLogger(__name__)
# 注:passlib bcrypt 在新版下有兼容问题,storage.py 已用 bcrypt 原生 API
# 这里不再创建 CryptContext

_api_key_header = APIKeyHeader(name="Authorization", auto_error=False)
# 修 P1-3: JWT 严格正则 (header.payload.signature, 3 段 base64url)
_JWT_PATTERN = _re.compile(r"^eyJ[A-Za-z0-9_=\-]+\.eyJ[A-Za-z0-9_=\-]+\.[A-Za-z0-9_\-]+$")
# 修 P1-6: token 长度上限(防内存炸弹)
_MAX_TOKEN_LEN = 256


def _bearer_or_raw(token: str) -> str:
    """修 P1-6: 限 token 长度,逗号分隔取第一个"""
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    else:
        token = token.strip()
    # 修 P1-6: 多值 header 用逗号分隔时取第一个
    if "," in token:
        token = token.split(",", 1)[0].strip()
    # 修 P1-6: 长度上限
    if len(token) > _MAX_TOKEN_LEN:
        return ""
    return token


async def authenticate_api_key(request: Request) -> dict[str, Any] | None:
    """从 Authorization header 拿 API Key 并校验。
    兼容:
        Authorization: Bearer mgw-xxx
        Authorization: mgw-xxx
    注:不再支持 ?api_key= query(防日志/Referer/代理泄露)— 修14 P1-3
    """
    token = None
    auth = request.headers.get("Authorization")
    if auth:
        token = _bearer_or_raw(auth)
    if not token:
        return None

    # 修 P1-3: 用严格正则判断 JWT(避免 "a.b.c" 这种非 JWT 也走 decode 路径)
    if _JWT_PATTERN.match(token):
        info = decode_jwt_token(token)
        if info and info.get("role") == "admin":
            return {
                "source": "admin_jwt",
                "name": info.get("sub", "admin"),
                "role": "admin",
                "quota_rpm": 999_999,
                "quota_daily_tokens": 999_999_999,
            }

    # 先查 storage 里的 API Key(优先)
    storage = get_storage()
    rec = storage.find_api_key(token)
    if rec:
        return {
            "source": "api_key",
            "key_id": rec["key_id"],
            "name": rec["name"],
            "quota_rpm": rec["quota_rpm"],
            "quota_daily_tokens": rec["quota_daily_tokens"],
        }

    # fallback:检查 config.yaml 里配的 gateway_api_keys
    settings = get_settings()
    for k in settings.auth.gateway_api_keys:
        if k and k == token:
            return {
                "source": "yaml",
                "key_id": "yaml",
                "name": "yaml-config",
                "quota_rpm": 10000,
                "quota_daily_tokens": 999_999_999,
            }
    return None


async def require_api_key(request: Request) -> dict[str, Any]:
    """FastAPI 依赖:必须通过 API Key 鉴权"""
    info = await authenticate_api_key(request)
    if not info:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Use Authorization: Bearer <key> or ?api_key=<key>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return info


# ========== WebUI JWT ==========
_bearer = HTTPBearer(auto_error=False)


def create_jwt_token(subject: str, role: str = "admin", expires_minutes: int | None = None) -> str:
    settings = get_settings()
    expires_minutes = expires_minutes or settings.auth.jwt_expire_minutes
    payload = {
        "sub": subject,
        "role": role,
        "aud": "moa-webui",  # P1-5 加固:绑定 audience
        "iss": "moa-gateway",  # P1-5 加固:绑定 issuer
        "iat": int(time.time()),
        "exp": int(time.time()) + expires_minutes * 60,
    }
    return jwt.encode(payload, settings.auth.jwt_secret, algorithm="HS256")


def decode_jwt_token(token: str) -> dict[str, Any] | None:
    settings = get_settings()
    # SEC-003: 防御 alg=none 攻击 (CVE-2024-33663)
    try:
        header = jwt.get_unverified_header(token)
    except JWTError:
        return None
    if header.get("alg", "").lower() in ("none", ""):
        logger.warning("JWT with alg=none rejected")
        return None
    try:
        return jwt.decode(
            token,
            settings.auth.jwt_secret,
            algorithms=["HS256"],
            audience="moa-webui",  # P1-5 加固:验证 audience
            issuer="moa-gateway",  # P1-5 加固:验证 issuer
            options={
                "verify_signature": True,
                "verify_exp": True,
                "require_exp": True,
                "verify_iat": True,
                "require_iat": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except JWTError as e:
        logger.debug("jwt decode failed: %s", e)
        return None


async def require_admin(request: Request, credentials=Depends(_bearer)) -> dict[str, Any]:
    """FastAPI 依赖:WebUI 必须登录(从 Authorization: Bearer <jwt>)"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    info = decode_jwt_token(credentials.credentials)
    if not info or info.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        )
    return info
