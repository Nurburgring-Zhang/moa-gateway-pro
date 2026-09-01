"""P8 主动任务分析闭环测试 — TaskAnalyzer / CapabilityRouter / TaskSupervisor。

验收标准对照:
- P8-1 复合任务分解出 ≥3 子任务且依赖正确 (LLM 以受控回调注入, 解析/校验真实)
- P8-2 能力路由分发决策有证据; 错配 capability 被拒
- P8-3 chaos 注入单路失败, 经重试/self-heal 任务仍完成
"""
from __future__ import annotations

import pytest

from moa_gateway.task_pipeline import (
    CapabilityRouter,
    MultimodalAllFailedError,
    SubTask,
    TaskAnalysisError,
    TaskAnalyzer,
    TaskSupervisor,
    _extract_json_array,
    _validate_plan,
)


# ---------------------------------------------------------------------------
# JSON 提取与计划校验
# ---------------------------------------------------------------------------

def test_extract_json_array_bare():
    text = '[{"id": "t1", "capability": "chat"}]'
    assert _extract_json_array(text) == [{"id": "t1", "capability": "chat"}]


def test_extract_json_array_fenced_with_prose():
    text = '好的，分解如下：\n```json\n[{"id":"t1","capability":"moa"}]\n```\n以上。'
    assert _extract_json_array(text) == [{"id": "t1", "capability": "moa"}]


def test_extract_json_array_no_array_raises():
    with pytest.raises(TaskAnalysisError):
        _extract_json_array("没有任何数组")


def test_validate_plan_rejects_bad_capability():
    with pytest.raises(TaskAnalysisError):
        _validate_plan([{"id": "t1", "capability": "hologram"}], 6)


def test_validate_plan_rejects_unknown_dep():
    with pytest.raises(TaskAnalysisError):
        _validate_plan(
            [{"id": "t1", "capability": "chat", "depends_on": ["ghost"]}], 6
        )


def test_validate_plan_rejects_cycle():
    raw = [
        {"id": "t1", "capability": "chat", "depends_on": ["t2"]},
        {"id": "t2", "capability": "chat", "depends_on": ["t1"]},
    ]
    with pytest.raises(TaskAnalysisError):
        _validate_plan(raw, 6)


def test_validate_plan_accepts_dag():
    raw = [
        {"id": "t1", "capability": "chat"},
        {"id": "t2", "capability": "moa", "depends_on": ["t1"]},
        {"id": "t3", "capability": "multimodal", "depends_on": ["t1"]},
    ]
    plan = _validate_plan(raw, 6)
    assert [t.id for t in plan] == ["t1", "t2", "t3"]


# ---------------------------------------------------------------------------
# P8-1: LLM 驱动分解 (受控回调注入, 无启发式兜底)
# ---------------------------------------------------------------------------

class _FakeOutcome:
    def __init__(self, content: str):
        self.content = content


_PLAN_JSON = """[
  {"id":"t1","description":"搜索资料","capability":"chat"},
  {"id":"t2","description":"撰写报告","capability":"moa","depends_on":["t1"]},
  {"id":"t3","description":"生成配图","capability":"multimodal","depends_on":["t1"],
   "params":{"modality":"image","platforms":["cogview"],"prompt":"x"}}
]"""


@pytest.mark.anyio
async def test_analyzer_decomposes_into_dag():
    async def fake_llm(messages, **params):
        # 系统提示必须要求 JSON 分解
        assert "JSON" in messages[0]["content"]
        return _FakeOutcome(_PLAN_JSON)

    analyzer = TaskAnalyzer(llm_call=fake_llm)
    plan = await analyzer.analyze("调研并产出一份带配图的报告")
    assert len(plan) >= 3
    caps = {t.id: t.capability for t in plan}
    assert caps["t2"] == "moa"
    assert plan[1].depends_on == ["t1"]


