"""moa_gateway.capability.model_context_db 真实测试(非 mock)"""
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.model_context_db import (
    MODEL_DATABASE,
    calculate_max_tokens,
    estimate_cost,
    find_cheapest_for_context,
    get_model_spec,
    list_models,
)

# =============================================================================
# 查询
# =============================================================================


def test_get_model_spec_deepseek_v3():
    """查 deepseek-v3,context=64000"""
    spec = get_model_spec("deepseek-v3")
    assert spec is not None
    assert spec.id == "deepseek-v3"
    assert spec.provider == "deepseek"
    assert spec.context_window == 64000
    assert spec.max_output == 8000
    assert spec.input_cost_per_1k > 0
    print(f"  ✓ test_get_model_spec_deepseek_v3 (context={spec.context_window})")
    return True


def test_get_model_spec_gpt4o():
    """查 gpt-4o,context=128000"""
    spec = get_model_spec("gpt-4o")
    assert spec is not None
    assert spec.id == "gpt-4o"
    assert spec.provider == "openai"
    assert spec.context_window == 128000
    assert spec.max_output == 16384
    assert spec.supports_vision is True
    print(f"  ✓ test_get_model_spec_gpt4o (context={spec.context_window})")
    return True


def test_get_model_spec_claude35():
    """查 claude-3-5-sonnet,context=200000"""
    spec = get_model_spec("claude-3-5-sonnet")
    assert spec is not None
    assert spec.id == "claude-3-5-sonnet"
    assert spec.provider == "anthropic"
    assert spec.context_window == 200000
    assert spec.supports_vision is True
    print(f"  ✓ test_get_model_spec_claude35 (context={spec.context_window})")
    return True


def test_get_model_spec_unknown():
    """查 unknown → None"""
    spec = get_model_spec("nonexistent-model-xyz-9999")
    assert spec is None
    print("  ✓ test_get_model_spec_unknown (returned None)")
    return True


# =============================================================================
# 过滤
# =============================================================================


def test_list_models_filter_provider():
    """只列 deepseek"""
    result = list_models(provider="deepseek")
    assert len(result) >= 2, f"expected >= 2 deepseek models, got {len(result)}"
    for spec in result:
        assert spec.provider == "deepseek", f"non-deepseek leaked: {spec.id}"
    ids = {s.id for s in result}
    assert "deepseek-v3" in ids
    assert "deepseek-r1" in ids
    print(f"  ✓ test_list_models_filter_provider (found {len(result)}: {sorted(ids)})")
    return True


def test_list_models_filter_tools():
    """只列 supports_tools=True"""
    result = list_models(supports_tools=True)
    assert len(result) > 0
    for spec in result:
        assert spec.supports_tools is True
    # 反向:supports_tools=False 应该包含 o1-preview / mock-fast / qwen-vl-max
    no_tools = list_models(supports_tools=False)
    no_tools_ids = {s.id for s in no_tools}
    assert "o1-preview" in no_tools_ids
    print(f"  ✓ test_list_models_filter_tools (with={len(result)}, without={len(no_tools)})")
    return True


def test_list_models_filter_min_context():
    """只列 context >= 100k"""
    result = list_models(min_context=100000)
    assert len(result) >= 3, f"expected >= 3 models with context >= 100k, got {len(result)}"
    for spec in result:
        assert spec.context_window >= 100000, f"{spec.id} has ctx={spec.context_window}"
    ids = {s.id for s in result}
    assert "gpt-4o" in ids
    assert "claude-3-5-sonnet" in ids
    print(f"  ✓ test_list_models_filter_min_context (found {len(result)} models with ctx>=100k)")
    return True


def test_list_models_filter_max_cost():
    """只列 input cost < 0.001"""
    result = list_models(max_cost=0.001)
    assert len(result) > 0
    for spec in result:
        assert spec.input_cost_per_1k < 0.001, f"{spec.id} has cost={spec.input_cost_per_1k}"
    ids = {s.id for s in result}
    assert "glm-4-flash" in ids, "glm-4-flash should be in cheap list"
    assert "mock" in ids, "mock (cost=0) should be in cheap list"
    # gpt-4o (0.0025) 不应出现
    assert "gpt-4o" not in ids
    print(f"  ✓ test_list_models_filter_max_cost (found {len(result)} cheap models)")
    return True


# =============================================================================
# calculate_max_tokens
# =============================================================================


def test_calculate_max_tokens_basic():
    """input=1000, requested=2000 → 2000(完全够用)"""
    result = calculate_max_tokens("deepseek-v3", input_tokens=1000, requested_output=2000)
    assert result == 2000, f"expected 2000, got {result}"
    print(f"  ✓ test_calculate_max_tokens_basic (returned {result})")
    return True


def test_calculate_max_tokens_truncated():
    """input 太大 → 返回剩余,带 warning"""
    # deepseek-v3 context=64000, input=60000, 剩 4000 raw, 3600 with 10% margin
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = calculate_max_tokens(
            "deepseek-v3", input_tokens=60000, requested_output=5000
        )
    # 3600 (min of 5000/3600/8000)
    assert result == 3600, f"expected 3600, got {result}"
    # 应该触发了 warning
    assert len(w) >= 1, "expected at least one warning"
    assert any("exceeds remaining context" in str(wi.message) for wi in w)
    print(f"  ✓ test_calculate_max_tokens_truncated (returned {result}, warning fired)")
    return True


