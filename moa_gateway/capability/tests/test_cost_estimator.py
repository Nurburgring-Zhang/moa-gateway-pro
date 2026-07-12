"""cost_estimator 真实测试(非 mock,全部 assert)"""
import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.cost_estimator import (
    Channel,
    CostEstimate,
    DEEPSEEK,
    GLM,
    MOONSHOT,
    QWEN,
    GPT_MINI,
    CLAUDE_HAIKU,
    CHANNEL_REGISTRY,
    PRESETS,
    estimate_moa_cost,
    dry_run_preset,
    compare_presets,
    format_report,
)


def test_estimate_single_channel():
    """1 channel, 1000 in + 500 out → ref cost = 1000/1000*input + 500/1000*output"""
    est = estimate_moa_cost(
        input_tokens=1000,
        output_tokens=500,
        channels=[DEEPSEEK],
        preset_name="single",
        include_fallback=False,
    )
    # ref cost = 0.0005 + 0.0005 = 0.001
    assert est.breakdown["ref:deepseek-v3"] == 0.001, (
        f"ref cost wrong: {est.breakdown['ref:deepseek-v3']}"
    )
    # aggregator uses first channel = deepseek; ref output = 500
    # agg_in = 500/1000 * 0.0005 = 0.00025; agg_out = 500/1000 * 0.001 = 0.0005
    expected_agg = 0.00025 + 0.0005
    assert abs(est.breakdown["aggregator:deepseek-v3"] - expected_agg) < 1e-9
    # multiplier 1.0 (no fallback)
    assert est.multiplier == 1.0
    # total = (0.001 + 0.00075) * 1.0
    assert abs(est.total_cost_usd - 0.00175) < 1e-9
    print(f"  ✓ test_estimate_single_channel total=${est.total_cost_usd:.6f}")


def test_estimate_multiple_channels():
    """3 channels, cost 是各 channel 之和"""
    channels = [DEEPSEEK, GLM, QWEN]
    est = estimate_moa_cost(
        input_tokens=1000,
        output_tokens=500,
        channels=channels,
        preset_name="multi",
        include_fallback=False,
    )
    # each ref cost:
    # deepseek: 0.0005 + 0.0005 = 0.001
    # glm: 0.0007 + 0.00035 = 0.00105
    # qwen: 0.0004 + 0.0006 = 0.001
    expected_refs = 0.001 + 0.00105 + 0.001
    actual_refs = sum(v for k, v in est.breakdown.items() if k.startswith("ref:"))
    assert abs(actual_refs - expected_refs) < 1e-9, (
        f"ref sum {actual_refs} != {expected_refs}"
    )
    # aggregator uses channels[0] = deepseek; ref outputs total = 500 * 3 = 1500
    expected_agg = (1500 / 1000.0) * 0.0005 + (500 / 1000.0) * 0.001
    assert abs(est.breakdown["aggregator:deepseek-v3"] - expected_agg) < 1e-9
    print(f"  ✓ test_estimate_multiple_channels total=${est.total_cost_usd:.6f}")

def test_estimate_with_fallback_multiplier():
    """include_fallback=True → multiplier=1.5"""
    est_no = estimate_moa_cost(
        input_tokens=1000, output_tokens=500,
        channels=[DEEPSEEK], include_fallback=False,
    )
    est_yes = estimate_moa_cost(
        input_tokens=1000, output_tokens=500,
        channels=[DEEPSEEK], include_fallback=True,
    )
    assert est_no.multiplier == 1.0
    assert est_yes.multiplier == 1.5
    # total cost ratio = 1.5
    assert abs(est_yes.total_cost_usd / est_no.total_cost_usd - 1.5) < 1e-9
    # confidence 也应该下降
    assert est_yes.confidence <= est_no.confidence
    print(f"  ✓ test_estimate_with_fallback_multiplier ({est_no.total_cost_usd:.6f} → {est_yes.total_cost_usd:.6f})")

def test_estimate_zero_tokens():
    """0 in + 0 out → cost=0"""
    est = estimate_moa_cost(
        input_tokens=0, output_tokens=0,
        channels=[DEEPSEEK, GLM],
        include_fallback=True,
    )
    assert est.total_cost_usd == 0.0
    assert all(v == 0.0 for v in est.breakdown.values())
    print("  ✓ test_estimate_zero_tokens")

def test_estimate_with_retry():
    """包含重试 → cost * retry_factor"""
    est_base = estimate_moa_cost(
        input_tokens=1000, output_tokens=500,
        channels=[DEEPSEEK], include_fallback=True, retry_factor=1.0,
    )
    est_retry = estimate_moa_cost(
        input_tokens=1000, output_tokens=500,
        channels=[DEEPSEEK], include_fallback=True, retry_factor=2.0,
    )
    # multiplier: 1.5 vs 1.5*2.0=3.0
    assert est_base.multiplier == 1.5
    assert est_retry.multiplier == 3.0
    # total ratio = 2.0
    assert abs(est_retry.total_cost_usd / est_base.total_cost_usd - 2.0) < 1e-9
    print(f"  ✓ test_estimate_with_retry (multiplier 1.5 → 3.0)")

def test_dry_run_preset_balanced():
    """用标准 balanced preset 估算"""
    est = dry_run_preset(PRESETS["balanced"], input_tokens=1000, output_tokens=500)
    # balanced: 2 unique ref × 4 copies = 8 ref calls + 1 aggregator
    assert est.multiplier == 1.5
    # 应该含 deepseek ref + glm ref + aggregator(deepseek)
    assert "ref:deepseek-v3 (×4)" in est.breakdown
    assert "ref:glm-4-plus (×4)" in est.breakdown
    assert "aggregator:deepseek-v3" in est.breakdown
    # total > 0 且 confidence 合理
    assert est.total_cost_usd > 0
    assert 0 < est.confidence <= 1.0
    # 验算:deepseek ref 单价 0.001/次 × 4 + glm ref 0.00105/次 × 4
    # + aggregator (deepseek 收 8*500=4000 in + 500 out)
    expected_deepseek_refs = 4 * (0.0005 + 0.0005)
    expected_glm_refs = 4 * (0.0007 + 0.00035)
    expected_agg = (4000 / 1000.0) * 0.0005 + (500 / 1000.0) * 0.001
    assert abs(est.breakdown["ref:deepseek-v3 (×4)"] - expected_deepseek_refs) < 1e-9
    assert abs(est.breakdown["ref:glm-4-plus (×4)"] - expected_glm_refs) < 1e-9
    assert abs(est.breakdown["aggregator:deepseek-v3"] - expected_agg) < 1e-9
    print(f"  ✓ test_dry_run_preset_balanced total=${est.total_cost_usd:.6f}")

def test_dry_run_preset_fast():
    """fast preset cost < balanced"""
    fast = dry_run_preset(PRESETS["fast"], input_tokens=1000, output_tokens=500)
    balanced = dry_run_preset(PRESETS["balanced"], input_tokens=1000, output_tokens=500)
    assert fast.total_cost_usd < balanced.total_cost_usd, (
        f"fast ${fast.total_cost_usd} should < balanced ${balanced.total_cost_usd}"
    )
    # fast 用 gpt-4o-mini(最便宜)
    assert "ref:gpt-4o-mini" in str(fast.breakdown)
    print(f"  ✓ test_dry_run_preset_fast (fast ${fast.total_cost_usd:.6f} < balanced ${balanced.total_cost_usd:.6f})")

def test_compare_presets_sorted():
    """compare_presets 返回 sorted by cost ascending"""
    presets = [PRESETS["premium"], PRESETS["fast"], PRESETS["balanced"]]
    results = compare_presets(presets, input_tokens=1000, output_tokens=500)
    # 验证 ascending
    for i in range(len(results) - 1):
        assert results[i].total_cost_usd <= results[i + 1].total_cost_usd, (
            f"not sorted at index {i}: {results[i].total_cost_usd} > {results[i+1].total_cost_usd}"
        )
    # fast 应该是最便宜的(由上一测试已确认 fast<balanced,这里再含 premium)
    assert results[0].total_cost_usd == min(r.total_cost_usd for r in results)
    assert results[-1].total_cost_usd == max(r.total_cost_usd for r in results)
    print(f"  ✓ test_compare_presets_sorted (min=${results[0].total_cost_usd:.6f}, max=${results[-1].total_cost_usd:.6f})")

def test_format_report_contains_total():
    """format_report 含 'Total'"""
    est = dry_run_preset(PRESETS["balanced"])
    report = format_report(est)
    assert "Total" in report
    assert "multiplier" in report
    assert "Confidence" in report
    assert "Breakdown" in report
    assert "===" in report
    print(f"  ✓ test_format_report_contains_total (len={len(report)})")

def test_format_report_contains_breakdown():
    """format_report 含 breakdown 内容"""
    est = dry_run_preset(PRESETS["premium"])
    report = format_report(est)
    # 应含所有 breakdown key
    for k in est.breakdown.keys():
        assert k in report, f"breakdown key '{k}' missing in report"
    # 应含 preset 描述
    assert "premium" in est.notes[0] or "premium" in str(est.breakdown)
    # 应有 $ 符号
    assert "$" in report
    print(f"  ✓ test_format_report_contains_breakdown (len={len(report)})")

