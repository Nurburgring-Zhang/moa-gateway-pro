"""Wave B5 — T5.1 tracer wiring regression tests.

Verifies the span wiring added across model_pool / workflow / moa routes /
assistant executor / agent loop:
- nested spans share one trace_id and carry a correct parent chain
- the lazy tracer works even when trace_enabled=false (no setup_tracer call)
- the HTTP middleware root span is the parent of route-level spans
- individual span names/attributes land in get_recent_spans
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient


@pytest.fixture(autouse=True)
def _fresh_tracer(monkeypatch):
    """Every test gets a brand-new global tracer (no cross-test span bleed)."""
    import moa_gateway.observability.tracer as tracer_mod

    monkeypatch.setattr(tracer_mod, "_gateway_tracer", None)
    yield
    tracer_mod.clear_trace_context()


# ============ pure tracer nesting ============


def test_nested_spans_share_trace_and_parent():
    """Child spans must inherit trace_id and point at the enclosing span."""
    from moa_gateway.observability.tracer import (
        clear_trace_context,
        get_current_span_id,
        get_current_trace_id,
        get_tracer,
        set_trace_context,
    )

    trace_id = uuid.uuid4().hex
    root_span_id = uuid.uuid4().hex[:16]
    set_trace_context(trace_id, root_span_id)
    try:
        tracer = get_tracer()
        with tracer.start_span("outer") as outer:
            assert outer.trace_id == trace_id
            assert outer.parent_span_id == root_span_id
            with tracer.start_span("inner") as inner:
                assert inner.trace_id == trace_id
                assert inner.parent_span_id == outer.span_id

        spans = tracer.get_recent_spans(limit=10)
        names = [s["name"] for s in spans]
        assert "outer" in names and "inner" in names
        # context must be restored to the request root after the spans close
        assert get_current_trace_id() == trace_id
        assert get_current_span_id() == root_span_id
    finally:
        clear_trace_context()


def test_lazy_tracer_works_without_setup():
    """trace_enabled=false means setup_tracer never runs; the lazy singleton
    created by get_tracer() must still record spans."""
    import moa_gateway.observability.tracer as tracer_mod

    assert tracer_mod._gateway_tracer is None  # guaranteed by fixture
    tracer = tracer_mod.get_tracer()
    assert isinstance(tracer, tracer_mod.GatewayTracer)
    with tracer.start_span("lazy-op") as span:
        span.set_attribute("k", "v")
    recent = tracer.get_recent_spans(limit=5)
    assert any(s["name"] == "lazy-op" and s["attributes"]["k"] == "v" for s in recent)


def test_config_otlp_endpoint_field():
    """ObservabilityConfig gains otlp_endpoint (empty default = in-memory)."""
    from moa_gateway.config import Settings

    s = Settings()
    assert s.observability.otlp_endpoint == ""
    s2 = Settings(observability={"otlp_endpoint": "http://collector:4317"})
    assert s2.observability.otlp_endpoint == "http://collector:4317"


# ============ middleware root span ============


def test_middleware_records_root_span():
    """Every HTTP request records a parentless root span with its trace_id."""
    from moa_gateway.observability.tracer import get_tracer
    from moa_gateway.server import create_app

    app = create_app()
    client = TestClient(app)
    trace_id = uuid.uuid4().hex
    resp = client.get("/health", headers={"X-Trace-ID": trace_id})
    assert resp.status_code == 200
    assert resp.headers["X-Trace-ID"] == trace_id

    root = [
        s
        for s in get_tracer().get_recent_spans(limit=50)
        if s["name"] == "GET /health" and s["trace_id"] == trace_id
    ]
    assert len(root) == 1
    assert root[0]["parent_span_id"] is None
    assert root[0]["span_id"] == resp.headers["X-Span-ID"]


# ============ route-level spans nest under the request ============


def test_moa_execute_span_nests_under_request_trace(monkeypatch):
    """POST /v1/moa/execute must emit a moa.execute child span whose
    parent_span_id is the middleware root span."""
    from moa_gateway import config as _cfg
    from moa_gateway.config import Settings
    from moa_gateway.observability.tracer import get_tracer
    from moa_gateway.server import create_app

    monkeypatch.setattr(
        _cfg, "_settings", Settings(auth={"gateway_api_keys": ["b5-test-key-001"]})
    )

    class _FakeResult:
        references: list = []
        final_content = "ok"
        mock_used = False  # v3.1.1: route reads result.mock_used for D6 labeling

        def to_dict(self):
            return {"final": "ok", "mock": False}

    class _FakeMoa:
        async def execute(self, **kwargs):
            return _FakeResult()

    monkeypatch.setattr("moa_gateway.routes.moa.get_moa", lambda: _FakeMoa())

    app = create_app()
    client = TestClient(app)
    trace_id = uuid.uuid4().hex
    resp = client.post(
        "/v1/moa/execute",
        headers={"X-Trace-ID": trace_id, "Authorization": "Bearer b5-test-key-001"},
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200, resp.text
    root_span_id = resp.headers["X-Span-ID"]

    spans = get_tracer().get_recent_spans(limit=100)
    moa_spans = [s for s in spans if s["name"] == "moa.execute"]
    assert len(moa_spans) == 1
    span = moa_spans[0]
    assert span["trace_id"] == trace_id
    assert span["parent_span_id"] == root_span_id
    assert span["attributes"]["moa.references"] == 0


def test_workflow_step_span_records_attributes(monkeypatch):
    """workflow.step span wraps each step and records id/type/success."""
    from moa_gateway.observability.tracer import get_tracer
    from moa_gateway.workflows.yaml_workflow import WorkflowStep, WorkflowYAML

    async def fake_inner(self, step, inputs, context, outputs):
        return {"success": True, "output": "x"}

    monkeypatch.setattr(WorkflowYAML, "_execute_step_inner", fake_inner)

    step = WorkflowStep(id="s1", type="transform")
    wf = object.__new__(WorkflowYAML)  # skip YAML parsing; only the wrapper matters
    result = asyncio.run(WorkflowYAML._execute_step(wf, step, {}, {}, {}))
    assert result["success"] is True

    spans = [s for s in get_tracer().get_recent_spans(limit=10) if s["name"] == "workflow.step"]
    assert len(spans) == 1
    assert spans[0]["attributes"]["workflow.step.id"] == "s1"
    assert spans[0]["attributes"]["workflow.step.type"] == "transform"
    assert spans[0]["attributes"]["workflow.step.success"] is True


def test_model_pool_call_span_records_tokens_and_mock(monkeypatch):
    """model_pool.call span carries endpoint/model attrs + token/mock facts."""
    from moa_gateway.model_pool import ModelPool
    from moa_gateway.observability.tracer import get_tracer
    from moa_gateway.providers.base import ChatResponse

    pool = object.__new__(ModelPool)
    ep = SimpleNamespace(config=SimpleNamespace(model="m/x", provider="mock"))
    pool.endpoints = {"e1": ep}

    async def fake_chain(self, endpoint_id, ep_, messages, temperature=0.6,
                         max_tokens=4096, tools=None, stream=False, max_retries=3):
        return ChatResponse(prompt_tokens=3, completion_tokens=4, total_tokens=7)

    monkeypatch.setattr(ModelPool, "_call_chain", fake_chain)
    monkeypatch.setattr(ModelPool, "_ep_is_mock", lambda self, e: True)

    resp = asyncio.run(pool.call("e1", [{"role": "user", "content": "hi"}]))
    assert resp.total_tokens == 7

    spans = [s for s in get_tracer().get_recent_spans(limit=10) if s["name"] == "model_pool.call"]
    assert len(spans) == 1
    attrs = spans[0]["attributes"]
    assert attrs["endpoint.id"] == "e1"
    assert attrs["model"] == "m/x"
    assert attrs["llm.mock"] is True
    assert attrs["llm.tokens"] == 7


def test_executor_run_span_recorded_on_timeout(monkeypatch):
    """assistant.run span wraps the whole run even when it times out."""
    from moa_gateway.assistant import executor
    from moa_gateway.assistant.models import Assistant, Run, Thread
    from moa_gateway.assistant.storage import get_storage
    from moa_gateway.observability.tracer import get_tracer

    monkeypatch.setattr(executor, "_run_timeouts", lambda: (0.05, 1.0))

    async def hang_llm(model, messages, tools, temperature):
        await asyncio.sleep(5)
        return {}

    monkeypatch.setattr(executor, "_call_llm", hang_llm)

    storage = get_storage()
    asst = storage.save_assistant(Assistant(owner_key_id="k"))
    thread = storage.save_thread(Thread(owner_key_id="k"))
    run = Run(thread_id=thread.id, assistant_id=asst.id)

    result = asyncio.run(executor.execute_run(run))
    assert result.status == "failed"

    spans = [s for s in get_tracer().get_recent_spans(limit=20) if s["name"] == "assistant.run"]
    assert len(spans) == 1
    assert spans[0]["attributes"]["assistant.run.id"] == run.id
