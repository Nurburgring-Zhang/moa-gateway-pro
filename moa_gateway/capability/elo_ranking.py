"""elo_ranking — Elo 评分 + Bootstrap CI + Worker 调度 (来自 02 MoA-together-ai)

核心能力:
  1. Bradley-Terry Elo 评分 (K=4 默认):E_a = 1 / (1 + 10^((R_b - R_a)/400))
  2. Bootstrap CI 95%:对 match 序列重采样 n_resamples 次,得到每模型 final rating 分布
  3. WorkerPool 调度:lottery (随机) / shortest_queue (最小负载)

设计原则:
  - 所有算法基于数学/统计(无 mock、无 hardcoded)
  - Bradley-Terry 是经典 Elo 期望胜率公式
  - Bootstrap 重采样:每次随机抽 n 次 (有放回),重算 Elo 后取分位数
  - CI:对 ci=0.95 取 (2.5%, 97.5%) 分位数
"""
from __future__ import annotations

import json
import math
import random
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, List, Literal, Optional, Tuple


# ============ 数据模型 ============
@dataclass
class MatchResult:
    """单场比赛结果"""
    winner_id: str
    loser_id: str
    timestamp: float


@dataclass
class EloRating:
    """单个模型的 Elo 评分"""
    model_id: str
    rating: float = 1500.0
    matches_played: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)


# ============ 工具函数 ============
def _expected_score(rating_a: float, rating_b: float) -> float:
    """Bradley-Terry 期望胜率

    E_a = 1 / (1 + 10^((R_b - R_a)/400))

    当 R_a = R_b 时 E_a = 0.5
    R_a 比 R_b 高 400 → E_a = 10/11 ≈ 0.909
    """
    return 1.0 / (1.0 + math.pow(10.0, (rating_b - rating_a) / 400.0))


def _update_rating(
    rating: float, expected: float, actual: float, k: float
) -> float:
    """Elo 评分更新

    new = old + K * (actual - expected)
    - actual=1.0 (赢),0.5 (平),0.0 (输)
    - 单局最大变化 = K (当 actual=1, expected=0 或 actual=0, expected=1)
    """
    return rating + k * (actual - expected)


# ============ Elo 排行榜 ============
class EloLeaderboard:
    """Elo 排行榜 — Bradley-Terry

    用法:
        lb = EloLeaderboard(k_factor=4.0)
        lb.add_model("gpt-4")
        lb.add_model("claude-3")
        lb.record_match("gpt-4", "claude-3")
        print(lb.ranked())
    """

    def __init__(self, k_factor: float = 4.0):
        if k_factor <= 0:
            raise ValueError(f"k_factor must be > 0, got {k_factor}")
        self.k_factor = float(k_factor)
        self._ratings: Dict[str, EloRating] = {}

    def add_model(self, model_id: str, initial_rating: float = 1500.0) -> None:
        """添加模型。重复添加则重置 rating 和 matches_played"""
        self._ratings[model_id] = EloRating(
            model_id=model_id,
            rating=float(initial_rating),
            matches_played=0,
        )

    def _ensure_known(self, model_id: str) -> None:
        """内部:若 model 未注册,自动以 1500 注册 (不计入 matches_played)"""
        if model_id not in self._ratings:
            self._ratings[model_id] = EloRating(
                model_id=model_id, rating=1500.0, matches_played=0
            )

    def record_match(
        self,
        winner_id: str,
        loser_id: str,
        timestamp: Optional[float] = None,
    ) -> MatchResult:
        """记录一场比赛并更新 Elo

        - winner 涨分,loser 跌分(理论上)
        - 单局最大变化 = K (在极端期望反差下)
        - 自动注册未知的 model_id (默认 1500)
        """
        if winner_id == loser_id:
            raise ValueError(f"winner and loser must differ, both = {winner_id}")
        self._ensure_known(winner_id)
        self._ensure_known(loser_id)

        r_w = self._ratings[winner_id].rating
        r_l = self._ratings[loser_id].rating
        e_w = _expected_score(r_w, r_l)
        e_l = 1.0 - e_w  # 守恒:E_a + E_b = 1

        new_w = _update_rating(r_w, e_w, 1.0, self.k_factor)
        new_l = _update_rating(r_l, e_l, 0.0, self.k_factor)

        self._ratings[winner_id].rating = new_w
        self._ratings[winner_id].matches_played += 1
        self._ratings[loser_id].rating = new_l
        self._ratings[loser_id].matches_played += 1

        if timestamp is None:
            timestamp = 0.0
        return MatchResult(winner_id=winner_id, loser_id=loser_id, timestamp=timestamp)

    def get_rating(self, model_id: str) -> float:
        """获取模型当前 rating;不存在 → 0.0"""
        if model_id not in self._ratings:
            return 0.0
        return self._ratings[model_id].rating

    def get_stats(self, model_id: str) -> Optional[EloRating]:
        """获取模型完整 EloRating;不存在 → None"""
        return self._ratings.get(model_id)

    def ranked(self) -> List[EloRating]:
        """按 rating 降序返回所有模型"""
        return sorted(
            self._ratings.values(), key=lambda r: r.rating, reverse=True
        )

    def __len__(self) -> int:
        return len(self._ratings)

    def __contains__(self, model_id: str) -> bool:
        return model_id in self._ratings

    def to_dict(self) -> Dict:
        return {
            "k_factor": self.k_factor,
            "ratings": {mid: r.to_dict() for mid, r in self._ratings.items()},
        }


