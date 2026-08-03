"""Tests for the High Availability module — circuit breaker, retry, health, graceful, failover."""
from __future__ import annotations

import asyncio
import time

import pytest

from moa_gateway.ha import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitState,
    FailoverManager,
    GracefulShutdown,
    HealthChecker,
    RetryConfig,
    calculate_delay,
    retry_async,
)

# ============================= Circuit Breaker Tests =============================


class TestCircuitBreaker:
    """Circuit breaker state machine tests."""

    def test_initial_state_is_closed(self):
        cb = CircuitBreaker("test-provider")
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_transitions_to_open_after_threshold(self):
        config = CircuitBreakerConfig(failure_threshold=3)
        cb = CircuitBreaker("test-provider", config)

        # Record failures up to threshold
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED  # not yet

        cb.record_failure()  # 3rd failure
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_success_resets_failure_count(self):
        config = CircuitBreakerConfig(failure_threshold=3)
        cb = CircuitBreaker("test-provider", config)

        cb.record_failure()
        cb.record_failure()
        cb.record_success()  # resets counter

        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED  # still closed (only 2 consecutive)

    def test_transitions_to_half_open_after_timeout(self):
        config = CircuitBreakerConfig(failure_threshold=2, recovery_timeout=0.1)
        cb = CircuitBreaker("test-provider", config)

        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.allow_request() is True

    def test_half_open_to_closed_on_success(self):
        config = CircuitBreakerConfig(
            failure_threshold=2, recovery_timeout=0.05, success_threshold=2
        )
        cb = CircuitBreaker("test-provider", config)

        # Trip the breaker
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.06)
        assert cb.state == CircuitState.HALF_OPEN

        # Successful probes
        cb.record_success()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_to_open_on_failure(self):
        config = CircuitBreakerConfig(failure_threshold=2, recovery_timeout=0.05)
        cb = CircuitBreaker("test-provider", config)

        cb.record_failure()
        cb.record_failure()
        time.sleep(0.06)
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_half_open_limits_requests(self):
        config = CircuitBreakerConfig(
            failure_threshold=2, recovery_timeout=0.05, half_open_max_calls=2
        )
        cb = CircuitBreaker("test-provider", config)

        cb.record_failure()
        cb.record_failure()
        time.sleep(0.06)

        assert cb.allow_request() is True  # 1st
        assert cb.allow_request() is True  # 2nd
        assert cb.allow_request() is False  # 3rd rejected

    def test_reset(self):
        config = CircuitBreakerConfig(failure_threshold=2)
        cb = CircuitBreaker("test-provider", config)

        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_get_status(self):
        cb = CircuitBreaker("provider-x")
        cb.record_failure()
        status = cb.get_status()
        assert status["name"] == "provider-x"
        assert status["state"] == "closed"
        assert status["failure_count"] == 1


class TestCircuitBreakerRegistry:
    """Registry for managing multiple circuit breakers."""

    def test_get_or_create(self):
        registry = CircuitBreakerRegistry()
        cb1 = registry.get_or_create("provider-a")
        cb2 = registry.get_or_create("provider-a")
        assert cb1 is cb2  # same instance

        cb3 = registry.get_or_create("provider-b")
        assert cb3 is not cb1

    def test_get_all_status(self):
        registry = CircuitBreakerRegistry()
        registry.get_or_create("p1")
        registry.get_or_create("p2")
        statuses = registry.get_all_status()
        assert len(statuses) == 2
        names = {s["name"] for s in statuses}
        assert names == {"p1", "p2"}


# ============================= Retry Tests =============================


class TestRetry:
    """Retry mechanism with exponential backoff."""

    def test_calculate_delay_exponential(self):
        config = RetryConfig(base_delay=1.0, exponential_base=2.0, jitter=False)
        assert calculate_delay(0, config) == 1.0
        assert calculate_delay(1, config) == 2.0
        assert calculate_delay(2, config) == 4.0

    def test_calculate_delay_respects_max(self):
        config = RetryConfig(base_delay=1.0, max_delay=5.0, exponential_base=2.0, jitter=False)
        assert calculate_delay(10, config) == 5.0  # capped at max

    def test_calculate_delay_with_jitter(self):
        config = RetryConfig(base_delay=1.0, exponential_base=2.0, jitter=True)
        delays = [calculate_delay(1, config) for _ in range(100)]
        # With jitter, delays should vary but be within [1.0, 3.0] range (2.0 * [0.5, 1.5])
        assert all(0.9 <= d <= 3.1 for d in delays)
        # Not all the same
        assert len({round(d, 4) for d in delays}) > 1

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_first_attempt(self):
        call_count = 0

        @retry_async(RetryConfig(max_attempts=3))
        async def success_fn():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await success_fn()
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_succeeds_after_failures(self):
        call_count = 0

        @retry_async(RetryConfig(max_attempts=3, base_delay=0.01))
        async def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("temporary failure")
            return "recovered"

        result = await flaky_fn()
        assert result == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_exhausts_attempts(self):
        @retry_async(RetryConfig(max_attempts=2, base_delay=0.01))
        async def always_fail():
            raise ValueError("permanent failure")

        with pytest.raises(ValueError, match="permanent failure"):
            await always_fail()


# ============================= Health Check Tests =============================


