"""Quota collection — per-dimension source-priority merge (M2).

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT):
``src/lib/quota/providerQuotaTelemetry.ts`` — the adapter contract and
``collectQuotaState``'s per-dimension merge (a higher-priority source never
gets shadowed by a lower one; adapters that error are skipped and recorded
as ``error``). Three real adapters ship with the gateway:

- :class:`ResponseHeaderAdapter` — values parsed from provider response
  headers (source ``response_headers``).
- :class:`ConfiguredQuotaAdapter` — operator-configured static limits per
  provider/dimension (source ``configured``).
- :class:`EstimatedQuotaAdapter` — estimates derived from observed usage
  counters against configured limits (source ``estimated``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Protocol

from .models import (
    QuotaState,
    QuotaValue,
    source_rank,
    status_for_values,
)

logger = logging.getLogger(__name__)


class QuotaSourceAdapter(Protocol):
    """Port of OmniRoute ``QuotaSourceAdapter``."""

    kind: str

    def supports(self, provider_id: str) -> bool:  # pragma: no cover - protocol
        ...

    def read(self, connection: dict[str, Any]) -> list[QuotaValue]:  # pragma: no cover
        ...


class ResponseHeaderAdapter:
    """Wraps pre-parsed header QuotaValues (source=response_headers)."""

    kind = "response_headers"

    def __init__(self, values_by_provider: dict[str, list[QuotaValue]] | None = None,
                 values: list[QuotaValue] | None = None) -> None:
        self._values_by_provider = values_by_provider or {}
        self._values = values or []

    def supports(self, provider_id: str) -> bool:
        return bool(self._values) or provider_id in self._values_by_provider

    def read(self, connection: dict[str, Any]) -> list[QuotaValue]:
        provider = str(connection.get("provider", ""))
        return list(self._values_by_provider.get(provider, [])) + list(self._values)


class ConfiguredQuotaAdapter:
    """Static operator limits: ``{provider: {dimension: {limit, reset_at, unit}}}``."""

    kind = "configured"

    def __init__(self, limits_by_provider: dict[str, dict[str, dict[str, Any]]]) -> None:
        self._limits = limits_by_provider or {}

    def supports(self, provider_id: str) -> bool:
        return provider_id in self._limits

    def read(self, connection: dict[str, Any]) -> list[QuotaValue]:
        provider = str(connection.get("provider", ""))
        configured = self._limits.get(provider, {})
        values: list[QuotaValue] = []
        for dimension, spec in configured.items():
            if not isinstance(spec, dict):
                continue
            limit = spec.get("limit")
            used = spec.get("used")
            remaining = spec.get("remaining")
            if remaining is None and limit is not None and used is not None:
                remaining = max(0.0, float(limit) - float(used))
            values.append(
                QuotaValue(
                    dimension=dimension,
                    limit=float(limit) if limit is not None else None,
                    used=float(used) if used is not None else None,
                    remaining=float(remaining) if remaining is not None else None,
                    reset_at=spec.get("reset_at"),
                    unit=spec.get("unit"),
                    source="configured",
                    confidence="medium",
                )
            )
        return values


class EstimatedQuotaAdapter:
    """Estimate remaining budget from observed usage counters.

    ``usage_by_provider`` maps provider → {dimension: {"used": n}}; limits
    come from the configured adapter map so the estimate has a denominator.
    """

    kind = "estimated"

    def __init__(
        self,
        usage_by_provider: dict[str, dict[str, float]],
        limits_by_provider: dict[str, dict[str, dict[str, Any]]],
    ) -> None:
        self._usage = usage_by_provider or {}
        self._limits = limits_by_provider or {}

    def supports(self, provider_id: str) -> bool:
        return provider_id in self._usage and provider_id in self._limits

    def read(self, connection: dict[str, Any]) -> list[QuotaValue]:
        provider = str(connection.get("provider", ""))
        usage = self._usage.get(provider, {})
        limits = self._limits.get(provider, {})
        values: list[QuotaValue] = []
        for dimension, used in usage.items():
            spec = limits.get(dimension)
            if not isinstance(spec, dict) or spec.get("limit") is None:
                continue
            limit = float(spec["limit"])
            used_f = float(used)
            values.append(
                QuotaValue(
                    dimension=dimension,
                    limit=limit,
                    used=used_f,
                    remaining=max(0.0, limit - used_f),
                    unit=spec.get("unit"),
                    source="estimated",
                    confidence="low",
                )
            )
        return values


def collect_quota_state(
    connection: dict[str, Any],
    adapters: list[Any],
    approaching_threshold: float = 0.2,
    fetched_at: str | None = None,
) -> QuotaState:
    """Port of ``collectQuotaState`` (per-dimension merge by source rank).

    ``connection`` carries at least ``id`` and ``provider`` (plus
    ``endpoint_id`` for the gateway's bookkeeping). Adapter errors are
    captured in ``error`` without aborting the merge — exactly upstream.
    """
    if fetched_at is None:
        now = datetime.now(timezone.utc)
        fetched_at = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"

    provider = str(connection.get("provider", ""))
    by_dimension: dict[str, QuotaValue] = {}
    supported = False
    last_error: str | None = None

    for adapter in adapters:
        try:
            if not adapter.supports(provider):
                continue
        except Exception as exc:  # defensive: adapter contract violation
            last_error = str(exc)
            continue
        supported = True
        try:
            values = adapter.read(connection)
        except Exception as exc:
            last_error = str(exc) if not isinstance(exc, type) else repr(exc)
            continue
        for value in values:
            current = by_dimension.get(value.dimension)
            if current is None or source_rank(value.source) < source_rank(current.source):
                by_dimension[value.dimension] = value

    values = list(by_dimension.values())
    if values:
        status = status_for_values(values, approaching_threshold)
    else:
        status = "unavailable" if supported else "unknown"
    return QuotaState(
        provider_id=provider,
        connection_id=str(connection.get("id", "")),
        endpoint_id=str(connection.get("endpoint_id", "")),
        supported=supported,
        fetched_at=fetched_at,
        values=values,
        status=status,
        error=last_error,
    )


def fold_values(state: QuotaState, incoming: list[QuotaValue],
                approaching_threshold: float = 0.2) -> QuotaState:
    """Merge NEW observations into an existing state and re-classify.

    Temporal-fold semantics (distinct from the single-pass
    :func:`merge_quota_values` port of ``collectQuotaState``): an incoming
    observation wins its dimension when its source rank is equal or better
    than the stored one — a fresh response-header reading must update the
    previous response-header reading. Lower-priority sources still never
    shadow higher-priority stored values.
    """
    by_dimension: dict[str, QuotaValue] = {v.dimension: v for v in state.values}
    for value in incoming:
        current = by_dimension.get(value.dimension)
        if current is None or source_rank(value.source) <= source_rank(current.source):
            by_dimension[value.dimension] = value
    merged = list(by_dimension.values())
    status = status_for_values(merged, approaching_threshold) if merged else state.status
    return state.model_copy(update={"values": merged, "status": status, "supported": True})