@pytest.mark.anyio
async def test_analyzer_rejects_invalid_llm_output():
    async def bad_llm(messages, **params):
        return _FakeOutcome("抱歉，我无法分解。")

    analyzer = TaskAnalyzer(llm_call=bad_llm)
    with pytest.raises(TaskAnalysisError):
        await analyzer.analyze("任意任务")


@pytest.mark.anyio
async def test_analyzer_no_heuristic_fallback_without_endpoints(monkeypatch):
    """无模型端点 → 显式失败, 不退回关键词启发式。"""
    import moa_gateway.model_pool as mp

    class _EmptyPool:
        endpoints: dict = {}

    monkeypatch.setattr(mp, "get_model_pool", lambda: _EmptyPool())
    analyzer = TaskAnalyzer()  # 走真实 model_pool 解析路径
    with pytest.raises(TaskAnalysisError):
        await analyzer.analyze("任意任务")


# ---------------------------------------------------------------------------
# P8-2: 能力路由分发
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_router_dispatches_by_capability(monkeypatch):
    router = CapabilityRouter()
    seen: list[str] = []

    async def fake_chat(task, upstream):
        seen.append("chat")
        return "out:chat"

    async def fake_moa(task, upstream):
        seen.append("moa")
        return "out:moa"

    monkeypatch.setattr(router, "_run_chat", fake_chat)
    monkeypatch.setattr(router, "_run_moa", fake_moa)

    t_chat = SubTask(id="a", description="d", capability="chat")
    t_moa = SubTask(id="b", description="d", capability="moa")
    r1 = await router.execute(t_chat, {})
    r2 = await router.execute(t_moa, {})
    assert r1 == "out:chat"
    assert r2 == "out:moa"
    assert seen == ["chat", "moa"]


@pytest.mark.anyio
async def test_router_rejects_unknown_capability():
    router = CapabilityRouter()
    t = SubTask(id="x", description="d", capability="chat")
    t.capability = "teleport"
    with pytest.raises(TaskAnalysisError):
        await router.execute(t, {})


# ---------------------------------------------------------------------------
# P8-3: 监督执行 — 波序 / 重试 / self-heal
# ---------------------------------------------------------------------------

class _ScriptedRouter(CapabilityRouter):
    """按脚本返回成功/失败, 模拟真实执行器的成败。"""

    def __init__(self, script: dict[str, list]):
        super().__init__()
        self.script = script
        self.calls: list[str] = []

    async def execute(self, task, upstream):
        self.calls.append(f"{task.id}:{task.capability}")
        outcomes = self.script.setdefault(task.id, [])
        if not outcomes:
            return f"ok:{task.id}"
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.anyio
async def test_supervisor_wave_order_and_outputs():
    tasks = [
        SubTask(id="t1", description="a", capability="chat"),
        SubTask(id="t2", description="b", capability="moa", depends_on=["t1"]),
        SubTask(id="t3", description="c", capability="chat", depends_on=["t1"]),
    ]
    sup = TaskSupervisor(router=_ScriptedRouter({}))
    result = await sup.run(tasks)
    assert result["success"] is True
    assert result["outputs"]["t1"] == "ok:t1"
    assert result["outputs"]["t2"] == "ok:t2"
    # t2/t3 同波并行, 都在 t1 之后
    assert len(result["trace"]) == 3


@pytest.mark.anyio
async def test_supervisor_retries_then_succeeds():
    # t1 第一次失败, 重试成功
    script = {"t1": [RuntimeError("transient"), "recovered"]}
    sup = TaskSupervisor(router=_ScriptedRouter(script))
    tasks = [SubTask(id="t1", description="a", capability="chat")]
    result = await sup.run(tasks)
    assert result["success"] is True
    assert result["outputs"]["t1"] == "recovered"
    assert tasks[0].attempts == 2


