"""moa_gateway.health.purge_manager — Automatic purge of dead endpoints.

Removes endpoints that have been unavailable for longer than a threshold.
Supports manual restore of purged endpoints.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections import deque
from datetime import datetime
from typing import Any

from .health_checker import EndpointHealth, HealthChecker

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

    def _skip_mock_enabled(self) -> bool:
        """Whether mock-backed endpoints are exempt from purging (D3)."""
        try:
            from ..config import get_settings

            return bool(get_settings().health.skip_mock_endpoints)
        except Exception:
            return True

    def _is_mock_endpoint_id(self, endpoint_id: str) -> bool:
        """Best-effort mock detection via the model pool."""
        pool = self._model_pool
        if pool is None or not hasattr(pool, "endpoints"):
            return False
        ep = pool.endpoints.get(endpoint_id)
        if ep is None:
            return False
        if hasattr(pool, "_ep_is_mock"):
            with contextlib.suppress(Exception):
                return bool(pool._ep_is_mock(ep))
        from ..providers import NO_AUTH_PROVIDERS, is_mock_key

        cfg = getattr(ep, "config", ep)
        key = getattr(cfg, "api_key_runtime", "") or getattr(cfg, "api_key", "") or ""
        provider = getattr(cfg, "provider", "") or ""
        return is_mock_key(key) and provider not in NO_AUTH_PROVIDERS

    async def check_and_purge(self) -> list[str]:
        """Check all endpoints and purge those exceeding the threshold.

        Returns list of purged endpoint IDs.
        """
        purged: list[str] = []
        all_health = self._health_checker.get_all_health()
        skip_mock = self._skip_mock_enabled()

        for endpoint_id, health in list(all_health.items()):
            if health.days_unavailable >= self._purge_threshold_days:
                # D3: mock-backed endpoints have no real upstream; their
                # "unavailability" is expected and must never trigger purge.
                if skip_mock and self._is_mock_endpoint_id(endpoint_id):
                    logger.info(
                        "Skipping purge of mock-backed endpoint %s", endpoint_id
                    )
                    continue
                await self._purge_endpoint(endpoint_id, health)
                purged.append(endpoint_id)

        if purged:
            logger.info(
                "Purged %d endpoints (threshold=%d days): %s",
                len(purged),
                self._purge_threshold_days,
                purged,
            )
        return purged

    def _endpoint_config_snapshot(self, endpoint_id: str) -> dict | None:
        """Capture a serialisable snapshot of the endpoint config (for restore).

        Security (B2 review H1): upstream API keys are NEVER persisted in the
        snapshot — keys are re-resolved from ``api_key_env`` at restore time.
        """
        pool = self._model_pool
        if pool is None or not hasattr(pool, "endpoints"):
            return None
        ep = pool.endpoints.get(endpoint_id)
        if ep is None:
            return None
        cfg = getattr(ep, "config", None)
        if cfg is None:
            return None
        dump = getattr(cfg, "model_dump", None)
        if callable(dump):
            with contextlib.suppress(Exception):
                data = dump(mode="json")
                if isinstance(data, dict):
                    data.pop("api_key", None)
                    data.pop("api_key_runtime", None)
                    data["endpoint_id"] = endpoint_id
                    return data
        with contextlib.suppress(Exception):
            data = {k: v for k, v in vars(cfg).items() if not k.startswith("_")}
            data.pop("api_key", None)
            data.pop("api_key_runtime", None)
            data["endpoint_id"] = endpoint_id
            return data
        return None

    async def _purge_endpoint(self, endpoint_id: str, health: EndpointHealth) -> None:
        """Execute purge: remove from model_pool, record log."""
        purge_record = {
            "endpoint_id": endpoint_id,
            "purged_at": datetime.now().isoformat(),
            "days_unavailable": health.days_unavailable,
            "last_error": health.last_error,
            "error_type_counts": dict(health.error_type_counts),
            "success_rate_at_purge": round(health.success_rate, 4),
            "status_at_purge": health.status.value,
            # D3: keep the config so the endpoint can be restored later
            "endpoint_config": self._endpoint_config_snapshot(endpoint_id),
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
            endpoint_id,
            health.days_unavailable,
            health.success_rate,
        )

    def get_purge_history(self) -> list[dict]:
        """Return in-memory purge history."""
        return list(self._purged_endpoints)

    async def restore_endpoint(self, endpoint_id: str, endpoint_config: dict) -> bool:
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

    async def restore_purged_endpoints(self) -> list[str]:
        """Restore endpoints purged in previous runs (D3 recovery).

        Conservative semantics (B2 review M1/M2):
        - endpoints still defined in the static config (settings.models) are
          restored — static config is the source of truth;
        - dynamic endpoints with a stored config snapshot are restored only
          when ``health.auto_restore_purged`` is enabled;
        - processed records are marked ``restored_at`` so a legitimately
          re-purged endpoint is not resurrected on every restart;
        - keys are never restored from snapshots; they are re-resolved from
          ``api_key_env`` by the normal endpoint build path.

        Returns the list of restored endpoint IDs.
        """
        pool = self._model_pool
        if pool is None or not hasattr(pool, "endpoints"):
            return []
        auto_restore_dynamic = False
        try:
            from ..config import get_settings

            auto_restore_dynamic = bool(
                getattr(get_settings().health, "auto_restore_purged", False)
            )
        except Exception:
            auto_restore_dynamic = False
        records: list[dict] = []
        if self._storage is not None and hasattr(self._storage, "list_purge_records"):
            try:
                records = self._storage.list_purge_records(limit=1000)
            except Exception as e:
                logger.warning("Failed to read purge records: %s", e)
        # latest un-restored record per endpoint (records ordered DESC)
        latest: dict[str, dict] = {}
        for rec in records:
            eid = rec.get("endpoint_id") or ""
            if eid and eid not in latest and not rec.get("restored_at"):
                latest[eid] = rec

        static_models: dict[str, Any] = {}
        try:
            from ..config import get_settings

            static_models = {m.id: m for m in get_settings().models}
        except Exception:
            static_models = {}

        restored: list[str] = []
        for eid, rec in latest.items():
            if eid in pool.endpoints:
                self._mark_restored(rec)
                continue
            cfg_dict: dict | None = None
            if eid in static_models:
                m = static_models[eid]
                tier = getattr(m.tier, "value", m.tier)
                cfg_dict = {
                    "endpoint_id": eid,
                    "provider": m.provider,
                    "model": m.model,
                    "tier": tier,
                    "api_base": getattr(m, "api_base", "") or "",
                    "api_key_env": getattr(m, "api_key_env", "") or "",
                    "enabled": True,
                }
            else:
                if not auto_restore_dynamic:
                    continue
                raw = rec.get("endpoint_config")
                if isinstance(raw, dict):
                    cfg_dict = dict(raw)
                elif isinstance(raw, str) and raw:
                    with contextlib.suppress(Exception):
                        loaded = json.loads(raw)
                        if isinstance(loaded, dict):
                            cfg_dict = loaded
                if cfg_dict is None:
                    continue
                # never resurrect secrets from snapshots
                cfg_dict.pop("api_key", None)
                cfg_dict.pop("api_key_runtime", None)
            cfg_dict.setdefault("endpoint_id", eid)
            cfg_dict.setdefault("enabled", True)
            if await self.restore_endpoint(eid, cfg_dict):
                restored.append(eid)
                self._mark_restored(rec)
        return restored

    def _mark_restored(self, rec: dict) -> None:
        """Mark a purge record as processed so restore stays idempotent."""
        rec["restored_at"] = datetime.now().isoformat()
        storage = self._storage
        if storage is not None and hasattr(storage, "mark_purge_record_restored"):
            with contextlib.suppress(Exception):
                storage.mark_purge_record_restored(
                    rec.get("id"), rec.get("endpoint_id") or ""
                )

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
