"""quorum 真实测试 (非 mock)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.quorum import (
    Participant,
    QuorumConfig,
    check_quorum,
    force_close,
    parse_battle,
    parse_rating,
    should_wait,
    swap_positions_battle,
    to_json,
)


# ============ Quorum 测试 ============
def test_check_quorum_reached():
    """5/5 响应, required=3 → reached=True"""
    ps = [
        Participant("p1", True, "ans1", 100.0),
        Participant("p2", True, "ans2", 100.5),
        Participant("p3", True, "ans3", 101.0),
        Participant("p4", True, "ans4", 101.5),
        Participant("p5", True, "ans5", 102.0),
    ]
    cfg = QuorumConfig(required=3, grace_seconds=30.0)
    s = check_quorum(ps, cfg, at=110.0)
    assert s.reached is True, f"got {s.reached}"
    assert s.responded_count == 5
    assert s.missing == []
    assert s.reached_at == 101.0  # 第 3 个响应 (required=3) 的时间
    print(f"  ✓ test_check_quorum_reached: reached=True, count=5, reached_at={s.reached_at}")
    return True


def test_check_quorum_not_reached():
    """2/5 响应, required=3 → reached=False, missing=3 ids"""
    ps = [
        Participant("p1", True, "ans1", 100.0),
        Participant("p2", True, "ans2", 100.5),
        Participant("p3", False, None, None),
        Participant("p4", False, None, None),
        Participant("p5", False, None, None),
    ]
    cfg = QuorumConfig(required=3, grace_seconds=30.0)
    s = check_quorum(ps, cfg, at=105.0)
    assert s.reached is False, f"got {s.reached}"
    assert s.responded_count == 2
    assert s.missing == ["p3", "p4", "p5"]
    assert s.reached_at is None
    print(f"  ✓ test_check_quorum_not_reached: reached=False, missing={s.missing}")
    return True


def test_check_quorum_boundary_equal_required():
    """边界: responded_count == required → reached=True"""
    ps = [
        Participant("p1", True, "ans1", 100.0),
        Participant("p2", True, "ans2", 100.5),
        Participant("p3", True, "ans3", 101.0),
        Participant("p4", False, None, None),
    ]
    cfg = QuorumConfig(required=3, grace_seconds=30.0)
    s = check_quorum(ps, cfg, at=105.0)
    assert s.reached is True
    assert s.responded_count == 3
    assert s.missing == ["p4"]
    assert s.reached_at == 101.0
    print(f"  ✓ test_check_quorum_boundary_equal_required: reached=True, missing={s.missing}")
    return True


def test_missing_list_correct():
    """missing 列表保持原顺序, 仅含未响应 id"""
    ps = [
        Participant("alpha", True, "a", 100.0),
        Participant("beta", False, None, None),
        Participant("gamma", True, "g", 100.2),
        Participant("delta", False, None, None),
    ]
    cfg = QuorumConfig(required=2, grace_seconds=10.0)
    s = check_quorum(ps, cfg, at=100.5)
    assert s.missing == ["beta", "delta"], f"got {s.missing}"
    assert s.responded_count == 2
    print(f"  ✓ test_missing_list_correct: missing={s.missing}")
    return True


def test_within_grace_true():
    """已达成 + 未超 grace → within_grace=True"""
    ps = [
        Participant("p1", True, "a", 100.0),
        Participant("p2", True, "b", 100.5),
        Participant("p3", True, "c", 101.0),
    ]
    cfg = QuorumConfig(required=3, grace_seconds=30.0)
    s = check_quorum(ps, cfg, at=110.0)  # first_response=100.0, at=110.0 → 10s < 30s
    assert s.within_grace is True, f"got {s.within_grace}"
    print("  ✓ test_within_grace_true: 10s < 30s grace")
    return True


def test_within_grace_false_timeout():
    """已达成 + 超过 grace → within_grace=False"""
    ps = [
        Participant("p1", True, "a", 100.0),
        Participant("p2", True, "b", 100.5),
        Participant("p3", True, "c", 101.0),
    ]
    cfg = QuorumConfig(required=3, grace_seconds=5.0)
    s = check_quorum(ps, cfg, at=200.0)  # 100s 远超 5s
    assert s.within_grace is False, f"got {s.within_grace}"
    print("  ✓ test_within_grace_false_timeout: 100s > 5s grace")
    return True


def test_should_wait_true():
    """reached + within_grace + wait_for_laggards=True → True"""
    ps = [
        Participant("p1", True, "a", 100.0),
        Participant("p2", True, "b", 100.5),
        Participant("p3", True, "c", 101.0),
        Participant("p4", False, None, None),  # 落伍者
    ]
    cfg = QuorumConfig(required=3, grace_seconds=30.0, wait_for_laggards=True)
    s = check_quorum(ps, cfg, at=110.0)
    assert should_wait(s, cfg, at=110.0) is True
    print("  ✓ test_should_wait_true: True")
    return True


def test_should_wait_false():
    """within_grace=False → should_wait=False"""
    ps = [
        Participant("p1", True, "a", 100.0),
        Participant("p2", True, "b", 100.5),
        Participant("p3", True, "c", 101.0),
        Participant("p4", False, None, None),
    ]
    cfg = QuorumConfig(required=3, grace_seconds=5.0, wait_for_laggards=True)
    s = check_quorum(ps, cfg, at=200.0)
    assert s.within_grace is False
    assert should_wait(s, cfg, at=200.0) is False
    print("  ✓ test_should_wait_false: False (timeout)")
    return True


def test_should_wait_disabled_by_config():
    """wait_for_laggards=False → should_wait=False"""
    ps = [
        Participant("p1", True, "a", 100.0),
        Participant("p2", True, "b", 100.5),
        Participant("p3", True, "c", 101.0),
    ]
    cfg = QuorumConfig(required=3, grace_seconds=30.0, wait_for_laggards=False)
    s = check_quorum(ps, cfg, at=110.0)
    assert should_wait(s, cfg, at=110.0) is False
    print("  ✓ test_should_wait_disabled_by_config: False (config disabled)")
    return True


def test_force_close_drops_unresponded():
    """force_close 把未响应者标为 DROPPED"""
    ps = [
        Participant("p1", True, "a", 100.0),
        Participant("p2", False, None, None),
        Participant("p3", True, "c", 101.0),
        Participant("p4", False, None, None),
    ]
    cfg = QuorumConfig(required=2, grace_seconds=30.0)
    closed_ps, dropped = force_close(ps, cfg, at=200.0)
    assert sorted(dropped) == ["p2", "p4"], f"got {dropped}"
    # 检查被关闭的
    by_id = {p.participant_id: p for p in closed_ps}
    assert by_id["p2"].responded is True
    assert by_id["p2"].response == "DROPPED"
    assert by_id["p2"].responded_at == 200.0
    # 原本响应的保持不变
    assert by_id["p1"].response == "a"
    assert by_id["p3"].response == "c"
    print(f"  ✓ test_force_close_drops_unresponded: dropped={dropped}")
    return True


# ============ LLM-as-Judge 评分测试 ============
def test_parse_rating_double_bracket_a():
    """parse_rating '[[rating_a]] 8' → 8"""
    assert parse_rating("[[rating_a]] 8") == 8
    assert parse_rating("After review, [[rating_a]] 7 overall") == 7
    print("  ✓ test_parse_rating_double_bracket_a: 8")
    return True


def test_parse_rating_colon_format():
    """parse_rating 'Rating: 9' → 9"""
    assert parse_rating("Rating: 9") == 9
    assert parse_rating("rating = 6") == 6
    print("  ✓ test_parse_rating_colon_format: 9")
    return True


def test_parse_rating_double_bracket_colon():
    """parse_rating '[[rating:7]]' → 7"""
    assert parse_rating("[[rating:7]]") == 7
    assert parse_rating("My judgment: [[rating: 4 ]]") == 4
    print("  ✓ test_parse_rating_double_bracket_colon: 7")
    return True


def test_parse_rating_fallback_to_5():
    """parse_rating 解析失败 → 5"""
    assert parse_rating("no rating here, just text") == 5
    assert parse_rating("") == 5
    assert parse_rating("this is garbage") == 5
    print("  ✓ test_parse_rating_fallback_to_5: 5")
    return True


def test_parse_rating_range_1_to_10():
    """parse_rating 强制 1-10 范围"""
    assert parse_rating("Rating: 0") == 1, "0 should clamp to 1"
    assert parse_rating("Rating: 15") == 10, "15 should clamp to 10"
    assert parse_rating("Rating: -5") == 1
    assert parse_rating("Rating: 100") == 10
    assert parse_rating("Rating: 1") == 1
    assert parse_rating("Rating: 10") == 10
    # 分数格式
    assert parse_rating("I rate this 8/10") == 8
    assert parse_rating("[7/10]") == 7
    print("  ✓ test_parse_rating_range_1_to_10: clamp works")
    return True


# ============ LLM-as-Judge 对战测试 ============
def test_parse_battle_a_wins():
    """parse_battle 'A is better' → ('A', ...)"""
    w, c = parse_battle("A is better than B in clarity")
    assert w == "A", f"got {w}"
    assert c >= 1
    # 显式标签
    w2, c2 = parse_battle("[[winner]] A")
    assert w2 == "A"
    assert c2 == 1
    print("  ✓ test_parse_battle_a_wins: A")
    return True


def test_parse_battle_b_wins():
    """parse_battle 'B is better' → 'B'"""
    w, _ = parse_battle("B is better")
    assert w == "B", f"got {w}"
    w2, _ = parse_battle("I prefer B for accuracy")
    assert w2 == "B"
    w3, _ = parse_battle("[[winner]] B")
    assert w3 == "B"
    print("  ✓ test_parse_battle_b_wins: B")
    return True


def test_parse_battle_tie():
    """parse_battle 'tie' → 'tie'"""
    w, c = parse_battle("This is a tie, both are good")
    assert w == "tie", f"got {w}"
    assert c == 1
    w2, _ = parse_battle("equal quality")
    assert w2 == "tie"
    # 解析失败
    w3, c3 = parse_battle("no clear winner here")
    assert w3 == "tie"
    assert c3 == 0
    print("  ✓ test_parse_battle_tie: tie")
    return True


# ============ 抗位置偏置测试 ============
def test_swap_positions_consistent():
    """swap_positions 一致 → 返回 winner 字符串

    一致 judge: 真正判断内容质量, 不论位置
    - 第 1 轮: ("good", "bad") → judge 选 A (good 胜)
    - 第 2 轮: ("bad", "good") → judge 选 B (good 胜, 因为 B=good)
    - 两次都指向原始 response "good" → 返回 "good"
    """
    def judge(prompt_a: str, prompt_b: str) -> str:
        # judge 看 content, 选内容更好的那个 (位置无关)
        if prompt_a == "good":
            return "[[winner]] A"
        return "[[winner]] B"

    result = swap_positions_battle("good", "bad", judge)
    assert result == "good", f"got {result}"
    print("  ✓ test_swap_positions_consistent: 'good' (consistent across positions)")
    return True


def test_swap_positions_inconsistent():
    """swap_positions 不一致 (有位置偏置) → 'tie'"""
    def judge(prompt_a: str, prompt_b: str) -> str:
        # judge 总是说第一个好 (位置偏置)
        return "[[winner]] A"

    # 两次都说 A 胜, 但映射回原 label:
    #   第 1 轮: (good, bad) → A 胜 → good
    #   第 2 轮: (bad, good) → A 胜 → bad (因为 A 现在是 bad)
    # 不一致 → tie
    result = swap_positions_battle("good", "bad", judge)
    assert result == "tie", f"expected tie, got {result}"
    print("  ✓ test_swap_positions_inconsistent: tie (position bias detected)")
    return True


# ============ JSON 序列化测试 ============
def test_json_serialization():
    """所有 dataclass 都能 to_dict / JSON 序列化"""
    cfg = QuorumConfig(required=3, grace_seconds=15.5, wait_for_laggards=True)
    p = Participant("p1", True, "ans", 100.0)
    ps = [p, Participant("p2", False, None, None)]
    s = check_quorum(ps, cfg, at=105.0)

    # 单独 dataclass
    cfg_d = cfg.to_dict()
    p_d = p.to_dict()
    s_d = s.to_dict()
    assert cfg_d["required"] == 3
    assert cfg_d["grace_seconds"] == 15.5
    assert cfg_d["wait_for_laggards"] is True
    assert p_d["participant_id"] == "p1"
    assert p_d["responded"] is True
    assert s_d["reached"] is False
    assert s_d["missing"] == ["p2"]

    # to_json 统一序列化
    j = to_json(cfg)
    obj = json.loads(j)
    assert obj["required"] == 3
    j2 = to_json(s)
    obj2 = json.loads(j2)
    assert obj2["responded_count"] == 1
    print("  ✓ test_json_serialization: all dataclasses serializable")
    return True


if __name__ == "__main__":
    tests = [
        test_check_quorum_reached,
        test_check_quorum_not_reached,
        test_check_quorum_boundary_equal_required,
        test_missing_list_correct,
        test_within_grace_true,
        test_within_grace_false_timeout,
        test_should_wait_true,
        test_should_wait_false,
        test_should_wait_disabled_by_config,
        test_force_close_drops_unresponded,
        test_parse_rating_double_bracket_a,
        test_parse_rating_colon_format,
        test_parse_rating_double_bracket_colon,
        test_parse_rating_fallback_to_5,
        test_parse_rating_range_1_to_10,
        test_parse_battle_a_wins,
        test_parse_battle_b_wins,
        test_parse_battle_tie,
        test_swap_positions_consistent,
        test_swap_positions_inconsistent,
        test_json_serialization,
    ]
    print(f"=== quorum 端到端测试 ({len(tests)} 项) ===")
    passed = 0
    failed = []
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
            failed.append(t.__name__)
    print(f"\n=== 结果: {passed}/{len(tests)} 通过 ===")
    if failed:
        print(f"失败: {failed}")
        sys.exit(1)
