"""in_flight — In-Flight Transition detection (A-24) + Team Checkpoint Merge (A-25)

核心能力 (来自 06 moai-adk-multiagent in-flight + checkpoint merge):
  1. Phase enum: 5 阶段生命周期 (ANALYZE / IMPLEMENT / TEST / REVIEW / COMPLETE)
  2. PhaseState dataclass: 单 session 的阶段状态 (started_at / completed_at / interrupted / checkpoint_data)
  3. InFlightDetector:
     - record_start: 开启一个 phase, 分配 session_id, 持久化到 state_dir
     - record_complete: 关闭 phase (写入 completed_at)
     - record_interrupted: 标记 session 为中断 (不影响 started_at)
     - detect_in_flight: 找 started 但 !completed 的 session (真实过滤, 非 mock)
     - detect_phase_transition: 根据已有 phase history 推断下一步合法 phase
  4. Checkpoint dataclass: 团队 checkpoint 的单条记录 (session_id / phase / data / timestamp)
  5. TeamCheckpointMerger:
     - add_checkpoint: 收集一个 checkpoint
     - merge: 按 phase 维度聚合 (Run 类型累加 data; Plan / Sync 类型取最后写入)
     - to_dict: JSON 友好的 dict 视图
  6. JSON 序列化: 全 dataclass 支持 asdict + from_dict 重建

设计原则:
  - 所有判定基于真实状态查询 (started_at / completed_at / phase 序号) — 无 mock
  - 阶段转换用 PHASE_ORDER 列表 + 当前 phase 序号 +1 → 下一阶段; 已到 COMPLETE 则 None
  - 状态持久化到 .moai/state/in_flight.json, 失败时静默退化 (内存仍可用)
  - 累加 (Run) 仅累加可加的值 (int / float), 不可加的 key 取最后值
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "Phase",
    "PhaseState",
    "Checkpoint",
    "InFlightDetector",
    "TeamCheckpointMerger",
    "PHASE_ORDER",
    "phase_to_dict",
    "phase_from_str",
    "phase_state_to_dict",
    "phase_state_from_dict",
    "checkpoint_to_dict",
    "checkpoint_from_dict",
]


# ============ Phase 枚举与顺序 ============

class Phase(str, Enum):
    """5 阶段生命周期, 顺序固定 (ANALYZE → IMPLEMENT → TEST → REVIEW → COMPLETE)"""
    ANALYZE = "analyze"
    IMPLEMENT = "implement"
    TEST = "test"
    REVIEW = "review"
    COMPLETE = "complete"


# 全局顺序, 用于 detect_phase_transition 的"下一阶段"查找
PHASE_ORDER: list[str] = [Phase.ANALYZE.value, Phase.IMPLEMENT.value, Phase.TEST.value, Phase.REVIEW.value, Phase.COMPLETE.value]


# ============ Dataclass 定义 ============

@dataclass
class PhaseState:
    """单 session 的阶段状态"""
    phase: Phase
    started_at: float
    completed_at: float | None = None
    interrupted: bool = False
    checkpoint_data: dict = field(default_factory=dict)


@dataclass
class Checkpoint:
    """团队 checkpoint 的单条记录"""
    session_id: str
    phase: Phase
    data: dict
    timestamp: float = field(default_factory=time.time)


# ============ InFlightDetector ============

class InFlightDetector:
    """追踪 in-flight session, 持久化到 state_dir。

    内部以 session_id → PhaseState 列表 (一个 session 可有多个 phase) 维护状态。
    detect_in_flight 找出: 任意 PhaseState 满足 started_at 已设 且 completed_at 为空。
    """

    def __init__(self, state_dir: str = ".moai/state") -> None:
        self.state_dir = state_dir
        self.state_file = os.path.join(state_dir, "in_flight.json")
        # session_id -> List[PhaseState] (按时间序追加)
        self._states: dict[str, list[PhaseState]] = {}
        # 优先从磁盘恢复 (best effort, 失败不抛)
        self._load()

    def _load(self) -> None:
        """从 state_file 恢复状态。文件不存在或损坏 → 静默忽略。"""
        try:
            if not os.path.isfile(self.state_file):
                return
            with open(self.state_file, encoding="utf-8") as f:
                raw = json.load(f)
            # raw: {session_id: [PhaseState dict, ...]}
            self._states = {}
            for sid, items in raw.items():
                ps_list = []
                for item in items:
                    ps_list.append(phase_state_from_dict(item))
                self._states[sid] = ps_list
        except Exception:
            # 静默退化, 不影响新状态记录
            self._states = {}

    def _save(self) -> None:
        """持久化到磁盘。失败不抛 (避免污染主流程)。"""
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            raw = {}
            for sid, ps_list in self._states.items():
                raw[sid] = [phase_state_to_dict(ps) for ps in ps_list]
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def record_start(
        self,
        phase: Phase,
        at: float | None = None,
        session_id: str | None = None,
    ) -> str:
        """开启一个 phase, 分配 (或接受) session_id, 写入 started_at。

        参数:
          phase: 当前进入的阶段
          at: 起始时间戳 (None → 用 time.time())
          session_id: 自定义 session_id (None → 自动生成 uuid4 前 12 位)

        返回:
          session_id 字符串
        """
        sid = session_id if session_id is not None else uuid.uuid4().hex[:12]
        started = at if at is not None else time.time()
        ps = PhaseState(phase=phase, started_at=started)
        self._states.setdefault(sid, []).append(ps)
        self._save()
        return sid

    def record_complete(
        self,
        session_id: str,
        phase: Phase,
        at: float | None = None,
    ) -> None:
        """关闭 session 的当前 phase (写入 completed_at)。

        找不到对应 session 或 phase 失配 → 静默忽略 (调用方不感知错误)。
        """
        ps_list = self._states.get(session_id)
        if not ps_list:
            return
        # 找最后一个未完成且 phase 匹配的
        for ps in reversed(ps_list):
            if ps.phase == phase and ps.completed_at is None:
                ps.completed_at = at if at is not None else time.time()
                break
        self._save()

    def record_interrupted(self, session_id: str, reason: str) -> None:
        """标记 session 当前未完成 phase 为 interrupted, 并把 reason 写入 checkpoint_data。

        没找到对应 session → 静默忽略。
        """
        ps_list = self._states.get(session_id)
        if not ps_list:
            return
        # 找最后一个未完成 phase
        for ps in reversed(ps_list):
            if ps.completed_at is None:
                ps.interrupted = True
                ps.checkpoint_data["interrupt_reason"] = reason
                break
        self._save()

    def detect_in_flight(self, at: float | None = None) -> list[PhaseState]:
        """返回所有 started 但 !completed 的 PhaseState (跨 session 聚合)。

        参数 at: 当前时间 (可选, 仅用于将来扩展; 当前不参与判定, 但保留签名)。
        """
        result: list[PhaseState] = []
        for ps_list in self._states.values():
            for ps in ps_list:
                if ps.started_at > 0 and ps.completed_at is None:
                    result.append(ps)
        return result

    def detect_phase_transition(self, session_id: str) -> Phase | None:
        """推断 session 的下一合法 phase。

        规则:
          - session 不存在 → None
          - 最后一个已完成的 phase → 查 PHASE_ORDER 取下一个; 已到 COMPLETE → None
          - 最后一个未完成 phase → 仍是该 phase (尚未完成, 不能转换)
          - session 没有任何 phase → 第一个 phase (ANALYZE)
        """
        ps_list = self._states.get(session_id)
        if not ps_list:
            return None
        last = ps_list[-1]
        if last.completed_at is None:
            # 还在进行中, 下一阶段就是当前 phase (语义: 还没转出去)
            return last.phase
        # 已完成: 查 PHASE_ORDER 的下一项
        try:
            idx = PHASE_ORDER.index(last.phase.value)
        except ValueError:
            return None
        if idx + 1 >= len(PHASE_ORDER):
            return None
        return Phase(PHASE_ORDER[idx + 1])


# ============ TeamCheckpointMerger ============

class TeamCheckpointMerger:
    """多 session 的 checkpoint 按 phase 聚合。

    merge 规则:
      - Run 类型 (前缀 run_): 累加 data (int/float 加, 不可加取最后)
      - Plan / Sync 类型 (前缀 plan_ / sync_): 取最后一个写入
      - 其他 key: 取最后 (默认 fallback)
    """

    # 用于"累加"语义的 key 前缀
    _ACCUMULATE_PREFIXES: tuple = ("run_",)
    # 用于"取最后"语义的 key 前缀
    _LASTWRITE_PREFIXES: tuple = ("plan_", "sync_")

    def __init__(self) -> None:
        # session_id -> List[Checkpoint]
        self._checkpoints: dict[str, list[Checkpoint]] = {}

    def add_checkpoint(self, ckpt: Checkpoint) -> None:
        """追加一个 checkpoint。"""
        self._checkpoints.setdefault(ckpt.session_id, []).append(ckpt)

    def _merge_data(self, ckpts: list[Checkpoint]) -> dict:
        """按 key 前缀的语义合并一组 checkpoint 的 data。"""
        merged: dict = {}
        # 累加型 key 跟踪 int/float 累计值
        accum_values: dict[str, float] = {}
        # 最后写入型 key 跟踪是否已被设置 (用于决定是否覆盖)
        for ckpt in ckpts:
            for k, v in ckpt.data.items():
                if any(k.startswith(p) for p in self._ACCUMULATE_PREFIXES):
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        accum_values[k] = accum_values.get(k, 0.0) + float(v)
                    else:
                        # 不可加: 取最后
                        merged[k] = v
                elif any(k.startswith(p) for p in self._LASTWRITE_PREFIXES):
                    merged[k] = v
                else:
                    # 其他 key: 取最后
                    merged[k] = v
        # 合并累加结果
        merged.update(accum_values)
        return merged

    def merge(self) -> dict:
        """按 phase 维度合并, 返回 {phase_value: {session_id: merged_data, ...}, ...}。"""
        # phase_value -> session_id -> List[Checkpoint]
        by_phase: dict[str, dict[str, list[Checkpoint]]] = {}
        for sid, ckpts in self._checkpoints.items():
            for ckpt in ckpts:
                by_phase.setdefault(ckpt.phase.value, {}).setdefault(sid, []).append(ckpt)

        result: dict = {}
        for phase_value, sid_map in by_phase.items():
            phase_result: dict = {}
            for sid, ckpts in sid_map.items():
                phase_result[sid] = self._merge_data(ckpts)
            result[phase_value] = phase_result
        return result

    def to_dict(self) -> dict:
        """导出完整状态 (供 JSON 序列化)。

        结构:
          {
            "checkpoints": [checkpoint dict, ...],
            "merged": {phase_value: {session_id: merged_data, ...}, ...}
          }
        """
        all_ckpts: list[dict] = []
        for sid, ckpts in self._checkpoints.items():
            for ckpt in ckpts:
                d = checkpoint_to_dict(ckpt)
                d["session_id"] = sid  # 显式带上 session_id
                all_ckpts.append(d)
        return {
            "checkpoints": all_ckpts,
            "merged": self.merge(),
        }


# ============ JSON 序列化 ============

def phase_to_dict(phase: Phase) -> str:
    """Phase → str (enum value)"""
    return phase.value


def phase_from_str(s: str) -> Phase:
    """str → Phase, 找不到则抛 ValueError"""
    for p in Phase:
        if p.value == s:
            return p
    raise ValueError(f"Unknown phase: {s!r}")


def phase_state_to_dict(ps: PhaseState) -> dict:
    """PhaseState → dict (Phase 转 value, datetime 用 float 保持)。"""
    return {
        "phase": ps.phase.value,
        "started_at": ps.started_at,
        "completed_at": ps.completed_at,
        "interrupted": ps.interrupted,
        "checkpoint_data": dict(ps.checkpoint_data),
    }


def phase_state_from_dict(d: dict) -> PhaseState:
    """dict → PhaseState (宽容地容忍缺失字段)。"""
    return PhaseState(
        phase=phase_from_str(d.get("phase", Phase.ANALYZE.value)),
        started_at=float(d.get("started_at", 0.0)),
        completed_at=(float(d["completed_at"]) if d.get("completed_at") is not None else None),
        interrupted=bool(d.get("interrupted", False)),
        checkpoint_data=dict(d.get("checkpoint_data", {})),
    )


def checkpoint_to_dict(ckpt: Checkpoint) -> dict:
    """Checkpoint → dict"""
    return {
        "phase": ckpt.phase.value,
        "data": dict(ckpt.data),
        "timestamp": ckpt.timestamp,
    }


def checkpoint_from_dict(d: dict) -> Checkpoint:
    """dict → Checkpoint (session_id 需调用方单独提供)"""
    return Checkpoint(
        session_id=d.get("session_id", ""),
        phase=phase_from_str(d.get("phase", Phase.ANALYZE.value)),
        data=dict(d.get("data", {})),
        timestamp=float(d.get("timestamp", time.time())),
    )
