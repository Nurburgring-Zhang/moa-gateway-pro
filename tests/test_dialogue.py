"""Tests for moa_gateway.dialogue — 多 AI 同框对话.

覆盖:
- Pydantic 模型 extra=forbid / 边界校验
- DialogueStorage CRUD / 分页 / WAL / 重启恢复
- DialogueEngine 三种模式 (round_robin / parallel_think / free_talk)
- 失败参与者处理(记录真实证据,不伪造)/ 单参与者超时 / max_rounds 上限
- 事件流格式 (room_id/round/speaker/delta/final/status)
- 路由 CRUD + 鉴权 + SSE 回放
- 端到端: 建房间(2 参与者) → 用户发言 → 2 条真实引擎产出的发言
  (测试环境无真实 key,按 settings.mock.mode=explicit 走 MockProvider 并显式标注)
"""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from moa_gateway.dialogue.models import (
    DialogueEvent,
    DialogueMessage,
    DialogueMode,
    DialogueRoom,
    MessageStatus,
    Participant,
    RoomStatus,
)
from moa_gateway.dialogue.storage import DialogueStorage, new_message_id, new_room_id
from moa_gateway.providers.base import ChatResponse, ProviderError

API_KEY = "dlg-test-key-001"


# ==================== Fixtures ====================


@pytest.fixture(autouse=True)
def _isolate_dialogue(tmp_path, monkeypatch):
    """隔离 dialogue 单例,数据库落在 tmp_path."""
    import moa_gateway.dialogue.engine as de
    import moa_gateway.dialogue.storage as ds

    monkeypatch.setattr("moa_gateway.config.ROOT_DIR", tmp_path)
    ds._ds_instance = None
    de._engine_instance = None
    yield
    ds._ds_instance = None
    de._engine_instance = None


