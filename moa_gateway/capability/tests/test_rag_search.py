"""Tests for the rag_search capability module."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
CAP_DIR = os.path.dirname(THIS_DIR)
if CAP_DIR not in sys.path:
    sys.path.insert(0, CAP_DIR)

import contextlib

import rag_search  # noqa: E402
from rag_search import (  # noqa: E402
    clear_cache,
    rag_search,
    set_cache_db_path,
)

CORPUS_BASIC = [
    {
        "id": "doc-1",
        "text": "The quick brown fox jumps over the lazy dog",
        "tags": ["animals", "english"],
    },
    {
        "id": "doc-2",
        "text": "Python is a programming language used for scripting and data science",
        "tags": ["programming", "python"],
    },
    {
        "id": "doc-3",
        "text": "Machine learning models learn from data and make predictions",
        "tags": ["ml", "ai"],
    },
    {
        "id": "doc-4",
        "text": "Cats and dogs are common household pets",
        "tags": ["animals", "pets"],
    },
]


class RagSearchTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(
            suffix=".sqlite3", delete=False
        )
        self._tmp.close()
        set_cache_db_path(self._tmp.name)
        clear_cache()

    def tearDown(self) -> None:
        with contextlib.suppress(OSError):
            os.unlink(self._tmp.name)


class TestBasicKeywordMatching(RagSearchTestBase):
    def test_simple_keyword_match(self) -> None:
        results = rag_search("python programming", CORPUS_BASIC)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "doc-2")
        self.assertGreater(results[0]["score"], 0.0)

    def test_multi_word_query_picks_best_doc(self) -> None:
        results = rag_search("data science machine learning", CORPUS_BASIC, max_results=2)
        ids = [r["id"] for r in results]
        self.assertIn("doc-3", ids)
        self.assertEqual(results[0]["id"], "doc-3")

    def test_animal_query(self) -> None:
        results = rag_search("dog cat pets", CORPUS_BASIC, max_results=3)
        ids = [r["id"] for r in results]
        self.assertIn("doc-4", ids)
        self.assertIn("doc-1", ids)

    def test_single_token_query(self) -> None:
        results = rag_search("python", CORPUS_BASIC)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "doc-2")

    def test_no_match_returns_empty(self) -> None:
        results = rag_search("cryptocurrency blockchain", CORPUS_BASIC)
        self.assertEqual(results, [])


class TestStopWordFiltering(RagSearchTestBase):
    def test_stop_words_filtered(self) -> None:
        results_with = rag_search("the fox", CORPUS_BASIC)
        results_without = rag_search("fox", CORPUS_BASIC)
        self.assertEqual(len(results_with), len(results_without))
        if results_with and results_without:
            self.assertAlmostEqual(
                results_with[0]["score"], results_without[0]["score"], places=6
            )

    def test_query_of_only_stop_words(self) -> None:
        results = rag_search("the is a an", CORPUS_BASIC)
        self.assertEqual(results, [])

    def test_common_verbs_filtered(self) -> None:
        r1 = rag_search("are pets", CORPUS_BASIC)
        r2 = rag_search("pets", CORPUS_BASIC)
        self.assertEqual(len(r1), len(r2))
        if r1 and r2:
            self.assertAlmostEqual(r1[0]["score"], r2[0]["score"], places=6)


class TestCaseInsensitivity(RagSearchTestBase):
    def test_uppercase_query(self) -> None:
        r1 = rag_search("PYTHON", CORPUS_BASIC)
        r2 = rag_search("python", CORPUS_BASIC)
        self.assertEqual(len(r1), len(r2))
        self.assertEqual(r1[0]["id"], r2[0]["id"])

    def test_mixed_case_text(self) -> None:
        corpus = [
            {"id": "a", "text": "PyThOn ProGRamming", "tags": []},
            {"id": "b", "text": "java programming", "tags": []},
        ]
        results = rag_search("python", corpus)
        self.assertEqual(results[0]["id"], "a")

    def test_uppercase_does_not_match_lowercase_only_when_normalized(self) -> None:
        results = rag_search("FOX", CORPUS_BASIC)
        self.assertTrue(any(r["id"] == "doc-1" for r in results))


class TestPunctuationHandling(RagSearchTestBase):
    def test_punctuation_in_query(self) -> None:
        results = rag_search("python, programming.", CORPUS_BASIC)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "doc-2")

    def test_punctuation_in_text(self) -> None:
        corpus = [
            {"id": "x", "text": "Hello, world! This is great.", "tags": []},
        ]
        results = rag_search("hello world great", corpus)
        self.assertEqual(len(results), 1)

    def test_symbols_and_numbers(self) -> None:
        corpus = [
            {"id": "x", "text": "Price: $100.50 (USD) - 50% off!", "tags": []},
        ]
        results = rag_search("price usd 100", corpus)
        self.assertEqual(len(results), 1)

    def test_extra_whitespace(self) -> None:
        r1 = rag_search("python   programming", CORPUS_BASIC)
        r2 = rag_search("python programming", CORPUS_BASIC)
        self.assertEqual([x["id"] for x in r1], [x["id"] for x in r2])


class TestSortOrder(RagSearchTestBase):
    def test_descending_score_order(self) -> None:
        results = rag_search("data", CORPUS_BASIC, max_results=5)
        scores = [r["score"] for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_better_match_ranks_first(self) -> None:
        results = rag_search("data science machine learning", CORPUS_BASIC, max_results=2)
        self.assertEqual(results[0]["id"], "doc-3")
        self.assertGreater(results[0]["score"], results[1]["score"])


class TestMaxResultsLimit(RagSearchTestBase):
    def test_max_results_caps_output(self) -> None:
        results = rag_search("data", CORPUS_BASIC, max_results=1)
        self.assertEqual(len(results), 1)

    def test_max_results_zero(self) -> None:
        results = rag_search("data", CORPUS_BASIC, max_results=0)
        self.assertEqual(results, [])

    def test_max_results_negative(self) -> None:
        results = rag_search("data", CORPUS_BASIC, max_results=-3)
        self.assertEqual(results, [])

    def test_max_results_larger_than_corpus(self) -> None:
        results = rag_search("fox dog", CORPUS_BASIC, max_results=100)
        self.assertLessEqual(len(results), len(CORPUS_BASIC))


class TestEmptyInputs(RagSearchTestBase):
    def test_empty_query(self) -> None:
        results = rag_search("", CORPUS_BASIC)
        self.assertEqual(results, [])

    def test_empty_corpus(self) -> None:
        results = rag_search("python", [])
        self.assertEqual(results, [])

    def test_empty_query_and_corpus(self) -> None:
        self.assertEqual(rag_search("", []), [])

    def test_none_query(self) -> None:
        self.assertEqual(rag_search(None, CORPUS_BASIC), [])

    def test_none_corpus(self) -> None:
        self.assertEqual(rag_search("python", None), [])

    def test_invalid_max_results_type(self) -> None:
        self.assertEqual(rag_search("python", CORPUS_BASIC, max_results="3"), [])


class TestCaching(RagSearchTestBase):
    def test_cache_miss_then_hit(self) -> None:
        clear_cache()
        first = rag_search("python programming", CORPUS_BASIC, max_results=2)
        second = rag_search("python programming", CORPUS_BASIC, max_results=2)
        self.assertEqual(first, second)
        self.assertGreater(len(first), 0)

    def test_cache_different_max_results_is_separate(self) -> None:
        r1 = rag_search("python", CORPUS_BASIC, max_results=1)
        r2 = rag_search("python", CORPUS_BASIC, max_results=5)
        self.assertEqual(len(r1), 1)
        self.assertEqual(len(r2), 1)

    def test_cache_expired(self) -> None:
        clear_cache()

        from rag_search import _cache_put, _hash_query
        h = _hash_query("python", 3)
        with open(self._tmp.name, "rb"):
            pass
        _cache_put(h, [{"id": "stale", "text": "old", "score": 0.99, "tags": []}])
        time.sleep(1.1)
        results = rag_search("python", CORPUS_BASIC, max_results=3, ttl_hours=0)
        self.assertNotEqual(results, [{"id": "stale", "text": "old", "score": 0.99, "tags": []}])
        self.assertEqual(results[0]["id"], "doc-2")

    def test_cache_disabled_with_zero_ttl(self) -> None:
        clear_cache()
        rag_search("python", CORPUS_BASIC, max_results=3, ttl_hours=0)
        results = rag_search("python", CORPUS_BASIC, max_results=3, ttl_hours=0)
        self.assertEqual(results[0]["id"], "doc-2")

    def test_cache_handles_corrupt_row(self) -> None:
        clear_cache()
        from rag_search import _hash_query
        h = _hash_query("python", 3)
        import sqlite3
        conn = sqlite3.connect(self._tmp.name)
        conn.execute(
            "INSERT OR REPLACE INTO rag_cache (query_hash, results_json, created_at) "
            "VALUES (?, ?, ?)",
            (h, "{not json", time.time()),
        )
        conn.commit()
        conn.close()
        results = rag_search("python", CORPUS_BASIC, max_results=3)
        self.assertEqual(results[0]["id"], "doc-2")


class TestTermFrequencyWeighting(RagSearchTestBase):
    def test_repeated_query_term_changes_score(self) -> None:
        once = rag_search("python", CORPUS_BASIC, max_results=1)
        repeated = rag_search("python python python", CORPUS_BASIC, max_results=1)
        self.assertEqual(once[0]["id"], repeated[0]["id"])
        self.assertNotAlmostEqual(once[0]["score"], repeated[0]["score"], places=6)

    def test_repeated_text_term_in_doc(self) -> None:
        corpus = [
            {"id": "a", "text": "data data data science", "tags": []},
            {"id": "b", "text": "data science", "tags": []},
        ]
        results = rag_search("data science", corpus, max_results=2)
        self.assertEqual(results[0]["id"], "a")
        self.assertGreater(results[0]["score"], results[1]["score"])

    def test_zero_score_items_excluded(self) -> None:
        corpus = [
            {"id": "a", "text": "completely unrelated content", "tags": []},
        ]
        results = rag_search("python", corpus)
        self.assertEqual(results, [])


class TestTagsPreservation(RagSearchTestBase):
    def test_tags_preserved(self) -> None:
        results = rag_search("python", CORPUS_BASIC)
        self.assertEqual(results[0]["tags"], ["programming", "python"])

    def test_empty_tags_preserved(self) -> None:
        corpus = [{"id": "x", "text": "python language", "tags": []}]
        results = rag_search("python", corpus)
        self.assertEqual(results[0]["tags"], [])

    def test_missing_tags_default_empty(self) -> None:
        corpus = [{"id": "x", "text": "python language"}]
        results = rag_search("python", corpus)
        self.assertEqual(results[0]["tags"], [])

    def test_multiple_results_carry_own_tags(self) -> None:
        results = rag_search("dog", CORPUS_BASIC, max_results=3)
        tag_map = {r["id"]: r["tags"] for r in results}
        if "doc-1" in tag_map:
            self.assertEqual(tag_map["doc-1"], ["animals", "english"])
        if "doc-4" in tag_map:
            self.assertEqual(tag_map["doc-4"], ["animals", "pets"])


class TestErrorResilience(RagSearchTestBase):
    def test_malformed_corpus_item_ignored(self) -> None:
        corpus = [
            None,
            "not a dict",
            42,
            {"id": "ok", "text": "python language", "tags": []},
        ]
        results = rag_search("python", corpus)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "ok")

    def test_returns_list_with_score_float(self) -> None:
        results = rag_search("python", CORPUS_BASIC)
        self.assertIsInstance(results[0]["score"], float)


if __name__ == "__main__":
    unittest.main(verbosity=2)
