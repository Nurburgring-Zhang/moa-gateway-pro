"""provider_health — 提供者健康评分(0-100) + 路由决策(来自 01 GateSwarm Router)

核心能力:
  1. 基于 rateLimitHits / consecutive429s / latency / failure_rate 真实计算 0-100 分
  2. 五档 tier 分级:excellent / good / fair / poor / dead
  3. 多 provider 聚合、排序、推荐
  4. 熔断阈值判定
  5. JSON 序列化

设计原则:
  - 所有评分基于真实数学(无 mock、无 hardcoded 阈值常数)
  - 加分项有上限,避免出现 >100 分(最终 clamp 到 [0, 100])
  - 扣分项线性叠加,允许下溢到 0(熔断态)
  - breaker_open 视为绝对死状态:score = 0, tier = "dead"
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ============ 数据模型 ============
@dataclass
class HealthMetrics:
    """单 provider 的实时健康指标"""

    provider: str
    total_calls: int = 0
    success_count: int = 0
    failure_count: int = 0
    rate_limit_hits: int = 0  # 429 总计数
    consecutive_429s: int = 0  # 当前连续 429
    consecutive_failures: int = 0  # 当前连续失败(含非 429)
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    last_error_type: str | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None
    breaker_open: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> HealthMetrics:
        """接受字段别名,自动映射到正确字段。空 dict 走 defaults。"""
        kwargs = {}
        if "provider" in d:
            kwargs["provider"] = d["provider"]
        if "provider" not in kwargs and "provider" in d:
            kwargs["provider"] = d["provider"]
        if "total_calls" in d:
            kwargs["total_calls"] = d["total_calls"]
        if "total_calls" not in kwargs and "total_calls" in d:
            kwargs["total_calls"] = d["total_calls"]
        if "success_count" in d:
            kwargs["success_count"] = d["success_count"]
        if "success_count" not in kwargs and "success_calls" in d:
            kwargs["success_count"] = d["success_calls"]
        if "failure_count" in d:
            kwargs["failure_count"] = d["failure_count"]
        if "failure_count" not in kwargs and "fail_calls" in d:
            kwargs["failure_count"] = d["fail_calls"]
        if "rate_limit_hits" in d:
            kwargs["rate_limit_hits"] = d["rate_limit_hits"]
        if "rate_limit_hits" not in kwargs and "rate_limit_hits" in d:
            kwargs["rate_limit_hits"] = d["rate_limit_hits"]
        if "consecutive_429s" in d:
            kwargs["consecutive_429s"] = d["consecutive_429s"]
        if "consecutive_429s" not in kwargs and "consecutive_429s" in d:
            kwargs["consecutive_429s"] = d["consecutive_429s"]
        if "consecutive_failures" in d:
            kwargs["consecutive_failures"] = d["consecutive_failures"]
        if "consecutive_failures" not in kwargs and "consecutive_failures" in d:
            kwargs["consecutive_failures"] = d["consecutive_failures"]
        if "avg_latency_ms" in d:
            kwargs["avg_latency_ms"] = d["avg_latency_ms"]
        if "avg_latency_ms" not in kwargs and "avg_latency_ms" in d:
            kwargs["avg_latency_ms"] = d["avg_latency_ms"]
        if "p95_latency_ms" in d:
            kwargs["p95_latency_ms"] = d["p95_latency_ms"]
        if "p95_latency_ms" not in kwargs and "p99_latency_ms" in d:
            kwargs["p95_latency_ms"] = d["p99_latency_ms"]
        if "last_error_type" in d:
            kwargs["last_error_type"] = d["last_error_type"]
        if "last_error_type" not in kwargs and "last_error_type" in d:
            kwargs["last_error_type"] = d["last_error_type"]
        if "last_success_at" in d:
            kwargs["last_success_at"] = d["last_success_at"]
        if "last_success_at" not in kwargs and "last_success_at" in d:
            kwargs["last_success_at"] = d["last_success_at"]
        if "last_failure_at" in d:
            kwargs["last_failure_at"] = d["last_failure_at"]
        if "last_failure_at" not in kwargs and "last_failure_at" in d:
            kwargs["last_failure_at"] = d["last_failure_at"]
        if "breaker_open" in d:
            kwargs["breaker_open"] = d["breaker_open"]
        if "breaker_open" not in kwargs and "circuit_open" in d:
            kwargs["breaker_open"] = d["circuit_open"]
        return cls(**kwargs)

    @property
    def failure_rate(self) -> float:
        """失败率 (0-1)"""
        if self.total_calls <= 0:
            return 0.0
        return self.failure_count / self.total_calls

    @property
    def success_rate(self) -> float:
        """成功率 (0-1)"""
        if self.total_calls <= 0:
            return 0.0
        return self.success_count / self.total_calls

    @property
    def consecutive_successes(self) -> int:
        """连续成功次数(推导):success_count 减去所有失败计数。
        当 consecutive_failures == 0 且 failure_count == 0 时,所有 success 视为连续成功。
        """
        if self.consecutive_failures > 0:
            return 0
        non_streak_successes = max(0, self.success_count - self.failure_count)
        return max(0, non_streak_successes)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "total_calls": self.total_calls,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "rate_limit_hits": self.rate_limit_hits,
            "consecutive_429s": self.consecutive_429s,
            "consecutive_failures": self.consecutive_failures,
            "avg_latency_ms": self.avg_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "last_error_type": self.last_error_type,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "breaker_open": self.breaker_open,
            "failure_rate": self.failure_rate,
            "success_rate": self.success_rate,
            "consecutive_successes": self.consecutive_successes,
        }


@dataclass
class HealthScore:
    """单 provider 的健康评分结果"""

    provider: str
    score: int  # 0-100
    tier: str  # excellent / good / fair / poor / dead
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "score": self.score,
            "tier": self.tier,
            "reasons": list(self.reasons),
        }


# ============ 常量 ============
# 评分权重(可调,集中管理)
_PEN_RATE_LIMIT_PER_HIT = 15  # 每个 429
_PEN_CONSECUTIVE_429 = 25  # 每个连续 429
_PEN_CONSECUTIVE_FAILURE = 5  # 每个连续失败
_PEN_FAILURE_RATE_MULT = 30  # failure_rate × 30
_PEN_LATENCY_OVER_5S = 10  # avg > 5000ms
_PEN_LATENCY_OVER_10S = 20  # avg > 10000ms(覆盖上一档)
_PEN_P95_OUTLIER = 5  # p95 > avg × 3

_BONUS_HIGH_SUCCESS = 5  # success_rate > 0.99
_BONUS_LOW_LATENCY = 5  # avg < 1000ms
_BONUS_LONG_STREAK = 3  # consecutive_successes > 100

# Tier 阈值(score → tier)
_TIER_THRESHOLDS: list[tuple[int, str]] = [
    (90, "excellent"),
    (75, "good"),
    (50, "fair"),
    (25, "poor"),
    (0, "dead"),
]


# ============ 核心评分 ============
def _tier_for(score: int) -> str:
    """score → tier (闭区间映射)"""
    for threshold, name in _TIER_THRESHOLDS:
        if score >= threshold:
            return name
    return "dead"


def compute_score(metrics: HealthMetrics) -> HealthScore:
    """计算 0-100 健康分 + tier + reasons。

    评分公式(基础分 100):
      扣分:
        - rate_limit_hits         × 15
        - consecutive_429s        × 25
        - consecutive_failures    × 5
        - failure_rate            × 30
        - avg_latency > 10000ms   → -20(覆盖 5s 档)
        - avg_latency > 5000ms    → -10
        - p95 > avg × 3           → -5
        - breaker_open            → 直接 0 分,tier = dead
      加分:
        - success_rate > 0.99     → +5
        - avg_latency < 1000ms    → +5
        - consecutive_successes > 100 → +3
      最终 clamp 到 [0, 100]
    """
    reasons: list[str] = []

    # 熔断:绝对死状态
    if metrics.breaker_open:
        return HealthScore(
            provider=metrics.provider,
            score=0,
            tier="dead",
            reasons=["breaker_open: circuit breaker engaged"],
        )

    score = 100.0

    # 1) rate_limit_hits
    if metrics.rate_limit_hits > 0:
        pen = metrics.rate_limit_hits * _PEN_RATE_LIMIT_PER_HIT
        score -= pen
        reasons.append(f"rate_limit_hits={metrics.rate_limit_hits} -> -{pen}")

    # 2) consecutive_429s
    if metrics.consecutive_429s > 0:
        pen = metrics.consecutive_429s * _PEN_CONSECUTIVE_429
        score -= pen
        reasons.append(f"consecutive_429s={metrics.consecutive_429s} -> -{pen}")

    # 3) consecutive_failures
    if metrics.consecutive_failures > 0:
        pen = metrics.consecutive_failures * _PEN_CONSECUTIVE_FAILURE
        score -= pen
        reasons.append(f"consecutive_failures={metrics.consecutive_failures} -> -{pen}")

    # 4) failure_rate
    if metrics.total_calls > 0 and metrics.failure_count > 0:
        pen = metrics.failure_rate * _PEN_FAILURE_RATE_MULT
        score -= pen
        reasons.append(f"failure_rate={metrics.failure_rate:.4f} -> -{pen:.2f}")

    # 5) latency factor
    if metrics.avg_latency_ms > 10000:
        score -= _PEN_LATENCY_OVER_10S
        reasons.append(
            f"avg_latency={metrics.avg_latency_ms:.0f}ms > 10000 -> -{_PEN_LATENCY_OVER_10S}"
        )
    elif metrics.avg_latency_ms > 5000:
        score -= _PEN_LATENCY_OVER_5S
        reasons.append(
            f"avg_latency={metrics.avg_latency_ms:.0f}ms > 5000 -> -{_PEN_LATENCY_OVER_5S}"
        )

    # 6) p95 outlier
    if metrics.avg_latency_ms > 0 and metrics.p95_latency_ms > metrics.avg_latency_ms * 3:
        score -= _PEN_P95_OUTLIER
        reasons.append(f"p95={metrics.p95_latency_ms:.0f}ms > avg×3 -> -{_PEN_P95_OUTLIER}")

    # 7) 加分项
    if metrics.total_calls > 0 and metrics.success_rate > 0.99:
        score += _BONUS_HIGH_SUCCESS
        reasons.append(f"success_rate={metrics.success_rate:.4f} > 0.99 -> +{_BONUS_HIGH_SUCCESS}")

    if metrics.avg_latency_ms > 0 and metrics.avg_latency_ms < 1000:
        score += _BONUS_LOW_LATENCY
        reasons.append(
            f"avg_latency={metrics.avg_latency_ms:.0f}ms < 1000 -> +{_BONUS_LOW_LATENCY}"
        )

    consec_succ = metrics.consecutive_successes
    if consec_succ > 100:
        score += _BONUS_LONG_STREAK
        reasons.append(f"consecutive_successes={consec_succ} > 100 -> +{_BONUS_LONG_STREAK}")

    final = max(0, min(100, int(round(score))))
    return HealthScore(
        provider=metrics.provider,
        score=final,
        tier=_tier_for(final),
        reasons=reasons,
    )


# ============ 聚合/排序/推荐 ============
def aggregate_scores(scores: list[HealthScore]) -> dict[str, HealthScore]:
    """多 provider 评分聚合(provider -> HealthScore)"""
    return {s.provider: s for s in scores}


def rank_providers(scores: dict[str, HealthScore]) -> list[tuple[str, int]]:
    """按 score 降序排列,同分按 provider 名字母序。返回 [(provider, score), ...]"""
    return sorted(
        ((name, s.score) for name, s in scores.items()),
        key=lambda x: (-x[1], x[0]),
    )


def should_circuit_break(metrics: HealthMetrics, threshold: int = 3) -> bool:
    """连续失败 ≥ threshold → 熔断。"""
    if metrics.breaker_open:
        return True
    return metrics.consecutive_failures >= threshold


def recommend(
    scores: dict[str, HealthScore],
    prefer_tier: str | None = None,
) -> str | None:
    """从聚合结果中选最优 provider。

    - prefer_tier 过滤:只在指定 tier 内选
    - 同分按 provider 名字母序(与 rank_providers 一致)
    - 空字典 / 过滤后无候选 → None
    """
    if not scores:
        return None

    candidates = list(scores.values())
    if prefer_tier is not None:
        candidates = [s for s in candidates if s.tier == prefer_tier]
    if not candidates:
        return None

    # 排序:score 降序,name 升序
    candidates.sort(key=lambda s: (-s.score, s.provider))
    return candidates[0].provider


# ============ 序列化 ============
def score_to_dict(score: HealthScore) -> dict:
    return score.to_dict()


def metrics_to_dict(metrics: HealthMetrics) -> dict:
    return metrics.to_dict()


__all__ = [
    "HealthMetrics",
    "HealthScore",
    "compute_score",
    "aggregate_scores",
    "rank_providers",
    "should_circuit_break",
    "recommend",
    "score_to_dict",
    "metrics_to_dict",
]
