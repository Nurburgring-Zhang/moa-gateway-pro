"""L3 Redis Distributed Cache — shared across gateway instances.

Gracefully degrades when Redis is unavailable.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from .base import CacheBackend, CacheEntry

logger = logging.getLogger(__name__)


class RedisCache(CacheBackend):
    """Redis-backed cache for distributed deployments.

    Falls back to no-op if Redis is not available or not configured.
    """

    def __init__(self, redis_url: str | None = None, prefix: str = "moa:cache:"):
        self._url = redis_url or os.getenv("REDIS_URL") or ""
        self._prefix = prefix
        self._redis: Any = None
        self._available = False

    @property
    def is_available(self) -> bool:
        return self._available

    async def connect(self) -> bool:
        """Try to connect to Redis. Returns True if successful."""
        if not self._url:
            logger.info("Redis URL not configured, L3 cache disabled")
            return False
        try:
            import redis.asyncio as aioredis  # noqa: PLC0415

            self._redis = aioredis.from_url(
                self._url, decode_responses=True, socket_timeout=5
            )
            await self._redis.ping()
            self._available = True
            logger.info("Redis cache connected: %s", self._url.split("@")[-1])
            return True
        except ImportError:
            logger.info("redis package not installed, L3 cache disabled")
            return False
        except Exception as e:
            logger.warning("Redis connection failed: %s", e)
            self._redis = None
            self._available = False
            return False

    async def disconnect(self):
        """Close Redis connection."""
        if self._redis:
            import contextlib  # noqa: PLC0415

            with contextlib.suppress(Exception):
                await self._redis.aclose()
            self._redis = None
            self._available = False

    async def get(self, key: str) -> CacheEntry | None:
        if not self._available or not self._redis:
            return None
        try:
            data = await self._redis.get(f"{self._prefix}{key}")
            if data:
                obj = json.loads(data)
                return CacheEntry(
                    key=obj["key"],
                    value=obj["value"],
                    created_at=obj["created_at"],
                    ttl_seconds=obj["ttl_seconds"],
                    hit_count=obj.get("hit_count", 0),
                    similarity=obj.get("similarity", 1.0),
                    layer="l3_redis",
                )
        except Exception as e:
            logger.debug("Redis get error: %s", e)
        return None

    async def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        if not self._available or not self._redis:
            return
        try:
            entry_data = {
                "key": key,
                "value": value,
                "created_at": time.time(),
                "ttl_seconds": ttl,
                "hit_count": 0,
                "similarity": 1.0,
            }
            await self._redis.setex(
                f"{self._prefix}{key}",
                ttl,
                json.dumps(entry_data, default=str, ensure_ascii=False),
            )
        except Exception as e:
            logger.debug("Redis set error: %s", e)

    async def delete(self, key: str) -> None:
        if not self._available or not self._redis:
            return
        try:
            await self._redis.delete(f"{self._prefix}{key}")
        except Exception as e:
            logger.debug("Redis delete error: %s", e)

    async def clear(self) -> None:
        if not self._available or not self._redis:
            return
        try:
            cursor = 0
            while True:
                cursor, keys = await self._redis.scan(
                    cursor, match=f"{self._prefix}*", count=100
                )
                if keys:
                    await self._redis.delete(*keys)
                if cursor == 0:
                    break
        except Exception as e:
            logger.debug("Redis clear error: %s", e)

    async def size(self) -> int:
        if not self._available or not self._redis:
            return 0
        try:
            cursor = 0
            count = 0
            while True:
                cursor, keys = await self._redis.scan(
                    cursor, match=f"{self._prefix}*", count=100
                )
                count += len(keys)
                if cursor == 0:
                    break
            return count
        except Exception:
            return -1
