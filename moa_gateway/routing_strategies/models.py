"""Data models for the OmniRoute-style routing-strategy engine.

These pydantic models describe the inputs and outputs of the pure-function
strategy pipeline: candidate endpoints (enriched with live telemetry and quota
windows), the per-request routing context, and the ordered selection result.

Design notes
------------
- ``EndpointCandidate`` deliberately mirrors the fields OmniRoute's combo
  engine feeds its 20 strategies (weight, context size, latency p95, quota
  windows, connection id, …) so each strategy can be ported 1:1.
- ``quota`` carries OmniRoute's provider-quota snapshot shape (``window5h`` /
  ``window7d`` / ``windowWeekly`` / ``windowMonthly`` / ``windows`` map,
  ``percentUsed`` in [0,1] or [0,100], ``resetAt``, ``limitReached``) so the
  reset-aware / reset-window / headroom strategies operate on the same data
  contract as OmniRoute's ``scoreResetAwareQuota``.
- ``extra="forbid"`` everywhere: typos in HTTP payloads must fail loudly.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class QuotaWindow(BaseModel):
    """One quota window observation (OmniRoute ``window5h`` / ``window7d`` …)."""

    model_config = ConfigDict(extra="forbid")

    # Percent used; values >1 are treated as percentages on a 0..100 scale
    # (mirrors OmniRoute ``normalizeWindowPercentUsed``).
    percent_used: float | None = Field(default=None, alias="percentUsed")
    # ISO-8601 string, epoch seconds/ms, or numeric string — parsed downstream
    # by ``parse_reset_time_ms`` exactly like OmniRoute's ``parseResetTimeMs``.
    reset_at: str | None = Field(default=None, alias="resetAt")


class QuotaSnapshot(BaseModel):
    """Provider quota snapshot attached to a candidate (OmniRoute quota shape)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # Overall percent used across the binding window (0..1 or 0..100).
    percent_used: float | None = Field(default=None, alias="percentUsed")
    # True when the provider reported the quota as fully consumed.
    limit_reached: bool = Field(default=False, alias="limitReached")
    # Single-signal top-level reset instant (fallback for window lookups).
    reset_at: str | None = Field(default=None, alias="resetAt")
    # Structural windows (OmniRoute field names).
    window_5h: QuotaWindow | None = Field(default=None, alias="window5h")
    window_7d: QuotaWindow | None = Field(default=None, alias="window7d")
    window_weekly: QuotaWindow | None = Field(default=None, alias="windowWeekly")
    window_monthly: QuotaWindow | None = Field(default=None, alias="windowMonthly")
    # Free-form windows map keyed by provider-specific names
    # (e.g. "weekly", "session", or model ids — OmniRoute #9330).
    windows: dict[str, QuotaWindow] | None = None