# ============ Bootstrap 置信区间 ============
def bootstrap_ci(
    ratings_before: List[EloRating],
    matches: List[MatchResult],
    n_resamples: int = 1000,
    ci: float = 0.95,
    k_factor: float = 4.0,
    seed: Optional[int] = None,
) -> Dict[str, Tuple[float, float]]:
    """Bootstrap CI — 对 match 序列有放回重采样 n_resamples 次,
    对每个 model_id 收集 final rating 分布,取 ci 对应的分位数

    返回: {model_id: (low, high), ...}

    真实算法:
      for _ in range(n_resamples):
          sample = random.choices(matches, k=len(matches))
          对 sample 跑 Elo,得 final ratings
          收集到每模型的 distribution
      对每模型 distribution 排序,取 low = quantile((1-ci)/2), high = quantile(1 - (1-ci)/2)

    顺序无关性:重采样独立,match 顺序不影响结果(因为每局都是独立更新)。
    """
    if not 0.0 < ci < 1.0:
        raise ValueError(f"ci must be in (0, 1), got {ci}")
    if n_resamples <= 0:
        raise ValueError(f"n_resamples must be > 0, got {n_resamples}")

    # 初始化基线 rating
    base: Dict[str, float] = {r.model_id: r.rating for r in ratings_before}
    all_model_ids = set(base.keys())
    for m in matches:
        all_model_ids.add(m.winner_id)
        all_model_ids.add(m.loser_id)

    if not all_model_ids:
        return {}

    # 退化情况:无 match → 所有 model 的 CI 都缩到 (base, base)
    if not matches:
        return {mid: (base[mid], base[mid]) for mid in all_model_ids}

    rng = random.Random(seed)

    # 收集每模型的所有 final rating
    distribution: Dict[str, List[float]] = {mid: [] for mid in all_model_ids}

    for _ in range(n_resamples):
        # 有放回重采样
        sample = [rng.choice(matches) for _ in range(len(matches))]
        # 从 base 开始跑 Elo
        cur: Dict[str, float] = dict(base)
        played: Dict[str, int] = {mid: 0 for mid in all_model_ids}
        for m in sample:
            w, l = m.winner_id, m.loser_id
            if w == l:
                continue
            r_w = cur.get(w, 1500.0)
            r_l = cur.get(l, 1500.0)
            e_w = _expected_score(r_w, r_l)
            new_w = _update_rating(r_w, e_w, 1.0, k_factor)
            new_l = _update_rating(r_l, 1.0 - e_w, 0.0, k_factor)
            cur[w] = new_w
            cur[l] = new_l
            played[w] = played.get(w, 0) + 1
            played[l] = played.get(l, 0) + 1
        for mid in all_model_ids:
            distribution[mid].append(cur.get(mid, 1500.0))

    # 计算分位数
    alpha = (1.0 - ci) / 2.0
    result: Dict[str, Tuple[float, float]] = {}
    for mid in all_model_ids:
        samples = distribution[mid]
        samples.sort()
        n = len(samples)
        # 线性插值分位数
        low_idx = alpha * (n - 1)
        high_idx = (1.0 - alpha) * (n - 1)
        lo = samples[int(low_idx)] + (low_idx - int(low_idx)) * (
            samples[min(int(low_idx) + 1, n - 1)] - samples[int(low_idx)]
        )
        hi = samples[int(high_idx)] + (high_idx - int(high_idx)) * (
            samples[min(int(high_idx) + 1, n - 1)] - samples[int(high_idx)]
        )
        result[mid] = (float(lo), float(hi))

    return result


# ============ Worker 调度 ============
Strategy = Literal["lottery", "shortest_queue"]


