"""The 20 OmniRoute routing strategies, ported to pure ordering functions.

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT):

- ``open-sse/services/combo/applyStrategyOrdering.ts`` — the dispatch chain
  (lkgp / strict-random / random / fill-first / p2c / least-used /
  cost-optimized / reset-aware / reset-window / context-optimized /
  cache-optimized / headroom / quota-share).
- ``open-sse/services/combo/targetSorters.ts`` — weighted roulette
  (``selectWeightedTarget`` + ``orderTargetsForWeightedFallback``), usage sort,
  power-of-two-choices scoring (``getP2CTargetScore``).
- ``open-sse/services/combo/rrState.ts`` — round-robin counters (MAX 500).
- ``src/shared/utils/shuffleDeck.ts`` — Fisher-Yates + strict-random decks
  (anti-repeat across cycles).
- ``open-sse/services/combo/quotaScoring.ts`` / ``headroomRanking.ts`` —
  reset-aware / reset-window / headroom math (see ``quota_scoring.py``).
- ``open-sse/services/autoCombo/scoring.ts`` — the auto multi-factor scorer
  (see ``auto_scoring.py``).
- ``open-sse/services/combo/promptCacheAffinity.ts`` — rendezvous hashing
  (see ``cache_affinity.py``).
- ``open-sse/services/combo/quotaShareStrategy.ts`` — the internal
  quota-share DRR + P2C selector lives in ``moa_gateway.quota_scheduler``
  and is delegated to here (M1 ↔ M2 contract).
- ``src/shared/constants/routingStrategies.ts`` — canonical names + aliases.

Every function has the signature
``fn(candidates, ctx, state) -> OrderingResult`` and only reorders — no early
returns, matching ``applyStrategyOrdering``'s contract. Randomness and clocks
are injected through ``StrategyState`` so behaviour is deterministic in tests.
"""

from __future__ import annotations

import logging
import math
import random
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

from .auto_scoring import score_pool
from .cache_affinity import rank_by_cache_affinity
from .models import EndpointCandidate, RoutingContext
from .quota_scoring import (
    candidate_window_utilization,
    compute_headroom,
    get_reset_window_remaining_ms,
    resolve_reset_aware_config,
    resolve_reset_window_config,
    score_reset_aware_quota,
)

logger = logging.getLogger(__name__)

# OmniRoute rrState.ts: eviction limit for round-robin counters.
MAX_RR_COUNTERS = 500
# OmniRoute quotaShareStrategy.ts: eviction limit for per-combo DRR states
# (reused here for strict-random deck namespaces).
MAX_DECKS = 200


# ---------------------------------------------------------------------------
# Strategy state (injected by the engine; never module-global)
# ---------------------------------------------------------------------------


@dataclass
class DeckState:
    order: list[str]
    index: int
    ids_key: str


@dataclass
class LkgpRecord:
    provider: str
    endpoint_id: str | None = None
    connection_id: str | None = None


@dataclass
class StrategyState:
    """Mutable per-engine state shared by stateful strategies.

    Held by ``RoutingStrategyEngine`` (one instance), guarded by the engine
    lock. ``now_ms`` is stamped once per resolve so every strategy in a single
    decision compares against the same instant (OmniRoute #9330).
    """

    rng: random.Random
    now_ms: float
    dry_run: bool = False
    rr_counters: OrderedDict = field(default_factory=OrderedDict)
    decks: OrderedDict = field(default_factory=OrderedDict)
    lkgp: dict = field(default_factory=dict)
    # session_key -> LkgpRecord (provider/endpoint that last served the session)
    session_bindings: dict = field(default_factory=dict)
    # auto-strategy inputs supplied by the engine
    auto_weights: dict | None = None
    quality_by_endpoint: dict | None = None
    fitness_overrides: dict | None = None
    # quota-share release callback captured for the engine to hand to callers
    quota_share_release: Callable[[], None] | None = None


def _rr_counter_get(state: StrategyState, key: str) -> int:
    return state.rr_counters.get(key, 0)


def _rr_counter_set(state: StrategyState, key: str, value: int) -> None:
    """Set with oldest-entry eviction (OmniRoute rrCounters contract)."""
    if key not in state.rr_counters and len(state.rr_counters) >= MAX_RR_COUNTERS:
        state.rr_counters.popitem(last=False)
    state.rr_counters[key] = value


