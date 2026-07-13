"""in_flight 单元测试 (>= 16 测试, 真实 assert, 严禁 mock)

覆盖:
  - Phase enum 5 阶段
  - PhaseState / Checkpoint dataclass 字段
  - InFlightDetector 初始化 / record_start / record_complete / record_interrupted
  - detect_in_flight (0 session / 多 session / 过滤已完成)
  - detect_phase_transition (未完成 / 已完成推进 / 越界 → None)
  - TeamCheckpointMerger.add_checkpoint / merge (Run 累加 / Plan+Sync 取最后) / to_dict
  - 时间戳 default
  - JSON 序列化 (PhaseState / Checkpoint)
  - 多 Phase 独立
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.in_flight import (
    Phase,
    PhaseState,
    Checkpoint,
    InFlightDetector,
    TeamCheckpointMerger,
    PHASE_ORDER,
    phase_to_dict,
    phase_from_str,
    phase_state_to_dict,
    phase_state_from_dict,
    checkpoint_to_dict,
    checkpoint_from_dict,
)


# ============ 工具: 临时 state_dir fixture ============

@pytest.fixture
def tmp_state_dir():
    """每个测试独享一个临时目录, 避免污染与互相干扰。"""
    with tempfile.TemporaryDirectory() as d:
        yield d


# ============ Phase enum ============

def test_phase_enum_has_five_values():
    assert len(list(Phase)) == 5


def test_phase_enum_values():
    assert Phase.ANALYZE.value == "analyze"
    assert Phase.IMPLEMENT.value == "implement"
    assert Phase.TEST.value == "test"
    assert Phase.REVIEW.value == "review"
    assert Phase.COMPLETE.value == "complete"


def test_phase_order_is_monotonic():
    """PHASE_ORDER 必须按 ANALYZE→IMPLEMENT→TEST→REVIEW→COMPLETE 排列"""
    assert PHASE_ORDER == ["analyze", "implement", "test", "review", "complete"]


def test_phase_from_str_roundtrip():
    for p in Phase:
        assert phase_from_str(phase_to_dict(p)) == p


def test_phase_from_str_unknown_raises():
    with pytest.raises(ValueError):
        phase_from_str("nonexistent")


# ============ PhaseState dataclass ============

def test_phase_state_default_fields():
    ps = PhaseState(phase=Phase.ANALYZE, started_at=1.0)
    assert ps.phase == Phase.ANALYZE
    assert ps.started_at == 1.0
    assert ps.completed_at is None
    assert ps.interrupted is False
    assert ps.checkpoint_data == {}


# ============ InFlightDetector 初始化 ============

def test_detector_init_creates_empty_state(tmp_state_dir):
    det = InFlightDetector(state_dir=tmp_state_dir)
    assert det._states == {}
    assert det.detect_in_flight() == []


# ============ record_start + detect_in_flight ============

def test_record_start_returns_session_id(tmp_state_dir):
    det = InFlightDetector(state_dir=tmp_state_dir)
    sid = det.record_start(Phase.ANALYZE, at=100.0)
    assert isinstance(sid, str)
    assert len(sid) >= 8


def test_record_start_and_detect_in_flight(tmp_state_dir):
    det = InFlightDetector(state_dir=tmp_state_dir)
    sid = det.record_start(Phase.IMPLEMENT, at=200.0)
    in_flight = det.detect_in_flight()
    assert len(in_flight) == 1
    assert in_flight[0].phase == Phase.IMPLEMENT
    assert in_flight[0].started_at == 200.0
    assert in_flight[0].completed_at is None


def test_record_complete_removes_from_in_flight(tmp_state_dir):
    det = InFlightDetector(state_dir=tmp_state_dir)
    sid = det.record_start(Phase.TEST, at=300.0)
    det.record_complete(sid, Phase.TEST, at=350.0)
    assert det.detect_in_flight() == []


def test_record_interrupted_keeps_in_flight_with_flag(tmp_state_dir):
    det = InFlightDetector(state_dir=tmp_state_dir)
    sid = det.record_start(Phase.REVIEW, at=400.0)
    det.record_interrupted(sid, reason="user_cancelled")
    in_flight = det.detect_in_flight()
    assert len(in_flight) == 1
    assert in_flight[0].interrupted is True
    assert in_flight[0].checkpoint_data.get("interrupt_reason") == "user_cancelled"


def test_detect_in_flight_multiple_sessions(tmp_state_dir):
    det = InFlightDetector(state_dir=tmp_state_dir)
    sid1 = det.record_start(Phase.ANALYZE, at=1.0)
    sid2 = det.record_start(Phase.IMPLEMENT, at=2.0)
    sid3 = det.record_start(Phase.TEST, at=3.0)
    # 完成其中一个
    det.record_complete(sid2, Phase.IMPLEMENT, at=4.0)
    in_flight = det.detect_in_flight()
    phases = sorted(ps.phase.value for ps in in_flight)
    assert phases == ["analyze", "test"]


def test_detect_in_flight_empty_no_sessions(tmp_state_dir):
    det = InFlightDetector(state_dir=tmp_state_dir)
    assert det.detect_in_flight() == []


# ============ detect_phase_transition ============

def test_detect_transition_in_progress_returns_current(tmp_state_dir):
    """session 还在某个 phase 中, 下一阶段判定返回该 phase (语义: 还没转出去)"""
    det = InFlightDetector(state_dir=tmp_state_dir)
    sid = det.record_start(Phase.IMPLEMENT, at=1.0)
    assert det.detect_phase_transition(sid) == Phase.IMPLEMENT


def test_detect_transition_completed_advances(tmp_state_dir):
    """完成 ANALYZE → 下一阶段 IMPLEMENT"""
    det = InFlightDetector(state_dir=tmp_state_dir)
    sid = det.record_start(Phase.ANALYZE, at=1.0)
    det.record_complete(sid, Phase.ANALYZE, at=2.0)
    # 这里需要新建一个 IMPLEMENT 段才能继续推进; 直接查"已完成 ANALYZE 的下一阶段"
    # 实现语义: 最后一个已完成的 phase 的下一项 → IMPLEMENT
    assert det.detect_phase_transition(sid) == Phase.IMPLEMENT


def test_detect_transition_at_complete_returns_none(tmp_state_dir):
    """已完成 COMPLETE → 下一阶段为 None (已结束)"""
    det = InFlightDetector(state_dir=tmp_state_dir)
    sid = det.record_start(Phase.COMPLETE, at=1.0)
    det.record_complete(sid, Phase.COMPLETE, at=2.0)
    assert det.detect_phase_transition(sid) is None


def test_detect_transition_unknown_session_returns_none(tmp_state_dir):
    det = InFlightDetector(state_dir=tmp_state_dir)
    assert det.detect_phase_transition("nope") is None


# ============ 多 Phase 独立 ============

def test_multi_phase_independent_state(tmp_state_dir):
    """同一 session 可有多个 PhaseState, 互相不干扰。"""
    det = InFlightDetector(state_dir=tmp_state_dir)
    sid = det.record_start(Phase.ANALYZE, at=1.0)
    det.record_complete(sid, Phase.ANALYZE, at=2.0)
    det.record_start(Phase.IMPLEMENT, at=3.0, session_id=sid)
    # ANALYZE 已完成, IMPLEMENT 未完成
    in_flight = det.detect_in_flight()
    assert len(in_flight) == 1
    assert in_flight[0].phase == Phase.IMPLEMENT
    # 转换: 最后一个已完成的 phase 是 IMPLEMENT(未完成)→ 仍是 IMPLEMENT
    assert det.detect_phase_transition(sid) == Phase.IMPLEMENT


# ============ Checkpoint dataclass ============

def test_checkpoint_default_timestamp_close_to_now():
    """Checkpoint 默认 timestamp 应接近 time.time()"""
    before = time.time()
    ckpt = Checkpoint(session_id="s1", phase=Phase.ANALYZE, data={"k": 1})
    after = time.time()
    assert before <= ckpt.timestamp <= after


def test_checkpoint_explicit_timestamp_preserved():
    ckpt = Checkpoint(session_id="s1", phase=Phase.TEST, data={}, timestamp=12345.0)
    assert ckpt.timestamp == 12345.0


# ============ TeamCheckpointMerger ============

def test_merger_add_and_merge_run_accumulates(tmp_state_dir):
    """Run 类型 (前缀 run_) 的 data 应累加。"""
    m = TeamCheckpointMerger()
    m.add_checkpoint(Checkpoint(session_id="alice", phase=Phase.IMPLEMENT,
                                 data={"run_tokens": 100, "run_steps": 3}))
    m.add_checkpoint(Checkpoint(session_id="alice", phase=Phase.IMPLEMENT,
                                 data={"run_tokens": 50, "run_steps": 2}))
    m.add_checkpoint(Checkpoint(session_id="bob", phase=Phase.IMPLEMENT,
                                 data={"run_tokens": 30, "run_steps": 1}))
    merged = m.merge()
    # phase=implement
    assert "implement" in merged
    # alice 累加: tokens=150, steps=5
    assert merged["implement"]["alice"]["run_tokens"] == 150
    assert merged["implement"]["alice"]["run_steps"] == 5
    # bob: tokens=30, steps=1
    assert merged["implement"]["bob"]["run_tokens"] == 30
    assert merged["implement"]["bob"]["run_steps"] == 1


def test_merger_merge_plan_and_sync_last_write():
    """Plan / Sync 类型 (前缀 plan_ / sync_) 取最后写入。"""
    m = TeamCheckpointMerger()
    m.add_checkpoint(Checkpoint(session_id="alice", phase=Phase.ANALYZE,
                                 data={"plan_strategy": "v1", "sync_version": "0.1"}))
    m.add_checkpoint(Checkpoint(session_id="alice", phase=Phase.ANALYZE,
                                 data={"plan_strategy": "v2", "sync_version": "0.2"}))
    merged = m.merge()
    # 最后写入胜出
    assert merged["analyze"]["alice"]["plan_strategy"] == "v2"
    assert merged["analyze"]["alice"]["sync_version"] == "0.2"


def test_merger_to_dict_structure():
    m = TeamCheckpointMerger()
    m.add_checkpoint(Checkpoint(session_id="s1", phase=Phase.TEST,
                                 data={"run_tokens": 10, "plan_x": "a"}))
    m.add_checkpoint(Checkpoint(session_id="s1", phase=Phase.TEST,
                                 data={"run_tokens": 5, "plan_x": "b"}))
    d = m.to_dict()
    assert "checkpoints" in d
    assert "merged" in d
    assert len(d["checkpoints"]) == 2
    # run_tokens 累加
    assert d["merged"]["test"]["s1"]["run_tokens"] == 15
    # plan_x 取最后
    assert d["merged"]["test"]["s1"]["plan_x"] == "b"


# ============ JSON 序列化 ============

def test_phase_state_json_roundtrip(tmp_state_dir):
    ps = PhaseState(phase=Phase.IMPLEMENT, started_at=10.0, completed_at=20.0,
                    interrupted=True, checkpoint_data={"k": "v"})
    d = phase_state_to_dict(ps)
    j = json.dumps(d)
    d2 = json.loads(j)
    ps2 = phase_state_from_dict(d2)
    assert ps2.phase == Phase.IMPLEMENT
    assert ps2.started_at == 10.0
    assert ps2.completed_at == 20.0
    assert ps2.interrupted is True
    assert ps2.checkpoint_data == {"k": "v"}


def test_checkpoint_json_roundtrip():
    ckpt = Checkpoint(session_id="s1", phase=Phase.REVIEW, data={"a": 1}, timestamp=42.0)
    d = checkpoint_to_dict(ckpt)
    d["session_id"] = "s1"  # 模拟外部带 session_id
    j = json.dumps(d)
    d2 = json.loads(j)
    ckpt2 = checkpoint_from_dict(d2)
    assert ckpt2.session_id == "s1"
    assert ckpt2.phase == Phase.REVIEW
    assert ckpt2.data == {"a": 1}
    assert ckpt2.timestamp == 42.0


# ============ 持久化往返 (磁盘) ============

def test_detector_persists_state_across_instances(tmp_state_dir):
    """关闭 detector 再开, 状态应从磁盘恢复。"""
    det1 = InFlightDetector(state_dir=tmp_state_dir)
    sid = det1.record_start(Phase.IMPLEMENT, at=100.0)
    # 新实例, 同一目录
    det2 = InFlightDetector(state_dir=tmp_state_dir)
    in_flight = det2.detect_in_flight()
    assert len(in_flight) == 1
    assert in_flight[0].phase == Phase.IMPLEMENT
    assert in_flight[0].started_at == 100.0
