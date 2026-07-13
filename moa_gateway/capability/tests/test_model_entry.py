"""moa_gateway.capability.model_entry 真实测试(非 mock)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from moa_gateway.capability.model_entry import (
    Modality,
    ModelEntry,
    CapabilityCheck,
    get_capability,
    filter_by_capability,
    filter_by_modality,
    filter_by_min_context,
    sort_by_cost,
    sort_by_context,
    find_within_budget,
    multimodal_score,
    to_json,
    from_json,
)


# =============================================================================
# 工具 fixture
# =============================================================================


def _mk(
    model_id: str = "m",
    provider: str = "openai",
    family: str = "gpt-4",
    context_window: int = 8000,
    max_output: int = 4000,
    modalities: list = None,
    supports_tools: bool = True,
    supports_vision: bool = False,
    supports_reasoning: bool = False,
    supports_streaming: bool = True,
    input_cost_per_1k: float = 0.01,
    output_cost_per_1k: float = 0.03,
) -> ModelEntry:
    if modalities is None:
        modalities = [Modality.TEXT]
    return ModelEntry(
        model_id=model_id,
        provider=provider,
        family=family,
        context_window=context_window,
        max_output=max_output,
        modalities=modalities,
        supports_tools=supports_tools,
        supports_vision=supports_vision,
        supports_reasoning=supports_reasoning,
        supports_streaming=supports_streaming,
        input_cost_per_1k=input_cost_per_1k,
        output_cost_per_1k=output_cost_per_1k,
    )


# =============================================================================
# 1) 12 字段全定义
# =============================================================================


def test_model_entry_12_fields():
    """ModelEntry 必须有 12 个字段全部可设"""
    fields = {
        "model_id", "provider", "family",
        "context_window", "max_output",
        "modalities",
        "supports_tools", "supports_vision", "supports_reasoning",
        "supports_streaming",
        "input_cost_per_1k", "output_cost_per_1k",
    }
    actual = set(ModelEntry.__dataclass_fields__.keys())
    assert fields.issubset(actual), f"missing fields: {fields - actual}"
    assert len(actual & fields) == 12, f"expected 12 spec fields, got {actual & fields}"
    print(f"  ✓ test_model_entry_12_fields (count={len(actual & fields)})")
    return True


# =============================================================================
# 2) Modality 5 个值
# =============================================================================


def test_modality_enum_five_values():
    """Modality 必须有 5 个值"""
    expected = {"TEXT", "IMAGE", "AUDIO", "VIDEO", "EMBEDDING"}
    actual = {m.name for m in Modality}
    assert actual == expected, f"modality values differ: {actual} vs {expected}"
    # 5 个全部可用
    for name in expected:
        m = Modality[name]
        assert m.value == name
    print(f"  ✓ test_modality_enum_five_values (count={len(actual)})")
    return True


# =============================================================================
# 3) get_capability 5 bool 字段
# =============================================================================


def test_get_capability_five_bools():
    """get_capability 必须返回 5 bool 字段"""
    # 场景 A: 全 True(模态含 IMAGE,所有 supports_*=True)
    e = _mk(
        modalities=[Modality.TEXT, Modality.IMAGE],
        supports_tools=True,
        supports_vision=True,
        supports_reasoning=True,
        supports_streaming=True,
    )
    cap = get_capability(e)
    assert isinstance(cap, CapabilityCheck)
    assert cap.supports_text is True
    assert cap.supports_vision is True
    assert cap.supports_tools is True
    assert cap.supports_streaming is True
    assert cap.supports_reasoning is True
    # 场景 B: IMAGE 不在 modalities → supports_vision 必须 False
    e2 = _mk(modalities=[Modality.TEXT], supports_vision=True)
    cap2 = get_capability(e2)
    assert cap2.supports_vision is False
    # 场景 C: 全 False
    e3 = _mk(
        modalities=[Modality.TEXT],
        supports_tools=False,
        supports_vision=False,
        supports_reasoning=False,
        supports_streaming=False,
    )
    cap3 = get_capability(e3)
    assert cap3.supports_text is True  # TEXT 永远 True
    assert cap3.supports_vision is False
    assert cap3.supports_tools is False
    assert cap3.supports_streaming is False
    assert cap3.supports_reasoning is False
    print("  ✓ test_get_capability_five_bools (3 scenarios)")
    return True


# =============================================================================
# 4) get_capability.compatible_modalities
# =============================================================================


def test_get_capability_compatible_modalities():
    """compatible_modalities 应为字符串列表"""
    e = _mk(modalities=[Modality.TEXT, Modality.IMAGE, Modality.AUDIO])
    cap = get_capability(e)
    assert isinstance(cap.compatible_modalities, list)
    assert "TEXT" in cap.compatible_modalities
    assert "IMAGE" in cap.compatible_modalities
    assert "AUDIO" in cap.compatible_modalities
    # 全部是 str(便于跨层传递)
    for m in cap.compatible_modalities:
        assert isinstance(m, str)
    print(f"  ✓ test_get_capability_compatible_modalities ({cap.compatible_modalities})")
    return True


# =============================================================================
# 5) filter_by_capability supports_vision
# =============================================================================


def test_filter_by_capability_supports_vision():
    """filter_by_capability(supports_vision=True) 过滤"""
    entries = [
        _mk(model_id="a", supports_vision=True),
        _mk(model_id="b", supports_vision=False),
        _mk(model_id="c", supports_vision=True),
    ]
    out = filter_by_capability(entries, "supports_vision", value=True)
    assert len(out) == 2
    assert {e.model_id for e in out} == {"a", "c"}
    # 反向
    out2 = filter_by_capability(entries, "supports_vision", value=False)
    assert len(out2) == 1
    assert out2[0].model_id == "b"
    # 顺序保留
    assert [e.model_id for e in out] == ["a", "c"]
    print(f"  ✓ test_filter_by_capability_supports_vision (kept={len(out)})")
    return True


# =============================================================================
# 6) filter_by_modality VISION
# =============================================================================


def test_filter_by_modality_image():
    """filter_by_modality(IMAGE) 过滤"""
    entries = [
        _mk(model_id="t1", modalities=[Modality.TEXT]),
        _mk(model_id="v1", modalities=[Modality.TEXT, Modality.IMAGE]),
        _mk(model_id="v2", modalities=[Modality.IMAGE]),
        _mk(model_id="a1", modalities=[Modality.AUDIO]),
    ]
    out = filter_by_modality(entries, Modality.IMAGE)
    assert len(out) == 2
    assert {e.model_id for e in out} == {"v1", "v2"}
    print(f"  ✓ test_filter_by_modality_image (kept={len(out)})")
    return True


# =============================================================================
# 7) filter_by_min_context
# =============================================================================


def test_filter_by_min_context():
    """filter_by_min_context(>= min)"""
    entries = [
        _mk(model_id="s", context_window=4000),
        _mk(model_id="m", context_window=16000),
        _mk(model_id="l", context_window=128000),
    ]
    out = filter_by_min_context(entries, 16000)
    assert len(out) == 2
    assert {e.model_id for e in out} == {"m", "l"}
    # 边界:min=0 全保留
    out2 = filter_by_min_context(entries, 0)
    assert len(out2) == 3
    # 边界:min 过大
    out3 = filter_by_min_context(entries, 200000)
    assert out3 == []
    print(f"  ✓ test_filter_by_min_context (min=16000, kept={len(out)})")
    return True


# =============================================================================
# 8) sort_by_cost 升序/降序
# =============================================================================


def test_sort_by_cost_ascending_descending():
    """sort_by_cost asc/desc"""
    entries = [
        _mk(model_id="cheap", input_cost_per_1k=0.001),
        _mk(model_id="mid",   input_cost_per_1k=0.01),
        _mk(model_id="pricey", input_cost_per_1k=0.1),
    ]
    asc = sort_by_cost(entries, ascending=True)
    assert [e.model_id for e in asc] == ["cheap", "mid", "pricey"]
    desc = sort_by_cost(entries, ascending=False)
    assert [e.model_id for e in desc] == ["pricey", "mid", "cheap"]
    # 默认 ascending
    default = sort_by_cost(entries)
    assert [e.model_id for e in default] == ["cheap", "mid", "pricey"]
    print("  ✓ test_sort_by_cost_ascending_descending")
    return True


# =============================================================================
# 9) sort_by_context 升序/降序
# =============================================================================


def test_sort_by_context_descending_ascending():
    """sort_by_context 默认 desc, 也可 asc"""
    entries = [
        _mk(model_id="s", context_window=4000),
        _mk(model_id="l", context_window=128000),
        _mk(model_id="m", context_window=16000),
    ]
    desc = sort_by_context(entries, descending=True)
    assert [e.model_id for e in desc] == ["l", "m", "s"]
    asc = sort_by_context(entries, descending=False)
    assert [e.model_id for e in asc] == ["s", "m", "l"]
    # 默认 descending
    default = sort_by_context(entries)
    assert [e.model_id for e in default] == ["l", "m", "s"]
    print("  ✓ test_sort_by_context_descending_ascending")
    return True


# =============================================================================
# 10) find_within_budget input only
# =============================================================================


def test_find_within_budget_input():
    """find_within_budget 只设 max_input_cost"""
    entries = [
        _mk(model_id="cheap",  input_cost_per_1k=0.001, output_cost_per_1k=0.05),
        _mk(model_id="mid",    input_cost_per_1k=0.01,  output_cost_per_1k=0.03),
        _mk(model_id="pricey", input_cost_per_1k=0.1,   output_cost_per_1k=0.3),
    ]
    out = find_within_budget(entries, max_input_cost=0.05)
    assert {e.model_id for e in out} == {"cheap", "mid"}
    print(f"  ✓ test_find_within_budget_input (kept={len(out)})")
    return True


# =============================================================================
# 11) find_within_budget output only
# =============================================================================


def test_find_within_budget_output():
    """find_within_budget 只设 max_output_cost"""
    entries = [
        _mk(model_id="cheap",  input_cost_per_1k=0.001, output_cost_per_1k=0.02),
        _mk(model_id="mid",    input_cost_per_1k=0.01,  output_cost_per_1k=0.03),
        _mk(model_id="pricey", input_cost_per_1k=0.1,   output_cost_per_1k=0.3),
    ]
    out = find_within_budget(entries, max_output_cost=0.04)
    # cheap=0.02 < 0.04 ✓, mid=0.03 < 0.04 ✓, pricey=0.3 > 0.04 ✗
    assert {e.model_id for e in out} == {"cheap", "mid"}
    print(f"  ✓ test_find_within_budget_output (kept={len(out)})")
    return True


# =============================================================================
# 12) find_within_budget 双向
# =============================================================================


def test_find_within_budget_both():
    """find_within_budget 同时设 input + output, AND 关系"""
    entries = [
        _mk(model_id="a", input_cost_per_1k=0.001, output_cost_per_1k=0.02),
        _mk(model_id="b", input_cost_per_1k=0.01,  output_cost_per_1k=0.03),
        _mk(model_id="c", input_cost_per_1k=0.1,   output_cost_per_1k=0.3),
    ]
    # a: input 0.001 < 0.05 ✓, output 0.02 < 0.04 ✓ → 留
    # b: input 0.01 < 0.05 ✓,  output 0.03 < 0.04 ✓ → 留
    # c: input 0.1  > 0.05 ✗ → 排除
    out = find_within_budget(entries, max_input_cost=0.05, max_output_cost=0.04)
    assert {e.model_id for e in out} == {"a", "b"}
    # 都 None → 全部
    out_all = find_within_budget(entries)
    assert len(out_all) == 3
    print(f"  ✓ test_find_within_budget_both (kept={len(out)})")
    return True


# =============================================================================
# 13) multimodal_score 全匹配 → 1.0
# =============================================================================


def test_multimodal_score_full_match():
    """entry 覆盖全部 query → 1.0"""
    e = _mk(modalities=[Modality.TEXT, Modality.IMAGE, Modality.AUDIO])
    score = multimodal_score(e, [Modality.TEXT, Modality.IMAGE])
    assert score == 1.0
    print("  ✓ test_multimodal_score_full_match (score=1.0)")
    return True


# =============================================================================
# 14) multimodal_score 半匹配 → 0.5
# =============================================================================


def test_multimodal_score_half_match():
    """query 2 个,entry 只覆盖 1 个 → 0.5"""
    e = _mk(modalities=[Modality.TEXT, Modality.IMAGE])
    score = multimodal_score(e, [Modality.TEXT, Modality.AUDIO])
    assert score == 0.5
    print("  ✓ test_multimodal_score_half_match (score=0.5)")
    return True


# =============================================================================
# 15) multimodal_score 不匹配 → 0.0
# =============================================================================


def test_multimodal_score_no_match():
    """entry 没有任何 query 模态 → 0.0"""
    e = _mk(modalities=[Modality.TEXT])
    score = multimodal_score(e, [Modality.IMAGE, Modality.AUDIO])
    assert score == 0.0
    # 空 query → 0.0
    e2 = _mk(modalities=[Modality.TEXT, Modality.IMAGE])
    assert multimodal_score(e2, []) == 0.0
    print("  ✓ test_multimodal_score_no_match (score=0.0)")
    return True


# =============================================================================
# 16) 边界: 空 entries → []
# =============================================================================


def test_empty_entries_returns_empty():
    """所有过滤/排序函数对空列表都返回 []"""
    assert filter_by_capability([], "supports_vision", True) == []
    assert filter_by_modality([], Modality.TEXT) == []
    assert filter_by_min_context([], 8000) == []
    assert sort_by_cost([]) == []
    assert sort_by_context([]) == []
    assert find_within_budget([]) == []
    assert find_within_budget([], max_input_cost=0.01) == []
    assert to_json([]) == "[]"
    assert from_json("[]") == []
    print("  ✓ test_empty_entries_returns_empty (8 cases)")
    return True


# =============================================================================
# 17) JSON 序列化 roundtrip
# =============================================================================


def test_json_serialization_roundtrip():
    """to_json → from_json roundtrip,内容不变"""
    entries = [
        _mk(
            model_id="gpt-4o",
            provider="openai",
            family="gpt-4",
            context_window=128000,
            max_output=16384,
            modalities=[Modality.TEXT, Modality.IMAGE],
            supports_tools=True,
            supports_vision=True,
            supports_reasoning=False,
            supports_streaming=True,
            input_cost_per_1k=0.005,
            output_cost_per_1k=0.015,
        ),
        _mk(
            model_id="deepseek-v3",
            provider="deepseek",
            family="deepseek",
            context_window=64000,
            max_output=8000,
            modalities=[Modality.TEXT],
            supports_tools=True,
            supports_vision=False,
            supports_reasoning=True,
            supports_streaming=True,
            input_cost_per_1k=0.00027,
            output_cost_per_1k=0.0011,
        ),
    ]
    s = to_json(entries)
    assert isinstance(s, str)
    # 验证 JSON 合法
    parsed = json.loads(s)
    assert len(parsed) == 2
    # modalities 序列化为字符串
    assert parsed[0]["modalities"] == ["TEXT", "IMAGE"]
    # roundtrip
    restored = from_json(s)
    assert len(restored) == 2
    assert restored[0].model_id == "gpt-4o"
    assert restored[0].context_window == 128000
    assert Modality.IMAGE in restored[0].modalities
    assert restored[1].model_id == "deepseek-v3"
    assert restored[1].supports_reasoning is True
    print(f"  ✓ test_json_serialization_roundtrip (2 entries, len_json={len(s)})")
    return True


# =============================================================================
# 18) 字段校验 (__post_init__)
# =============================================================================


def test_field_validation_post_init():
    """__post_init__ 应校验非法输入"""
    # 负 context_window
    with pytest.raises(ValueError):
        ModelEntry(
            model_id="x",
            provider="openai",
            family="gpt-4",
            context_window=-1,
            max_output=1000,
            modalities=[Modality.TEXT],
            supports_tools=True,
            supports_vision=False,
            supports_reasoning=False,
            supports_streaming=True,
            input_cost_per_1k=0.01,
            output_cost_per_1k=0.03,
        )
    # max_output > context_window
    with pytest.raises(ValueError):
        _mk(context_window=1000, max_output=2000)
    # 空 model_id
    with pytest.raises(ValueError):
        _mk(model_id="")
    # 负成本
    with pytest.raises(ValueError):
        _mk(input_cost_per_1k=-0.01)
    # 非法 modality 类型
    with pytest.raises(ValueError):
        _mk(modalities=["TEXT"])  # str 而不是 Modality
    # 非法 capability 字段
    with pytest.raises(ValueError):
        filter_by_capability([_mk()], "not_a_field", True)
    print("  ✓ test_field_validation_post_init (5 cases)")
    return True


# =============================================================================
# runner
# =============================================================================


def run_all():
    tests = [
        test_model_entry_12_fields,
        test_modality_enum_five_values,
        test_get_capability_five_bools,
        test_get_capability_compatible_modalities,
        test_filter_by_capability_supports_vision,
        test_filter_by_modality_image,
        test_filter_by_min_context,
        test_sort_by_cost_ascending_descending,
        test_sort_by_context_descending_ascending,
        test_find_within_budget_input,
        test_find_within_budget_output,
        test_find_within_budget_both,
        test_multimodal_score_full_match,
        test_multimodal_score_half_match,
        test_multimodal_score_no_match,
        test_empty_entries_returns_empty,
        test_json_serialization_roundtrip,
        test_field_validation_post_init,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            if t():
                passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{'='*60}")
    print(f"Results: {passed}/{len(tests)} passed, {failed} failed")
    print(f"{'='*60}")
    return passed, failed


if __name__ == "__main__":
    p, f = run_all()
    sys.exit(0 if f == 0 else 1)
