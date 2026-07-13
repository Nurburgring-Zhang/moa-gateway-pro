"""test_acceptance — Acceptance Tree + EARS/GEARS 模式匹配测试"""
import json
import pytest

from moa_gateway.capability.acceptance import (
    AcceptanceCriterion,
    AcceptanceTree,
    GEARSPattern,
    EARSPattern,
    validate_pattern,
    parse_ears,
    tree_to_dict,
    tree_from_dict,
)


# ============ 1. AcceptanceCriterion 4 字段 ============
def test_criterion_has_four_core_fields():
    ac = AcceptanceCriterion(
        id="ac-1",
        given="user is logged in",
        when="user clicks logout",
        then="user is redirected to home",
    )
    assert ac.id == "ac-1"
    assert ac.given == "user is logged in"
    assert ac.when == "user clicks logout"
    assert ac.then == "user is redirected to home"
    assert ac.parent_id is None
    assert ac.children_ids == []


def test_criterion_default_parent_and_children():
    ac = AcceptanceCriterion(
        id="ac-2", given="g", when="w", then="t",
    )
    assert ac.parent_id is None
    assert ac.children_ids == []


# ============ 2. AcceptanceTree add ============
def test_tree_add_criterion():
    tree = AcceptanceTree("root")
    ac = AcceptanceCriterion(
        id="ac-1", given="g", when="w", then="t", parent_id="root",
    )
    tree.add_criterion(ac)
    assert tree.get_criterion("ac-1") is ac
    root = tree.get_criterion("root")
    assert "ac-1" in root.children_ids


def test_tree_add_duplicate_raises():
    tree = AcceptanceTree("root")
    tree.add_criterion(AcceptanceCriterion(
        id="ac-1", given="g", when="w", then="t", parent_id="root",
    ))
    with pytest.raises(ValueError, match="duplicate"):
        tree.add_criterion(AcceptanceCriterion(
            id="ac-1", given="g", when="w", then="t", parent_id="root",
        ))


def test_tree_add_missing_parent_raises():
    tree = AcceptanceTree("root")
    with pytest.raises(ValueError, match="parent not found"):
        tree.add_criterion(AcceptanceCriterion(
            id="ac-x", given="g", when="w", then="t", parent_id="ghost",
        ))


# ============ 3. get_criterion ============
def test_get_criterion_existing_and_missing():
    tree = AcceptanceTree("root")
    tree.add_criterion(AcceptanceCriterion(
        id="ac-1", given="g", when="w", then="t", parent_id="root",
    ))
    assert tree.get_criterion("ac-1") is not None
    assert tree.get_criterion("ac-1").id == "ac-1"
    assert tree.get_criterion("nonexistent") is None


# ============ 4. get_children ============
def test_get_children_direct_only():
    tree = AcceptanceTree("root")
    tree.add_criterion(AcceptanceCriterion(
        id="a", given="g", when="w", then="t", parent_id="root",
    ))
    tree.add_criterion(AcceptanceCriterion(
        id="b", given="g", when="w", then="t", parent_id="root",
    ))
    tree.add_criterion(AcceptanceCriterion(
        id="c", given="g", when="w", then="t", parent_id="a",
    ))
    children = tree.get_children("root")
    ids = sorted(c.id for c in children)
    assert ids == ["a", "b"]
    assert tree.get_children("a")[0].id == "c"


def test_get_children_missing_returns_empty():
    tree = AcceptanceTree("root")
    assert tree.get_children("missing") == []


# ============ 5. get_descendants ============
def test_get_descendants_nested():
    tree = AcceptanceTree("root")
    tree.add_criterion(AcceptanceCriterion(
        id="a", given="g", when="w", then="t", parent_id="root",
    ))
    tree.add_criterion(AcceptanceCriterion(
        id="b", given="g", when="w", then="t", parent_id="a",
    ))
    tree.add_criterion(AcceptanceCriterion(
        id="c", given="g", when="w", then="t", parent_id="b",
    ))
    desc = tree.get_descendants("root")
    ids = [d.id for d in desc]
    assert ids == ["a", "b", "c"]


def test_get_descendants_leaf_is_empty():
    tree = AcceptanceTree("root")
    tree.add_criterion(AcceptanceCriterion(
        id="leaf", given="g", when="w", then="t", parent_id="root",
    ))
    assert tree.get_descendants("leaf") == []


# ============ 6. validate_ids 重复 ============
def test_validate_ids_duplicate_detection():
    tree = AcceptanceTree("root")
    tree._criteria["dup"] = AcceptanceCriterion(
        id="dup", given="g", when="w", then="t", parent_id="root",
    )
    tree._criteria["dup2"] = AcceptanceCriterion(
        id="dup", given="g2", when="w2", then="t2", parent_id="root",
    )
    errors = tree.validate_ids()
    assert any("duplicate" in e for e in errors)


# ============ 7. validate_ids 格式 ============
def test_validate_ids_format_invalid_chars():
    tree = AcceptanceTree("root")
    tree._criteria["bad id!"] = AcceptanceCriterion(
        id="bad id!", given="g", when="w", then="t", parent_id="root",
    )
    errors = tree.validate_ids()
    assert any("invalid id format" in e for e in errors)


def test_validate_ids_clean_tree():
    tree = AcceptanceTree("root")
    tree.add_criterion(AcceptanceCriterion(
        id="ac-1", given="g", when="w", then="t", parent_id="root",
    ))
    assert tree.validate_ids() == []


# ============ 8. 5 GEARS 模式枚举 ============
def test_gears_patterns_count_and_values():
    values = [p.value for p in GEARSPattern]
    assert len(values) == 5
    assert "GIVEN_正常" in values
    assert "WHEN_正常" in values
    assert "THEN_正常" in values
    assert "GIVEN_异常" in values
    assert "WHEN_异常" in values


