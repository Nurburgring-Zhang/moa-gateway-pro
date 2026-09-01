"""moa_gateway.dialogue.storage — 对话房间/消息 SQLite 持久化.

复用现有 database/storage 模式:
- DatabaseEngine 工厂(SQLite 默认 WAL / PostgreSQL 可选)
- 与主 Storage 同一个库文件(ROOT_DIR / settings.storage.db_path),重启可恢复
- 建表用 IF NOT EXISTS,老库平滑升级
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from pathlib import Path

from .. import config as _cfg
from ..database import DatabaseEngine
from .models import DialogueMessage, DialogueMode, DialogueRoom, MessageStatus, Participant, RoomStatus

logger = logging.getLogger(__name__)

DIALOGUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS dialogue_rooms (
    room_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    participants TEXT NOT NULL,
    max_rounds INTEGER NOT NULL DEFAULT 2,
    participant_timeout REAL NOT NULL DEFAULT 60,
    current_round INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS dialogue_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT UNIQUE NOT NULL,
    room_id TEXT NOT NULL,
    round INTEGER NOT NULL DEFAULT 0,
    speaker TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    endpoint_id TEXT,
    status TEXT NOT NULL DEFAULT 'ok',
    mock INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    latency_ms REAL NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dialogue_messages_room ON dialogue_messages(room_id, id);
CREATE INDEX IF NOT EXISTS idx_dialogue_rooms_status ON dialogue_rooms(status);
"""


def new_room_id() -> str:
    return "room_" + secrets.token_hex(8)


def new_message_id() -> str:
    return "dmsg_" + secrets.token_hex(8)


class DialogueStorage:
    """对话房间持久化层(与主 Storage 同库,独立表)"""

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            settings = _cfg.get_settings()
            db_path = _cfg.ROOT_DIR / settings.storage.db_path
        self.db_path = Path(db_path)
        self._engine = DatabaseEngine.create(db_path=self.db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self._engine.execute_script(DIALOGUE_SCHEMA)

    # ========== Rooms ==========
    def create_room(self, room: DialogueRoom) -> DialogueRoom:
        with self._engine.conn() as c:
            c.execute(
                "INSERT INTO dialogue_rooms (room_id, topic, mode, status, participants, "
                "max_rounds, participant_timeout, current_round, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    room.room_id,
                    room.topic,
                    room.mode.value,
                    room.status.value,
                    json.dumps([p.model_dump() for p in room.participants], ensure_ascii=False),
                    room.max_rounds,
                    room.participant_timeout,
                    room.current_round,
                    room.created_at,
                    room.updated_at,
                ),
            )
        return room

    def get_room(self, room_id: str) -> DialogueRoom | None:
        with self._engine.conn() as c:
            row = c.execute("SELECT * FROM dialogue_rooms WHERE room_id = ?", (room_id,)).fetchone()
        if not row:
            return None
        return self._row_to_room(row)

    def list_rooms(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[DialogueRoom]:
        sql = "SELECT * FROM dialogue_rooms"
        args: list = []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        args.extend([limit, offset])
        with self._engine.conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [self._row_to_room(r) for r in rows]

    def update_room(self, room: DialogueRoom) -> bool:
        room.updated_at = time.time()
        with self._engine.conn() as c:
            cur = c.execute(
                "UPDATE dialogue_rooms SET topic = ?, mode = ?, status = ?, participants = ?, "
                "max_rounds = ?, participant_timeout = ?, current_round = ?, updated_at = ? "
                "WHERE room_id = ?",
                (
                    room.topic,
                    room.mode.value,
                    room.status.value,
                    json.dumps([p.model_dump() for p in room.participants], ensure_ascii=False),
                    room.max_rounds,
                    room.participant_timeout,
                    room.current_round,
                    room.updated_at,
                    room.room_id,
                ),
            )
            return cur.rowcount > 0

    def delete_room(self, room_id: str) -> bool:
        with self._engine.conn() as c:
            c.execute("DELETE FROM dialogue_messages WHERE room_id = ?", (room_id,))
            cur = c.execute("DELETE FROM dialogue_rooms WHERE room_id = ?", (room_id,))
            return cur.rowcount > 0

    @staticmethod
    def _row_to_room(row) -> DialogueRoom:
        d = dict(row)
        participants = [Participant(**p) for p in json.loads(d["participants"])]
        return DialogueRoom(
            room_id=d["room_id"],
            topic=d["topic"],
            mode=DialogueMode(d["mode"]),
            status=RoomStatus(d["status"]),
            participants=participants,
            max_rounds=d["max_rounds"],
            participant_timeout=d["participant_timeout"],
            current_round=d["current_round"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )

    # ========== Messages ==========
    def add_message(self, msg: DialogueMessage) -> DialogueMessage:
        with self._engine.conn() as c:
            c.execute(
                "INSERT INTO dialogue_messages (message_id, room_id, round, speaker, role, "
                "content, endpoint_id, status, mock, error, latency_ms, prompt_tokens, "
                "completion_tokens, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    msg.message_id,
                    msg.room_id,
                    msg.round,
                    msg.speaker,
                    msg.role,
                    msg.content,
                    msg.endpoint_id,
                    msg.status.value,
                    1 if msg.mock else 0,
                    msg.error,
                    msg.latency_ms,
                    msg.prompt_tokens,
                    msg.completion_tokens,
                    msg.created_at,
                ),
            )
        return msg

    def list_messages(
        self, room_id: str, limit: int = 100, offset: int = 0, ascending: bool = True
    ) -> list[DialogueMessage]:
        order = "ASC" if ascending else "DESC"
        with self._engine.conn() as c:
            rows = c.execute(
                f"SELECT * FROM dialogue_messages WHERE room_id = ? "  # noqa: S608 (order 白名单)
                f"ORDER BY id {order} LIMIT ? OFFSET ?",
                (room_id, limit, offset),
            ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def count_messages(self, room_id: str) -> int:
        with self._engine.conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM dialogue_messages WHERE room_id = ?", (room_id,)
            ).fetchone()
        return int(row["n"]) if row else 0

    @staticmethod
    def _row_to_message(row) -> DialogueMessage:
        d = dict(row)
        return DialogueMessage(
            message_id=d["message_id"],
            room_id=d["room_id"],
            round=d["round"],
            speaker=d["speaker"],
            role=d["role"],
            content=d["content"],
            endpoint_id=d["endpoint_id"],
            status=MessageStatus(d["status"]),
            mock=bool(d["mock"]),
            error=d["error"],
            latency_ms=d["latency_ms"],
            prompt_tokens=d["prompt_tokens"],
            completion_tokens=d["completion_tokens"],
            created_at=d["created_at"],
        )


_ds_instance: DialogueStorage | None = None
_ds_lock = threading.Lock()


def get_dialogue_storage() -> DialogueStorage:
    global _ds_instance
    if _ds_instance is None:
        with _ds_lock:
            if _ds_instance is None:
                _ds_instance = DialogueStorage()
    return _ds_instance
