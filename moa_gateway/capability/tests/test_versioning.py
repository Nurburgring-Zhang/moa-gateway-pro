"""versioning 真实测试 (非 mock)"""
import sys
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.versioning import (
    ProposalVersion,
    VersionChain,
    VersionStore,
    parse_rating,
    parse_battle,
    swap_positions_battle,
    diff_versions,
    to_json,
)


# ============ VersionStore 测试 ============
def test_version_store_add_version_returns_v1():
    """首版 add_version → "v1" """
    store = VersionStore()
    v1 = store.add_version("prop1", "first draft")
    assert v1 == "v1", f"got {v1!r}"
    print(f"  ✓ test_version_store_add_version_returns_v1: {v1}")
    return True


def test_version_store_add_version_sequential_ids():
    """连续 add_version → v1, v2, v3 顺序"""
    store = VersionStore()
    a = store.add_version("prop1", "a")
    b = store.add_version("prop1", "b")
    c = store.add_version("prop1", "c")
    assert a == "v1" and b == "v2" and c == "v3", f"got {a},{b},{c}"
    print(f"  ✓ test_version_store_add_version_sequential_ids: {a},{b},{c}")
    return True


def test_version_store_get_chain_order():
    """get_chain 按 add 顺序返回 (时间升序)"""
    store = VersionStore()
    store.add_version("p", "alpha")
    store.add_version("p", "beta")
    store.add_version("p", "gamma")
    chain = store.get_chain("p")
    ids = [v.version_id for v in chain.versions]
    contents = [v.content for v in chain.versions]
    assert ids == ["v1", "v2", "v3"], f"got {ids}"
    assert contents == ["alpha", "beta", "gamma"], f"got {contents}"
    print(f"  ✓ test_version_store_get_chain_order: {ids}")
    return True


def test_version_store_get_version():
    """get_version 找到存在的 / 不存在返回 None"""
    store = VersionStore()
    store.add_version("p", "x")
    store.add_version("p", "y")
    v2 = store.get_version("p", "v2")
    assert v2 is not None
    assert v2.content == "y"
    missing = store.get_version("p", "v99")
    assert missing is None
    cross = store.get_version("other", "v1")
    assert cross is None
    print(f"  ✓ test_version_store_get_version: v2.content={v2.content}, v99/missing=None")
    return True


def test_version_store_latest():
    """latest 返回最后一个 add 的版本"""
    store = VersionStore()
    assert store.latest("p") is None
    store.add_version("p", "v1 content")
    store.add_version("p", "v2 content")
    store.add_version("p", "v3 content")
    latest = store.latest("p")
    assert latest is not None
    assert latest.version_id == "v3"
    assert latest.content == "v3 content"
    print(f"  ✓ test_version_store_latest: {latest.version_id} content={latest.content!r}")
    return True


def test_version_store_multi_version_chain():
    """多 proposal 各自独立, 互不干扰"""
    store = VersionStore()
    store.add_version("alpha", "a1")
    store.add_version("alpha", "a2")
    store.add_version("beta", "b1")
    store.add_version("beta", "b2")
    store.add_version("beta", "b3")
    a_chain = store.get_chain("alpha")
    b_chain = store.get_chain("beta")
    assert len(a_chain) == 2
    assert len(b_chain) == 3
    assert [v.version_id for v in a_chain.versions] == ["v1", "v2"]
    assert [v.version_id for v in b_chain.versions] == ["v1", "v2", "v3"]
    print(f"  ✓ test_version_store_multi_version_chain: alpha={len(a_chain)}, beta={len(b_chain)}")
    return True


