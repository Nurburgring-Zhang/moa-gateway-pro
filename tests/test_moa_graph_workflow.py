"""P4-4 moa_graph 工作流步骤测试。

验收标准对照:
- MOA 任务以 moa_graph 步骤类型进入 DAG: 依赖/分波/条件全支持
- 内置 moa-pipeline.yaml 可装载、结构合法、真跑(打桩 HTTP 边界)
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from moa_gateway.workflows.workflow_loader import WorkflowLoader
from moa_gateway.workflows.yaml_workflow import VALID_STEP_TYPES, WorkflowYAML

BUILTIN = Path(__file__).parent.parent / "moa_gateway" / "workflows" / "builtin"


# ---------------------------------------------------------------------------
# 结构与装载
# ---------------------------------------------------------------------------

def test_moa_graph_is_valid_step_type():
    assert "moa_graph" in VALID_STEP_TYPES


def test_builtin_moa_pipeline_loads_with_dag():
    loader = WorkflowLoader()
    wf = loader.load_from_file(BUILTIN / "moa-pipeline.yaml")
    assert wf.name == "moa-pipeline"
    ids = [s.id for s in wf.steps]
    assert ids == ["research", "risk_review", "synthesize", "format"]
    types = {s.id: s.type for s in wf.steps}
    assert types["research"] == "moa_graph"
    assert types["risk_review"] == "moa_graph"
    assert types["synthesize"] == "moa_graph"
    # 依赖声明完整
    synth = next(s for s in wf.steps if s.id == "synthesize")
    assert synth.depends_on == ["research", "risk_review"]
    # 拓扑排序成功 = DAG 合法（构造时已校验, 此处再显式验证）
    order = wf._topological_sort()
    assert order.index("synthesize") > order.index("research")
    assert order.index("synthesize") > order.index("risk_review")


# ---------------------------------------------------------------------------
# 真实执行（打桩 HTTP 边界, 验证请求体/依赖序/图证据）
# ---------------------------------------------------------------------------

_MINIMAL_WF = """
name: graph-test
description: t
version: '1.0'
steps:
  - id: a
    type: moa_graph
    inputs:
      prompt: "solve {{task}}"
      reference_count: 3
    outputs: [r1]
  - id: b
    type: moa_graph
    depends_on: [a]
    inputs:
      prompt: "refine {{steps.a.output}}"
      strategy: judge
      critic_rounds: 2
    outputs: [r2]
"""


@pytest.mark.anyio
async def test_moa_graph_execution_order_and_bodies(monkeypatch):
    import moa_gateway.workflows.yaml_workflow as yw

    calls: list[dict] = []

    async def fake_http_post(url: str, body: dict):
        calls.append({"url": url, "body": body})
        return {
            "final_content": f"answer-{len(calls)}",
            "references": [{"t": 1}, {"t": 2}, {"t": 3}],
            "critics": [{"c": 1}],
            "layers": [{"layer": 1}],
        }

    monkeypatch.setattr(yw, "_http_post", fake_http_post)
    monkeypatch.setattr(yw, "_get_gateway_url", lambda: "http://gw")

    wf = WorkflowYAML(_MINIMAL_WF)
    result = await wf.execute(context={"task": "the mission"})

    assert result["success"] is True
    assert len(calls) == 2
    # 依赖顺序: a 先于 b
    first, second = calls[0]["body"], calls[1]["body"]
    assert first["strategy"] == "layered"  # moa_graph 默认分层图策略
    assert first["reference_count"] == 3
    assert first["messages"][0]["content"] == "solve the mission"
    assert second["strategy"] == "judge"
    assert second["critic_rounds"] == 2
    # b 的 prompt 吃到了 a 的真实输出（DAG 变量流转）
    assert "answer-1" in second["messages"][0]["content"]
    # 最终输出来自 b
    assert result["outputs"]["steps.b.output"] == "answer-2"


@pytest.mark.anyio
async def test_moa_graph_returns_graph_evidence(monkeypatch):
    import moa_gateway.workflows.yaml_workflow as yw

    async def fake_http_post(url: str, body: dict):
        return {
            "final_content": "done",
            "references": [1, 2, 3],
            "critics": [1],
            "layers": [{"layer": 1}, {"layer": 2}],
            "tool_trace": [{"tool": "code_execute"}],
        }

    monkeypatch.setattr(yw, "_http_post", fake_http_post)
    monkeypatch.setattr(yw, "_get_gateway_url", lambda: "http://gw")

    wf = WorkflowYAML(_MINIMAL_WF)
    # 直接调内部方法验证 graph 证据字段
    out = await wf._execute_moa_graph({"prompt": "x"})
    assert out["success"] is True
    assert out["graph"]["strategy"] == "layered"
    assert out["graph"]["references_count"] == 3
    assert out["graph"]["critics_count"] == 1
    assert len(out["graph"]["layers"]) == 2
    assert out["graph"]["tool_trace"] == [{"tool": "code_execute"}]


@pytest.mark.anyio
async def test_moa_graph_http_error_fails_step(monkeypatch):
    import moa_gateway.workflows.yaml_workflow as yw

    async def fake_http_post(url: str, body: dict):
        return {"error": "502 upstream"}

    monkeypatch.setattr(yw, "_http_post", fake_http_post)
    monkeypatch.setattr(yw, "_get_gateway_url", lambda: "http://gw")

    wf = WorkflowYAML(_MINIMAL_WF)
    result = await wf.execute(context={"task": "t"})
    assert result["success"] is False
    assert "502 upstream" in result["error"]


@pytest.mark.anyio
async def test_moa_graph_in_conditional_branch(monkeypatch):
    import moa_gateway.workflows.yaml_workflow as yw

    calls: list[dict] = []

    async def fake_http_post(url: str, body: dict):
        calls.append(body)
        return {"final_content": "branched"}

    monkeypatch.setattr(yw, "_http_post", fake_http_post)
    monkeypatch.setattr(yw, "_get_gateway_url", lambda: "http://gw")

    wf_yaml = """
name: cond-graph
description: t
version: '1.0'
steps:
  - id: gate
    type: conditional
    condition: "{{flag}}"
    if_true:
      type: moa_graph
      inputs:
        prompt: "go deep"
    if_false:
      type: transform
      inputs:
        template: "skip"
    outputs: [g]
"""
    wf = WorkflowYAML(wf_yaml)
    result = await wf.execute(context={"flag": True})
    assert result["success"] is True
    assert len(calls) == 1
    assert calls[0]["strategy"] == "layered"
    assert result["outputs"]["steps.gate.output"] == "branched"

    # flag=False 时走 if_false 分支, 不触发 moa_graph
    calls.clear()
    wf2 = WorkflowYAML(wf_yaml)
    result2 = await wf2.execute(context={"flag": False})
    assert result2["success"] is True
    assert len(calls) == 0
    assert result2["outputs"]["steps.gate.output"] == "skip"
