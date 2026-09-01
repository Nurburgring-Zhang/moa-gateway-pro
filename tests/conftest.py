"""Shared fixtures for Blue-Team regression tests."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import modules that bind ``config.get_settings`` at import time BEFORE any
# test fixture patches that attribute. If e.g. model_pool were first imported
# while a fixture had swapped ``get_settings`` for a test double, it would keep
# the test double bound forever and leak empty settings across tests.
import moa_gateway.model_pool  # noqa: E402,F401
import moa_gateway.router  # noqa: E402,F401
import moa_gateway.storage  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """Isolate every test from real config / data dir."""
    monkeypatch.setattr("moa_gateway.config.DATA_DIR", tmp_path)
    monkeypatch.setattr("moa_gateway.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("moa_gateway.config._settings", None)

    # storage.py binds DATA_DIR/ROOT_DIR at import time; redirect them (and the
    # fernet key path) so tests never touch the real data/ directory or DB.
    # (moa_gateway.storage is pre-imported at conftest module level.)
    monkeypatch.setattr("moa_gateway.storage.DATA_DIR", tmp_path)
    monkeypatch.setattr("moa_gateway.storage.ROOT_DIR", tmp_path)
    monkeypatch.setattr("moa_gateway.storage._FERNET_PATH", tmp_path / ".fernet_key")

    # Reset Storage singleton to prevent cross-test state leakage
    if "moa_gateway.storage" in sys.modules:
        from moa_gateway.storage import Storage
        orig_instance = Storage._instance
        Storage._instance = None
    else:
        orig_instance = None

    # Reset RateLimiter singleton to prevent cached settings leakage
    if "moa_gateway.ratelimit" in sys.modules:
        import moa_gateway.ratelimit as _rl
        orig_limiter = _rl._limiter
        _rl._limiter = None
    else:
        orig_limiter = None

    # Reset ModelPool / Router module singletons: they cache settings and
    # endpoints from whichever test created them first.
    orig_pool = None
    if "moa_gateway.model_pool" in sys.modules:
        import moa_gateway.model_pool as _mp
        orig_pool = _mp._pool
        _mp._pool = None
    orig_router = None
    if "moa_gateway.router" in sys.modules:
        import moa_gateway.router as _rt
        orig_router = _rt._router
        _rt._router = None

    # Response-cache manager is a process-wide singleton; cached entries from
    # earlier test files would be hit by later tests (e.g. chaos tests that
    # patch ModelPool.call still got 200 from a cache hit). Reset per test.
    from moa_gateway.cache.manager import reset_cache_manager as _reset_cache
    _reset_cache()

    # Every ModelPool subscribes itself to settings-change callbacks; clear
    # the list per test so stale pools neither leak nor get notified later.
    import moa_gateway.config as _cfg_mod
    orig_subscribers = list(_cfg_mod._settings_subscribers)
    _cfg_mod._settings_subscribers.clear()

    # Graceful-shutdown singleton: a test that drains a shutdown must not
    # leave the flag set — the observability middleware would 503 every
    # later test's requests ("Server is shutting down").
    from moa_gateway.ha.graceful import graceful as _graceful
    _graceful.reset()

    # Capability toggles cache: force re-read from the (isolated) storage.
    import moa_gateway.capability_toggles as _toggles
    orig_toggle_cache = _toggles._cache
    _toggles._cache = None

    yield

    # Restore toggle cache
    _toggles._cache = orig_toggle_cache

    # Restore singletons
    if "moa_gateway.storage" in sys.modules:
        from moa_gateway.storage import Storage
        Storage._instance = orig_instance
    if "moa_gateway.ratelimit" in sys.modules:
        import moa_gateway.ratelimit as _rl
        _rl._limiter = orig_limiter
    if "moa_gateway.model_pool" in sys.modules:
        import moa_gateway.model_pool as _mp
        _mp._pool = orig_pool
    if "moa_gateway.router" in sys.modules:
        import moa_gateway.router as _rt
        _rt._router = orig_router
    _cfg_mod._settings_subscribers.clear()
    _cfg_mod._settings_subscribers.extend(orig_subscribers)


@pytest.fixture
def make_settings():
    """Helper to build a Settings object with custom auth fields."""
    from moa_gateway.config import Settings

    def _factory(**auth_overrides):
        auth_defaults = {
            "admin_username": "admin",
            "admin_password": "SuperStr0ng!Pass#2024",
            "jwt_secret": "test-secret-long-enough-for-hs256-signing-key-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [],
        }
        auth_defaults.update(auth_overrides)
        return Settings(auth=auth_defaults)

    return _factory


@pytest.fixture
def storage_instance(tmp_path, make_settings):
    """Create a real Storage instance backed by an isolated temp DB."""
    from moa_gateway.storage import Storage

    settings = make_settings()
    with patch("moa_gateway.storage.get_settings", return_value=settings):
        with patch("moa_gateway.storage._FERNET_PATH", tmp_path / ".fernet_key"):
            with patch("moa_gateway.storage.DATA_DIR", tmp_path):
                Storage._instance = None
                s = Storage(db_path=tmp_path / "test.db")
                yield s
                Storage._instance = None
