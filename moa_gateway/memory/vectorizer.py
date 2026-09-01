"""Dense vectorization for hybrid memory recall.

Source: MemoraX Code (https://github.com/memorax-ai/memorax-code, MIT)
performs dense+sparse hybrid search server-side (``k_dense``/``k_sparse`` in
``provider/memorax/adapter.ts``).  This port keeps the same hybrid contract
but computes the dense channel locally, in two tiers:

Tier 1 (preferred) — gateway-internal embedding capability.
    ``moa_gateway.capability.embedding.MockEmbeddingProvider`` is the
    gateway's built-in embedding service (deterministic SHA-256 token-position
    hash embeddings, L2-normalized).  It is *really invoked*: the provider is
    instantiated and ``embed()`` is called for every vectorization.

Tier 2 (degradation fallback) — deterministic local character n-gram hashing.
    Used only when the capability module cannot be imported or raises at call
    time (e.g. the capability package is trimmed from a minimal install).
    Character 2..4-grams are hashed into a fixed-dimension vector with
    sublinear TF weighting, then L2-normalized so cosine similarity is exact.

Degradation boundary (honest statement):
    Neither tier is a trained semantic model.  Tier 1 captures token overlap
    with positional hashing; Tier 2 captures character n-gram overlap (more
    robust to whitespace/tokenization differences, less to word order).  Both
    are fully deterministic (same text -> same vector, across processes),
    which is exactly what idempotent recall tests rely on.  Swapping in a real
    embedding model only requires providing another object with
    ``embed(list[str]) -> list[list[float]]``.
"""

from __future__ import annotations

import hashlib
import logging
import math
import struct
from collections.abc import Sequence

logger = logging.getLogger(__name__)

DEFAULT_DIM = 384


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Real cosine similarity; 0.0 for zero vectors, ValueError on dim mismatch."""
    if len(a) != len(b):
        raise ValueError(f"cosine: dimension mismatch {len(a)} vs {len(b)}")
    dot = norm_a = norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0.0:
        return vector
    return [x / norm for x in vector]


def _char_ngrams(text: str, min_n: int = 2, max_n: int = 4) -> dict[str, int]:
    """Character n-gram frequencies (2..4), whitespace collapsed, lowercased."""
    normalized = "".join(ch if not ch.isspace() else " " for ch in text.lower())
    counts: dict[str, int] = {}
    for n in range(min_n, max_n + 1):
        if len(normalized) < n:
            if normalized:
                counts[normalized] = counts.get(normalized, 0) + 1
            continue
        for i in range(len(normalized) - n + 1):
            gram = normalized[i : i + n]
            counts[gram] = counts.get(gram, 0) + 1
    return counts


def char_ngram_vector(text: str, dim: int = DEFAULT_DIM) -> list[float]:
    """Tier-2 deterministic character n-gram hash vector (L2 normalized).

    Each n-gram is hashed (SHA-256) to a dimension index and a sign; counts
    use sublinear TF (1 + ln c) so repeated grams do not dominate.
    """
    if dim <= 0:
        raise ValueError("dim must be > 0")
    vector = [0.0] * dim
    grams = _char_ngrams(text or "")
    if not grams:
        grams = {"<empty>": 1}
    for gram, count in grams.items():
        digest = hashlib.sha256(gram.encode("utf-8")).digest()
        index = struct.unpack(">I", digest[:4])[0] % dim
        sign = 1.0 if digest[4] & 1 == 0 else -1.0
        vector[index] += sign * (1.0 + math.log(count))
    return _normalize(vector)


class DenseVectorizer:
    """Dense channel vectorizer with capability-first, n-gram-fallback tiers."""

    def __init__(self, dim: int = DEFAULT_DIM):
        self.dim = dim
        self._provider = None
        self._provider_model = ""
        self._provider_failed = False
        self._init_gateway_provider()

    def _init_gateway_provider(self) -> None:
        """Tier 1: bind the gateway-internal embedding capability (real call path)."""
        try:
            from ..capability.embedding import MockEmbeddingProvider

            self._provider = MockEmbeddingProvider(model="memory-dense-v1", dim=self.dim)
            self._provider_model = "gateway-embedding"
        except Exception as exc:  # pragma: no cover - capability trimmed installs
            logger.warning("memory vectorizer: gateway embedding unavailable (%s); "
                           "degrading to char-ngram hashing", exc)
            self._provider = None
            self._provider_failed = True
            self._provider_model = "char-ngram"

    @property
    def backend(self) -> str:
        """Active backend name (observable in /v1/memory/recall responses)."""
        return self._provider_model

    def embed(self, text: str) -> list[float]:
        """Vectorize one text through the active tier."""
        if self._provider is not None and not self._provider_failed:
            try:
                vectors = self._provider.embed([text or ""])
                if vectors and len(vectors[0]) == self.dim:
                    return list(vectors[0])
            except Exception as exc:
                logger.warning(
                    "memory vectorizer: gateway embedding call failed (%s); "
                    "degrading to char-ngram hashing for this and future calls",
                    exc,
                )
                self._provider_failed = True
                self._provider_model = "char-ngram"
        return char_ngram_vector(text, self.dim)

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]
