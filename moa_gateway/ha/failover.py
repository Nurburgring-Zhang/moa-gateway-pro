"""Failover Management — automatic provider switching on failure."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .circuit_breaker import CircuitState, breaker_registry

logger = logging.getLogger(__name__)


@dataclass
class ProviderStatus:
    """Health status of a single provider."""

    name: str
    priority: int
    healthy: bool = True
    last_success: float | None = None
    last_failure: float | None = None
    consecutive_failures: int = 0


class FailoverManager:
    """Manages provider failover with priority-based selection."""

    def __init__(self, max_consecutive_failures: int = 3) -> None:
        self._providers: dict[str, ProviderStatus] = {}
        self._failover_history: list[dict] = []
        self._max_consecutive_failures = max_consecutive_failures

    def register_provider(self, name: str, priority: int = 0) -> None:
        """Register a provider with its priority (lower = higher priority)."""
        self._providers[name] = ProviderStatus(name=name, priority=priority)

    def get_active_provider(self, model: str | None = None) -> str | None:
        """Get the best available provider based on health and priority."""
        healthy_providers: list[ProviderStatus] = []

        for name, status in self._providers.items():
            breaker = breaker_registry.get_or_create(name)
            if breaker.state != CircuitState.OPEN and status.healthy:
                healthy_providers.append(status)

        if not healthy_providers:
            # All providers unhealthy — degrade to highest priority
            all_sorted = sorted(self._providers.values(), key=lambda p: p.priority)
            return all_sorted[0].name if all_sorted else None

        healthy_providers.sort(key=lambda p: p.priority)
        return healthy_providers[0].name

    def record_success(self, provider_name: str) -> None:
        """Record a successful request for a provider."""
        if provider_name in self._providers:
            status = self._providers[provider_name]
            status.healthy = True
            status.last_success = time.time()
            status.consecutive_failures = 0

    def record_failure(self, provider_name: str) -> None:
        """Record a failed request — may trigger failover."""
        if provider_name in self._providers:
            status = self._providers[provider_name]
            status.consecutive_failures += 1
            status.last_failure = time.time()

            if status.consecutive_failures >= self._max_consecutive_failures:
                status.healthy = False
                self._failover_history.append(
                    {
                        "provider": provider_name,
                        "time": time.time(),
                        "reason": f"consecutive_failures>={self._max_consecutive_failures}",
                    }
                )
                logger.warning(
                    "Provider %s marked unhealthy after %d consecutive failures",
                    provider_name,
                    status.consecutive_failures,
                )

    def get_status(self) -> dict:
        """Return overall failover status for monitoring."""
        return {
            "providers": {
                name: {
                    "healthy": s.healthy,
                    "priority": s.priority,
                    "consecutive_failures": s.consecutive_failures,
                    "last_success": s.last_success,
                    "circuit_state": breaker_registry.get_or_create(name).state.value,
                }
                for name, s in self._providers.items()
            },
            "recent_failovers": self._failover_history[-10:],
        }

    def reset(self) -> None:
        """Reset all providers to healthy (useful for testing)."""
        for status in self._providers.values():
            status.healthy = True
            status.consecutive_failures = 0
        self._failover_history.clear()


# Global singleton
failover_manager = FailoverManager()