def fisher_yates_shuffle(items: list, rng: random.Random) -> list:
    """Port of ``fisherYatesShuffle`` (returns a new list)."""
    result = list(items)
    for i in range(len(result) - 1, 0, -1):
        j = rng.randrange(i + 1)
        result[i], result[j] = result[j], result[i]
    return result


@dataclass
class OrderingResult:
    ordered: list[EndpointCandidate]
    mode: str = "single"
    scores: dict[str, float] = field(default_factory=dict)
    applied: bool = True


StrategyFn = Callable[[list[EndpointCandidate], RoutingContext, StrategyState], OrderingResult]


# ---------------------------------------------------------------------------
# 1. priority — keep explicit priority order (stable)
# ---------------------------------------------------------------------------


def order_priority(candidates, ctx, state):
    ordered = sorted(candidates, key=lambda c: c.priority)  # stable
    return OrderingResult(ordered=ordered)


# ---------------------------------------------------------------------------
# 2. weighted — roulette pick, then weight-desc fallback chain
# ---------------------------------------------------------------------------


def _weighted_weight(candidate: EndpointCandidate) -> float:
    """OmniRoute ``selectWeightedTarget`` uses ``weight || 0``."""
    weight = candidate.weight
    if isinstance(weight, (int, float)) and math.isfinite(weight) and weight > 0:
        return float(weight)
    return 0.0


def order_weighted(candidates, ctx, state):
    if not candidates:
        return OrderingResult(ordered=[])
    total = sum(_weighted_weight(c) for c in candidates)
    if total <= 0:
        winner = candidates[state.rng.randrange(len(candidates))]
    else:
        roll = state.rng.random() * total
        winner = candidates[-1]
        for candidate in candidates:
            roll -= _weighted_weight(candidate)
            if roll <= 0:
                winner = candidate
                break
    # orderTargetsForWeightedFallback: winner first, rest by weight desc.
    rest = [c for c in candidates if c is not winner]
    rest.sort(key=_weighted_weight, reverse=True)
    return OrderingResult(ordered=[winner] + rest)


# ---------------------------------------------------------------------------
# 3. round-robin — rotate by per-group counter
# ---------------------------------------------------------------------------


def order_round_robin(candidates, ctx, state):
    if not candidates:
        return OrderingResult(ordered=[])
    key = f"round-robin:{ctx.group_key}"
    counter = _rr_counter_get(state, key)
    start = counter % len(candidates)
    ordered = candidates[start:] + candidates[:start]
    if not state.dry_run:
        _rr_counter_set(state, key, counter + 1)
    return OrderingResult(ordered=ordered)


# ---------------------------------------------------------------------------
# 4. context-relay — stay on the session's provider/endpoint
# ---------------------------------------------------------------------------


def order_context_relay(candidates, ctx, state):
    """Continue the conversation where the session left off.

    OmniRoute's context-relay records the serving model/provider per session
    (``recordSessionModelUsage``) and generates handoffs when the session
    moves; the ordering component pins the last serving target first so the
    cached context is relayed instead of rebuilt elsewhere.
    """
    ordered = sorted(candidates, key=lambda c: c.priority)
    binding = state.session_bindings.get(ctx.session_key) if ctx.session_key else None
    if binding is None:
        return OrderingResult(ordered=ordered, applied=False)

    match_index = -1
    if binding.endpoint_id:
        for i, candidate in enumerate(ordered):
            if candidate.endpoint_id == binding.endpoint_id:
                match_index = i
                break
    if match_index < 0 and binding.provider:
        for i, candidate in enumerate(ordered):
            if candidate.provider == binding.provider:
                match_index = i
                break
    if match_index > 0:
        ordered = [ordered[match_index]] + ordered[:match_index] + ordered[match_index + 1 :]
    return OrderingResult(ordered=ordered, applied=match_index >= 0)


# ---------------------------------------------------------------------------
# 5. fill-first — preserve priority order (fill current quota before moving)
# ---------------------------------------------------------------------------


def order_fill_first(candidates, ctx, state):
    # applyStrategyOrdering: "Fill-first ordering: preserving priority order".
    return OrderingResult(ordered=sorted(candidates, key=lambda c: c.priority))


