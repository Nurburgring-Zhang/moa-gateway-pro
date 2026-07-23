"""moa_gateway.health.purge_manager — Automatic purge of dead endpoints.

Removes endpoints that have been unavailable for longer than a threshold.
Supports manual restore of purged endpoints.
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from typing import Any

from .health_checker import EndpointHealth, HealthChecker, HealthStatus

logger = logging.getLogger(__name__)


class PurgeManager:
    """Manage automatic cleanup of unavailable endpoints."""

    def __init__(
        self,
        health_checker: HealthChecker,
        model_pool: Any | None = None,
        storage: Any | None = None,
        purge_threshold_days: int = 7,
        benchmark_engine: Any | None = None,
        capability_probe: Any | None = None,
    ):
        self._health_checker = health_checker
        self._model_pool = model_pool
        self._storage = storage
        self._purge_threshold_days = purge_threshold_days
        # P2-8: Use deque to bound purge history memory
        self._purged_endpoints: deque[dict] = deque(maxlen=500)
        # P2-6: Hold references to benchmark/capability for cleanup
        self._benchmark_engine = benchmark_engine
        self._capability_probe = capability_probe

    async def check_and_purge(self) -> list[str]:
        """Check all endpoints and purge those exceeding the threshold.

        Returns list of purged endpoint IDs.
        """
        purged: list[str] = []
        all_health = self._health_checker.get_all_health()

        for endpoint_id, health in list(all_health.items()):
            if health.days_unavailable >= self._purge_threshold_days:
                await self._purge_endpoint(endpoint_id, health)
                purged.append(endpoint_id)

        if purged:
            logger.info(
                "Purged %d endpoints (threshold=%d days): %s",
                len(purged), self._purge_threshold_days, purged,
            )
        return purged

    async def _purge_endpoint(
        self, endpoint_id: str, health: EndpointHealth
    ) -> None:
        """Execute purge: remove from model_pool, record log."""
        purge_record = {
            "endpoint_id": endpoint_id,
            "purged_at": datetime.now().isoformat(),
            "days_unavailable": health.days_unavailable,
            "last_error": health.last_error,
            "error_type_counts": dict(health.error_type_counts),
            "success_rate_at_purge": round(health.success_rate, 4),
            "status_at_purge": health.status.value,
        }
        self._purged_endpoints.append(purge_record)

        # Persist purge record
        if self._storage and hasattr(self._storage, "save_purge_record"):
            try:
                self._storage.save_purge_record(purge_record)
            except Exception as e:
                logger.warning("Failed to persist purge record for %s: %s", endpoint_id, e)

        # Remove from model_pool
        if self._model_pool and hasattr(self._model_pool, "remove_endpoint"):
            try:
                self._model_pool.remove_endpoint(endpoint_id)
            except Exception as e:
                logger.warning("Failed to remove endpoint %s from pool: %s", endpoint_id, e)

        # P2-6: Remove benchmark metrics for the purged endpoint
        if self._benchmark_engine and hasattr(self._benchmark_engine, "remove_endpoint"):
            try:
                self._benchmark_engine.remove_endpoint(endpoint_id)
            except Exception as e:
                logger.warning("Failed to remove benchmark data for %s: %s", endpoint_id, e)

        # P2-6: Remove capability results for the purged endpoint
        if self._capability_probe and hasattr(self._capability_probe, "remove_endpoint"):
            try:
                self._capability_probe.remove_endpoint(endpoint_id)
            except Exception as e:
                logger.warning("Failed to remove capability data for %s: %s", endpoint_id, e)

        # Remove from health_checker
        self._health_checker.remove_health(endpoint_id)

        logger.info(
            "Purged endpoint %s: unavailable for %d days, success_rate=%.2f",
            endpoint_id, health.days_unavailable, health.success_rate,
        )

    def get_purge_history(self) -> list[dict]:
        """Return in-memory purge history."""
        return list(self._purged_endpoints)

    async def restore_endpoint(
        self, endpoint_id: str, endpoint_config: dict
    ) -> bool:
        """Restore a purged endpoint (manual operation).

        Args:
            endpoint_id: The endpoint ID to restore.
            endpoint_config: Configuration dict for upsert_endpoint.

        Returns True if restoration succeeded.
        """
        if self._model_pool and hasattr(self._model_pool, "upsert_endpoint"):
            try:
                self._model_pool.upsert_endpoint(endpoint_config)
                # Reset health state for the restored endpoint
                self._health_checker.remove_health(endpoint_id)
                _ = self._health_checker.get_health(endpoint_id)  # create fresh
                logger.info("Restored endpoint: %s", endpoint_id)
                return True
            except Exception as e:
                logger.error("Failed to restore endpoint %s: %s", endpoint_id, e)
                return False
        return False

    def set_cleanup_targets(self, benchmark_engine=None, capability_probe=None) -> None:
        """Set benchmark/capability references after construction (P2-6).

        Called by server.py after benchmark system is initialized, since
        health system (and PurgeManager) are created first.
        """
        self._benchmark_engine = benchmark_engine or self._benchmark_engine
        self._capability_probe = capability_probe or self._capability_probe

    def get_status(self) -> dict[str, Any]:
        """Return purge manager status."""
        return {
            "purge_threshold_days": self._purge_threshold_days,
            "total_purged": len(self._purged_endpoints),
            "purge_history": list(self._purged_endpoints),
        }
