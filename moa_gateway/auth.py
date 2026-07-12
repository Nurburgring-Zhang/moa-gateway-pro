"""moa_gateway.auth — 鉴权(API Key + WebUI JWT)"""
from __future__ import annotations
import time
import logging
from typing import Optional, Dict, Any
from fastapi import Request, HTTPException, Depends, status
from fastapi.security import APIKeyHeader, HTTPBearer
from jose import jwt, JWTError
from passlib.context import CryptContext

from .config import get_settings
from .storage import get_storage

logger = logging.getLogger(__name__)
# 注:passlib bcrypt 在新版下有兼容问题,storage.py 已用 bcrypt 原生 API
# 这里不再创建 CryptContext

_api_key_header = APIKeyHeader(name="Authorization", auto_error=False)


def _bearer_or_raw(token: str) -> str:
    if token.lower().startswith("bearer "):
        return token[7:].strip()
    return token.strip()


async def authenticate_api_key(request: Request) -> Optional[Dict[str, Any]]:
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

    # 修24: 先试 admin JWT(WebUI 登录后拿的)
    if token.count(".") == 2:  # JWT 格式:header.payload.signature
        info = decode_jwt_token(token)
        if info and info.get("role") == "admin":
            return {"source": "admin_jwt", "name": info.get("sub", "admin"),
                    "role": "admin",
                    "quota_rpm": 999_999, "quota_daily_tokens": 999_999_999}

    # 先查 storage 里的 API Key(优先)
    storage = get_storage()
    rec = storage.find_api_key(token)
    if rec:
        return {"source": "api_key", "key_id": rec["key_id"],
                "name": rec["name"], "quota_rpm": rec["quota_rpm"],
                "quota_daily_tokens": rec["quota_daily_tokens"]}

    # fallback:检查 config.yaml 里配的 gateway_api_keys
    settings = get_settings()
    for k in settings.auth.gateway_api_keys:
        if k and k == token:
            return {"source": "yaml", "key_id": "yaml",
                    "name": "yaml-config",
                    "quota_rpm": 10000, "quota_daily_tokens": 999_999_999}
    return None


async def require_api_key(request: Request) -> Dict[str, Any]:
    """FastAPI 依赖:必须通过 API Key 鉴权"""
    info = await authenticate_api_key(request)
    if not info:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. "
                   "Use Authorization: Bearer <key> or ?api_key=<key>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return info


# ========== WebUI JWT ==========
_bearer = HTTPBearer(auto_error=False)


def create_jwt_token(subject: str, role: str = "admin",
                     expires_minutes: Optional[int] = None) -> str:
    settings = get_settings()
    expires_minutes = expires_minutes or settings.auth.jwt_expire_minutes
    payload = {
        "sub": subject,
        "role": role,
        "aud": "moa-webui",                # P1-5 加固:绑定 audience
        "iss": "moa-gateway",              # P1-5 加固:绑定 issuer
        "iat": int(time.time()),
        "exp": int(time.time()) + expires_minutes * 60,
    }
    return jwt.encode(payload, settings.auth.jwt_secret, algorithm="HS256")


def decode_jwt_token(token: str) -> Optional[Dict[str, Any]]:
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.auth.jwt_secret,
            algorithms=["HS256"],
            audience="moa-webui",          # P1-5 加固:验证 audience
            issuer="moa-gateway",          # P1-5 加固:验证 issuer
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


async def require_admin(request: Request,
                        credentials = Depends(_bearer)) -> Dict[str, Any]:
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
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )
    return info
