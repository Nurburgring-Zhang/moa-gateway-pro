"""moa_gateway.capability.embedding — Embedding 端点 + 批处理 + 余弦相似度/语义搜索

来源: 02 MoA-together-ai / 04 moa-main-commercial (Embedding API)

提供:
- Embedding dataclass: 一条 (text, vector, dim, model) 记录
- cosine_similarity: dot(a, b) / (||a|| * ||b||),0 向量返 0
- euclidean_distance: sqrt(sum((a-b)^2))
- hash_embedding: SHA-256 分块 seed + token 位置编码的 deterministic embedding
  (无 ML 依赖,offline / dev fallback)
- batch_embed: 批量 hash_embedding,带 batch_size 分块
- EmbeddingIndex: 内存 ANN-like 索引(add / add_batch / search / __len__)
- semantic_search: hash_embedding(query) → index.search 端到端
- MockEmbeddingProvider: 模拟 OpenAI /v1/embeddings 端点(无 API key 时 E2E)

与 FastAPI 集成示例:
    from fastapi import FastAPI
    from pydantic import BaseModel
    from moa_gateway.capability.embedding import MockEmbeddingProvider

    app = FastAPI()
    provider = MockEmbeddingProvider(model="hash-384", dim=384)

    class EmbeddingRequest(BaseModel):
        input: list[str]

    @app.post("/v1/embeddings")
    def embeddings(req: EmbeddingRequest):
        vectors = provider.embed(req.input)
        return {
            "object": "list",
            "data": [
                {"object": "embedding", "index": i, "embedding": v}
                for i, v in enumerate(vectors)
            ],
            "model": provider.model,
            "usage": {"prompt_tokens": -1, "total_tokens": -1},
        }

设计约束:
- hash_embedding 不是 SOTA embedding(没有语义理解),但:
    1. 完全 deterministic(同 text → 同 vector,跨进程/跨机器一致)
    2. 相似 text(token 重叠多)→ 高 cosine similarity
    3. L2 normalized → cosine ≡ dot product
- 生产建议替换为真实模型(OpenAI text-embedding-3-small/large, BGE, MTEB 等),
  本模块仅作 offline / dev / unit-test fallback
"""
from __future__ import annotations

import hashlib
import logging
import math
import struct
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "Embedding",
    "EmbeddingIndex",
    "cosine_similarity",
    "euclidean_distance",
    "hash_embedding",
    "batch_embed",
    "semantic_search",
    "MockEmbeddingProvider",
]


# =============================================================================
# 数学工具
# =============================================================================


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """真实余弦相似度: dot(a, b) / (||a|| * ||b||)

    边界:
    - 长度不匹配 raise ValueError
    - 任一向量 0 向量 → 0.0(分母为 0,定义 0 相似度)
    - 反向向量 → -1.0
    """
    if len(a) != len(b):
        raise ValueError(
            f"cosine_similarity: length mismatch a={len(a)} vs b={len(b)}"
        )
    if not a:
        return 0.0
    dot = 0.0
    norm_a_sq = 0.0
    norm_b_sq = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a_sq += x * x
        norm_b_sq += y * y
    norm_a = math.sqrt(norm_a_sq)
    norm_b = math.sqrt(norm_b_sq)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def euclidean_distance(a: List[float], b: List[float]) -> float:
    """L2 距离: sqrt(sum((a_i - b_i)^2))

    长度不匹配 raise ValueError。
    """
    if len(a) != len(b):
        raise ValueError(
            f"euclidean_distance: length mismatch a={len(a)} vs b={len(b)}"
        )
    s = 0.0
    for x, y in zip(a, b):
        d = x - y
        s += d * d
    return math.sqrt(s)


# =============================================================================
# Token 化(用于 hash_embedding 的位置编码)
# =============================================================================


def _tokenize(text: str) -> List[str]:
    """轻量 token 化:小写 + 切分非字母数字

    与 BERT/SentencePiece 等真实 tokenizer 完全不同,
    仅用于让"相似 text token 重叠多"成立。
    """
    if not text:
        return []
    out: List[str] = []
    cur: List[str] = []
    for ch in text.lower():
        if ch.isalnum():
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur))
                cur = []
    if cur:
        out.append("".join(cur))
    return out


