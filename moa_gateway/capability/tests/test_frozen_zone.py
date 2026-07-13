"""frozen_zone 真实测试 — 端到端验证 (非 mock)

覆盖:
- 4 Zone 枚举值与 is_frozen / is_evolvable 分类
- FrozenEntry 字段、自动 sentinel 兜底、to_dict / to_json
- 8 HARNESS_FROZEN_* 常量字面量
- FrozenRegistry add / get_zone / is_frozen / is_evolvable
- can_modify FROZEN → False / EVOLVABLE → True
- assert_modifiable FROZEN → raise / EVOLVABLE → ok / 未注册 → ok
- FrozenZoneError is Exception + 字段
- 边界: 不存在 path
- JSON 序列化(含时间戳 int 化)
"""
import sys
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.frozen_zone import (
    Zone,
    FrozenEntry,
    FrozenRegistry,
    FrozenZoneError,
    can_modify,
    assert_modifiable,
    classify,
    HARNESS_FROZEN_CANONICAL,
    HARNESS_FROZEN_SAFETY,
    HARNESS_FROZEN_TUNING,
    HARNESS_FROZEN_EXPERIMENTAL,
    HARNESS_FROZEN_LEARNER,
    HARNESS_FROZEN_REGISTRY,
    HARNESS_FROZEN_BOOTSTRAP,
    HARNESS_FROZEN_CONFIG,
    ALL_HARNESS_FROZEN_SENTINELS,
)


# ============ Zone 枚举 ============

def test_zone_has_4_members():
    """Zone 必须有且仅有 4 个成员"""
    members = {z.value for z in Zone}
    assert members == {
        "frozen-canonical",
        "frozen-safety",
        "evolvable-tuning",
        "evolvable-experimental",
    }, f"got {members}"
    print("  ✓ test_zone_has_4_members")
    assert True


def test_zone_is_frozen_property():
    """FROZEN_* 的 is_frozen == True;EVOLVABLE_* 的 is_frozen == False"""
    assert Zone.FROZEN_CANONICAL.is_frozen is True
    assert Zone.FROZEN_SAFETY.is_frozen is True
    assert Zone.EVOLVABLE_TUNING.is_frozen is False
    assert Zone.EVOLVABLE_EXPERIMENTAL.is_frozen is False
    print("  ✓ test_zone_is_frozen_property")
    assert True


def test_zone_is_evolvable_property():
    """EVOLVABLE_* 的 is_evolvable == True;FROZEN_* 的 is_evolvable == False"""
    assert Zone.EVOLVABLE_TUNING.is_evolvable is True
    assert Zone.EVOLVABLE_EXPERIMENTAL.is_evolvable is True
    assert Zone.FROZEN_CANONICAL.is_evolvable is False
    assert Zone.FROZEN_SAFETY.is_evolvable is False
    print("  ✓ test_zone_is_evolvable_property")
    assert True


def test_zone_default_sentinel():
    """zone.default_sentinel 与对应 HARNESS_FROZEN_* 一致"""
    assert Zone.FROZEN_CANONICAL.default_sentinel == HARNESS_FROZEN_CANONICAL
    assert Zone.FROZEN_SAFETY.default_sentinel == HARNESS_FROZEN_SAFETY
    assert Zone.EVOLVABLE_TUNING.default_sentinel == HARNESS_FROZEN_TUNING
    assert Zone.EVOLVABLE_EXPERIMENTAL.default_sentinel == HARNESS_FROZEN_EXPERIMENTAL
    print("  ✓ test_zone_default_sentinel")
    assert True


# ============ FrozenEntry 字段 ============

def test_frozen_entry_fields():
    """FrozenEntry 必须有 5 个字段且类型正确"""
    e = FrozenEntry(
        path="core/canonical.py",
        zone=Zone.FROZEN_CANONICAL,
        sentinel=HARNESS_FROZEN_CANONICAL,
        reason="must not edit",
        added_at=1700000000.0,
    )
    assert e.path == "core/canonical.py"
    assert e.zone is Zone.FROZEN_CANONICAL
    assert e.sentinel == HARNESS_FROZEN_CANONICAL
    assert e.reason == "must not edit"
    assert e.added_at == 1700000000.0
    print("  ✓ test_frozen_entry_fields")
    assert True


def test_frozen_entry_default_sentinel_filled():
    """sentinel 缺省时由 zone.default_sentinel 自动填充"""
    e = FrozenEntry(path="tuning/x.json", zone=Zone.EVOLVABLE_TUNING)
    assert e.sentinel == HARNESS_FROZEN_TUNING
    assert e.added_at > 0
    print("  ✓ test_frozen_entry_default_sentinel_filled")
    assert True


