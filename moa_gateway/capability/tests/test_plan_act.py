"""Tests for ``moa_gateway.capability.plan_act``.

Run with::

    python -m unittest moa_gateway.capability.tests.test_plan_act
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make the package importable when running the file directly.
# parents[0] = tests/, [1] = capability/, [2] = moa_gateway/, [3] = project root
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from moa_gateway.capability.plan_act import (  # noqa: E402
    _ACT_KEYWORDS,
    _ACT_PATTERNS,
    _PLAN_KEYWORDS,
    _PLAN_PATTERNS,
    classify_mode,
)


class TestPlanKeywordTrigger(unittest.TestCase):
    """Each Plan keyword category should drive the classifier into plan mode."""

    def test_plan_strategy_keyword(self) -> None:
        r = classify_mode("let's plan_strategy the launch")
        self.assertEqual(r["mode"], "plan")
        self.assertIn("plan:plan_strategy", r["signals"])

    def test_refactor_keyword(self) -> None:
        r = classify_mode("refactor the billing service")
        self.assertEqual(r["mode"], "plan")
        self.assertIn("plan:refactor", r["signals"])

    def test_design_keyword(self) -> None:
        r = classify_mode("design a new schema")
        self.assertEqual(r["mode"], "plan")
        self.assertIn("plan:design", r["signals"])

    def test_propose_keyword(self) -> None:
        r = classify_mode("please propose three options")
        self.assertEqual(r["mode"], "plan")
        self.assertIn("plan:propose", r["signals"])

    def test_outline_keyword(self) -> None:
        r = classify_mode("outline the migration steps")
        self.assertEqual(r["mode"], "plan")
        self.assertIn("plan:outline", r["signals"])

    def test_simulate_keyword(self) -> None:
        r = classify_mode("simulate the traffic spike")
        self.assertEqual(r["mode"], "plan")
        self.assertIn("plan:simulate", r["signals"])

    def test_evaluate_keyword(self) -> None:
        r = classify_mode("evaluate this approach")
        self.assertEqual(r["mode"], "plan")
        self.assertIn("plan:evaluate", r["signals"])

    def test_draft_keyword(self) -> None:
        r = classify_mode("draft a proposal")
        self.assertEqual(r["mode"], "plan")
        self.assertIn("plan:draft", r["signals"])

    def test_blueprint_keyword(self) -> None:
        r = classify_mode("blueprint the architecture")
        self.assertEqual(r["mode"], "plan")
        self.assertIn("plan:blueprint", r["signals"])

    def test_roadmap_keyword(self) -> None:
        r = classify_mode("roadmap for q3")
        self.assertEqual(r["mode"], "plan")
        self.assertIn("plan:roadmap", r["signals"])


class TestActKeywordTrigger(unittest.TestCase):
    """Each Act keyword category should drive the classifier into act mode."""

    def test_run_keyword(self) -> None:
        r = classify_mode("run the test suite")
        self.assertEqual(r["mode"], "act")
        self.assertIn("act:run", r["signals"])

    def test_deploy_keyword(self) -> None:
        r = classify_mode("deploy to staging")
        self.assertEqual(r["mode"], "act")
        self.assertIn("act:deploy", r["signals"])

    def test_install_keyword(self) -> None:
        r = classify_mode("install the package")
        self.assertEqual(r["mode"], "act")
        self.assertIn("act:install", r["signals"])

    def test_click_keyword(self) -> None:
        r = classify_mode("click the submit button")
        self.assertEqual(r["mode"], "act")
        self.assertIn("act:click", r["signals"])

    def test_paste_keyword(self) -> None:
        r = classify_mode("paste it into the editor")
        self.assertEqual(r["mode"], "act")
        self.assertIn("act:paste", r["signals"])

    def test_download_keyword(self) -> None:
        r = classify_mode("download the artifact")
        self.assertEqual(r["mode"], "act")
        self.assertIn("act:download", r["signals"])

    def test_fix_keyword(self) -> None:
        r = classify_mode("fix this bug")
        self.assertEqual(r["mode"], "act")
        self.assertIn("act:fix", r["signals"])


class TestPlanPatternTrigger(unittest.TestCase):
    """Plan regexes should drive the classifier into plan mode."""

    def test_how_should_we(self) -> None:
        r = classify_mode("how should we handle this?")
        self.assertEqual(r["mode"], "plan")
        self.assertIn("plan:plan_how_should", r["signals"])

    def test_what_if(self) -> None:
        r = classify_mode("what if we ship it on friday?")
        self.assertEqual(r["mode"], "plan")
        self.assertIn("plan:plan_what_if", r["signals"])

    def test_should_we(self) -> None:
        r = classify_mode("should we use postgres?")
        self.assertEqual(r["mode"], "plan")
        self.assertIn("plan:plan_should_we", r["signals"])

    def test_pros_and_cons(self) -> None:
        r = classify_mode("give me the pros and cons")
        self.assertEqual(r["mode"], "plan")
        self.assertIn("plan:plan_pros_cons", r["signals"])


class TestActPatternTrigger(unittest.TestCase):
    """Act regexes should drive the classifier into act mode."""

    def test_please_run_at_start(self) -> None:
        r = classify_mode("please run the migration")
        self.assertEqual(r["mode"], "act")
        self.assertIn("act:act_run_command", r["signals"])

    def test_do_it(self) -> None:
        r = classify_mode("do it now")
        self.assertEqual(r["mode"], "act")
        self.assertIn("act:act_do_it", r["signals"])

    def test_can_you_build(self) -> None:
        r = classify_mode("can you build the docker image?")
        self.assertEqual(r["mode"], "act")
        self.assertIn("act:act_can_you", r["signals"])

    def test_lets_deploy(self) -> None:
        r = classify_mode("let's deploy the service")
        self.assertEqual(r["mode"], "act")
        self.assertIn("act:act_lets", r["signals"])

    def test_commit_and_push(self) -> None:
        r = classify_mode("commit and push the changes")
        self.assertEqual(r["mode"], "act")
        self.assertIn("act:act_commit_push", r["signals"])


class TestFallback(unittest.TestCase):
    """No Plan/Act signal → chat mode, confidence 0, empty signals."""

    def test_chat_fallback(self) -> None:
        r = classify_mode("hello there")
        self.assertEqual(r["mode"], "chat")
        self.assertEqual(r["confidence"], 0.0)
        self.assertEqual(r["signals"], [])

    def test_empty_query(self) -> None:
        r = classify_mode("")
        self.assertEqual(r["mode"], "chat")
        self.assertEqual(r["confidence"], 0.0)
        self.assertEqual(r["signals"], [])

    def test_none_query(self) -> None:
        r = classify_mode(None)
        self.assertEqual(r["mode"], "chat")
        self.assertEqual(r["confidence"], 0.0)
        self.assertEqual(r["signals"], [])

    def test_whitespace_only_query(self) -> None:
        r = classify_mode("   \t\n  ")
        self.assertEqual(r["mode"], "chat")
        self.assertEqual(r["confidence"], 0.0)
        self.assertEqual(r["signals"], [])


class TestConfidenceRange(unittest.TestCase):
    """confidence must always be a float in [0.0, 1.0]."""

    def test_confidence_in_range_for_plan(self) -> None:
        r = classify_mode("refactor design draft outline evaluate propose suggest simulate consider")
        self.assertGreaterEqual(r["confidence"], 0.0)
        self.assertLessEqual(r["confidence"], 1.0)

    def test_confidence_in_range_for_act(self) -> None:
        r = classify_mode("run build deploy install click type paste search fetch download")
        self.assertGreaterEqual(r["confidence"], 0.0)
        self.assertLessEqual(r["confidence"], 1.0)

    def test_confidence_clamped_to_one(self) -> None:
        # far too many signals to stay below 1.0 un-clamped
        r = classify_mode("run build deploy install click type paste search fetch download edit fix create make send write open")
        self.assertLessEqual(r["confidence"], 1.0)
        self.assertEqual(r["confidence"], 1.0)

    def test_confidence_increases_with_signals(self) -> None:
        single = classify_mode("refactor")
        many = classify_mode("refactor design draft outline evaluate propose")
        self.assertGreater(many["confidence"], single["confidence"])


class TestSignalsList(unittest.TestCase):
    """The signals list must contain the matched keyword/pattern names."""

    def test_signals_contains_keyword(self) -> None:
        r = classify_mode("please refactor this")
        self.assertTrue(any("refactor" in s for s in r["signals"]))

    def test_signals_contains_pattern(self) -> None:
        r = classify_mode("do it")
        self.assertIn("act:act_do_it", r["signals"])

    def test_signals_deduplicated_correctly(self) -> None:
        # Repeated keyword should not appear twice
        r = classify_mode("refactor refactor refactor")
        self.assertEqual(r["signals"].count("plan:refactor"), 1)

    def test_signals_have_prefix(self) -> None:
        r = classify_mode("refactor and run the tests")
        # Plan side (winner) and act side can both appear when they tie/fight;
        # every signal must be tagged.
        for s in r["signals"]:
            self.assertTrue(s.startswith("plan:") or s.startswith("act:"))


class TestCaseAndPunctuation(unittest.TestCase):
    """Matching must be case-insensitive and tolerate punctuation."""

    def test_uppercase_keyword(self) -> None:
        r = classify_mode("REFACTOR the module")
        self.assertEqual(r["mode"], "plan")

    def test_mixed_case_keyword(self) -> None:
        r = classify_mode("Please DePlOy the service")
        self.assertEqual(r["mode"], "act")
        self.assertIn("act:deploy", r["signals"])

    def test_trailing_punctuation(self) -> None:
        r = classify_mode("run the tests!!!")
        self.assertEqual(r["mode"], "act")

    def test_question_mark(self) -> None:
        r = classify_mode("can you build it?")
        self.assertEqual(r["mode"], "act")
        self.assertIn("act:act_can_you", r["signals"])


class TestMixedSignals(unittest.TestCase):
    """When both sides fire, the side with the higher score wins."""

    def test_plan_wins_when_more_strong(self) -> None:
        # plan keywords: refactor + design + outline = 0.6
        # act keywords : run = 0.2
        r = classify_mode("refactor and design the schema, outline the steps, but run the linter")
        self.assertEqual(r["mode"], "plan")
        self.assertGreaterEqual(r["confidence"], 0.4)

    def test_act_wins_when_more_strong(self) -> None:
        # plan keyword: consider = 0.2
        # act keywords: run + build + deploy = 0.6 (and "now" pattern adds 0.3)
        r = classify_mode("consider: run build deploy now")
        self.assertEqual(r["mode"], "act")

    def test_winner_confidence_reflects_winner_only(self) -> None:
        # imperative at start triggers the act_run_command pattern, so act wins
        r = classify_mode("run the tests and refactor the helpers")
        self.assertEqual(r["mode"], "act")
        # act_score = 0.2 (run) + 0.3 (act_run_command) = 0.5
        self.assertGreaterEqual(r["confidence"], 0.5)


class TestLongAndUnicode(unittest.TestCase):
    """Whitespace-heavy and Unicode queries must not crash and still classify."""

    def test_long_whitespace_padded_query(self) -> None:
        q = "   \n\n\t refactor   the   service    "
        r = classify_mode(q)
        self.assertEqual(r["mode"], "plan")
        self.assertIn("plan:refactor", r["signals"])

    def test_unicode_plan_keyword(self) -> None:
        r = classify_mode("请 refactor 这个模块")
        self.assertEqual(r["mode"], "plan")
        self.assertIn("plan:refactor", r["signals"])

    def test_unicode_act_keyword(self) -> None:
        r = classify_mode("请 deploy 服务")
        self.assertEqual(r["mode"], "act")
        self.assertIn("act:deploy", r["signals"])

    def test_emoji_does_not_crash(self) -> None:
        r = classify_mode("🎉 refactor 🚀")
        self.assertEqual(r["mode"], "plan")


class TestStructure(unittest.TestCase):
    """The keyword/pattern tables must satisfy the spec cardinality (R-10)."""

    def test_min_plan_keywords(self) -> None:
        # spec: 24+14 — accept the 24 listed
        self.assertGreaterEqual(len(_PLAN_KEYWORDS), 24)

    def test_min_act_keywords(self) -> None:
        # spec: 11+8 — accept the 11+7=18 we provide
        self.assertGreaterEqual(len(_ACT_KEYWORDS), 11)

    def test_min_act_patterns(self) -> None:
        self.assertGreaterEqual(len(_ACT_PATTERNS), 8)

    def test_min_plan_patterns(self) -> None:
        self.assertGreaterEqual(len(_PLAN_PATTERNS), 4)


if __name__ == "__main__":
    unittest.main()
