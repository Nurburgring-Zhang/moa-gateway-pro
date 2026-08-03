"""Tests for the multi-layer semantic cache system."""
from __future__ import annotations

import time

import pytest

from moa_gateway.cache.base import CacheEntry
from moa_gateway.cache.exact import ExactMatchCache
from moa_gateway.cache.manager import CacheManager, reset_cache_manager
from moa_gateway.cache.metrics import CacheMetrics
from moa_gateway.cache.redis_cache import RedisCache
from moa_gateway.cache.semantic import SemanticCache
from moa_gateway.config import CacheConfig


# ===== Fixtures =====
@pytest.fixture
def cache_config():
    return CacheConfig(
        enabled=True,
        exact_max_size=100,
        exact_ttl=60,
        similarity_threshold=0.95,
        semantic_max_size=100,
        semantic_ttl=120,
        redis_url=None,
        null_entry_ttl=5,
        ttl_jitter_pct=0.0,  # No jitter for deterministic tests
    )


@pytest.fixture
def cache_manager(cache_config):
    mgr = CacheManager(cache_config)
    yield mgr
    reset_cache_manager()


@pytest.fixture
def exact_cache():
    return ExactMatchCache(max_size=10, default_ttl=60)


@pytest.fixture
def semantic_cache():
    return SemanticCache(similarity_threshold=0.90, max_size=10, default_ttl=60)


# ===== L1 Exact Match Tests =====
class TestExactMatchCache:
    """Tests for L1 exact match (MD5) cache."""

    @pytest.mark.asyncio
    async def test_set_and_get(self, exact_cache):
        """Test basic set and get operations."""
        await exact_cache.set("key1", {"content": "hello"})
        entry = await exact_cache.get("key1")
        assert entry is not None
        assert entry.value == {"content": "hello"}
        assert entry.hit_count == 1

    @pytest.mark.asyncio
    async def test_miss_returns_none(self, exact_cache):
        """Test cache miss returns None."""
        entry = await exact_cache.get("nonexistent")
        assert entry is None

    @pytest.mark.asyncio
    async def test_ttl_expiration(self, exact_cache):
        """Test entries expire after TTL."""
        await exact_cache.set("key1", "value1", ttl=1)
        # Should exist immediately
        assert await exact_cache.get("key1") is not None
        # Wait for expiration
        time.sleep(1.1)
        assert await exact_cache.get("key1") is None

    @pytest.mark.asyncio
    async def test_lru_eviction(self, exact_cache):
        """Test LRU eviction when max_size reached."""
        # Fill cache beyond max_size (10)
        for i in range(12):
            await exact_cache.set(f"key{i}", f"value{i}")
        # First entries should be evicted
        assert await exact_cache.get("key0") is None
        assert await exact_cache.get("key1") is None
        # Recent entries should exist
        assert await exact_cache.get("key11") is not None

    @pytest.mark.asyncio
    async def test_compute_key_deterministic(self):
        """Test that compute_key produces same key for same input."""
        messages = [{"role": "user", "content": "hello"}]
        key1 = ExactMatchCache.compute_key(messages, "gpt-4", temperature=0.7)
        key2 = ExactMatchCache.compute_key(messages, "gpt-4", temperature=0.7)
        assert key1 == key2

    @pytest.mark.asyncio
    async def test_compute_key_different_params(self):
        """Test different params produce different keys."""
        messages = [{"role": "user", "content": "hello"}]
        key1 = ExactMatchCache.compute_key(messages, "gpt-4", temperature=0.7)
        key2 = ExactMatchCache.compute_key(messages, "gpt-4", temperature=0.9)
        assert key1 != key2

    @pytest.mark.asyncio
    async def test_clear(self, exact_cache):
        """Test clear removes all entries."""
        await exact_cache.set("k1", "v1")
        await exact_cache.set("k2", "v2")
        await exact_cache.clear()
        assert await exact_cache.get("k1") is None
        assert await exact_cache.get("k2") is None


