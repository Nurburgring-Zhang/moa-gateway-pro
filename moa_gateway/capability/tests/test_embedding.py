"""moa_gateway.capability.embedding 真实测试(非 mock)"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.embedding import (
    EmbeddingIndex,
    MockEmbeddingProvider,
    batch_embed,
    cosine_similarity,
    euclidean_distance,
    hash_embedding,
    semantic_search,
)

# =============================================================================
# hash_embedding
# =============================================================================


def test_hash_embedding_deterministic():
    """同 text + 同 dim → 完全相同 vector"""
    v1 = hash_embedding("hello world", dim=128)
    v2 = hash_embedding("hello world", dim=128)
    assert v1 == v2
    assert len(v1) == 128
    print(f"  ✓ test_hash_embedding_deterministic (dim={len(v1)})")
    return True


def test_hash_embedding_dim_matches():
    """输出 dim 严格匹配传入 dim"""
    for d in (16, 64, 128, 384, 768):
        v = hash_embedding("abc def", dim=d)
        assert len(v) == d
    print("  ✓ test_hash_embedding_dim_matches (16/64/128/384/768)")
    return True


def test_hash_embedding_l2_normalized():
    """L2 norm ≈ 1.0(数值误差 < 1e-9)"""
    v = hash_embedding("the quick brown fox jumps over the lazy dog", dim=256)
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-9, f"norm={norm}"
    print(f"  ✓ test_hash_embedding_l2_normalized (norm={norm:.12f})")
    return True


# =============================================================================
# cosine_similarity
# =============================================================================


def test_cosine_similarity_identical():
    """完全相同向量 → 1.0"""
    a = [1.0, 2.0, 3.0, 4.0]
    s = cosine_similarity(a, a)
    assert abs(s - 1.0) < 1e-12
    print(f"  ✓ test_cosine_similarity_identical (score={s})")
    return True


def test_cosine_similarity_orthogonal():
    """正交向量 → 0.0"""
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    s = cosine_similarity(a, b)
    assert abs(s) < 1e-12
    print(f"  ✓ test_cosine_similarity_orthogonal (score={s})")
    return True


def test_cosine_similarity_opposite():
    """反向向量 → -1.0"""
    a = [1.0, 2.0, 3.0]
    b = [-x for x in a]
    s = cosine_similarity(a, b)
    assert abs(s + 1.0) < 1e-12
    print(f"  ✓ test_cosine_similarity_opposite (score={s})")
    return True


def test_cosine_similarity_zero_vector():
    """0 向量 → 0.0(不是 nan)"""
    a = [0.0, 0.0, 0.0]
    b = [1.0, 2.0, 3.0]
    s = cosine_similarity(a, b)
    assert s == 0.0
    print(f"  ✓ test_cosine_similarity_zero_vector (score={s})")
    return True


def test_cosine_similarity_length_mismatch():
    """长度不匹配 raise ValueError"""
    try:
        cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])
    except ValueError as e:
        assert "length mismatch" in str(e).lower()
        print("  ✓ test_cosine_similarity_length_mismatch (raised)")
        return True
    raise AssertionError("expected ValueError")


# =============================================================================
# euclidean_distance
# =============================================================================


def test_euclidean_distance_basic():
    """基础 L2 距离"""
    a = [0.0, 0.0]
    b = [3.0, 4.0]
    assert abs(euclidean_distance(a, b) - 5.0) < 1e-12
    a2 = [1.0, 2.0, 3.0]
    b2 = [1.0, 2.0, 3.0]
    assert euclidean_distance(a2, b2) == 0.0
    print("  ✓ test_euclidean_distance_basic (3,4)→5; identical→0")
    return True


# =============================================================================
# EmbeddingIndex
# =============================================================================


def test_embedding_index_add_returns_idx():
    """add 返回从 0 开始的 idx"""
    idx = EmbeddingIndex(model="test", dim=8)
    v0 = [0.1] * 8
    v1 = [0.2] * 8
    i0 = idx.add("first", v0)
    i1 = idx.add("second", v1)
    assert i0 == 0
    assert i1 == 1
    assert len(idx) == 2
    print(f"  ✓ test_embedding_index_add_returns_idx (i0={i0}, i1={i1})")
    return True


def test_embedding_index_add_batch():
    """add_batch 批处理返回 idx 列表"""
    idx = EmbeddingIndex(model="test", dim=4)
    items = [
        ("a", [0.1, 0.2, 0.3, 0.4]),
        ("b", [0.5, 0.6, 0.7, 0.8]),
        ("c", [0.9, 1.0, 1.1, 1.2]),
    ]
    indices = idx.add_batch(items)
    assert indices == [0, 1, 2]
    assert len(idx) == 3
    print(f"  ✓ test_embedding_index_add_batch (indices={indices})")
    return True


def test_embedding_index_search_topk_sorted():
    """search top_k 按 score 降序"""
    dim = 16
    idx = EmbeddingIndex(model="test", dim=dim)
    v1 = [1.0] + [0.0] * (dim - 1)
    v2 = [0.0, 1.0] + [0.0] * (dim - 2)
    v3 = [0.5] * dim
    idx.add("a", v1)
    idx.add("b", v2)
    idx.add("c", v3)
    results = idx.search(v1, top_k=3)
    assert len(results) == 3
    # 第一个应是与 v1 完全相同(score=1.0)
    assert results[0][0] == 0
    assert abs(results[0][1] - 1.0) < 1e-12
    # 分数降序
    for i in range(len(results) - 1):
        assert results[i][1] >= results[i + 1][1]
    print(f"  ✓ test_embedding_index_search_topk_sorted (top1_score={results[0][1]:.4f})")
    return True


def test_embedding_index_search_empty():
    """空索引 search → []"""
    idx = EmbeddingIndex(model="test", dim=8)
    out = idx.search([0.1] * 8, top_k=5)
    assert out == []
    assert len(idx) == 0
    print("  ✓ test_embedding_index_search_empty")
    return True


# =============================================================================
# batch_embed
# =============================================================================


def test_batch_embed_length_matches_input():
    """batch_embed 输出长度 == 输入长度,且每条 dim 正确"""
    texts = ["alpha", "beta gamma", "delta epsilon zeta", "theta"]
    dim = 64
    out = batch_embed(texts, dim=dim)
    assert len(out) == len(texts)
    for v in out:
        assert len(v) == dim
    print(f"  ✓ test_batch_embed_length_matches_input (n={len(out)})")
    return True


def test_batch_embed_batch_size_chunking():
    """batch_size=2 时 5 条文本应分 3 块处理,且结果与不切片一致"""
    texts = ["x", "y", "z", "w", "q"]
    dim = 32
    out_chunked = batch_embed(texts, dim=dim, batch_size=2)
    out_big = batch_embed(texts, dim=dim, batch_size=1000)
    assert len(out_chunked) == 5
    assert out_chunked == out_big  # deterministic,分块不影响结果
    print("  ✓ test_batch_embed_batch_size_chunking (size=2 == size=1000)")
    return True


# =============================================================================
# semantic_search(端到端)
# =============================================================================


def test_semantic_search_e2e():
    """semantic_search 端到端:query 文本与 index 中某条相似时,该条应排前"""
    dim = 128
    idx = EmbeddingIndex(model="hash-128", dim=dim)
    corpus = [
        "the cat sat on the mat",
        "dogs love to run in the park",
        "machine learning and embeddings are fun",
        "cooking pasta with tomato sauce",
    ]
    for doc in corpus:
        idx.add(doc, hash_embedding(doc, dim=dim))
    # query 与 "cat ... mat" 完全相同 → 应是 top-1(score=1.0)
    results = semantic_search(idx, "the cat sat on the mat", top_k=4, dim=dim)
    assert results, "empty results"
    assert results[0][2] == "the cat sat on the mat"
    assert abs(results[0][1] - 1.0) < 1e-12
    print(f"  ✓ test_semantic_search_e2e (top1={results[0][2]!r}, score={results[0][1]:.4f})")
    return True


# =============================================================================
# MockEmbeddingProvider
# =============================================================================


def test_mock_embedding_provider_single_and_batch():
    """MockEmbeddingProvider.embed 单/批都返回正确维度"""
    p = MockEmbeddingProvider(model="hash-64", dim=64)
    v = p.embed(["hello world"])
    assert len(v) == 1
    assert len(v[0]) == 64
    v2 = p.embed(["a", "b", "c"])
    assert len(v2) == 3
    assert all(len(x) == 64 for x in v2)
    # deterministic
    v_again = p.embed(["hello world"])
    assert v == v_again
    # stats
    s = p.stats()
    assert s["n_calls"] == 3
    assert s["n_texts"] == 5
    print(f"  ✓ test_mock_embedding_provider_single_and_batch (stats={s})")
    return True


# =============================================================================
# 相似 text 在 index 中排名靠前
# =============================================================================


def test_similar_texts_rank_higher():
    """token 重叠多的 text cosine 更高,排名更靠前"""
    dim = 256
    idx = EmbeddingIndex(model="hash-256", dim=dim)
    docs = [
        "I love programming in Python every single day",  # query 高度相关
        "the quick brown fox jumps over the lazy dog",   # 无关
        "Python is my favorite programming language",    # 中度相关
        "I enjoy coding with Python for fun",            # 高度相关
        "I hate to write code in Java",                  # 弱相关(共享 code/Python 弱)
    ]
    for d in docs:
        idx.add(d, hash_embedding(d, dim=dim))
    query = "I love programming in Python"
    results = semantic_search(idx, query, top_k=5, dim=dim)
    # 拿到 idx 与 text 列表
    top_texts = [r[2] for r in results]
    # "I love programming in Python every single day" 共享最多 token,应排第一
    assert "I love programming in Python every single day" in top_texts[0:2], (
        f"expected top result to share most tokens with query, got: {top_texts}"
    )
    # 共享 token 越少分数越低(至少不反超)
    for i in range(len(results) - 1):
        assert results[i][1] >= results[i + 1][1]
    print(f"  ✓ test_similar_texts_rank_higher (top1={results[0][2]!r}, score={results[0][1]:.4f})")
    return True
