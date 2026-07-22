"""L1 Exact Match Cache — MD5 hash based, in-memory LRU."""
from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from typing import Any

from .base import CacheBackend, CacheEntry


class ExactMatchCache(CacheBackend):
    """In-memory exact match cache using MD5 hash of (messages + model + params).

    Uses OrderedDict for LRU eviction.
    """

    def __init__(self, max_size: int = 10000, default_ttl: int = 3600):
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._max_size = max_size
        self._default_ttl = default_ttl

    @staticmethod
    def compute_key(messages: list, model: str, **kwargs) -> str:
        """Compute deterministic cache key from request parameters."""
        payload = json.dumps(
            {
                "messages": messages,
                "model": model,
                "temperature": kwargs.get("temperature", 1.0),
                "max_tokens": kwargs.get("max_tokens"),
                "top_p": kwargs.get("top_p", 1.0),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.md5(payload.encode()).hexdigest()

    async def get(self, key: str) -> CacheEntry | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry.is_expired:
            self._store.pop(key, None)
            return None
        # Move to end (most recently used)
        self._store.move_to_end(key)
        entry.hit_count += 1
        return entry

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        # Evict oldest if at capacity
        while len(self._store) >= self._max_size:
            self._store.popitem(last=False)

        self._store[key] = CacheEntry(
            key=key,
            value=value,
            created_at=time.time(),
            ttl_seconds=ttl or self._default_ttl,
            layer="l1_exact",
        )
        self._store.move_to_end(key)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def clear(self) -> None:
        self._store.clear()

    async def size(self) -> int:
        return len(self._store)
