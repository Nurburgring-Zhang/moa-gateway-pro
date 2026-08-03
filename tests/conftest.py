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


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """Isolate every test from real config / data dir."""
    monkeypatch.setattr("moa_gateway.config.DATA_DIR", tmp_path)
    monkeypatch.setattr("moa_gateway.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("moa_gateway.config._settings", None)

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

    yield

    # Restore singletons
    if "moa_gateway.storage" in sys.modules:
        from moa_gateway.storage import Storage
        Storage._instance = orig_instance
    if "moa_gateway.ratelimit" in sys.modules:
        import moa_gateway.ratelimit as _rl
        _rl._limiter = orig_limiter


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