# ---------------------------------------------------------------------------
# 6. p2c — power of two choices
# ---------------------------------------------------------------------------


def _p2c_score(candidate: EndpointCandidate) -> float:
    """Port of ``getP2CTargetScore``."""
    if candidate.breaker_state == "OPEN":
        return -math.inf
    # OmniRoute: finite successRate/100 else neutral 0.5. Endpoints with no
    # observed requests carry no success data → neutral.
    if candidate.request_count > 0:
        success_score = candidate.success_rate
    else:
        success_score = 0.5
    avg_latency = candidate.avg_latency_ms or candidate.latency_p95_ms
    if math.isfinite(avg_latency) and avg_latency > 0:
        latency_score = 1 / math.log10(avg_latency + 10)
    else:
        latency_score = 0.25
    breaker_penalty = 0.25 if candidate.breaker_state == "HALF_OPEN" else 0.0
    return success_score + latency_score - breaker_penalty


def order_p2c(candidates, ctx, state):
    if len(candidates) <= 1:
        return OrderingResult(ordered=list(candidates))
    first_index = state.rng.randrange(len(candidates))
    second_index = state.rng.randrange(len(candidates) - 1)
    if second_index >= first_index:
        second_index += 1
    first = candidates[first_index]
    second = candidates[second_index]
    selected_index = second_index if _p2c_score(second) > _p2c_score(first) else first_index
    ordered = [candidates[selected_index]] + [
        c for i, c in enumerate(candidates) if i != selected_index
    ]
    return OrderingResult(ordered=ordered)


# ---------------------------------------------------------------------------
# 7. random — Fisher-Yates shuffle
# ---------------------------------------------------------------------------


def order_random(candidates, ctx, state):
    return OrderingResult(ordered=fisher_yates_shuffle(candidates, state.rng))


# ---------------------------------------------------------------------------
# 8. least-used — ascending lifetime request count
# ---------------------------------------------------------------------------


def order_least_used(candidates, ctx, state):
    decorated = sorted(
        enumerate(candidates), key=lambda pair: (pair[1].request_count, pair[0])
    )
    return OrderingResult(ordered=[c for _, c in decorated])


# ---------------------------------------------------------------------------
# 9. cost-optimized — cheapest input price first
# ---------------------------------------------------------------------------


def order_cost_optimized(candidates, ctx, state):
    # OmniRoute sortModelsByCost: ascending pricing input cost, stable.
    decorated = sorted(
        enumerate(candidates), key=lambda pair: (pair[1].cost_per_1k_input, pair[0])
    )
    return OrderingResult(ordered=[c for _, c in decorated])


# ---------------------------------------------------------------------------
# 10. reset-aware — quota reset-pressure scoring + tie-band rotation
# ---------------------------------------------------------------------------


def order_reset_aware(candidates, ctx, state):
    if not candidates:
        return OrderingResult(ordered=[])
    config = resolve_reset_aware_config()
    scored = [
        (index, candidate, score_reset_aware_quota(candidate.quota, config, state.now_ms))
        for index, candidate in enumerate(candidates)
    ]
    scored.sort(key=lambda entry: (-entry[2], entry[0]))
    best_score = scored[0][2]
    tied = [entry for entry in scored if best_score - entry[2] <= config.tie_band]
    ordered_entries = scored
    if len(tied) > 1:
        rotated = _rotate_leading_ties(state, tied, f"reset-aware:{ctx.group_key}")
        tied_keys = {id(entry[1]) for entry in rotated}
        ordered_entries = rotated + [e for e in scored if id(e[1]) not in tied_keys]
    return OrderingResult(
        ordered=[entry[1] for entry in ordered_entries],
        scores={entry[1].endpoint_id: entry[2] for entry in scored if math.isfinite(entry[2])},
    )


def _rotate_leading_ties(state: StrategyState, tied: list, key: str) -> list:
    """Port of ``rotateLeadingTies`` (rrState counter rotation)."""
    if len(tied) <= 1:
        return tied
    counter = _rr_counter_get(state, key)
    if not state.dry_run:
        _rr_counter_set(state, key, counter + 1)
    start = counter % len(tied)
    return tied[start:] + tied[:start]


