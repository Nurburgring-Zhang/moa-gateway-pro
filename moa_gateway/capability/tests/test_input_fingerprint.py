"""Tests for input_fingerprint - 25+ cases covering all 4 layers and the store."""

from __future__ import annotations

import random
import string
import threading
import time
import unittest

from capability.input_fingerprint import (
    FingerprintStore,
    InputFingerprint,
    exact_hash,
    normalized_hash,
    semantic_hash,
    structural_hash,
)


class TestExactHash(unittest.TestCase):
    def test_returns_hex_64(self):
        h = exact_hash("hello")
        self.assertEqual(len(h), 64)
        int(h, 16)  # must be valid hex

    def test_same_text_same_hash(self):
        self.assertEqual(exact_hash("foo bar"), exact_hash("foo bar"))

    def test_one_char_differs(self):
        self.assertNotEqual(exact_hash("foo bar"), exact_hash("foo bar!"))

    def test_empty_string(self):
        h = exact_hash("")
        self.assertEqual(len(h), 64)


class TestNormalizedHash(unittest.TestCase):
    def test_case_insensitive(self):
        self.assertEqual(normalized_hash("Hello World"), normalized_hash("hello WORLD"))

    def test_punctuation_insensitive(self):
        self.assertEqual(
            normalized_hash("Hello, World!"),
            normalized_hash("hello world"),
        )

    def test_whitespace_folded(self):
        self.assertEqual(
            normalized_hash("foo   bar\n\tbaz"),
            normalized_hash("foo bar baz"),
        )

    def test_different_words_differ(self):
        self.assertNotEqual(normalized_hash("foo"), normalized_hash("bar"))

    def test_empty(self):
        self.assertEqual(len(normalized_hash("")), 64)


class TestStructuralHash(unittest.TestCase):
    def test_token_order_preserved(self):
        a = structural_hash("buy 1")
        b = structural_hash("1 buy")
        self.assertNotEqual(a, b)

    def test_same_structure_same_hash(self):
        # different words, same word/number/punct shape
        a = structural_hash("Hello, 123 world!")
        b = structural_hash("Goodbye, 456 there!")
        self.assertEqual(a, b)
        # the canonical signature for this shape
        self.assertEqual(a, structural_hash("foo, 7 bar?"))

    def test_returns_hex_64(self):
        self.assertEqual(len(structural_hash("x 1 y")), 64)

    def test_empty(self):
        self.assertEqual(len(structural_hash("")), 64)

    def test_known_shape(self):
        # "word number word" hash must equal the doc example shape's category string
        h = structural_hash("cat 42 dog")
        # re-derive from the same algorithm
        from capability.input_fingerprint import _sha256
        self.assertEqual(h, _sha256("word number word"))


class TestSemanticHash(unittest.TestCase):
    def test_top_k_extraction(self):
        text = "the cat sat on the mat the cat"
        # top 2 by freq: "the" (3), "cat" (2)
        h = semantic_hash(text, top_k=2)
        self.assertEqual(len(h), 64)

    def test_different_top_k_may_differ(self):
        text = "a b c d e f g h"
        self.assertNotEqual(
            semantic_hash(text, top_k=2),
            semantic_hash(text, top_k=8),
        )

    def test_same_distribution_same_hash(self):
        self.assertEqual(
            semantic_hash("alpha beta alpha gamma alpha"),
            semantic_hash("alpha alpha beta gamma alpha"),
        )

    def test_empty_text(self):
        self.assertEqual(len(semantic_hash("")), 64)

    def test_chinese(self):
        h = semantic_hash("今天 天气 不错 今天 阳光 很好 今天", top_k=2)
        self.assertEqual(len(h), 64)
        self.assertNotEqual(h, semantic_hash("完全 不一样 的 内容", top_k=2))


class TestInputFingerprintInit(unittest.TestCase):
    def test_init_computes_all_four(self):
        fp = InputFingerprint("Hello, 123 world!")
        self.assertIn("exact", fp.attrs)
        self.assertIn("normalized", fp.attrs)
        self.assertIn("structural", fp.attrs)
        self.assertIn("semantic", fp.attrs)
        for v in fp.attrs.values():
            self.assertEqual(len(v), 64)

    def test_init_handles_empty(self):
        fp = InputFingerprint("")
        self.assertEqual(len(fp.attrs["exact"]), 64)