@pytest.mark.anyio
async def test_supervisor_self_heal_moa_to_chat():
    # moa 一直失败 → self-heal 换路到 chat 成功
    class _HealRouter(_ScriptedRouter):
        async def execute(self, task, upstream):
            self.calls.append(f"{task.id}:{task.capability}")
            if task.capability == "moa":
                raise RuntimeError("moa down")
            return f"healed:{task.capability}"

    sup = TaskSupervisor(router=_HealRouter({}))
    tasks = [SubTask(id="t1", description="a", capability="moa")]
    result = await sup.run(tasks)
    assert result["success"] is True
    assert result["outputs"]["t1"] == "healed:chat"
    # 执行轨迹记录了 moa 两次 + self-heal 一次
    assert tasks[0].executors[-1] == "self-heal"


@pytest.mark.anyio
async def test_supervisor_marks_unrecoverable_failure():
    # chat 无 self-heal 路由, 三次全失败 → failed 但整体如实报告
    script = {"t1": [RuntimeError("e1"), RuntimeError("e2"), RuntimeError("e3")]}
    sup = TaskSupervisor(router=_ScriptedRouter(script))
    tasks = [SubTask(id="t1", description="a", capability="chat")]
    result = await sup.run(tasks)
    assert result["success"] is False
    assert tasks[0].status == "failed"
    assert tasks[0].error


@pytest.mark.anyio
async def test_supervisor_self_heal_multimodal_strips_bad_platform():
    """P8-3 补强: multimodal 全路由失败时, self-heal 应剔除永久性不可用平台
    (no_key), 对瞬时失败平台 (timeout) 重扇出。

    回归点: 曾有一版实现里 _run_multimodal 抛裸 RuntimeError 丢掉 FanoutResult,
    导致 _heal_reroute 的多模态分支是死代码。现在异常携带 result dict,
    supervisor 必须真正重扇出并成功。
    """
    seen_platform_sets: list[list[str]] = []

    class _HealRouter(CapabilityRouter):
        async def execute(self, task, upstream):
            platforms = list(task.params.get("platforms") or [])
            seen_platform_sets.append(platforms)
            if len(platforms) > 1:
                result_dict = {
                    "routes": [
                        {"platform": "flaky-platform", "status": "timeout", "error": "slow"},
                        {"platform": "nokey-platform", "status": "no_key", "error": "no key"},
                    ]
                }
                raise MultimodalAllFailedError("all routes failed", result=result_dict)
            return {"routes": [{"platform": platforms[0], "status": "success"}]}

    sup = TaskSupervisor(router=_HealRouter())
    tasks = [
        SubTask(
            id="m1",
            description="gen image",
            capability="multimodal",
            params={"modality": "image", "platforms": ["flaky-platform", "nokey-platform"]},
        )
    ]
    result = await sup.run(tasks)
    assert result["success"] is True
    assert tasks[0].status == "success"
    assert tasks[0].executors[-1] == "self-heal"
    # 前两次扇出含全部平台, self-heal 后剔除 no_key 平台只重试瞬时失败者
    assert seen_platform_sets[0] == ["flaky-platform", "nokey-platform"]
    assert seen_platform_sets[-1] == ["flaky-platform"]


@pytest.mark.anyio
async def test_supervisor_multimodal_all_permanently_bad_stays_failed():
    """若全部平台都是永久性不可用 (no_key), self-heal 无重试集 → 如实 failed。"""

    class _AllBadRouter(CapabilityRouter):
        async def execute(self, task, upstream):
            result_dict = {
                "routes": [
                    {"platform": "p1", "status": "no_key", "error": "no key"},
                ]
            }
            raise MultimodalAllFailedError("all routes failed", result=result_dict)

    sup = TaskSupervisor(router=_AllBadRouter())
    tasks = [
        SubTask(
            id="m1",
            description="gen image",
            capability="multimodal",
            params={"modality": "image", "platforms": ["p1"]},
        )
    ]
    result = await sup.run(tasks)
    assert result["success"] is False
    assert tasks[0].status == "failed"
    assert tasks[0].error
