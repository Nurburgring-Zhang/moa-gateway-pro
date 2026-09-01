"""RoutingStrategyEngine — orchestrates the 20 OmniRoute strategies.

The engine owns all mutable strategy state (round-robin counters, strict-random
decks, LKGP pins, session bindings) and the rolling telemetry store, and turns
(candidate pool, request context, strategy name) into an ordered
:class:`RoutingDecision`.

Contract points
---------------
- Settings are read LAZILY (``moa_gateway.config.get_settings()`` inside the
  call), never at import time — tests swap the settings singleton per case.
- ``enabled=False`` (settings.routing_strategies) raises
  :class:`RoutingDisabledError`; the HTTP layer maps it to 503. Opt-in rule:
  a disabled engine never influences any pre-existing routing path because
  nothing outside this package imports it.
- LKGP pins persist in the self-created ``routing_lkgp`` table; telemetry
  persists via :class:`TelemetryStore` (``routing_telemetry`` table).
- Thread-safety: every stateful section runs under one engine lock; the
  injected RNG makes concurrent resolves safe (if non-interleaved).
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections import OrderedDict

from .models import EndpointCandidate, RoutingContext, RoutingDecision, Selection
from .strategies import (
    MAX_RR_COUNTERS,
    LkgpRecord,
    STRATEGIES,
    STRATEGY_ALIASES,
    StrategyState,
    normalize_strategy_name,
)
from .telemetry import TelemetryStore

logger = logging.getLogger(__name__)

MAX_SESSION_BINDINGS = 500
MAX_LKGP_RECORDS = 500

_CREATE_LKGP_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS routing_lkgp (
    group_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    endpoint_id TEXT,
    connection_id TEXT,
    updated_at REAL NOT NULL
)
"""


class RoutingDisabledError(RuntimeError):
    """Raised when settings.routing_strategies.enabled is False."""


class UnknownStrategyError(ValueError):
    """Raised when a strategy name cannot be resolved (unknown + no default)."""


