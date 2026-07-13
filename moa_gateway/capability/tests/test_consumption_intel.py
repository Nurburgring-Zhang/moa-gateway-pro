"""consumption_intel real tests (non-mock)"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.consumption_intel import (
    RequestContext,
    EndpointSpec,
    SelectionDecision,
    select_endpoint,
    select_batch,
    self_heal_rebalance,
    self_heal_rebalance_inplace,
    decision_to_json,
    decision_from_json,
    endpoint_to_json,
    endpoint_from_json,
    FAILURE_SKIP_THRESHOLD,
)


# ============ test fixtures ============


def _ep(
    eid: str,
    tier: str = "standard",
    cost: float = 1.0,
    latency: float = 500.0,
    caps: list = None,
    enabled: bool = True,
    failures: int = 0,
) -> EndpointSpec:
    return EndpointSpec(
        endpoint_id=eid,
        model_id=f"model-{eid}",
        cost_per_1k_input=cost,
        cost_per_1k_output=cost * 1.5,
        avg_latency_ms=latency,
        capabilities=list(caps or []),
        tier=tier,
        enabled=enabled,
        consecutive_failures=failures,
    )


def _ctx(
    rid: str = "req-1",
    query: str = "hello",
    caps: list = None,
    max_cost: float = None,
    max_latency: float = None,
    priority: str = "normal",
) -> RequestContext:
    return RequestContext(
        request_id=rid,
        query=query,
        required_capabilities=list(caps or []),
        max_cost_per_1k=max_cost,
        max_latency_ms=max_latency,
        priority=priority,
    )


# ============ tests ============


def test_static_priority_free_above_lite_above_standard():
    """static priority: free > lite > standard (tier ascending)"""
    eps = [
        _ep("std", tier="standard", cost=0.5),
        _ep("fre", tier="free", cost=0.1),
        _ep("lit", tier="lite", cost=0.3),
    ]
    ctx = _ctx()
    d = select_endpoint(ctx, eps)
    assert d.selected_endpoint_id == "fre", f"got {d.selected_endpoint_id}"
    assert "lit" in d.fallback_chain
    assert "std" in d.fallback_chain
    assert d.fallback_chain.index("lit") < d.fallback_chain.index("std")
    print(f"  OK test_static_priority_free_above_lite_above_standard: selected={d.selected_endpoint_id}, chain={d.fallback_chain}")


def test_capabilities_filter_vision():
    """capabilities filter: ctx needs vision, only vision-capable endpoints qualify"""
    eps = [
        _ep("no-vision-1", caps=["text"]),
        _ep("vision-a", caps=["text", "vision"]),
        _ep("no-vision-2", caps=["tools"]),
        _ep("vision-b", caps=["vision", "tools"]),
    ]
    ctx = _ctx(caps=["vision"])
    d = select_endpoint(ctx, eps)
    assert d.selected_endpoint_id in ("vision-a", "vision-b"), f"got {d.selected_endpoint_id}"
    for fid in d.fallback_chain:
        ep = next(e for e in eps if e.endpoint_id == fid)
        assert "vision" in ep.capabilities, f"non-vision {fid} in chain"
    assert d.selected_endpoint_id is not None
    all_vision = {d.selected_endpoint_id, *d.fallback_chain}
    assert all_vision <= {"vision-a", "vision-b"}, f"got {all_vision}"
    print(f"  OK test_capabilities_filter_vision: selected={d.selected_endpoint_id}")


def test_primary_and_fallback_chain():
    """primary + fallback chain (4 candidates)"""
    eps = [_ep(f"e{i}", tier="free", cost=0.1 * (i + 1)) for i in range(4)]
    ctx = _ctx()
    d = select_endpoint(ctx, eps)
    assert d.selected_endpoint_id == "e0"
    assert d.fallback_chain == ["e1", "e2", "e3"], f"got {d.fallback_chain}"
    print(f"  OK test_primary_and_fallback_chain: primary={d.selected_endpoint_id}, chain={d.fallback_chain}")


def test_skip_consecutive_failures_3():
    """consecutive_failures >= 3 -> skip"""
    eps = [
        _ep("bad", tier="free", cost=0.1, failures=3),
        _ep("good", tier="free", cost=0.2),
        _ep("ok", tier="lite", cost=0.3),
    ]
    ctx = _ctx()
    d = select_endpoint(ctx, eps)
    assert d.selected_endpoint_id == "good", f"got {d.selected_endpoint_id}"
    assert "bad" not in [d.selected_endpoint_id, *d.fallback_chain]
    print(f"  OK test_skip_consecutive_failures_3: selected={d.selected_endpoint_id}")


def test_skip_consecutive_failures_above_threshold():
    """consecutive_failures = 5 still skipped (>= threshold)"""
    eps = [
        _ep("broken", tier="free", cost=0.1, failures=5),
        _ep("ok", tier="free", cost=0.2, failures=2),
    ]
    ctx = _ctx()
    d = select_endpoint(ctx, eps)
    assert d.selected_endpoint_id == "ok", f"got {d.selected_endpoint_id}"
    assert "broken" not in d.fallback_chain
    print(f"  OK test_skip_consecutive_failures_above_threshold: selected={d.selected_endpoint_id}")


def test_vision_degrade_to_supporting_vision():
    """vision degrade: strict filter empty -> relax + promote vision alt"""
    eps = [
        _ep("text-only", tier="free", cost=0.1, caps=["text"]),
        _ep("vision-pro", tier="premium", cost=2.0, caps=["text", "vision"]),
    ]
    ctx = _ctx(caps=["vision"], max_cost=0.5)
    d = select_endpoint(ctx, eps)
    assert d.selected_endpoint_id == "vision-pro", f"got {d.selected_endpoint_id}"
    assert d.vision_degraded_to == "vision-pro", f"vision_degraded_to={d.vision_degraded_to}"
    assert "text-only" in d.fallback_chain
    print(f"  OK test_vision_degrade_to_supporting_vision: selected={d.selected_endpoint_id}, degraded_to={d.vision_degraded_to}")


def test_max_cost_filter():
    """max_cost filter: endpoints over budget are filtered out"""
    eps = [
        _ep("cheap", tier="free", cost=0.5),
        _ep("expensive", tier="premium", cost=5.0),
        _ep("mid", tier="standard", cost=1.5),
    ]
    ctx = _ctx(max_cost=2.0)
    d = select_endpoint(ctx, eps)
    assert d.selected_endpoint_id == "cheap", f"got {d.selected_endpoint_id}"
    for fid in [d.selected_endpoint_id, *d.fallback_chain]:
        ep = next(e for e in eps if e.endpoint_id == fid)
        assert ep.cost_per_1k_input <= 2.0, f"{fid} cost {ep.cost_per_1k_input} > 2.0"
    print(f"  OK test_max_cost_filter: selected={d.selected_endpoint_id}")


def test_max_latency_filter():
    """max_latency filter"""
    eps = [
        _ep("slow", tier="premium", cost=0.1, latency=5000.0),
        _ep("fast", tier="premium", cost=0.5, latency=200.0),
        _ep("medium", tier="lite", cost=0.3, latency=800.0),
    ]
    ctx = _ctx(max_latency=1000.0)
    d = select_endpoint(ctx, eps)
    assert d.selected_endpoint_id == "medium", f"got {d.selected_endpoint_id}"
    assert "slow" not in [d.selected_endpoint_id, *d.fallback_chain]
    for fid in [d.selected_endpoint_id, *d.fallback_chain]:
        ep = next(e for e in eps if e.endpoint_id == fid)
        assert ep.avg_latency_ms <= 1000.0, f"{fid} latency {ep.avg_latency_ms} > 1000"
    print(f"  OK test_max_latency_filter: selected={d.selected_endpoint_id}")


def test_empty_endpoints_returns_none():
    """empty endpoints -> selected = None"""
    ctx = _ctx()
    d = select_endpoint(ctx, [])
    assert d.selected_endpoint_id is None
    assert d.fallback_chain == []
    assert "no endpoint" in d.reason.lower() or "no" in d.reason.lower()
    print(f"  OK test_empty_endpoints_returns_none: reason={d.reason!r}")


def test_single_endpoint_selects_it():
    """1 endpoint -> selects it"""
    eps = [_ep("solo", tier="standard", cost=1.0, caps=["text", "vision"])]
    ctx = _ctx(caps=["vision"])
    d = select_endpoint(ctx, eps)
    assert d.selected_endpoint_id == "solo"
    assert d.fallback_chain == []
    print(f"  OK test_single_endpoint_selects_it: selected={d.selected_endpoint_id}")


def test_priority_high_prefers_premium():
    """priority=high: reason includes '(high priority)'"""
    eps = [
        _ep("free-ep", tier="free", cost=0.1, caps=["text"]),
        _ep("premium-ep", tier="premium", cost=2.0, caps=["text", "vision"]),
    ]
    ctx = _ctx(priority="high", caps=["vision"])
    d = select_endpoint(ctx, eps)
    assert d.selected_endpoint_id == "premium-ep", f"got {d.selected_endpoint_id}"
    assert "high" in d.reason.lower(), f"reason missing high: {d.reason!r}"
    print(f"  OK test_priority_high_prefers_premium: selected={d.selected_endpoint_id}, reason={d.reason!r}")


def test_priority_high_with_all_vision_picks_free():
    """priority=high but all qualify: still picks free (lowest tier)"""
    eps = [
        _ep("a", tier="free", cost=0.1, caps=["vision"]),
        _ep("b", tier="premium", cost=2.0, caps=["vision"]),
    ]
    ctx = _ctx(priority="high", caps=["vision"])
    d = select_endpoint(ctx, eps)
    assert d.selected_endpoint_id == "a", f"got {d.selected_endpoint_id}"
    assert "high" in d.reason.lower()
    print(f"  OK test_priority_high_with_all_vision_picks_free: selected={d.selected_endpoint_id}")


def test_estimated_cost_calculation():
    """estimated_cost = cost_per_1k_input * 1.0"""
    eps = [
        _ep("cheap", tier="free", cost=0.123),
        _ep("med", tier="standard", cost=1.5),
    ]
    ctx = _ctx()
    d = select_endpoint(ctx, eps)
    assert abs(d.estimated_cost_usd - 0.123) < 1e-9, f"got {d.estimated_cost_usd}"
    print(f"  OK test_estimated_cost_calculation: cost={d.estimated_cost_usd}")


def test_self_heal_failures_3_demotes():
    """self_heal: consecutive_failures >= 3 -> demote one tier"""
    eps = [
        _ep("a", tier="flagship", failures=4),
        _ep("b", tier="premium", failures=3),
        _ep("c", tier="standard", failures=10),
    ]
    new_tiers = self_heal_rebalance(eps)
    assert new_tiers == ["premium", "standard", "lite"], f"got {new_tiers}"
    print(f"  OK test_self_heal_failures_3_demotes: {new_tiers}")


def test_self_heal_zero_failures_keeps():
    """self_heal: consecutive_failures = 0 -> keep original tier"""
    eps = [
        _ep("a", tier="flagship", failures=0),
        _ep("b", tier="premium", failures=2),
        _ep("c", tier="free", failures=0),
    ]
    new_tiers = self_heal_rebalance(eps)
    assert new_tiers == ["flagship", "premium", "free"], f"got {new_tiers}"
    print(f"  OK test_self_heal_zero_failures_keeps: {new_tiers}")


def test_self_heal_inplace_records_changes():
    """self_heal_inplace returns change list"""
    eps = [
        _ep("a", tier="flagship", failures=0),
        _ep("b", tier="premium", failures=3),
    ]
    changes = self_heal_rebalance_inplace(eps)
    assert len(changes) == 1
    eid, old, new = changes[0]
    assert eid == "b"
    assert old == "premium"
    assert new == "standard"
    assert eps[1].tier == "standard"
    print(f"  OK test_self_heal_inplace_records_changes: {changes}")


def test_select_batch():
    """batch: multiple ctxs in one call"""
    eps = [
        _ep("free", tier="free", cost=0.1, caps=["text"]),
        _ep("vision", tier="standard", cost=0.5, caps=["text", "vision"]),
    ]
    ctxs = [
        _ctx(rid="r1", caps=["text"]),
        _ctx(rid="r2", caps=["vision"]),
        _ctx(rid="r3", caps=["text", "vision"]),
    ]
    decisions = select_batch(ctxs, eps)
    assert len(decisions) == 3
    assert decisions[0].selected_endpoint_id == "free"
    assert decisions[1].selected_endpoint_id == "vision"
    assert decisions[2].selected_endpoint_id == "vision"
    print(f"  OK test_select_batch: {[d.selected_endpoint_id for d in decisions]}")


def test_json_serialization_roundtrip():
    """JSON serialization roundtrip"""
    d = SelectionDecision(
        selected_endpoint_id="ep-1",
        fallback_chain=["ep-2", "ep-3"],
        vision_degraded_to="ep-1",
        reason="test decision",
        estimated_cost_usd=0.42,
    )
    s = decision_to_json(d)
    assert isinstance(s, str)
    parsed = json.loads(s)
    assert parsed["selected_endpoint_id"] == "ep-1"
    assert parsed["fallback_chain"] == ["ep-2", "ep-3"]
    d2 = decision_from_json(s)
    assert d2.selected_endpoint_id == d.selected_endpoint_id
    assert d2.fallback_chain == d.fallback_chain
    assert d2.vision_degraded_to == d.vision_degraded_to
    assert d2.reason == d.reason
    assert abs(d2.estimated_cost_usd - d.estimated_cost_usd) < 1e-9
    print(f"  OK test_json_serialization_roundtrip: roundtrip OK")


def test_endpoint_json_serialization():
    """EndpointSpec JSON roundtrip"""
    ep = _ep("x", tier="premium", cost=2.0, latency=300.0, caps=["text", "vision", "tools"], failures=2)
    s = endpoint_to_json(ep)
    ep2 = endpoint_from_json(s)
    assert ep2.endpoint_id == ep.endpoint_id
    assert ep2.tier == ep.tier
    assert ep2.cost_per_1k_input == ep.cost_per_1k_input
    assert ep2.avg_latency_ms == ep.avg_latency_ms
    assert ep2.capabilities == ep.capabilities
    assert ep2.consecutive_failures == ep.consecutive_failures
    print(f"  OK test_endpoint_json_serialization: roundtrip OK")


def test_boundary_capabilities_mismatch():
    """boundary: no endpoint has the required (non-vision) capability -> None"""
    eps = [
        _ep("a", tier="premium", caps=["text"]),
        _ep("b", tier="flagship", caps=["tools"]),
    ]
    ctx = _ctx(caps=["audio"])  # audio: not in any endpoint, not vision -> no degrade
    d = select_endpoint(ctx, eps)
    assert d.selected_endpoint_id is None, f"got {d.selected_endpoint_id}"
    assert d.fallback_chain == []
    assert d.vision_degraded_to is None
    print(f"  OK test_boundary_capabilities_mismatch: reason={d.reason!r}")


def test_disabled_endpoint_excluded():
    """boundary: disabled endpoint is excluded"""
    eps = [
        _ep("on", tier="free", cost=0.1),
        _ep("off", tier="premium", cost=0.1, enabled=False),
    ]
    ctx = _ctx()
    d = select_endpoint(ctx, eps)
    assert d.selected_endpoint_id == "on", f"got {d.selected_endpoint_id}"
    assert "off" not in [d.selected_endpoint_id, *d.fallback_chain]
    print(f"  OK test_disabled_endpoint_excluded: selected={d.selected_endpoint_id}")


def test_same_tier_cheaper_wins():
    """same tier: cheaper wins (cost ascending secondary sort)"""
    eps = [
        _ep("expensive", tier="standard", cost=2.0),
        _ep("cheap", tier="standard", cost=0.5),
    ]
    ctx = _ctx()
    d = select_endpoint(ctx, eps)
    assert d.selected_endpoint_id == "cheap", f"got {d.selected_endpoint_id}"
    print(f"  OK test_same_tier_cheaper_wins: selected={d.selected_endpoint_id}")


def test_all_endpoints_failed_returns_none():
    """all candidates exceed failure threshold -> None"""
    eps = [
        _ep("a", tier="free", cost=0.1, failures=3),
        _ep("b", tier="lite", cost=0.2, failures=5),
        _ep("c", tier="standard", cost=0.5, failures=10),
    ]
    ctx = _ctx()
    d = select_endpoint(ctx, eps)
    assert d.selected_endpoint_id is None
    assert "exceeded" in d.reason.lower() or "fail" in d.reason.lower() or "threshold" in d.reason.lower()
    print(f"  OK test_all_endpoints_failed_returns_none: reason={d.reason!r}")


# ============ Runner ============


if __name__ == "__main__":
    tests = [
        test_static_priority_free_above_lite_above_standard,
        test_capabilities_filter_vision,
        test_primary_and_fallback_chain,
        test_skip_consecutive_failures_3,
        test_skip_consecutive_failures_above_threshold,
        test_vision_degrade_to_supporting_vision,
        test_max_cost_filter,
        test_max_latency_filter,
        test_empty_endpoints_returns_none,
        test_single_endpoint_selects_it,
        test_priority_high_prefers_premium,
        test_priority_high_with_all_vision_picks_free,
        test_estimated_cost_calculation,
        test_self_heal_failures_3_demotes,
        test_self_heal_zero_failures_keeps,
        test_self_heal_inplace_records_changes,
        test_select_batch,
        test_json_serialization_roundtrip,
        test_endpoint_json_serialization,
        test_boundary_capabilities_mismatch,
        test_disabled_endpoint_excluded,
        test_same_tier_cheaper_wins,
        test_all_endpoints_failed_returns_none,
    ]
    print(f"=== consumption_intel end-to-end tests ({len(tests)} items) ===")
    passed = 0
    failed = []
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  X {t.__name__}: {type(e).__name__}: {e}")
            failed.append(t.__name__)
    print(f"\n=== result: {passed}/{len(tests)} passed ===")
    if failed:
        print(f"failed: {failed}")
        sys.exit(1)
