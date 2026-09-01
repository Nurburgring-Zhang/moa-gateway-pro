"""moa_gateway.dialogue.models — 多 AI 对话房间的 Pydantic 领域模型.

纪律: 所有模型 extra="forbid",未知字段直接拒绝(与 req_models 一致)。
"""

from __future__ import annotations

import time
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class DialogueMode(str, Enum):
    """对话编排模式"""

    ROUND_ROBIN = "round_robin"  # 轮流发言
    PARALLEL_THINK = "parallel_think"  # 并行思考后汇总
    FREE_TALK = "free_talk"  # 主持人 LLM 自主决定发言者


class RoomStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class MessageStatus(str, Enum):
    OK = "ok"
    ERROR = "error"  # 调用失败 — 记录真实失败证据,绝不伪造内容
    TIMEOUT = "timeout"  # 单参与者超时


class Participant(BaseModel):
    """对话参与者 — 绑定一个真实 model endpoint + 人设"""

    model_config = ConfigDict(extra="forbid")

    endpoint_id: str = Field(..., min_length=1, description="model_pool 里的真实 endpoint id")
    name: str = Field(..., min_length=1, description="显示名(发言者标注)")
    persona: str = Field("", description="人设系统提示")


class DialogueRoom(BaseModel):
    """对话房间"""

    model_config = ConfigDict(extra="forbid")

    room_id: str = Field(..., min_length=1)
    topic: str = Field(..., min_length=1)
    mode: DialogueMode = DialogueMode.ROUND_ROBIN
    status: RoomStatus = RoomStatus.ACTIVE
    participants: list[Participant] = Field(..., min_length=1)
    max_rounds: int = Field(2, ge=1, le=20, description="单次用户发言触发的最大轮数上限")
    participant_timeout: float = Field(60.0, ge=1, le=600, description="单参与者调用超时(秒)")
    current_round: int = Field(0, ge=0)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class DialogueMessage(BaseModel):
    """对话历史中的一条消息"""

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(..., min_length=1)
    room_id: str = Field(..., min_length=1)
    round: int = Field(0, ge=0, description="所属轮次(用户发言触发轮)")
    speaker: str = Field(..., min_length=1, description='参与者显示名 / "user" / "moderator"')
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str = ""
    endpoint_id: str | None = None
    status: MessageStatus = MessageStatus.OK
    mock: bool = Field(False, description="该条是否由 MockProvider 产出(显式标注)")
    error: str | None = Field(None, description="失败证据(status != ok 时必填)")
    latency_ms: float = 0.0
    prompt_tokens: int = Field(0, ge=0)
    completion_tokens: int = Field(0, ge=0)
    created_at: float = Field(default_factory=time.time)


class DialogueEvent(BaseModel):
    """事件流事件 — 每条发言产生 {room_id, round, speaker, delta/final, status}

    type 取值:
      turn_start / round_start / message_start / delta / message_end /
      moderator / round_end / turn_complete
    """

    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., min_length=1)
    room_id: str = Field(..., min_length=1)
    round: int = 0
    speaker: str = ""
    delta: str | None = None
    final: str | None = None
    status: str = "ok"
    mock: bool = False
    error: str | None = None
    step: int | None = None
    ts: float = Field(default_factory=time.time)
