"""P3 MultiModalFanout 多路并发聚合引擎测试。

验收标准对照:
- P3-1 单测: 3 路并发 2 成功 1 超时 → 返回 2 结果 + 失败证据
- P3-2 E2E: mock.mode=disabled 下无 key 返回 503 逐路证据
- P3-3: 任一路 mock 则 X-MOA-Mock 头标注 true
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from moa_gateway.multimodal_fanout import MultiModalFanout, RouteResult


# ---------------------------------------------------------------------------
# 受控 provider（真实对象、可控延迟 —— 验证引擎并发语义，不打外部 API）
# ---------------------------------------------------------------------------

class _FakeImageProvider:
    def __init__(self, delay: float, urls: list[str], fail: bool = False):
        self.delay = delay
        self.urls = urls
        self.fail = fail

    async def generate_image(self, prompt: str, size: str = "1024x1024", n: int = 1):
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("provider exploded")
        return list(self.urls)


def _fanout_with_routes(routes: dict[str, _FakeImageProvider]) -> MultiModalFanout:
    """构造一个 image 模态下按平台名注入受控 provider 的引擎。"""
    fo = MultiModalFanout()
    fo._mock_mode = "disabled"

    def _resolve(modality: str, platform_id: str):
        prov = routes.get(platform_id)
        if prov is None:
            return None, False, "no_key (mock.mode=disabled, set the platform API key)"
        return prov, False, ""

    fo._resolve_provider = _resolve  # type: ignore[method-assign]
    return fo


# ---------------------------------------------------------------------------
# P3-1: all 模式部分成功语义
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_all_mode_partial_success_with_timeout_evidence():
    """3 路并发: 2 成功 1 超时 → 2 结果 + 超时证据, 整体不失败。"""
    fo = _fanout_with_routes({
        "p-fast": _FakeImageProvider(0.01, ["http://img/a.png"]),
        "p-slow-ok": _FakeImageProvider(0.03, ["http://img/b.png"]),
        "p-hang": _FakeImageProvider(5.0, ["http://img/never.png"]),
    })
    result = await fo.execute(
        "image", ["p-fast", "p-slow-ok", "p-hang"], {"prompt": "x"},
        mode="all", per_route_timeout_s=0.5,
    )
    assert len(result.routes) == 3
    assert len(result.successes) == 2
    statuses = {r.platform: r.status for r in result.routes}
    assert statuses["p-fast"] == "success"
    assert statuses["p-slow-ok"] == "success"
    assert statuses["p-hang"] == "timeout"
    timeout_route = next(r for r in result.routes if r.status == "timeout")
    assert timeout_route.error and "0.5s" in timeout_route.error
    assert isinstance(result.primary, list) and len(result.primary) == 2
    assert result.any_mock is False


@pytest.mark.anyio
async def test_all_mode_failure_evidence():
    fo = _fanout_with_routes({
        "p-ok": _FakeImageProvider(0.01, ["http://img/a.png"]),
        "p-boom": _FakeImageProvider(0.01, [], fail=True),
    })
    result = await fo.execute("image", ["p-ok", "p-boom"], {"prompt": "x"}, mode="all")
    by_platform = {r.platform: r for r in result.routes}
    assert by_platform["p-ok"].status == "success"
    assert by_platform["p-boom"].status == "failed"
    assert "provider exploded" in (by_platform["p-boom"].error or "")
    assert len(result.successes) == 1


# ---------------------------------------------------------------------------
# fastest / best 模式
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_fastest_returns_first_success_and_cancels_rest():
    fo = _fanout_with_routes({
        "p-slow": _FakeImageProvider(2.0, ["http://img/slow.png"]),
        "p-fast": _FakeImageProvider(0.01, ["http://img/fast.png"]),
    })
    result = await fo.execute(
        "image", ["p-slow", "p-fast"], {"prompt": "x"},
        mode="fastest", per_route_timeout_s=5.0,
    )
    assert result.primary == {"urls": ["http://img/fast.png"]}
    statuses = {r.platform: r.status for r in result.routes}
    assert statuses["p-fast"] == "success"
    assert statuses["p-slow"] in ("cancelled", "success")  # 慢路被取消或恰好同时完成


@pytest.mark.anyio
async def test_best_prefers_real_over_mock_then_latency():
    fo = _fanout_with_routes({
        "p-mock-fast": _FakeImageProvider(0.01, ["http://img/mock.png"]),
        "p-real-slow": _FakeImageProvider(0.03, ["http://img/real.png"]),
    })
    # 手工把 p-mock-fast 标成 mock 路由以验证打分优先级
    orig = fo._resolve_provider

    def _resolve(modality, platform_id):
        prov, _, reason = orig(modality, platform_id)
        return prov, platform_id == "p-mock-fast", reason

    fo._resolve_provider = _resolve  # type: ignore[method-assign]
    result = await fo.execute(
        "image", ["p-mock-fast", "p-real-slow"], {"prompt": "x"}, mode="best"
    )
    # 真实 provider 即使更慢也胜出
    assert result.primary == {"urls": ["http://img/real.png"]}
    assert result.any_mock is True  # 成功路里有 mock 路 → 标注


# ---------------------------------------------------------------------------
# D6 策略: 无密钥路由
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_no_key_routes_recorded_as_evidence(monkeypatch):
    fo = MultiModalFanout()
    fo._mock_mode = "disabled"
    for var in ("ZHIPU_API_KEY", "WANX_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    result = await fo.execute("image", ["cogview", "wanx"], {"prompt": "x"}, mode="all")
    assert all(r.status == "no_key" for r in result.routes)
    assert all(r.error for r in result.routes)
    assert result.successes == []


@pytest.mark.anyio
async def test_explicit_mock_mode_uses_labeled_mock(monkeypatch):
    fo = MultiModalFanout()
    fo._mock_mode = "explicit"
    for var in ("ZHIPU_API_KEY", "WANX_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    result = await fo.execute("image", ["cogview"], {"prompt": "x"}, mode="all")
    assert len(result.successes) == 1
    assert result.successes[0].is_mock is True
    assert result.any_mock is True


def test_ineligible_modality_rejected():
    fo = MultiModalFanout()

    async def _run():
        with pytest.raises(ValueError):
            await fo.execute("music", ["minimax_music"], {}, mode="all")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# P3-2 E2E: 端点 + mock.mode=disabled 无 key → 503 逐路证据
# ---------------------------------------------------------------------------

@pytest.fixture
async def app_disabled_mock():
    from moa_gateway.config import Settings

    test_settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "TestPass123!",
            "jwt_secret": "mm-test-secret-long-enough-for-hs256-signing-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": ["test-key-123"],
        },
        mock={"mode": "disabled"},
    )
    with patch("moa_gateway.config.get_settings", return_value=test_settings):
        with patch("moa_gateway.config._settings", test_settings):
            from moa_gateway.server import create_app

            yield create_app()


@pytest.mark.anyio
async def test_endpoint_503_with_route_evidence_when_disabled_and_keyless(
    app_disabled_mock, monkeypatch
):
    for var in ("ZHIPU_API_KEY", "WANX_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    transport = ASGITransport(app=app_disabled_mock)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.post(
            "/v1/multimodal/execute",
            json={"modality": "image", "platforms": ["cogview", "wanx"], "prompt": "hi"},
            headers={"Authorization": "Bearer test-key-123"},
        )
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "no executable route" in str(detail)
    assert len(detail["routes"]) == 2
    assert all(r["status"] == "no_key" for r in detail["routes"])


@pytest.mark.anyio
async def test_endpoint_explicit_mock_sets_x_moa_mock_header(app_disabled_mock, monkeypatch):
    """同 app 但把 fanout 的 mock 策略切到 explicit, 验证 X-MOA-Mock 标注。"""
    for var in ("ZHIPU_API_KEY", "WANX_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    from moa_gateway import multimodal_fanout as mf

    orig = mf._fanout
    mf._fanout = None
    try:
        fo = mf.get_fanout()
        fo._mock_mode = "explicit"
        transport = ASGITransport(app=app_disabled_mock)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.post(
                "/v1/multimodal/execute",
                json={"modality": "image", "platforms": ["cogview"], "prompt": "hi"},
                headers={"Authorization": "Bearer test-key-123"},
            )
        assert resp.status_code == 200
        assert resp.headers.get("X-MOA-Mock", "").lower() == "true"
        body = resp.json()
        assert body["any_mock"] is True
        assert body["success_count"] == 1
    finally:
        mf._fanout = orig