# ---------------------------------------------------------------------------
# 11. reset-window — soonest reset first + 60s tie band
# ---------------------------------------------------------------------------


def order_reset_window(candidates, ctx, state):
    if not candidates:
        return OrderingResult(ordered=[])
    config = resolve_reset_window_config()
    scored = [
        (
            index,
            candidate,
            get_reset_window_remaining_ms(candidate.quota, config.windows, state.now_ms),
        )
        for index, candidate in enumerate(candidates)
    ]
    scored.sort(key=lambda entry: (entry[2], entry[0]))
    best_remaining = scored[0][2]
    if not math.isfinite(best_remaining) or config.tie_band_ms <= 0:
        return OrderingResult(ordered=[entry[1] for entry in scored])
    tied = [entry for entry in scored if entry[2] - best_remaining <= config.tie_band_ms]
    if len(tied) <= 1:
        return OrderingResult(ordered=[entry[1] for entry in scored])
    rotated = _rotate_leading_ties(state, tied, f"reset-window:{ctx.group_key}")
    tied_keys = {id(entry[1]) for entry in rotated}
    ordered_entries = rotated + [e for e in scored if id(e[1]) not in tied_keys]
    return OrderingResult(ordered=[entry[1] for entry in ordered_entries])


# ---------------------------------------------------------------------------
# 12. headroom — most free capacity first
# ---------------------------------------------------------------------------


def order_headroom(candidates, ctx, state):
    if len(candidates) <= 1:
        return OrderingResult(ordered=list(candidates))
    decorated = []
    for index, candidate in enumerate(candidates):
        util_5h = candidate_window_utilization(candidate.quota, "session")
        util_7d = candidate_window_utilization(candidate.quota, "weekly")
        decorated.append((compute_headroom(util_5h, util_7d), index, candidate))
    decorated.sort(key=lambda entry: (-entry[0], entry[1]))
    return OrderingResult(
        ordered=[candidate for _, _, candidate in decorated],
        scores={c.endpoint_id: h for h, _, c in decorated},
    )


# ---------------------------------------------------------------------------
# 13. strict-random — shuffle deck, deal without replacement
# ---------------------------------------------------------------------------


def _deck_next(state: StrategyState, namespace: str, item_ids: list[str]) -> str:
    """Port of ``getNextFromDeck``/``planNextFromDeckSync`` (anti-repeat)."""
    if not item_ids:
        return ""
    if len(item_ids) == 1:
        return item_ids[0]
    ids_key = ",".join(sorted(item_ids))
    existing = state.decks.get(namespace)
    if existing and existing.ids_key == ids_key and existing.index < len(existing.order):
        selected = existing.order[existing.index]
        if not state.dry_run:
            state.decks[namespace] = DeckState(
                order=existing.order, index=existing.index + 1, ids_key=ids_key
            )
        return selected

    last_used = (
        existing.order[-1]
        if existing and existing.ids_key == ids_key and existing.order
        else None
    )
    new_order = fisher_yates_shuffle(item_ids, state.rng)
    if last_used is not None and new_order[0] == last_used and len(new_order) > 1:
        swap_idx = 1 + state.rng.randrange(len(new_order) - 1)
        new_order[0], new_order[swap_idx] = new_order[swap_idx], new_order[0]
    if not state.dry_run:
        if namespace not in state.decks and len(state.decks) >= MAX_DECKS:
            state.decks.popitem(last=False)
        state.decks[namespace] = DeckState(order=new_order, index=1, ids_key=ids_key)
    return new_order[0]


def order_strict_random(candidates, ctx, state):
    if not candidates:
        return OrderingResult(ordered=[])
    keys = [c.execution_key for c in candidates]
    selected_key = _deck_next(state, f"combo:{ctx.group_key}", keys)
    selected = next((c for c in candidates if c.execution_key == selected_key), None)
    # #3959: shuffle the fallback remainder too.
    rest = fisher_yates_shuffle(
        [c for c in candidates if c.execution_key != selected_key], state.rng
    )
    ordered = ([selected] if selected is not None else []) + rest
    return OrderingResult(ordered=ordered)


# ---------------------------------------------------------------------------
# 14. auto — multi-factor weighted scoring
# ---------------------------------------------------------------------------


