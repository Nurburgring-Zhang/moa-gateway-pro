"""Hybrid memory recall recipe — ported from MemoraX Code.

Source: MemoraX Code (https://github.com/memorax-ai/memorax-code, MIT):

- search payload shape (``query``/``user_id``/``top_k``/``k_dense``/
  ``k_sparse``/``min_semantic_similarity``) from
  ``provider/memorax/adapter.ts::buildMemoraxSearchPayload``;
- ``<memories>`` XML rendering (per-``memory_type`` ``<facts>`` buckets,
  item-line format, escaping, per-item and total char truncation) from
  ``provider/memorax/adapter.ts::renderMemoraxContextBlocks``;
- retrieval skip reasons (``disabled`` / ``prompt_missing`` /
  ``control_command`` / ``empty_context``) from
  ``memory/automatic-retrieval.ts``.

MemoraX delegates the actual dense+sparse search to the external MemoraX
service; this port executes the same recipe locally against the SQLite item
store:

- dense channel : cosine similarity of query/item vectors (vectorizer tiers
  documented in ``vectorizer.py``);
- sparse channel: TF-weighted keyword scoring with IDF across the user's
  corpus;
- fusion        : candidate pool = top ``k_dense`` by dense ∪ top
  ``k_sparse`` by sparse, ranked by the mean of the two channel scores;
- gate          : ``min_semantic_similarity`` (settings ``min_score``) is
  applied to the dense (semantic) score, matching the MemoraX parameter name;
- output        : top_k items rendered into the ``<memories>`` XML block,
  truncated to ``max_item_chars`` per item and ``max_context_chars`` total.
"""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import MemoryConfig
from .classifier import UNCLASSIFIED
from .store import MemoryStore
from .vectorizer import DenseVectorizer, cosine_similarity

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]", re.UNICODE)
_CONTROL_COMMAND_RE = re.compile(r"^(?:memorax-code|memorax-cli)(?:\s|$)", re.IGNORECASE)

# Dense/sparse fusion weights (equal blend; both scores are in [0, 1]).
_DENSE_WEIGHT = 0.5
_SPARSE_WEIGHT = 0.5


