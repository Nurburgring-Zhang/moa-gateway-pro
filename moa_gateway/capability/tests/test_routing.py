"""routing 真实测试 — 启发式判定 + JSON 序列化(非 mock)"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.routing import (
    HarnessTier, Priority,
    RoutingDecision, HarnessConfig,
    route_request, auto_detect_tier, priority_from_severity,
    tools_for_tier,
    config_to_json, config_from_json,
)


# ============ 枚举完整性 ============

def test_harness_tier_three_values():
    """HarnessTier 必须正好 3 档"""
    members = list(HarnessTier)
    assert len(members) == 3, f"expected 3 tiers, got {len(members)}"
    assert HarnessTier.MINIMAL.value == "minimal"
    assert HarnessTier.STANDARD.value == "standard"
    assert HarnessTier.THOROUGH.value == "thorough"
    print("  ✓ test_harness_tier_three_values")
    assert True


def test_priority_five_values():
    """Priority 必须正好 5 档 (P0-P4)"""
    members = list(Priority)
    assert len(members) == 5, f"expected 5 priorities, got {len(members)}"
    assert Priority.P0.value == "P0"
    assert Priority.P1.value == "P1"
    assert Priority.P2.value == "P2"
    assert Priority.P3.value == "P3"
    assert Priority.P4.value == "P4"
    print("  ✓ test_priority_five_values")
    assert True


# ============ route_request 各档位 ============

def test_route_request_minimal_p0_bugfix():
    """P0 + bugfix → MINIMAL"""
    cfg = route_request(
        task_description="URGENT: fix login crash",
        file_count=2,
        single_domain=True,
        is_bugfix=True,
    )
    assert cfg.tier == HarnessTier.MINIMAL, f"got {cfg.tier}"
    assert cfg.priority == Priority.P0, f"got {cfg.priority}"
    assert cfg.max_iterations == 3, f"got {cfg.max_iterations}"
    assert "read_file" in cfg.tools
    assert "subagent" not in cfg.tools
    assert cfg.decision is not None
    assert "P0" in cfg.decision.reason or "bugfix" in cfg.decision.reason.lower()
    print("  ✓ test_route_request_minimal_p0_bugfix")
    assert True


def test_route_request_minimal_small_scoped():
    """P2 + file<=3 + single_domain → MINIMAL"""
    cfg = route_request(
        task_description="update README",
        file_count=2,
        single_domain=True,
        is_bugfix=False,
    )
    assert cfg.tier == HarnessTier.MINIMAL, f"got {cfg.tier}"
    assert cfg.max_iterations == 3
    assert "single domain" in cfg.decision.reason.lower() or \
           "small" in cfg.decision.reason.lower()
    print("  ✓ test_route_request_minimal_small_scoped")
    assert True


def test_route_request_standard_default():
    """default → STANDARD"""
    cfg = route_request(
        task_description="implement feature X",
        file_count=5,
        single_domain=True,
    )
    assert cfg.tier == HarnessTier.STANDARD, f"got {cfg.tier}"
    assert cfg.max_iterations == 8
    assert "write_file" in cfg.tools
    assert "run_tests" not in cfg.tools
    print("  ✓ test_route_request_standard_default")
    assert True


def test_route_request_thorough_many_files():
    """多文件 (>8) → THOROUGH"""
    cfg = route_request(
        task_description="refactor module",
        file_count=12,
        single_domain=True,
    )
    assert cfg.tier == HarnessTier.THOROUGH, f"got {cfg.tier}"
    assert cfg.max_iterations == 20
    assert "subagent" in cfg.tools
    assert "run_tests" in cfg.tools
    assert "12" in cfg.decision.reason
    print("  ✓ test_route_request_thorough_many_files")
    assert True


def test_route_request_thorough_multi_domain():
    """多域 → THOROUGH"""
    cfg = route_request(
        task_description="add feature",
        file_count=3,
        single_domain=False,
    )
    assert cfg.tier == HarnessTier.THOROUGH, f"got {cfg.tier}"
    assert "multi-domain" in cfg.decision.reason
    print("  ✓ test_route_request_thorough_multi_domain")
    assert True


# ============ auto_detect_tier 关键词 ============

def test_auto_detect_tier_fix_is_minimal():
    """task 含 'fix' → MINIMAL"""
    assert auto_detect_tier("please fix the off-by-one bug") == HarnessTier.MINIMAL
    assert auto_detect_tier("hotfix: revert broken change") == HarnessTier.MINIMAL
    assert auto_detect_tier("typo in docs") == HarnessTier.MINIMAL
    print("  ✓ test_auto_detect_tier_fix_is_minimal")
    assert True


def test_auto_detect_tier_design_is_thorough():
    """task 含 'design'/'architecture' → THOROUGH"""
    assert auto_detect_tier("design the new auth flow") == HarnessTier.THOROUGH
    assert auto_detect_tier("refactor the whole architecture") == HarnessTier.THOROUGH
    assert auto_detect_tier("investigate flaky tests deeply") == HarnessTier.THOROUGH
    print("  ✓ test_auto_detect_tier_design_is_thorough")
    assert True


def test_auto_detect_tier_implement_is_standard():
    """中性 task → STANDARD"""
    assert auto_detect_tier("implement the new endpoint") == HarnessTier.STANDARD
    assert auto_detect_tier("add export button to settings") == HarnessTier.STANDARD
    assert auto_detect_tier("") == HarnessTier.STANDARD
    print("  ✓ test_auto_detect_tier_implement_is_standard")
    assert True


# ============ priority_from_severity 全 5 档 ============

def test_priority_from_severity_critical():
    assert priority_from_severity("critical") == Priority.P0
    assert priority_from_severity("urgent") == Priority.P0
    assert priority_from_severity("CRITICAL") == Priority.P0  # case-insensitive
    print("  ✓ test_priority_from_severity_critical")
    assert True


def test_priority_from_severity_high():
    assert priority_from_severity("high") == Priority.P1
    print("  ✓ test_priority_from_severity_high")
    assert True


def test_priority_from_severity_medium():
    assert priority_from_severity("medium") == Priority.P2
    assert priority_from_severity("normal") == Priority.P2
    print("  ✓ test_priority_from_severity_medium")
    assert True


def test_priority_from_severity_low():
    assert priority_from_severity("low") == Priority.P3
    print("  ✓ test_priority_from_severity_low")
    assert True


def test_priority_from_severity_backlog():
    assert priority_from_severity("backlog") == Priority.P4
    print("  ✓ test_priority_from_severity_backlog")
    assert True


# ============ tools_for_tier 三档不同 ============

def test_tools_for_tier_three_tiers_differ():
    minimal = tools_for_tier(HarnessTier.MINIMAL)
    standard = tools_for_tier(HarnessTier.STANDARD)
    thorough = tools_for_tier(HarnessTier.THOROUGH)

    # minimal: 2 个,只读+搜
    assert len(minimal) == 2, f"minimal tools = {minimal}"
    assert set(minimal) == {"read_file", "search"}, f"got {minimal}"

    # standard: 5 个
    assert len(standard) == 5, f"standard tools = {standard}"
    assert set(standard).issuperset(set(minimal))
    assert "write_file" in standard
    assert "edit" in standard
    assert "bash" in standard

    # thorough: 8 个,含 subagent
    assert len(thorough) == 8, f"thorough tools = {thorough}"
    assert set(thorough).issuperset(set(standard))
    assert "run_tests" in thorough
    assert "web_search" in thorough
    assert "subagent" in thorough

    # 返回的是新 list,改返回不应影响内部
    minimal.append("hacked")
    again = tools_for_tier(HarnessTier.MINIMAL)
    assert "hacked" not in again, "tools_for_tier should return a copy"
    print("  ✓ test_tools_for_tier_three_tiers_differ")
    assert True


# ============ JSON 序列化 ============

def test_json_serialization_roundtrip():
    """HarnessConfig 能 round-trip 序列化"""
    cfg = route_request(
        task_description="URGENT: fix login crash",
        file_count=2,
        single_domain=True,
        is_bugfix=True,
    )
    payload = config_to_json(cfg)
    assert isinstance(payload, str)
    parsed = json.loads(payload)
    assert parsed["tier"] == "minimal"
    assert parsed["priority"] == "P0"
    assert parsed["max_iterations"] == 3
    assert "read_file" in parsed["tools"]
    assert parsed["decision"]["tier"] == "minimal"
    assert parsed["decision"]["priority"] == "P0"
    assert isinstance(parsed["decision"]["agent_count"], int)
    assert isinstance(parsed["decision"]["reason"], str)

    # round-trip
    cfg2 = config_from_json(payload)
    assert cfg2.tier == cfg.tier
    assert cfg2.priority == cfg.priority
    assert cfg2.max_iterations == cfg.max_iterations
    assert cfg2.tools == cfg.tools
    assert cfg2.decision.reason == cfg.decision.reason
    print("  ✓ test_json_serialization_roundtrip")
    assert True


# ============ HarnessConfig 字段完整性 ============

def test_harness_config_fields():
    """HarnessConfig 包含全部规定字段"""
    cfg = HarnessConfig(
        tier=HarnessTier.STANDARD,
        priority=Priority.P1,
        tools=["read_file"],
        max_iterations=5,
    )
    assert cfg.tier == HarnessTier.STANDARD
    assert cfg.priority == Priority.P1
    assert cfg.tools == ["read_file"]
    assert cfg.max_iterations == 5
    # decision 默认 None
    assert cfg.decision is None

    d = cfg.to_dict()
    assert d["tier"] == "standard"
    assert d["priority"] == "P1"
    assert d["decision"] is None
    print("  ✓ test_harness_config_fields")
    assert True


def test_routing_decision_fields():
    """RoutingDecision 字段 + 序列化"""
    rd = RoutingDecision(
        tier=HarnessTier.THOROUGH,
        priority=Priority.P1,
        agent_count=6,
        tools_enabled=["a", "b"],
        reason="broad scope",
    )
    assert rd.tier == HarnessTier.THOROUGH
    assert rd.priority == Priority.P1
    assert rd.agent_count == 6
    assert rd.tools_enabled == ["a", "b"]
    assert rd.reason == "broad scope"
    d = rd.to_dict()
    assert d["tier"] == "thorough"
    assert d["priority"] == "P1"
    assert d["agent_count"] == 6
    print("  ✓ test_routing_decision_fields")
    assert True
