"""Wave B4 regression tests — D7 usage accounting, D12 run robustness,
D13 persistent TaskBoard."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# T4.1 — agent loops accumulate real LLM usage (D7)
# ---------------------------------------------------------------------------


def test_react_loop_accumulates_usage():
    from moa_gateway.agent_loop import LlmOutcome, LlmUsage, ReActLoop

    async def llm_call(messages, **params):
        if len([m for m in messages if m["role"] == "user" and "Observation" in m.get("content", "")]) == 0:
            return LlmOutcome(
                content='Thought: search\nAction: echo\nAction Input: {"text": "hi"}',
                usage=LlmUsage(prompt_tokens=10, completion_tokens=5, cost=0.01),
            )
        return LlmOutcome(
            content="Thought: done\nFinal Answer: 42",
            usage=LlmUsage(prompt_tokens=12, completion_tokens=6, cost=0.02),
        )

    loop = ReActLoop(llm_call)

    async def echo(text: str) -> str:
        return text

    loop.tool_executor.register("echo", echo, "echo tool")

    result = asyncio.run(loop.run([{"role": "user", "content": "go"}]))
    assert result.success is True
    assert result.final_response == "42"
    assert result.iterations == 2
    assert result.total_tokens == 10 + 5 + 12 + 6
    assert result.total_cost == pytest.approx(0.03)


def test_react_loop_str_callback_still_works():
    """Legacy callbacks returning bare str must keep working (zero usage)."""
    from moa_gateway.agent_loop import ReActLoop

    async def llm_call(messages, **params):
        return "Thought: ok\nFinal Answer: legacy"

    loop = ReActLoop(llm_call)
    result = asyncio.run(loop.run([{"role": "user", "content": "go"}]))
    assert result.success is True
    assert result.final_response == "legacy"
    assert result.total_tokens == 0
    assert result.total_cost == 0.0


def test_plan_execute_accumulates_usage():
    from moa_gateway.agent_loop import LlmOutcome, LlmUsage, PlanExecuteLoop

    calls = {"n": 0}

    async def llm_call(messages, **params):
        calls["n"] += 1
        if calls["n"] == 1:
            content = '[{"description": "s1", "tool": "llm", "arguments": {"query": "x"}, "depends_on": []}]'
        elif calls["n"] == 2:
            content = "step done"
        else:
            content = "final answer"
        return LlmOutcome(
            content=content,
            usage=LlmUsage(prompt_tokens=4, completion_tokens=2, cost=0.001),
        )

    loop = PlanExecuteLoop(llm_call)
    result = asyncio.run(loop.run([{"role": "user", "content": "plan it"}]))
    assert result.success is True
    assert calls["n"] == 3  # plan + 1 llm step + synthesis
    assert result.total_tokens == 3 * (4 + 2)
    assert result.total_cost == pytest.approx(0.003)


# ---------------------------------------------------------------------------
# T4.2 — assistant runs: timeout, duplicate guard, zombie sweep (D12)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_assistant_storage(tmp_path, monkeypatch):
    import moa_gateway.assistant.storage as ast_storage

    ast_storage._storage = None
    original_init = ast_storage.AssistantStorage.__init__

    def patched_init(self, data_dir=None):
        original_init(self, data_dir=str(tmp_path / "assistants"))

    monkeypatch.setattr(ast_storage.AssistantStorage, "__init__", patched_init)
    yield
    ast_storage._storage = None


def test_executor_timeout_marks_run_failed(monkeypatch):
    from moa_gateway.assistant import executor
    from moa_gateway.assistant.models import Run

    monkeypatch.setattr(executor, "_run_timeouts", lambda: (0.05, 1.0))

    async def slow_inner(run):
        await asyncio.sleep(0.5)
        return run

    monkeypatch.setattr(executor, "_execute_run_inner", slow_inner)

    run = Run(thread_id="thread_t", assistant_id="asst_t")
    result = asyncio.run(executor.execute_run(run))
    assert result.status == "failed"
    assert result.last_error is not None
    assert result.last_error["code"] == "timeout"
    # guard must be released so a retry is possible
    assert run.id not in executor._active_run_ids


def test_executor_skips_duplicate_execution(monkeypatch):
    from moa_gateway.assistant import executor
    from moa_gateway.assistant.models import Run

    run = Run(thread_id="thread_d", assistant_id="asst_d")
    executor._active_run_ids.add(run.id)
    try:
        async def boom(inner_run):
            raise AssertionError("inner must not run for a duplicate")

        monkeypatch.setattr(executor, "_execute_run_inner", boom)
        result = asyncio.run(executor.execute_run(run))
        assert result.id == run.id
        # still occupied: the *real* executor owns the slot
        assert run.id in executor._active_run_ids
    finally:
        executor._active_run_ids.discard(run.id)


def test_cleanup_stale_runs_fails_zombies():
    from moa_gateway.assistant.models import Run
    from moa_gateway.assistant.storage import get_storage

    storage = get_storage()
    queued = Run(thread_id="th1", assistant_id="a", status="queued")
    running = Run(thread_id="th1", assistant_id="a", status="in_progress")
    done = Run(thread_id="th2", assistant_id="a", status="completed")
    for r in (queued, running, done):
        storage.save_run(r)

    swept = storage.cleanup_stale_runs()
    assert swept == 2
    assert storage.get_run(queued.id).status == "failed"
    assert storage.get_run(running.id).status == "failed"
    assert storage.get_run(running.id).last_error["message"] == (
        "run interrupted by gateway restart"
    )
    assert storage.get_run(done.id).status == "completed"


@pytest.fixture
def assistant_app(monkeypatch):
    from fastapi import FastAPI

    import moa_gateway.routes.assistant as assistant_routes
    from moa_gateway.auth import require_api_key

    monkeypatch.setattr(assistant_routes, "execute_run", AsyncMock())

    test_app = FastAPI()
    test_app.include_router(assistant_routes.router)
    test_app.dependency_overrides[require_api_key] = lambda: {
        "key": "test-key",
        "key_id": "test-key",
        "name": "test",
    }
    return test_app


def test_executor_timeout_cancels_real_inner(monkeypatch):
    """Timeout must cancel a *real* inner execution blocked in _call_llm and
    land the run as failed on disk (CancelledError must pierce the inner
    except-Exception and be converted by the wait_for wrapper)."""
    from moa_gateway.assistant import executor
    from moa_gateway.assistant.models import Assistant, Run, Thread
    from moa_gateway.assistant.storage import get_storage

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
    assert result.last_error["code"] == "timeout"
    on_disk = storage.get_run(run.id)
    assert on_disk is not None and on_disk.status == "failed"
    assert run.id not in executor._active_run_ids


def test_submit_tool_outputs_continues_run(monkeypatch):
    """Full submit flow: route-flipped in_progress run is accepted, the LLM is
    re-called with the tool output, and the run completes with a message."""
    from moa_gateway.assistant import executor
    from moa_gateway.assistant.models import Assistant, Run, Thread
    from moa_gateway.assistant.storage import get_storage

    async def fake_llm(model, messages, tools, temperature):
        assert any(m["role"] == "tool" for m in messages)
        return {
            "choices": [{"message": {"content": "final"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }

    monkeypatch.setattr(executor, "_call_llm", fake_llm)

    storage = get_storage()
    asst = storage.save_assistant(Assistant(owner_key_id="k"))
    thread = storage.save_thread(Thread(owner_key_id="k"))
    run = Run(thread_id=thread.id, assistant_id=asst.id, status="requires_action")
    storage.save_run(run)

    # simulate the route's persisted requires_action -> in_progress flip
    run.status = "in_progress"
    storage.save_run(run)

    result = asyncio.run(
        executor.submit_tool_outputs(run, [{"tool_call_id": "tc1", "output": "42"}])
    )
    assert result.status == "completed"
    msgs = storage.list_messages(thread.id)
    assert any(m.role == "assistant" for m in msgs)

    # a terminal run can no longer accept tool outputs
    with pytest.raises(ValueError):
        asyncio.run(executor.submit_tool_outputs(result, []))


@pytest.mark.asyncio
async def test_create_run_conflict_returns_409(assistant_app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=assistant_app), base_url="http://test"
    ) as client:
        a = (await client.post("/v1/assistants", json={"model": "gpt-4o"})).json()
        t = (await client.post("/v1/threads", json={})).json()

        r1 = await client.post(
            f"/v1/threads/{t['id']}/runs", json={"assistant_id": a["id"]}
        )
        assert r1.status_code == 200
        assert r1.json()["status"] == "queued"

        # second run while the first is still queued → 409
        r2 = await client.post(
            f"/v1/threads/{t['id']}/runs", json={"assistant_id": a["id"]}
        )
        assert r2.status_code == 409
        assert r1.json()["id"] in r2.json()["detail"]


@pytest.mark.asyncio
async def test_submit_route_persists_flip_and_blocks_racing_submit(monkeypatch):
    """The route must persist the in_progress flip *before* the background
    task starts, and a second submit must get 400 (not silently re-run)."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    import moa_gateway.routes.assistant as assistant_routes
    from moa_gateway.assistant.storage import get_storage
    from moa_gateway.auth import require_api_key

    monkeypatch.setattr(assistant_routes, "execute_run", AsyncMock())
    monkeypatch.setattr(assistant_routes, "submit_tool_outputs", AsyncMock())

    app = FastAPI()
    app.include_router(assistant_routes.router)
    app.dependency_overrides[require_api_key] = lambda: {
        "key": "test-key",
        "key_id": "test-key",
        "name": "test",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        a = (await client.post("/v1/assistants", json={"model": "gpt-4o"})).json()
        t = (await client.post("/v1/threads", json={})).json()
        run = (
            await client.post(f"/v1/threads/{t['id']}/runs", json={"assistant_id": a["id"]})
        ).json()

        # move the run into requires_action (as a tool-calling LLM round would)
        storage = get_storage()
        stored = storage.get_run(run["id"])
        stored.status = "requires_action"
        storage.save_run(stored)

        r1 = await client.post(
            f"/v1/threads/{t['id']}/runs/{run['id']}/submit_tool_outputs",
            json={"tool_outputs": [{"tool_call_id": "tc", "output": "x"}]},
        )
        assert r1.status_code == 200
        assert r1.json()["status"] == "in_progress"
        # the flip must be on disk, not just in the response object
        assert storage.get_run(run["id"]).status == "in_progress"

        # racing second submit → 400
        r2 = await client.post(
            f"/v1/threads/{t['id']}/runs/{run['id']}/submit_tool_outputs",
            json={"tool_outputs": [{"tool_call_id": "tc", "output": "y"}]},
        )
        assert r2.status_code == 400


# ---------------------------------------------------------------------------
# T4.3 — persistent TaskBoard + /v1/agent/tasks CRUD (D13)
# ---------------------------------------------------------------------------


def _make_board(tmp_path, monkeypatch):
    from moa_gateway.capability.subagent_comms import SqliteTaskBoard
    from moa_gateway.storage import Storage

    # bypass admin bootstrap: test settings have no admin_password
    monkeypatch.setattr(Storage, "_bootstrap_admin", lambda self, settings: None)
    storage = Storage(db_path=tmp_path / "tasks.db")
    return SqliteTaskBoard(storage=storage), storage


def test_task_board_persists_across_instances(tmp_path, monkeypatch):
    from moa_gateway.capability.subagent_comms import SqliteTaskBoard

    board, storage = _make_board(tmp_path, monkeypatch)
    parent = board.create_task("parent", assignee="s1")
    child = board.create_task("child", parent=parent)

    # a brand-new board over the same DB sees the tasks (restart survival)
    board2 = SqliteTaskBoard(storage=storage)
    assert board2.get_task(parent).title == "parent"
    assert [t.task_id for t in board2.get_subtasks(parent)] == [child]

    board2.update_status(child, "completed")
    assert board.get_task(child).status == "completed"

    filtered = board.list_tasks(status="completed")
    assert [t.task_id for t in filtered] == [child]

    with pytest.raises(KeyError):
        board.create_task("bad", parent="t_nope")
    with pytest.raises(ValueError):
        board.update_status(child, "not_a_status")

    assert board.delete_task(parent) is True
    assert board.get_task(parent) is None


@pytest.fixture
def tasks_app(monkeypatch, tmp_path):
    from fastapi import FastAPI

    from moa_gateway.auth import require_api_key
    from moa_gateway.capability import subagent_comms
    from moa_gateway.routes.tasks import router

    board, _ = _make_board(tmp_path, monkeypatch)
    monkeypatch.setattr(subagent_comms, "get_task_board", lambda: board)

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[require_api_key] = lambda: {
        "key": "test-key",
        "key_id": "test-key",
        "name": "test",
    }
    return test_app


@pytest.mark.asyncio
async def test_tasks_crud_endpoints(tasks_app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=tasks_app), base_url="http://test"
    ) as client:
        # create
        r = await client.post("/v1/agent/tasks", json={"title": "task A"})
        assert r.status_code == 200
        task = r.json()
        assert task["title"] == "task A"
        assert task["status"] == "pending"
        tid = task["task_id"]

        # list + filter
        r = await client.get("/v1/agent/tasks")
        assert r.status_code == 200
        assert len(r.json()["data"]) == 1
        r = await client.get("/v1/agent/tasks", params={"status": "completed"})
        assert r.json()["data"] == []

        # update status
        r = await client.put(f"/v1/agent/tasks/{tid}", json={"status": "in_progress"})
        assert r.status_code == 200
        assert r.json()["status"] == "in_progress"

        # invalid status → 422
        r = await client.put(f"/v1/agent/tasks/{tid}", json={"status": "bogus"})
        assert r.status_code == 422

        # delete
        r = await client.delete(f"/v1/agent/tasks/{tid}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True

        # gone
        r = await client.get(f"/v1/agent/tasks/{tid}")
        assert r.status_code == 404
        r = await client.delete(f"/v1/agent/tasks/{tid}")
        assert r.status_code == 404

        # parent must exist → 404
        r = await client.post(
            "/v1/agent/tasks", json={"title": "orphan", "parent_task_id": "t_missing"}
        )
        assert r.status_code == 404

        # invalid list filter → 422
        r = await client.get("/v1/agent/tasks", params={"status": "nope"})
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_tasks_pagination_has_more(tasks_app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=tasks_app), base_url="http://test"
    ) as client:
        for i in range(3):
            assert (await client.post("/v1/agent/tasks", json={"title": f"t{i}"})).status_code == 200

        r = await client.get("/v1/agent/tasks", params={"limit": 2})
        body = r.json()
        assert len(body["data"]) == 2
        assert body["has_more"] is True
        assert body["total"] == 3

        r = await client.get("/v1/agent/tasks", params={"limit": 2, "offset": 2})
        body = r.json()
        assert len(body["data"]) == 1
        assert body["has_more"] is False

        # out-of-range pagination params → 422
        assert (await client.get("/v1/agent/tasks", params={"limit": 0})).status_code == 422
        assert (await client.get("/v1/agent/tasks", params={"offset": -1})).status_code == 422


@pytest.mark.asyncio
async def test_tasks_unassign_via_explicit_null(tasks_app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=tasks_app), base_url="http://test"
    ) as client:
        task = (
            await client.post(
                "/v1/agent/tasks", json={"title": "assigned", "assignee_session": "s1"}
            )
        ).json()
        tid = task["task_id"]
        assert task["assignee_session"] == "s1"

        # explicit null clears the assignee
        r = await client.put(f"/v1/agent/tasks/{tid}", json={"assignee_session": None})
        assert r.status_code == 200
        assert r.json()["assignee_session"] is None

        # absent key leaves it untouched
        r = await client.put(f"/v1/agent/tasks/{tid}", json={"assignee_session": "s2"})
        assert r.json()["assignee_session"] == "s2"
        r = await client.put(f"/v1/agent/tasks/{tid}", json={"status": "completed"})
        assert r.json()["assignee_session"] == "s2"