def test_calculate_max_tokens_safety_margin():
    """safety_margin 生效(更大 margin → 更小 max_tokens)"""
    # o3-mini: context=200000, max_output=100000
    # input=100000, 剩 100000
    # 0.1: 100000 * 0.9 = 90000, min(90000, 100000) = 90000  ✓
    # 0.3: 100000 * 0.7 = 70000, min(70000, 100000) = 70000  ✓
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r_10 = calculate_max_tokens(
            "o3-mini", input_tokens=100000, requested_output=200000,
            safety_margin=0.1,
        )
        r_30 = calculate_max_tokens(
            "o3-mini", input_tokens=100000, requested_output=200000,
            safety_margin=0.3,
        )
    assert r_10 == 90000, f"r_10={r_10}, expected 90000 (100000 * 0.9)"
    assert r_30 == 70000, f"r_30={r_30}, expected 70000 (100000 * 0.7)"
    assert r_10 > r_30

    # 第二个对比:更大的 margin → 更小的有效 max_tokens
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r_50 = calculate_max_tokens(
            "o3-mini", input_tokens=100000, requested_output=200000,
            safety_margin=0.5,
        )
    assert r_50 == 50000, f"r_50={r_50}, expected 50000 (100000 * 0.5)"
    assert r_50 < r_30 < r_10
    print(
        f"  ✓ test_calculate_max_tokens_safety_margin "
        f"(o3-mini: 0.1→{r_10}, 0.3→{r_30}, 0.5→{r_50})"
    )
    return True


# =============================================================================
# estimate_cost
# =============================================================================


def test_estimate_cost():
    """deepseek-v3 1k in + 500 out → 总 cost > 0"""
    cost = estimate_cost("deepseek-v3", input_tokens=1000, output_tokens=500)
    assert cost["currency"] == "USD"
    # input = 1 * 0.00027 = 0.00027
    # output = 0.5 * 0.0011 = 0.00055
    # total = 0.00082
    assert cost["input_cost"] > 0
    assert cost["output_cost"] > 0
    assert cost["total_cost"] > 0
    expected_input = 0.00027
    expected_output = 0.00055
    assert abs(cost["input_cost"] - expected_input) < 1e-9, f"input_cost={cost['input_cost']}"
    assert abs(cost["output_cost"] - expected_output) < 1e-9, f"output_cost={cost['output_cost']}"
    assert abs(cost["total_cost"] - (expected_input + expected_output)) < 1e-9
    print(f"  ✓ test_estimate_cost (deepseek-v3 1k+500out: ${cost['total_cost']:.6f})")
    return True


# =============================================================================
# find_cheapest_for_context
# =============================================================================


def test_find_cheapest_for_context():
    """找 200k context 最便宜"""
    # 200k context 至少要能装下 200k token
    result = find_cheapest_for_context(required_context=200000)
    assert len(result) >= 1, "expected at least one model with 200k context"
    for spec in result:
        assert spec.context_window >= 200000

    # 验证排序:按 input_cost 升序
    costs = [s.input_cost_per_1k for s in result]
    assert costs == sorted(costs), f"not sorted ascending: {costs}"

    # gemini-1.5-flash 应该是非 mock 中最便宜的(0.000075),mock-smart 0 成本不算真实场景
    real_cheapest = [s for s in result if s.provider != "mock"]
    assert len(real_cheapest) >= 1
    assert real_cheapest[0].id == "gemini-1.5-flash"
    assert real_cheapest[0].input_cost_per_1k == 0.000075

    # 找 2M context 应该只有 gemini-1.5-pro / gemini-2.0-flash (mock-smart ctx=200k 不够)
    mega = find_cheapest_for_context(required_context=2_000_000)
    mega_ids = {s.id for s in mega}
    assert "gemini-1.5-pro" in mega_ids
    # 验证 mega 列表里没有 mock
    for s in mega:
        assert s.provider != "mock" or s.context_window >= 2_000_000
    print(
        f"  ✓ test_find_cheapest_for_context "
        f"(200k ctx: {len(result)} models, cheapest={result[0].id})"
    )
    return True


# =============================================================================
# 数据完整性
# =============================================================================


def test_count_models():
    """len(MODEL_DATABASE) >= 40"""
    n = len(MODEL_DATABASE)
    assert n >= 40, f"expected >= 40 models, got {n}"
    # 顺便 sanity check:每个 spec 都有 context_window > 0, costs >= 0
    for mid, spec in MODEL_DATABASE.items():
        assert spec.id == mid, f"key/id mismatch: {mid} vs {spec.id}"
        assert spec.context_window > 0, f"{mid} has context_window=0"
        assert spec.max_output > 0, f"{mid} has max_output=0"
        assert spec.max_output <= spec.context_window, (
            f"{mid} max_output ({spec.max_output}) > context_window ({spec.context_window})"
        )
        assert spec.input_cost_per_1k >= 0
        assert spec.output_cost_per_1k >= 0
    print(f"  ✓ test_count_models (database has {n} models, all valid)")
    return True


# =============================================================================
# runner
# =============================================================================


def run_all():
    tests = [
        test_get_model_spec_deepseek_v3,
        test_get_model_spec_gpt4o,
        test_get_model_spec_claude35,
        test_get_model_spec_unknown,
        test_list_models_filter_provider,
        test_list_models_filter_tools,
        test_list_models_filter_min_context,
        test_list_models_filter_max_cost,
        test_calculate_max_tokens_basic,
        test_calculate_max_tokens_truncated,
        test_calculate_max_tokens_safety_margin,
        test_estimate_cost,
        test_find_cheapest_for_context,
        test_count_models,
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
