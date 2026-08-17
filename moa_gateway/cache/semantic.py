"""L2 Semantic Cache — vector similarity based.

Uses character n-gram TF-IDF vectors for local similarity matching.
Simhash-based pre-filtering reduces linear scan from O(N) to O(bucket_size).
Production deployments can replace _text_to_vector with embedding API calls
(e.g., OpenAI text-embedding-3-small, sentence-transformers).
"""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict
from typing import Any

from .base import CacheBackend, CacheEntry

_SIMHASH_BITS = 64
_SIMHASH_BANDS = 4
_BAND_SIZE = _SIMHASH_BITS // _SIMHASH_BANDS


class SemanticCache(CacheBackend):
    """Semantic similarity cache using cosine similarity on n-gram vectors.

    Uses simhash locality-sensitive hashing to pre-filter candidates,
    reducing full cosine comparisons from O(N) to O(bucket_size).
    """

    def __init__(
        self,
        similarity_threshold: float = 0.95,
        max_size: int = 5000,
        default_ttl: int = 86400,
    ):
        self._entries: list[tuple[dict, int, CacheEntry]] = []  # (vec, simhash, entry)
        self._bands: list[dict[int, list[int]]] = [
            defaultdict(list) for _ in range(_SIMHASH_BANDS)
        ]
        self._threshold = similarity_threshold
        self._max_size = max_size
        self._default_ttl = default_ttl

    def _text_to_vector(self, text: str) -> dict[str, float]:
        """Convert text to normalized character n-gram vector."""
        text_lower = text.lower().strip()
        if len(text_lower) < 3:
            return {"__short__": 1.0} if text_lower else {}

        ngrams: dict[str, int] = {}
        for i in range(len(text_lower) - 2):
            gram = text_lower[i : i + 3]
            ngrams[gram] = ngrams.get(gram, 0) + 1

        norm = sum(v * v for v in ngrams.values()) ** 0.5
        if norm > 0:
            return {k: v / norm for k, v in ngrams.items()}
        return {}

    @staticmethod
    def _compute_simhash(vec: dict[str, float]) -> int:
        """Compute 64-bit simhash from a weighted feature vector."""
        v = [0.0] * _SIMHASH_BITS
        for feature, weight in vec.items():
            h = int(hashlib.md5(feature.encode()).hexdigest(), 16)
            for i in range(_SIMHASH_BITS):
                if h & (1 << i):
                    v[i] += weight
                else:
                    v[i] -= weight
        fingerprint = 0
        for i in range(_SIMHASH_BITS):
            if v[i] > 0:
                fingerprint |= 1 << i
        return fingerprint

    def _get_band_keys(self, fingerprint: int) -> list[int]:
        """Split simhash into band keys for LSH bucketing."""
        mask = (1 << _BAND_SIZE) - 1
        return [(fingerprint >> (i * _BAND_SIZE)) & mask for i in range(_SIMHASH_BANDS)]

    def _cosine_similarity(self, vec_a: dict, vec_b: dict) -> float:
        """Cosine similarity between two sparse vectors (already L2-normalized)."""
        if not vec_a or not vec_b:
            return 0.0
        common_keys = set(vec_a.keys()) & set(vec_b.keys())
        if not common_keys:
            return 0.0
        return sum(vec_a[k] * vec_b[k] for k in common_keys)  # type: ignore[no-any-return]

    @staticmethod
    def messages_to_text(messages: list) -> str:
        """Extract text content from message list."""
        return " ".join(m.get("content", "") for m in messages if m.get("content"))

    def _get_candidates(self, fingerprint: int) -> set[int]:
        """Get candidate indices from LSH bands."""
        band_keys = self._get_band_keys(fingerprint)
        candidates: set[int] = set()
        for band_idx, key in enumerate(band_keys):
            candidates.update(self._bands[band_idx].get(key, []))
        return candidates

    async def get(self, key: str, scope: str = "") -> CacheEntry | None:
        """Find best semantic match above threshold using simhash pre-filtering.

        ``scope`` (audit F32): only entries stored under the SAME scope
        (model|strategy|preset) are eligible — a semantically-similar question
        asked under a different model/strategy must not hit this cache.
        """
        query_vec = self._text_to_vector(key)
        if not query_vec:
            return None

        query_hash = self._compute_simhash(query_vec)
        candidate_indices = self._get_candidates(query_hash)

        # If few entries, just scan all (simhash overhead not worth it)
        if len(self._entries) <= 100:
            candidate_indices = set(range(len(self._entries)))

        best_match: CacheEntry | None = None
        best_sim = 0.0
        expired_indices: list[int] = []

        for idx in candidate_indices:
            if idx >= len(self._entries):
                continue
            vec, _, entry = self._entries[idx]
            if entry.is_expired:
                expired_indices.append(idx)
                continue
            # Scope isolation: never serve a different model/strategy's response.
            if entry.scope != scope:
                continue
            sim = self._cosine_similarity(query_vec, vec)
            if sim >= self._threshold and sim > best_sim:
                best_sim = sim
                best_match = entry

        # Lazy expiry cleanup (only when >10% expired in scanned set)
        if expired_indices and len(expired_indices) > len(self._entries) * 0.1:
            self._rebuild_index()

        if best_match:
            best_match.hit_count += 1
            best_match.similarity = best_sim
            return best_match
        return None

    def _rebuild_index(self) -> None:
        """Remove expired entries and rebuild band index."""
        active = [(v, h, e) for v, h, e in self._entries if not e.is_expired]
        self._entries = active
        self._bands = [defaultdict(list) for _ in range(_SIMHASH_BANDS)]
        for idx, (_, fingerprint, _) in enumerate(self._entries):
            band_keys = self._get_band_keys(fingerprint)
            for band_idx, bk in enumerate(band_keys):
                self._bands[band_idx][bk].append(idx)

    async def set(self, key: str, value: Any, ttl: int | None = None, scope: str = "") -> None:
        """Store entry with vector and simhash index (scoped by config)."""
        if len(self._entries) >= self._max_size:
            self._entries.sort(key=lambda x: x[2].created_at)
            cutoff = len(self._entries) // 4
            self._entries = self._entries[cutoff:]
            self._rebuild_index()

        vec = self._text_to_vector(key)
        if not vec:
            return

        fingerprint = self._compute_simhash(vec)
        entry = CacheEntry(
            key=hashlib.md5(key.encode()).hexdigest(),
            value=value,
            created_at=time.time(),
            ttl_seconds=ttl or self._default_ttl,
            layer="l2_semantic",
            scope=scope,
        )

        idx = len(self._entries)
        self._entries.append((vec, fingerprint, entry))

        band_keys = self._get_band_keys(fingerprint)
        for band_idx, bk in enumerate(band_keys):
            self._bands[band_idx][bk].append(idx)

    async def delete(self, key: str) -> None:
        target_hash = hashlib.md5(key.encode()).hexdigest()
        self._entries = [(v, h, e) for v, h, e in self._entries if e.key != target_hash]
        self._rebuild_index()

    async def clear(self) -> None:
        self._entries.clear()
        self._bands = [defaultdict(list) for _ in range(_SIMHASH_BANDS)]

    async def size(self) -> int:
        return len(self._entries)
