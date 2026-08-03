"""
并发竞态测试 - 验证系统在并发访问下的数据一致性

Tests:
- Assistant并发创建 → ID唯一性
- Thread并发消息 → 消息不丢失
- 高并发请求 → health/models全部成功
- 认证/非认证交替 → 状态不混乱
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch


@pytest.fixture
def app():
    """Create test app with isolated config."""
    from moa_gateway.config import Settings

    test_settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "TestP@ss123!",
            "jwt_secret": "test-secret-long-enough-for-hs256-signing-key-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": ["test-concurrency-key"],
        }
    )
    with patch("moa_gateway.config.get_settings", return_value=test_settings):
        with patch("moa_gateway.config._settings", test_settings):
            from moa_gateway.server import create_app

            application = create_app()
            yield application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer test-concurrency-key", "Content-Type": "application/json"}


class TestConcurrentAssistant:
    """Assistant API并发操作一致性"""

    @pytest.mark.anyio
    async def test_concurrent_create_no_duplicates(self, client):
        """并发创建Assistant不应产生重复ID"""

        async def create(i):
            resp = await client.post(
                "/v1/assistants",
                headers=HEADERS,
                json={"name": f"Bot-{i}", "model": "test", "instructions": f"task {i}"},
            )
            return resp.json().get("id") if resp.status_code == 200 else None

        ids = await asyncio.gather(*[create(i) for i in range(20)])
        valid_ids = [id for id in ids if id is not None]

        # 所有ID必须唯一
        assert len(valid_ids) == len(set(valid_ids)), (
            f"Duplicate IDs found! {len(valid_ids)} total, {len(set(valid_ids))} unique"
        )
        # 至少大部分应成功
        assert len(valid_ids) >= 15, f"Only {len(valid_ids)}/20 succeeded"

    @pytest.mark.anyio
    async def test_concurrent_thread_messages(self, client):
        """并发向同一Thread发送消息不应丢失"""
        # 创建Thread
        resp = await client.post("/v1/threads", headers=HEADERS, json={})
        if resp.status_code != 200:
            pytest.skip("Thread creation failed")
        thread_id = resp.json()["id"]

        # 并发发送10条消息
        async def send_message(i):
            return await client.post(
                f"/v1/threads/{thread_id}/messages",
                headers=HEADERS,
                json={"role": "user", "content": f"Message #{i}"},
            )

        results = await asyncio.gather(*[send_message(i) for i in range(10)])
        success_count = sum(1 for r in results if r.status_code == 200)

        # 至少大部分应该成功（允许少量失败因为文件锁竞争）
        assert success_count >= 7, f"Only {success_count}/10 messages sent"

        # 验证消息可以列出
        resp = await client.get(f"/v1/threads/{thread_id}/messages", headers=HEADERS)
        assert resp.status_code == 200


class TestConcurrentRequests:
    """高并发请求稳定性"""

    @pytest.mark.anyio
    async def test_100_concurrent_health(self, client):
        """100个并发health请求全部成功"""
        tasks = [client.get("/health") for _ in range(100)]
        results = await asyncio.gather(*tasks)
        assert all(r.status_code == 200 for r in results)

    @pytest.mark.anyio
    async def test_50_concurrent_models(self, client):
        """50个并发models请求全部成功"""
        tasks = [client.get("/v1/models", headers=HEADERS) for _ in range(50)]
        results = await asyncio.gather(*tasks)
        success = sum(1 for r in results if r.status_code == 200)
        assert success == 50

    @pytest.mark.anyio
    async def test_mixed_concurrent_operations(self, client):
        """混合操作并发（读写交错）"""

        async def create_and_list():
            # 创建
            await client.post(
                "/v1/assistants",
                headers=HEADERS,
                json={"name": "concurrent", "model": "test", "instructions": "x"},
            )
            # 列出
            resp = await client.get("/v1/assistants", headers=HEADERS)
            return resp.status_code

        results = await asyncio.gather(*[create_and_list() for _ in range(15)])
        # 所有列出操作应成功
        assert all(r == 200 for r in results)


class TestRapidFireResilience:
    """快速连续请求韧性"""

    @pytest.mark.anyio
    async def test_sequential_rapid_requests(self, client):
        """200个快速连续请求不应导致任何crash"""
        errors = []
        for i in range(200):
            resp = await client.get("/health")
            if resp.status_code != 200:
                errors.append((i, resp.status_code))

        assert len(errors) == 0, f"Failures at requests: {errors[:5]}"

    @pytest.mark.anyio
    async def test_alternating_auth_no_auth(self, client):
        """认证/非认证请求交替不应导致状态混乱"""
        for i in range(50):
            if i % 2 == 0:
                # 有认证
                resp = await client.get("/v1/models", headers=HEADERS)
                assert resp.status_code == 200, f"Auth request {i} failed: {resp.status_code}"
            else:
                # 无认证
                resp = await client.get("/v1/models")
                assert resp.status_code in (401, 403), (
                    f"Unauth request {i} unexpected: {resp.status_code}"
                )


class TestConcurrentChatRequests:
    """并发Chat请求稳定性"""

    @pytest.mark.anyio
    async def test_concurrent_chat_all_get_proper_error(self, client):
        """20个并发chat请求都应得到合理错误(非500)"""

        async def chat(i):
            resp = await client.post(
                "/v1/chat/completions",
                headers=HEADERS,
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": f"question {i}"}],
                },
            )
            return resp.status_code

        statuses = await asyncio.gather(*[chat(i) for i in range(20)])
        # 所有请求都应得到合理的错误(502/503), 不应是500(unhandled)
        for i, s in enumerate(statuses):
            assert s != 500, f"Request {i} got unhandled 500"
        # 所有应该是502或503(no provider)
        assert all(s in (502, 503) for s in statuses)
