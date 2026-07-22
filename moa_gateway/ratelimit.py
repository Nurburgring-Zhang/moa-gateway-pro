"""moa_gateway.ratelimit — 限流(滑动窗口 + 每日 token 限额)"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import HTTPException, status

from .config import get_settings
from .storage import get_storage

logger = logging.getLogger(__name__)


def _bucket_id(window_seconds: int = 60) -> str:
    """生成当前分钟桶 key"""
    return str(int(time.time()) // window_seconds)


def _today() -> str:
    return time.strftime("%Y%m%d")


class RateLimiter:
    """限流器:
    - 每 API Key 每分钟 RPM 限制
    - 每 API Key 每日 token 限制
    """

    def __init__(self):
        self.settings = get_settings().ratelimit
        self.storage = get_storage()

    def check_and_incr(self, key_info: dict[str, Any]) -> tuple[int, int, int, int]:
        """检查并增加计数。返回 (rpm_used, rpm_limit, daily_tokens, daily_token_limit)
        修 36: 优先用 per-key quota_rpm (从 key_info 读),fallback 到全局 per_key_rpm。
        修 37: admin JWT 没 key_id,跳过 RPM 限流(quota 999_999 已基本无限)
        """
        if not self.settings.enabled:
            return 0, 0, 0, 0
        # 修 37: admin_jwt/yaml-config 没 key_id,直接返回全局视图
        if not key_info.get("key_id"):
            return 0, self.settings.per_key_rpm, 0, self.settings.per_key_daily_tokens
        key_id = key_info["key_id"]
        # 修 36: per-key 限流 — 用 key 自身的 quota_rpm
        rpm_limit = key_info.get("quota_rpm") or self.settings.per_key_rpm
        daily_limit = key_info.get("quota_daily_tokens") or self.settings.per_key_daily_tokens
        bucket = _bucket_id(60)
        # RPM
        used_rpm = self.storage.incr_rpm(key_id, bucket)
        # 清理旧 bucket(超过 5 分钟)
        # 这里 sqlite 不会自动清理,生产可加 cron;暂保留
        if used_rpm > rpm_limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: {used_rpm} > {rpm_limit} rpm",
                headers={"Retry-After": "60"},
            )
        return used_rpm, rpm_limit, 0, daily_limit

    def incr_tokens(self, key_info: dict[str, Any], tokens: int):
        """修 P1-1: 先检查再 incr,避免超额后 counter 永远卡在 limit+1
        原: 累加 → 判定 → 超 429,但 counter 已被加,用户永久锁死到下一天
        """
        if not self.settings.enabled or tokens <= 0:
            return
        # 修 37: admin_jwt/yaml-config 没 key_id,跳过
        if not key_info.get("key_id"):
            return
        key_id = key_info["key_id"]
        # 修 36: per-key 限流
        daily_limit = key_info.get("quota_daily_tokens") or self.settings.per_key_daily_tokens
        day = _today()
        # 修 P1-1: 先读 current,够才 incr
        current = self.storage.get_daily_tokens(key_id, day)
        if current + tokens > daily_limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily token quota exceeded: {current} + {tokens} > {daily_limit}",
            )
        # 修 P1-1: 检查通过,再累加
        self.storage.incr_daily_tokens(key_id, day, tokens)

    def get_quota(self, key_info: dict[str, Any]) -> dict[str, int]:
        # 修 37: admin_jwt/yaml-config 没 key_id,返回全局视图
        if not key_info.get("key_id"):
            return {
                "daily_tokens_used": 0,
                "daily_tokens_limit": self.settings.per_key_daily_tokens,
                "rpm_limit": self.settings.per_key_rpm,
            }
        key_id = key_info["key_id"]
        # 修 36: per-key 限流
        rpm_limit = key_info.get("quota_rpm") or self.settings.per_key_rpm
        daily_limit = key_info.get("quota_daily_tokens") or self.settings.per_key_daily_tokens
        day = _today()
        return {
            "daily_tokens_used": self.storage.get_daily_tokens(key_id, day),
            "daily_tokens_limit": daily_limit,
            "rpm_limit": rpm_limit,
        }


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter
