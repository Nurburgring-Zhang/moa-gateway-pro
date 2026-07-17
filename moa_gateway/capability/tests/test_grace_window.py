"""grace_window 真实测试 — 端到端验证 (非 mock)

覆盖:
- CheckResult 字段
- GraceConfig 7 天默认
- CheckRegistry register
- record_pass
- record_fail 设 failed_at + grace_until
- should_block pass → False
- should_block fail 在 grace → False
- should_block fail 超 grace → True
- should_block enabled=False → False
- get_warnings 列出 grace 期
- get_warnings 排除 pass
- grace_status "passing"
- grace_status "in_grace"
- grace_status "blocking"
- 边界: 不存在 check_id
- 边界: 0 check
- JSON 序列化
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.grace_window import (
    CheckRegistry,
    CheckResult,
    GraceConfig,
    grace_status,
)

# ============ CheckResult ============

def test_check_result_fields_default():
    cr = CheckResult(check_id="c1", name="n1", passed=True)
    assert cr.check_id == "c1"
    assert cr.name == "n1"
    assert cr.passed is True
    assert cr.failed_at is None
    assert cr.grace_until is None
    print("  ✓ test_check_result_fields_default")
    assert True


def test_check_result_fields_after_fail():
    cr = CheckResult(
        check_id="c1", name="n1", passed=False,
        failed_at=100.0, grace_until=100.0 + 7 * 86400,
    )
    assert cr.failed_at == 100.0
    assert cr.grace_until == 100.0 + 7 * 86400
    print("  ✓ test_check_result_fields_after_fail")
    assert True


def test_check_result_to_dict():
    cr = CheckResult(check_id="x", name="y", passed=False,
                     failed_at=1.0, grace_until=2.0)
    d = cr.to_dict()
    assert d["check_id"] == "x"
    assert d["name"] == "y"
    assert d["passed"] is False
    assert d["failed_at"] == 1.0
    assert d["grace_until"] == 2.0
    print("  ✓ test_check_result_to_dict")
    assert True


def test_check_result_to_json_roundtrip():
    cr = CheckResult(check_id="x", name="y", passed=True)
    j = cr.to_json()
    parsed = json.loads(j)
    assert parsed["check_id"] == "x"
    assert parsed["passed"] is True
    assert parsed["failed_at"] is None
    print("  ✓ test_check_result_to_json_roundtrip")
    assert True


# ============ GraceConfig ============

def test_grace_config_default_is_7_days():
    cfg = GraceConfig()
    assert cfg.grace_seconds == 7 * 86400
    assert cfg.enabled is True
    print("  ✓ test_grace_config_default_is_7_days")
    assert True


def test_grace_config_custom_values():
    cfg = GraceConfig(grace_seconds=3600, enabled=False)
    assert cfg.grace_seconds == 3600
    assert cfg.enabled is False
    print("  ✓ test_grace_config_custom_values")
    assert True


def test_grace_config_negative_clamped_to_zero():
    cfg = GraceConfig(grace_seconds=-100)
    assert cfg.grace_seconds == 0.0
    print("  ✓ test_grace_config_negative_clamped_to_zero")
    assert True


# ============ CheckRegistry.register ============

def test_register_returns_string_check_id():
    reg = CheckRegistry()
    cid = reg.register("lint")
    assert isinstance(cid, str)
    assert len(cid) > 0
    print("  ✓ test_register_returns_string_check_id")
    assert True


def test_register_creates_passing_check():
    reg = CheckRegistry()
    cid = reg.register("lint")
    cr = reg.get(cid)
    assert cr is not None
    assert cr.passed is True
    assert cr.failed_at is None
    assert cr.grace_until is None
    assert cr.name == "lint"
    print("  ✓ test_register_creates_passing_check")
    assert True


def test_register_multiple_unique_ids():
    reg = CheckRegistry()
    ids = {reg.register(f"c-{i}") for i in range(10)}
    assert len(ids) == 10
    print("  ✓ test_register_multiple_unique_ids")
    assert True


# ============ record_pass ============

def test_record_pass_after_fail_resets_to_passing():
    reg = CheckRegistry()
    cid = reg.register("x")
    reg.record_fail(cid, at=100.0)
    assert reg.get(cid).passed is False
    reg.record_pass(cid)
    cr = reg.get(cid)
    assert cr.passed is True
    assert cr.failed_at is None
    assert cr.grace_until is None
    print("  ✓ test_record_pass_after_fail_resets_to_passing")
    assert True


def test_record_pass_on_unknown_id_does_not_raise():
    reg = CheckRegistry()
    reg.record_pass("nonexistent-id")
    print("  ✓ test_record_pass_on_unknown_id_does_not_raise")
    assert True


# ============ record_fail ============

def test_record_fail_sets_failed_at_and_grace_until():
    reg = CheckRegistry()
    cid = reg.register("x")
    reg.record_fail(cid, at=1000.0)
    cr = reg.get(cid)
    assert cr.passed is False
    assert cr.failed_at == 1000.0
    assert cr.grace_until == 1000.0 + 7 * 86400
    print("  ✓ test_record_fail_sets_failed_at_and_grace_until")
    assert True


def test_record_fail_uses_default_grace_seconds():
    reg = CheckRegistry(GraceConfig(grace_seconds=3600))
    cid = reg.register("x")
    reg.record_fail(cid, at=0.0)
    cr = reg.get(cid)
    assert cr.grace_until == 3600.0
    print("  ✓ test_record_fail_uses_default_grace_seconds")
    assert True


# ============ should_block ============

def test_should_block_pass_returns_false():
    reg = CheckRegistry()
    cid = reg.register("x")
    assert reg.should_block(cid) is False
    assert reg.should_block(cid, at=1e9) is False
    print("  ✓ test_should_block_pass_returns_false")
    assert True


def test_should_block_in_grace_returns_false():
    reg = CheckRegistry()
    cid = reg.register("x")
    reg.record_fail(cid, at=1000.0)
    # grace_until = 1000 + 604800; at=1000+1 仍在 grace
    assert reg.should_block(cid, at=1000.0 + 1) is False
    assert reg.should_block(cid, at=1000.0 + 604800 - 1) is False
    print("  ✓ test_should_block_in_grace_returns_false")
    assert True


def test_should_block_after_grace_returns_true():
    reg = CheckRegistry()
    cid = reg.register("x")
    reg.record_fail(cid, at=1000.0)
    assert reg.should_block(cid, at=1000.0 + 604800) is True
    assert reg.should_block(cid, at=1000.0 + 604800 + 1) is True
    print("  ✓ test_should_block_after_grace_returns_true")
    assert True


def test_should_block_disabled_returns_false():
    reg = CheckRegistry(GraceConfig(enabled=False))
    cid = reg.register("x")
    reg.record_fail(cid, at=0.0)
    # 即便超 grace,disabled 也不阻塞
    assert reg.should_block(cid, at=1e9) is False
    print("  ✓ test_should_block_disabled_returns_false")
    assert True


def test_should_block_unknown_id_returns_false():
    reg = CheckRegistry()
    assert reg.should_block("does-not-exist") is False
    assert reg.should_block("does-not-exist", at=1e9) is False
    print("  ✓ test_should_block_unknown_id_returns_false")
    assert True


# ============ get_warnings ============

def test_get_warnings_lists_in_grace():
    reg = CheckRegistry()
    cid = reg.register("x")
    reg.record_fail(cid, at=1000.0)
    warnings = reg.get_warnings(at=1000.0 + 100)
    assert len(warnings) == 1
    assert warnings[0].check_id == cid
    assert warnings[0].passed is False
    print("  ✓ test_get_warnings_lists_in_grace")
    assert True


def test_get_warnings_excludes_passing():
    reg = CheckRegistry()
    a = reg.register("a")
    b = reg.register("b")
    reg.record_fail(a, at=0.0)
    reg.record_pass(b)  # 显式 pass
    warnings = reg.get_warnings(at=10.0)
    ids = {w.check_id for w in warnings}
    assert a in ids
    assert b not in ids
    print("  ✓ test_get_warnings_excludes_passing")
    assert True


def test_get_warnings_excludes_blocked():
    reg = CheckRegistry()
    cid = reg.register("x")
    reg.record_fail(cid, at=0.0)
    # 超 grace: 既不在 warnings,也会被 should_block
    warnings = reg.get_warnings(at=1e9)
    assert len(warnings) == 0
    assert reg.should_block(cid, at=1e9) is True
    print("  ✓ test_get_warnings_excludes_blocked")
    assert True


# ============ grace_status ============

def test_grace_status_passing():
    reg = CheckRegistry()
    cid = reg.register("x")
    assert grace_status(cid, reg) == "passing"
    print("  ✓ test_grace_status_passing")
    assert True


def test_grace_status_in_grace():
    reg = CheckRegistry()
    cid = reg.register("x")
    reg.record_fail(cid, at=0.0)
    assert grace_status(cid, reg, at=100.0) == "in_grace"
    print("  ✓ test_grace_status_in_grace")
    assert True


def test_grace_status_blocking():
    reg = CheckRegistry()
    cid = reg.register("x")
    reg.record_fail(cid, at=0.0)
    assert grace_status(cid, reg, at=7 * 86400 + 1) == "blocking"
    print("  ✓ test_grace_status_blocking")
    assert True


def test_grace_status_unknown_id_is_passing():
    reg = CheckRegistry()
    assert grace_status("nope", reg) == "passing"
    print("  ✓ test_grace_status_unknown_id_is_passing")
    assert True


# ============ 边界 ============

def test_empty_registry_no_warnings_no_block():
    reg = CheckRegistry()
    assert reg.get_warnings() == []
    assert reg.get_all() == []
    assert reg.should_block("any") is False
    print("  ✓ test_empty_registry_no_warnings_no_block")
    assert True


def test_set_enabled_and_set_grace_seconds():
    reg = CheckRegistry()
    reg.set_enabled(False)
    assert reg.config.enabled is False
    reg.set_enabled(True)
    reg.set_grace_seconds(60)
    assert reg.config.grace_seconds == 60.0
    reg.set_grace_seconds(-5)
    assert reg.config.grace_seconds == 0.0
    print("  ✓ test_set_enabled_and_set_grace_seconds")
    assert True


def test_clear_removes_all():
    reg = CheckRegistry()
    reg.register("a")
    reg.register("b")
    assert len(reg.get_all()) == 2
    reg.clear()
    assert len(reg.get_all()) == 0
    print("  ✓ test_clear_removes_all")
    assert True


# ============ JSON ============

def test_registry_export_json_includes_all_checks():
    reg = CheckRegistry()
    reg.register("a")
    b = reg.register("b")
    reg.record_fail(b, at=0.0)
    j = reg.export_json()
    arr = json.loads(j)
    assert isinstance(arr, list)
    assert len(arr) == 2
    names = {entry["name"] for entry in arr}
    assert names == {"a", "b"}
    print("  ✓ test_registry_export_json_includes_all_checks")
    assert True


def test_export_json_empty_registry_is_empty_array():
    reg = CheckRegistry()
    j = reg.export_json()
    assert json.loads(j) == []
    print("  ✓ test_export_json_empty_registry_is_empty_array")
    assert True


# ============ main ============

def main() -> int:
    tests = [
        test_check_result_fields_default,
        test_check_result_fields_after_fail,
        test_check_result_to_dict,
        test_check_result_to_json_roundtrip,
        test_grace_config_default_is_7_days,
        test_grace_config_custom_values,
        test_grace_config_negative_clamped_to_zero,
        test_register_returns_string_check_id,
        test_register_creates_passing_check,
        test_register_multiple_unique_ids,
        test_record_pass_after_fail_resets_to_passing,
        test_record_pass_on_unknown_id_does_not_raise,
        test_record_fail_sets_failed_at_and_grace_until,
        test_record_fail_uses_default_grace_seconds,
        test_should_block_pass_returns_false,
        test_should_block_in_grace_returns_false,
        test_should_block_after_grace_returns_true,
        test_should_block_disabled_returns_false,
        test_should_block_unknown_id_returns_false,
        test_get_warnings_lists_in_grace,
        test_get_warnings_excludes_passing,
        test_get_warnings_excludes_blocked,
        test_grace_status_passing,
        test_grace_status_in_grace,
        test_grace_status_blocking,
        test_grace_status_unknown_id_is_passing,
        test_empty_registry_no_warnings_no_block,
        test_set_enabled_and_set_grace_seconds,
        test_clear_removes_all,
        test_registry_export_json_includes_all_checks,
        test_export_json_empty_registry_is_empty_array,
    ]
    print(f"Running {len(tests)} tests...")
    passed = 0
    failed = []
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"  ✗ {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__, repr(e)))
            print(f"  ✗ {t.__name__}: {e!r}")
    print(f"\n{passed}/{len(tests)} passed")
    if failed:
        for n, e in failed:
            print(f"  FAILED {n}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