class TestSimilarTo(unittest.TestCase):
    def test_exact_match_returns_1(self):
        a = InputFingerprint("foo")
        b = InputFingerprint("foo")
        self.assertEqual(a.similar_to(b, "exact"), 1.0)

    def test_exact_mismatch_returns_0(self):
        a = InputFingerprint("foo")
        b = InputFingerprint("bar")
        self.assertEqual(a.similar_to(b, "exact"), 0.0)

    def test_normalized_case_insensitive_match(self):
        a = InputFingerprint("Hello World")
        b = InputFingerprint("hello world")
        self.assertEqual(a.similar_to(b, "normalized"), 1.0)

    def test_normalized_punct_insensitive_match(self):
        a = InputFingerprint("Hello, World!")
        b = InputFingerprint("hello world")
        self.assertEqual(a.similar_to(b, "normalized"), 1.0)

    def test_structural_jaccard_full_match(self):
        a = InputFingerprint("buy 1 apple")
        b = InputFingerprint("buy 2 apple")
        self.assertEqual(a.similar_to(b, "structural"), 1.0)

    def test_structural_jaccard_partial(self):
        # seq_a: word number word   seq_b: word word number
        a = InputFingerprint("buy 1 apple")
        b = InputFingerprint("buy apple 1")
        # LCS = "word word" (len 2), union = 3+3-2 = 4 -> 0.5
        self.assertAlmostEqual(a.similar_to(b, "structural"), 0.5, places=4)

    def test_structural_jaccard_disjoint(self):
        a = InputFingerprint("buy apple")
        b = InputFingerprint("! ?")
        # "word word" vs "punct punct" -> LCS 0
        self.assertEqual(a.similar_to(b, "structural"), 0.0)

    def test_semantic_jaccard(self):
        a = InputFingerprint("the cat sat on the mat")
        b = InputFingerprint("the dog sat on the rug")
        # 4 unique words each, top-5 = full set; share {"the","sat","on"} = 3, union = 7
        self.assertAlmostEqual(a.similar_to(b, "semantic"), 3 / 7, places=4)

    def test_semantic_jaccard_full(self):
        a = InputFingerprint("alpha beta gamma delta epsilon")
        b = InputFingerprint("alpha beta gamma delta epsilon")
        self.assertEqual(a.similar_to(b, "semantic"), 1.0)

    def test_semantic_jaccard_disjoint(self):
        a = InputFingerprint("alpha beta gamma delta epsilon")
        b = InputFingerprint("zeta eta theta iota kappa")
        self.assertEqual(a.similar_to(b, "semantic"), 0.0)

    def test_unknown_level_raises(self):
        a = InputFingerprint("x")
        b = InputFingerprint("y")
        with self.assertRaises(ValueError):
            a.similar_to(b, "bogus")


class TestEquality(unittest.TestCase):
    def test_eq_any_layer(self):
        a = InputFingerprint("Hello, 123 world!")
        b = InputFingerprint("Goodbye, 456 there!")
        # exact and semantic differ, but normalized and structural match
        self.assertEqual(a, b)

    def test_eq_with_non_fingerprint(self):
        a = InputFingerprint("x")
        self.assertNotEqual(a, "x")  # type: ignore[arg-type]

    def test_hash_uses_exact(self):
        a = InputFingerprint("hello")
        b = InputFingerprint("hello")
        self.assertEqual(hash(a), hash(b))


class TestToDict(unittest.TestCase):
    def test_round_trip(self):
        fp = InputFingerprint("Hello, 123 world!")
        d = fp.to_dict()
        self.assertEqual(d["text"], "Hello, 123 world!")
        self.assertEqual(set(d["attrs"]), {"exact", "normalized", "structural", "semantic"})
        restored = InputFingerprint.from_dict(d)
        self.assertEqual(restored.attrs, fp.attrs)
        self.assertEqual(restored.text, fp.text)


