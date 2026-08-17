"""Wave B3 regression tests: internal callback auth (D2/D12), discovery key
injection (D5) and real metrics wiring (D4)."""
from __future__ import annotations

import asyncio

import pytest

# ---------------------------------------------------------------------------
# T3.1 internal callback auth
# ---------------------------------------------------------------------------


def test_internal_auth_headers_from_env(monkeypatch):
    from moa_gateway.internal_callback import internal_auth_headers

    monkeypatch.setenv("MOA_GATEWAY_KEY", "env-key-1")
    assert internal_auth_headers() == {"Authorization": "Bearer env-key-1"}


def test_internal_auth_headers_falls_back_to_settings(monkeypatch):
    from moa_gateway import config as _cfg
    from moa_gateway.config import AuthConfig, Settings
    from moa_gateway.internal_callback import internal_auth_headers

    monkeypatch.delenv("MOA_GATEWAY_KEY", raising=False)
    monkeypatch.setattr(
        _cfg, "_settings", Settings(auth=AuthConfig(gateway_api_keys=["cfg-key-9"]))
    )
    assert internal_auth_headers() == {"Authorization": "Bearer cfg-key-9"}


def test_internal_auth_headers_empty_when_no_key(monkeypatch):
    from moa_gateway import config as _cfg
    from moa_gateway.config import AuthConfig, Settings
    from moa_gateway.internal_callback import internal_auth_headers

    monkeypatch.delenv("MOA_GATEWAY_KEY", raising=False)
    monkeypatch.setattr(_cfg, "_settings", Settings(auth=AuthConfig(gateway_api_keys=[])))
    assert internal_auth_headers() == {}


def test_internal_gateway_url_env_override(monkeypatch):
    from moa_gateway.internal_callback import internal_gateway_url

    monkeypatch.setenv("MOA_GATEWAY_URL", "http://10.0.0.5:9000/")
    assert internal_gateway_url() == "http://10.0.0.5:9000"


def test_internal_gateway_url_from_settings_port(monkeypatch):
    from moa_gateway import config as _cfg
    from moa_gateway.config import ServerConfig, Settings
    from moa_gateway.internal_callback import internal_gateway_url

    monkeypatch.delenv("MOA_GATEWAY_URL", raising=False)
    monkeypatch.setattr(_cfg, "_settings", Settings(server=ServerConfig(port=7777)))
    assert internal_gateway_url() == "http://127.0.0.1:7777"


class _FakeHttpResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeAsyncClient:
    captured: dict = {}

    def __init__(self, *args, response=None, **kwargs):
        self._response = response or _FakeHttpResponse()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None, **kwargs):
        _FakeAsyncClient.captured = {"url": url, "json": json, "headers": headers or {}}
        return self._response


def test_workflow_http_post_injects_auth_header(monkeypatch):
    import httpx

    from moa_gateway.workflows.yaml_workflow import _http_post

    monkeypatch.setenv("MOA_GATEWAY_KEY", "wf-key-1")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    result = asyncio.run(_http_post("http://x/v1/chat/completions", {"model": "m"}))
    assert result == {"ok": True}
    headers = _FakeAsyncClient.captured["headers"]
    assert headers.get("Authorization") == "Bearer wf-key-1"


def test_workflow_http_post_error_returns_error_dict(monkeypatch):
    import httpx

    from moa_gateway.workflows.yaml_workflow import _http_post

    monkeypatch.setenv("MOA_GATEWAY_KEY", "wf-key-1")

    class _Err(_FakeAsyncClient):
        def __init__(self, *a, **k):
            super().__init__(*a, response=_FakeHttpResponse(401, text="unauthorized"), **k)

    monkeypatch.setattr(httpx, "AsyncClient", _Err)
    result = asyncio.run(_http_post("http://x/v1/moa/execute", {}))
    assert result["error"] == "HTTP 401"


