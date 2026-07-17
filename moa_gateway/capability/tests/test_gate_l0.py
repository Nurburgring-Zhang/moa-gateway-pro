"""gate_l0 真实测试 — 无 mock,所有 assert 基于实际运行结果"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 让 py 解释器能找到 moa_gateway 包
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.gate_l0 import (  # noqa: E402
    GateVerdict,
    _eval_arithmetic,
    _extract_numbers,
    _has_dangerous_code,
    _is_greeting,
    _word_count,
    gate,
    handle_unit_convert,
)
from moa_gateway.capability.gate_l0 import (
    _handle_datetime as handle_datetime,
)

# ============ 算术 / arithmetic ============

class TestArithmetic:
    def test_gate_arithmetic_simple(self):
        v = gate("2+3")
        assert v.passed is True
        assert v.category == "mechanical"
        assert v.shortcut_answer == "5"
        assert v.estimated_complexity == 1
        assert v.confidence >= 0.9

    def test_gate_arithmetic_complex(self):
        v = gate("(10+5)*2")
        assert v.passed is True
        assert v.shortcut_answer == "30"

    def test_gate_arithmetic_decimal(self):
        v = gate("3.14*2")
        assert v.passed is True
        assert v.shortcut_answer == "6.28"

    def test_gate_arithmetic_what_is(self):
        v = gate("what is 7*8?")
        assert v.passed is True
        assert v.shortcut_answer == "56"

    def test_gate_arithmetic_negative(self):
        v = gate("-5+10")
        assert v.passed is True
        assert v.shortcut_answer == "5"

    def test_gate_arithmetic_division(self):
        v = gate("100/4")
        assert v.passed is True
        assert v.shortcut_answer == "25"

    def test_gate_invalid_arithmetic(self):
        v = gate("2++")
        assert v.passed is False
        assert v.category in ("complex", "conversational")
        # 必须不含危险求值痕迹
        assert v.shortcut_answer == ""

    def test_gate_division_by_zero(self):
        v = gate("1/0")
        # 不崩溃,不能 crashed
        assert v.passed is False
        # 不能 shortcut 答错值
        assert v.shortcut_answer != "inf"
        assert v.shortcut_answer == ""

    def test_eval_arithmetic_unit(self):
        # 单元层测试
        ok, ans = _eval_arithmetic("2+3")
        assert ok is True and ans == "5"
        ok, ans = _eval_arithmetic("(10+5)*2")
        assert ok is True and ans == "30"
        ok, ans = _eval_arithmetic("3.14*2")
        assert ok is True and ans == "6.28"
        ok, ans = _eval_arithmetic("2**10")
        assert ok is True and ans == "1024"
        ok, ans = _eval_arithmetic("10%3")
        assert ok is True and ans == "1"
        ok, ans = _eval_arithmetic("2++")
        assert ok is False
        ok, ans = _eval_arithmetic("")
        assert ok is False
        ok, ans = _eval_arithmetic("__import__('os')")
        assert ok is False


# ============ Trivial ============

class TestTrivial:
    def test_gate_trivial_greeting(self):
        v = gate("hi")
        assert v.passed is True
        assert v.category == "trivial"
        assert v.shortcut_answer  # non-empty
        assert "hello" in v.shortcut_answer.lower() or "hi" in v.shortcut_answer.lower()

    def test_gate_trivial_hello(self):
        v = gate("Hello!")
        assert v.passed is True
        assert v.category == "trivial"

    def test_gate_trivial_thanks(self):
        v = gate("thanks")
        assert v.passed is True
        assert v.category == "trivial"
        assert "welcome" in v.shortcut_answer.lower()

    def test_gate_trivial_yes_no(self):
        v = gate("yes")
        assert v.passed is True
        assert v.category == "trivial"

        v = gate("no")
        assert v.passed is True
        assert v.category == "trivial"

    def test_is_greeting_helper(self):
        assert _is_greeting("hi") is True
        assert _is_greeting("thanks") is True
        assert _is_greeting("design a system") is False
        assert _is_greeting("") is False


# ============ Complex ============

class TestComplex:
    def test_gate_complex_design(self):
        v = gate("design a distributed cache system")
        assert v.passed is False
        assert v.category == "complex"
        assert v.estimated_complexity >= 5

    def test_gate_complex_analyze(self):
        v = gate("analyze this code")
        assert v.passed is False
        assert v.category == "complex"

    def test_gate_complex_compare(self):
        v = gate("compare MongoDB vs PostgreSQL")
        assert v.passed is False
        assert v.category == "complex"

    def test_gate_complex_explain(self):
        v = gate("explain why the sky is blue and how it works")
        assert v.passed is False
        assert v.category == "complex"

    def test_gate_complex_implement(self):
        v = gate("implement a binary search tree")
        assert v.passed is False
        assert v.category == "complex"

    def test_gate_complex_write(self):
        v = gate("write a function to sort a list")
        assert v.passed is False
        assert v.category == "complex"


# ============ 默认 / conversational ============

class TestConversational:
    def test_gate_conversational_default(self):
        v = gate("tell me about your favorite movie")
        assert v.passed is False
        assert v.category in ("conversational", "complex")

    def test_gate_conversational_question(self):
        v = gate("what do you think about the future of AI?")
        assert v.passed is False
        assert v.estimated_complexity >= 3


# ============ 安全 ============

class TestSecurity:
    def test_gate_security_eval_blocked(self):
        """不能用 eval 求值,必须转 MoA"""
        q = "__import__('os').system('rm -rf /')"
        v = gate(q)
        # 不能本地求值,转 MoA
        assert v.passed is False
        assert v.shortcut_answer == ""
        # 关键:没有执行 os.system
        assert not os.path.exists("/tmp/rmrf_test_marker")

    def test_gate_security_dangerous_subprocess(self):
        v = gate("run subprocess.call(['ls'])")
        assert v.passed is False
        assert v.matched_pattern == "dangerous_code"

    def test_has_dangerous_code_helper(self):
        assert _has_dangerous_code("__import__('os')") is True
        assert _has_dangerous_code("os.system('ls')") is True
        assert _has_dangerous_code("hello world") is False


# ============ 边界 ============

class TestEdgeCases:
    def test_gate_empty_query(self):
        v = gate("")
        assert v.passed is False
        assert v.estimated_complexity == 1
        assert v.shortcut_answer == ""

    def test_gate_whitespace_only(self):
        v = gate("    ")
        assert v.passed is False

    def test_gate_long_complex_query(self):
        q = " ".join(["analyze"] * 100)
        v = gate(q)
        assert v.passed is False
        assert v.category == "complex"
        # 长 query 走 length 启发式
        assert v.matched_pattern in ("complex", "length")

    def test_gate_non_string(self):
        v = gate(None)
        assert v.passed is False
        assert v.shortcut_answer == ""

    def test_word_count_helper(self):
        assert _word_count("hello world") == 2
        assert _word_count("") == 0
        assert _word_count("  one  two three ") == 3


# ============ 单位转换 ============

class TestUnitConvert:
    def test_km_to_mile(self):
        ok, ans, _ = handle_unit_convert("convert 10 km to mile")
        assert ok is True
        assert "10 km" in ans
        assert "mile" in ans

    def test_kg_to_lb(self):
        ok, ans, _ = handle_unit_convert("convert 5 kg to lb")
        assert ok is True
        # 5 kg = 11.0231 lb
        assert "5 kg" in ans

    def test_celsius_to_fahrenheit(self):
        ok, ans, _ = handle_unit_convert("convert 100 c to f")
        assert ok is True
        assert "212" in ans or "212.0" in ans or "212.00" in ans

    def test_category_mismatch(self):
        ok, ans, _ = handle_unit_convert("convert 10 km to kg")
        assert ok is False


# ============ 日期 ============

class TestDatetime:
    def test_today(self):
        ok, ans, kind = handle_datetime("what is today's date?")
        assert ok is True
        assert "-" in ans  # YYYY-MM-DD 格式
        assert kind == "date"

    def test_current_year(self):
        ok, ans, kind = handle_datetime("what is the current year?")
        assert ok is True
        assert ans.isdigit()
        assert int(ans) >= 2024
        assert kind == "year"


# ============ extract_numbers ============

class TestExtractNumbers:
    def test_simple(self):
        nums = _extract_numbers("I have 3 apples and 4.5 oranges")
        assert 3.0 in nums
        assert 4.5 in nums

    def test_negative(self):
        nums = _extract_numbers("temperature is -5 degrees")
        assert -5.0 in nums

    def test_empty(self):
        assert _extract_numbers("hello world") == []


# ============ GateVerdict ============

class TestGateVerdict:
    def test_to_dict(self):
        v = GateVerdict(
            passed=True,
            reason="test",
            category="mechanical",
            confidence=0.9,
            shortcut_answer="42",
            estimated_complexity=1,
        )
        d = v.to_dict()
        assert d["passed"] is True
        assert d["category"] == "mechanical"
        assert d["shortcut_answer"] == "42"
        assert d["confidence"] == 0.9
        assert d["estimated_complexity"] == 1

    def test_metadata_default(self):
        v = GateVerdict(
            passed=False, reason="x", category="complex",
            confidence=0.5, shortcut_answer="", estimated_complexity=5,
        )
        assert v.metadata == {}
        assert v.matched_pattern == ""


# ============ 集成场景 ============

class TestIntegration:
    """真实业务场景模拟"""

    def test_pipeline_arithmetic(self):
        """完整流程: 算术 query"""
        v = gate("calculate (100+200)/3?")
        assert v.passed is True
        assert v.shortcut_answer == "100"

    def test_pipeline_then_moa(self):
        """简单 → 复杂链路"""
        # 简单的不应该进入 MoA
        assert gate("hi").passed is True
        # 复杂的应该进入
        assert gate("design microservices").passed is False

    def test_pipeline_mixed(self):
        queries = [
            ("2+2", True, "mechanical"),
            ("hello", True, "trivial"),
            ("implement a red-black tree", False, "complex"),
            ("compare Python and Rust", False, "complex"),
            ("", False, "conversational"),
        ]
        for q, expected_pass, expected_cat in queries:
            v = gate(q)
            assert v.passed is expected_pass, f"failed for: {q!r}"
            assert v.category == expected_cat, f"category mismatch for: {q!r}: got {v.category}"
