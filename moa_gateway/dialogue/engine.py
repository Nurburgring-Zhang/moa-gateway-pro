"""moa_gateway.dialogue.engine — 多 AI 对话编排引擎.

三种模式的发言全部走 model_pool 真实调用 LLM endpoint:
- round_robin:    按序每轮每个参与者调用,上下文 = 完整共享历史(含其他 AI
                  发言,标注发言者)
- parallel_think: asyncio.gather 所有参与者并行调用,全部返回后汇总进历史
                  (每条独立可见)
- free_talk:      每轮先让"主持人"(participants[0] 的 endpoint,真实 LLM)
                  决定下一个发言者(输出 JSON {speaker, reason}),再调该发言者;
                  连续 N 轮无进展自动收敛

纪律:
- 任何参与者调用失败 → 记录真实失败证据(status=error/timeout),绝不伪造内容
- 没有可用真实 endpoint 时按 settings.mock.mode 走 MockProvider,model_pool
  已内置该策略;引擎对 mock 产出显式标注 (message.mock / event.mock)
- 每条发言产生事件 {room_id, round, speaker, delta/final, status}
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from collections import defaultdict, deque

from ..model_pool import ModelPool, get_model_pool
from ..providers.base import ProviderError
from .models import (
    DialogueEvent,
    DialogueMessage,
    DialogueMode,
    DialogueRoom,
    MessageStatus,
    Participant,
)
from .storage import DialogueStorage, get_dialogue_storage, new_message_id

logger = logging.getLogger(__name__)

# free_talk 收敛参数
NO_PROGRESS_LIMIT = 3  # 连续 N 轮无进展(主持人无效输出/调用失败/空发言)→ 收敛
SAME_SPEAKER_LIMIT = 3  # 同一发言者连续 N 次独白 → 视为停滞,收敛
END_TOKENS = {"END", "STOP", "CONVERGE", "FINISH", "NONE", "END_DISCUSSION"}
CONTEXT_HISTORY_LIMIT = 100  # 上下文窗口最多带多少条历史

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class DialogueEngine:
    """多 AI 同框对话引擎"""

    def __init__(self, pool: ModelPool | None = None, storage: DialogueStorage | None = None):
        self.pool = pool or get_model_pool()
        self.storage = storage or get_dialogue_storage()
        # room_id -> 订阅者队列(SSE 实时推送)
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        # room_id -> 事件环形缓冲(SSE replay / 事后审计)
        self._buffers: dict[str, deque] = {}
        # room_id -> 轮次锁(同一房间的并发 turn 串行化)
        self._room_locks: dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()

    # ==================== 事件总线 ====================
    def publish(self, room_id: str, event: DialogueEvent) -> None:
        d = event.model_dump()
        buf = self._buffers.setdefault(room_id, deque(maxlen=1000))
        buf.append(d)
        for q in list(self._subscribers.get(room_id, [])):
            try:
                q.put_nowait(d)
            except asyncio.QueueFull:
                # 慢消费者:丢最旧事件,保最新
                try:
                    q.get_nowait()
                    q.put_nowait(d)
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                    pass

    def get_buffer(self, room_id: str) -> list[dict]:
        return list(self._buffers.get(room_id, []))

    def subscribe(self, room_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers[room_id].append(q)
        return q

    def unsubscribe(self, room_id: str, q: asyncio.Queue) -> None:
        subs = self._subscribers.get(room_id)
        if subs and q in subs:
            subs.remove(q)

    def drop_room(self, room_id: str) -> None:
        self._buffers.pop(room_id, None)
        self._subscribers.pop(room_id, None)
        self._room_locks.pop(room_id, None)

    async def _room_lock(self, room_id: str) -> asyncio.Lock:
        async with self._meta_lock:
            lock = self._room_locks.get(room_id)
            if lock is None:
                lock = asyncio.Lock()
                self._room_locks[room_id] = lock
            return lock

    # ==================== 主入口 ====================
    async def run_turn(
        self, room: DialogueRoom, content: str, stream: bool = False
    ) -> list[DialogueMessage]:
        """用户发言并触发一轮多 AI 响应,返回本轮新增的 AI/系统消息"""
        lock = await self._room_lock(room.room_id)
        async with lock:
            round_no = room.current_round + 1
            room.current_round = round_no
            self.storage.update_room(room)

            self.publish(
                room.room_id,
                DialogueEvent(type="turn_start", room_id=room.room_id, round=round_no, status="started"),
            )

            user_msg = DialogueMessage(
                message_id=new_message_id(),
                room_id=room.room_id,
                round=round_no,
                speaker="user",
                role="user",
                content=content,
            )
            self.storage.add_message(user_msg)
            self.publish(
                room.room_id,
                DialogueEvent(
                    type="message_end",
                    room_id=room.room_id,
                    round=round_no,
                    speaker="user",
                    final=content,
                    status="ok",
                ),
            )

            history: list[DialogueMessage] = self.storage.list_messages(
                room.room_id, limit=CONTEXT_HISTORY_LIMIT
            )

            if room.mode == DialogueMode.ROUND_ROBIN:
                new_msgs = await self._run_round_robin(room, round_no, history, stream)
            elif room.mode == DialogueMode.PARALLEL_THINK:
                new_msgs = await self._run_parallel_think(room, round_no, history, stream)
            elif room.mode == DialogueMode.FREE_TALK:
                new_msgs = await self._run_free_talk(room, round_no, history, stream)
            else:  # pragma: no cover — pydantic Literal 已挡住
                raise ValueError(f"unknown dialogue mode: {room.mode}")

            self.publish(
                room.room_id,
                DialogueEvent(
                    type="turn_complete",
                    room_id=room.room_id,
                    round=round_no,
                    status="done",
                    final=str(len(new_msgs)),
                ),
            )
            return new_msgs

    # ==================== 三种模式 ====================
    async def _run_round_robin(
        self,
        room: DialogueRoom,
        round_no: int,
        history: list[DialogueMessage],
        stream: bool,
    ) -> list[DialogueMessage]:
        """轮流发言: 每轮每个参与者按序真实调用,产出立即进入共享历史"""
        new_msgs: list[DialogueMessage] = []
        for step in range(1, room.max_rounds + 1):
            self.publish(
                room.room_id,
                DialogueEvent(
                    type="round_start", room_id=room.room_id, round=round_no, step=step,
                    status="started",
                ),
            )
            for p in room.participants:
                msg = await self._invoke_participant(room, p, round_no, history, stream, step)
                new_msgs.append(msg)
                if msg.status == MessageStatus.OK:
                    history.append(msg)  # 后续发言者能看到这条发言
            self.publish(
                room.room_id,
                DialogueEvent(
                    type="round_end", room_id=room.room_id, round=round_no, step=step, status="ok"
                ),
            )
        return new_msgs

    async def _run_parallel_think(
        self,
        room: DialogueRoom,
        round_no: int,
        history: list[DialogueMessage],
        stream: bool,
    ) -> list[DialogueMessage]:
        """并行思考: gather 所有参与者并行真实调用,全部返回后汇总进历史"""
        new_msgs: list[DialogueMessage] = []
        for step in range(1, room.max_rounds + 1):
            self.publish(
                room.room_id,
                DialogueEvent(
                    type="round_start", room_id=room.room_id, round=round_no, step=step,
                    status="started",
                ),
            )
            snapshot = list(history)  # 同轮内互不可见,保证真并行独立思考
            results = await asyncio.gather(
                *[
                    self._invoke_participant(room, p, round_no, snapshot, stream, step)
                    for p in room.participants
                ]
            )
            for msg in results:
                new_msgs.append(msg)
                if msg.status == MessageStatus.OK:
                    history.append(msg)  # 全部返回后汇总,每条独立可见
            self.publish(
                room.room_id,
                DialogueEvent(
                    type="round_end", room_id=room.room_id, round=round_no, step=step, status="ok"
                ),
            )
        return new_msgs

    async def _run_free_talk(
        self,
        room: DialogueRoom,
        round_no: int,
        history: list[DialogueMessage],
        stream: bool,
    ) -> list[DialogueMessage]:
        """自由讨论: 主持人 LLM 每轮决定下一个发言者,无进展自动收敛"""
        host = room.participants[0]
        valid_names = [p.name for p in room.participants]
        by_name = {p.name: p for p in room.participants}
        new_msgs: list[DialogueMessage] = []
        no_progress = 0
        same_speaker_streak = 0
        last_speaker: str | None = None

        for step in range(1, room.max_rounds + 1):
            self.publish(
                room.room_id,
                DialogueEvent(
                    type="round_start", room_id=room.room_id, round=round_no, step=step,
                    status="started",
                ),
            )
            decision, reason, host_msg = await self._host_decide(room, host, round_no, history, step)
            new_msgs.append(host_msg)
            if host_msg.status != MessageStatus.OK:
                no_progress += 1
                history.append(host_msg)
            elif decision is None:
                # 主持人明确收敛
                self.publish(
                    room.room_id,
                    DialogueEvent(
                        type="round_end", room_id=room.room_id, round=round_no, step=step,
                        status="converged", final=reason,
                    ),
                )
                break
            elif decision == "":
                # 无效输出 — 记录证据并计入无进展
                no_progress += 1
                history.append(host_msg)
            else:
                speaker = by_name[decision]
                msg = await self._invoke_participant(room, speaker, round_no, history, stream, step)
                new_msgs.append(msg)
                if msg.status == MessageStatus.OK and msg.content.strip():
                    no_progress = 0
                    history.append(msg)
                else:
                    no_progress += 1
                    history.append(msg)
                if last_speaker == decision:
                    same_speaker_streak += 1
                else:
                    same_speaker_streak = 1
                last_speaker = decision

            self.publish(
                room.room_id,
                DialogueEvent(
                    type="round_end", room_id=room.room_id, round=round_no, step=step, status="ok"
                ),
            )
            if no_progress >= NO_PROGRESS_LIMIT:
                self.publish(
                    room.room_id,
                    DialogueEvent(
                        type="round_end", room_id=room.room_id, round=round_no, step=step,
                        status="converged",
                        final=f"auto-converged: {no_progress} consecutive rounds without progress",
                    ),
                )
                break
            if same_speaker_streak >= SAME_SPEAKER_LIMIT:
                self.publish(
                    room.room_id,
                    DialogueEvent(
                        type="round_end", room_id=room.room_id, round=round_no, step=step,
                        status="converged",
                        final=f"auto-converged: {last_speaker} spoke {same_speaker_streak} "
                        "times in a row",
                    ),
                )
                break
        return new_msgs

    # ==================== 主持人 ====================
    async def _host_decide(
        self,
        room: DialogueRoom,
        host: Participant,
        round_no: int,
        history: list[DialogueMessage],
        step: int,
    ) -> tuple[str | None, str, DialogueMessage]:
        """真实调用主持人 LLM 决定下一个发言者。

        返回 (decision, reason, host_msg):
          decision = 参与者名 / None(收敛 END) / ""(无效输出)
        """
        transcript = "\n".join(self._format_line(m) for m in history[-30:])
        names = ", ".join(p.name for p in room.participants)
        prompt = (
            f"You are the moderator of a multi-AI discussion room.\n"
            f"Topic: {room.topic}\n"
            f"Participants: {names}\n"
            f"Transcript so far:\n{transcript}\n\n"
            "Decide who should speak next. If the discussion has reached a conclusion "
            'or is going in circles, output speaker "END".\n'
            'Respond with ONLY strict JSON: {"speaker": "<participant name or END>", '
            '"reason": "<one short sentence>"}'
        )
        t0 = time.monotonic()
        try:
            resp = await asyncio.wait_for(
                self.pool.call(
                    host.endpoint_id,
                    [{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=256,
                    max_retries=1,
                ),
                timeout=room.participant_timeout,
            )
        except asyncio.TimeoutError:
            msg = self._failure_message(
                room, host, round_no,
                f"moderator call exceeded {room.participant_timeout}s timeout",
                MessageStatus.TIMEOUT, latency_ms=(time.monotonic() - t0) * 1000,
            )
            self._emit_failure_events(room, round_no, host, msg, step)
            return "", "moderator timeout", msg
        except Exception as e:
            if not isinstance(e, ProviderError):
                logger.exception("free_talk moderator call failed: %s", e)
            msg = self._failure_message(
                room, host, round_no,
                f"moderator call failed: {type(e).__name__}: {e}",
                MessageStatus.ERROR, latency_ms=(time.monotonic() - t0) * 1000,
            )
            self._emit_failure_events(room, round_no, host, msg, step)
            return "", f"moderator failed: {e}", msg

        decision, reason = self._parse_host_decision(resp.content, [p.name for p in room.participants])
        ep = self.pool.get_endpoint(host.endpoint_id)
        msg = DialogueMessage(
            message_id=new_message_id(),
            room_id=room.room_id,
            round=round_no,
            speaker="moderator",
            role="system",
            content=json.dumps({"speaker": decision if decision is not None else "END",
                                "reason": reason}, ensure_ascii=False),
            endpoint_id=host.endpoint_id,
            status=MessageStatus.OK,
            mock=bool(ep is not None and self.pool._ep_is_mock(ep)),
            latency_ms=(time.monotonic() - t0) * 1000,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
        )
        self.storage.add_message(msg)
        self.publish(
            room.room_id,
            DialogueEvent(
                type="moderator", room_id=room.room_id, round=round_no, speaker="moderator",
                final=msg.content, status="ok", mock=msg.mock, step=step,
            ),
        )
        return decision, reason, msg

    @staticmethod
    def _parse_host_decision(text: str, valid_names: list[str]) -> tuple[str | None, str]:
        """解析主持人 JSON 输出 → (speaker|None=END|""=无效, reason)"""
        candidate = (text or "").strip()
        data: dict | None = None
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            m = _JSON_OBJECT_RE.search(candidate)
            if m:
                try:
                    data = json.loads(m.group(0))
                except (json.JSONDecodeError, ValueError):
                    data = None
        if not isinstance(data, dict):
            return "", "moderator output is not valid JSON"
        speaker = str(data.get("speaker", "")).strip()
        reason = str(data.get("reason", "")).strip()
        if not speaker:
            return "", "moderator output missing speaker"
        if speaker in valid_names:
            return speaker, reason
        if speaker.upper() in END_TOKENS:
            return None, reason or "moderator decided to converge"
        return "", f"moderator picked unknown speaker: {speaker}"

    # ==================== 参与者调用 ====================
    async def _invoke_participant(
        self,
        room: DialogueRoom,
        participant: Participant,
        round_no: int,
        history: list[DialogueMessage],
        stream: bool,
        step: int,
    ) -> DialogueMessage:
        """真实调用一个参与者的 endpoint;失败记录证据,绝不伪造内容"""
        ep = self.pool.get_endpoint(participant.endpoint_id)
        if ep is None or not ep.is_available or ep.provider_obj is None:
            reason = (
                f"endpoint {participant.endpoint_id} not found"
                if ep is None
                else f"endpoint {participant.endpoint_id} unavailable "
                f"(enabled={ep.config.enabled}, health={ep.health_status})"
            )
            msg = self._failure_message(room, participant, round_no, reason, MessageStatus.ERROR)
            self._emit_failure_events(room, round_no, participant, msg, step)
            return msg

        messages = self._build_context(room, participant, history)
        is_mock = self.pool._ep_is_mock(ep)
        self.publish(
            room.room_id,
            DialogueEvent(
                type="message_start", room_id=room.room_id, round=round_no,
                speaker=participant.name, status="started", mock=is_mock, step=step,
            ),
        )
        t0 = time.monotonic()
        try:
            if stream:
                content, pt, ct = await self._stream_call(room, ep, participant, round_no, messages, step)
            else:
                resp = await asyncio.wait_for(
                    self.pool.call(
                        participant.endpoint_id,
                        messages,
                        temperature=0.7,
                        max_tokens=2048,
                        max_retries=1,
                    ),
                    timeout=room.participant_timeout,
                )
                content, pt, ct = resp.content, resp.prompt_tokens, resp.completion_tokens
        except asyncio.TimeoutError:
            msg = self._failure_message(
                room, participant, round_no,
                f"participant call exceeded {room.participant_timeout}s timeout",
                MessageStatus.TIMEOUT, latency_ms=(time.monotonic() - t0) * 1000,
            )
            self._emit_failure_events(room, round_no, participant, msg, step)
            return msg
        except Exception as e:
            if not isinstance(e, ProviderError):
                logger.exception("dialogue participant call failed: %s", e)
            msg = self._failure_message(
                room, participant, round_no,
                f"participant call failed: {type(e).__name__}: {e}",
                MessageStatus.ERROR, latency_ms=(time.monotonic() - t0) * 1000,
            )
            self._emit_failure_events(room, round_no, participant, msg, step)
            return msg

        msg = DialogueMessage(
            message_id=new_message_id(),
            room_id=room.room_id,
            round=round_no,
            speaker=participant.name,
            role="assistant",
            content=content,
            endpoint_id=participant.endpoint_id,
            status=MessageStatus.OK,
            mock=is_mock,
            latency_ms=(time.monotonic() - t0) * 1000,
            prompt_tokens=pt,
            completion_tokens=ct,
        )
        self.storage.add_message(msg)
        self.publish(
            room.room_id,
            DialogueEvent(
                type="message_end", room_id=room.room_id, round=round_no,
                speaker=participant.name, final=content, status="ok", mock=is_mock, step=step,
            ),
        )
        return msg

    async def _stream_call(
        self, room, ep, participant, round_no, messages, step
    ) -> tuple[str, int, int]:
        """真实 token 流: provider.chat_stream 支持流就逐块推 delta 事件。

        基类 Provider.chat_stream 默认回退为整条 yield(不支持流的 provider
        自动变成整条推送),无需 mock。
        """
        provider = ep.provider_obj
        req = self.pool.build_chat_request(
            ep, messages, temperature=0.7, max_tokens=2048, stream=True
        )
        chunks: list[str] = []

        async def consume() -> None:
            async for chunk in provider.chat_stream(req):
                if not chunk:
                    continue
                chunks.append(chunk)
                self.publish(
                    room.room_id,
                    DialogueEvent(
                        type="delta", room_id=room.room_id, round=round_no,
                        speaker=participant.name, delta=chunk, status="streaming", step=step,
                    ),
                )

        await asyncio.wait_for(consume(), timeout=room.participant_timeout)
        content = "".join(chunks)
        completion_tokens = sum(max(1, len(c) // 4) for c in chunks)
        prompt_tokens = sum(len(str(m.get("content", ""))) for m in messages) // 4
        return content, prompt_tokens, completion_tokens

    # ==================== 上下文构造 ====================
    def _build_context(
        self, room: DialogueRoom, participant: Participant, history: list[DialogueMessage]
    ) -> list[dict]:
        """共享历史 → 该参与者的 messages(其他 AI 发言标注发言者)"""
        peers = ", ".join(p.name for p in room.participants if p.name != participant.name)
        system = (
            f"You are '{participant.name}', one participant in a multi-AI discussion room. "
            f"Topic: {room.topic}. Other participants: {peers}. "
            f"{participant.persona}".strip()
            + f" Respond as {participant.name} only, stay in character, be concise, and "
            "engage directly with what the other participants said."
        )
        msgs: list[dict] = [{"role": "system", "content": system}]
        for m in history:
            if m.status != MessageStatus.OK and m.role == "assistant":
                continue  # 失败证据不进上下文
            if m.role == "user":
                msgs.append({"role": "user", "content": m.content})
            elif m.role == "system":
                msgs.append({"role": "user", "content": f"[moderator]: {m.content}"})
            elif m.speaker == participant.name:
                msgs.append({"role": "assistant", "content": m.content})
            else:
                msgs.append({"role": "user", "content": f"[{m.speaker}]: {m.content}"})
        return msgs

    @staticmethod
    def _format_line(m: DialogueMessage) -> str:
        if m.status != MessageStatus.OK and m.role == "assistant":
            return f"{m.speaker}: [call failed: {m.error}]"
        return f"{m.speaker}: {m.content}"

    # ==================== 失败处理 ====================
    def _failure_message(
        self,
        room: DialogueRoom,
        participant: Participant,
        round_no: int,
        evidence: str,
        status: MessageStatus,
        latency_ms: float = 0.0,
    ) -> DialogueMessage:
        msg = DialogueMessage(
            message_id=new_message_id(),
            room_id=room.room_id,
            round=round_no,
            speaker=participant.name,
            role="assistant",
            content="",  # 不伪造内容,只留失败证据
            endpoint_id=participant.endpoint_id,
            status=status,
            error=evidence,
            latency_ms=latency_ms,
        )
        self.storage.add_message(msg)
        return msg

    def _emit_failure_events(
        self, room: DialogueRoom, round_no: int, participant: Participant,
        msg: DialogueMessage, step: int,
    ) -> None:
        self.publish(
            room.room_id,
            DialogueEvent(
                type="message_end", room_id=room.room_id, round=round_no,
                speaker=participant.name, final="", status=msg.status.value,
                error=msg.error, step=step,
            ),
        )


_engine_instance: DialogueEngine | None = None
_engine_lock = threading.Lock()


def get_dialogue_engine() -> DialogueEngine:
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                _engine_instance = DialogueEngine()
    return _engine_instance
