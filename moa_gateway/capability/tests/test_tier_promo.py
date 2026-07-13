"""tier_promo 单元测试 (>= 16 测试, 真实 assert, 严禁 mock)

覆盖:
  - PromotionLevel 枚举 (4 个 tier)
  - PromotionConfig 阈值 + confidence_threshold 默认值
  - compute_tier 1/3/5/10 events → TIER_1/2/3/4
  - compute_tier confidence < 0.70 → 维持
  - record_evidence 累加
  - classify_tier_from_evidence 主入口
  - 边界: 0 events
  - SubAgentBoundary can_spawn allowed / disallowed
  - cohabitation_check same parent → True / diff parent → False
  - 边界: 0 children
  - JSON 序列化往返
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.tier_promo import (
    PromotionLevel,
    Evidence,
    PromotionConfig,
    PromotionState,
    SubAgentBoundary,
    CONFIDENCE_KAPPA,
    DEFAULT_CONFIDENCE_THRESHOLD,
    compute_tier,
    record_evidence,
    classify_tier_from_evidence,
    evidence_to_dict,
    evidence_from_dict,
    promotion_state_to_dict,
    promotion_state_from_dict,
    promotion_config_to_dict,
    promotion_config_from_dict,
    subagent_boundary_to_dict,
    subagent_boundary_from_dict,
)


# ============ PromotionLevel 枚举 ============

def test_promotion_level_has_four_tiers():
    """PromotionLevel 必须有 4 个 tier (TIER_1..TIER_4)"""
    levels = list(PromotionLevel)
    assert len(levels) == 4
    names = {l.name for l in levels}
    assert names == {"TIER_1", "TIER_2", "TIER_3", "TIER_4"}


def test_promotion_level_values():
    """每个 tier 的 numeric value 必须为 1/2/3/4"""
    assert PromotionLevel.TIER_1.value == 1
    assert PromotionLevel.TIER_2.value == 2
    assert PromotionLevel.TIER_3.value == 3
    assert PromotionLevel.TIER_4.value == 4


def test_promotion_level_numeric_property():
    """numeric property 等同于 value"""
    for lvl in PromotionLevel:
        assert lvl.numeric == lvl.value


def test_promotion_level_lookup_by_name():
    """PromotionLevel['TIER_3'] 可用 name 查询"""
    assert PromotionLevel["TIER_3"] is PromotionLevel.TIER_3


# ============ PromotionConfig 默认值 ============

def test_promotion_config_default_thresholds():
    """默认阈值必须为 1/3/5/10"""
    cfg = PromotionConfig()
    assert cfg.tier_1_threshold == 1
    assert cfg.tier_2_threshold == 3
    assert cfg.tier_3_threshold == 5
    assert cfg.tier_4_threshold == 10


def test_promotion_config_default_confidence_threshold():
    """默认 confidence_threshold 必须为 0.70"""
    cfg = PromotionConfig()
    assert cfg.confidence_threshold == DEFAULT_CONFIDENCE_THRESHOLD == 0.70


def test_promotion_config_threshold_list_monotonic():
    """threshold_list() 必须单调递增"""
    cfg = PromotionConfig()
    thresholds = cfg.threshold_list()
    for i in range(1, len(thresholds)):
        assert thresholds[i] > thresholds[i - 1]


# ============ compute_tier 各 tier 触发 ============

def test_compute_tier_1_event_tier_1():
    """1 event + 高 confidence → TIER_1"""
    cfg = PromotionConfig()
    assert compute_tier(1, 0.80, cfg) == PromotionLevel.TIER_1


def test_compute_tier_3_events_tier_2():
    """3 events + 高 confidence → TIER_2"""
    cfg = PromotionConfig()
    assert compute_tier(3, 0.80, cfg) == PromotionLevel.TIER_2


def test_compute_tier_5_events_tier_3():
    """5 events + 高 confidence → TIER_3"""
    cfg = PromotionConfig()
    assert compute_tier(5, 0.85, cfg) == PromotionLevel.TIER_3


def test_compute_tier_10_events_tier_4():
    """10 events + 高 confidence → TIER_4"""
    cfg = PromotionConfig()
    assert compute_tier(10, 0.90, cfg) == PromotionLevel.TIER_4


# ============ compute_tier confidence 抑制 ============

def test_compute_tier_low_confidence_maintains():
    """confidence < 0.70 → 不 promote, 维持 current_tier"""
    cfg = PromotionConfig()
    # evidence_count 足够 TIER_4, 但 confidence 只有 0.5
    tier = compute_tier(50, 0.50, cfg, current_tier=PromotionLevel.TIER_1)
    assert tier == PromotionLevel.TIER_1


def test_compute_tier_low_confidence_default_floor():
    """confidence < 0.70 且无 current_tier → 维持默认 TIER_1"""
    cfg = PromotionConfig()
    tier = compute_tier(99, 0.30, cfg)
    assert tier == PromotionLevel.TIER_1


# ============ record_evidence 累加 ============

def test_record_evidence_accumulates_count():
    """record_evidence 累加 evidence_count"""
    cfg = PromotionConfig()
    state = PromotionState()
    ev = Evidence(event_type="x", timestamp=1.0, weight=2.0)
    ns = record_evidence(state, ev, cfg)
    assert ns.evidence_count == 1
    ns2 = record_evidence(ns, ev, cfg)
    assert ns2.evidence_count == 2
    ns3 = record_evidence(ns2, ev, cfg)
    assert ns3.evidence_count == 3


def test_record_evidence_returns_new_state():
    """record_evidence 不可变更新, 返回新对象"""
    cfg = PromotionConfig()
    state = PromotionState()
    ev = Evidence(event_type="x", timestamp=1.0, weight=1.0)
    ns = record_evidence(state, ev, cfg)
    assert ns is not state
    assert state.evidence_count == 0  # 原 state 不变


def test_record_evidence_promotes_tier():
    """累加到足够多 evidence 且 confidence 充足 → 触发 promote"""
    cfg = PromotionConfig()
    state = PromotionState()
    # 3 events, 总 weight=3.0 → confidence = 3/(3+3) = 0.5 (不足)
    # 提高 weight 到 10.0 → confidence = 10/(10+3) = 0.769 (>0.70)
    ev = Evidence(event_type="big", timestamp=1.0, weight=10.0)
    s = state
    s = record_evidence(s, ev, cfg)
    s = record_evidence(s, ev, cfg)
    s = record_evidence(s, ev, cfg)
    assert s.evidence_count == 3
    assert s.current_tier == PromotionLevel.TIER_2


# ============ classify_tier_from_evidence 主入口 ============

def test_classify_tier_from_evidence_promotion_path():
    """足够 evidence + 高 weight → promote 到对应 tier"""
    cfg = PromotionConfig()
    evs = [
        Evidence(event_type="a", timestamp=1.0, weight=5.0),
        Evidence(event_type="b", timestamp=2.0, weight=5.0),
        Evidence(event_type="c", timestamp=3.0, weight=5.0),
    ]
    tier = classify_tier_from_evidence(evs, cfg)
    # 3 events, total_weight=15 → confidence = 15/18 = 0.833 → TIER_2
    assert tier == PromotionLevel.TIER_2


def test_classify_tier_from_evidence_insufficient_confidence():
    """weight 太低 → confidence < 0.70 → 维持 TIER_1"""
    cfg = PromotionConfig()
    evs = [
        Evidence(event_type="a", timestamp=1.0, weight=0.1),
        Evidence(event_type="b", timestamp=2.0, weight=0.1),
        Evidence(event_type="c", timestamp=3.0, weight=0.1),
        Evidence(event_type="d", timestamp=4.0, weight=0.1),
        Evidence(event_type="e", timestamp=5.0, weight=0.1),
    ]
    # 5 events, total_weight=0.5 → confidence = 0.5/3.5 = 0.143 → 不足
    tier = classify_tier_from_evidence(evs, cfg)
    assert tier == PromotionLevel.TIER_1


# ============ 边界: 0 events ============

def test_classify_tier_zero_evidence():
    """0 evidence → 维持 TIER_1"""
    cfg = PromotionConfig()
    tier = classify_tier_from_evidence([], cfg)
    assert tier == PromotionLevel.TIER_1


def test_compute_tier_zero_evidence_no_promote():
    """0 evidence + 高 confidence → 仍 TIER_1 (>= 1 才到 TIER_1 阈值, 边界值不算)"""
    cfg = PromotionConfig()
    tier = compute_tier(0, 0.95, cfg)
    # 0 < tier_1_threshold=1, 按规则仍 TIER_1
    assert tier == PromotionLevel.TIER_1


# ============ SubAgentBoundary.can_spawn ============

def test_subagent_boundary_can_spawn_allowed():
    """白名单内的 child_id → can_spawn = True"""
    b = SubAgentBoundary(parent_id="p1", allowed_children=["c1", "c2", "c3"])
    assert b.can_spawn("c1") is True
    assert b.can_spawn("c2") is True
    assert b.can_spawn("c3") is True


def test_subagent_boundary_can_spawn_disallowed():
    """白名单外的 child_id → can_spawn = False"""
    b = SubAgentBoundary(parent_id="p1", allowed_children=["c1", "c2"])
    assert b.can_spawn("c9") is False
    assert b.can_spawn("malicious") is False
    assert b.can_spawn("") is False


# ============ SubAgentBoundary.cohabitation_check ============

def test_subagent_boundary_cohabitation_same_parent():
    """同 parent → 允许共处 (True)"""
    b1 = SubAgentBoundary(parent_id="p1", allowed_children=["c1"])
    b2 = SubAgentBoundary(parent_id="p1", allowed_children=["c2"])
    assert b1.cohabitation_check(b2.parent_id) is True


def test_subagent_boundary_cohabitation_diff_parent():
    """不同 parent → 不可共处 (False)"""
    b1 = SubAgentBoundary(parent_id="p1", allowed_children=["c1"])
    b2 = SubAgentBoundary(parent_id="p2", allowed_children=["c1"])
    assert b1.cohabitation_check(b2.parent_id) is False


# ============ 边界: 0 children ============

def test_subagent_boundary_zero_children():
    """0 children → 任何 spawn 都 False"""
    b = SubAgentBoundary(parent_id="p1", allowed_children=[])
    assert b.can_spawn("anything") is False
    assert b.cohabitation_check("p1") is True  # 同 parent 仍 OK
    assert b.cohabitation_check("p2") is False


def test_subagent_boundary_add_child_immutable():
    """add_child 不可变更新, 返回新对象"""
    b1 = SubAgentBoundary(parent_id="p1", allowed_children=["c1"])
    b2 = b1.add_child("c2")
    assert b1.can_spawn("c2") is False  # 原对象不变
    assert b2.can_spawn("c2") is True
    assert b2.parent_id == "p1"
    # 重复添加不重复
    b3 = b2.add_child("c1")  # 已存在
    assert b3.allowed_children == b2.allowed_children


# ============ JSON 序列化 ============

def test_evidence_json_roundtrip():
    """Evidence JSON 往返一致"""
    ev = Evidence(event_type="test_event", timestamp=12345.678, weight=2.5)
    j = json.dumps(evidence_to_dict(ev))
    d = json.loads(j)
    ev2 = evidence_from_dict(d)
    assert ev2.event_type == "test_event"
    assert math.isclose(ev2.timestamp, 12345.678)
    assert math.isclose(ev2.weight, 2.5)


def test_promotion_state_json_roundtrip():
    """PromotionState JSON 往返一致"""
    s = PromotionState(
        current_tier=PromotionLevel.TIER_3,
        evidence_count=5,
        confidence=0.85,
    )
    j = json.dumps(promotion_state_to_dict(s))
    d = json.loads(j)
    s2 = promotion_state_from_dict(d)
    assert s2.current_tier == PromotionLevel.TIER_3
    assert s2.evidence_count == 5
    assert math.isclose(s2.confidence, 0.85)


def test_promotion_config_json_roundtrip():
    """PromotionConfig JSON 往返一致"""
    cfg = PromotionConfig(
        tier_1_threshold=2,
        tier_2_threshold=4,
        tier_3_threshold=7,
        tier_4_threshold=15,
        confidence_threshold=0.65,
    )
    j = json.dumps(promotion_config_to_dict(cfg))
    d = json.loads(j)
    cfg2 = promotion_config_from_dict(d)
    assert cfg2.tier_1_threshold == 2
    assert cfg2.tier_2_threshold == 4
    assert cfg2.tier_3_threshold == 7
    assert cfg2.tier_4_threshold == 15
    assert math.isclose(cfg2.confidence_threshold, 0.65)


def test_subagent_boundary_json_roundtrip():
    """SubAgentBoundary JSON 往返一致"""
    b = SubAgentBoundary(parent_id="parent_42", allowed_children=["a", "b", "c"])
    j = json.dumps(subagent_boundary_to_dict(b))
    d = json.loads(j)
    b2 = subagent_boundary_from_dict(d)
    assert b2.parent_id == "parent_42"
    assert b2.allowed_children == ["a", "b", "c"]
    assert b2.can_spawn("b") is True
    assert b2.can_spawn("x") is False
