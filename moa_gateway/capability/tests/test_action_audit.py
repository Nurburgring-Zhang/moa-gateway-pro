"""action_audit 真实测试 — 端到端验证 (非 mock)

覆盖:
- AuditDecision 4 态
- AuditStep 5 步
- 5 步协议顺序
- cache hit / miss
- default_policy: read → ALLOW;delete → ADMIN_REVIEW;exec → DENY
- 哈希一致性 / 不同 action 不同哈希
- PERSIST 内存 + 文件
- 空 cache
- JSON 序列化
- 多次 audit 不同 audit_id
"""
import sys
import os
import json
import tempfile
import hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.action_audit import (
    AuditDecision,
    AuditStep,
    AuditLog,
    AuditGate,
    default_policy,
    get_step_order,
    _hash_action_data,
)


# ============ AuditDecision ============

def test_audit_decision_allow_value():
    assert AuditDecision.ALLOW.value == "allow"
    print("  ✓ test_audit_decision_allow_value")
    assert True


def test_audit_decision_deny_value():
    assert AuditDecision.DENY.value == "deny"
    print("  ✓ test_audit_decision_deny_value")
    assert True


def test_audit_decision_admin_review_value():
    assert AuditDecision.ADMIN_REVIEW.value == "admin_review"
    print("  ✓ test_audit_decision_admin_review_value")
    assert True


def test_audit_decision_defer_value():
    assert AuditDecision.DEFER.value == "defer"
    print("  ✓ test_audit_decision_defer_value")
    assert True


def test_audit_decision_count_is_four():
    assert len(list(AuditDecision)) == 4
    print("  ✓ test_audit_decision_count_is_four")
    assert True


# ============ AuditStep ============

def test_audit_step_hash():
    assert AuditStep.HASH.value == "hash"
    print("  ✓ test_audit_step_hash")
    assert True


def test_audit_step_cache_check():
    assert AuditStep.CACHE_CHECK.value == "cache_check"
    print("  ✓ test_audit_step_cache_check")
    assert True


def test_audit_step_invoke():
    assert AuditStep.INVOKE.value == "invoke"
    print("  ✓ test_audit_step_invoke")
    assert True


def test_audit_step_route():
    assert AuditStep.ROUTE.value == "route"
    print("  ✓ test_audit_step_route")
    assert True


def test_audit_step_persist():
    assert AuditStep.PERSIST.value == "persist"
    print("  ✓ test_audit_step_persist")
    assert True


def test_audit_step_count_is_five():
    assert len(list(AuditStep)) == 5
    print("  ✓ test_audit_step_count_is_five")
    assert True


# ============ 5 步顺序 ============

def test_step_order_correct():
    order = get_step_order()
    expected = [
        AuditStep.HASH, AuditStep.CACHE_CHECK, AuditStep.INVOKE,
        AuditStep.ROUTE, AuditStep.PERSIST,
    ]
    assert order == expected, f"got {order}"
    print("  ✓ test_step_order_correct")
    assert True


# ============ default_policy ============

def test_default_policy_read_returns_allow():
    assert default_policy({"action": "read"}) == AuditDecision.ALLOW
    assert default_policy({"action": "list"}) == AuditDecision.ALLOW
    assert default_policy({"action": "get"}) == AuditDecision.ALLOW
    print("  ✓ test_default_policy_read_returns_allow")
    assert True


def test_default_policy_delete_returns_admin_review():
    assert default_policy({"action": "delete"}) == AuditDecision.ADMIN_REVIEW
    assert default_policy({"action": "destroy"}) == AuditDecision.ADMIN_REVIEW
    assert default_policy({"action": "rm"}) == AuditDecision.ADMIN_REVIEW
    print("  ✓ test_default_policy_delete_returns_admin_review")
    assert True


def test_default_policy_exec_returns_deny():
    assert default_policy({"action": "exec"}) == AuditDecision.DENY
    assert default_policy({"action": "run"}) == AuditDecision.DENY
    assert default_policy({"action": "execute"}) == AuditDecision.DENY
    print("  ✓ test_default_policy_exec_returns_deny")
    assert True


# ============ Hash 一致性 ============

def test_hash_consistency_same_data():
    h1 = _hash_action_data({"action": "read", "path": "/tmp"})
    h2 = _hash_action_data({"action": "read", "path": "/tmp"})
    assert h1 == h2, f"expected same hash, got {h1} vs {h2}"
    assert len(h1) == 64  # SHA-256 hex length
    print("  ✓ test_hash_consistency_same_data")
    assert True


def test_hash_different_for_different_data():
    h1 = _hash_action_data({"action": "read", "path": "/tmp/a"})
    h2 = _hash_action_data({"action": "read", "path": "/tmp/b"})
    assert h1 != h2
    h3 = _hash_action_data({"action": "write", "path": "/tmp/a"})
    assert h1 != h3
    print("  ✓ test_hash_different_for_different_data")
    assert True


