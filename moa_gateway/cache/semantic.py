"""L2 Semantic Cache — vector similarity based.

Uses character n-gram TF-IDF vectors for local similarity matching.
Production deployments can replace _text_to_vector with embedding API calls
(e.g., OpenAI text-embedding-3-small, sentence-transformers).
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from .base import CacheBackend, CacheEntry


class SemanticCache(CacheBackend):
    """Semantic similarity cache using cosine similarity on n-gram vectors.

    This is a self-contained implementation that requires no external dependencies.
    For production, replace _text_to_vector with an embedding model API call.
    """

    def __init__(
        self,
        similarity_threshold: float = 0.95,
        max_size: int = 5000,
        default_ttl: int = 86400,
    ):
        self._entries: list[tuple[dict, CacheEntry]] = []
        self._threshold = similarity_threshold
        self._max_size = max_size
        self._default_ttl = default_ttl

    def _text_to_vector(self, text: str) -> dict[str, float]:
        """Convert text to normalized character n-gram vector.

        Uses 3-grams for character-level similarity. The resulting dict
        maps n-gram -> normalized weight (L2 norm = 1).
        """
        text_lower = text.lower().strip()
        if len(text_lower) < 3:
            return {"__short__": 1.0} if text_lower else {}

        ngrams: dict[str, int] = {}
        for i in range(len(text_lower) - 2):
            gram = text_lower[i : i + 3]
            ngrams[gram] = ngrams.get(gram, 0) + 1

        # L2 normalize
        norm = sum(v * v for v in ngrams.values()) ** 0.5
        if norm > 0:
            return {k: v / norm for k, v in ngrams.items()}
        return {}

    def _cosine_similarity(self, vec_a: dict, vec_b: dict) -> float:
        """Cosine similarity between two sparse vectors (already L2-normalized)."""
        if not vec_a or not vec_b:
            return 0.0
        # Since both are normalized, dot product = cosine similarity
        common_keys = set(vec_a.keys()) & set(vec_b.keys())
        if not common_keys:
            return 0.0
        return sum(vec_a[k] * vec_b[k] for k in common_keys)

    @staticmethod
    def messages_to_text(messages: list) -> str:
        """Extract text content from message list."""
        return " ".join(
            m.get("content", "") for m in messages if m.get("content")
        )

    async def get(self, key: str) -> CacheEntry | None:
        """Find best semantic match above threshold.

        Here `key` is the raw text to match against (not a hash).
        """
        query_vec = self._text_to_vector(key)
        if not query_vec:
            return None

        best_match: CacheEntry | None = None
        best_sim = 0.0

        # Clean expired entries during scan
        active: list[tuple[dict, CacheEntry]] = []
        for vec, entry in self._entries:
            if entry.is_expired:
                continue
            active.append((vec, entry))
            sim = self._cosine_similarity(query_vec, vec)
            if sim >= self._threshold and sim > best_sim:
                best_sim = sim
                best_match = entry

        self._entries = active

        if best_match:
            best_match.hit_count += 1
            best_match.similarity = best_sim
            return best_match
        return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store entry with vector index. `key` is the raw text."""
        if len(self._entries) >= self._max_size:
            # Evict oldest 25%
            self._entries.sort(key=lambda x: x[1].created_at)
            cutoff = len(self._entries) // 4
            self._entries = self._entries[cutoff:]

        vec = self._text_to_vector(key)
        if not vec:
            return

        entry = CacheEntry(
            key=hashlib.md5(key.encode()).hexdigest(),
            value=value,
            created_at=time.time(),
            ttl_seconds=ttl or self._default_ttl,
            layer="l2_semantic",
        )
        self._entries.append((vec, entry))

    async def delete(self, key: str) -> None:
        target_hash = hashlib.md5(key.encode()).hexdigest()
        self._entries = [
            (v, e) for v, e in self._entries if e.key != target_hash
        ]

    async def clear(self) -> None:
        self._entries.clear()

    async def size(self) -> int:
        return len(self._entries)