def test_executor_call_llm_injects_auth_header(monkeypatch):
    import httpx

    from moa_gateway.assistant.executor import _call_llm

    monkeypatch.setenv("MOA_GATEWAY_KEY", "run-key-7")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    resp = asyncio.run(
        _call_llm("deepseek-v3", [{"role": "user", "content": "hi"}], [], 0.7)
    )
    assert resp == {"ok": True}
    cap = _FakeAsyncClient.captured
    assert cap["url"].endswith("/v1/chat/completions")
    assert cap["headers"].get("Authorization") == "Bearer run-key-7"
    assert cap["json"]["model"] == "deepseek-v3"


# ---------------------------------------------------------------------------
# T3.2 discovery api_keys injection
# ---------------------------------------------------------------------------


def test_discovery_engine_uses_injected_keys():
    from moa_gateway.discovery import FreeModelDiscoveryEngine

    engine = FreeModelDiscoveryEngine(api_keys={"groq": "g-1", "siliconflow": "s-1"})
    assert engine._api_keys == {"groq": "g-1", "siliconflow": "s-1"}

    engine2 = FreeModelDiscoveryEngine()
    assert engine2._api_keys == {}


# ---------------------------------------------------------------------------
# T3.3 real metrics wiring
# ---------------------------------------------------------------------------


def _counter_value(metric, **labels):
    if hasattr(metric, 'labels'):
        labeled = metric.labels(**labels)
        if hasattr(labeled, '_value'):
            return labeled._value.get()  # noqa: SLF001
    return 0


def test_cache_metrics_mirrored_to_prometheus():
    from moa_gateway.cache.metrics import CacheMetrics
    from moa_gateway.observability.metrics import cache_hits_total, cache_misses_total

    # Skip if Prometheus is not available (uses _Dummy fallback)
    if type(cache_hits_total).__name__ == "_Dummy":
        import pytest
        pytest.skip("Prometheus not available (using _Dummy metrics)")

    m = CacheMetrics()
    hits_before = _counter_value(cache_hits_total, layer="exact")
    sem_before = _counter_value(cache_hits_total, layer="semantic")
    miss_before = _counter_value(cache_misses_total, layer="all")

    m.record_hit("l1_exact")
    m.record_hit("l2_semantic")
    m.record_miss()

    assert _counter_value(cache_hits_total, layer="exact") == hits_before + 1
    assert _counter_value(cache_hits_total, layer="semantic") == sem_before + 1
    assert _counter_value(cache_misses_total, layer="all") == miss_before + 1
    # in-memory stats still consistent
    assert m.total_hits == 2
    assert m.total_requests == 3


class _FakeProvider:
    def __init__(self, resp=None, err=None):
        self._resp = resp
        self._err = err

    async def chat(self, req):
        if self._err is not None:
            raise self._err
        return self._resp


class _DummyStorage:
    def list_endpoints(self):
        return []


def _make_pool(monkeypatch, endpoint_cfg):
    from moa_gateway import config as _cfg
    from moa_gateway.config import Settings
    from moa_gateway.model_pool import ModelPool

    settings = Settings(models=[endpoint_cfg])
    monkeypatch.setattr(_cfg, "_settings", settings)
    pool = ModelPool(settings=settings, storage=_DummyStorage())
    return pool