# =============================================================================
# Hash-based Embedding
# =============================================================================


def _hash_block_to_ints(seed: bytes, block_index: int, n_ints: int) -> List[int]:
    """用 SHA-256(seed || block_index) 扩展为 n_ints 个 uint32

    每次 hash 出 32 字节 = 8 个 uint32,n_ints > 8 时链式扩展。
    """
    ints: List[int] = []
    counter = 0
    while len(ints) < n_ints:
        h = hashlib.sha256()
        h.update(seed)
        h.update(struct.pack(">II", block_index, counter))
        digest = h.digest()
        # 每 4 字节解 1 个 uint32
        for i in range(0, len(digest), 4):
            if len(ints) >= n_ints:
                break
            ints.append(struct.unpack(">I", digest[i : i + 4])[0])
        counter += 1
    return ints


def hash_embedding(text: str, dim: int = 384) -> List[float]:
    """SHA-256 分块 seed + token 位置编码的 deterministic embedding

    数学保证:
    1. **Deterministic**: 同一 text + 同一 dim → 完全相同的 vector(跨进程/机器一致)
    2. **Token overlap → 高 cosine**:
       对每个 token t (lowercased, alphanumeric-only):
         seed = SHA-256(t)[:16]
         contributions[t][pos] 来自 SHA-256(seed || pos) 解出的 uint32
       两个 text 共享的 token 会在相同位置贡献相同分量,共享 token 越多 cosine 越高
    3. **Token position 编码**:
       不同位置(pos)用不同 hash 链 → 同一 token 在不同位置有不同贡献
       → 短语级语义(rough)
    4. **L2 normalized**: 输出 ||v||_2 = 1.0(数值上 ≈ 1.0,因 IEEE 754)
    5. **非 ML**: 不学语义,只捕捉 token 重叠

    Args:
        text: 输入文本
        dim: 输出维度,必须 > 0

    Returns:
        L2 normalized 的 dim 维 float 列表
    """
    if dim <= 0:
        raise ValueError(f"dim must be > 0, got {dim}")

    tokens = _tokenize(text)

    # 空文本 → 用一个固定 token 兜底,避免全 0(否则 norm=0 → cosine 永远 0)
    if not tokens:
        tokens = ["<empty>"]

    # 去重保留位置(简单 uniq-pos 编码,减少 hash 调用)
    # 但保留顺序;同一 token 重复出现时只在 first pos 贡献(rough phrase 编码)
    seen = set()
    unique_with_pos: List[Tuple[str, int]] = []
    for tok in tokens:
        if tok in seen:
            continue
        seen.add(tok)
        unique_with_pos.append((tok, len(unique_with_pos)))

    # 累加器 + 计数器(用于符号反转减少偏置)
    acc = [0.0] * dim
    for tok, pos in unique_with_pos:
        # seed = SHA-256(tok)[:16]
        seed = hashlib.sha256(tok.encode("utf-8")).digest()[:16]
        # 需要 dim 个 int → 决定 block 数量
        # 每 block 8 uint32 = 8 dim slots
        n_blocks = (dim + 7) // 8
        ints = _hash_block_to_ints(seed, pos, n_blocks * 8)
        # 位置符号反转:奇数位置 idx 翻转符号,降低偏置
        sign = 1.0 if (pos % 2 == 0) else -1.0
        for i in range(dim):
            u = ints[i]  # uint32 in [0, 2^32)
            # 映射到 [-1, 1](中心化)
            v = (u / 4294967295.0) * 2.0 - 1.0
            acc[i] += sign * v

    # L2 normalize
    norm = math.sqrt(sum(x * x for x in acc))
    if norm == 0.0:
        # 极小概率,返回零向量(下游 cosine 会返 0.0)
        return [0.0] * dim
    inv = 1.0 / norm
    return [x * inv for x in acc]


def batch_embed(
    texts: List[str],
    dim: int = 384,
    batch_size: int = 64,
) -> List[List[float]]:
    """批量 hash embedding,带 batch_size 分块

    Args:
        texts: 输入文本列表
        dim: 每条 embedding 维度
        batch_size: 每块大小(< 1 视为 1)

    Returns:
        与输入等长的 list[list[float]]
    """
    if batch_size < 1:
        batch_size = 1
    out: List[List[float]] = []
    for start in range(0, len(texts), batch_size):
        chunk = texts[start : start + batch_size]
        for t in chunk:
            out.append(hash_embedding(t, dim=dim))
    return out


