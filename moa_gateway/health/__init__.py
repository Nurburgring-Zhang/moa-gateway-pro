"""moa_gateway.health — API health management system.

Provides endpoint health tracking, probe engine, and automatic purge
of unavailable endpoints.
"""
from __future__ import annotations

from typing import Any

from .health_checker import EndpointHealth, HealthChecker, HealthStatus
from .probe_engine import ProbeEngine
from .purge_manager import PurgeManager

__all__ = [
    "HealthChecker",
    "EndpointHealth",
    "HealthStatus",
    "ProbeEngine",
    "PurgeManager",
    "get_health_checker",
    "get_probe_engine",
    "get_purge_manager",
    "init_health_system",
    "shutdown_health_system",
]

# ========== Singleton Management ==========
_health_checker: HealthChecker | None = None
_probe_engine: ProbeEngine | None = None
_purge_manager: PurgeManager | None = None


def get_health_checker() -> HealthChecker:
    """Get the singleton HealthChecker instance."""
    global _health_checker
    if _health_checker is None:
        _health_checker = HealthChecker()
    return _health_checker


def get_probe_engine() -> ProbeEngine:
    """Get the singleton ProbeEngine instance."""
    global _probe_engine
    if _probe_engine is None:
        _probe_engine = ProbeEngine(get_health_checker())
    return _probe_engine


def get_purge_manager() -> PurgeManager:
    """Get the singleton PurgeManager instance."""
    global _purge_manager
    if _purge_manager is None:
        _purge_manager = PurgeManager(get_health_checker())
    return _purge_manager


def init_health_system(
    model_pool: Any | None = None,
    storage: Any | None = None,
    settings: Any | None = None,
) -> tuple[HealthChecker, ProbeEngine, PurgeManager]:
    """Initialize the health management system. Called during server startup."""
    global _health_checker, _probe_engine, _purge_manager

    from ..config import get_settings
    settings = settings or get_settings()

    health_cfg = settings.health
    _health_checker = HealthChecker(storage=storage)
    # P1-2: Wire HealthConfig probe_interval_* settings into ProbeEngine
    probe_intervals = {
        "new": health_cfg.probe_interval_new,
        "healthy": health_cfg.probe_interval_healthy,
        "degraded": health_cfg.probe_interval_degraded,
        "unhealthy": health_cfg.probe_interval_unhealthy,
        "dead": 3600,
    }
    _probe_engine = ProbeEngine(
        _health_checker,
        model_pool=model_pool,
        probe_timeout=health_cfg.probe_timeout,
        probe_intervals=probe_intervals,
    )
    _purge_manager = PurgeManager(
        _health_checker,
        model_pool=model_pool,
        storage=storage,
        purge_threshold_days=health_cfg.purge_threshold_days,
    )

    # Load persisted health state
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_health_checker.load_state())
        else:
            loop.run_until_complete(_health_checker.load_state())
    except Exception:
        pass  # load_state is best-effort

    return _health_checker, _probe_engine, _purge_manager


async def shutdown_health_system() -> None:
    """Shutdown the health management system. Called during server shutdown."""
    global _health_checker, _probe_engine, _purge_manager

    if _probe_engine is not None:
        await _probe_engine.stop_all()
    if _health_checker is not None:
        await _health_checker.save_state()

    _health_checker = None
    _probe_engine = None
    _purge_manager = None
