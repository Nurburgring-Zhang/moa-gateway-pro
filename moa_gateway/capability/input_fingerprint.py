"""Input Fingerprint - Multi-layer fingerprinting for input deduplication.

Provides four layers of hashing to detect similar inputs at different
abstraction levels:
    1. exact       - raw text hash
    2. normalized  - case/whitespace/punctuation insensitive
    3. structural  - token-type sequence (word/number/punct)
    4. semantic    - top-K high-frequency words

Used for dedup, rate-limiting and caching of repeated prompts.
"""

from __future__ import annotations

import hashlib
import re
import threading
import unicodedata
from collections import Counter

try:
    from typing import Literal
except ImportError:  # pragma: no cover - py<3.8
    pass  # type: ignore

__all__ = [
    "exact_hash",
    "normalized_hash",
    "structural_hash",
    "semantic_hash",
    "InputFingerprint",
    "FingerprintStore",
]

_PUNCT_RE = re.compile(
    r"[\u2000-\u206f\u2e00-\u2e7f"
    r"\u3000-\u303f"  # CJK symbols & punctuation (、。「」『』，。 etc.)
    r"\uff00-\uffef"  # fullwidth forms (，．！etc.)
    r"'!\"#$%&()*+,\-./:;<=>?@\[\\\]^_`{|}~]"
)
_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(
    r"""
    [A-Za-z\u4e00-\u9fff]+          # word (ascii letters or CJK)
  | \d+(?:\.\d+)?                    # number (int or float)
  | [^\sA-Za-z0-9\u4e00-\u9fff]      # punct / other
    """,
    re.VERBOSE,
)


def _sha256(data: str) -> str:
    """Return hex SHA256 digest (64 chars) of utf-8 encoded data."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def exact_hash(text: str) -> str:
    """SHA256 of the raw text. Sensitive to every byte."""
    return _sha256(text or "")


def normalized_hash(text: str) -> str:
    """SHA256 of text lower-cased, whitespace-folded and punctuation-stripped.

    ``"Hello, World!"`` and ``"hello world"`` hash to the same digest;
    ``"你好，世界"`` and ``"你好世界"`` hash to the same digest.

    Implementation: extract every word/number token, lowercase, and
    concatenate them with single ASCII spaces (or none, for consecutive
    CJK runs). This guarantees that punctuation never leaves phantom
    whitespace behind.
    """
    if not text:
        return _sha256("")
    s = unicodedata.normalize("NFKC", text).lower()
    toks = _TOKEN_RE.findall(s)
    if not toks:
        return _sha256("")

    def _is_cjk(t: str) -> bool:
        return t and "\u4e00" <= t[0] <= "\u9fff"

    pieces: list[str] = []
    for t in toks:
        if not (t[0].isalnum() or _is_cjk(t)):
            continue
        if pieces and not (_is_cjk(t) and _is_cjk(pieces[-1])):
            pieces.append(" ")
        pieces.append(t)
    return _sha256("".join(pieces))


def _classify(tok: str) -> str:
    if tok and tok[0].isdigit():
        return "number"
    if all(not c.isalnum() for c in tok):
        return "punct"
    return "word"


def structural_hash(text: str) -> str:
    """SHA256 of the token-type sequence.

    ``"Hello, 123 world!"`` -> ``"word punct number word punct"`` -> hash.
    Preserves order of categories so that "buy 1" and "buy 2" still match,
    while "1 buy" does not.
    """
    if not text:
        return _sha256("")
    toks = _TOKEN_RE.findall(text)
    seq = " ".join(_classify(t) for t in toks)
    return _sha256(seq)


def semantic_hash(text: str, top_k: int = 5) -> str:
    """SHA256 of the top-K most frequent word tokens, in descending frequency.

    Ties are broken alphabetically. The relative ordering of the top-K
    bucket yields a coarse but stable semantic signature.

    Examples:
        >>> semantic_hash("the cat sat on the mat", top_k=2)
    """
    if not text or top_k <= 0:
        return _sha256("")
    toks = _TOKEN_RE.findall(unicodedata.normalize("NFKC", text).lower())
    words = [t for t in toks if t and t[0].isalpha()]
    if not words:
        return _sha256("")
    counter = Counter(words)
    top = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]
    sig = " ".join(f"{w}:{c}" for w, c in top)
    return _sha256(sig)


def _lcs_length(a: list[str], b: list[str]) -> int:
    """Length of the longest common subsequence (iterative DP)."""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, start=1):
            cur[j] = prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1])
        prev = cur
    return prev[-1]


class InputFingerprint:
    """A 4-layer fingerprint of a single text input."""

    LEVELS: tuple[str, ...] = ("exact", "normalized", "structural", "semantic")

    def __init__(self, text: str, top_k: int = 5) -> None:
        self.text: str = text or ""
        self.top_k: int = top_k
        try:
            self.attrs: dict[str, str] = {
                "exact": exact_hash(self.text),
                "normalized": normalized_hash(self.text),
                "structural": structural_hash(self.text),
                "semantic": semantic_hash(self.text, top_k=self.top_k),
            }
        except Exception:
            # Fallback: hashes on empty string are still valid digests.
            empty = _sha256("")
            self.attrs = {
                "exact": empty,
                "normalized": empty,
                "structural": empty,
                "semantic": empty,
            }

    # ---- similarity ----------------------------------------------------
    def similar_to(self, other: InputFingerprint, level: str = "normalized") -> float:
        """Return a similarity in [0, 1] at the requested ``level``."""
        if level not in self.LEVELS:
            raise ValueError(f"unknown level {level!r}; expected one of {self.LEVELS}")
        if level == "exact":
            return 1.0 if self.attrs["exact"] == other.attrs["exact"] else 0.0
        if level == "normalized":
            return 1.0 if self.attrs["normalized"] == other.attrs["normalized"] else 0.0
        if level == "structural":
            return self._structural_similarity(other)
        return self._semantic_similarity(other)

    def _structural_similarity(self, other: InputFingerprint) -> float:
        seq_a = self._structural_sequence()
        seq_b = other._structural_sequence()
        if not seq_a and not seq_b:
            return 1.0
        if not seq_a or not seq_b:
            return 0.0
        if seq_a == seq_b:
            return 1.0
        lcs = _lcs_length(seq_a, seq_b)
        union = len(seq_a) + len(seq_b) - lcs
        return lcs / union if union else 0.0

    def _semantic_similarity(self, other: InputFingerprint) -> float:
        set_a = self._semantic_set()
        set_b = other._semantic_set()
        if not set_a and not set_b:
            return 1.0
        inter = len(set_a & set_b)
        union = len(set_a | set_b)
        return inter / union if union else 0.0

    def _structural_sequence(self) -> list[str]:
        if not self.text:
            return []
        return [_classify(t) for t in _TOKEN_RE.findall(self.text)]

    def _semantic_set(self) -> set:
        if not self.text:
            return set()
        toks = _TOKEN_RE.findall(unicodedata.normalize("NFKC", self.text).lower())
        words = [t for t in toks if t and t[0].isalpha()]
        if not words:
            return set()
        return {w for w, _ in Counter(words).most_common(self.top_k)}

    # ---- dunder / serialization ---------------------------------------
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, InputFingerprint):
            return NotImplemented
        # any one of the 4 layers matching counts as "equal" (collapsing union).
        return any(self.attrs[lv] == other.attrs[lv] for lv in self.LEVELS)

    def __hash__(self) -> int:
        # Use the exact hash so set/dict membership is well-defined.
        return int(self.attrs["exact"], 16)

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict representation."""
        return {
            "text": self.text,
            "top_k": self.top_k,
            "attrs": dict(self.attrs),
        }

    @classmethod
    def from_dict(cls, data: dict) -> InputFingerprint:
        """Inverse of :meth:`to_dict`."""
        fp = cls(data.get("text", ""), top_k=int(data.get("top_k", 5)))
        for k, v in (data.get("attrs") or {}).items():
            if k in fp.attrs:
                fp.attrs[k] = v
        return fp

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"InputFingerprint(exact={self.attrs['exact'][:8]}..., "
            f"normalized={self.attrs['normalized'][:8]}..., "
            f"structural={self.attrs['structural'][:8]}..., "
            f"semantic={self.attrs['semantic'][:8]}...)"
        )


