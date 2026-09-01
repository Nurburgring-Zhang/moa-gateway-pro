"""moa_gateway.dialogue — 多 AI 同框对话 (Multi-AI Dialogue Room).

多个 AI 参与者在同一个对话房间内围绕主题实时发言:
- round_robin:    轮流发言,每轮每个参与者按序真实调用 LLM
- parallel_think: 所有参与者并行真实调用,全部返回后汇总进历史
- free_talk:      主持人 LLM 自主决定下一个发言者,连续无进展自动收敛

所有发言都通过 model_pool.call 走真实 LLM endpoint;没有可用真实
endpoint 时按 settings.mock.mode 由 MockProvider 兜底并显式标注 mock。
"""

from .engine import DialogueEngine, get_dialogue_engine
from .models import (
    DialogueEvent,
    DialogueMessage,
    DialogueMode,
    DialogueRoom,
    Participant,
    RoomStatus,
)
from .storage import DialogueStorage, get_dialogue_storage

__all__ = [
    "DialogueEngine",
    "get_dialogue_engine",
    "DialogueEvent",
    "DialogueMessage",
    "DialogueMode",
    "DialogueRoom",
    "Participant",
    "RoomStatus",
    "DialogueStorage",
    "get_dialogue_storage",
]
