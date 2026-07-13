"""RAG keyword retrieval capability.

Provides a lightweight, fully-local RAG-style keyword search over an in-memory
corpus. Combines Jaccard overlap with a token-frequency-weighted term score,
ranks candidates by their combined score, and caches results in SQLite for a
configurable TTL window.

The module never calls an LLM; it only performs deterministic text
pre-processing and arithmetic scoring. All public functions swallow internal
errors and return an empty result list as a safety fallback.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import string
import threading
import time
from typing import Any, Dict, List, Tuple

__all__ = ["rag_search", "clear_cache", "set_cache_db_path"]

_STOP_WORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "if", "then", "else", "when",
        "at", "by", "for", "from", "in", "into", "of", "on", "out", "over",
        "to", "up", "with", "as", "is", "are", "was", "were", "be", "been",
        "being", "do", "does", "did", "doing", "have", "has", "had", "having",
        "this", "that", "these", "those", "i", "you", "he", "she", "it", "we",
        "they", "me", "him", "her", "us", "them", "my", "your", "his", "its",
        "our", "their", "what", "which", "who", "whom", "not", "no", "so",
        "than", "too", "very", "can", "will", "just", "should", "now",
    }
)

_PUNCT_TABLE = str.maketrans("", "", string.punctuation)
_TOKEN_RE = re.compile(r"[\w]+", flags=re.UNICODE)

_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "rag_cache.sqlite3"
)

_db_lock = threading.Lock()
_db_path = _DEFAULT_DB_PATH


def set_cache_db_path(path: str) -> None:
    """Override the SQLite cache file path. Useful for tests and isolation."""
    global _db_path
    _db_path = path


def clear_cache() -> None:
    """Remove every row from the rag_cache table. Errors are swallowed."""
    try:
        with _db_lock:
            conn = _get_conn()
            try:
                conn.execute("DELETE FROM rag_cache")
                conn.commit()
            finally:
                conn.close()
    except Exception:
        return


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path, timeout=5.0, isolation_level=None)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_cache (
            query_hash TEXT PRIMARY KEY,
            results_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    return conn


def _tokenize(text: str) -> List[str]:
    """Lowercase, strip punctuation, drop stop-words, and return tokens."""
    if not text:
        return []
    lowered = text.lower()
    raw_tokens = _TOKEN_RE.findall(lowered)
    return [t for t in raw_tokens if t and t not in _STOP_WORDS]


def _term_freqs(tokens: List[str]) -> Dict[str, int]:
    """Build a term-frequency map from a token list."""
    freq: Dict[str, int] = {}
    for tok in tokens:
        freq[tok] = freq.get(tok, 0) + 1
    return freq


def _hash_query(query: str, max_results: int) -> str:
    h = hashlib.sha256()
    h.update(query.lower().encode("utf-8"))
    h.update(b"|")
    h.update(str(max_results).encode("utf-8"))
    return h.hexdigest()


def _score(query_tokens: List[str], query_freq: Dict[str, int],
           doc_tokens: List[str], doc_freq: Dict[str, int]) -> float:
    """Combine Jaccard overlap with a term-frequency-weighted overlap score."""
    if not query_tokens or not doc_tokens:
        return 0.0

    qset = set(query_tokens)
    dset = set(doc_tokens)
    intersection = qset & dset
    union = qset | dset
    if not union:
        return 0.0

    jaccard = len(intersection) / len(union)

    weighted_overlap = 0.0
    weight_total = 0.0
    for term in intersection:
        qf = query_freq.get(term, 0)
        df = doc_freq.get(term, 0)
        weighted_overlap += (qf + 1) * (df + 1)
        weight_total += (qf + 1) * (qf + 1)
    weighted = weighted_overlap / weight_total if weight_total else 0.0

    return 0.5 * jaccard + 0.5 * weighted


def _rank(corpus: List[Dict[str, str]], query_tokens: List[str],
          max_results: int) -> List[Dict[str, Any]]:
    query_freq = _term_freqs(query_tokens)
    scored: List[Tuple[float, Dict[str, str]]] = []
    for item in corpus:
        try:
            text = item.get("text", "") or ""
            doc_tokens = _tokenize(text)
            doc_freq = _term_freqs(doc_tokens)
            s = _score(query_tokens, query_freq, doc_tokens, doc_freq)
        except Exception:
            s = 0.0
        if s > 0.0:
            scored.append((s, item))

    scored.sort(key=lambda pair: (-pair[0], pair[1].get("id", "")))

    results: List[Dict[str, Any]] = []
    for score_val, item in scored[:max_results]:
        tags = item.get("tags", [])
        if not isinstance(tags, list):
            try:
                tags = list(tags)
            except Exception:
                tags = []
        results.append(
            {
                "id": item.get("id", ""),
                "text": item.get("text", ""),
                "score": float(score_val),
                "tags": tags,
            }
        )
    return results


def _cache_get(query_hash: str, ttl_hours: int) -> List[Dict[str, Any]]:
    try:
        ttl_seconds = max(int(ttl_hours), 0) * 3600
        with _db_lock:
            conn = _get_conn()
            try:
                row = conn.execute(
                    "SELECT results_json, created_at FROM rag_cache "
                    "WHERE query_hash = ?",
                    (query_hash,),
                ).fetchone()
            finally:
                conn.close()
        if not row:
            return []
        results_json, created_at = row
        if (time.time() - float(created_at)) > ttl_seconds:
            return []
        cached = json.loads(results_json)
        if isinstance(cached, list):
            return cached
        return []
    except Exception:
        return []


def _cache_put(query_hash: str, results: List[Dict[str, Any]]) -> None:
    try:
        payload = json.dumps(results, ensure_ascii=False)
        with _db_lock:
            conn = _get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO rag_cache "
                    "(query_hash, results_json, created_at) VALUES (?, ?, ?)",
                    (query_hash, payload, time.time()),
                )
            finally:
                conn.close()
    except Exception:
        return


def rag_search(
    query: str,
    corpus: List[Dict[str, str]],
    max_results: int = 3,
    ttl_hours: int = 24,
) -> List[Dict[str, Any]]:
    """Keyword-based RAG retrieval over a local corpus.

    For each item in ``corpus`` (which must be dicts containing ``id``,
    ``text``, and optionally ``tags``), compute a Jaccard + token-frequency
    overlap score between ``query`` and the item's ``text``. Results are
    returned in descending score order, capped at ``max_results``.

    Identical queries within ``ttl_hours`` are served from a SQLite cache at
    ``_db_path``; the cache is transparently invalidated when stale.

    Parameters
    ----------
    query:
        The natural-language query. May be empty (returns ``[]``).
    corpus:
        List of dicts shaped like ``{"id": str, "text": str, "tags": list}``.
        Non-dict items are ignored without raising.
    max_results:
        Maximum number of hits to return. Values ``<= 0`` yield ``[]``.
    ttl_hours:
        Cache lifetime in hours. Non-positive values disable cache reads but
        still write fresh results.

    Returns
    -------
    list of dict
        Each entry has the form
        ``{"id": str, "text": str, "score": float, "tags": list}`` sorted by
        ``score`` descending. Any internal error degrades to ``[]``.
    """
    try:
        if not isinstance(query, str):
            return []
        if not isinstance(corpus, list):
            return []
        if not isinstance(max_results, int) or max_results <= 0:
            return []

        query_tokens = _tokenize(query)
        query_hash = _hash_query(query, max_results)

        if ttl_hours > 0 and query_tokens:
            cached = _cache_get(query_hash, ttl_hours)
            if cached:
                return cached

        results = _rank(corpus, query_tokens, max_results)

        if ttl_hours > 0 and query_tokens:
            _cache_put(query_hash, results)

        return results
    except Exception:
        return []