def test_frozen_entry_to_dict_and_json():
    """to_dict 输出 dict;to_json 输出合法 JSON;zone 序列化为字符串值"""
    e = FrozenEntry(
        path="safety/y.py",
        zone=Zone.FROZEN_SAFETY,
        sentinel=HARNESS_FROZEN_SAFETY,
        reason="r",
        added_at=123.0,
    )
    d = e.to_dict()
    assert d["zone"] == "frozen-safety"
    assert d["path"] == "safety/y.py"
    assert d["sentinel"] == HARNESS_FROZEN_SAFETY
    s = e.to_json()
    obj = json.loads(s)
    assert obj["zone"] == "frozen-safety"
    print("  ✓ test_frozen_entry_to_dict_and_json")
    assert True


# ============ 8 HARNESS_FROZEN_* 常量 ============

def test_8_harness_frozen_sentinels():
    """8 个 HARNESS_FROZEN_* 常量字面量必须正确"""
    assert HARNESS_FROZEN_CANONICAL    == "HARNESS_FROZEN_CANONICAL"
    assert HARNESS_FROZEN_SAFETY       == "HARNESS_FROZEN_SAFETY"
    assert HARNESS_FROZEN_TUNING       == "HARNESS_FROZEN_TUNING"
    assert HARNESS_FROZEN_EXPERIMENTAL == "HARNESS_FROZEN_EXPERIMENTAL"
    assert HARNESS_FROZEN_LEARNER      == "HARNESS_FROZEN_LEARNER"
    assert HARNESS_FROZEN_REGISTRY     == "HARNESS_FROZEN_REGISTRY"
    assert HARNESS_FROZEN_BOOTSTRAP    == "HARNESS_FROZEN_BOOTSTRAP"
    assert HARNESS_FROZEN_CONFIG       == "HARNESS_FROZEN_CONFIG"
    print("  ✓ test_8_harness_frozen_sentinels")
    assert True


def test_all_harness_frozen_sentinels_list():
    """ALL_HARNESS_FROZEN_SENTINELS 含 8 个,无重复"""
    assert len(ALL_HARNESS_FROZEN_SENTINELS) == 8
    assert len(set(ALL_HARNESS_FROZEN_SENTINELS)) == 8
    assert HARNESS_FROZEN_CANONICAL in ALL_HARNESS_FROZEN_SENTINELS
    assert HARNESS_FROZEN_CONFIG in ALL_HARNESS_FROZEN_SENTINELS
    print("  ✓ test_all_harness_frozen_sentinels_list")
    assert True


# ============ FrozenRegistry add / get_zone ============

def test_registry_add_and_get_zone():
    """add 后 get_zone 返回正确 zone"""
    r = FrozenRegistry()
    r.add(FrozenEntry(path="a.py", zone=Zone.FROZEN_CANONICAL, reason="canonical"))
    r.add(FrozenEntry(path="b.py", zone=Zone.EVOLVABLE_TUNING, reason="tuning"))
    assert r.get_zone("a.py") is Zone.FROZEN_CANONICAL
    assert r.get_zone("b.py") is Zone.EVOLVABLE_TUNING
    assert len(r) == 2
    print("  ✓ test_registry_add_and_get_zone")
    assert True


def test_registry_add_overwrites_same_path():
    """同 path 二次 add → 覆盖"""
    r = FrozenRegistry()
    r.add(FrozenEntry(path="x.py", zone=Zone.FROZEN_CANONICAL, reason="v1"))
    r.add(FrozenEntry(path="x.py", zone=Zone.FROZEN_SAFETY, reason="v2"))
    assert r.get_zone("x.py") is Zone.FROZEN_SAFETY
    assert r.get("x.py").reason == "v2"
    assert len(r) == 1
    print("  ✓ test_registry_add_overwrites_same_path")
    assert True


# ============ is_frozen / is_evolvable ============

def test_is_frozen_canonical():
    """FROZEN_CANONICAL → is_frozen True"""
    r = FrozenRegistry()
    r.add(FrozenEntry(path="c.py", zone=Zone.FROZEN_CANONICAL))
    assert r.is_frozen("c.py") is True
    assert r.is_evolvable("c.py") is False
    print("  ✓ test_is_frozen_canonical")
    assert True


def test_is_frozen_safety():
    """FROZEN_SAFETY → is_frozen True"""
    r = FrozenRegistry()
    r.add(FrozenEntry(path="s.py", zone=Zone.FROZEN_SAFETY))
    assert r.is_frozen("s.py") is True
    assert r.is_evolvable("s.py") is False
    print("  ✓ test_is_frozen_safety")
    assert True


def test_is_frozen_evolvable_is_false():
    """EVOLVABLE_* → is_frozen False"""
    r = FrozenRegistry()
    r.add(FrozenEntry(path="t.json", zone=Zone.EVOLVABLE_TUNING))
    r.add(FrozenEntry(path="e.yaml", zone=Zone.EVOLVABLE_EXPERIMENTAL))
    assert r.is_frozen("t.json") is False
    assert r.is_frozen("e.yaml") is False
    print("  ✓ test_is_frozen_evolvable_is_false")
    assert True


