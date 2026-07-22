"""Deep Health Checks — Liveness / Readiness / Startup probes."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class HealthStatus(str, Enum):
    """Component health levels."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class ComponentHealth:
    """Result of a single component health check."""

    name: str
    status: HealthStatus
    latency_ms: float
    message: str = ""
    last_check: float = 0.0


class HealthChecker:
    """Deep health checker with liveness/readiness/startup probes."""

    def __init__(self) -> None:
        self._checks: dict[str, Callable] = {}
        self._results: dict[str, ComponentHealth] = {}
        self._startup_complete = False
        self._start_time = time.time()

    def register_check(self, name: str, check_fn: Callable) -> None:
        """Register a health check function (sync or async)."""
        self._checks[name] = check_fn

    async def liveness(self) -> dict:
        """Liveness probe — is the process alive?"""
        return {
            "status": "alive",
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "pid": os.getpid(),
        }

    async def readiness(self) -> dict:
        """Readiness probe — can we accept traffic?"""
        if not self._startup_complete:
            return {"status": "not_ready", "reason": "startup_incomplete"}

        results = await self.run_checks()
        overall = HealthStatus.HEALTHY
        for r in results.values():
            if r.status == HealthStatus.UNHEALTHY:
                overall = HealthStatus.UNHEALTHY
                break
            elif r.status == HealthStatus.DEGRADED:
                overall = HealthStatus.DEGRADED

        return {
            "status": overall.value,
            "components": {
                name: {
                    "status": r.status.value,
                    "latency_ms": r.latency_ms,
                    "message": r.message,
                }
                for name, r in results.items()
            },
        }

    async def startup(self) -> dict:
        """Startup probe — has initialization completed?"""
        return {
            "status": "started" if self._startup_complete else "starting",
            "startup_complete": self._startup_complete,
        }

    def mark_ready(self) -> None:
        """Mark the application as ready to serve traffic."""
        self._startup_complete = True

    def mark_not_ready(self) -> None:
        """Mark application as not ready (e.g. during shutdown)."""
        self._startup_complete = False

    async def run_checks(self) -> dict[str, ComponentHealth]:
        """Run all registered health checks."""
        for name, check_fn in self._checks.items():
            start = time.time()
            try:
                if asyncio.iscoroutinefunction(check_fn):
                    result = await check_fn()
                else:
                    result = check_fn()
                latency = (time.time() - start) * 1000
                self._results[name] = ComponentHealth(
                    name=name,
                    status=HealthStatus.HEALTHY if result else HealthStatus.UNHEALTHY,
                    latency_ms=round(latency, 2),
                    last_check=time.time(),
                )
            except Exception as e:
                latency = (time.time() - start) * 1000
                self._results[name] = ComponentHealth(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    latency_ms=round(latency, 2),
                    message=str(e),
                    last_check=time.time(),
                )
        return self._results


# Global singleton
health_checker = HealthChecker()