def test_version_store_parent_version_id_link():
    """parent_version_id 正确关联到父版本"""
    store = VersionStore()
    v1 = store.add_version("p", "draft 1")
    v2 = store.add_version("p", "draft 2", parent=v1, critique="v1 lacks depth",
                           improvement="add details")
    v3 = store.add_version("p", "draft 3", parent=v2, critique="v2 missing example",
                           improvement="add example")
    assert v2 == "v2" and v3 == "v3"
    fetched_v2 = store.get_version("p", v2)
    fetched_v3 = store.get_version("p", v3)
    assert fetched_v2.parent_version_id == "v1", f"got {fetched_v2.parent_version_id}"
    assert fetched_v3.parent_version_id == "v2", f"got {fetched_v3.parent_version_id}"
    fetched_v1 = store.get_version("p", v1)
    assert fetched_v1.parent_version_id is None
    # 元数据
    assert fetched_v2.critique == "v1 lacks depth"
    assert fetched_v2.improvement_summary == "add details"
    assert fetched_v3.critique == "v2 missing example"
    print(f"  ✓ test_version_store_parent_version_id_link: {v1}→{v2}→{v3}")
    return True


def test_version_store_get_chain_unknown_proposal():
    """get_chain 不存在的 proposal 返回空链 (不抛错)"""
    store = VersionStore()
    chain = store.get_chain("nonexistent")
    assert chain.proposal_id == "nonexistent"
    assert chain.versions == []
    print(f"  ✓ test_version_store_get_chain_unknown_proposal: empty chain")
    return True


# ============ diff_versions 测试 ============
def test_diff_versions_length_change():
    """diff_versions 长度变化正确"""
    v1 = ProposalVersion(version_id="v1", content="short")
    v2 = ProposalVersion(version_id="v2", content="this is a much longer text than before")
    d = diff_versions(v1, v2)
    assert d["len_v1"] == len("short")
    assert d["len_v2"] == len("this is a much longer text than before")
    assert d["len_delta"] == d["len_v2"] - d["len_v1"]
    assert d["len_delta"] > 0
    assert d["v1_id"] == "v1"
    assert d["v2_id"] == "v2"
    print(f"  ✓ test_diff_versions_length_change: delta={d['len_delta']}")
    return True


def test_diff_versions_added_keywords():
    """diff_versions 关键词新增识别"""
    v1 = ProposalVersion(version_id="v1", content="apple banana cherry")
    v2 = ProposalVersion(version_id="v2", content="apple banana cherry dragon fruit elephant")
    d = diff_versions(v1, v2)
    # apple/banana/cherry 是 common
    assert "apple" in d["common_keywords"]
    assert "banana" in d["common_keywords"]
    # dragon/elephant 是新增
    assert "dragon" in d["added_keywords"]
    assert "elephant" in d["added_keywords"]
    # 没有 removed
    assert d["removed_keywords"] == []
    print(f"  ✓ test_diff_versions_added_keywords: added={d['added_keywords']}")
    return True


def test_diff_versions_removed_keywords_and_similarity():
    """diff_versions 关键词删除 + Jaccard 相似度"""
    v1 = ProposalVersion(version_id="v1",
                         content="quantum physics molecular biology chemistry astronomy mathematics")
    v2 = ProposalVersion(version_id="v2",
                         content="quantum physics molecular biology chemistry")
    d = diff_versions(v1, v2)
    assert "astronomy" in d["removed_keywords"]
    assert "mathematics" in d["removed_keywords"]
    assert d["added_keywords"] == []
    # 4 common / 6 union = 0.6667
    assert 0.6 < d["similarity"] < 0.75, f"got {d['similarity']}"
    print(f"  ✓ test_diff_versions_removed_keywords_and_similarity: removed={d['removed_keywords']}, sim={d['similarity']}")
    return True


def test_diff_versions_score_delta_uses_critique():
    """diff_versions 评分用 critique 字段 (parse_rating)"""
    v1 = ProposalVersion(version_id="v1", content="x", critique="[[rating]] 3")
    v2 = ProposalVersion(version_id="v2", content="x y z", critique="Rating: 8")
    d = diff_versions(v1, v2)
    assert d["score_v1"] == 3
    assert d["score_v2"] == 8
    assert d["score_delta"] == 5
    print(f"  ✓ test_diff_versions_score_delta_uses_critique: {d['score_v1']}→{d['score_v2']} (Δ={d['score_delta']})")
    return True


