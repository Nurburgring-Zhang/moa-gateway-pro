"""High Availability module — circuit breaker, retry, health, graceful shutdown, failover."""

from .circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitState,
    breaker_registry,
)
from .failover import FailoverManager, failover_manager
from .graceful import GracefulShutdown, graceful
from .health import HealthChecker, HealthStatus, health_checker
from .retry import RetryConfig, calculate_delay, retry_async

__all__ = [
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitBreakerRegistry",
    "CircuitState",
    "FailoverManager",
    "GracefulShutdown",
    "HealthChecker",
    "HealthStatus",
    "RetryConfig",
    "breaker_registry",
    "calculate_delay",
    "failover_manager",
    "graceful",
    "health_checker",
    "retry_async",
]