class EndpointCandidate(BaseModel):
    """A routable endpoint enriched with the telemetry routing needs.

    Field semantics follow OmniRoute's ``ResolvedComboTarget``:
    ``endpoint_id`` ≈ executionKey, ``provider`` ≈ provider id,
    ``connection_id`` ≈ account/connection, ``weight`` ≈ combo step weight.
    """

    # protected_namespaces: "model_id" is a domain field, not a pydantic API.
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    endpoint_id: str = Field(min_length=1)
    # Provider / platform identifier (used by lkgp + quota-share buckets).
    provider: str = ""
    model_id: str = ""
    # Optional account-level identity (quota-share concurrency, cache affinity).
    connection_id: str | None = None

    # --- operator-controlled ordering knobs -------------------------------
    # Explicit priority (lower = earlier); OmniRoute "priority" strategy keeps
    # the configured list order, which here is expressed via this field.
    priority: int = 0
    # Weighted / DRR share. OmniRoute ``normalizeWeight``: 0 stays 0 (disabled),
    # invalid -> 1; our engine applies the same rule.
    weight: float = 100.0

    # --- telemetry ----------------------------------------------------------
    latency_p95_ms: float = Field(default=0.0, ge=0)
    avg_latency_ms: float = Field(default=0.0, ge=0)
    latency_stddev_ms: float = Field(default=0.0, ge=0)
    success_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    # Lifetime request count (least-used ordering key).
    request_count: int = Field(default=0, ge=0)

    # --- economics / capacity ------------------------------------------------
    cost_per_1k_input: float = Field(default=0.0, ge=0)
    cost_per_1k_output: float = Field(default=0.0, ge=0)
    # Model context limit in tokens; 0/None = unknown (ranks last in
    # context-optimized, exactly like OmniRoute ``?? 0``).
    context_limit: int = Field(default=0, ge=0)
    # Number of parallel connections/accounts backing this endpoint
    # (auto strategy ``connectionDensity`` factor).
    connection_pool_size: int = Field(default=1, ge=1)

    # --- health ---------------------------------------------------------------
    # Circuit-breaker style state used by p2c / auto exactly as OmniRoute:
    # CLOSED (healthy) / HALF_OPEN / OPEN.
    breaker_state: str = "CLOSED"
    # True unless explicitly disabled by the operator.
    enabled: bool = True

    # --- quota ---------------------------------------------------------------
    # Remaining quota percentage 0..100 (auto strategy ``quota`` factor).
    quota_remaining_pct: float = Field(default=100.0, ge=0.0, le=100.0)
    quota: QuotaSnapshot | None = None

    # --- tiering / affinity ----------------------------------------------------
    # OmniRoute T10 account tier: ultra / pro / standard / free.
    account_tier: str = "standard"
    # Quota reset interval in seconds (tier reset bonus).
    quota_reset_interval_secs: float | None = None
    # Task capability hints used by the task-fitness lookup (e.g. "coder").
    tags: list[str] = Field(default_factory=list)

    @property
    def total_cost_per_1k(self) -> float:
        return self.cost_per_1k_input + self.cost_per_1k_output

    @property
    def execution_key(self) -> str:
        """OmniRoute executionKey: per-endpoint (+account when expanded)."""
        if self.connection_id:
            return f"{self.endpoint_id}@{self.connection_id}"
        return self.endpoint_id


class RoutingContext(BaseModel):
    """Per-request routing context (session, task, body hints)."""

    model_config = ConfigDict(extra="forbid")

    # Session key for context-relay stickiness + cache affinity scoping.
    session_key: str | None = None
    # Combo/group name namespace for round-robin counters, decks and DRR state.
    group_key: str = "default"
    # Task type for the fitness table (coding/analysis/…; default = neutral).
    task_type: str = "default"
    # Explicit routing-strategy override for this request (else engine default).
    strategy: str | None = None
    # Request body hints: ``prompt_cache_key`` / ``metadata.prompt_cache_key``
    # (cache-optimized) and ``model`` (quota-share per-model bucket gating).
    body: dict[str, Any] = Field(default_factory=dict)
    # Requested model string for quota-share per-model buckets.
    model: str | None = None
    # Per-connection concurrency caps for the quota-share strategy.
    max_concurrent_by_connection: dict[str, int] = Field(default_factory=dict)


class Selection(BaseModel):
    """One ranked entry of a routing decision."""

    model_config = ConfigDict(extra="forbid")

    endpoint_id: str
    rank: int
    score: float | None = None
    # Human-readable reason fragment (strategy-specific).
    reason: str = ""


class RoutingDecision(BaseModel):
    """Result of running one strategy over a candidate pool."""

    model_config = ConfigDict(extra="forbid")

    # Canonical strategy name that produced this decision.
    strategy: str
    # Ordered endpoint ids: index 0 is the primary selection, the rest form
    # the fallback chain (OmniRoute ``orderedTargets``).
    ordered: list[str]
    selections: list[Selection] = Field(default_factory=list)
    # "single" = pick-first-fallback-chain, "fanout" = fusion panel,
    # "sequential" = pipeline steps.
    mode: str = "single"
    # Per-endpoint score map when the strategy scores (auto/quota strategies).
    scores: dict[str, float] = Field(default_factory=dict)

    @property
    def selected(self) -> str | None:
        return self.ordered[0] if self.ordered else None
