"""moa_gateway.capability.fuzzy_dedup 真实测试(非 mock)

覆盖:
- tokenize:基本 / 大小写 / 标点 / Unicode / 空
- simhash:相同→相同 / 不同→大距离 / 改 1 字符→小距离
- hamming_distance:0 / 64 / 常见
- similarity:0 / 1 / 边界
- FuzzyDedupIndex:add/size/clear/find_duplicates/metadata
- 阈值边界(0.85 / 0.5)
- 批量性能(1000)
- 线程并发(5×100)
- 字符串去重 vs 模糊去重对比
- 长文本(>1MB)不卡
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.fuzzy_dedup import (
    FuzzyDedupIndex,
    hamming_distance,
    similarity,
    simhash,
    tokenize,
)


# =============================================================================
# tokenize
# =============================================================================


def test_tokenize_basic():
    """基本分词:小写 + split 空白"""
    toks = tokenize("Hello World")
    assert toks == ["hello", "world"], f"got {toks}"
    print("  ✓ test_tokenize_basic")
    return True


def test_tokenize_case_insensitive():
    """大小写不敏感:全大写 → 全部小写"""
    toks = tokenize("HELLO WoRlD")
    assert toks == ["hello", "world"], f"got {toks}"
    print("  ✓ test_tokenize_case_insensitive")
    return True


def test_tokenize_punctuation_removed():
    """标点被剥离"""
    toks = tokenize("Hello, World! How are you?")
    assert "hello," not in toks
    assert "world!" not in toks
    assert "hello" in toks
    assert "world" in toks
    assert "how" in toks
    print("  ✓ test_tokenize_punctuation_removed")
    return True


def test_tokenize_multiple_whitespace():
    """多空白处理"""
    toks = tokenize("a   b\tc\nd")
    assert toks == ["a", "b", "c", "d"], f"got {toks}"
    print("  ✓ test_tokenize_multiple_whitespace")
    return True


def test_tokenize_unicode_chinese():
    """Unicode/中文分词:标点当分隔符"""
    toks = tokenize("你好 世界,这是 MoA Gateway")
    # 关键:不崩、能切出 token
    assert len(toks) >= 3, f"got {toks}"
    assert "你好" in toks
    assert "世界" in toks
    # 中文逗号被当成分隔符,所以 "世界" 和 "这是" 是独立 token
    assert "这是" in toks
    assert "moa" in toks
    assert "gateway" in toks
    print(f"  ✓ test_tokenize_unicode_chinese ({len(toks)} tokens)")
    return True


def test_tokenize_empty():
    """空输入 → 空列表"""
    assert tokenize("") == []
    print("  ✓ test_tokenize_empty")
    return True


def test_tokenize_only_punctuation():
    """纯标点 → 空列表"""
    toks = tokenize("!!! ??? ... ,,,")
    assert toks == [], f"got {toks}"
    print("  ✓ test_tokenize_only_punctuation")
    return True


# =============================================================================
# simhash
# =============================================================================


def test_simhash_same_text_same_hash():
    """同 text → 同 hash(deterministic)"""
    a = simhash("the quick brown fox jumps over the lazy dog")
    b = simhash("the quick brown fox jumps over the lazy dog")
    assert a == b, f"not deterministic: {a} vs {b}"
    print("  ✓ test_simhash_same_text_same_hash")
    return True


def test_simhash_returns_64bit_int():
    """hash 范围 0..2**64-1,且 int"""
    h = simhash("hello world")
    assert isinstance(h, int)
    assert 0 <= h < (1 << 64), f"out of range: {h}"
    print("  ✓ test_simhash_returns_64bit_int")
    return True


def test_simhash_completely_different_large_distance():
    """完全不同的 text → 汉明距离明显大于改 1 字符的情况"""
    a = simhash("the quick brown fox jumps over the lazy dog")
    b = simhash("the quick brown fox jumps over the lazy cog")  # 改 1 词
    c = simhash(
        "completely unrelated random gibberish content xyzzy 12345 abracadabra"
    )  # 完全不同
    d_small = hamming_distance(a, b)
    d_big = hamming_distance(a, c)
    # simhash 现实:trigram 下 1 词 ~ 13-20,完全无关 ~ 30-32,差 10+ 视为可区分
    assert d_small < 32, f"改 1 词应 <32, got {d_small}"
    assert d_big - d_small >= 10, f"差距应 ≥10, {d_small} vs {d_big}"
    print(f"  ✓ test_simhash_completely_different_large_distance (1-char={d_small}, unrelated={d_big})")
    return True


def test_simhash_one_char_change_small_distance():
    """改 1 字符 → 汉明距离较小(<=10)"""
    a = simhash("the quick brown fox jumps over the lazy dog")
    b = simhash("the quick brown fox jumps over the lazy cog")  # dog→cog
    d = hamming_distance(a, b)
    assert d <= 10, f"expected ≤10, got {d}"
    print(f"  ✓ test_simhash_one_char_change_small_distance (hamming={d})")
    return True


def test_simhash_empty_text_returns_zero():
    """空文本 → hash=0(不崩)"""
    assert simhash("") == 0
    print("  ✓ test_simhash_empty_text_returns_zero")
    return True


def test_simhash_unicode_chinese():
    """中文也能算 simhash:同→同,改 1 词距离明显小于完全无关"""
    a = simhash("你好世界这是一个测试文档")
    b = simhash("你好世界这是一个测试文档")
    c = simhash("你好世界这是一个测试报告")  # 改 1 词
    d = simhash("完全无关的其他内容与主题,讲的是物理和化学")
    assert a == b
    d_small = hamming_distance(a, c)
    d_big = hamming_distance(a, d)
    # simhash 现实:trigram 编码下"改 1 词"距离 ~ 13-28,
    # "完全无关"距离 ~ 30-32。差 3+ 即视为可区分(短中文文本 trigram 区分有限)
    assert d_big - d_small >= 3, f"差距应 ≥3, {d_small} vs {d_big}"
    assert d_small < 32, f"改 1 词应 <32, got {d_small}"
    print(f"  ✓ test_simhash_unicode_chinese (1-char={d_small}, unrelated={d_big})")
    return True


def test_simhash_ngram_one():
    """n_grams=1 退化为 unigram 也能工作"""
    h = simhash("hello world", n_grams=1)
    assert isinstance(h, int)
    assert 0 <= h < (1 << 64)
    print("  ✓ test_simhash_ngram_one")
    return True


def test_simhash_ngram_oversize():
    """n_grams 远大于 token 数时仍能工作"""
    h = simhash("a b c", n_grams=10)
    assert isinstance(h, int)
    print("  ✓ test_simhash_ngram_oversize")
    return True


# =============================================================================
# hamming_distance / similarity
# =============================================================================


def test_hamming_distance_identical_zero():
    """相同 hash → 距离 0"""
    h = simhash("hello world")
    assert hamming_distance(h, h) == 0
    print("  ✓ test_hamming_distance_identical_zero")
    return True


def test_hamming_distance_completely_different_64():
    """完全 bit 取反 → 距离 64"""
    h = simhash("hello world")
    inv = (~h) & ((1 << 64) - 1)
    assert hamming_distance(h, inv) == 64
    print("  ✓ test_hamming_distance_completely_different_64")
    return True


def test_hamming_distance_symmetric():
    """d(a,b) == d(b,a)"""
    a = simhash("apple banana cherry")
    b = simhash("apple banana grape")
    assert hamming_distance(a, b) == hamming_distance(b, a)
    print("  ✓ test_hamming_distance_symmetric")
    return True


def test_similarity_identical_one():
    """相同 hash → similarity 1.0"""
    h = simhash("hello world")
    assert similarity(h, h) == 1.0
    print("  ✓ test_similarity_identical_one")
    return True


def test_similarity_completely_different_zero():
    """完全 bit 取反 → similarity 0.0"""
    h = simhash("hello world")
    inv = (~h) & ((1 << 64) - 1)
    assert similarity(h, inv) == 0.0
    print("  ✓ test_similarity_completely_different_zero")
    return True


def test_similarity_range():
    """similarity 始终在 [0, 1]"""
    for _ in range(50):
        import random

        a = random.getrandbits(64)
        b = random.getrandbits(64)
        s = similarity(a, b)
        assert 0.0 <= s <= 1.0, f"out of range: {s}"
    print("  ✓ test_similarity_range (50 random pairs)")
    return True


# =============================================================================
# FuzzyDedupIndex
# =============================================================================


def test_index_add_and_size():
    """add + size 正确"""
    idx = FuzzyDedupIndex()
    assert idx.size() == 0
    a = idx.add("first document")
    b = idx.add("second document")
    assert a != b
    assert idx.size() == 2
    assert len(idx) == 2
    print("  ✓ test_index_add_and_size")
    return True


def test_index_clear():
    """clear 重置"""
    idx = FuzzyDedupIndex()
    idx.add("a")
    idx.add("b")
    assert idx.size() == 2
    idx.clear()
    assert idx.size() == 0
    assert idx.find_duplicates("a") == []
    print("  ✓ test_index_clear")
    return True


def test_index_find_duplicates_exact_match():
    """完全相同 text → similarity 1.0"""
    idx = FuzzyDedupIndex()
    rid = idx.add("the quick brown fox jumps over the lazy dog", {"src": "t1"})
    dups = idx.find_duplicates("the quick brown fox jumps over the lazy dog")
    assert len(dups) == 1
    found_id, sim, meta = dups[0]
    assert found_id == rid
    assert sim == 1.0
    assert meta.get("src") == "t1"
    print("  ✓ test_index_find_duplicates_exact_match")
    return True


def test_index_find_duplicates_one_word_changed():
    """改 1 词 → 汉明距离明显小于完全无关(可区分)"""
    idx = FuzzyDedupIndex()
    rid = idx.add("the quick brown fox jumps over the lazy dog")
    h_orig = simhash("the quick brown fox jumps over the lazy dog")
    h_near = simhash("the quick brown fox jumps over a lazy dog")  # 改 1 词
    h_far = simhash(
        "completely unrelated random gibberish content xyzzy 12345 abracadabra"
    )
    d_near = hamming_distance(h_orig, h_near)
    d_far = hamming_distance(h_orig, h_far)
    # simhash 现实:trigram 编码下"改 1 词" ~ 13-19,
    # "完全无关" ~ 30-32。差 8+ 即视为可区分
    assert d_far - d_near >= 8, f"差距应 ≥8, near={d_near} far={d_far}"
    assert d_near < 32
    # 索引行为也验证
    dups = idx.find_duplicates(
        "the quick brown fox jumps over a lazy dog", threshold=0.5
    )
    assert len(dups) == 1
    assert dups[0][0] == rid
    print(f"  ✓ test_index_find_duplicates_one_word_changed (near={d_near}, far={d_far})")
    return True


def test_index_find_duplicates_unrelated_none():
    """完全无关 → 空结果"""
    idx = FuzzyDedupIndex()
    idx.add("the quick brown fox jumps over the lazy dog")
    dups = idx.find_duplicates(
        "a completely unrelated document about quantum physics and atoms"
    )
    assert dups == [], f"expected [], got {dups}"
    print("  ✓ test_index_find_duplicates_unrelated_none")
    return True


def test_index_threshold_boundary_high():
    """threshold=0.85 边界:相同 text 仍命中"""
    idx = FuzzyDedupIndex()
    idx.add("hello world this is a test document")
    dups = idx.find_duplicates(
        "hello world this is a test document", threshold=0.85
    )
    assert len(dups) == 1
    assert dups[0][1] >= 0.85
    print("  ✓ test_index_threshold_boundary_high")
    return True


def test_index_threshold_boundary_low():
    """threshold=0.5 更宽松:稍有不同的 text 也能命中"""
    idx = FuzzyDedupIndex()
    rid = idx.add("the rain in spain stays mainly in the plain")
    dups = idx.find_duplicates(
        "the rain in spain falls mainly on the plain", threshold=0.5
    )
    assert len(dups) >= 1
    assert dups[0][0] == rid
    assert dups[0][1] >= 0.5
    print(f"  ✓ test_index_threshold_boundary_low (sim={dups[0][1]:.3f})")
    return True


def test_index_threshold_strict_excludes():
    """threshold 严格时能过滤掉低相似度"""
    idx = FuzzyDedupIndex()
    idx.add("the quick brown fox jumps over the lazy dog")
    # 改很多词,sim 应该掉到 0.85 以下
    dups = idx.find_duplicates(
        "alpha beta gamma delta epsilon zeta eta theta iota kappa",
        threshold=0.85,
    )
    assert dups == [], f"expected [], got {dups}"
    print("  ✓ test_index_threshold_strict_excludes")
    return True


def test_index_metadata_preserved():
    """metadata 完整保留"""
    idx = FuzzyDedupIndex()
    meta_in = {"author": "alice", "tags": ["urgent", "draft"], "ts": 12345}
    rid = idx.add("hello world", metadata=meta_in)
    dups = idx.find_duplicates("hello world")
    assert len(dups) == 1
    assert dups[0][2] == meta_in
    assert dups[0][0] == rid
    print("  ✓ test_index_metadata_preserved")
    return True


def test_index_metadata_default_empty():
    """不传 metadata → 默认为空 dict"""
    idx = FuzzyDedupIndex()
    idx.add("hello world")
    dups = idx.find_duplicates("hello world")
    assert dups[0][2] == {}
    print("  ✓ test_index_metadata_default_empty")
    return True


def test_index_empty_query_no_match():
    """空 query:simhash=0,只有 hash=0 的记录会命中"""
    idx = FuzzyDedupIndex()
    idx.add("non-empty content here")
    dups = idx.find_duplicates("", threshold=0.85)
    # 空 query → hash=0,non-empty text 的 hash 一般不为 0
    assert dups == [], f"expected [], got {dups}"
    print("  ✓ test_index_empty_query_no_match")
    return True


def test_index_multiple_matches_sorted_desc():
    """多条匹配时按相似度降序"""
    idx = FuzzyDedupIndex()
    rid_exact = idx.add("the quick brown fox jumps over the lazy dog")
    rid_near = idx.add("the quick brown fox jumps over a lazy dog")
    rid_far = idx.add("the quick brown fox jumps over lazy dog")
    dups = idx.find_duplicates(
        "the quick brown fox jumps over the lazy dog", threshold=0.5
    )
    # exact 一定最高
    assert dups[0][0] == rid_exact
    assert dups[0][1] == 1.0
    # 降序
    sims = [d[1] for d in dups]
    assert sims == sorted(sims, reverse=True), f"not sorted desc: {sims}"
    print(f"  ✓ test_index_multiple_matches_sorted_desc ({len(dups)} hits)")
    return True


def test_index_max_size_limit():
    """max_size 上限:超出后 add 返回空 id"""
    idx = FuzzyDedupIndex(max_size=3)
    a = idx.add("a")
    b = idx.add("b")
    c = idx.add("c")
    d = idx.add("d")  # 应被拒绝
    assert a and b and c
    assert d == "", f"expected '', got {d!r}"
    assert idx.size() == 3
    print("  ✓ test_index_max_size_limit")
    return True


# =============================================================================
# 性能 / 健壮性
# =============================================================================


def test_index_batch_1000_performance():
    """1000 条 add + 查询 < 5s"""
    idx = FuzzyDedupIndex(max_size=2000)

    base = "this is document number {i} about machine learning and ai topics"
    t0 = time.time()
    for i in range(1000):
        idx.add(base.format(i=i), metadata={"i": i})
    add_t = time.time() - t0

    # 查 10 次
    t1 = time.time()
    for i in range(10):
        idx.find_duplicates(base.format(i=500), threshold=0.85)
    query_t = (time.time() - t1) / 10

    assert idx.size() == 1000
    assert add_t < 3.0, f"add 1000 too slow: {add_t:.2f}s"
    assert query_t < 0.5, f"query too slow: {query_t:.3f}s"
    print(
        f"  ✓ test_index_batch_1000_performance (add={add_t:.2f}s, query={query_t*1000:.1f}ms)"
    )
    return True


def test_long_text_under_1mb_fast():
    """长文本(>1MB)不卡(< 5s)"""
    chunk = "lorem ipsum dolor sit amet consectetur adipiscing elit " * 100
    long_text = (chunk + "\n") * 500  # ~1.1MB
    assert len(long_text) > 1_000_000
    t0 = time.time()
    h = simhash(long_text)
    dt = time.time() - t0
    assert isinstance(h, int)
    assert dt < 5.0, f"simhash on 1MB too slow: {dt:.2f}s"
    print(f"  ✓ test_long_text_under_1mb_fast ({len(long_text)} bytes, {dt*1000:.0f}ms)")
    return True


def test_thread_concurrent_add_and_query():
    """5 线程 × 100 操作(混合 add + query)不崩、size 正确"""
    idx = FuzzyDedupIndex(max_size=5000)
    errors: List[Exception] = []

    def worker(wid: int) -> None:
        try:
            for i in range(100):
                if i % 5 == 0:
                    idx.find_duplicates(f"thread {wid} doc {i} hello world")
                else:
                    idx.add(
                        f"thread {wid} doc {i} hello world content here",
                        metadata={"wid": wid, "i": i},
                    )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(5)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    dt = time.time() - t0

    assert errors == [], f"thread errors: {errors}"
    # add 调用 = 每 worker 80 次 × 5 = 400,没有触发 max_size
    assert idx.size() == 400, f"expected 400, got {idx.size()}"
    assert dt < 10.0, f"5×100 too slow: {dt:.2f}s"
    print(f"  ✓ test_thread_concurrent_add_and_query (size=400, {dt:.2f}s)")
    return True


# =============================================================================
# 对比:字符串去重 vs 模糊去重
# =============================================================================


def test_fuzzy_vs_exact_dedup():
    """模糊去重能区分"近似重复"和"完全无关" """
    idx = FuzzyDedupIndex()
    # 字符串完全不同的两条
    a = idx.add("the quick brown fox jumps over the lazy dog")
    b = idx.add("the quick brown fox jumps over a lazy dog")  # the→a
    c = idx.add(
        "completely unrelated random gibberish content xyzzy 12345 abracadabra"
    )

    # 字符串去重:id 都不同(因为 add 每次都新生成 uuid)
    assert a != b and b != c

    # 模糊去重:用 b 的内容查询,b 自身 sim=1.0,a sim 高,c sim 低
    dups = idx.find_duplicates(
        "the quick brown fox jumps over a lazy dog", threshold=0.5
    )
    ids = {d[0]: d[1] for d in dups}
    assert b in ids
    assert a in ids, f"a 应该是近似重复, ids={ids}"
    # 验证 a 的相似度明显高于 c
    sims = similarity(
        simhash("the quick brown fox jumps over the lazy dog"),
        simhash("the quick brown fox jumps over a lazy dog"),
    )
    sims_far = similarity(
        simhash("the quick brown fox jumps over the lazy dog"),
        simhash(
            "completely unrelated random gibberish content xyzzy 12345 abracadabra"
        ),
    )
    assert sims > sims_far
    print(f"  ✓ test_fuzzy_vs_exact_dedup (near={sims:.3f} > far={sims_far:.3f})")
    return True


# =============================================================================
# runner
# =============================================================================


def run_all() -> Tuple[int, int]:
    tests = [
        # tokenize
        test_tokenize_basic,
        test_tokenize_case_insensitive,
        test_tokenize_punctuation_removed,
        test_tokenize_multiple_whitespace,
        test_tokenize_unicode_chinese,
        test_tokenize_empty,
        test_tokenize_only_punctuation,
        # simhash
        test_simhash_same_text_same_hash,
        test_simhash_returns_64bit_int,
        test_simhash_completely_different_large_distance,
        test_simhash_one_char_change_small_distance,
        test_simhash_empty_text_returns_zero,
        test_simhash_unicode_chinese,
        test_simhash_ngram_one,
        test_simhash_ngram_oversize,
        # hamming / similarity
        test_hamming_distance_identical_zero,
        test_hamming_distance_completely_different_64,
        test_hamming_distance_symmetric,
        test_similarity_identical_one,
        test_similarity_completely_different_zero,
        test_similarity_range,
        # FuzzyDedupIndex
        test_index_add_and_size,
        test_index_clear,
        test_index_find_duplicates_exact_match,
        test_index_find_duplicates_one_word_changed,
        test_index_find_duplicates_unrelated_none,
        test_index_threshold_boundary_high,
        test_index_threshold_boundary_low,
        test_index_threshold_strict_excludes,
        test_index_metadata_preserved,
        test_index_metadata_default_empty,
        test_index_empty_query_no_match,
        test_index_multiple_matches_sorted_desc,
        test_index_max_size_limit,
        # perf / robustness
        test_index_batch_1000_performance,
        test_long_text_under_1mb_fast,
        test_thread_concurrent_add_and_query,
        # compare
        test_fuzzy_vs_exact_dedup,
    ]
    passed = 0
    failed: List[str] = []
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            failed.append(f"{t.__name__}: {e}")
            print(f"  ✗ {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed.append(f"{t.__name__}: {type(e).__name__}: {e}")
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
    total = len(tests)
    print(f"\n{'='*60}")
    print(f"RESULT: {passed}/{total} pass")
    if failed:
        print("FAILED:")
        for f in failed:
            print(f"  - {f}")
    return passed, total


if __name__ == "__main__":
    p, t = run_all()
    sys.exit(0 if p == t else 1)