def order_auto(candidates, ctx, state):
    if not candidates:
        return OrderingResult(ordered=[])
    session_binding = state.session_bindings.get(ctx.session_key) if ctx.session_key else None
    session_provider = session_binding.provider if session_binding else None
    scored = score_pool(
        candidates,
        ctx.task_type,
        weights=state.auto_weights,
        session_provider=session_provider,
        quality_by_endpoint=state.quality_by_endpoint,
        fitness_overrides=state.fitness_overrides,
    )
    return OrderingResult(
        ordered=[candidate for candidate, _, _ in scored],
        scores={candidate.endpoint_id: score for candidate, score, _ in scored},
    )


# ---------------------------------------------------------------------------
# 15. lkgp — last known good provider pinned first
# ---------------------------------------------------------------------------


def order_lkgp(candidates, ctx, state):
    ordered = sorted(candidates, key=lambda c: c.priority)
    record = state.lkgp.get(ctx.group_key)
    if record is None:
        return OrderingResult(ordered=ordered, applied=False)
    try:
        index = -1
        if record.endpoint_id:
            for i, candidate in enumerate(ordered):
                if candidate.endpoint_id == record.endpoint_id:
                    index = i
                    break
        if index < 0 and record.provider:
            for i, candidate in enumerate(ordered):
                if candidate.provider == record.provider and (
                    record.connection_id is None
                    or candidate.connection_id == record.connection_id
                ):
                    index = i
                    break
            if index < 0:
                for i, candidate in enumerate(ordered):
                    if candidate.provider == record.provider:
                        index = i
                        break
        if index > 0:
            ordered = [ordered[index]] + ordered[:index] + ordered[index + 1 :]
        return OrderingResult(ordered=ordered, applied=index >= 0)
    except Exception:  # non-fatal, exactly like upstream's try/catch
        logger.warning("lkgp ordering failed; keeping priority order", exc_info=True)
        return OrderingResult(ordered=sorted(candidates, key=lambda c: c.priority), applied=False)


# ---------------------------------------------------------------------------
# 16. context-optimized — largest context window first
# ---------------------------------------------------------------------------


def order_context_optimized(candidates, ctx, state):
    decorated = sorted(
        enumerate(candidates), key=lambda pair: (-pair[1].context_limit, pair[0])
    )
    return OrderingResult(ordered=[c for _, c in decorated])


# ---------------------------------------------------------------------------
# 17. cache-optimized — rendezvous prompt-cache affinity
# ---------------------------------------------------------------------------


def order_cache_optimized(candidates, ctx, state):
    ordered, resolution = rank_by_cache_affinity(candidates, ctx.body)
    return OrderingResult(ordered=ordered, applied=resolution is not None)


# ---------------------------------------------------------------------------
# 18. fusion — fan out to the whole panel (judge synthesis happens upstream)
# ---------------------------------------------------------------------------


def order_fusion(candidates, ctx, state):
    """OmniRoute fusion fans the prompt out to EVERY panel model in parallel;
    the ordered list is the panel (configured priority order), mode=fanout."""
    return OrderingResult(
        ordered=sorted(candidates, key=lambda c: c.priority), mode="fanout"
    )


# ---------------------------------------------------------------------------
# 19. pipeline — sequential steps in configured order
# ---------------------------------------------------------------------------


def order_pipeline(candidates, ctx, state):
    """OmniRoute pipeline threads each step's output into the next; the order
    is the step list itself, executed sequentially (mode=sequential)."""
    return OrderingResult(ordered=list(candidates), mode="sequential")


# ---------------------------------------------------------------------------
# 20. quota-share — DRR + P2C (delegated to moa_gateway.quota_scheduler, M2)
# ---------------------------------------------------------------------------