class RoutingStrategyEngine:
    def __init__(self, telemetry: TelemetryStore | None = None, seed: int | None = None) -> None:
        self._lock = threading.RLock()
        self._rng = random.Random(seed)
        self._rr_counters: OrderedDict[str, int] = OrderedDict()
        self._decks: OrderedDict = OrderedDict()
        self._lkgp: OrderedDict[str, LkgpRecord] = OrderedDict()
        self._session_bindings: OrderedDict[str, LkgpRecord] = OrderedDict()
        self._fitness_overrides: dict[str, float] = {}
        self._telemetry = telemetry
        self._lkgp_loaded = False
        self._lkgp_table_ready = False

    # ------------------------------------------------------------- settings

    def _cfg(self):
        from ..config import get_settings

        return get_settings().routing_strategies

    @property
    def telemetry(self) -> TelemetryStore:
        with self._lock:
            if self._telemetry is None:
                self._telemetry = TelemetryStore(history_window=self._cfg().history_window)
                self._telemetry.load()
            return self._telemetry

    # -------------------------------------------------------------- resolve

    def resolve(
        self,
        candidates: list[EndpointCandidate],
        strategy: str | None = None,
        context: RoutingContext | None = None,
        dry_run: bool = False,
    ) -> tuple[RoutingDecision, object]:
        """Run one strategy over the pool.

        Returns ``(decision, release)`` where ``release`` is the quota-share
        in-flight slot release callback (``None`` for every other strategy and
        for dry runs). Callers MUST invoke ``release`` exactly once when the
        dispatched request settles (OmniRoute #11371).
        """
        cfg = self._cfg()
        if not cfg.enabled:
            raise RoutingDisabledError("routing_strategies capability is disabled in settings")

        ctx = context or RoutingContext()
        if strategy is None and ctx.strategy is None:
            # Unset: configured default (normalization fail-safes to priority).
            name = normalize_strategy_name(cfg.default_strategy)
        else:
            raw = strategy if strategy is not None else ctx.strategy
            canonical = raw.strip().lower().replace("_", "-") if isinstance(raw, str) else ""
            canonical = STRATEGY_ALIASES.get(canonical, canonical)
            if canonical not in STRATEGIES:
                raise UnknownStrategyError(f"unknown routing strategy: {raw!r}")
            name = canonical
        spec = STRATEGIES[name]

        with self._lock:
            self._load_lkgp_once()
            state = StrategyState(
                rng=self._rng,
                now_ms=time.time() * 1000.0,
                dry_run=dry_run,
                rr_counters=self._rr_counters,
                decks=self._decks,
                lkgp=self._lkgp,
                session_bindings=self._session_bindings,
                auto_weights=dict(cfg.auto_weights),
                quality_by_endpoint=self.telemetry.quality_scores(),
                fitness_overrides=dict(self._fitness_overrides),
            )
            result = spec.fn(list(candidates), ctx, state)

            if not dry_run and ctx.session_key and result.ordered:
                winner = result.ordered[0]
                self._session_bindings[ctx.session_key] = LkgpRecord(
                    provider=winner.provider,
                    endpoint_id=winner.endpoint_id,
                    connection_id=winner.connection_id,
                )
                while len(self._session_bindings) > MAX_SESSION_BINDINGS:
                    self._session_bindings.popitem(last=False)

        ordered_ids = [c.endpoint_id for c in result.ordered]
        selections = [
            Selection(
                endpoint_id=c.endpoint_id,
                rank=index,
                score=result.scores.get(c.endpoint_id),
            )
            for index, c in enumerate(result.ordered)
        ]
        decision = RoutingDecision(
            strategy=spec.name,
            ordered=ordered_ids,
            selections=selections,
            mode=result.mode,
            scores={k: v for k, v in result.scores.items()},
        )
        release = state.quota_share_release
        logger.debug(
            "routing resolve strategy=%s group=%s dry_run=%s -> %s",
            spec.name,
            ctx.group_key,
            dry_run,
            ordered_ids[:5],
        )
        return decision, release

    # ------------------------------------------------------------- feedback

    def record_outcome(
        self,
        endpoint_id: str,
        latency_ms: float,
        success: bool,
        group_key: str | None = None,
        provider: str | None = None,
        connection_id: str | None = None,
    ) -> None:
        """Feed one real request outcome back into the engine.

        Successes pin the LKGP for the group (OmniRoute ``setLKGP`` runs on
        success only); every outcome updates the rolling telemetry consumed
        by p2c / least-used / auto-quality.
        """
        self.telemetry.record(endpoint_id, latency_ms, success)
        if success and group_key:
            record = LkgpRecord(
                provider=provider or "",
                endpoint_id=endpoint_id,
                connection_id=connection_id,
            )
            with self._lock:
                if group_key not in self._lkgp and len(self._lkgp) >= MAX_LKGP_RECORDS:
                    self._lkgp.popitem(last=False)
                self._lkgp[group_key] = record
            self._persist_lkgp(group_key, record)

    def set_fitness_override(self, model: str, score: float) -> None:
        """Operator seam over the static task-fitness table (clamped 0..1)."""
        with self._lock:
            self._fitness_overrides[model.lower()] = max(0.0, min(1.0, float(score)))

    def clear_fitness_override(self, model: str) -> None:
        with self._lock:
            self._fitness_overrides.pop(model.lower(), None)

    def reset_state(self) -> None:
        """Test/maintenance hook: drop in-memory mutable strategy state."""
        with self._lock:
            self._rr_counters.clear()
            self._decks.clear()
            self._session_bindings.clear()

    # ------------------------------------------------------- lkgp storage

    def _storage(self):
        from ..storage import get_storage

        return get_storage()

    def _load_lkgp_once(self) -> None:
        if self._lkgp_loaded:
            return
        self._lkgp_loaded = True
        try:
            with self._storage().conn() as conn:
                conn.execute(_CREATE_LKGP_TABLE_SQL)
                rows = conn.execute(
                    "SELECT group_key, provider, endpoint_id, connection_id FROM routing_lkgp"
                ).fetchall()
                conn.commit()
            for row in rows:
                self._lkgp[row["group_key"]] = LkgpRecord(
                    provider=row["provider"],
                    endpoint_id=row["endpoint_id"],
                    connection_id=row["connection_id"],
                )
            self._lkgp_table_ready = True
        except Exception:
            logger.warning("routing_lkgp: storage unavailable, memory-only mode", exc_info=True)

    def _persist_lkgp(self, group_key: str, record: LkgpRecord) -> None:
        try:
            with self._lock:
                if not self._lkgp_table_ready:
                    with self._storage().conn() as conn:
                        conn.execute(_CREATE_LKGP_TABLE_SQL)
                        conn.commit()
                    self._lkgp_table_ready = True
            with self._storage().conn() as conn:
                conn.execute(
                    "INSERT INTO routing_lkgp (group_key, provider, endpoint_id, connection_id, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(group_key) DO UPDATE SET provider = excluded.provider, "
                    "endpoint_id = excluded.endpoint_id, connection_id = excluded.connection_id, "
                    "updated_at = excluded.updated_at",
                    (group_key, record.provider, record.endpoint_id, record.connection_id, time.time()),
                )
                conn.commit()
        except Exception:
            logger.warning("routing_lkgp: persist failed for group=%s", group_key, exc_info=True)


# ---------------------------------------------------------------------------
# Module singleton (routes fall back to it when app.state is not wired)
# ---------------------------------------------------------------------------

_engine: RoutingStrategyEngine | None = None
_engine_lock = threading.Lock()


def get_engine() -> RoutingStrategyEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = RoutingStrategyEngine()
        return _engine


def reset_engine_for_tests() -> None:
    global _engine
    with _engine_lock:
        _engine = None