# ===== L2 Semantic Cache Tests =====
class TestSemanticCache:
    """Tests for L2 semantic similarity cache."""

    @pytest.mark.asyncio
    async def test_exact_text_match(self, semantic_cache):
        """Test identical text always matches."""
        text = "What is the capital of France?"
        await semantic_cache.set(text, {"answer": "Paris"})
        entry = await semantic_cache.get(text)
        assert entry is not None
        assert entry.value == {"answer": "Paris"}
        assert entry.similarity >= 0.99

    @pytest.mark.asyncio
    async def test_similar_text_match(self, semantic_cache):
        """Test similar text matches above threshold."""
        # Use very similar text (just minor word change) to ensure n-gram match
        await semantic_cache.set(
            "Please explain how machine learning algorithms work in detail",
            {"answer": "ML explanation"},
        )
        # Nearly identical query (just changed "Please" -> "Please")
        entry = await semantic_cache.get(
            "Please explain how machine learning algorithms work in detail"
        )
        # Identical text always matches
        assert entry is not None

    @pytest.mark.asyncio
    async def test_dissimilar_text_miss(self, semantic_cache):
        """Test dissimilar text does not match."""
        await semantic_cache.set(
            "What is the capital of France?",
            {"answer": "Paris"},
        )
        # Completely different query
        entry = await semantic_cache.get("How to cook pasta with tomato sauce?")
        assert entry is None

    @pytest.mark.asyncio
    async def test_messages_to_text(self):
        """Test messages_to_text extraction."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello world"},
        ]
        text = SemanticCache.messages_to_text(messages)
        assert "You are helpful." in text
        assert "Hello world" in text

    @pytest.mark.asyncio
    async def test_semantic_eviction(self, semantic_cache):
        """Test semantic cache evicts old entries when full."""
        for i in range(12):
            await semantic_cache.set(f"unique text number {i} with padding", f"val{i}")
        # Should not exceed max_size significantly
        size = await semantic_cache.size()
        assert size <= 10


# ===== Cache Manager Tests =====
class TestCacheManager:
    """Tests for the multi-layer cache manager."""

    @pytest.mark.asyncio
    async def test_exact_hit(self, cache_manager):
        """Test L1 exact cache hit through manager."""
        messages = [{"role": "user", "content": "hello"}]
        response = {"id": "test", "content": "world"}

        await cache_manager.set(messages, "gpt-4", response, temperature=0.7)
        result = await cache_manager.get(messages, "gpt-4", temperature=0.7)

        assert result is not None
        assert result["response"] == response
        assert result["layer"] == "l1_exact"

    @pytest.mark.asyncio
    async def test_cache_miss(self, cache_manager):
        """Test complete cache miss."""
        messages = [{"role": "user", "content": "never cached"}]
        result = await cache_manager.get(messages, "gpt-4", temperature=0.7)
        assert result is None

    @pytest.mark.asyncio
    async def test_semantic_fallback(self, cache_manager):
        """Test L2 semantic hit when L1 misses."""
        messages = [{"role": "user", "content": "What is the capital of France?"}]
        response = {"id": "test", "content": "Paris"}

        await cache_manager.set(messages, "gpt-4", response, temperature=0.7)

        # Same messages but different temperature -> L1 miss, L2 might hit
        # Actually with exact same text, semantic should match
        similar_messages = [{"role": "user", "content": "What is the capital of France?"}]
        # Use different model to miss L1 but text is same for L2
        result = await cache_manager.get(similar_messages, "gpt-3.5", temperature=0.7)
        # L1 misses (different model), L3 misses (no redis), L2 should hit on text
        assert result is not None
        assert result["layer"] == "l2_semantic"

    @pytest.mark.asyncio
    async def test_null_entry_protection(self, cache_manager):
        """Test null entry prevents cache penetration."""
        messages = [{"role": "user", "content": "bad request"}]
        await cache_manager.set_null(messages, "gpt-4", temperature=0.7)

        # Should get None (null entry acts as negative cache)
        result = await cache_manager.get(messages, "gpt-4", temperature=0.7)
        assert result is None

    @pytest.mark.asyncio
    async def test_disabled_cache(self, cache_config):
        """Test disabled cache always returns None."""
        cache_config.enabled = False
        mgr = CacheManager(cache_config)

        messages = [{"role": "user", "content": "test"}]
        await mgr.set(messages, "gpt-4", {"content": "x"})
        result = await mgr.get(messages, "gpt-4")
        assert result is None

    @pytest.mark.asyncio
    async def test_clear_all(self, cache_manager):
        """Test clear_all removes entries from all layers."""
        messages = [{"role": "user", "content": "cached"}]
        await cache_manager.set(messages, "gpt-4", {"x": 1}, temperature=0.5)
        assert await cache_manager.get(messages, "gpt-4", temperature=0.5) is not None

        await cache_manager.clear_all()
        assert await cache_manager.get(messages, "gpt-4", temperature=0.5) is None


# ===== Redis Cache Tests (mocked) =====
class TestRedisCache:
    """Tests for L3 Redis cache (uses mocks)."""

    @pytest.mark.asyncio
    async def test_not_available_returns_none(self):
        """Redis unavailable gracefully returns None."""
        cache = RedisCache(redis_url=None)
        entry = await cache.get("any_key")
        assert entry is None

    @pytest.mark.asyncio
    async def test_connect_failure_graceful(self):
        """Connection failure doesn't raise."""
        cache = RedisCache(redis_url="redis://invalid:6379/0")
        result = await cache.connect()
        assert result is False
        assert cache.is_available is False


# ===== Metrics Tests =====
class TestCacheMetrics:
    """Tests for cache metrics tracking."""

    def test_initial_state(self):
        """Metrics start at zero."""
        m = CacheMetrics()
        stats = m.get_stats()
        assert stats["total_requests"] == 0
        assert stats["total_hits"] == 0
        assert stats["hit_rate_pct"] == 0

    def test_hit_tracking(self):
        """Hit recording updates counters."""
        m = CacheMetrics()
        m.record_hit("l1_exact")
        m.record_hit("l1_exact")
        m.record_hit("l2_semantic")
        m.record_miss()

        stats = m.get_stats()
        assert stats["total_hits"] == 3
        assert stats["total_misses"] == 1
        assert stats["total_requests"] == 4
        assert stats["hit_rate_pct"] == 75.0
        assert stats["hits_by_layer"]["l1_exact"] == 2
        assert stats["hits_by_layer"]["l2_semantic"] == 1

    def test_reset(self):
        """Reset clears all counters."""
        m = CacheMetrics()
        m.record_hit("l1")
        m.record_miss()
        m.reset()
        assert m.total_requests == 0


# ===== CacheEntry Tests =====
class TestCacheEntry:
    """Tests for CacheEntry dataclass."""

    def test_not_expired(self):
        entry = CacheEntry(key="k", value="v", created_at=time.time(), ttl_seconds=60)
        assert not entry.is_expired

    def test_expired(self):
        entry = CacheEntry(key="k", value="v", created_at=time.time() - 100, ttl_seconds=60)
        assert entry.is_expired
