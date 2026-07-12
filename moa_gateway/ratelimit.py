"""moa_gateway.ratelimit — 限流(滑动窗口 + 每日 token 限额)"""
from __future__ import annotations
import time
import logging
from typing import Dict, Any, Tuple, Optional
from fastapi import HTTPException, status, Request

from .storage import get_storage
from .config import get_settings

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

    def check_and_incr(self, key_info: Dict[str, Any]) -> Tuple[int, int, int, int]:
        """检查并增加计数。返回 (rpm_used, rpm_limit, daily_tokens, daily_token_limit)"""
        if not self.settings.enabled:
            return 0, 0, 0, 0
        key_id = key_info["key_id"]
        bucket = _bucket_id(60)
        # RPM
        used_rpm = self.storage.incr_rpm(key_id, bucket)
        # 清理旧 bucket(超过 5 分钟)
        # 这里 sqlite 不会自动清理,生产可加 cron;暂保留
        if used_rpm > self.settings.per_key_rpm:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: {used_rpm} > {self.settings.per_key_rpm} rpm",
                headers={"Retry-After": "60"}
            )
        return used_rpm, self.settings.per_key_rpm, 0, self.settings.per_key_daily_tokens

    def incr_tokens(self, key_info: Dict[str, Any], tokens: int):
        if not self.settings.enabled or tokens <= 0:
            return
        key_id = key_info["key_id"]
        day = _today()
        cur = self.storage.incr_daily_tokens(key_id, day, tokens)
        if cur > self.settings.per_key_daily_tokens:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily token quota exceeded: {cur} > {self.settings.per_key_daily_tokens}",
            )

    def get_quota(self, key_info: Dict[str, Any]) -> Dict[str, int]:
        key_id = key_info["key_id"]
        day = _today()
        return {
            "daily_tokens_used": self.storage.get_daily_tokens(key_id, day),
            "daily_tokens_limit": self.settings.per_key_daily_tokens,
            "rpm_limit": self.settings.per_key_rpm,
        }


_limiter: Optional[RateLimiter] = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter
