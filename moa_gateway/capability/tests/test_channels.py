"""channels.py 测试 — 端到端验证三通道 fallback 抽象 (R-23)

覆盖:
- CH1 成功直接返回
- CH1 失败 → CH2 重试
- CH1+CH2 失败 → CH3
- 全部失败抛 ChannelError
- 每个 channel 单独 disabled 时 chain 跳过
- latency_ms 正确
- fallback_path 列表
- 错误分类 (4 类 R-24)
- 并发 chain
- 空 query / 长 query
- ChannelResult 字段完整性
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.channels import (
    APIChannel,
    ChannelChain,
    ChannelError,
    ChannelResult,
    ChannelType,
    CLIChannel,
    SubagentChannel,
    classify_error,
)

# ============ Helpers ============

def _ok_chain(fail_ch1=False, fail_ch2=False, fail_ch3=False, fail_kind="cli"):
    """构造带可控失败位的 chain — 默认全成功 (fail_kind 仅在对应通道要失败时生效)。"""
    c1 = SubagentChannel(fail_rate=1.0 if fail_ch1 else 0.0, sleep_ms=2)
    c2 = CLIChannel(fail_kind=fail_kind if fail_ch2 else None, sleep_ms=2)
    c3 = APIChannel(fail_kind=fail_kind if fail_ch3 else None, sleep_ms=2)
    return ChannelChain([c1, c2, c3])


# ============ ChannelType ============

def test_channel_type_values():
    assert ChannelType.SUBAGENT.value == "ch1"
    assert ChannelType.CLI.value == "ch2"
    assert ChannelType.API.value == "ch3"
    assert ChannelType.SUBAGENT.label == "subagent"
    assert ChannelType.CLI.label == "cli"
    assert ChannelType.API.label == "api"
    # str Enum 兼容
    assert ChannelType.SUBAGENT == "ch1"
    print("  ✓ test_channel_type_values")
    assert True


# ============ ChannelResult ============

def test_channel_result_field_completeness():
    r = ChannelResult(
        channel=ChannelType.SUBAGENT,
        success=True,
        output="hello",
        latency_ms=42,
        error=None,
    )
    assert r.channel == ChannelType.SUBAGENT
    assert r.success is True
    assert r.output == "hello"
    assert r.latency_ms == 42
    assert r.error is None
    # NamedTuple 字段访问 + 索引
    assert r[0] == ChannelType.SUBAGENT
    assert r[1] is True
    assert r[2] == "hello"
    assert r[3] == 42
    assert r[4] is None
    # _fields 完整
    assert ChannelResult._fields == ("channel", "success", "output", "latency_ms", "error")
    print("  ✓ test_channel_result_field_completeness")
    assert True


# ============ CH1 SubagentChannel ============

async def test_ch1_subagent_success():
    ch = SubagentChannel()
    r = await ch.execute("hello world")
    assert r.channel == ChannelType.SUBAGENT
    assert r.success is True
    assert r.error is None
    assert r.output  # 非空
    assert r.latency_ms >= 0
    print("  ✓ test_ch1_subagent_success")
    assert True


async def test_ch1_subagent_failure_when_forced():
    ch = SubagentChannel(fail_rate=1.0, sleep_ms=1)
    r = await ch.execute("will fail")
    assert r.success is False
    assert r.error is not None
    print("  ✓ test_ch1_subagent_failure_when_forced")
    assert True


def test_ch1_subagent_sync_runner():
    asyncio.run(test_ch1_subagent_success())
    asyncio.run(test_ch1_subagent_failure_when_forced())


# ============ CH2 CLIChannel ============

async def test_ch2_cli_success():
    ch = CLIChannel()
    r = await ch.execute("ping")
    assert r.channel == ChannelType.CLI
    assert r.success is True
    assert r.output.startswith("[cli]")
    print("  ✓ test_ch2_cli_success")
    assert True


async def test_ch2_cli_auth_failure():
    ch = CLIChannel(fail_kind="auth")
    r = await ch.execute("ping")
    assert r.success is False
    assert r.error is not None
    assert "auth" in r.error
    print("  ✓ test_ch2_cli_auth_failure")
    assert True


async def test_ch2_cli_empty_output():
    ch = CLIChannel(fail_empty_output=True)
    r = await ch.execute("ping")
    assert r.success is False
    assert "empty" in (r.error or "")
    print("  ✓ test_ch2_cli_empty_output")
    assert True


def test_ch2_cli_sync_runner():
    asyncio.run(test_ch2_cli_success())
    asyncio.run(test_ch2_cli_auth_failure())
    asyncio.run(test_ch2_cli_empty_output())


# ============ CH3 APIChannel ============

async def test_ch3_api_success():
    ch = APIChannel()
    r = await ch.execute("ping")
    assert r.channel == ChannelType.API
    assert r.success is True
    assert r.output.startswith("[api]")
    print("  ✓ test_ch3_api_success")
    assert True


async def test_ch3_api_missing_key_raises_permission():
    ch = APIChannel(api_key_env="DEFINITELY_NOT_SET_XYZ_12345")
    r = await ch.execute("ping")
    assert r.success is False
    assert "auth" in (r.error or "") or "permission" in (r.error or "").lower()
    print("  ✓ test_ch3_api_missing_key_raises_permission")
    assert True


def test_ch3_api_sync_runner():
    asyncio.run(test_ch3_api_success())
    asyncio.run(test_ch3_api_missing_key_raises_permission())


# ============ Chain — happy path / fallback / all fail ============

async def test_chain_ch1_success_returns_directly():
    chain = _ok_chain()  # CH1 成功
    out = await chain.execute("hello")
    assert out["channel"] == ChannelType.SUBAGENT
    assert out["result"].success is True
    assert out["fallback_path"] == [ChannelType.SUBAGENT]
    assert len(out["attempts"]) == 1
    print("  ✓ test_chain_ch1_success_returns_directly")
    assert True


async def test_chain_ch1_fail_falls_back_to_ch2():
    chain = _ok_chain(fail_ch1=True)
    out = await chain.execute("hello")
    assert out["channel"] == ChannelType.CLI
    assert out["fallback_path"] == [ChannelType.SUBAGENT, ChannelType.CLI]
    assert len(out["attempts"]) == 2
    assert out["attempts"][0].success is False
    assert out["attempts"][1].success is True
    print("  ✓ test_chain_ch1_fail_falls_back_to_ch2")
    assert True


async def test_chain_ch1_ch2_fail_falls_back_to_ch3():
    chain = _ok_chain(fail_ch1=True, fail_ch2=True)
    out = await chain.execute("hello")
    assert out["channel"] == ChannelType.API
    assert out["fallback_path"] == [ChannelType.SUBAGENT, ChannelType.CLI, ChannelType.API]
    assert len(out["attempts"]) == 3
    print("  ✓ test_chain_ch1_ch2_fail_falls_back_to_ch3")
    assert True


async def test_chain_all_fail_raises_channel_error():
    chain = _ok_chain(fail_ch1=True, fail_ch2=True, fail_ch3=True, fail_kind="cli")
    try:
        await chain.execute("hello")
        raised = False
    except ChannelError as exc:
        raised = True
        assert len(exc.attempts) == 3
        assert all(not a.success for a in exc.attempts)
        d = exc.to_dict()
        assert "attempts" in d and len(d["attempts"]) == 3
    assert raised, "ChannelError should have been raised"
    print("  ✓ test_chain_all_fail_raises_channel_error")
    assert True


def test_chain_scenarios_sync_runner():
    asyncio.run(test_chain_ch1_success_returns_directly())
    asyncio.run(test_chain_ch1_fail_falls_back_to_ch2())
    asyncio.run(test_chain_ch1_ch2_fail_falls_back_to_ch3())
    asyncio.run(test_chain_all_fail_raises_channel_error())


# ============ Chain — disable / enable ============

async def test_chain_disable_ch1_skips_to_ch2():
    chain = _ok_chain()
    chain.set_enabled(ChannelType.SUBAGENT, False)
    out = await chain.execute("hi")
    assert out["channel"] == ChannelType.CLI
    assert out["fallback_path"] == [ChannelType.CLI]
    print("  ✓ test_chain_disable_ch1_skips_to_ch2")
    assert True


async def test_chain_disable_ch2_skips_to_ch3():
    chain = _ok_chain()
    chain.set_enabled(ChannelType.CLI, False)
    out = await chain.execute("hi")
    assert out["channel"] == ChannelType.SUBAGENT  # CH1 仍成功
    assert ChannelType.CLI not in out["fallback_path"]
    print("  ✓ test_chain_disable_ch2_skips_to_ch3")
    assert True


async def test_chain_disable_all_fails():
    chain = _ok_chain()
    for t in (ChannelType.SUBAGENT, ChannelType.CLI, ChannelType.API):
        chain.set_enabled(t, False)
    try:
        await chain.execute("hi")
        raised = False
    except ChannelError:
        raised = True
    assert raised
    print("  ✓ test_chain_disable_all_fails")
    assert True


def test_chain_disable_sync_runner():
    asyncio.run(test_chain_disable_ch1_skips_to_ch2())
    asyncio.run(test_chain_disable_ch2_skips_to_ch3())
    asyncio.run(test_chain_disable_all_fails())


# ============ latency_ms ============

async def test_chain_latency_positive_and_monotonic():
    chain = _ok_chain()  # CH1 成功
    t0 = time.perf_counter()
    out = await chain.execute("hi")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert out["result"].latency_ms >= 0
    # 实测 wall time 应该 ≥ 报告的 latency
    assert out["result"].latency_ms <= elapsed_ms + 5
    print("  ✓ test_chain_latency_positive_and_monotonic")
    assert True


async def test_chain_latency_aggregates():
    chain = _ok_chain(fail_ch1=True, fail_ch2=False)
    out = await chain.execute("hi")
    assert len(out["attempts"]) == 2
    for a in out["attempts"]:
        assert a.latency_ms >= 0
    print("  ✓ test_chain_latency_aggregates")
    assert True


def test_chain_latency_sync_runner():
    asyncio.run(test_chain_latency_positive_and_monotonic())
    asyncio.run(test_chain_latency_aggregates())


# ============ fallback_path ============

async def test_chain_fallback_path_ch3_only():
    chain = ChannelChain([
        SubagentChannel(enabled=False),
        CLIChannel(enabled=False),
        APIChannel(),
    ])
    out = await chain.execute("hi")
    assert out["fallback_path"] == [ChannelType.API]
    print("  ✓ test_chain_fallback_path_ch3_only")
    assert True


def test_chain_fallback_path_sync_runner():
    asyncio.run(test_chain_fallback_path_ch3_only())


# ============ classify_error (R-24) ============

def test_classify_error_auth_permission_error():
    assert classify_error(PermissionError("denied")) == "auth"
    print("  ✓ test_classify_error_auth_permission_error")
    assert True


def test_classify_error_auth_message_401():
    e = RuntimeError("HTTP 401 unauthorized")
    assert classify_error(e) == "auth"
    print("  ✓ test_classify_error_auth_message_401")
    assert True


def test_classify_error_timeout():
    assert classify_error(TimeoutError("took too long")) == "timeout"
    print("  ✓ test_classify_error_timeout")
    assert True


def test_classify_error_asyncio_timeout():
    assert classify_error(asyncio.TimeoutError()) == "timeout"
    print("  ✓ test_classify_error_asyncio_timeout")
    assert True


def test_classify_error_empty_value_error():
    e = ValueError("empty response from cli")
    assert classify_error(e) == "empty"
    print("  ✓ test_classify_error_empty_value_error")
    assert True


def test_classify_error_cli_fallback():
    e = RuntimeError("some random cli bug")
    assert classify_error(e) == "cli"
    print("  ✓ test_classify_error_cli_fallback")
    assert True


def test_classify_error_none():
    assert classify_error(None) == "cli"  # 兜底
    print("  ✓ test_classify_error_none")
    assert True


# ============ concurrency ============

async def test_chain_concurrent_multiple():
    chains = [_ok_chain() for _ in range(5)]
    results = await asyncio.gather(*(c.execute(f"q{i}") for i, c in enumerate(chains)))
    assert len(results) == 5
    for r in results:
        assert r["channel"] == ChannelType.SUBAGENT
        assert r["result"].success is True
    print("  ✓ test_chain_concurrent_multiple")
    assert True


async def test_chain_concurrent_with_failures_mixed():
    """混合并发:一些 chain 触发 fallback,一些直接成功。"""
    chains = [
        _ok_chain(),                          # CH1 ok
        _ok_chain(fail_ch1=True),             # → CH2
        _ok_chain(fail_ch1=True, fail_ch2=True),  # → CH3
    ]
    results = await asyncio.gather(*(c.execute("q") for c in chains))
    assert results[0]["channel"] == ChannelType.SUBAGENT
    assert results[1]["channel"] == ChannelType.CLI
    assert results[2]["channel"] == ChannelType.API
    print("  ✓ test_chain_concurrent_with_failures_mixed")
    assert True


def test_chain_concurrent_sync_runner():
    asyncio.run(test_chain_concurrent_multiple())
    asyncio.run(test_chain_concurrent_with_failures_mixed())


# ============ query edge cases ============

async def test_chain_empty_query():
    chain = _ok_chain()
    out = await chain.execute("")
    # CH1 仍能给出 "(empty query)" 文本 → 成功
    assert out["channel"] == ChannelType.SUBAGENT
    assert out["result"].success is True
    print("  ✓ test_chain_empty_query")
    assert True


async def test_chain_long_query():
    long_q = "x" * 100_000
    chain = _ok_chain()
    out = await chain.execute(long_q)
    assert out["result"].success is True
    print("  ✓ test_chain_long_query")
    assert True


async def test_chain_unicode_query():
    chain = _ok_chain()
    out = await chain.execute("你好世界 — 测试中文 + emoji 🦀")
    assert out["result"].success is True
    print("  ✓ test_chain_unicode_query")
    assert True


def test_chain_query_sync_runner():
    asyncio.run(test_chain_empty_query())
    asyncio.run(test_chain_long_query())
    asyncio.run(test_chain_unicode_query())


# ============ execute_safe ============

async def test_chain_execute_safe_returns_dict_on_fail():
    chain = _ok_chain(fail_ch1=True, fail_ch2=True, fail_ch3=True, fail_kind="cli")
    out = await chain.execute_safe("hi")
    assert out["channel"] is None
    assert out["result"] is None
    assert "error" in out
    assert len(out["attempts"]) == 3
    print("  ✓ test_chain_execute_safe_returns_dict_on_fail")
    assert True


async def test_chain_execute_safe_returns_normal_on_success():
    chain = _ok_chain()
    out = await chain.execute_safe("hi")
    assert "error" not in out
    assert out["channel"] == ChannelType.SUBAGENT
    print("  ✓ test_chain_execute_safe_returns_normal_on_success")
    assert True


def test_chain_execute_safe_sync_runner():
    asyncio.run(test_chain_execute_safe_returns_dict_on_fail())
    asyncio.run(test_chain_execute_safe_returns_normal_on_success())


# ============ runner ============

def _run_all():
    tests: list = [
        test_channel_type_values,
        test_channel_result_field_completeness,
        test_ch1_subagent_sync_runner,
        test_ch2_cli_sync_runner,
        test_ch3_api_sync_runner,
        test_chain_scenarios_sync_runner,
        test_chain_disable_sync_runner,
        test_chain_latency_sync_runner,
        test_chain_fallback_path_sync_runner,
        test_classify_error_auth_permission_error,
        test_classify_error_auth_message_401,
        test_classify_error_timeout,
        test_classify_error_asyncio_timeout,
        test_classify_error_empty_value_error,
        test_classify_error_cli_fallback,
        test_classify_error_none,
        test_chain_concurrent_sync_runner,
        test_chain_query_sync_runner,
        test_chain_execute_safe_sync_runner,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
    total = passed + failed
    print(f"\n{'='*60}")
    print(f"RESULT: {passed}/{total} pass" + (f"  ({failed} failed)" if failed else ""))
    print(f"{'='*60}")
    return failed == 0


if __name__ == "__main__":
    import sys
    ok = _run_all()
    sys.exit(0 if ok else 1)