def test_is_evolvable():
    """EVOLVABLE_* → is_evolvable True;FROZEN_* → is_evolvable False"""
    r = FrozenRegistry()
    r.add(FrozenEntry(path="t.json", zone=Zone.EVOLVABLE_TUNING))
    r.add(FrozenEntry(path="e.yaml", zone=Zone.EVOLVABLE_EXPERIMENTAL))
    r.add(FrozenEntry(path="c.py", zone=Zone.FROZEN_CANONICAL))
    assert r.is_evolvable("t.json") is True
    assert r.is_evolvable("e.yaml") is True
    assert r.is_evolvable("c.py") is False
    print("  ✓ test_is_evolvable")
    assert True


# ============ can_modify ============

def test_can_modify_frozen_false():
    """can_modify 在 FROZEN_* → False"""
    assert can_modify("any", Zone.FROZEN_CANONICAL) is False
    assert can_modify("any", Zone.FROZEN_SAFETY) is False
    print("  ✓ test_can_modify_frozen_false")
    assert True


def test_can_modify_evolvable_true():
    """can_modify 在 EVOLVABLE_* → True"""
    assert can_modify("any", Zone.EVOLVABLE_TUNING) is True
    assert can_modify("any", Zone.EVOLVABLE_EXPERIMENTAL) is True
    print("  ✓ test_can_modify_evolvable_true")
    assert True


# ============ assert_modifiable ============

def test_assert_modifiable_frozen_raises():
    """assert_modifiable 在 FROZEN_* → 抛 FrozenZoneError"""
    r = FrozenRegistry()
    r.add(FrozenEntry(path="c.py", zone=Zone.FROZEN_CANONICAL, reason="no edit"))
    raised = False
    try:
        assert_modifiable("c.py", r)
    except FrozenZoneError as e:
        raised = True
        assert e.path == "c.py"
        assert e.sentinel == HARNESS_FROZEN_CANONICAL
        assert e.reason == "no edit"
    assert raised, "expected FrozenZoneError"
    print("  ✓ test_assert_modifiable_frozen_raises")
    assert True


def test_assert_modifiable_evolvable_ok():
    """assert_modifiable 在 EVOLVABLE_* → 静默通过"""
    r = FrozenRegistry()
    r.add(FrozenEntry(path="t.json", zone=Zone.EVOLVABLE_TUNING))
    assert_modifiable("t.json", r)
    assert_modifiable("e.yaml", r)  # 不存在
    print("  ✓ test_assert_modifiable_evolvable_ok")
    assert True


# ============ FrozenZoneError ============

def test_frozen_zone_error_is_exception():
    """FrozenZoneError 必须是 Exception 子类"""
    assert issubclass(FrozenZoneError, Exception)
    err = FrozenZoneError(path="x", sentinel="HARNESS_FROZEN_X", reason="because")
    assert isinstance(err, Exception)
    assert err.path == "x"
    assert err.sentinel == "HARNESS_FROZEN_X"
    assert "x" in str(err) and "HARNESS_FROZEN_X" in str(err)
    print("  ✓ test_frozen_zone_error_is_exception")
    assert True


# ============ 边界 ============

def test_missing_path_returns_none_and_false():
    """未注册 path → get_zone None, is_frozen False, is_evolvable False"""
    r = FrozenRegistry()
    assert r.get_zone("nonexistent.py") is None
    assert r.is_frozen("nonexistent.py") is False
    assert r.is_evolvable("nonexistent.py") is False
    assert "nonexistent.py" not in r
    print("  ✓ test_missing_path_returns_none_and_false")
    assert True


# ============ JSON 序列化 ============

def test_registry_json_serialization():
    """FrozenRegistry.to_json 输出合法 JSON 数组;to_dict 输出 dict[path]"""
    r = FrozenRegistry()
    r.add(FrozenEntry(path="c.py", zone=Zone.FROZEN_CANONICAL, reason="c", added_at=1.0))
    r.add(FrozenEntry(path="t.json", zone=Zone.EVOLVABLE_TUNING, reason="t", added_at=2.0))
    s = r.to_json()
    arr = json.loads(s)
    assert isinstance(arr, list) and len(arr) == 2
    zones = sorted(x["zone"] for x in arr)
    assert zones == ["evolvable-tuning", "frozen-canonical"]
    d = r.to_dict()
    assert "c.py" in d and "t.json" in d
    assert d["c.py"]["sentinel"] == HARNESS_FROZEN_CANONICAL
    print("  ✓ test_registry_json_serialization")
    assert True


def test_classify_helper():
    """classify() 把 Zone 分类成 'frozen' / 'evolvable' / 'unknown'"""
    assert classify(Zone.FROZEN_CANONICAL) == "frozen"
    assert classify(Zone.FROZEN_SAFETY) == "frozen"
    assert classify(Zone.EVOLVABLE_TUNING) == "evolvable"
    assert classify(Zone.EVOLVABLE_EXPERIMENTAL) == "evolvable"
    assert classify("not-a-zone") == "unknown"
    print("  ✓ test_classify_helper")
    assert True
