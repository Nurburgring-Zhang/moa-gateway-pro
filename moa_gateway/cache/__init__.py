"""Multi-layer semantic cache system for MoA Gateway Pro.

Architecture:
- L1: Exact match (MD5 hash) — in-memory, fastest
- L2: Semantic match (vector similarity) — in-memory, ~0.95 threshold
- L3: Redis distributed — shared across instances
"""
from ..config import CacheConfig
from .base import CacheBackend, CacheEntry
from .exact import ExactMatchCache
from .manager import CacheManager
from .metrics import CacheMetrics
from .redis_cache import RedisCache
from .semantic import SemanticCache

__all__ = [
    "CacheEntry",
    "CacheBackend",
    "ExactMatchCache",
    "SemanticCache",
    "RedisCache",
    "CacheManager",
    "CacheMetrics",
    "CacheConfig",
]