@dataclass
class RecallResult:
    retrieved: bool
    context: str | None = None
    skip_reason: str | None = None
    item_count: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0
    backend: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "retrieved": self.retrieved,
            "item_count": self.item_count,
            "latency_ms": round(self.latency_ms, 2),
            "backend": self.backend,
        }
        if self.context is not None:
            payload["context"] = self.context
        if self.skip_reason:
            payload["skip_reason"] = self.skip_reason
        if self.items:
            payload["items"] = self.items
        return payload


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens + single CJK characters (for zh content)."""
    return _TOKEN_RE.findall((text or "").lower())


def _sparse_scores(
    query_tokens: list[str], items_tokens: list[list[str]]
) -> list[float]:
    """TF-weighted keyword scores in ~[0,1] for each item against the query.

    score(item) = Σ_t idf(t) * tf(t, item) / Σ_t idf(t),  t ∈ unique query
    tokens.  tf uses sublinear log scaling; idf is smoothed corpus idf.
    """
    n_docs = len(items_tokens)
    if not query_tokens or n_docs == 0:
        return [0.0] * n_docs
    unique_query = list(dict.fromkeys(query_tokens))
    doc_freq: dict[str, int] = {}
    for tokens in items_tokens:
        for token in set(tokens):
            if token in unique_query:
                doc_freq[token] = doc_freq.get(token, 0) + 1
    idf = {
        token: math.log(1.0 + n_docs / (1.0 + doc_freq.get(token, 0)))
        for token in unique_query
    }
    total_idf = sum(idf.values())
    if total_idf <= 0:
        return [0.0] * n_docs
    scores: list[float] = []
    for tokens in items_tokens:
        if not tokens:
            scores.append(0.0)
            continue
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        score = 0.0
        for token in unique_query:
            count = counts.get(token, 0)
            if count:
                tf = 1.0 + math.log(count)
                score += idf[token] * tf
        scores.append(min(1.0, score / total_idf))
    return scores


def _sanitize_inline(value: str) -> str:
    return re.sub(r"\s{2,}", " ", re.sub(r"\r?\n+", " ", value)).strip()


def _escape_text(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _escape_attribute(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def _format_time(epoch: float | None) -> str:
    if not epoch:
        return ""
    try:
        tm = time.localtime(float(epoch))
    except (OverflowError, OSError, ValueError):
        return ""
    return time.strftime("%Y-%m-%d %H:%M", tm)


def render_memories_xml(
    rendered: list[tuple[str, str, float | None]],
    memory_type_order: list[str],
    max_item_chars: int,
    max_context_chars: int,
) -> str:
    """Render memory items into MemoraX's ``<memories>`` XML block.

    ``rendered`` entries are ``(memory_type, text, updated_at_epoch|None)``.
    Buckets follow ``memory_type_order``; types present in the items but
    absent from the configured order (e.g. ``unclassified``) are appended in
    first-seen order, exactly like MemoraX's bucket map.
    """
    lines_by_type: dict[str, list[str]] = {}
    ordered: list[str] = []
    for memory_type in memory_type_order:
        lines_by_type[memory_type] = []
        ordered.append(memory_type)
    for memory_type, text, updated_at in rendered:
        sanitized = _sanitize_inline(text)
        if not sanitized:
            continue
        time_prefix = _format_time(updated_at)
        prefix = f"   -[{time_prefix}] " if time_prefix else "   - "
        line = prefix + _escape_text(_truncate(sanitized, max_item_chars))
        if memory_type not in lines_by_type:
            lines_by_type[memory_type] = []
            ordered.append(memory_type)
        lines_by_type[memory_type].append(line)

    lines = ["<memories>"]
    for memory_type in ordered:
        type_lines = lines_by_type.get(memory_type) or []
        if not type_lines:
            continue
        lines.append(f'  <facts memory_type="{_escape_attribute(memory_type)}">')
        lines.extend(type_lines)
        lines.append("  </facts>")
    lines.append("</memories>")
    return _truncate("\n".join(lines), max_context_chars)


def hybrid_recall(
    store: MemoryStore,
    vectorizer: DenseVectorizer,
    *,
    query: str,
    effective_user_id: str,
    cfg: MemoryConfig,
    retrieval_enabled: bool | None = None,
) -> RecallResult:
    """Execute the full recall recipe for one query/user scope."""
    started = time.perf_counter()

    def finish(result: RecallResult) -> RecallResult:
        result.latency_ms = (time.perf_counter() - started) * 1000.0
        result.backend = vectorizer.backend
        return result

    if retrieval_enabled is False:
        return finish(RecallResult(retrieved=False, skip_reason="disabled"))
    trimmed = (query or "").strip()
    if not trimmed:
        return finish(RecallResult(retrieved=False, skip_reason="prompt_missing"))
    if trimmed.startswith((":")) or trimmed.startswith(("：")) or _CONTROL_COMMAND_RE.match(trimmed):
        return finish(RecallResult(retrieved=False, skip_reason="control_command"))

    top_k = max(1, cfg.top_k)
    k_dense = top_k
    k_sparse = top_k
    candidates = store.list_items(effective_user_id, limit=500)
    if not candidates:
        return finish(RecallResult(retrieved=False, skip_reason="no_memories"))

    # --- dense channel ------------------------------------------------------
    query_vector = vectorizer.embed(trimmed)
    dense: list[float] = []
    for item in candidates:
        vector = item.get("embedding")
        if not vector:
            vector = vectorizer.embed(item["content"])
        dense.append(max(0.0, cosine_similarity(query_vector, vector)))

    # --- sparse channel -----------------------------------------------------
    query_tokens = tokenize(trimmed)
    items_tokens = [tokenize(item["content"]) for item in candidates]
    sparse = _sparse_scores(query_tokens, items_tokens)

    # --- candidate pool (top k_dense ∪ top k_sparse) -------------------------
    pool: set[int] = set()
    pool.update(sorted(range(len(candidates)), key=lambda i: dense[i], reverse=True)[:k_dense])
    pool.update(sorted(range(len(candidates)), key=lambda i: sparse[i], reverse=True)[:k_sparse])

    # --- semantic gate + fusion ranking --------------------------------------
    scored: list[tuple[float, float, float, int]] = []
    for index in pool:
        semantic = dense[index]
        if semantic < cfg.min_score:
            continue
        fused = _DENSE_WEIGHT * semantic + _SPARSE_WEIGHT * sparse[index]
        scored.append((fused, semantic, sparse[index], index))
    scored.sort(key=lambda entry: (-entry[0], -candidates[entry[3]]["updated_at"]))
    selected = scored[:top_k]

    rendered: list[tuple[str, str, float | None]] = []
    exposed_items: list[dict[str, Any]] = []
    for fused, semantic, sparse_score, index in selected:
        item = candidates[index]
        rendered.append((item["memory_type"], item["content"], item["updated_at"]))
        exposed_items.append(
            {
                "id": item["id"],
                "memory_type": item["memory_type"],
                "content": _truncate(_sanitize_inline(item["content"]), cfg.max_item_chars),
                "score": round(fused, 4),
                "dense_score": round(semantic, 4),
                "sparse_score": round(sparse_score, 4),
            }
        )
    if not rendered:
        return finish(RecallResult(retrieved=False, skip_reason="empty_context"))

    order = list(cfg.memory_type_order)
    if UNCLASSIFIED not in order:
        order.append(UNCLASSIFIED)
    context = render_memories_xml(rendered, order, cfg.max_item_chars, cfg.max_context_chars)
    return finish(
        RecallResult(
            retrieved=True,
            context=context,
            item_count=len(rendered),
            items=exposed_items,
        )
    )
