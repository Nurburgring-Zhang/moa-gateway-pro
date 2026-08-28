"""O5 — Reinforcer: 任务结果 -> 能力评分反馈(持久化)。

每次编排运行后, 把被调用能力的 成功/失败/延迟 记录持久化, 形成能力评分,
供 Planner 未来做更优匹配(强化信号)。真实持久化到 data/orchestrator_scores.json。

评分模型(真实、可复算):
  score = success_rate * 0.7 + speed_score * 0.3
  success_rate = successes / runs
  speed_score  = 1 - min(avg_latency_ms / 5000, 1)   # 越快越高
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path("data") / "orchestrator_scores.json"


class Reinforcer:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_PATH
        self._lock = threading.Lock()
        self._scores: dict[str, dict[str, Any]] = self._load()

    # ---- 持久化 ----
    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            if self._path.exists():
                return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("reinforcer load failed: %s", e)
        return {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._scores, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as e:  # noqa: BLE001
            logger.warning("reinforcer save failed: %s", e)

    # ---- 记录 ----
    def record(self, capability_id: str, ok: bool, latency_ms: float = 0.0) -> None:
        with self._lock:
            s = self._scores.setdefault(
                capability_id,
                {"runs": 0, "successes": 0, "failures": 0, "total_latency_ms": 0.0, "last_at": 0.0, "score": 0.5},
            )
            s["runs"] += 1
            if ok:
                s["successes"] += 1
            else:
                s["failures"] += 1
            s["total_latency_ms"] += float(latency_ms or 0.0)
            s["last_at"] = time.time()
            s["score"] = self._compute_score(s)
            self._save()

    def record_run(self, trace: list[dict[str, Any]], step_results: dict[str, Any]) -> int:
        """从一次编排执行的 trace/step_results 记录每个能力的结果。返回记录条数。"""
        n = 0
        for t in trace:
            cap_id = t.get("capability_id")
            if not cap_id:
                continue
            res = step_results.get(t.get("step_id"), {}) or {}
            ok = bool(res.get("ok"))
            latency = float(res.get("latency_ms", 0.0) or 0.0)
            self.record(cap_id, ok, latency)
            n += 1
        return n

    # ---- 查询 ----
    def _compute_score(self, s: dict[str, Any]) -> float:
        runs = max(1, s.get("runs", 0))
        success_rate = s.get("successes", 0) / runs
        avg_latency = s.get("total_latency_ms", 0.0) / runs
        speed_score = 1.0 - min(avg_latency / 5000.0, 1.0)
        return round(success_rate * 0.7 + speed_score * 0.3, 4)

    def get_score(self, capability_id: str) -> float:
        s = self._scores.get(capability_id)
        return float(s.get("score", 0.5)) if s else 0.5

    def get_scores(self) -> dict[str, dict[str, Any]]:
        return dict(self._scores)


_reinforcer: Reinforcer | None = None


def get_reinforcer() -> Reinforcer:
    global _reinforcer
    if _reinforcer is None:
        _reinforcer = Reinforcer()
    return _reinforcer
