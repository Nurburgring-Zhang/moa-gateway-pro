"""Multi-layer cache manager — coordinates L1/L2/L3 with protection."""
from __future__ import annotations

import logging
import random
import time
from typing import Any

from ..config import CacheConfig
from .exact import ExactMatchCache
from .metrics import CacheMetrics
from .redis_cache import RedisCache
from .semantic import SemanticCache

logger = logging.getLogger(__name__)

# Sentinel for null-entry protection (cache penetration guard)
_NULL_SENTINEL = "__NULL_ENTRY__"


class CacheManager:
    """Orchestrates multi-layer cache lookups and stores.

    Lookup order: L1 (exact) -> L3 (Redis exact) -> L2 (semantic)
    Store: writes to all layers simultaneously.

    Protection:
    - Null entries (short TTL) prevent cache penetration
    - TTL jitter prevents cache avalanche (thundering herd)
    """

    def __init__(self, config: CacheConfig | None = None):
        self._config = config or CacheConfig()
        self.enabled = self._config.enabled

        self.l1 = ExactMatchCache(
            max_size=self._config.exact_max_size,
            default_ttl=self._config.exact_ttl,
        )
        self.l2 = SemanticCache(
            similarity_threshold=self._config.similarity_threshold,
            max_size=self._config.semantic_max_size,
            default_ttl=self._config.semantic_ttl,
        )
        self.l3 = RedisCache(
            redis_url=self._config.redis_url,
            prefix=self._config.redis_prefix,
        )
        self.metrics = CacheMetrics()

    async def initialize(self) -> None:
        """Initialize connections (Redis, etc.)."""
        if not self.enabled:
            logger.info("Cache system disabled by config")
            return
        redis_ok = await self.l3.connect()
        if redis_ok:
            logger.info("Cache system initialized: L1(exact) + L2(semantic) + L3(Redis)")
        else:
            logger.info("Cache system initialized: L1(exact) + L2(semantic) [Redis unavailable]")

    async def shutdown(self) -> None:
        """Cleanup on app shutdown."""
        await self.l3.disconnect()

    def _apply_ttl_jitter(self, ttl: int) -> int:
        """Apply random jitter to TTL to prevent cache avalanche."""
        jitter_pct = self._config.ttl_jitter_pct
        if jitter_pct <= 0:
            return ttl
        delta = int(ttl * jitter_pct)
        return ttl + random.randint(-delta, delta)

    async def get(  # noqa: PLR0911
        self, messages: list, model: str, **kwargs
    ) -> dict | None:
        """Multi-layer cache lookup.

        Returns cached response dict or None on miss.
        """
        if not self.enabled:
            return None

        t0 = time.time()
        exact_key = ExactMatchCache.compute_key(messages, model, **kwargs)

        # --- L1: Exact match (in-memory) ---
        entry = await self.l1.get(exact_key)
        if entry:
            if entry.value == _NULL_SENTINEL:
                # Null entry guard — treat as miss but don't hit upstream
                self.metrics.record_miss()
                return None
            self.metrics.record_hit("l1_exact")
            self.metrics.record_lookup_latency((time.time() - t0) * 1000)
            return {"response": entry.value, "layer": "l1_exact", "similarity": 1.0}

        # --- L3: Redis (distributed exact match) ---
        entry = await self.l3.get(exact_key)
        if entry:
            if entry.value == _NULL_SENTINEL:
                self.metrics.record_miss()
                return None
            self.metrics.record_hit("l3_redis")
            # Backfill L1
            await self.l1.set(exact_key, entry.value, entry.ttl_seconds)
            self.metrics.record_lookup_latency((time.time() - t0) * 1000)
            return {"response": entry.value, "layer": "l3_redis", "similarity": 1.0}

        # --- L2: Semantic match ---
        text = SemanticCache.messages_to_text(messages)
        if text:
            entry = await self.l2.get(text)
            if entry:
                self.metrics.record_hit("l2_semantic")
                self.metrics.record_lookup_latency((time.time() - t0) * 1000)
                return {
                    "response": entry.value,
                    "layer": "l2_semantic",
                    "similarity": entry.similarity,
                }

        # All miss
        self.metrics.record_miss()
        self.metrics.record_lookup_latency((time.time() - t0) * 1000)
        return None

    async def set(
        self, messages: list, model: str, response: Any, **kwargs
    ) -> None:
        """Store response in all cache layers."""
        if not self.enabled:
            return

        exact_key = ExactMatchCache.compute_key(messages, model, **kwargs)

        # L1 with jitter
        l1_ttl = self._apply_ttl_jitter(self._config.exact_ttl)
        await self.l1.set(exact_key, response, ttl=l1_ttl)

        # L3 with jitter
        l3_ttl = self._apply_ttl_jitter(self._config.exact_ttl)
        await self.l3.set(exact_key, response, ttl=l3_ttl)

        # L2 semantic index
        text = SemanticCache.messages_to_text(messages)
        if text:
            l2_ttl = self._apply_ttl_jitter(self._config.semantic_ttl)
            await self.l2.set(text, response, ttl=l2_ttl)

    async def set_null(self, messages: list, model: str, **kwargs) -> None:
        """Store null entry to prevent cache penetration.

        When upstream returns error or empty, cache a short-lived null entry
        so repeated identical bad requests don't hammer upstream.
        """
        if not self.enabled:
            return
        exact_key = ExactMatchCache.compute_key(messages, model, **kwargs)
        await self.l1.set(exact_key, _NULL_SENTINEL, ttl=self._config.null_entry_ttl)

    async def invalidate(self, messages: list, model: str, **kwargs) -> None:
        """Invalidate cache entry across all layers."""
        exact_key = ExactMatchCache.compute_key(messages, model, **kwargs)
        await self.l1.delete(exact_key)
        await self.l3.delete(exact_key)
        text = SemanticCache.messages_to_text(messages)
        if text:
            await self.l2.delete(text)

    async def clear_all(self) -> None:
        """Clear all cache layers."""
        await self.l1.clear()
        await self.l2.clear()
        await self.l3.clear()
        self.metrics.reset()
        logger.info("All cache layers cleared")

    def get_stats(self) -> dict:
        """Return combined cache statistics."""
        return self.metrics.get_stats()

    def get_config(self) -> dict:
        """Return current cache configuration."""
        return self._config.model_dump()


# ===== Singleton =====
_cache_manager: CacheManager | None = None


def get_cache_manager() -> CacheManager:
    """Get or create the global CacheManager singleton."""
    global _cache_manager  # noqa: PLW0603
    if _cache_manager is None:
        from ..config import get_settings  # noqa: PLC0415

        settings = get_settings()
        cache_cfg = getattr(settings, "cache", None)
        if cache_cfg and isinstance(cache_cfg, CacheConfig):
            config = cache_cfg
        elif cache_cfg and hasattr(cache_cfg, "model_dump"):
            config = CacheConfig(**cache_cfg.model_dump())
        elif isinstance(cache_cfg, dict):
            config = CacheConfig(**cache_cfg)
        else:
            config = CacheConfig()
        _cache_manager = CacheManager(config)
    return _cache_manager


def reset_cache_manager() -> None:
    """Reset singleton (for testing)."""
    global _cache_manager  # noqa: PLW0603
    _cache_manager = None