# ============ audit 5 步协议 ============

def test_audit_first_step_is_hash_on_cache_miss():
    gate = AuditGate()
    log = gate.audit("act-1", {"action": "read", "path": "/x"})
    assert log.step_taken == AuditStep.PERSIST
    assert log.cached is False
    assert log.decision == AuditDecision.ALLOW
    assert log.action_id == "act-1"
    assert len(log.audit_id) > 0
    assert log.timestamp > 0
    print("  ✓ test_audit_first_step_is_hash_on_cache_miss")
    assert True


def test_audit_cache_hit_sets_cached_true():
    gate = AuditGate()
    data = {"action": "read", "path": "/cached"}
    log1 = gate.audit("a1", data)
    log2 = gate.audit("a2", data)
    assert log1.cached is False
    assert log2.cached is True
    assert log2.decision == AuditDecision.ALLOW
    assert log2.step_taken == AuditStep.CACHE_CHECK
    print("  ✓ test_audit_cache_hit_sets_cached_true")
    assert True


def test_audit_cache_miss_does_not_set_cached():
    gate = AuditGate()
    log = gate.audit("a1", {"action": "write", "path": "/x"})
    assert log.cached is False
    print("  ✓ test_audit_cache_miss_does_not_set_cached")
    assert True


def test_audit_empty_cache_acts_as_miss():
    gate = AuditGate(cache={})
    log = gate.audit("a", {"action": "read", "path": "/empty"})
    assert log.cached is False
    assert log.decision == AuditDecision.ALLOW
    assert log.step_taken == AuditStep.PERSIST
    print("  ✓ test_audit_empty_cache_acts_as_miss")
    assert True


# ============ Route 决策分发 ============

def test_audit_route_allow():
    gate = AuditGate()
    log = gate.audit("a", {"action": "get", "id": 1})
    assert log.decision == AuditDecision.ALLOW
    print("  ✓ test_audit_route_allow")
    assert True


def test_audit_route_admin_review():
    gate = AuditGate()
    log = gate.audit("a", {"action": "delete", "id": 1})
    assert log.decision == AuditDecision.ADMIN_REVIEW
    print("  ✓ test_audit_route_admin_review")
    assert True


def test_audit_route_deny():
    gate = AuditGate()
    log = gate.audit("a", {"action": "exec", "cmd": "rm -rf /"})
    assert log.decision == AuditDecision.DENY
    print("  ✓ test_audit_route_deny")
    assert True


def test_custom_policy_fn_used():
    def custom(data):
        return AuditDecision.DEFER

    gate = AuditGate(policy_fn=custom)
    log = gate.audit("a", {"action": "anything"})
    assert log.decision == AuditDecision.DEFER
    print("  ✓ test_custom_policy_fn_used")
    assert True


# ============ PERSIST ============

def test_persist_writes_to_memory_logs():
    gate = AuditGate()
    gate.audit("a1", {"action": "read", "path": "/p1"})
    gate.audit("a2", {"action": "delete", "path": "/p2"})
    gate.audit("a3", {"action": "exec", "path": "/p3"})
    logs = gate.get_logs()
    assert len(logs) == 3
    assert [l.action_id for l in logs] == ["a1", "a2", "a3"]
    assert logs[0].decision == AuditDecision.ALLOW
    assert logs[1].decision == AuditDecision.ADMIN_REVIEW
    assert logs[2].decision == AuditDecision.DENY
    print("  ✓ test_persist_writes_to_memory_logs")
    assert True


def test_persist_writes_to_log_path_file():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "audit.jsonl")
        gate = AuditGate(log_path=path)
        gate.audit("ax", {"action": "read", "path": "/file"})
        gate.audit("ay", {"action": "delete", "path": "/file2"})
        assert os.path.isfile(path)
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        assert len(lines) == 2
        rec0 = json.loads(lines[0])
        rec1 = json.loads(lines[1])
        assert rec0["action_id"] == "ax"
        assert rec0["decision"] == "allow"
        assert rec1["action_id"] == "ay"
        assert rec1["decision"] == "admin_review"
        assert rec0["step_taken"] == "persist"
        print("  ✓ test_persist_writes_to_log_path_file")
        assert True


def test_persist_no_log_path_no_file_created():
    with tempfile.TemporaryDirectory() as tmp:
        before = set(os.listdir(tmp))
        gate = AuditGate()
        gate.audit("a", {"action": "read"})
        after = set(os.listdir(tmp))
        assert before == after
        print("  ✓ test_persist_no_log_path_no_file_created")
        assert True


# ============ JSON 序列化 ============

def test_audit_log_to_dict():
    log = AuditLog(
        audit_id="aid",
        action_id="act",
        hash_before="h1",
        hash_after="h2",
        decision=AuditDecision.ALLOW,
        step_taken=AuditStep.PERSIST,
        timestamp=123.45,
        cached=False,
    )
    d = log.to_dict()
    assert d["audit_id"] == "aid"
    assert d["decision"] == "allow"
    assert d["step_taken"] == "persist"
    assert d["timestamp"] == 123.45
    assert d["cached"] is False
    print("  ✓ test_audit_log_to_dict")
    assert True


