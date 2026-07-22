"""Circuit Breaker — prevent cascading failures across providers."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum


class CircuitState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal — requests flow through
    OPEN = "open"  # Tripped — requests rejected
    HALF_OPEN = "half_open"  # Probing — limited requests allowed


@dataclass
class CircuitBreakerConfig:
    """Configuration for a circuit breaker instance."""

    failure_threshold: int = 5  # Consecutive failures before tripping
    recovery_timeout: float = 30.0  # Seconds to wait before probing
    half_open_max_calls: int = 3  # Max probe requests in half-open
    success_threshold: int = 2  # Consecutive successes to close


class CircuitBreaker:
    """Provider-level circuit breaker with thread-safe state transitions."""

    def __init__(self, name: str, config: CircuitBreakerConfig | None = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._half_open_calls = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        """Current state with automatic OPEN -> HALF_OPEN transition."""
        with self._lock:
            if (
                self._state == CircuitState.OPEN
                and self._last_failure_time
                and time.time() - self._last_failure_time >= self.config.recovery_timeout
            ):
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                self._success_count = 0
            return self._state

    def allow_request(self) -> bool:
        """Determine whether a request should be allowed through."""
        state = self.state
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.OPEN:
            return False
        # HALF_OPEN
        with self._lock:
            if self._half_open_calls < self.config.half_open_max_calls:
                self._half_open_calls += 1
                return True
            return False

    def record_success(self) -> None:
        """Record a successful request."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
            else:
                self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed request."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN or self._failure_count >= self.config.failure_threshold:
                self._state = CircuitState.OPEN

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._half_open_calls = 0
            self._last_failure_time = None

    def get_status(self) -> dict:
        """Return a status dict for monitoring."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "last_failure": self._last_failure_time,
        }


class CircuitBreakerRegistry:
    """Registry managing circuit breakers for multiple providers."""

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get_or_create(
        self, name: str, config: CircuitBreakerConfig | None = None
    ) -> CircuitBreaker:
        """Get an existing breaker or create a new one."""
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name, config)
            return self._breakers[name]

    def get_all_status(self) -> list[dict]:
        """Return status of all registered breakers."""
        return [b.get_status() for b in self._breakers.values()]

    def reset_all(self) -> None:
        """Reset all breakers (useful for testing)."""
        for b in self._breakers.values():
            b.reset()


# Global singleton
breaker_registry = CircuitBreakerRegistry()