# ============ parse_rating 测试 ============
def test_parse_rating_double_bracket_8():
    """parse_rating: '[[rating]] 8' → 8"""
    r = parse_rating("Here is my judgment. [[rating]] 8")
    assert r == 8, f"got {r}"
    print(f"  ✓ test_parse_rating_double_bracket_8: {r}")
    return True


def test_parse_rating_colon_9():
    """parse_rating: 'Rating: 9' → 9"""
    r = parse_rating("Rating: 9 / 10 — excellent work")
    assert r == 9, f"got {r}"
    print(f"  ✓ test_parse_rating_colon_9: {r}")
    return True


def test_parse_rating_double_bracket_colon_7():
    """parse_rating: '[[rating:7]]' → 7 (备选格式)"""
    r = parse_rating("final verdict: [[rating:7]]")
    assert r == 7, f"got {r}"
    print(f"  ✓ test_parse_rating_double_bracket_colon_7: {r}")
    return True


def test_parse_rating_fallback_to_5():
    """parse_rating: 无法解析 → 5 (默认中位)"""
    r = parse_rating("this response has no numeric rating at all")
    assert r == 5, f"got {r}"
    # 空 / None
    assert parse_rating("") == 5
    assert parse_rating(None) == 5
    print(f"  ✓ test_parse_rating_fallback_to_5: {r}")
    return True


def test_parse_rating_clamps_to_range():
    """parse_rating: 越界值 (0 / 99) 钳制到 1-10"""
    assert parse_rating("Rating: 0") == 1
    assert parse_rating("Rating: 99") == 10
    assert parse_rating("Rating: 5") == 5
    assert parse_rating("Rating: -3") == 1
    print(f"  ✓ test_parse_rating_clamps_to_range: 0→1, 99→10, -3→1")
    return True


# ============ parse_battle 测试 ============
def test_parse_battle_a_wins():
    """parse_battle: A 胜 (显式 winner)"""
    w, c = parse_battle("After careful comparison, [[winner]] A — clearer explanation")
    assert w == "A", f"got {w}"
    assert c == 1, f"got {c}"
    print(f"  ✓ test_parse_battle_a_wins: winner={w} confidence={c}")
    return True


def test_parse_battle_b_wins():
    """parse_battle: B 胜 (better than 反向)"""
    w, c = parse_battle("B is better than A in terms of clarity and depth")
    assert w == "B", f"got {w}"
    print(f"  ✓ test_parse_battle_b_wins: winner={w}")
    return True


def test_parse_battle_tie():
    """parse_battle: tie 措辞"""
    w, c = parse_battle("Both responses are roughly equivalent. It's a tie.")
    assert w == "tie", f"got {w}"
    print(f"  ✓ test_parse_battle_tie: {w}")
    return True


# ============ swap_positions_battle 测试 ============
def test_swap_positions_consistent():
    """swap_positions: 双向一致 → 返回胜者

    构造一个 *位置无关* judge: 始终把 "marker_alpha" 标签所在的 response 判为胜者。
    这样的判定结果是绝对 winner (在原始 response 维度上), 不受位置影响。
    """
    def jfn(x: str, y: str) -> str:
        # 真正的位置无关: y 里有 marker → 投 A (因为 y 是 judge 的"A"位置)
        if "marker_alpha" in y:
            return "[[winner]] A — y has the marker"
        if "marker_alpha" in x:
            return "[[winner]] B — x has the marker"
        return "tie"

    response_a = "this is alpha content without marker"             # 不含 marker
    response_b = "this is beta content WITH marker_alpha inside"   # 含 marker

    result = swap_positions_battle(response_a, response_b, jfn)
    # round 1: judge_fn(a, b) → y=b 含 marker → A → response_a 胜
    # round 2: judge_fn(b, a) → y=a 不含 marker, x=b 含 marker → B → response_a 胜 (位置互换后)
    # 两轮原始 winner 都是 response_a → 一致 → 返回 response_a
    assert result == response_a, f"got {result!r}"
    print(f"  ✓ test_swap_positions_consistent: result=response_a (consistent both rounds)")
    return True


