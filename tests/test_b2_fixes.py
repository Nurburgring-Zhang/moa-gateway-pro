"""Wave B2 verification tests — Mock explicitness (D6) + purge self-destruct removal (D3)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

# ========== T2.1 config schema ==========


def test_mock_config_defaults_and_overrides():
    from moa_gateway.config import MockConfig, Settings

    m = MockConfig()
    assert m.mode == "explicit"
    assert m.header_name == "X-MOA-Mock"

    s = Settings(mock={"mode": "disabled", "header_name": "X-Custom-Mock"})
    assert s.mock.mode == "disabled"
    assert s.mock.header_name == "X-Custom-Mock"

    with pytest.raises(Exception):
        MockConfig(mode="silent")  # only explicit|disabled allowed


def test_health_config_new_keys():
    from moa_gateway.config import Settings

    s = Settings()
    assert s.health.purge_initial_delay_seconds == 86400
    assert s.health.skip_mock_endpoints is True

    s2 = Settings(health={"purge_initial_delay_seconds": 60, "skip_mock_endpoints": False})
    assert s2.health.purge_initial_delay_seconds == 60
    assert s2.health.skip_mock_endpoints is False


def test_config_yaml_has_mock_and_purge_keys():
    cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert data["mock"]["mode"] in ("explicit", "disabled")
    assert data["mock"]["header_name"] == "X-MOA-Mock"
    assert data["health"]["purge_initial_delay_seconds"] == 86400
    assert data["health"]["skip_mock_endpoints"] is True


# ========== T2.2 provider-level explicit mock ==========


def test_build_provider_disabled_mode_raises_503(monkeypatch):
    from moa_gateway import config as _cfg
    from moa_gateway.config import Settings
    from moa_gateway.providers import build_provider
    from moa_gateway.providers.base import ProviderError

    monkeypatch.setattr(
        _cfg, "_settings", Settings(mock={"mode": "disabled"})
    )
    with pytest.raises(ProviderError) as exc_info:
        build_provider("openrouter", api_key="", model="some/model", api_base="https://x")
    assert exc_info.value.status == 503
    assert "mock mode is disabled" in str(exc_info.value)


def test_build_provider_explicit_mode_returns_mock(monkeypatch):
    from moa_gateway import config as _cfg
    from moa_gateway.config import Settings
    from moa_gateway.providers import MockProvider, build_provider

    monkeypatch.setattr(_cfg, "_settings", Settings(mock={"mode": "explicit"}))
    p = build_provider("openrouter", api_key="", model="some/model")
    assert isinstance(p, MockProvider)


def test_build_provider_no_auth_provider_not_mock(monkeypatch):
    """no-auth platforms (ovhcloud/llm7) must never fall back to mock."""
    from moa_gateway import config as _cfg
    from moa_gateway.config import Settings
    from moa_gateway.providers import MockProvider, build_provider

    monkeypatch.setattr(_cfg, "_settings", Settings(mock={"mode": "explicit"}))
    p = build_provider("ovhcloud", api_key="", api_base="https://x")
    assert not isinstance(p, MockProvider)


# ========== T2.3 response labeling ==========


def test_mock_headers_helper(monkeypatch):
    from moa_gateway import config as _cfg
    from moa_gateway._helpers import mock_headers
    from moa_gateway.config import Settings

    assert mock_headers(False) == {}
    monkeypatch.setattr(_cfg, "_settings", Settings())
    assert mock_headers(True) == {"X-MOA-Mock": "true"}
    monkeypatch.setattr(
        _cfg, "_settings", Settings(mock={"header_name": "X-Custom"})
    )
    assert mock_headers(True) == {"X-Custom": "true"}


# ========== T2.4 /health visibility ==========


def test_health_endpoint_reports_mock_counts(monkeypatch):
    import moa_gateway.routes.health as health_mod

    class _FakePool:
        def snapshot(self):
            return {
                "total": 21,
                "enabled": 21,
                "healthy": 21,
                "mock_backed": 19,
                "real_backed": 2,
            }

    monkeypatch.setattr(health_mod, "get_model_pool", lambda: _FakePool())
    resp = asyncio.run(health_mod.health())
    assert resp["endpoints_total"] == 21
    assert resp["mock_endpoints_count"] == 19
    assert resp["real_endpoints_count"] == 2
    assert resp["mock_mode"] in ("explicit", "disabled")


# ========== T2.5 purge self-destruct removal ==========


class _FakePurgeManager:
    def __init__(self):
        self.calls = 0

    async def check_and_purge(self):
        self.calls += 1
        return []


def test_daily_purge_loop_respects_initial_delay():
    from moa_gateway.server import _daily_purge_loop

    async def run(delay: float, wait: float) -> int:
        mgr = _FakePurgeManager()
        task = asyncio.create_task(_daily_purge_loop(mgr, delay))
        await asyncio.sleep(wait)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return mgr.calls

    # large initial delay -> no purge within 0.3s (the old code purged instantly)
    assert asyncio.run(run(1000.0, 0.3)) == 0
    # zero delay -> purge fires immediately
    assert asyncio.run(run(0.0, 0.3)) >= 1


class _FakeEndpoint:
    def __init__(self, eid: str, provider: str = "openrouter", key: str = ""):
        class _Cfg:
            pass

        self.config = _Cfg()
        self.config.id = eid
        self.config.provider = provider
        self.config.model = f"m/{eid}"
        self.config.api_base = "https://example.invalid"
        self.config.api_key_runtime = key
        self.config.api_key_env = ""
        self.config.tier = "free"
        self.config.enabled = True


class _FakePool:
    def __init__(self, eps: dict[str, _FakeEndpoint], mock_ids: set[str]):
        self.endpoints = eps
        self._mock_ids = mock_ids
        self.removed: list[str] = []
        self.upserted: list[dict] = []

    def _ep_is_mock(self, e) -> bool:
        return e.config.id in self._mock_ids

    def remove_endpoint(self, eid: str) -> bool:
        self.removed.append(eid)
        self.endpoints.pop(eid, None)
        return True

    def upsert_endpoint(self, cfg: dict):
        self.upserted.append(cfg)
        eid = cfg["endpoint_id"]
        self.endpoints[eid] = _FakeEndpoint(eid)
        return self.endpoints[eid]


class _FakeStorage:
    def __init__(self):
        self.records: list[dict] = []

    def save_purge_record(self, record: dict) -> None:
        self.records.append(record)

    def list_purge_records(self, limit: int = 100) -> list[dict]:
        return list(reversed(self.records))[:limit]


def _aged_health(eid: str, days: int = 10):
    from moa_gateway.health.health_checker import EndpointHealth

    h = EndpointHealth(endpoint_id=eid)
    h.unavailable_since = datetime.now() - timedelta(days=days)
    h.consecutive_failures = 100
    h.last_error = "boom"
    return h


def test_probe_engine_skips_mock_endpoints(monkeypatch):
    from moa_gateway.health.health_checker import HealthChecker
    from moa_gateway.health.probe_engine import ProbeEngine

    ep = _FakeEndpoint("m1")
    pool = _FakePool({"m1": ep}, mock_ids={"m1"})
    engine = ProbeEngine(HealthChecker(), model_pool=pool)

    def _no_network(*a, **k):
        raise AssertionError("network must not be touched for mock endpoints")

    monkeypatch.setattr("moa_gateway.health.probe_engine.httpx.AsyncClient", _no_network)
    assert asyncio.run(engine.probe_endpoint("m1")) is True


def test_purge_manager_skips_mock_endpoints():
    from moa_gateway.health.health_checker import HealthChecker
    from moa_gateway.health.purge_manager import PurgeManager

    ep = _FakeEndpoint("m1")
    pool = _FakePool({"m1": ep}, mock_ids={"m1"})
    checker = HealthChecker()
    checker._health["m1"] = _aged_health("m1")
    mgr = PurgeManager(checker, model_pool=pool, storage=_FakeStorage())

    purged = asyncio.run(mgr.check_and_purge())
    assert purged == []
    assert pool.removed == []
    assert "m1" in pool.endpoints


def test_purge_manager_still_purges_real_dead_endpoints():
    from moa_gateway.health.health_checker import HealthChecker
    from moa_gateway.health.purge_manager import PurgeManager

    ep = _FakeEndpoint("r1", key="real-key-123")
    pool = _FakePool({"r1": ep}, mock_ids=set())
    checker = HealthChecker()
    checker._health["r1"] = _aged_health("r1")
    storage = _FakeStorage()
    mgr = PurgeManager(checker, model_pool=pool, storage=storage)

    purged = asyncio.run(mgr.check_and_purge())
    assert purged == ["r1"]
    assert pool.removed == ["r1"]
    # purge record carries a config snapshot for later restore —
    # but NEVER the upstream API key (B2 review H1)
    assert len(storage.records) == 1
    snap = storage.records[0]["endpoint_config"]
    assert snap is not None and snap["endpoint_id"] == "r1"
    assert "api_key" not in snap
    assert "api_key_runtime" not in snap


def test_snapshot_never_contains_keys():
    """Snapshot redaction must also work for non-pydantic configs (vars path)."""
    from moa_gateway.health.health_checker import HealthChecker
    from moa_gateway.health.purge_manager import PurgeManager

    ep = _FakeEndpoint("k1", key="super-secret-key")
    ep.config.api_key = "super-secret-key"  # type: ignore[attr-defined]
    pool = _FakePool({"k1": ep}, mock_ids=set())
    mgr = PurgeManager(HealthChecker(), model_pool=pool, storage=None)
    snap = mgr._endpoint_config_snapshot("k1")
    assert snap is not None
    assert "api_key" not in snap
    assert "api_key_runtime" not in snap
    assert not any("secret" in str(v) for v in snap.values())


def test_restore_dynamic_endpoint_requires_flag():
    """Snapshot-based restore of dynamic endpoints is opt-in (B2 review M2)."""
    from moa_gateway import config as _cfg
    from moa_gateway.config import Settings
    from moa_gateway.health.health_checker import HealthChecker
    from moa_gateway.health.purge_manager import PurgeManager

    def _make():
        checker = HealthChecker()
        pool = _FakePool({}, mock_ids=set())
        storage = _FakeStorage()
        storage.records.append(
            {
                "endpoint_id": "lost-1",
                "endpoint_config": {
                    "endpoint_id": "lost-1",
                    "provider": "openrouter",
                    "model": "m/lost",
                    "tier": "free",
                },
            }
        )
        return PurgeManager(checker, model_pool=pool, storage=storage), pool

    # default: auto_restore_purged disabled -> no resurrection
    mgr, pool = _make()
    monkey_settings = Settings()  # auto_restore_purged defaults to False
    orig = _cfg._settings
    _cfg._settings = monkey_settings
    try:
        assert asyncio.run(mgr.restore_purged_endpoints()) == []
        assert "lost-1" not in pool.endpoints
    finally:
        _cfg._settings = orig

    # flag enabled -> restored from snapshot, then idempotent
    mgr, pool = _make()
    _cfg._settings = Settings(health={"auto_restore_purged": True})
    try:
        assert asyncio.run(mgr.restore_purged_endpoints()) == ["lost-1"]
        assert "lost-1" in pool.endpoints
        assert asyncio.run(mgr.restore_purged_endpoints()) == []
    finally:
        _cfg._settings = orig


def test_restore_static_endpoint_from_config(monkeypatch):
    """Endpoints still defined in config.yaml are restored without the flag."""
    from moa_gateway import config as _cfg
    from moa_gateway.config import ModelEndpointConfig, Settings
    from moa_gateway.health.health_checker import HealthChecker
    from moa_gateway.health.purge_manager import PurgeManager

    static = ModelEndpointConfig(
        id="static-1", provider="qwen", model="qwen-turbo", tier="free"
    )
    monkeypatch.setattr(_cfg, "_settings", Settings(models=[static]))

    checker = HealthChecker()
    pool = _FakePool({}, mock_ids=set())
    storage = _FakeStorage()
    # legacy record WITHOUT config snapshot (e.g. the 14 wrongly purged ones)
    storage.records.append({"endpoint_id": "static-1"})
    mgr = PurgeManager(checker, model_pool=pool, storage=storage)

    restored = asyncio.run(mgr.restore_purged_endpoints())
    assert restored == ["static-1"]
    assert "static-1" in pool.endpoints
    # restored config comes from static settings, key re-resolved via env
    cfg = pool.upserted[0]
    assert cfg["provider"] == "qwen"
    assert "api_key" not in cfg and "api_key_runtime" not in cfg


def test_storage_purge_record_config_roundtrip(storage_instance):
    storage_instance.save_purge_record(
        {
            "endpoint_id": "ep-x",
            "purged_at": "2024-01-01T00:00:00",
            "days_unavailable": 8,
            "last_error": "timeout",
            "error_type_counts": {"timeout": 3},
            "success_rate_at_purge": 0.1,
            "status_at_purge": "dead",
            "endpoint_config": {"endpoint_id": "ep-x", "provider": "groq", "model": "llama"},
        }
    )
    rows = storage_instance.list_purge_records()
    assert len(rows) == 1
    assert rows[0]["endpoint_id"] == "ep-x"
    assert rows[0]["endpoint_config"] == {
        "endpoint_id": "ep-x",
        "provider": "groq",
        "model": "llama",
    }
