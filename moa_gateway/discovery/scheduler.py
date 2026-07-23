"""moa_gateway.discovery.scheduler — Periodic discovery scheduler.

Uses asyncio.create_task for scheduling (no APScheduler dependency).
First run starts after a 60-second delay; subsequent runs repeat every interval_hours.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .auto_configurator import AutoConfigurator
from .discovery_engine import FreeModelDiscoveryEngine

logger = logging.getLogger(__name__)


class DiscoveryScheduler:
    """Schedule periodic model discovery and auto-configuration."""

    def __init__(
        self,
        engine: FreeModelDiscoveryEngine,
        configurator: AutoConfigurator,
        probe_engine=None,
        purge_manager=None,
        benchmark_engine=None,
        capability_probe=None,
        optimizer=None,
    ):
        self._engine = engine
        self._configurator = configurator
        self._probe_engine = probe_engine
        self._purge_manager = purge_manager
        self._benchmark_engine = benchmark_engine
        self._capability_probe = capability_probe
        self._optimizer = optimizer
        self._task: asyncio.Task[None] | None = None
        self._last_result: dict[str, Any] | None = None
        self._running = False

    async def start(self, interval_hours: int = 24) -> None:
        """Start the scheduled discovery loop.

        The first run fires after 60 seconds; subsequent runs every interval_hours.
        """
        if self._task is not None:
            logger.warning("DiscoveryScheduler is already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(interval_hours))
        logger.info("DiscoveryScheduler started (interval=%dh)", interval_hours)

    async def stop(self) -> None:
        """Stop the scheduler and wait for the loop to exit."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("DiscoveryScheduler stopped")

    async def run_once(self) -> dict[str, Any]:
        """Manually trigger one full discovery + configure + cleanup cycle."""
        try:
            models = await self._engine.discover_all()
            result = await self._configurator.configure_discovered(models)
            cleanup = await self._configurator.cleanup_stale()

            # Task #43: Start health monitoring for newly configured endpoints
            health_monitoring_started = 0
            if self._probe_engine and hasattr(self._configurator, "_last_configured"):
                for eid in self._configurator._last_configured:
                    try:
                        await self._probe_engine.start_monitoring(eid)
                        health_monitoring_started += 1
                    except Exception as e:
                        logger.warning("Failed to start monitoring %s: %s", eid, e)

            # Task #43: Run purge check for dead endpoints
            purge_result = {"purged": []}
            if self._purge_manager:
                try:
                    purged = await self._purge_manager.check_and_purge()
                    purge_result = {"purged": purged}
                except Exception as e:
                    logger.warning("Purge check failed: %s", e)

            # Task #44: Trigger benchmark and capability probe
            benchmark_result = {"status": "skipped"}
            capability_result = {"status": "skipped"}
            if self._benchmark_engine:
                try:
                    bench_results = await self._benchmark_engine.benchmark_all()
                    benchmark_result = {
                        "status": "completed",
                        "total": len(bench_results),
                        "successful": sum(1 for r in bench_results.values() if r.success),
                    }
                except Exception as e:
                    logger.warning("Benchmark trigger failed: %s", e)
                    benchmark_result = {"status": "error", "error": str(e)}
            if self._capability_probe:
                try:
                    cap_results = await self._capability_probe.probe_all()
                    capability_result = {
                        "status": "completed",
                        "total": len(cap_results),
                    }
                except Exception as e:
                    logger.warning("Capability probe trigger failed: %s", e)
                    capability_result = {"status": "error", "error": str(e)}

            # Task #45: Trigger MOA optimisation
            optimizer_result = {"status": "skipped"}
            if self._optimizer:
                try:
                    opt_res = await self._optimizer.run_daily_optimization()
                    optimizer_result = {
                        "status": "completed",
                        "best_strategy": opt_res.best_strategy,
                        "quality_score": round(opt_res.quality_score, 4),
                        "recommendation": opt_res.recommendation,
                    }
                except Exception as e:
                    logger.warning("MOA optimisation trigger failed: %s", e)
                    optimizer_result = {"status": "error", "error": str(e)}

            self._last_result = {
                "timestamp": time.time(),
                "status": "success",
                "discovered": len(models),
                "configured": result.get("configured", 0),
                "skipped": result.get("skipped", 0),
                "errors": result.get("errors", []),
                "cleanup": cleanup,
                "health_monitoring_started": health_monitoring_started,
                "purge": purge_result,
                "benchmark": benchmark_result,
                "capabilities": capability_result,
                "optimizer": optimizer_result,
            }
            logger.info(
                "Discovery cycle complete: %d discovered, %d configured, %d removed, "
                "%d health-monitored, %d purged",
                len(models),
                result.get("configured", 0),
                cleanup.get("removed", 0),
                health_monitoring_started,
                len(purge_result.get("purged", [])),
            )
        except Exception as e:
            self._last_result = {
                "timestamp": time.time(),
                "status": "error",
                "error": str(e),
            }
            logger.error("Discovery cycle failed: %s", e, exc_info=True)

        return self._last_result

    def get_status(self) -> dict[str, Any]:
        """Return the last discovery result and current running state."""
        if self._last_result is None:
            return {"status": "never_run", "running": self._running}

        return {
            "status": self._last_result.get("status", "unknown"),
            "running": self._running,
            "last_run": self._last_result.get("timestamp", 0),
            "discovered": self._last_result.get("discovered", 0),
            "configured": self._last_result.get("configured", 0),
            "skipped": self._last_result.get("skipped", 0),
            "errors": self._last_result.get("errors", []),
            "cleanup": self._last_result.get("cleanup", {}),
            "health_monitoring_started": self._last_result.get("health_monitoring_started", 0),
            "purge": self._last_result.get("purge", {}),
            "benchmark": self._last_result.get("benchmark", {}),
            "capabilities": self._last_result.get("capabilities", {}),
            "optimizer": self._last_result.get("optimizer", {}),
        }

    async def _run_loop(self, interval_hours: int) -> None:
        """Main scheduling loop: first run after 60s, then every interval_hours."""
        try:
            await asyncio.sleep(60)
            await self.run_once()

            interval_seconds = interval_hours * 3600
            while self._running:
                await asyncio.sleep(interval_seconds)
                if not self._running:
                    break
                await self.run_once()
        except asyncio.CancelledError:
            logger.info("DiscoveryScheduler loop cancelled")
            raise
