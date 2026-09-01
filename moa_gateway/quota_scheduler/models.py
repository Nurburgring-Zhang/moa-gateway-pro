"""Quota telemetry contracts (M2).

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT):
``src/lib/quota/providerQuotaTelemetry.ts`` — the provider-neutral quota
value model, source-priority merge semantics and status classification.

Source priority (per dimension, higher priority wins on merge):
``provider_api > response_headers > configured > estimated > unknown``.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

QUOTA_DIMENSIONS = (
    "requests",
    "tokens",
    "input_tokens",
    "output_tokens",
    "credits",
    "currency",
    "daily_requests",
    "weekly_requests",
    "monthly_requests",
    "rate_limit",
    "unknown",
)

QUOTA_SOURCES = ("provider_api", "response_headers", "configured", "estimated", "unknown")

# OmniRoute SOURCE_PRIORITY (index = rank; lower rank wins).
SOURCE_PRIORITY: tuple[str, ...] = QUOTA_SOURCES

CONFIDENCE_LEVELS = ("authoritative", "high", "medium", "low", "unknown")

STATUSES = ("healthy", "approaching_limit", "exhausted", "unavailable", "unknown")


def source_rank(source: str) -> int:
    """OmniRoute ``sourceRank``; unknown kinds rank lowest."""
    try:
        return SOURCE_PRIORITY.index(source)
    except ValueError:
        return len(SOURCE_PRIORITY)


class QuotaValue(BaseModel):
    """One quota observation for a single dimension."""

    model_config = ConfigDict(extra="forbid")

    dimension: str = "unknown"
    limit: float | None = None
    used: float | None = None
    remaining: float | None = None
    # ISO-8601 reset instant (normalized upstream by header parsing).
    reset_at: str | None = None
    unit: str | None = None
    source: str = "unknown"
    confidence: str = "unknown"

    @property
    def is_finite_remaining(self) -> bool:
        return self.remaining is not None and math.isfinite(self.remaining)

    def is_exhausted(self) -> bool:
        """OmniRoute exhaustion test inside ``statusForValues``."""
        if self.is_finite_remaining and self.remaining <= 0:
            return True
        if (
            self.used is not None
            and self.limit is not None
            and math.isfinite(self.used)
            and math.isfinite(self.limit)
            and self.used >= self.limit
        ):
            return True
        return False


class QuotaState(BaseModel):
    """Aggregated quota state for one endpoint/connection."""

    model_config = ConfigDict(extra="forbid")

    provider_id: str
    connection_id: str = ""
    endpoint_id: str = ""
    supported: bool = False
    fetched_at: str = ""
    values: list[QuotaValue] = Field(default_factory=list)
    status: str = "unknown"
    error: str | None = None


def status_for_values(values: list[QuotaValue], approaching_threshold: float = 0.2) -> str:
    """Port of ``statusForValues`` (exhausted → approaching → healthy)."""
    if not values:
        return "unknown"
    if any(value.is_exhausted() for value in values):
        return "exhausted"
    approaching = False
    for value in values:
        if (
            value.remaining is not None
            and value.limit is not None
            and math.isfinite(value.remaining)
            and math.isfinite(value.limit)
            and value.limit > 0
            and value.remaining / value.limit <= approaching_threshold
        ):
            approaching = True
            break
    return "approaching_limit" if approaching else "healthy"


def unknown_quota_state(
    provider_id: str,
    connection_id: str = "",
    endpoint_id: str = "",
    fetched_at: str = "",
    error: str | None = None,
) -> QuotaState:
    """Port of ``unknownQuotaState``."""
    return QuotaState(
        provider_id=provider_id,
        connection_id=connection_id,
        endpoint_id=endpoint_id,
        supported=False,
        fetched_at=fetched_at,
        values=[],
        status="unknown",
        error=error,
    )


def merge_quota_values(
    existing: list[QuotaValue], incoming: list[QuotaValue]
) -> list[QuotaValue]:
    """Per-dimension merge keeping the highest-priority source per dimension.

    Port of the ``collectQuotaState`` merge loop, exposed for reuse by the
    monitor when folding header observations into stored state.
    """
    by_dimension: dict[str, QuotaValue] = {}
    for value in list(existing) + list(incoming):
        current = by_dimension.get(value.dimension)
        if current is None or source_rank(value.source) < source_rank(current.source):
            by_dimension[value.dimension] = value
    return list(by_dimension.values())


def state_to_summary(state: QuotaState) -> dict[str, Any]:
    """JSON-friendly summary used by the HTTP status surface."""
    return {
        "provider_id": state.provider_id,
        "connection_id": state.connection_id,
        "endpoint_id": state.endpoint_id,
        "supported": state.supported,
        "fetched_at": state.fetched_at,
        "status": state.status,
        "error": state.error,
        "values": [v.model_dump(exclude_none=True) for v in state.values],
    }