# ============ 9. 6 EARS legacy 模式 ============
def test_ears_patterns_count_and_values():
    values = [p.value for p in EARSPattern]
    assert len(values) == 6
    for name in ("UBIQUITOUS", "EVENT_DRIVEN", "STATE_DRIVEN",
                 "OPTIONAL", "UNWANTED", "TIMED"):
        assert name in values


# ============ 10. parse_ears EVENT_DRIVEN ============
def test_parse_ears_event_driven():
    text = "When user clicks submit, the form is saved"
    acs = parse_ears(text)
    assert len(acs) == 1
    ac = acs[0]
    assert ac.pattern == EARSPattern.EVENT_DRIVEN.value
    assert "submit" in ac.when
    assert "saved" in ac.then


# ============ 11. parse_ears STATE_DRIVEN ============
def test_parse_ears_state_driven():
    text = "While in logged_out state, the system shows login form"
    acs = parse_ears(text)
    assert len(acs) == 1
    ac = acs[0]
    assert ac.pattern == EARSPattern.STATE_DRIVEN.value
    assert "logged_out" in ac.given


# ============ 12. parse_ears TIMED ============
def test_parse_ears_timed():
    text = "Within 5 seconds, the request shall complete"
    acs = parse_ears(text)
    assert len(acs) == 1
    ac = acs[0]
    assert ac.pattern == EARSPattern.TIMED.value
    assert "5 seconds" in ac.given or "5 seconds" in ac.when


# ============ 13. parse_ears 多行混合 ============
def test_parse_ears_multiline_filters_blanks():
    text = """
    When user clicks A, do X

    If error occurs, the system shall not crash
    """
    acs = parse_ears(text)
    assert len(acs) == 2
    assert acs[0].pattern == EARSPattern.EVENT_DRIVEN.value
    assert acs[1].pattern == EARSPattern.UNWANTED.value


# ============ 14. validate_pattern GEARS ============
def test_validate_pattern_given_normal():
    ac = AcceptanceCriterion(id="x", given="user is admin", when="", then="")
    assert validate_pattern(ac) == GEARSPattern.GIVEN_NORMAL.value


def test_validate_pattern_when_abnormal():
    ac = AcceptanceCriterion(id="x", given="", when="network timeout occurs", then="")
    assert validate_pattern(ac) == GEARSPattern.WHEN_ABNORMAL.value


def test_validate_pattern_then_normal_full():
    ac = AcceptanceCriterion(
        id="x", given="logged in", when="clicks buy", then="order created",
    )
    assert validate_pattern(ac) == GEARSPattern.THEN_NORMAL.value


# ============ 15. validate_pattern EARS ============
def test_validate_pattern_ears_via_ears_field():
    """EARS 模式由 parse_ears 写入 ac.pattern,validate_pattern 仍是 GEARS 路径。

    验证: 手动设置 pattern 后,字段保持;validate_pattern 只看 G/W/T 字段。
    """
    ac = parse_ears("When X is pressed, Y is shown")[0]
    assert ac.pattern == "EVENT_DRIVEN"
    pat = validate_pattern(ac)
    assert pat == GEARSPattern.THEN_NORMAL.value


# ============ 16. 嵌套继承 ============
def test_nested_inheritance_chain():
    tree = AcceptanceTree("root")
    tree.add_criterion(AcceptanceCriterion(
        id="l1", given="g1", when="w1", then="t1", parent_id="root",
    ))
    tree.add_criterion(AcceptanceCriterion(
        id="l2", given="g2", when="w2", then="t2", parent_id="l1",
    ))
    tree.add_criterion(AcceptanceCriterion(
        id="l3", given="g3", when="w3", then="t3", parent_id="l2",
    ))
    l1 = tree.get_criterion("l1")
    l2 = tree.get_criterion("l2")
    l3 = tree.get_criterion("l3")
    assert l1.parent_id == "root"
    assert l2.parent_id == "l1"
    assert l3.parent_id == "l2"
    assert l1.children_ids == ["l2"]
    assert l2.children_ids == ["l3"]
    desc = tree.get_descendants("root")
    assert [d.id for d in desc] == ["l1", "l2", "l3"]


# ============ 17. JSON 序列化 ============
def test_json_serialization_roundtrip():
    tree = AcceptanceTree("root")
    tree.add_criterion(AcceptanceCriterion(
        id="a", given="g", when="w", then="t", parent_id="root",
    ))
    tree.add_criterion(AcceptanceCriterion(
        id="b", given="g", when="w", then="t", parent_id="a",
    ))
    payload = tree_to_dict(tree)
    s = json.dumps(payload)
    loaded = json.loads(s)
    assert "criteria" in loaded
    assert len(loaded["criteria"]) == 3

    tree2 = tree_from_dict(loaded)
    assert tree2.get_criterion("a") is not None
    assert tree2.get_criterion("b") is not None
    assert tree2.get_descendants("root")[0].id == "a"


# ============ 18. 边界: 0 criterion ============
def test_empty_tree_only_root():
    tree = AcceptanceTree("root")
    criteria = tree.all_criteria()
    assert len(criteria) == 1
    assert criteria[0].id == "root"
    assert tree.get_children("root") == []
    assert tree.get_descendants("root") == []
    assert tree.validate_ids() == []


def test_parse_ears_empty_input():
    assert parse_ears("") == []
    assert parse_ears("\n\n   \n") == []


def test_get_criterion_unknown_returns_none():
    tree = AcceptanceTree("root")
    assert tree.get_criterion("nope") is None
