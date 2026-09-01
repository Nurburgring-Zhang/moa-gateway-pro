"""Auto-strategy multi-factor scoring.

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT):
- ``open-sse/services/autoCombo/scoring.ts`` — ``DEFAULT_WEIGHTS``,
  ``normalizeScoringWeights``, ``calculateScore`` (clamp01 weighted sum),
  ``calculateTierScore`` (ultra/pro/standard/free + reset bonus),
  ``computePoolMaxima`` (floors: cost 0.001, latency 1, stddev 0.001),
  ``calculateFactors`` (health from breaker state, cost/latency/stability
  normalized against pool maxima, connectionDensity, neutral 0.5 quality).
- ``open-sse/services/autoCombo/taskFitness.ts`` — static FITNESS_TABLE
  (versioned rows, longest-pattern-first, segment-boundary matching #8603 /
  #11503) + WILDCARD_BOOSTS over the neutral 0.5 baseline. The DB-backed
  resolution layers (user_override / arena_elo / models.dev tier) have no
  equivalent store in this gateway and are omitted by design; the engine
  accepts per-model fitness overrides so operators keep the same seam.

The 12 gateway weights in ``RoutingStrategiesConfig.auto_weights`` map to the
OmniRoute factors: quota, health, cost_inv, latency_inv, task_fit, stability,
tier (=tierPriority), specificity (=specificityMatch), context_affinity,
session_avail (=sessionAvailability), conn_density (=connectionDensity),
quality. ``cacheAffinity`` / ``resetWindowAffinity`` default weight 0 upstream
and are not exposed here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .models import EndpointCandidate

# --- OmniRoute DEFAULT_WEIGHTS (scoring.ts) ---------------------------------
# Keys use the gateway config spelling; values match OmniRoute's defaults.
DEFAULT_WEIGHTS: dict[str, float] = {
    "quota": 0.1429,
    "health": 0.1605,
    "cost_inv": 0.1429,
    "latency_inv": 0.1143,
    "task_fit": 0.0762,
    "stability": 0.0476,
    "tier": 0.0476,
    "specificity": 0.0476,
    "context_affinity": 0.0476,
    "session_avail": 0.0476,
    "conn_density": 0.0476,
    "quality": 0.03,
}

_BASE_TIER_SCORES = {"ultra": 1.0, "pro": 0.67, "standard": 0.33, "free": 0.0}


def clamp01(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, value))


def normalize_scoring_weights(weights: dict[str, float] | None) -> dict[str, float]:
    """Port of ``normalizeScoringWeights``: sanitise + renormalise to sum 1."""
    if not weights:
        return dict(DEFAULT_WEIGHTS)
    sanitized: dict[str, float] = {}
    for key in DEFAULT_WEIGHTS:
        raw = weights.get(key)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = math.nan
        sanitized[key] = value if math.isfinite(value) and value >= 0 else 0.0
    total = sum(sanitized.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {key: value / total for key, value in sanitized.items()}


def calculate_tier_score(tier: str | None, quota_reset_interval_secs: float | None) -> float:
    """Port of ``calculateTierScore`` (base*0.8 + resetBonus*0.2)."""
    base = _BASE_TIER_SCORES.get((tier or "").lower(), 0.33)
    reset_bonus = 0.0
    if quota_reset_interval_secs is not None and quota_reset_interval_secs > 0:
        reset_bonus = max(0.0, 1 - quota_reset_interval_secs / 2_592_000)
    return min(1.0, base * 0.8 + reset_bonus * 0.2)


# --- Pool maxima ---------------------------------------------------------------


@dataclass(frozen=True)
class PoolMaxima:
    max_cost: float
    max_latency: float
    max_stddev: float


def compute_pool_maxima(pool: list[EndpointCandidate]) -> PoolMaxima:
    """Port of ``computePoolMaxima`` with the same floors."""
    max_cost = 0.001
    max_latency = 1.0
    max_stddev = 0.001
    for candidate in pool:
        cost = candidate.cost_per_1k_input  # OmniRoute scores on input price
        if cost > max_cost:
            max_cost = cost
        if candidate.latency_p95_ms > max_latency:
            max_latency = candidate.latency_p95_ms
        if candidate.latency_stddev_ms > max_stddev:
            max_stddev = candidate.latency_stddev_ms
    return PoolMaxima(max_cost, max_latency, max_stddev)


# --- Task fitness (FITNESS_TABLE + WILDCARD_BOOSTS) ----------------------------

# Versioned rows only (taskFitness.ts layer 4, #11503).
FITNESS_TABLE: dict[str, dict[str, float]] = {
    "coding": {
        "gpt-4o": 0.9,
        "gpt-4o-mini": 0.8,
        "gpt-4-turbo": 0.88,
        "o3": 0.95,
        "o4-mini": 0.88,
        "gemini-2.5-pro": 0.92,
        "gemini-2.5-flash": 0.82,
        "deepseek-coder": 0.9,
        "deepseek-v3": 0.85,
        "deepseek-r1": 0.88,
        "deepseek-chat": 0.84,
        "deepseek-v3.2": 0.86,
        "grok-3": 0.8,
        "glm-5.1": 0.78,
        "minimax-m2.5": 0.75,
        "minimax-m2": 0.72,
    },
    "review": {
        "gpt-4o": 0.88,
        "gpt-4o-mini": 0.72,
        "o3": 0.92,
        "gemini-2.5-pro": 0.93,
        "deepseek-r1": 0.85,
        "deepseek-v3": 0.8,
    },
    "planning": {
        "gpt-4o": 0.88,
        "o3": 0.95,
        "gemini-2.5-pro": 0.93,
        "deepseek-r1": 0.85,
    },
    "analysis": {
        "gemini-2.5-pro": 0.95,
        "gemini-3.1-pro": 0.95,
        "gpt-4o": 0.85,
        "o3": 0.93,
        "deepseek-r1": 0.88,
        "deepseek-chat": 0.8,
        "glm-5.1": 0.82,
        "minimax-m2.5": 0.76,
    },
    "debugging": {
        "gpt-4o": 0.88,
        "deepseek-coder": 0.9,
        "deepseek-v3": 0.82,
    },
    "documentation": {
        "gpt-4o": 0.92,
        "gpt-4o-mini": 0.85,
        "deepseek-v3": 0.78,
    },
    "default": {
        "gpt-4o": 0.85,
        "gemini-3.1-pro": 0.85,
        "deepseek-v3": 0.75,
        "deepseek-chat": 0.74,
        "grok-3": 0.73,
        "glm-5.1": 0.75,
        "minimax-m2.5": 0.7,
    },
}

WILDCARD_BOOSTS: list[tuple[str, str, float]] = [
    ("coder", "coding", 0.15),
    ("code", "coding", 0.1),
    ("fast", "coding", 0.05),
    ("thinking", "planning", 0.1),
    ("thinking", "analysis", 0.1),
]

_SEGMENT_SEPARATORS = {"-", ".", "/"}


def _matches_at_segment_boundary(model: str, pattern: str) -> bool:
    """Port of ``matchesAtSegmentBoundary`` (#11503)."""
    if not pattern:
        return False
    index = model.find(pattern)
    while index != -1:
        starts_ok = index == 0 or model[index - 1] in _SEGMENT_SEPARATORS
        end_index = index + len(pattern)
        ends_ok = end_index == len(model) or model[end_index] in _SEGMENT_SEPARATORS
        if starts_ok and ends_ok:
            return True
        index = model.find(pattern, index + 1)
    return False


def get_static_fitness_table_score(model: str, task_type: str) -> float | None:
    """Port of ``getStaticFitnessTableScore`` (longest pattern first #8603)."""
    normalized_model = model.lower()
    normalized_task = task_type.lower()
    table = FITNESS_TABLE.get(normalized_task, FITNESS_TABLE["default"])
    for pattern, score in sorted(table.items(), key=lambda entry: -len(entry[0])):
        if _matches_at_segment_boundary(normalized_model, pattern):
            return score
    return None


def get_task_fitness(model: str, task_type: str) -> float:
    """Fitness resolution chain layers 4-5 (table → wildcard 0.5 baseline)."""
    normalized_model = model.lower()
    normalized_task = task_type.lower()
    static_score = get_static_fitness_table_score(normalized_model, normalized_task)
    if static_score is not None:
        return static_score
    base_score = 0.5
    for pattern, boosted_task, boost in WILDCARD_BOOSTS:
        if pattern in normalized_model and normalized_task == boosted_task:
            base_score += boost
    return min(1.0, base_score)


# --- Factor calculation ---------------------------------------------------------


@dataclass
class AutoFactors:
    quota: float
    health: float
    cost_inv: float
    latency_inv: float
    task_fit: float
    stability: float
    tier: float
    specificity: float
    context_affinity: float
    session_avail: float
    conn_density: float
    quality: float

    def as_dict(self) -> dict[str, float]:
        return {
            "quota": self.quota,
            "health": self.health,
            "cost_inv": self.cost_inv,
            "latency_inv": self.latency_inv,
            "task_fit": self.task_fit,
            "stability": self.stability,
            "tier": self.tier,
            "specificity": self.specificity,
            "context_affinity": self.context_affinity,
            "session_avail": self.session_avail,
            "conn_density": self.conn_density,
            "quality": self.quality,
        }


def calculate_factors(
    candidate: EndpointCandidate,
    maxima: PoolMaxima,
    task_type: str,
    session_provider: str | None = None,
    quality: float | None = None,
    fitness_overrides: dict[str, float] | None = None,
) -> AutoFactors:
    """Port of ``calculateFactors`` adapted to the gateway candidate model.

    - ``health``: CLOSED=1.0 / HALF_OPEN=0.5 / OPEN=0.0 (breaker state).
    - ``context_affinity``: 1.0 when this endpoint's provider served the
      current session (OmniRoute contextAffinity pin), neutral 0.5 otherwise.
    - ``quality``: feedback signal; neutral 0.5 when no telemetry (cold
      candidates neither boosted nor penalized — upstream contract).
    - ``specificity``: neutral 0.5 (manifest routing not ported).
    """
    model_name = candidate.model_id or candidate.endpoint_id
    if fitness_overrides and model_name.lower() in fitness_overrides:
        task_fit = clamp01(fitness_overrides[model_name.lower()])
    else:
        task_fit = clamp01(get_task_fitness(model_name, task_type))

    if candidate.breaker_state == "CLOSED":
        health = 1.0
    elif candidate.breaker_state == "HALF_OPEN":
        health = 0.5
    else:
        health = 0.0

    context_affinity = 0.5
    if session_provider and candidate.provider and candidate.provider == session_provider:
        context_affinity = 1.0

    return AutoFactors(
        quota=clamp01(candidate.quota_remaining_pct / 100.0),
        health=health,
        cost_inv=clamp01(1 - candidate.cost_per_1k_input / maxima.max_cost),
        latency_inv=clamp01(1 - candidate.latency_p95_ms / maxima.max_latency),
        task_fit=task_fit,
        stability=clamp01(1 - candidate.latency_stddev_ms / maxima.max_stddev),
        tier=calculate_tier_score(candidate.account_tier, candidate.quota_reset_interval_secs),
        specificity=0.5,
        context_affinity=clamp01(context_affinity),
        session_avail=1.0,
        conn_density=clamp01((candidate.connection_pool_size - 1) / 10.0),
        quality=clamp01(quality if quality is not None else 0.5),
    )


def calculate_score(factors: AutoFactors, weights: dict[str, float]) -> float:
    """Port of ``calculateScore``: clamp01 of the weighted factor sum."""
    factor_map = factors.as_dict()
    total = 0.0
    for key, weight in weights.items():
        total += weight * factor_map.get(key, 0.0)
    return clamp01(total)


def score_pool(
    pool: list[EndpointCandidate],
    task_type: str,
    weights: dict[str, float] | None = None,
    session_provider: str | None = None,
    quality_by_endpoint: dict[str, float] | None = None,
    fitness_overrides: dict[str, float] | None = None,
) -> list[tuple[EndpointCandidate, float, AutoFactors]]:
    """Score and sort a pool (descending score, stable for ties)."""
    normalized = normalize_scoring_weights(weights)
    maxima = compute_pool_maxima(pool)
    scored: list[tuple[int, EndpointCandidate, float, AutoFactors]] = []
    for index, candidate in enumerate(pool):
        quality = None
        if quality_by_endpoint is not None:
            quality = quality_by_endpoint.get(candidate.endpoint_id)
        factors = calculate_factors(
            candidate,
            maxima,
            task_type,
            session_provider=session_provider,
            quality=quality,
            fitness_overrides=fitness_overrides,
        )
        scored.append((index, candidate, calculate_score(factors, normalized), factors))
    scored.sort(key=lambda entry: (-entry[2], entry[0]))
    return [(candidate, score, factors) for _, candidate, score, factors in scored]