def test_swap_positions_inconsistent():
    """swap_positions: 双向不一致 → 'tie'"""
    def jfn(x, y):
        # 第 1 轮偏好 x (label A); 第 2 轮 (x=response_b) 仍偏好 x → 与第 1 轮原始 winner=response_a 不一致
        return f"[[winner]] A — prefer current first arg"

    # 第 1 轮: x=response_a → A → response_a 胜
    # 第 2 轮: x=response_b → A → response_b 胜
    # 不一致 → tie
    result = swap_positions_battle("alpha", "beta", jfn)
    assert result == "tie", f"got {result!r}"
    print(f"  ✓ test_swap_positions_inconsistent: result={result!r}")
    return True


def test_swap_positions_both_tie():
    """swap_positions: 双向都 tie → 'tie'"""
    def jfn(x, y):
        return "They are equivalent and it's a tie overall."

    result = swap_positions_battle("alpha", "beta", jfn)
    assert result == "tie", f"got {result!r}"
    print(f"  ✓ test_swap_positions_both_tie: result={result!r}")
    return True


# ============ JSON 序列化测试 ============
def test_json_serialization():
    """JSON 序列化: ProposalVersion / VersionChain / diff dict"""
    store = VersionStore()
    store.add_version("p", "v1 content", created_by="alice", created_at=1000.0)
    store.add_version("p", "v2 content", parent="v1", critique="v1 too short",
                      improvement="add detail", created_by="bob", created_at=1001.0)
    chain = store.get_chain("p")
    s = to_json(chain)
    parsed = json.loads(s)
    assert parsed["proposal_id"] == "p"
    assert len(parsed["versions"]) == 2
    assert parsed["versions"][0]["version_id"] == "v1"
    assert parsed["versions"][1]["parent_version_id"] == "v1"
    assert parsed["versions"][1]["critique"] == "v1 too short"
    assert parsed["versions"][1]["created_by"] == "bob"

    # diff_versions dict 也能序列化
    d = diff_versions(chain.versions[0], chain.versions[1])
    s2 = to_json(d)
    parsed2 = json.loads(s2)
    assert "len_delta" in parsed2
    assert "added_keywords" in parsed2
    assert "similarity" in parsed2
    print(f"  ✓ test_json_serialization: chain serialized, 2 versions, len_delta={parsed2['len_delta']}")
    return True


def test_proposal_version_to_dict_fields():
    """ProposalVersion.to_dict 字段完整"""
    pv = ProposalVersion(
        version_id="v1",
        content="hello",
        parent_version_id=None,
        created_at=123.45,
        created_by="alice",
        critique=None,
        improvement_summary=None,
    )
    d = pv.to_dict()
    expected = {"version_id", "content", "parent_version_id", "created_at",
                "created_by", "critique", "improvement_summary"}
    assert set(d.keys()) == expected, f"got {set(d.keys())}"
    assert d["content"] == "hello"
    assert d["created_at"] == 123.45
    print(f"  ✓ test_proposal_version_to_dict_fields: keys={sorted(d.keys())}")
    return True


if __name__ == "__main__":
    tests = [
        test_version_store_add_version_returns_v1,
        test_version_store_add_version_sequential_ids,
        test_version_store_get_chain_order,
        test_version_store_get_version,
        test_version_store_latest,
        test_version_store_multi_version_chain,
        test_version_store_parent_version_id_link,
        test_version_store_get_chain_unknown_proposal,
        test_diff_versions_length_change,
        test_diff_versions_added_keywords,
        test_diff_versions_removed_keywords_and_similarity,
        test_diff_versions_score_delta_uses_critique,
        test_parse_rating_double_bracket_8,
        test_parse_rating_colon_9,
        test_parse_rating_double_bracket_colon_7,
        test_parse_rating_fallback_to_5,
        test_parse_rating_clamps_to_range,
        test_parse_battle_a_wins,
        test_parse_battle_b_wins,
        test_parse_battle_tie,
        test_swap_positions_consistent,
        test_swap_positions_inconsistent,
        test_swap_positions_both_tie,
        test_json_serialization,
        test_proposal_version_to_dict_fields,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            print(f"  ✗ {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ {fn.__name__}: EXC {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(0 if failed == 0 else 1)