def order_quota_share(candidates, ctx, state):
    if not candidates:
        return OrderingResult(ordered=[])
    # Lazy import: M2 must never import M1 (no circular edge).
    from ..quota_scheduler.quota_share import ShareTarget, select_quota_share_target

    by_key = {}
    share_targets = []
    for candidate in candidates:
        key = candidate.execution_key
        by_key[key] = candidate
        share_targets.append(
            ShareTarget(
                execution_key=key,
                endpoint_id=candidate.endpoint_id,
                connection_id=candidate.connection_id or "",
                provider=candidate.provider,
                weight=candidate.weight,
            )
        )
    model_str = ctx.model or (candidates[0].model_id if candidates else "")
    result = select_quota_share_target(
        share_targets,
        ctx.group_key,
        model_str,
        now_ms=state.now_ms,
        max_concurrent_by_connection=ctx.max_concurrent_by_connection or None,
        commit=not state.dry_run,
    )
    ordered = [by_key[key] for key in result.ordered_keys if key in by_key]
    if not state.dry_run:
        state.quota_share_release = result.release
    return OrderingResult(ordered=ordered)


# ---------------------------------------------------------------------------
# Registry of the 20 canonical strategies
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategySpec:
    name: str
    description: str
    mode: str  # default decision mode
    internal: bool
    fn: StrategyFn


# Descriptions: OmniRoute docs/routing/AUTO-COMBO.md "All Routing Strategies".
STRATEGIES: dict[str, StrategySpec] = {}


def _register(spec: StrategySpec) -> None:
    STRATEGIES[spec.name] = spec


_register(StrategySpec("priority", "First-target ordered list with explicit priority", "single", False, order_priority))
_register(StrategySpec("weighted", "Weighted random by per-target weight", "single", False, order_weighted))
_register(StrategySpec("round-robin", "Cycle through targets in order", "single", False, order_round_robin))
_register(StrategySpec("context-relay", "Hand off context across targets (long conversations)", "single", False, order_context_relay))
_register(StrategySpec("fill-first", "Fill each target's quota before moving to next", "single", False, order_fill_first))
_register(StrategySpec("p2c", "Power-of-2-choices random load balancing", "single", False, order_p2c))
_register(StrategySpec("random", "Uniform random selection", "single", False, order_random))
_register(StrategySpec("least-used", "Pick target with lowest current load", "single", False, order_least_used))
_register(StrategySpec("cost-optimized", "Minimize $ per request given catalog pricing", "single", False, order_cost_optimized))
_register(StrategySpec("reset-aware", "Prioritize by quota reset time — short reset windows ranked higher", "single", False, order_reset_aware))
_register(StrategySpec("reset-window", "Prefer targets whose quota window resets soonest", "single", False, order_reset_window))
_register(StrategySpec("headroom", "Pick the target with the most remaining quota headroom", "single", False, order_headroom))
_register(StrategySpec("strict-random", "Random without deduplication of repeats", "single", False, order_strict_random))
_register(StrategySpec("auto", "Multi-factor weighted scoring (quota/health/cost/latency/task-fit/…)", "single", False, order_auto))
_register(StrategySpec("lkgp", "Last-Known-Good Path: pin to the last successful provider, then fall back", "single", False, order_lkgp))
_register(StrategySpec("context-optimized", "Pick target with best fit for current context size", "single", False, order_context_optimized))
_register(StrategySpec("cache-optimized", "Reorder targets by prompt-cache affinity (rendezvous hashing)", "single", False, order_cache_optimized))
_register(StrategySpec("fusion", "Fan out to a panel of models in parallel, then synthesize one answer", "fanout", False, order_fusion))
_register(StrategySpec("pipeline", "Run targets sequentially, threading each step's output into the next", "sequential", False, order_pipeline))
_register(StrategySpec("quota-share", "Internal DRR + P2C quota-share selector (auto-minted quota pools)", "single", True, order_quota_share))

# OmniRoute normalizeRoutingStrategy aliases (routingStrategies.ts).
STRATEGY_ALIASES = {
    "usage": "least-used",
    "context": "context-optimized",
    "weekly-reset": "reset-window",
    "reset-window-order": "reset-window",
}


def normalize_strategy_name(value: Any) -> str:
    """Port of ``normalizeRoutingStrategy`` (unknown → "priority").

    Accepts underscore spellings (``round_robin``) as synonyms of the
    canonical hyphenated names.
    """
    if not isinstance(value, str):
        return "priority"
    normalized = value.strip().lower().replace("_", "-")
    normalized = STRATEGY_ALIASES.get(normalized, normalized)
    if normalized in STRATEGIES:
        return normalized
    return "priority"