def test_pool_call_records_success_metrics(monkeypatch):
    from moa_gateway.config import ModelEndpointConfig
    from moa_gateway.observability.metrics import (
        llm_requests_total,
        llm_tokens_total,
    )
    from moa_gateway.providers.base import ChatResponse

    cfg = ModelEndpointConfig(
        id="mx-1", provider="qwen", model="mx-model", tier="free", enabled=True
    )
    pool = _make_pool(monkeypatch, cfg)
    ep = pool.endpoints["mx-1"]
    ep.provider_obj = _FakeProvider(  # type: ignore[assignment]
        resp=ChatResponse(content="ok", prompt_tokens=5, completion_tokens=7)
    )

    req_before = _counter_value(
        llm_requests_total, model="mx-model", provider="qwen", status="success"
    )
    tin_before = _counter_value(llm_tokens_total, model="mx-model", direction="input")
    tout_before = _counter_value(llm_tokens_total, model="mx-model", direction="output")

    resp = asyncio.run(pool.call("mx-1", [{"role": "user", "content": "hi"}]))
    assert resp.content == "ok"

    assert (
        _counter_value(llm_requests_total, model="mx-model", provider="qwen", status="success")
        == req_before + 1
    )
    assert _counter_value(llm_tokens_total, model="mx-model", direction="input") == tin_before + 5
    assert (
        _counter_value(llm_tokens_total, model="mx-model", direction="output") == tout_before + 7
    )


def test_pool_call_records_error_metrics(monkeypatch):
    from moa_gateway.config import ModelEndpointConfig
    from moa_gateway.observability.metrics import llm_requests_total
    from moa_gateway.providers.base import ProviderError

    cfg = ModelEndpointConfig(
        id="mx-2", provider="deepseek", model="mx-bad", tier="free", enabled=True
    )
    pool = _make_pool(monkeypatch, cfg)
    ep = pool.endpoints["mx-2"]
    ep.provider_obj = _FakeProvider(err=ProviderError("boom", status=500))  # type: ignore[assignment]

    err_before = _counter_value(
        llm_requests_total, model="mx-bad", provider="deepseek", status="error"
    )

    with pytest.raises(Exception):
        asyncio.run(pool.call("mx-2", [{"role": "user", "content": "hi"}], max_retries=1))

    # review B-M1: exactly ONE error record per attempted endpoint — the
    # chain tail must not add a second (double-counted) error.
    assert (
        _counter_value(llm_requests_total, model="mx-bad", provider="deepseek", status="error")
        == err_before + 1
    )


def test_pool_tail_error_only_when_nothing_attempted(monkeypatch):
    """All-unavailable chain: single tail error record, no double counting."""
    from moa_gateway.config import ModelEndpointConfig
    from moa_gateway.observability.metrics import llm_requests_total

    cfg = ModelEndpointConfig(
        id="mx-off", provider="deepseek", model="mx-off", tier="free", enabled=False
    )
    pool = _make_pool(monkeypatch, cfg)

    err_before = _counter_value(
        llm_requests_total, model="mx-off", provider="deepseek", status="error"
    )
    with pytest.raises(Exception):
        asyncio.run(pool.call("mx-off", [{"role": "user", "content": "hi"}], max_retries=1))
    assert (
        _counter_value(llm_requests_total, model="mx-off", provider="deepseek", status="error")
        == err_before + 1
    )


def test_pool_mock_traffic_attributed_to_mock_provider(monkeypatch):
    """Review B-M2: MockProvider traffic must not inflate real-provider
    counters; it is attributed to provider="mock" with zero cost."""
    from moa_gateway.config import ModelEndpointConfig
    from moa_gateway.observability.metrics import llm_cost_dollars, llm_requests_total
    from moa_gateway.providers import MockProvider

    cfg = ModelEndpointConfig(
        id="mx-mock", provider="qwen", model="mx-mock", tier="free", enabled=True
    )
    pool = _make_pool(monkeypatch, cfg)
    ep = pool.endpoints["mx-mock"]
    ep.provider_obj = MockProvider(model="mx-mock")  # type: ignore[assignment]

    real_before = _counter_value(
        llm_requests_total, model="mx-mock", provider="qwen", status="success"
    )
    mock_before = _counter_value(
        llm_requests_total, model="mx-mock", provider="mock", status="success"
    )
    # llm_cost_dollars is labeled (model, org_id); mock traffic must add zero
    # cost so the counter for this model must stay untouched.
    cost_before = _counter_value(llm_cost_dollars, model="mx-mock", org_id="default")

    asyncio.run(pool.call("mx-mock", [{"role": "user", "content": "hi"}]))

    assert (
        _counter_value(llm_requests_total, model="mx-mock", provider="qwen", status="success")
        == real_before
    )
    assert (
        _counter_value(llm_requests_total, model="mx-mock", provider="mock", status="success")
        == mock_before + 1
    )
    assert _counter_value(llm_cost_dollars, model="mx-mock", org_id="default") == cost_before


