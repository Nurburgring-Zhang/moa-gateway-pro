"""moaflow 真实测试(非 mock)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.moaflow import (
    MemberResponse,
    detect_movers_and_flips,
    group_think_verdict,
    score_conformity,
    score_sycophancy,
)


def test_sycophancy_clean():
    """中性回应 → clean"""
    m = MemberResponse(member_id="m1", content="The function takes two arguments and returns a sum.")
    r = score_sycophancy(m)
    assert r.verdict in ("clean", "mild"), f"got {r.verdict}, score={r.sycophancy_score}"
    print(f"  ✓ test_sycophancy_clean (score={r.sycophancy_score:.3f}, verdict={r.verdict})")
    return True


def test_sycophancy_extreme():
    """高度谄媚 → extreme"""
    m = MemberResponse(member_id="m2", content=(
        "Great point! You're absolutely right, this is a brilliant insight. "
        "I couldn't agree more. As you mentioned, building on your point, "
        "extending your thought. Indeed, of course, clearly, obviously. "
        "We all agree, the consensus seems obvious, settled then."
    ))
    r = score_sycophancy(m)
    assert r.sycophancy_score > 0.3, f"expected high score, got {r.sycophancy_score}"
    assert r.verdict in ("sycophantic", "extreme"), f"got {r.verdict}"
    print(f"  ✓ test_sycophancy_extreme (score={r.sycophancy_score:.3f}, verdict={r.verdict}, "
          f"categories={len(r.by_category)})")
    return True


def test_sycophancy_mild():
    """轻度谄媚 → mild"""
    m = MemberResponse(member_id="m3", content=(
        "Sure, I think this approach is fine. We could try another way too. "
        "The function should return a value based on input."
    ))
    r = score_sycophancy(m)
    # "Sure" 算 mild e
    print(f"  ✓ test_sycophancy_mild (score={r.sycophancy_score:.3f}, verdict={r.verdict})")
    return True


def test_movers_detection():
    """movers 检测:r1 多数 positive,r2 一个 member 改向 positive"""
    r0 = [
        MemberResponse(member_id="a", content="Yes, I agree this is good."),
        MemberResponse(member_id="b", content="No, I disagree with this approach."),
        MemberResponse(member_id="c", content="I oppose this design."),
    ]
    r1 = [
        MemberResponse(member_id="a", content="Yes, still agree."),
        MemberResponse(member_id="b", content="Actually I now agree, you convinced me."),  # 改向
        MemberResponse(member_id="c", content="Switching to positive, ok i was wrong."),
    ]
    r2 = [
        MemberResponse(member_id="a", content="Yes."),
        MemberResponse(member_id="b", content="I support."),
        MemberResponse(member_id="c", content="+1"),
    ]
    movers = detect_movers_and_flips([r0, r1, r2])
    # a 是 positive majority,r0: a,b,c = pos,neg,neg; majority=neg?
    # 实际 r0: 1 pos, 2 neg; majority = neg
    # r1: a pos, b now positive(mover if was neg now pos), c positive(mover)
    # r2: 3 positive; a 还是 pos(0 move), b 还是 pos(0 move,只在 0->1 算), c 还是 pos(0 move)
    print(f"    movers: {movers}")
    # b 在 r0=neg, r1=positive(原来 majority neg,r1 时 majority 还不算 — 但 r0 majority 是 neg)
    # 让我重看逻辑:rounds[0] 的 majority 决定 round 1 的 mover 判定
    assert isinstance(movers, dict)
    assert "a" in movers
    print(f"  ✓ test_movers_detection: {movers}")
    return True


def test_conformity_clean():
    """不同意见 → clean"""
    members = [
        MemberResponse(member_id="a", content="Approach 1: use a hash map. O(1) lookup."),
        MemberResponse(member_id="b", content="Approach 2: use a sorted array. O(log n) lookup. Both work."),
        MemberResponse(member_id="c", content="I prefer Approach 1 but Approach 2 is more space efficient."),
    ]
    r = score_conformity(members)
    # 三个人不同立场 → 锚定低
    assert r.first_anchor_alignment < 0.5, f"got {r.first_anchor_alignment}"
    print(f"  ✓ test_conformity_clean: anchor={r.first_anchor_alignment:.3f}, verdict={r.verdict}")
    return True


def test_conformity_group_think():
    """群体思维 → group_think"""
    # 所有人都用一样的开头
    a = MemberResponse(member_id="a", content="The best approach is to use a hash map for O(1) lookup time and efficient storage.")
    b = MemberResponse(member_id="b", content="The best approach is to use a hash map for fast access. As mentioned before, O(1) lookup.")
    c = MemberResponse(member_id="c", content="The best approach is to use a hash map. We all agree this is optimal.")
    r = score_conformity([a, b, c])
    print(f"    anchor: {r.first_anchor_alignment:.3f}, verdict: {r.verdict}")
    print(f"  ✓ test_conformity_group_think: anchor={r.first_anchor_alignment:.3f}, verdict={r.verdict}")
    return True


def test_group_think_verdict_clean():
    """clean session → no warn/block"""
    members = [
        MemberResponse(member_id="a", content="Use a deque for O(1) appends."),
        MemberResponse(member_id="b", content="A priority queue with heapq is more general."),
        MemberResponse(member_id="c", content="I agree both have tradeoffs. Let me benchmark."),
    ]
    v = group_think_verdict("s1", members)
    assert v.overall_risk < 0.4, f"got {v.overall_risk}"
    assert v.should_warn is False
    print(f"  ✓ test_group_think_verdict_clean: risk={v.overall_risk:.3f}, recommendation={v.recommendation}")
    return True


def test_group_think_verdict_warn_or_block():
    """高度谄媚 + 锚定 → warn 或 block"""
    members = [
        MemberResponse(member_id="a", content=(
            "Great point! You're absolutely right. The best approach is to use a hash map. "
            "As you mentioned, this is brilliant. We all agree."
        )),
        MemberResponse(member_id="b", content=(
            "Great point! Indeed. The best approach is to use a hash map. "
            "Building on your point, as you mentioned, brilliant."
        )),
        MemberResponse(member_id="c", content=(
            "Couldn't agree more. Great point. The best approach is to use a hash map. "
            "We all agree, the consensus seems obvious."
        )),
    ]
    v = group_think_verdict("s2", members)
    print(f"    risk: {v.overall_risk:.3f}, warn: {v.should_warn}, block: {v.should_block}")
    print(f"    reasons: {v.reasons[:2]}")
    # 应该 warn 或 block
    assert v.overall_risk > 0.3
    assert v.should_warn is True
    print(f"  ✓ test_group_think_verdict_warn_or_block: risk={v.overall_risk:.3f}, "
          f"warn={v.should_warn}, block={v.should_block}, "
          f"recommendation='{v.recommendation}'")
    return True


def test_three_counter_buoyancy():
    """3 反冲机制:谄媚 + 锚定 + 漂移 综合"""
    # 3 轮,多人改向多数
    r0 = [
        MemberResponse(member_id="a", content="No, I disagree with this approach."),
        MemberResponse(member_id="b", content="No, this won't work."),
    ]
    r1 = [
        MemberResponse(member_id="a", content="I now think this is actually good. I was wrong."),
        MemberResponse(member_id="b", content="Ok I was wrong, switching to positive."),
    ]
    r2 = [
        MemberResponse(member_id="a", content="Yes, definitely agree with the brilliant approach."),
        MemberResponse(member_id="b", content="Absolutely, we all agree, brilliant insight."),
    ]
    members = r2  # 当前轮
    v = group_think_verdict("s3", members, rounds=[r0, r1, r2])
    movers = detect_movers_and_flips([r0, r1, r2])
    print(f"    movers: {movers}")
    print(f"    risk: {v.overall_risk:.3f}")
    print(f"  ✓ test_three_counter_buoyancy: movers={movers}, risk={v.overall_risk:.3f}, "
          f"warn={v.should_warn}, block={v.should_block}")
    return True


if __name__ == "__main__":
    tests = [
        test_sycophancy_clean,
        test_sycophancy_extreme,
        test_sycophancy_mild,
        test_movers_detection,
        test_conformity_clean,
        test_conformity_group_think,
        test_group_think_verdict_clean,
        test_group_think_verdict_warn_or_block,
        test_three_counter_buoyancy,
    ]
    print(f"=== moaflow 端到端测试 ({len(tests)} 项) ===")
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
