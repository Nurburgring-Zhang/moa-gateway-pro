"""Cache abstract base class and shared types."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheEntry:
    """Represents a cached response."""

    key: str
    value: Any
    created_at: float
    ttl_seconds: int
    hit_count: int = 0
    similarity: float = 1.0  # 1.0 for exact match
    layer: str = ""  # which cache layer produced this

    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl_seconds


class CacheBackend(ABC):
    """Abstract cache backend interface."""

    @abstractmethod
    async def get(self, key: str) -> CacheEntry | None:
        ...

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        ...

    @abstractmethod
    async def clear(self) -> None:
        ...

    async def size(self) -> int:
        """Return the number of entries (if supported)."""
        return -1
