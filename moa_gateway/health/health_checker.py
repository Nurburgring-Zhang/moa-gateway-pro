"""moa_gateway.health.health_checker — Endpoint health state tracking.

Tracks per-endpoint health using a sliding window of probe results,
latency statistics, and failure classification.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """Endpoint health status levels."""

    HEALTHY = "healthy"       # success rate > 95%
    DEGRADED = "degraded"     # success rate 50%-95%
    UNHEALTHY = "unhealthy"   # success rate < 50%
    DEAD = "dead"             # unavailable for 7+ consecutive days


@dataclass
class EndpointHealth:
    """Per-endpoint health metrics with sliding-window statistics."""

    endpoint_id: str
    status: HealthStatus = HealthStatus.HEALTHY

    # Sliding window statistics (last 100 probes)
    _results: deque = field(default_factory=lambda: deque(maxlen=100))
    _latencies: deque = field(default_factory=lambda: deque(maxlen=100))

    # Failure tracking
    consecutive_failures: int = 0
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    unavailable_since: datetime | None = None  # start of continuous unavailability

    # Error classification
    last_error: str | None = None
    error_type_counts: dict[str, int] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        if not self._results:
            return 1.0
        return sum(self._results) / len(self._results)

    @property
    def latency_p50(self) -> float:
        if not self._latencies:
            return 0.0
        sorted_l = sorted(self._latencies)
        return sorted_l[len(sorted_l) // 2]

    @property
    def latency_p95(self) -> float:
        if not self._latencies:
            return 0.0
        sorted_l = sorted(self._latencies)
        idx = int(len(sorted_l) * 0.95)
        return sorted_l[min(idx, len(sorted_l) - 1)]

    @property
    def days_unavailable(self) -> int:
        if self.unavailable_since is None:
            return 0
        return (datetime.now() - self.unavailable_since).days

    @property
    def total_probes(self) -> int:
        return len(self._results)

    def record_success(self, latency_ms: float) -> None:
        """Record a successful probe."""
        self._results.append(1)
        self._latencies.append(latency_ms)
        self.consecutive_failures = 0
        self.last_success_at = datetime.now()
        self.unavailable_since = None
        self._update_status()

    def record_failure(self, error: str, error_type: str = "unknown") -> None:
        """Record a failed probe."""
        self._results.append(0)
        self.consecutive_failures += 1
        self.last_failure_at = datetime.now()
        self.last_error = error
        self.error_type_counts[error_type] = self.error_type_counts.get(error_type, 0) + 1
        if self.consecutive_failures == 1:
            self.unavailable_since = datetime.now()
        self._update_status()

    def _update_status(self) -> None:
        """Recompute health status based on current metrics."""
        rate = self.success_rate
        if self.consecutive_failures == 0 and rate > 0.95:
            self.status = HealthStatus.HEALTHY
        elif self.days_unavailable >= 7:
            self.status = HealthStatus.DEAD
        elif rate > 0.5:
            self.status = HealthStatus.DEGRADED
        else:
            self.status = HealthStatus.UNHEALTHY

    def to_dict(self) -> dict[str, Any]:
        """Serialize for persistence."""
        return {
            "endpoint_id": self.endpoint_id,
            "status": self.status.value,
            "results": list(self._results),
            "latencies": list(self._latencies),
            "consecutive_failures": self.consecutive_failures,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_failure_at": self.last_failure_at.isoformat() if self.last_failure_at else None,
            "unavailable_since": self.unavailable_since.isoformat() if self.unavailable_since else None,
            "last_error": self.last_error,
            "error_type_counts": dict(self.error_type_counts),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EndpointHealth:
        """Deserialize from persisted state."""
        eh = cls(endpoint_id=data["endpoint_id"])
        eh.status = HealthStatus(data.get("status", "healthy"))
        eh._results = deque(data.get("results", []), maxlen=100)
        eh._latencies = deque(data.get("latencies", []), maxlen=100)
        eh.consecutive_failures = data.get("consecutive_failures", 0)
        eh.last_success_at = (
            datetime.fromisoformat(data["last_success_at"])
            if data.get("last_success_at")
            else None
        )
        eh.last_failure_at = (
            datetime.fromisoformat(data["last_failure_at"])
            if data.get("last_failure_at")
            else None
        )
        eh.unavailable_since = (
            datetime.fromisoformat(data["unavailable_since"])
            if data.get("unavailable_since")
            else None
        )
        eh.last_error = data.get("last_error")
        eh.error_type_counts = data.get("error_type_counts", {})
        return eh

    def summary(self) -> dict[str, Any]:
        """Return a summary dict suitable for API responses."""
        return {
            "endpoint_id": self.endpoint_id,
            "status": self.status.value,
            "success_rate": round(self.success_rate, 4),
            "latency_p50_ms": round(self.latency_p50, 2),
            "latency_p95_ms": round(self.latency_p95, 2),
            "consecutive_failures": self.consecutive_failures,
            "total_probes": self.total_probes,
            "days_unavailable": self.days_unavailable,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_failure_at": self.last_failure_at.isoformat() if self.last_failure_at else None,
            "unavailable_since": self.unavailable_since.isoformat() if self.unavailable_since else None,
            "last_error": self.last_error,
            "error_type_counts": dict(self.error_type_counts),
        }


class HealthChecker:
    """Manage health state for all endpoints."""

    def __init__(self, storage: Any | None = None):
        self._health: dict[str, EndpointHealth] = {}
        self._storage = storage

    def get_health(self, endpoint_id: str) -> EndpointHealth:
        """Get or create health record for an endpoint."""
        if endpoint_id not in self._health:
            self._health[endpoint_id] = EndpointHealth(endpoint_id=endpoint_id)
        return self._health[endpoint_id]

    def get_all_health(self) -> dict[str, EndpointHealth]:
        """Return all health records."""
        return dict(self._health)

    def get_healthy_endpoints(self) -> list[str]:
        """Return IDs of all healthy endpoints."""
        return [
            eid for eid, h in self._health.items()
            if h.status == HealthStatus.HEALTHY
        ]

    def get_endpoints_by_status(self, status: HealthStatus) -> list[str]:
        """Return IDs of endpoints matching the given status."""
        return [
            eid for eid, h in self._health.items()
            if h.status == status
        ]

    def remove_health(self, endpoint_id: str) -> None:
        """Remove health record for an endpoint (after purge)."""
        self._health.pop(endpoint_id, None)

    def get_summary(self) -> dict[str, Any]:
        """Return aggregate health summary."""
        status_counts: dict[str, int] = {}
        for h in self._health.values():
            s = h.status.value
            status_counts[s] = status_counts.get(s, 0) + 1
        return {
            "total_tracked": len(self._health),
            "status_counts": status_counts,
            "endpoints": [h.summary() for h in self._health.values()],
        }

    async def save_state(self) -> None:
        """Persist health state to storage."""
        if not self._storage:
            return
        import json
        data = {eid: h.to_dict() for eid, h in self._health.items()}
        self._storage.set_config_override("_health_state", json.dumps(data, ensure_ascii=False))
        logger.debug("Saved health state for %d endpoints", len(data))

    async def load_state(self) -> None:
        """Load health state from storage."""
        if not self._storage:
            return
        import json
        raw = self._storage.get_config_overrides().get("_health_state")
        if not raw:
            return
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse health state from storage")
            return
        for eid, h_dict in data.items():
            try:
                self._health[eid] = EndpointHealth.from_dict(h_dict)
            except Exception as e:
                logger.warning("Failed to load health state for %s: %s", eid, e)
        logger.info("Loaded health state for %d endpoints", len(self._health))
