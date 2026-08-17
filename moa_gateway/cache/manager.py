"""Multi-layer cache manager — coordinates L1/L2/L3 with protection."""

from __future__ import annotations

import asyncio
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
        # Single-flight locks keyed by exact cache key — prevents cache
        # stampede (thundering herd) when N identical requests miss at once.
        self._inflight: dict[str, asyncio.Future] = {}
        self._inflight_lock = asyncio.Lock()

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

        Returns cached response dict or None on miss. Hit dicts carry a
        ``mock`` key (True/False/None for legacy entries) so callers can
        replay the explicit-mock labeling (v3.1.1 audit P2-C).
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
            body, mock = self._unwrap(entry.value)
            return {"response": body, "layer": "l1_exact", "similarity": 1.0, "mock": mock}

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
            body, mock = self._unwrap(entry.value)
            return {"response": body, "layer": "l3_redis", "similarity": 1.0, "mock": mock}

        # --- L2: Semantic match ---
        text = SemanticCache.messages_to_text(messages)
        if text:
            # Audit F32: scope by model|strategy|preset so a semantic hit never
            # serves a response produced under a different configuration.
            entry = await self.l2.get(text, scope=self._config_scope(model, **kwargs))
            if entry:
                self.metrics.record_hit("l2_semantic")
                self.metrics.record_lookup_latency((time.time() - t0) * 1000)
                body, mock = self._unwrap(entry.value)
                return {
                    "response": body,
                    "layer": "l2_semantic",
                    "similarity": entry.similarity,
                    "mock": mock,
                }

        # All miss
        self.metrics.record_miss()
        self.metrics.record_lookup_latency((time.time() - t0) * 1000)
        return None

    async def set(
        self, messages: list, model: str, response: Any, *, stream: bool = False,
        mock: bool = False, **kwargs
    ) -> None:
        """Store response in all cache layers.

        ``mock`` records whether the response was produced by the synthetic
        MockProvider so cache replay can re-attach the explicit-mock label
        (v3.1.1 audit P2-C).
        """
        if not self.enabled:
            return

        # Respect skip_streaming config: do not cache streaming responses
        if stream and self._config.skip_streaming:
            logger.debug("Skipping cache store: streaming response (skip_streaming=True)")
            return

        wrapped = self._wrap(response, mock)
        exact_key = ExactMatchCache.compute_key(messages, model, **kwargs)

        # L1 with jitter
        l1_ttl = self._apply_ttl_jitter(self._config.exact_ttl)
        await self.l1.set(exact_key, wrapped, ttl=l1_ttl)

        # L3 with jitter
        l3_ttl = self._apply_ttl_jitter(self._config.exact_ttl)
        await self.l3.set(exact_key, wrapped, ttl=l3_ttl)

        # L2 semantic index (scoped by model|strategy|preset, audit F32)
        text = SemanticCache.messages_to_text(messages)
        if text:
            l2_ttl = self._apply_ttl_jitter(self._config.semantic_ttl)
            await self.l2.set(text, wrapped, ttl=l2_ttl, scope=self._config_scope(model, **kwargs))

    # v3.1.1 audit P2-C: envelope carrying the mock flag next to the body.
    _ENVELOPE_MARKER = "__moa_cache_envelope_v1__"

    @classmethod
    def _wrap(cls, response: Any, mock: bool) -> dict:
        return {cls._ENVELOPE_MARKER: True, "mock": bool(mock), "body": response}

    @classmethod
    def _unwrap(cls, value: Any) -> tuple[Any, bool | None]:
        if isinstance(value, dict) and value.get(cls._ENVELOPE_MARKER) is True:
            return value.get("body"), bool(value.get("mock"))
        return value, None  # legacy entry — label unknown

    @staticmethod
    def _config_scope(model: str, **kwargs) -> str:
        """Cache scope discriminator — responses are only reusable within the
        same model + strategy + preset combination."""
        return f"{model}|{kwargs.get('strategy')}|{kwargs.get('preset')}"

    async def get_or_compute(
        self, messages: list, model: str, compute, *, stream: bool = False, **kwargs
    ) -> Any:
        """Single-flight cache lookup-or-compute.

        On cache miss, only the FIRST caller invokes ``compute()``; subsequent
        identical callers await the same in-flight future, preventing a cache
        stampede (thundering herd) against the upstream provider.
        Returns (value, cache_hit: bool).
        """
        if not self.enabled:
            return await compute(), False

        cached = await self.get(messages, model, **kwargs)
        if cached is not None:
            return cached["response"], True

        exact_key = ExactMatchCache.compute_key(messages, model, **kwargs)
        loop = asyncio.get_event_loop()
        is_producer = False
        fut: Any = None
        async with self._inflight_lock:
            existing = self._inflight.get(exact_key)
            if existing is None:
                is_producer = True
                fut = loop.create_future()
                self._inflight[exact_key] = fut
            else:
                fut = existing

        if is_producer:
            producer_fut = fut  # type: ignore[assignment]
            try:
                result = await compute()
                await self.set(messages, model, result, stream=stream, **kwargs)
                producer_fut.set_result(result)
                return result, False
            except BaseException as e:
                if not producer_fut.done():
                    producer_fut.set_exception(e)
                raise
            finally:
                async with self._inflight_lock:
                    self._inflight.pop(exact_key, None)
        else:
            return await fut, True

    async def set_null(self, messages: list, model: str, **kwargs) -> None:
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
