"""moa_gateway.discovery.auto_configurator — Auto-configurator.

Registers discovered free models into ModelPool / Storage and cleans up
stale auto-discovered endpoints whose health checks have failed repeatedly.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from .discovery_engine import DiscoveredModel, infer_tier
from .free_model_catalog import get_api_key_env

if TYPE_CHECKING:
    from ..model_pool import ModelPool
    from ..storage import Storage

logger = logging.getLogger(__name__)


def _make_endpoint_id(platform_id: str, model_id: str) -> str:
    """Generate a unique, traceable endpoint ID.

    e.g. ("openrouter", "meta-llama/llama-3.1-8b-instruct:free")
      -> "openrouter-llama-3.1-8b-instruct"
    """
    short = model_id
    if short.endswith(":free"):
        short = short[:-5]
    if "/" in short:
        short = short.rsplit("/", 1)[-1]
    short = short.replace(":", "-")
    return f"{platform_id}-{short}"


class AutoConfigurator:
    """Register discovered models into the pool and clean up stale ones."""

    def __init__(self, pool: ModelPool, storage: Storage):
        self._pool = pool
        self._storage = storage
        self._last_configured: set[str] = set()
        self._last_errors: list[str] = []

    async def configure_discovered(self, models: list[DiscoveredModel]) -> dict[str, Any]:
        """Register discovered models into ModelPool.

        Returns {"configured": N, "skipped": N, "errors": [...]}.
        """
        configured = 0
        skipped = 0
        errors: list[str] = []
        self._last_configured = set()

        for model in models:
            try:
                endpoint_id = _make_endpoint_id(model.platform_id, model.model_id)
                tier = infer_tier(model.model_id, model.context_window)

                if model.auth_type in ("bearer", "query_param"):
                    api_key_env = get_api_key_env(model.platform_id)
                else:
                    api_key_env = ""

                ep_dict: dict[str, Any] = {
                    "endpoint_id": endpoint_id,
                    "provider": model.platform_id,
                    "model": model.model_id,
                    "tier": tier,
                    "api_base": model.base_url,
                    "api_key_env": api_key_env,
                    "cost_per_1k_input": 0.0,
                    "cost_per_1k_output": 0.0,
                    "max_tokens": 8192,
                    "timeout": 120,
                    "weight": 100,
                    "enabled": True,
                    "tags": ["free", "auto-discovered", model.platform_id],
                    "extra": {
                        "display_name": model.display_name,
                        "api_format": model.api_format,
                        "auth_type": model.auth_type,
                        "free_limit": model.free_limit,
                        "streaming": model.streaming,
                        "function_calling": model.function_calling,
                        "context_window": model.context_window,
                        "discovered_at": model.discovered_at,
                    },
                }

                self._pool.upsert_endpoint(ep_dict)
                self._last_configured.add(endpoint_id)
                configured += 1
            except Exception as e:
                errors.append(f"{model.platform_id}/{model.model_id}: {e}")
                skipped += 1
                logger.error("Failed to configure %s/%s: %s", model.platform_id, model.model_id, e)

        self._last_errors = errors
        logger.info(
            "configure_discovered: %d configured, %d skipped, %d errors",
            configured, skipped, len(errors),
        )
        return {"configured": configured, "skipped": skipped, "errors": errors}

    async def cleanup_stale(self) -> dict[str, Any]:
        """Remove auto-discovered endpoints that have failed health checks 3+ times.

        Returns {"checked": N, "removed": N}.
        """
        removed = 0
        checked = 0

        for ep_data in self._storage.list_endpoints():
            tags = ep_data.get("tags", [])
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except Exception:
                    tags = []

            if "auto-discovered" not in tags:
                continue

            checked += 1
            eid = ep_data["endpoint_id"]
            ep = self._pool.endpoints.get(eid)
            if ep and ep.consecutive_failures >= 3:
                self._pool.remove_endpoint(eid)
                removed += 1
                logger.info(
                    "Removed stale endpoint %s (consecutive_failures=%d)",
                    eid, ep.consecutive_failures,
                )

        logger.info("cleanup_stale: checked %d, removed %d", checked, removed)
        return {"checked": checked, "removed": removed}

    def get_discovery_report(self) -> dict[str, Any]:
        """Generate a summary report of auto-discovered endpoints."""
        all_endpoints = self._storage.list_endpoints()
        auto_eps: list[dict[str, Any]] = []

        for ep_data in all_endpoints:
            tags = ep_data.get("tags", [])
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except Exception:
                    tags = []
            if "auto-discovered" in tags:
                auto_eps.append(ep_data)

        tier_dist: dict[str, int] = {
            "free": 0, "lite": 0, "standard": 0, "premium": 0, "flagship": 0,
        }
        platform_set: set[str] = set()
        stale_count = 0

        for ep in auto_eps:
            tier = ep.get("tier", "standard")
            if tier in tier_dist:
                tier_dist[tier] += 1

            tags = ep.get("tags", [])
            if isinstance(tags, list):
                for tag in tags:
                    if tag not in ("free", "auto-discovered"):
                        platform_set.add(tag)

            eid = ep.get("endpoint_id", "")
            pool_ep = self._pool.endpoints.get(eid)
            if pool_ep and pool_ep.consecutive_failures >= 3:
                stale_count += 1

        return {
            "total_platforms": len(platform_set),
            "total_models": len(auto_eps),
            "new_models": len(self._last_configured),
            "stale_models": stale_count,
            "tier_distribution": tier_dist,
            "last_errors": self._last_errors,
        }