class FingerprintStore:
    """Thread-safe store of input fingerprints with collision queries."""

    def __init__(self, max_size: int = 50000) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        self.max_size = int(max_size)
        self._lock = threading.RLock()
        self._items: list[tuple[InputFingerprint, dict | None]] = []
        self._by_exact: dict[str, int] = {}

    # ---- mutators ------------------------------------------------------
    def add(self, text: str, metadata: dict | None = None) -> InputFingerprint:
        """Add a text to the store and return its fingerprint.

        If the store is full, the oldest entry is evicted (FIFO).
        """
        try:
            fp = InputFingerprint(text)
        except Exception:
            fp = InputFingerprint("")
        with self._lock:
            if len(self._items) >= self.max_size:
                old_fp, _ = self._items.pop(0)
                key = old_fp.attrs["exact"]
                cur = self._by_exact.get(key, 0)
                if cur <= 1:
                    self._by_exact.pop(key, None)
                else:
                    self._by_exact[key] = cur - 1
            self._items.append((fp, metadata))
            self._by_exact[fp.attrs["exact"]] = self._by_exact.get(fp.attrs["exact"], 0) + 1
            return fp

    # ---- queries -------------------------------------------------------
    def size(self) -> int:
        with self._lock:
            return len(self._items)

    def find_collisions(
        self,
        text: str,
        min_levels: int = 2,
    ) -> list[tuple[InputFingerprint, float]]:
        """Find stored entries that match ``text`` on at least ``min_levels`` layers.

        Returns a list of ``(fingerprint, score)`` where ``score`` is the
        fraction of matching layers in [0, 1].
        """
        if not 1 <= min_levels <= 4:
            raise ValueError("min_levels must be in 1..4")
        try:
            target = InputFingerprint(text)
        except Exception:
            return []
        results: list[tuple[InputFingerprint, float]] = []
        with self._lock:
            snapshot = list(self._items)
        for fp, _meta in snapshot:
            try:
                hits = sum(
                    1 for lv in InputFingerprint.LEVELS
                    if fp.attrs[lv] == target.attrs[lv]
                )
            except Exception:
                continue
            if hits >= min_levels:
                results.append((fp, hits / 4.0))
        return results

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._by_exact.clear()
