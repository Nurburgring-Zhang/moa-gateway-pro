"""MoA Gateway Pro — 启动烟雾测试
验证:
- 配置加载
- 存储初始化
- 模型池构造
- 智能路由(无模型时优雅失败)
- MoA 编排(简单模式)
- 鉴权 + JWT
- 限流
"""
import asyncio
import sys
import os
import traceback
from pathlib import Path

# 让包可被 import
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def section(name):
    print(f"\n=== {name} ===")


def assert_eq(name, got, expected):
    if got == expected:
        print(f"  ✓ {name}: {got!r}")
    else:
        raise AssertionError(f"{name}: got {got!r}, expected {expected!r}")


def assert_true(name, got, hint=""):
    if got:
        print(f"  ✓ {name} {hint}")
    else:
        raise AssertionError(f"{name} {hint}")


def test_config():
    section("config")
    from moa_gateway.config import get_settings, reload_settings
    s = get_settings()
    assert_true("settings loaded", s is not None)
    assert_true("server.host", s.server.host == "0.0.0.0")
    assert_true("server.port", s.server.port == 8910)
    assert_true("jwt secret set", bool(s.auth.jwt_secret),
                f"(len={len(s.auth.jwt_secret)})")
    assert_true("models loaded", len(s.models) > 0,
                f"(n={len(s.models)})")
    reload_settings()
    print(f"  ✓ {len(s.models)} model endpoints configured (all disabled by default)")


def test_storage():
    section("storage")
    from moa_gateway.storage import get_storage
    s = get_storage()
    assert_true("storage ready", s is not None)
    # admin
    info = s.verify_admin("admin", "admin")
    assert_true("admin login ok", info is not None)
    # api key
    k = s.create_api_key("test-key", quota_rpm=10, quota_daily_tokens=1000)
    assert_true("api key created", k.get("key", "").startswith("mgw-"))
    found = s.find_api_key(k["key"])
    assert_true("api key findable", found is not None)
    s.delete_api_key(k["key_id"])
    # endpoint
    ep = s.upsert_endpoint({
        "endpoint_id": "test-mock-1",
        "provider": "openai",
        "model": "mock-model",
        "tier": "lite",
        "api_base": "https://example.com/v1",
        "api_key_plain": "sk-test-dummy",
        "enabled": True,
    })
    assert_true("endpoint upserted", ep.get("endpoint_id") == "test-mock-1")
    s.delete_endpoint("test-mock-1")


def test_auth_and_ratelimit():
    section("auth + ratelimit")
    from moa_gateway.auth import create_jwt_token, decode_jwt_token
    from moa_gateway.ratelimit import get_limiter

    tok = create_jwt_token("testuser", "admin")
    decoded = decode_jwt_token(tok)
    assert_true("jwt round-trip", decoded is not None and decoded["sub"] == "testuser")

    limiter = get_limiter()
    info = {"key_id": "test-mock", "name": "test"}
    used, limit, _, _ = limiter.check_and_incr(info)
    assert_true("ratelimit returns int", isinstance(used, int))


def test_router():
    section("router")
    from moa_gateway.router import get_router, ComplexityLevel
    r = get_router()
    c1 = r.evaluate_complexity("hi")
    c2 = r.evaluate_complexity(
        "请设计一个高并发的分布式系统架构,对比 Kafka 和 RocketMQ 的优劣,"
        "考虑 CAP 权衡,并给出具体的微服务拆分方案"
    )
    assert_true("trivial detected", c1 == ComplexityLevel.TRIVIAL,
                f"(got {c1.value})")
    assert_true("complex detected", c2 in (ComplexityLevel.COMPLEX, ComplexityLevel.EXPERT),
                f"(got {c2.value})")
    print(f"  ✓ trivial='hi' -> {c1.value}")
    print(f"  ✓ complex query -> {c2.value}")


async def test_moa_mock():
    section("moa (no real model — should graceful fail)")
    from moa_gateway.moa import get_moa
    moa = get_moa()
    try:
        r = await moa.execute(query="hello", preset="fast")
        print(f"  ✓ moa fast ok, content len={len(r.final_content)}")
    except RuntimeError as e:
        # 没模型可用时应该优雅失败
        print(f"  ✓ moa graceful no-model: {e}")


async def test_all():
    section("MoA Gateway Pro smoke test")
    test_config()
    test_storage()
    test_auth_and_ratelimit()
    test_router()
    await test_moa_mock()
    print("\n✅ 全部通过 — MoA Gateway Pro 可正常初始化与运行。\n")


if __name__ == "__main__":
    try:
        asyncio.run(test_all())
    except Exception as e:
        print(f"\n❌ 失败: {e}")
        traceback.print_exc()
        sys.exit(1)