# =============================================================================
# EmbeddingIndex
# =============================================================================


@dataclass
class Embedding:
    """单条 embedding 记录"""

    text: str
    vector: List[float]
    dim: int
    model: str


class EmbeddingIndex:
    """简单内存 embedding 索引

    - add / add_batch: 追加
    - search: 暴力全量 cosine top-k(O(N) 每次)
      (对 dev / 单元测试足够,生产可换 faiss / hnswlib)
    """

    def __init__(self, model: str, dim: int) -> None:
        if dim <= 0:
            raise ValueError(f"dim must be > 0, got {dim}")
        self.model = model
        self.dim = dim
        self._items: List[Tuple[str, List[float]]] = []

    def __len__(self) -> int:
        return len(self._items)

    def add(self, text: str, vector: List[float]) -> int:
        """追加一条,返回分配到的 idx"""
        if len(vector) != self.dim:
            raise ValueError(
                f"vector dim {len(vector)} != index dim {self.dim}"
            )
        idx = len(self._items)
        self._items.append((text, vector))
        return idx

    def add_batch(
        self, items: List[Tuple[str, List[float]]]
    ) -> List[int]:
        """批量追加,返回每个 item 分配到的 idx 列表(按输入顺序)"""
        out_indices: List[int] = []
        for text, vec in items:
            out_indices.append(self.add(text, vec))
        return out_indices

    def search(
        self, query_vec: List[float], top_k: int = 5
    ) -> List[Tuple[int, float, str]]:
        """按 cosine 相似度返回 top_k (idx, score, text),降序

        空索引 → []
        """
        if not self._items:
            return []
        if len(query_vec) != self.dim:
            raise ValueError(
                f"query_vec dim {len(query_vec)} != index dim {self.dim}"
            )
        if top_k < 1:
            return []

        scored: List[Tuple[int, float, str]] = []
        for idx, (text, vec) in enumerate(self._items):
            score = cosine_similarity(query_vec, vec)
            scored.append((idx, score, text))

        # 降序;稳定排序
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:top_k]


# =============================================================================
# Semantic search (端到端)
# =============================================================================


def semantic_search(
    index: EmbeddingIndex,
    query: str,
    top_k: int = 5,
    dim: Optional[int] = None,
) -> List[Tuple[int, float, str]]:
    """hash_embedding(query) → index.search

    Args:
        index: EmbeddingIndex 实例(其 dim 必须 == dim 或 dim=None 时默认 index.dim)
        query: 查询文本
        top_k: 返回条数
        dim: embedding 维度,None → 用 index.dim

    Returns:
        List of (idx, score, text)
    """
    if dim is None:
        dim = index.dim
    q_vec = hash_embedding(query, dim=dim)
    return index.search(q_vec, top_k=top_k)


# =============================================================================
# MockEmbeddingProvider(模拟 OpenAI /v1/embeddings)
# =============================================================================


@dataclass
class MockEmbeddingProvider:
    """模拟 OpenAI /v1/embeddings 端点

    - embed(texts) → list[list[float]]
    - model: provider 报告的 model id
    - dim: 输出维度
    - 同样的输入总是一致(底层是 hash_embedding)
    - 可在没真 API key 时做 E2E(测试 / 本地开发)

    用法:
        provider = MockEmbeddingProvider(model="hash-384", dim=384)
        vecs = provider.embed(["hello", "world"])
        assert len(vecs) == 2
    """

    model: str = "hash-384"
    dim: int = 384
    _stats: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.dim <= 0:
            raise ValueError(f"dim must be > 0, got {self.dim}")
        self._stats["n_calls"] = 0
        self._stats["n_texts"] = 0

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if texts is None:
            raise ValueError("texts must not be None")
        out = batch_embed(list(texts), dim=self.dim)
        self._stats["n_calls"] += 1
        self._stats["n_texts"] += len(out)
        return out

    def stats(self) -> dict:
        return dict(self._stats)