def test_audit_log_to_json_roundtrip():
    log = AuditLog(
        audit_id="aid-1",
        action_id="act-1",
        hash_before="abc",
        hash_after="abc",
        decision=AuditDecision.DENY,
        step_taken=AuditStep.ROUTE,
        timestamp=1.0,
        cached=False,
    )
    j = log.to_json()
    parsed = json.loads(j)
    assert parsed["decision"] == "deny"
    assert parsed["step_taken"] == "route"
    assert parsed["audit_id"] == "aid-1"
    print("  ✓ test_audit_log_to_json_roundtrip")
    assert True


def test_gate_export_json_includes_all_logs():
    gate = AuditGate()
    gate.audit("a1", {"action": "read"})
    gate.audit("a2", {"action": "delete"})
    j = gate.export_json()
    arr = json.loads(j)
    assert isinstance(arr, list)
    assert len(arr) == 2
    for entry in arr:
        assert "audit_id" in entry
        assert "decision" in entry
        assert "step_taken" in entry
    print("  ✓ test_gate_export_json_includes_all_logs")
    assert True


# ============ 多次 audit 唯一性 ============

def test_multiple_audits_have_different_audit_ids():
    gate = AuditGate()
    ids = set()
    for i in range(20):
        log = gate.audit(f"act-{i}", {"action": "read", "i": i})
        ids.add(log.audit_id)
    assert len(ids) == 20, f"expected 20 unique ids, got {len(ids)}"
    print("  ✓ test_multiple_audits_have_different_audit_ids")
    assert True


def test_repeated_audit_same_data_cache_shortcut():
    """相同 action_data 多次 audit: 第 1 次走完整 5 步,后续走 CACHE_CHECK。"""
    gate = AuditGate()
    data = {"action": "read", "path": "/shortcut"}
    log1 = gate.audit("a", data)
    log2 = gate.audit("b", data)
    log3 = gate.audit("c", data)
    assert log1.cached is False
    assert log1.step_taken == AuditStep.PERSIST
    assert log2.cached is True
    assert log2.step_taken == AuditStep.CACHE_CHECK
    assert log3.cached is True
    assert log3.step_taken == AuditStep.CACHE_CHECK
    print("  ✓ test_repeated_audit_same_data_cache_shortcut")
    assert True


# ============ 额外: audit_id 唯一 + 缓存清理 ============

def test_clear_cache_forces_re_audit():
    gate = AuditGate()
    data = {"action": "read", "path": "/x"}
    log1 = gate.audit("a", data)
    assert log1.cached is False
    log2 = gate.audit("a", data)
    assert log2.cached is True
    gate.clear_cache()
    log3 = gate.audit("a", data)
    assert log3.cached is False
    assert log3.audit_id != log2.audit_id
    print("  ✓ test_clear_cache_forces_re_audit")
    assert True


def test_audit_with_non_dict_action_data():
    """非 dict 输入应该被规范化为 dict,不应抛错。"""
    gate = AuditGate()
    log = gate.audit("a", None)  # type: ignore[arg-type]
    assert log.audit_id
    assert log.decision in list(AuditDecision)
    print("  ✓ test_audit_with_non_dict_action_data")
    assert True


# ============ main ============

def main() -> int:
    tests = [
        test_audit_decision_allow_value,
        test_audit_decision_deny_value,
        test_audit_decision_admin_review_value,
        test_audit_decision_defer_value,
        test_audit_decision_count_is_four,
        test_audit_step_hash,
        test_audit_step_cache_check,
        test_audit_step_invoke,
        test_audit_step_route,
        test_audit_step_persist,
        test_audit_step_count_is_five,
        test_step_order_correct,
        test_default_policy_read_returns_allow,
        test_default_policy_delete_returns_admin_review,
        test_default_policy_exec_returns_deny,
        test_hash_consistency_same_data,
        test_hash_different_for_different_data,
        test_audit_first_step_is_hash_on_cache_miss,
        test_audit_cache_hit_sets_cached_true,
        test_audit_cache_miss_does_not_set_cached,
        test_audit_empty_cache_acts_as_miss,
        test_audit_route_allow,
        test_audit_route_admin_review,
        test_audit_route_deny,
        test_custom_policy_fn_used,
        test_persist_writes_to_memory_logs,
        test_persist_writes_to_log_path_file,
        test_persist_no_log_path_no_file_created,
        test_audit_log_to_dict,
        test_audit_log_to_json_roundtrip,
        test_gate_export_json_includes_all_logs,
        test_multiple_audits_have_different_audit_ids,
        test_repeated_audit_same_data_cache_shortcut,
        test_clear_cache_forces_re_audit,
        test_audit_with_non_dict_action_data,
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