class TestHealthChecker:
    """Deep health check probes."""

    @pytest.mark.asyncio
    async def test_liveness_always_returns_alive(self):
        hc = HealthChecker()
        result = await hc.liveness()
        assert result["status"] == "alive"
        assert "uptime_seconds" in result
        assert "pid" in result

    @pytest.mark.asyncio
    async def test_readiness_not_ready_before_startup(self):
        hc = HealthChecker()
        result = await hc.readiness()
        assert result["status"] == "not_ready"

    @pytest.mark.asyncio
    async def test_readiness_healthy_after_mark_ready(self):
        hc = HealthChecker()
        hc.mark_ready()
        result = await hc.readiness()
        assert result["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_readiness_unhealthy_when_check_fails(self):
        hc = HealthChecker()
        hc.mark_ready()

        def failing_check():
            raise RuntimeError("DB connection failed")

        hc.register_check("database", failing_check)
        result = await hc.readiness()
        assert result["status"] == "unhealthy"
        assert "database" in result["components"]
        assert result["components"]["database"]["status"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_readiness_degraded_status(self):
        hc = HealthChecker()
        hc.mark_ready()

        hc.register_check("redis", lambda: True)
        hc.register_check("secondary", lambda: False)  # returns falsy = unhealthy

        result = await hc.readiness()
        # One component unhealthy -> overall unhealthy
        assert result["status"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_startup_probe(self):
        hc = HealthChecker()
        result = await hc.startup()
        assert result["status"] == "starting"
        assert result["startup_complete"] is False

        hc.mark_ready()
        result = await hc.startup()
        assert result["status"] == "started"
        assert result["startup_complete"] is True

    @pytest.mark.asyncio
    async def test_async_check_function(self):
        hc = HealthChecker()
        hc.mark_ready()

        async def async_check():
            await asyncio.sleep(0.01)
            return True

        hc.register_check("async_component", async_check)
        result = await hc.readiness()
        assert result["status"] == "healthy"


# ============================= Graceful Shutdown Tests =============================


class TestGracefulShutdown:
    """Graceful shutdown with request draining."""

    def test_initial_state(self):
        gs = GracefulShutdown(timeout=10)
        assert gs.is_shutting_down is False
        assert gs.active_requests == 0

    def test_request_tracking(self):
        gs = GracefulShutdown()
        gs.increment_requests()
        gs.increment_requests()
        assert gs.active_requests == 2
        gs.decrement_requests()
        assert gs.active_requests == 1

    @pytest.mark.asyncio
    async def test_shutdown_waits_for_requests(self):
        gs = GracefulShutdown(timeout=5)
        gs.increment_requests()

        async def simulate_request_complete():
            await asyncio.sleep(0.1)
            gs.decrement_requests()

        asyncio.create_task(simulate_request_complete())
        await gs.shutdown()

        assert gs.is_shutting_down is True
        assert gs.active_requests == 0

    @pytest.mark.asyncio
    async def test_shutdown_timeout(self):
        gs = GracefulShutdown(timeout=0.1)
        gs.increment_requests()  # never decremented

        start = time.time()
        await gs.shutdown()
        elapsed = time.time() - start

        assert gs.is_shutting_down is True
        assert elapsed < 0.5  # timeout triggers quickly
        assert gs.active_requests == 1  # still active (force exit)

    def test_get_status(self):
        gs = GracefulShutdown(timeout=30)
        gs.increment_requests()
        status = gs.get_status()
        assert status["shutting_down"] is False
        assert status["active_requests"] == 1
        assert status["timeout_seconds"] == 30


# ============================= Failover Tests =============================


class TestFailoverManager:
    """Provider failover with priority-based selection."""

    def test_register_and_select_provider(self):
        fm = FailoverManager()
        fm.register_provider("openai", priority=0)
        fm.register_provider("anthropic", priority=1)
        fm.register_provider("local", priority=2)

        # Should select highest priority (lowest number)
        assert fm.get_active_provider() == "openai"

    def test_failover_on_consecutive_failures(self):
        fm = FailoverManager(max_consecutive_failures=2)
        fm.register_provider("openai", priority=0)
        fm.register_provider("anthropic", priority=1)

        # Simulate failures
        fm.record_failure("openai")
        fm.record_failure("openai")  # 2nd failure triggers unhealthy

        # Should failover to anthropic
        assert fm.get_active_provider() == "anthropic"

    def test_recovery_after_success(self):
        fm = FailoverManager(max_consecutive_failures=2)
        fm.register_provider("openai", priority=0)
        fm.register_provider("anthropic", priority=1)

        fm.record_failure("openai")
        fm.record_success("openai")  # resets counter

        fm.record_failure("openai")
        # Still healthy — counter was reset
        assert fm.get_active_provider() == "openai"

    def test_all_providers_unhealthy_returns_highest_priority(self):
        fm = FailoverManager(max_consecutive_failures=1)
        fm.register_provider("openai", priority=0)
        fm.register_provider("anthropic", priority=1)

        fm.record_failure("openai")
        fm.record_failure("anthropic")

        # Both unhealthy — fall back to highest priority (degraded mode)
        assert fm.get_active_provider() == "openai"

    def test_get_status(self):
        fm = FailoverManager()
        fm.register_provider("openai", priority=0)
        fm.record_failure("openai")

        status = fm.get_status()
        assert "openai" in status["providers"]
        assert status["providers"]["openai"]["consecutive_failures"] == 1

    def test_reset(self):
        fm = FailoverManager(max_consecutive_failures=1)
        fm.register_provider("openai", priority=0)
        fm.record_failure("openai")
        assert fm.get_active_provider() is None or fm.get_active_provider() == "openai"

        fm.reset()
        status = fm.get_status()
        assert status["providers"]["openai"]["healthy"] is True
        assert status["providers"]["openai"]["consecutive_failures"] == 0