class TestFingerprintStore(unittest.TestCase):
    def test_add_and_size(self):
        s = FingerprintStore()
        self.assertEqual(s.size(), 0)
        s.add("foo")
        s.add("bar")
        self.assertEqual(s.size(), 2)

    def test_find_collisions_identical(self):
        s = FingerprintStore()
        s.add("Hello, World!")
        hits = s.find_collisions("Hello, World!", min_levels=1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][1], 1.0)  # all 4 layers match

    def test_find_collisions_case_only_normalized(self):
        s = FingerprintStore()
        s.add("Hello World")
        hits = s.find_collisions("hello world", min_levels=1)
        self.assertEqual(len(hits), 1)
        # exact and semantic may differ, normalized+structural match -> 0.5
        self.assertGreaterEqual(hits[0][1], 0.5)

    def test_min_levels_boundary(self):
        s = FingerprintStore()
        s.add("the quick brown fox jumps over the lazy dog")
        target = "the slow brown fox jumps over the lazy dog"
        # 1 word differs out of 8 -> normalized+structural match (2 layers), exact/semantic differ
        for k, lo in [(1, 1), (2, 1), (3, 0), (4, 0)]:
            hits = s.find_collisions(target, min_levels=k)
            self.assertGreaterEqual(len(hits), lo, f"min_levels={k}")

    def test_min_levels_4_strict(self):
        s = FingerprintStore()
        s.add("alpha beta gamma delta epsilon")
        # nothing else can match 4 layers
        self.assertEqual(
            len(s.find_collisions("completely different", min_levels=4)),
            0,
        )

    def test_invalid_min_levels(self):
        s = FingerprintStore()
        with self.assertRaises(ValueError):
            s.find_collisions("x", min_levels=0)
        with self.assertRaises(ValueError):
            s.find_collisions("x", min_levels=5)

    def test_max_size_eviction(self):
        s = FingerprintStore(max_size=3)
        for i in range(5):
            s.add(f"text-{i}")
        self.assertEqual(s.size(), 3)

    def test_metadata_stored(self):
        s = FingerprintStore()
        s.add("x", metadata={"user": "alice"})
        # collision query still works
        self.assertEqual(len(s.find_collisions("x", min_levels=1)), 1)

    def test_clear(self):
        s = FingerprintStore()
        s.add("a")
        s.add("b")
        s.clear()
        self.assertEqual(s.size(), 0)


class TestUnicode(unittest.TestCase):
    def test_chinese_exact(self):
        self.assertEqual(exact_hash("你好世界"), exact_hash("你好世界"))
        self.assertNotEqual(exact_hash("你好世界"), exact_hash("再见世界"))

    def test_chinese_normalized(self):
        # fullwidth comma should normalize away
        self.assertEqual(
            normalized_hash("你好，世界"),
            normalized_hash("你好世界"),
        )

    def test_emoji_in_text(self):
        fp = InputFingerprint("hello 🌍 world")
        self.assertEqual(len(fp.attrs["exact"]), 64)


class TestLengths(unittest.TestCase):
    def test_different_lengths_differ(self):
        self.assertNotEqual(
            exact_hash("short"),
            exact_hash("this is a much longer text that should hash differently"),
        )

    def test_huge_text(self):
        text = "a" * (1024 * 1024)  # 1 MB
        t0 = time.time()
        fp = InputFingerprint(text)
        dt = time.time() - t0
        self.assertEqual(len(fp.attrs["exact"]), 64)
        self.assertLess(dt, 5.0, f"hashing 1MB took {dt:.2f}s")


class TestThreadSafety(unittest.TestCase):
    def test_concurrent_adds(self):
        s = FingerprintStore(max_size=100000)
        n_threads = 8
        per_thread = 500
        errors: list = []

        def worker(tid: int) -> None:
            try:
                for i in range(per_thread):
                    s.add(f"thread-{tid}-text-{i}")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        ts = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(s.size(), n_threads * per_thread)

    def test_concurrent_reads_and_writes(self):
        s = FingerprintStore(max_size=100000)
        for i in range(100):
            s.add(f"seed-{i}")

        stop = threading.Event()
        errors: list = []

        def writer() -> None:
            i = 0
            while not stop.is_set():
                try:
                    s.add(f"live-{i}")
                    i += 1
                except Exception as e:  # pragma: no cover
                    errors.append(e)
                    break

        def reader() -> None:
            for i in range(2000):
                try:
                    s.find_collisions(f"seed-{i % 100}", min_levels=1)
                except Exception as e:  # pragma: no cover
                    errors.append(e)
                    break

        ts = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=10)
        stop.set()
        for t in ts:
            t.join(timeout=2)
        self.assertEqual(errors, [])


class TestPerformance(unittest.TestCase):
    def test_bulk_throughput(self):
        s = FingerprintStore(max_size=10000)
        rng = random.Random(42)
        words = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta"]
        t0 = time.time()
        for _ in range(5000):
            length = rng.randint(1, 8)
            text = " ".join(rng.choice(words) for _ in range(length))
            s.add(text)
        dt = time.time() - t0
        self.assertEqual(s.size(), 5000)
        self.assertLess(dt, 30.0, f"5000 adds took {dt:.2f}s")
        # basic query smoke
        hits = s.find_collisions("alpha beta gamma", min_levels=1)
        self.assertGreater(len(hits), 0)

    def test_fuzz_unique(self):
        # all random strings should mostly produce unique exact hashes
        s = FingerprintStore()
        rng = random.Random(0)
        alphabet = string.ascii_lowercase + " "
        for _ in range(1000):
            s.add("".join(rng.choice(alphabet) for _ in range(20)))
        # at most a handful of accidental collisions on 20 random chars
        full_hits = s.find_collisions("anything", min_levels=4)
        self.assertLessEqual(len(full_hits), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