class WorkerPool:
    """Worker 池调度 — lottery / shortest_queue

    lottery:每次 submit 随机选一个 worker(简单公平)
    shortest_queue:每次 submit 选当前活跃任务最少的 worker(负载均衡)

    submit 立即返回 Future,不阻塞调用方。
    job 在独立 ThreadPoolExecutor 中执行(每 worker 自己的池子)。
    """

    def __init__(self, workers: List[str], max_jobs_per_worker: int = 4):
        if not workers:
            raise ValueError("workers must be non-empty")
        # 去重保序
        seen = set()
        uniq: List[str] = []
        for w in workers:
            if w not in seen:
                seen.add(w)
                uniq.append(w)
        if not uniq:
            raise ValueError("workers must be non-empty after dedup")
        self._workers: List[str] = uniq
        self._max_per_worker = max(1, int(max_jobs_per_worker))
        self._strategy: Strategy = "lottery"
        self._rng = random.Random()
        self._lock = threading.Lock()
        # 每 worker 一个 ThreadPoolExecutor
        self._executors: Dict[str, ThreadPoolExecutor] = {
            w: ThreadPoolExecutor(max_workers=self._max_per_worker) for w in uniq
        }
        # 跟踪当前活跃 job 数
        self._loads: Dict[str, int] = {w: 0 for w in uniq}

    def set_strategy(self, strategy: Strategy) -> None:
        s = (strategy or "").strip().lower()
        if s not in ("lottery", "shortest_queue"):
            raise ValueError(
                f"strategy must be 'lottery' or 'shortest_queue', got {strategy}"
            )
        self._strategy = s

    def get_strategy(self) -> Strategy:
        return self._strategy

    def _pick_worker(self) -> str:
        """根据 strategy 选 worker"""
        if self._strategy == "shortest_queue":
            with self._lock:
                # 选 load 最小的(平局时选第一个)
                min_load = min(self._loads.values())
                for w in self._workers:
                    if self._loads[w] == min_load:
                        return w
        # lottery
        return self._rng.choice(self._workers)

    def submit(self, job: Callable, *args, **kwargs) -> Future:
        """提交一个 job 到某 worker;返回 Future"""
        worker = self._pick_worker()
        executor = self._executors[worker]

        with self._lock:
            self._loads[worker] += 1

        def _wrapped():
            try:
                return job(*args, **kwargs)
            finally:
                with self._lock:
                    self._loads[worker] -= 1

        return executor.submit(_wrapped)

    def worker_loads(self) -> Dict[str, int]:
        """当前每 worker 活跃 job 数(快照)"""
        with self._lock:
            return dict(self._loads)

    def workers(self) -> List[str]:
        return list(self._workers)

    def shutdown(self, wait: bool = True) -> None:
        for ex in self._executors.values():
            ex.shutdown(wait=wait)

    def to_dict(self) -> Dict:
        return {
            "strategy": self._strategy,
            "workers": list(self._workers),
            "max_jobs_per_worker": self._max_per_worker,
            "loads": self.worker_loads(),
        }


# ============ JSON 序列化 ============
def to_json(obj, indent: Optional[int] = 2) -> str:
    """统一 JSON 序列化:支持 dataclass, EloRating, MatchResult, Leaderboard, WorkerPool"""
    if isinstance(obj, EloRating):
        return json.dumps(obj.to_dict(), indent=indent, ensure_ascii=False)
    if isinstance(obj, MatchResult):
        return json.dumps(asdict(obj), indent=indent, ensure_ascii=False)
    if isinstance(obj, EloLeaderboard):
        return json.dumps(obj.to_dict(), indent=indent, ensure_ascii=False)
    if isinstance(obj, WorkerPool):
        return json.dumps(obj.to_dict(), indent=indent, ensure_ascii=False)
    if isinstance(obj, dict):
        return json.dumps(obj, indent=indent, ensure_ascii=False, default=_json_default)
    if isinstance(obj, (list, tuple)):
        return json.dumps(
            list(obj), indent=indent, ensure_ascii=False, default=_json_default
        )
    return json.dumps(obj, indent=indent, ensure_ascii=False, default=_json_default)


def _json_default(o):
    """兜底:处理 dataclass / tuple / EloRating"""
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if hasattr(o, "__dataclass_fields__"):
        return asdict(o)
    if isinstance(o, tuple):
        return list(o)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


__all__ = [
    "MatchResult",
    "EloRating",
    "EloLeaderboard",
    "bootstrap_ci",
    "WorkerPool",
    "to_json",
]