# ---------------------------------------------------------------------------
# Review fixes: ratelimit yaml exemption + conditional failure propagation
# ---------------------------------------------------------------------------


def test_ratelimit_exempts_yaml_trusted_keys():
    """Review A-M1: internal loopback uses the yaml key; it must not consume
    the shared per-key RPM bucket."""
    from types import SimpleNamespace

    from moa_gateway.ratelimit import RateLimiter

    class _TripwireStorage:
        def incr_rpm(self, *a, **k):
            raise AssertionError("yaml key must not touch the RPM bucket")

        def get_daily_tokens(self, *a, **k):
            raise AssertionError("yaml key must not touch the daily token bucket")

        def incr_daily_tokens(self, *a, **k):
            raise AssertionError("yaml key must not touch the daily token bucket")

    rl = object.__new__(RateLimiter)
    rl.settings = SimpleNamespace(
        enabled=True, per_key_rpm=60, per_key_daily_tokens=1000
    )
    rl.storage = _TripwireStorage()

    key_info = {"source": "yaml", "key_id": "yaml"}
    used, limit, _, _ = rl.check_and_incr(key_info)
    assert used == 0 and limit == 60
    rl.incr_tokens(key_info, 500)  # must not raise


def test_workflow_conditional_unknown_branch_fails(monkeypatch):
    """Review A-M2: an invalid branch type must fail the workflow instead of
    silently returning an empty output."""
    from moa_gateway.workflows.yaml_workflow import WorkflowYAML

    wf = WorkflowYAML(
        """
name: cond-bad
version: "1.0"
steps:
  - id: c1
    type: conditional
    condition: "true"
    if_true:
      type: caht
      inputs: {}
"""
    )
    result = asyncio.run(wf.execute({}))
    assert result["success"] is False
    assert "c1" in result["error"]


# ---------------------------------------------------------------------------
# Router last-resort fallback (auto must not 503 when target tier is empty)
# ---------------------------------------------------------------------------


def test_router_upgrades_when_target_tier_empty(monkeypatch):
    from moa_gateway.config import ModelEndpointConfig
    from moa_gateway.router import IntelligentRouter

    # only a premium endpoint exists; trivial queries map to the free tier
    cfg = ModelEndpointConfig(
        id="only-premium", provider="qwen", model="pm", tier="premium", enabled=True
    )
    pool = _make_pool(monkeypatch, cfg)
    router = IntelligentRouter(model_pool=pool)
    decision = router.route("hi")
    assert decision.primary is not None
    assert decision.primary.id == "only-premium"


# ---------------------------------------------------------------------------
# Workflow failure propagation (no silent empty outputs)
# ---------------------------------------------------------------------------


def test_workflow_step_failure_propagates(monkeypatch):
    import httpx

    from moa_gateway.workflows.yaml_workflow import WorkflowYAML

    class _Err503(_FakeAsyncClient):
        def __init__(self, *a, **k):
            super().__init__(
                *a, response=_FakeHttpResponse(503, text="no available model"), **k
            )

    monkeypatch.setenv("MOA_GATEWAY_KEY", "wf-key-1")
    monkeypatch.setattr(httpx, "AsyncClient", _Err503)

    wf = WorkflowYAML(
        """
name: fail-chat
version: "1.0"
steps:
  - id: s1
    type: chat
    inputs:
      model: m
      prompt: hi
"""
    )
    result = asyncio.run(wf.execute({}))
    assert result["success"] is False
    assert "s1" in result["error"]
    assert result["steps"][0]["success"] is False
