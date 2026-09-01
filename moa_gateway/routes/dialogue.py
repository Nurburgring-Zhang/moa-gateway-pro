"""moa_gateway.routes.dialogue — 多 AI 同框对话路由.

- POST   /v1/dialogue/rooms                    创建房间
- GET    /v1/dialogue/rooms                    房间列表
- GET    /v1/dialogue/rooms/{room_id}          详情 + 历史(分页)
- POST   /v1/dialogue/rooms/{room_id}/messages 用户发言并触发一轮多 AI 响应
- GET    /v1/dialogue/rooms/{room_id}/stream   SSE 事件流(逐参与者推送)
- DELETE /v1/dialogue/rooms/{room_id}          删除房间

全部 require_api_key 鉴权;POST 端点走 per-key 限流。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..auth import require_api_key
from ..dialogue.engine import get_dialogue_engine
from ..dialogue.models import DialogueMode, DialogueRoom, Participant, RoomStatus
from ..dialogue.storage import get_dialogue_storage, new_room_id
from ..model_pool import get_model_pool
from ..ratelimit import get_limiter
from ..req_models import CreateDialogueRoomRequest, PostDialogueMessageRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dialogue"])

_SSE_KEEPALIVE_SECONDS = 15.0


def _check_rate_limit(key_info: dict[str, Any]) -> None:
    limiter = get_limiter()
    try:
        limiter.check_and_incr(key_info)
    except HTTPException:
        raise


def _room_or_404(room_id: str) -> DialogueRoom:
    room = get_dialogue_storage().get_room(room_id)
    if not room:
        raise HTTPException(status_code=404, detail=f"dialogue room '{room_id}' not found")
    return room


def _sse_line(event: dict[str, Any]) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


# ==================== 房间 CRUD ====================


@router.post("/v1/dialogue/rooms")
async def create_dialogue_room(
    req: CreateDialogueRoomRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """创建多 AI 对话房间。participants 的 endpoint_id 必须是模型池里真实存在的端点。"""
    _check_rate_limit(key_info)
    pool = get_model_pool()

    # 端点存在性 + 可用性校验(失败直接 4xx,不吞错)
    for spec in req.participants:
        ep = pool.get_endpoint(spec.endpoint_id)
        if ep is None:
            raise HTTPException(
                status_code=404,
                detail=f"endpoint '{spec.endpoint_id}' not found in model pool",
            )
        if not ep.config.enabled:
            raise HTTPException(
                status_code=400,
                detail=f"endpoint '{spec.endpoint_id}' is disabled",
            )

    names: set[str] = set()
    participants: list[Participant] = []
    for spec in req.participants:
        name = (spec.name or spec.endpoint_id).strip()
        if name in names:
            raise HTTPException(
                status_code=400, detail=f"duplicate participant name: '{name}'"
            )
        names.add(name)
        participants.append(
            Participant(endpoint_id=spec.endpoint_id, name=name, persona=spec.persona)
        )

    room = DialogueRoom(
        room_id=new_room_id(),
        topic=req.topic,
        mode=DialogueMode(req.mode),
        status=RoomStatus.ACTIVE,
        participants=participants,
        max_rounds=req.max_rounds,
        participant_timeout=req.participant_timeout,
    )
    try:
        get_dialogue_storage().create_room(room)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("failed to persist dialogue room: %s", e)
        raise HTTPException(status_code=500, detail="failed to persist dialogue room") from e
    logger.info(
        "dialogue room created: %s mode=%s participants=%d topic=%r",
        room.room_id, room.mode.value, len(participants), room.topic,
    )
    return room.model_dump()


@router.get("/v1/dialogue/rooms")
async def list_dialogue_rooms(
    status: str | None = Query(None, description="按状态过滤 (active/archived)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """房间列表"""
    if status is not None and status not in ("active", "archived"):
        raise HTTPException(status_code=400, detail="status must be 'active' or 'archived'")
    rooms = get_dialogue_storage().list_rooms(status=status, limit=limit, offset=offset)
    return {
        "rooms": [r.model_dump() for r in rooms],
        "total": len(rooms),
        "limit": limit,
        "offset": offset,
    }


@router.get("/v1/dialogue/rooms/{room_id}")
async def get_dialogue_room(
    room_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """房间详情 + 历史消息(分页,升序)"""
    room = _room_or_404(room_id)
    storage = get_dialogue_storage()
    messages = storage.list_messages(room_id, limit=limit, offset=offset, ascending=True)
    return {
        "room": room.model_dump(),
        "messages": [m.model_dump() for m in messages],
        "total_messages": storage.count_messages(room_id),
        "limit": limit,
        "offset": offset,
    }


@router.delete("/v1/dialogue/rooms/{room_id}")
async def delete_dialogue_room(
    room_id: str,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """删除房间及其全部消息"""
    _room_or_404(room_id)
    ok = get_dialogue_storage().delete_room(room_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"dialogue room '{room_id}' not found")
    get_dialogue_engine().drop_room(room_id)
    return {"status": "deleted", "room_id": room_id}


# ==================== 发言 + SSE ====================


@router.post("/v1/dialogue/rooms/{room_id}/messages")
async def post_dialogue_message(
    room_id: str,
    req: PostDialogueMessageRequest,
    stream: bool = Query(False, description="AI 发言是否走真实 token 流(事件流可见 delta)"),
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """用户发言并触发一轮多 AI 响应(同步返回本轮全部新消息)。

    每条 AI 发言都是 model_pool 真实调用;失败的参与者记录真实失败证据
    (status=error/timeout),mock 兜底产出显式标注 mock=true。
    """
    _check_rate_limit(key_info)
    room = _room_or_404(room_id)
    if room.status != RoomStatus.ACTIVE:
        raise HTTPException(status_code=409, detail=f"room '{room_id}' is {room.status.value}")

    engine = get_dialogue_engine()
    try:
        new_msgs = await engine.run_turn(room, req.content, stream=stream)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("dialogue turn failed for room %s: %s", room_id, e)
        raise HTTPException(status_code=500, detail="dialogue turn failed") from e

    responses = [m.model_dump() for m in new_msgs]
    return {
        "room_id": room_id,
        "round": room.current_round,
        "mode": room.mode.value,
        "responses": responses,
        "ok_count": sum(1 for m in new_msgs if m.status.value == "ok" and m.role == "assistant"),
        "mock_used": any(m.mock for m in new_msgs),
    }


@router.get("/v1/dialogue/rooms/{room_id}/stream")
async def stream_dialogue_room(
    room_id: str,
    replay: bool = Query(True, description="先回放本轮已缓冲的事件"),
    live: bool = Query(True, description="回放后保持实时订阅 (false 时回放完即结束)"),
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """SSE 事件流: 逐参与者推送 {room_id, round, speaker, delta/final, status}。

    provider 支持流式时逐 token 推 delta 事件,否则整条 message_end 推送。
    """
    _room_or_404(room_id)
    engine = get_dialogue_engine()

    async def event_stream():
        if replay:
            for ev in engine.get_buffer(room_id):
                yield _sse_line(ev)
        if not live:
            return
        q = engine.subscribe(room_id)
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=_SSE_KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield _sse_line(ev)
        except asyncio.CancelledError:
            raise
        finally:
            engine.unsubscribe(room_id, q)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