def _make_settings(**overrides):
    from moa_gateway.config import ModelEndpointConfig, Settings

    base = dict(
        auth={
            "admin_username": "admin",
            "admin_password": "DlgTestP@ss!2024",
            "jwt_secret": "test-dialogue-secret-long-enough-for-hs256-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [API_KEY],
        },
        models=[
            ModelEndpointConfig(
                id="ep-alpha", provider="mock", model="mock-lite-alpha",
                tier="standard", enabled=True,
            ),
            ModelEndpointConfig(
                id="ep-beta", provider="mock", model="mock-lite-beta",
                tier="standard", enabled=True,
            ),
            ModelEndpointConfig(
                id="ep-gamma", provider="mock", model="mock-lite-gamma",
                tier="standard", enabled=True,
            ),
            ModelEndpointConfig(
                id="ep-off", provider="mock", model="mock-off",
                tier="standard", enabled=False,
            ),
        ],
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def dlg_settings():
    return _make_settings()


@pytest.fixture
def pool(dlg_settings, storage_instance):
    """真实 ModelPool,端点全部走 MockProvider(测试环境无 key 的既定策略)."""
    from moa_gateway.model_pool import ModelPool

    return ModelPool(settings=dlg_settings, storage=storage_instance)


@pytest.fixture
def dstorage(tmp_path):
    return DialogueStorage(db_path=tmp_path / "dialogue.db")


@pytest.fixture
def engine(pool, dstorage):
    from moa_gateway.dialogue.engine import DialogueEngine

    return DialogueEngine(pool=pool, storage=dstorage)


def _make_room(
    dstorage,
    mode: str = "round_robin",
    max_rounds: int = 1,
    endpoint_ids: tuple[str, ...] = ("ep-alpha", "ep-beta"),
    names: tuple[str, ...] = ("Alpha", "Beta"),
    participant_timeout: float = 10.0,
) -> DialogueRoom:
    parts = [
        Participant(endpoint_id=eid, name=name, persona=f"You are {name}.")
        for eid, name in zip(endpoint_ids, names)
    ]
    room = DialogueRoom(
        room_id=new_room_id(),
        topic="How should we design a rate limiter?",
        mode=DialogueMode(mode),
        participants=parts,
        max_rounds=max_rounds,
        participant_timeout=participant_timeout,
    )
    dstorage.create_room(room)
    return room


@pytest.fixture
async def app():
    """完整 create_app()(验证 server.py 真实接线),配置走 mock 端点."""
    test_settings = _make_settings()
    with patch("moa_gateway.config._settings", test_settings):
        from moa_gateway.server import create_app

        application = create_app()
        yield application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", timeout=60) as ac:
        yield ac


@pytest.fixture
def headers():
    return {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


def _parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


# ==================== 1. Models ====================


class TestModels:
    def test_participant_extra_forbid(self):
        with pytest.raises(ValidationError):
            Participant(endpoint_id="ep-alpha", name="A", persona="x", hacker="boom")

    def test_room_rejects_unknown_mode_and_bad_bounds(self):
        parts = [
            Participant(endpoint_id="a", name="A"),
            Participant(endpoint_id="b", name="B"),
        ]
        with pytest.raises(ValidationError):
            DialogueRoom(room_id="r1", topic="t", mode="nope", participants=parts)
        with pytest.raises(ValidationError):
            DialogueRoom(room_id="r1", topic="t", participants=parts, max_rounds=0)
        with pytest.raises(ValidationError):
            DialogueRoom(room_id="r1", topic="t", participants=parts, max_rounds=99)
        with pytest.raises(ValidationError):
            DialogueRoom(room_id="r1", topic="t", participants=[])

    def test_message_and_event_models(self):
        msg = DialogueMessage(
            message_id=new_message_id(), room_id="r1", speaker="Alpha", role="assistant"
        )
        assert msg.status == MessageStatus.OK
        assert msg.mock is False
        with pytest.raises(ValidationError):
            DialogueMessage(
                message_id="m", room_id="r", speaker="s", role="alien"
            )
        ev = DialogueEvent(type="message_end", room_id="r1", speaker="Alpha", final="hi")
        dumped = ev.model_dump()
        for key in ("room_id", "round", "speaker", "final", "status"):
            assert key in dumped


# ==================== 2. Storage ====================


class TestStorage:
    def test_room_crud_roundtrip(self, dstorage):
        room = _make_room(dstorage)
        got = dstorage.get_room(room.room_id)
        assert got is not None
        assert got.topic == room.topic
        assert got.mode == DialogueMode.ROUND_ROBIN
        assert [p.name for p in got.participants] == ["Alpha", "Beta"]
        assert got.participants[0].persona == "You are Alpha."
        rooms = dstorage.list_rooms()
        assert len(rooms) == 1
        assert dstorage.delete_room(room.room_id) is True
        assert dstorage.get_room(room.room_id) is None
        assert dstorage.delete_room(room.room_id) is False

    def test_list_rooms_status_filter(self, dstorage):
        r1 = _make_room(dstorage)
        r2 = _make_room(dstorage)
        r2.status = RoomStatus.ARCHIVED
        dstorage.update_room(r2)
        active = dstorage.list_rooms(status="active")
        archived = dstorage.list_rooms(status="archived")
        assert [r.room_id for r in active] == [r1.room_id]
        assert [r.room_id for r in archived] == [r2.room_id]

    def test_messages_pagination_and_count(self, dstorage):
        room = _make_room(dstorage)
        ids = []
        for i in range(10):
            m = DialogueMessage(
                message_id=new_message_id(), room_id=room.room_id, speaker="Alpha",
                role="assistant", content=f"msg-{i}",
            )
            dstorage.add_message(m)
            ids.append(m.message_id)
        assert dstorage.count_messages(room.room_id) == 10
        page = dstorage.list_messages(room.room_id, limit=4, offset=2)
        assert [m.content for m in page] == ["msg-2", "msg-3", "msg-4", "msg-5"]
        desc = dstorage.list_messages(room.room_id, limit=3, ascending=False)
        assert [m.content for m in desc] == ["msg-9", "msg-8", "msg-7"]

    def test_delete_room_removes_messages(self, dstorage):
        room = _make_room(dstorage)
        dstorage.add_message(
            DialogueMessage(
                message_id=new_message_id(), room_id=room.room_id,
                speaker="user", role="user", content="hello",
            )
        )
        assert dstorage.count_messages(room.room_id) == 1
        dstorage.delete_room(room.room_id)
        assert dstorage.count_messages(room.room_id) == 0

    def test_persistence_across_restart(self, tmp_path):
        db_file = tmp_path / "persist.db"
        s1 = DialogueStorage(db_path=db_file)
        room = _make_room(s1)
        s1.add_message(
            DialogueMessage(
                message_id=new_message_id(), room_id=room.room_id,
                speaker="Alpha", role="assistant", content="survive restart",
            )
        )
        # “重启”: 全新实例打开同一个库
        s2 = DialogueStorage(db_path=db_file)
        got = s2.get_room(room.room_id)
        assert got is not None
        assert got.mode == DialogueMode.ROUND_ROBIN
        msgs = s2.list_messages(room.room_id)
        assert len(msgs) == 1
        assert msgs[0].content == "survive restart"

    def test_sqlite_wal_mode_enabled(self, dstorage):
        with dstorage._engine.conn() as c:
            row = c.execute("PRAGMA journal_mode").fetchone()
        assert str(row[0]).lower() == "wal"


# ==================== 3. Engine — round_robin ====================


class TestRoundRobin:
    async def test_each_participant_speaks_in_order(self, engine, dstorage):
        room = _make_room(dstorage, mode="round_robin", max_rounds=1)
        msgs = await engine.run_turn(room, "Kick off the discussion")
        assistants = [m for m in msgs if m.role == "assistant"]
        assert [m.speaker for m in assistants] == ["Alpha", "Beta"]
        assert all(m.status == MessageStatus.OK for m in assistants)
        assert all(m.content.strip() for m in assistants)
        assert all(m.endpoint_id in ("ep-alpha", "ep-beta") for m in assistants)

    async def test_max_rounds_respected(self, engine, dstorage):
        room = _make_room(dstorage, mode="round_robin", max_rounds=3)
        msgs = await engine.run_turn(room, "Go")
        assistants = [m for m in msgs if m.role == "assistant"]
        assert len(assistants) == 6  # 3 轮 × 2 参与者
        assert [m.speaker for m in assistants] == ["Alpha", "Beta"] * 3

    async def test_shared_history_labels_peer_speaker(self, engine, dstorage, pool):
        """Beta 收到的上下文必须包含 Alpha 的发言且标注发言者."""
        captured: list[list[dict]] = []
        real_chat = pool.endpoints["ep-beta"].provider_obj.chat

        async def spy_chat(req):
            captured.append(list(req.messages))
            return await real_chat(req)

        pool.endpoints["ep-beta"].provider_obj.chat = spy_chat
        room = _make_room(dstorage, mode="round_robin", max_rounds=1)
        await engine.run_turn(room, "What do you think about token buckets?")
        assert captured, "Beta endpoint was never called"
        ctx = captured[-1]
        joined = json.dumps(ctx, ensure_ascii=False)
        assert "[Alpha]:" in joined  # 其他 AI 发言带发言者标注
        assert "What do you think about token buckets?" in joined  # 用户发言在上下文里

    async def test_user_message_persisted_with_round(self, engine, dstorage):
        room = _make_room(dstorage, mode="round_robin", max_rounds=1)
        await engine.run_turn(room, "first user line")
        await engine.run_turn(room, "second user line")
        all_msgs = dstorage.list_messages(room.room_id, limit=100)
        users = [m for m in all_msgs if m.role == "user"]
        assert [m.content for m in users] == ["first user line", "second user line"]
        assert [m.round for m in users] == [1, 2]
        assert dstorage.get_room(room.room_id).current_round == 2


# ==================== 4. Engine — parallel_think ====================


class TestParallelThink:
    async def test_all_participants_recorded(self, engine, dstorage):
        room = _make_room(dstorage, mode="parallel_think", max_rounds=1)
        msgs = await engine.run_turn(room, "Think in parallel")
        assistants = [m for m in msgs if m.role == "assistant"]
        assert sorted(m.speaker for m in assistants) == ["Alpha", "Beta"]
        assert all(m.status == MessageStatus.OK for m in assistants)
        assert all(m.content.strip() for m in assistants)

    async def test_runs_concurrently(self, engine, dstorage, pool):
        """两个参与者各 sleep 0.4s;并行总耗时应明显小于串行 0.8s."""
        for eid in ("ep-alpha", "ep-beta"):
            async def slow_chat(req):
                await asyncio.sleep(0.4)
                return ChatResponse(content="parallel thought", model=req.model)

            pool.endpoints[eid].provider_obj.chat = slow_chat
        room = _make_room(dstorage, mode="parallel_think", max_rounds=1)
        t0 = time.monotonic()
        msgs = await engine.run_turn(room, "go")
        elapsed = time.monotonic() - t0
        assert len([m for m in msgs if m.role == "assistant"]) == 2
        assert elapsed < 0.75, f"parallel_think not concurrent: {elapsed:.2f}s"

    async def test_partial_failure_keeps_others(self, engine, dstorage, pool):
        async def boom(req):
            raise ProviderError("upstream 500 alpha exploded", status=500)

        pool.endpoints["ep-alpha"].provider_obj.chat = boom
        room = _make_room(dstorage, mode="parallel_think", max_rounds=1)
        msgs = await engine.run_turn(room, "go")
        by_speaker = {m.speaker: m for m in msgs if m.role == "assistant"}
        assert by_speaker["Alpha"].status == MessageStatus.ERROR
        assert "alpha exploded" in by_speaker["Alpha"].error
        assert by_speaker["Alpha"].content == ""  # 不伪造内容
        assert by_speaker["Beta"].status == MessageStatus.OK
        assert by_speaker["Beta"].content.strip()


# ==================== 5. Engine — free_talk ====================


class TestFreeTalk:
    def _patch_host(self, pool, decisions: list[str]):
        """主持人 endpoint = participants[0] = ep-alpha;按序返回 JSON 决策."""
        state = {"i": 0}

        async def host_chat(req):
            i = state["i"]
            state["i"] += 1
            content = decisions[i] if i < len(decisions) else decisions[-1]
            return ChatResponse(content=content, model=req.model)

        pool.endpoints["ep-alpha"].provider_obj.chat = host_chat

    async def test_host_selects_speaker_then_end(self, engine, dstorage, pool):
        self._patch_host(
            pool,
            [
                '{"speaker": "Beta", "reason": "Beta should open"}',
                '{"speaker": "END", "reason": "conclusion reached"}',
            ],
        )
        room = _make_room(dstorage, mode="free_talk", max_rounds=5)
        msgs = await engine.run_turn(room, "Discuss")
        speakers = [m.speaker for m in msgs]
        assert "Beta" in speakers  # 主持人点名的发言者真实发言了
        beta = next(m for m in msgs if m.speaker == "Beta")
        assert beta.status == MessageStatus.OK
        assert beta.content.strip()
        moderators = [m for m in msgs if m.speaker == "moderator"]
        assert len(moderators) == 2  # 第二次决策 END 后收敛
        assert all(m.role == "system" for m in moderators)

    async def test_invalid_host_output_auto_converges(self, engine, dstorage, pool):
        """MockProvider 输出不是 JSON → 连续无进展 → 自动收敛,不会死循环."""
        room = _make_room(dstorage, mode="free_talk", max_rounds=10)
        t0 = time.monotonic()
        msgs = await engine.run_turn(room, "Discuss")
        assert time.monotonic() - t0 < 30
        # NO_PROGRESS_LIMIT=3 → 恰好 3 条主持人消息后收敛
        assert len(msgs) == 3
        assert all(m.speaker == "moderator" for m in msgs)
        converged = [
            e for e in engine.get_buffer(room.room_id) if e.get("status") == "converged"
        ]
        assert converged, "no converged event emitted"

    async def test_max_rounds_cap(self, engine, dstorage, pool):
        self._patch_host(pool, ['{"speaker": "Beta", "reason": "keep going"}'])
        room = _make_room(dstorage, mode="free_talk", max_rounds=2)
        msgs = await engine.run_turn(room, "Discuss")
        beta_msgs = [m for m in msgs if m.speaker == "Beta"]
        moderator_msgs = [m for m in msgs if m.speaker == "moderator"]
        assert len(beta_msgs) == 2  # max_rounds 上限生效
        assert len(moderator_msgs) == 2

    async def test_host_failure_recorded_as_evidence(self, engine, dstorage, pool):
        async def boom(req):
            raise ProviderError("moderator upstream down", status=500)

        pool.endpoints["ep-alpha"].provider_obj.chat = boom
        room = _make_room(dstorage, mode="free_talk", max_rounds=5)
        msgs = await engine.run_turn(room, "Discuss")
        assert len(msgs) == 3  # 连续 3 轮主持人失败 → 收敛
        assert all(m.status == MessageStatus.ERROR for m in msgs)
        assert all("moderator upstream down" in (m.error or "") for m in msgs)
        assert all(m.content == "" for m in msgs)  # 不伪造


# ==================== 6. 失败 / 超时 / mock 标注 ====================


class TestFailureHandling:
    async def test_failed_participant_records_real_evidence(self, engine, dstorage, pool):
        async def boom(req):
            raise ProviderError("upstream 500 boom", status=500)

        pool.endpoints["ep-alpha"].provider_obj.chat = boom
        room = _make_room(dstorage, mode="round_robin", max_rounds=1)
        msgs = await engine.run_turn(room, "go")
        by_speaker = {m.speaker: m for m in msgs if m.role == "assistant"}
        alpha = by_speaker["Alpha"]
        assert alpha.status == MessageStatus.ERROR
        assert "upstream 500 boom" in alpha.error
        assert alpha.content == ""  # 失败证据,不是伪造内容
        # 失败消息也落库了(可审计)
        stored = dstorage.list_messages(room.room_id, limit=100)
        assert any(m.status == MessageStatus.ERROR for m in stored)
        # 其他参与者不受影响
        assert by_speaker["Beta"].status == MessageStatus.OK

    async def test_participant_timeout_recorded(self, engine, dstorage, pool):
        async def slow(req):
            await asyncio.sleep(5)
            return ChatResponse(content="too late", model=req.model)

        pool.endpoints["ep-alpha"].provider_obj.chat = slow
        room = _make_room(dstorage, mode="round_robin", max_rounds=1, participant_timeout=1.0)
        t0 = time.monotonic()
        msgs = await engine.run_turn(room, "go")
        assert time.monotonic() - t0 < 4
        by_speaker = {m.speaker: m for m in msgs if m.role == "assistant"}
        assert by_speaker["Alpha"].status == MessageStatus.TIMEOUT
        assert "timeout" in by_speaker["Alpha"].error
        assert by_speaker["Beta"].status == MessageStatus.OK

    async def test_missing_endpoint_records_evidence(self, engine, dstorage):
        room = _make_room(
            dstorage, mode="round_robin", max_rounds=1,
            endpoint_ids=("ep-nonexistent", "ep-beta"),
            names=("Ghost", "Beta"),
        )
        msgs = await engine.run_turn(room, "go")
        by_speaker = {m.speaker: m for m in msgs if m.role == "assistant"}
        assert by_speaker["Ghost"].status == MessageStatus.ERROR
        assert "not found" in by_speaker["Ghost"].error
        assert by_speaker["Beta"].status == MessageStatus.OK

    async def test_mock_outputs_explicitly_labeled(self, engine, dstorage):
        """测试环境无真实 key → MockProvider 兜底,必须显式标注 mock."""
        room = _make_room(dstorage, mode="round_robin", max_rounds=1)
        msgs = await engine.run_turn(room, "go")
        assistants = [m for m in msgs if m.role == "assistant"]
        assert assistants and all(m.mock is True for m in assistants)
        end_events = [
            e for e in engine.get_buffer(room.room_id)
            if e["type"] == "message_end" and e["speaker"] in ("Alpha", "Beta")
        ]
        assert end_events and all(e["mock"] is True for e in end_events)


# ==================== 7. 事件流 ====================


class TestEvents:
    async def test_lifecycle_events_have_required_shape(self, engine, dstorage):
        room = _make_room(dstorage, mode="round_robin", max_rounds=1)
        await engine.run_turn(room, "go")
        events = engine.get_buffer(room.room_id)
        types = [e["type"] for e in events]
        assert types[0] == "turn_start"
        assert types[-1] == "turn_complete"
        for t in ("round_start", "message_start", "message_end", "round_end"):
            assert t in types
        # 每条发言事件必须带 {room_id, round, speaker, final/delta, status}
        for e in events:
            for key in ("room_id", "round", "speaker", "status"):
                assert key in e
            assert e["room_id"] == room.room_id
        ends = [e for e in events if e["type"] == "message_end"]
        assert [e["speaker"] for e in ends] == ["user", "Alpha", "Beta"]
        assert all(e["final"] for e in ends)

    async def test_delta_streaming_via_chat_stream(self, engine, dstorage):
        """stream=True 走 provider.chat_stream 真实 token 流 → delta 事件."""
        room = _make_room(dstorage, mode="round_robin", max_rounds=1)
        msgs = await engine.run_turn(room, "write some code", stream=True)
        events = engine.get_buffer(room.room_id)
        deltas = [e for e in events if e["type"] == "delta"]
        assert deltas, "no delta events emitted"
        assert all(e["status"] == "streaming" for e in deltas)
        # delta 拼回来 == 落库的最终内容(对每个参与者)
        for m in msgs:
            if m.role != "assistant" or m.status != MessageStatus.OK:
                continue
            joined = "".join(
                e["delta"] for e in deltas if e["speaker"] == m.speaker
            )
            assert joined == m.content

    async def test_subscriber_queue_receives_live_events(self, engine, dstorage):
        room = _make_room(dstorage, mode="round_robin", max_rounds=1)
        q = engine.subscribe(room.room_id)
        try:
            await engine.run_turn(room, "go")
            received = []
            while not q.empty():
                received.append(q.get_nowait())
            assert any(e["type"] == "turn_start" for e in received)
            assert any(e["type"] == "turn_complete" for e in received)
        finally:
            engine.unsubscribe(room.room_id, q)


# ==================== 8. 路由 — 鉴权 ====================


class TestAuth:
    async def test_all_endpoints_require_auth_401(self, client):
        resp = await client.post("/v1/dialogue/rooms", json={"topic": "t"})
        assert resp.status_code == 401
        resp = await client.get("/v1/dialogue/rooms")
        assert resp.status_code == 401
        resp = await client.get("/v1/dialogue/rooms/room_x")
        assert resp.status_code == 401
        resp = await client.post("/v1/dialogue/rooms/room_x/messages", json={"content": "hi"})
        assert resp.status_code == 401
        resp = await client.get("/v1/dialogue/rooms/room_x/stream")
        assert resp.status_code == 401
        resp = await client.delete("/v1/dialogue/rooms/room_x")
        assert resp.status_code == 401

    async def test_wrong_key_401(self, client):
        bad = {"Authorization": "Bearer not-a-real-key"}
        resp = await client.get("/v1/dialogue/rooms", headers=bad)
        assert resp.status_code == 401
        resp = await client.post(
            "/v1/dialogue/rooms", headers=bad, json={"topic": "t"}
        )
        assert resp.status_code == 401


# ==================== 9. 路由 — 房间 CRUD ====================


class TestRoomAPI:
    async def test_create_room_success(self, client, headers):
        resp = await client.post(
            "/v1/dialogue/rooms",
            headers=headers,
            json={
                "topic": "Rate limiter design",
                "mode": "round_robin",
                "participants": [
                    {"endpoint_id": "ep-alpha", "name": "Alpha", "persona": "optimist"},
                    {"endpoint_id": "ep-beta", "name": "Beta"},
                ],
                "max_rounds": 1,
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["room_id"].startswith("room_")
        assert data["mode"] == "round_robin"
        assert data["status"] == "active"
        assert [p["name"] for p in data["participants"]] == ["Alpha", "Beta"]
        assert data["participants"][1]["persona"] == ""

    async def test_create_room_unknown_endpoint_404(self, client, headers):
        resp = await client.post(
            "/v1/dialogue/rooms",
            headers=headers,
            json={
                "topic": "t",
                "participants": [
                    {"endpoint_id": "ep-alpha"},
                    {"endpoint_id": "ep-does-not-exist"},
                ],
            },
        )
        assert resp.status_code == 404
        assert "ep-does-not-exist" in resp.json()["detail"]

    async def test_create_room_disabled_endpoint_400(self, client, headers):
        resp = await client.post(
            "/v1/dialogue/rooms",
            headers=headers,
            json={
                "topic": "t",
                "participants": [{"endpoint_id": "ep-alpha"}, {"endpoint_id": "ep-off"}],
            },
        )
        assert resp.status_code == 400
        assert "disabled" in resp.json()["detail"]

    async def test_create_room_single_participant_422(self, client, headers):
        resp = await client.post(
            "/v1/dialogue/rooms",
            headers=headers,
            json={"topic": "t", "participants": [{"endpoint_id": "ep-alpha"}]},
        )
        assert resp.status_code == 422  # 多 AI 同框至少 2 个参与者

    async def test_create_room_invalid_mode_422(self, client, headers):
        resp = await client.post(
            "/v1/dialogue/rooms",
            headers=headers,
            json={
                "topic": "t",
                "mode": "battle_royale",
                "participants": [{"endpoint_id": "ep-alpha"}, {"endpoint_id": "ep-beta"}],
            },
        )
        assert resp.status_code == 422

    async def test_create_room_extra_field_422(self, client, headers):
        resp = await client.post(
            "/v1/dialogue/rooms",
            headers=headers,
            json={
                "topic": "t",
                "participants": [{"endpoint_id": "ep-alpha"}, {"endpoint_id": "ep-beta"}],
                "evil_extra": True,
            },
        )
        assert resp.status_code == 422  # extra=forbid

    async def test_create_room_duplicate_name_400(self, client, headers):
        resp = await client.post(
            "/v1/dialogue/rooms",
            headers=headers,
            json={
                "topic": "t",
                "participants": [
                    {"endpoint_id": "ep-alpha", "name": "Twins"},
                    {"endpoint_id": "ep-beta", "name": "Twins"},
                ],
            },
        )
        assert resp.status_code == 400

    async def test_list_rooms_and_detail_with_history(self, client, headers):
        created = (
            await client.post(
                "/v1/dialogue/rooms",
                headers=headers,
                json={
                    "topic": "List me",
                    "participants": [{"endpoint_id": "ep-alpha"}, {"endpoint_id": "ep-beta"}],
                    "max_rounds": 1,
                },
            )
        ).json()
        listing = await client.get("/v1/dialogue/rooms", headers=headers)
        assert listing.status_code == 200
        rooms = listing.json()["rooms"]
        assert any(r["room_id"] == created["room_id"] for r in rooms)

        await client.post(
            f"/v1/dialogue/rooms/{created['room_id']}/messages",
            headers=headers,
            json={"content": "hello room"},
        )
        detail = await client.get(
            f"/v1/dialogue/rooms/{created['room_id']}", headers=headers
        )
        assert detail.status_code == 200
        body = detail.json()
        assert body["room"]["room_id"] == created["room_id"]
        assert body["total_messages"] >= 3  # user + 2 AI
        speakers = [m["speaker"] for m in body["messages"]]
        assert "user" in speakers

    async def test_get_room_404(self, client, headers):
        resp = await client.get("/v1/dialogue/rooms/room_missing", headers=headers)
        assert resp.status_code == 404

    async def test_delete_room_then_404(self, client, headers):
        created = (
            await client.post(
                "/v1/dialogue/rooms",
                headers=headers,
                json={
                    "topic": "Delete me",
                    "participants": [{"endpoint_id": "ep-alpha"}, {"endpoint_id": "ep-beta"}],
                },
            )
        ).json()
        resp = await client.delete(f"/v1/dialogue/rooms/{created['room_id']}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        resp = await client.get(f"/v1/dialogue/rooms/{created['room_id']}", headers=headers)
        assert resp.status_code == 404
        resp = await client.delete(f"/v1/dialogue/rooms/{created['room_id']}", headers=headers)
        assert resp.status_code == 404

    async def test_post_message_unknown_room_404(self, client, headers):
        resp = await client.post(
            "/v1/dialogue/rooms/room_missing/messages",
            headers=headers,
            json={"content": "hi"},
        )
        assert resp.status_code == 404

    async def test_post_message_empty_content_422(self, client, headers):
        created = (
            await client.post(
                "/v1/dialogue/rooms",
                headers=headers,
                json={
                    "topic": "t",
                    "participants": [{"endpoint_id": "ep-alpha"}, {"endpoint_id": "ep-beta"}],
                },
            )
        ).json()
        resp = await client.post(
            f"/v1/dialogue/rooms/{created['room_id']}/messages",
            headers=headers,
            json={"content": ""},
        )
        assert resp.status_code == 422

    async def test_post_message_archived_room_409(self, client, headers):
        from moa_gateway.dialogue.models import RoomStatus
        from moa_gateway.dialogue.storage import get_dialogue_storage

        created = (
            await client.post(
                "/v1/dialogue/rooms",
                headers=headers,
                json={
                    "topic": "Archive me",
                    "participants": [{"endpoint_id": "ep-alpha"}, {"endpoint_id": "ep-beta"}],
                },
            )
        ).json()
        storage = get_dialogue_storage()
        room = storage.get_room(created["room_id"])
        room.status = RoomStatus.ARCHIVED
        storage.update_room(room)
        resp = await client.post(
            f"/v1/dialogue/rooms/{created['room_id']}/messages",
            headers=headers,
            json={"content": "hi"},
        )
        assert resp.status_code == 409


# ==================== 10. 端到端 ====================


class TestEndToEnd:
    async def test_two_participants_produce_two_real_responses(self, client, headers):
        """建房间(2 参与者) → 用户发言 → 收到 2 条真实引擎产出的发言."""
        created = (
            await client.post(
                "/v1/dialogue/rooms",
                headers=headers,
                json={
                    "topic": "SQLite WAL vs DELETE journal",
                    "mode": "round_robin",
                    "participants": [
                        {"endpoint_id": "ep-alpha", "name": "Alpha", "persona": "DBA"},
                        {"endpoint_id": "ep-beta", "name": "Beta", "persona": "SRE"},
                    ],
                    "max_rounds": 1,
                },
            )
        ).json()
        room_id = created["room_id"]

        resp = await client.post(
            f"/v1/dialogue/rooms/{room_id}/messages",
            headers=headers,
            json={"content": "Which journal mode should we use and why?"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["room_id"] == room_id
        assert body["round"] == 1
        assistants = [r for r in body["responses"] if r["role"] == "assistant"]
        assert len(assistants) == 2  # 2 个参与者各 1 条
        assert sorted(r["speaker"] for r in assistants) == ["Alpha", "Beta"]
        assert all(r["status"] == "ok" for r in assistants)
        assert all(r["content"].strip() for r in assistants)
        # 测试环境无真实 key → MockProvider 显式标注(项目既定政策)
        assert body["mock_used"] is True
        assert all(r["mock"] is True for r in assistants)

    async def test_sse_replay_events_readable(self, client, headers):
        """SSE 流事件可读: 回放缓冲包含逐参与者 message_end + turn_complete."""
        created = (
            await client.post(
                "/v1/dialogue/rooms",
                headers=headers,
                json={
                    "topic": "SSE check",
                    "participants": [
                        {"endpoint_id": "ep-alpha", "name": "Alpha"},
                        {"endpoint_id": "ep-beta", "name": "Beta"},
                    ],
                    "max_rounds": 1,
                },
            )
        ).json()
        room_id = created["room_id"]
        await client.post(
            f"/v1/dialogue/rooms/{room_id}/messages",
            headers=headers,
            json={"content": "say something"},
        )

        resp = await client.get(
            f"/v1/dialogue/rooms/{room_id}/stream",
            headers=headers,
            params={"replay": "true", "live": "false"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(resp.text)
        assert events, "SSE stream returned no events"
        for e in events:
            for key in ("type", "room_id", "round", "speaker", "status"):
                assert key in e
            assert e["room_id"] == room_id
        ends = {
            e["speaker"]: e
            for e in events
            if e["type"] == "message_end" and e["speaker"] in ("Alpha", "Beta")
        }
        assert set(ends) == {"Alpha", "Beta"}  # 逐参与者推送
        assert all(e["final"].strip() for e in ends.values())
        assert any(e["type"] == "turn_complete" for e in events)

    async def test_sse_stream_404_unknown_room(self, client, headers):
        resp = await client.get(
            "/v1/dialogue/rooms/room_missing/stream",
            headers=headers,
            params={"live": "false"},
        )
        assert resp.status_code == 404

    async def test_parallel_mode_via_api(self, client, headers):
        created = (
            await client.post(
                "/v1/dialogue/rooms",
                headers=headers,
                json={
                    "topic": "Parallel API check",
                    "mode": "parallel_think",
                    "participants": [
                        {"endpoint_id": "ep-alpha", "name": "Alpha"},
                        {"endpoint_id": "ep-gamma", "name": "Gamma"},
                    ],
                    "max_rounds": 1,
                },
            )
        ).json()
        resp = await client.post(
            f"/v1/dialogue/rooms/{created['room_id']}/messages",
            headers=headers,
            json={"content": "think"},
        )
        assert resp.status_code == 200
        assistants = [r for r in resp.json()["responses"] if r["role"] == "assistant"]
        assert sorted(r["speaker"] for r in assistants) == ["Alpha", "Gamma"]
        assert all(r["status"] == "ok" for r in assistants)