def test_estimate_high_reliability_channel():
    """reliability=1.0 vs 0.5 → 高 reliability 给出更高 confidence"""
    high = Channel("high", 0.001, 0.002, 500, 1.0)
    low = Channel("low", 0.001, 0.002, 500, 0.5)
    est_high = estimate_moa_cost(
        1000, 500, [high, high, high], include_fallback=False,
    )
    est_low = estimate_moa_cost(
        1000, 500, [low, low, low], include_fallback=False,
    )
    assert est_high.confidence > est_low.confidence, (
        f"high {est_high.confidence} should > low {est_low.confidence}"
    )
    # cost 应相同(价格相同,只 reliability 不同)
    assert abs(est_high.total_cost_usd - est_low.total_cost_usd) < 1e-9
    print(f"  ✓ test_estimate_high_reliability_channel ({est_high.confidence:.3f} > {est_low.confidence:.3f})")

def test_realistic_moa_estimate():
    """真实场景:8 refs × 200 in + 1500 out,估算 cost 在 $0.01-$0.10 范围"""
    # 8 个 deepseek refs(模拟 balanced preset 的 2 unique × 4 copies)
    channels = [DEEPSEEK] * 8
    est = estimate_moa_cost(
        input_tokens=200,
        output_tokens=1500,
        channels=channels,
        preset_name="realistic",
        include_fallback=True,
    )
    # 验算:每 ref = 200/1000*0.0005 + 1500/1000*0.001 = 0.0001 + 0.0015 = 0.0016
    # 8 refs = 0.0128
    # aggregator(deepseek): input = 8 * 1500 = 12000; 12000/1000*0.0005 + 1500/1000*0.001
    #                     = 0.006 + 0.0015 = 0.0075
    # raw = 0.0128 + 0.0075 = 0.0203
    # × 1.5 fallback = 0.03045
    expected_total = 0.0203 * 1.5
    assert abs(est.total_cost_usd - expected_total) < 1e-6, (
        f"realistic total {est.total_cost_usd} != {expected_total}"
    )
    assert 0.01 <= est.total_cost_usd <= 0.10, (
        f"realistic cost ${est.total_cost_usd} out of range [0.01, 0.10]"
    )
    # confidence 应该是高(deepseek reliability 0.95,带 fallback × 0.9)
    # base = 0.95*0.6 + 0.95*0.4 = 0.95; ×0.9 = 0.855
    assert 0.8 <= est.confidence <= 1.0
    # 8 refs, 1 agg → 9 entries
    assert sum(1 for k in est.breakdown if k.startswith("ref:")) == 1  # deepseek 聚合
    assert sum(1 for k in est.breakdown if k.startswith("aggregator:")) == 1
    # 但 ref:deepseek-v3 应该是 8 倍
    expected_ref_total = 8 * 0.0016
    assert abs(est.breakdown["ref:deepseek-v3"] - expected_ref_total) < 1e-9
    print(f"  ✓ test_realistic_moa_estimate total=${est.total_cost_usd:.6f}")

def test_channel_validation():
    """Channel 验证 reliability/cost 范围"""
    try:
        Channel("bad", 0.001, 0.002, 500, 1.5)
        assert False, "should have raised"
    except ValueError:
        pass
    try:
        Channel("bad2", -0.001, 0.002, 500, 0.9)
        assert False, "should have raised"
    except ValueError:
        pass
    print("  ✓ test_channel_validation")

def test_cost_estimate_to_dict():
    """CostEstimate.to_dict 返回可序列化 dict"""
    est = estimate_moa_cost(
        1000, 500, [DEEPSEEK, GLM], include_fallback=True,
    )
    d = est.to_dict()
    assert isinstance(d, dict)
    assert "total_cost_usd" in d
    assert "breakdown" in d
    assert "multiplier" in d
    assert "confidence" in d
    assert "notes" in d
    assert d["multiplier"] == 1.5
    # 数值应是 round 后的(可序列化)
    assert isinstance(d["total_cost_usd"], float)
    assert isinstance(d["breakdown"], dict)
    print(f"  ✓ test_cost_estimate_to_dict keys={list(d.keys())}")

# ============ runner ============

def main() -> int:
    tests = [
        test_estimate_single_channel,
        test_estimate_multiple_channels,
        test_estimate_with_fallback_multiplier,
        test_estimate_zero_tokens,
        test_estimate_with_retry,
        test_dry_run_preset_balanced,
        test_dry_run_preset_fast,
        test_compare_presets_sorted,
        test_format_report_contains_total,
        test_format_report_contains_breakdown,
        test_estimate_high_reliability_channel,
        test_realistic_moa_estimate,
        test_channel_validation,
        test_cost_estimate_to_dict,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"  ✗ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: EXCEPTION {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(tests)} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
