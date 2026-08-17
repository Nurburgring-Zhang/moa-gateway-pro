"""Tests for OpenAI Assistant API compatible endpoints."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _isolate_assistant_storage(tmp_path, monkeypatch):
    """Isolate assistant storage to temp directory."""
    import moa_gateway.assistant.storage as ast_storage
    ast_storage._storage = None
    monkeypatch.setattr(ast_storage, "_storage", None)
    # Patch the class to use tmp_path
    original_init = ast_storage.AssistantStorage.__init__

    def patched_init(self, data_dir=None):
        original_init(self, data_dir=str(tmp_path / "assistants"))

    monkeypatch.setattr(ast_storage.AssistantStorage, "__init__", patched_init)


@pytest.fixture
def app():
    """Create test app with assistant routes."""
    from fastapi import FastAPI

    from moa_gateway.routes.assistant import router

    test_app = FastAPI()
    test_app.include_router(router)

    # Override auth dependency
    from moa_gateway.auth import require_api_key
    test_app.dependency_overrides[require_api_key] = lambda: {"key": "test-key", "key_id": "test-key", "name": "test"}
    return test_app


@pytest.fixture
async def client(app):
    """Async test client."""
    from httpx import ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_create_assistant(client):
    """Test creating an assistant."""
    resp = await client.post("/v1/assistants", json={
        "model": "gpt-4o",
        "name": "Test Assistant",
        "instructions": "You are a helpful assistant.",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "assistant"
    assert data["name"] == "Test Assistant"
    assert data["model"] == "gpt-4o"
    assert data["id"].startswith("asst_")


@pytest.mark.asyncio
async def test_create_thread_and_message(client):
    """Test creating a thread and adding a message."""
    # Create thread
    resp = await client.post("/v1/threads", json={})
    assert resp.status_code == 200
    thread = resp.json()
    assert thread["object"] == "thread"
    thread_id = thread["id"]

    # Add message
    resp = await client.post(f"/v1/threads/{thread_id}/messages", json={
        "role": "user",
        "content": "Hello, world!",
    })
    assert resp.status_code == 200
    msg = resp.json()
    assert msg["role"] == "user"
    assert msg["thread_id"] == thread_id
    assert msg["content"][0]["text"]["value"] == "Hello, world!"


@pytest.mark.asyncio
async def test_list_messages(client):
    """Test listing messages in a thread."""
    # Create thread
    resp = await client.post("/v1/threads", json={})
    thread_id = resp.json()["id"]

    # Add messages
    await client.post(f"/v1/threads/{thread_id}/messages", json={"role": "user", "content": "msg1"})
    await client.post(f"/v1/threads/{thread_id}/messages", json={"role": "user", "content": "msg2"})

    # List
    resp = await client.get(f"/v1/threads/{thread_id}/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert len(data["data"]) == 2


@pytest.mark.asyncio
async def test_create_run(client):
    """Test creating a run returns queued status."""
    # Create assistant
    resp = await client.post("/v1/assistants", json={"model": "gpt-4o", "name": "A1"})
    assistant_id = resp.json()["id"]

    # Create thread
    resp = await client.post("/v1/threads", json={})
    thread_id = resp.json()["id"]

    # Add message
    await client.post(f"/v1/threads/{thread_id}/messages", json={"role": "user", "content": "hi"})

    # Create run (mock executor to avoid actual LLM call)
    with patch("moa_gateway.routes.assistant._run_in_background", new_callable=AsyncMock):
        resp = await client.post(f"/v1/threads/{thread_id}/runs", json={
            "assistant_id": assistant_id,
        })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "queued"
    assert run["thread_id"] == thread_id
    assert run["assistant_id"] == assistant_id


@pytest.mark.asyncio
async def test_get_run_not_found(client):
    """Test getting a non-existent run returns 404."""
    resp = await client.post("/v1/threads", json={})
    thread_id = resp.json()["id"]

    resp = await client.get(f"/v1/threads/{thread_id}/runs/run_nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_full_lifecycle(client):
    """Test full lifecycle: assistant -> thread -> message -> run -> poll."""
    # Create assistant
    resp = await client.post("/v1/assistants", json={
        "model": "gpt-4o",
        "name": "Lifecycle Test",
        "instructions": "Reply with Hello",
    })
    assert resp.status_code == 200
    assistant_id = resp.json()["id"]

    # Create thread with initial message
    resp = await client.post("/v1/threads", json={
        "messages": [{"role": "user", "content": "Say hello"}],
    })
    assert resp.status_code == 200
    thread_id = resp.json()["id"]

    # List messages to verify initial message
    resp = await client.get(f"/v1/threads/{thread_id}/messages")
    assert len(resp.json()["data"]) == 1

    # Create run (mock background execution)
    with patch("moa_gateway.routes.assistant._run_in_background", new_callable=AsyncMock):
        resp = await client.post(f"/v1/threads/{thread_id}/runs", json={
            "assistant_id": assistant_id,
        })
    assert resp.status_code == 200
    run_id = resp.json()["id"]

    # Poll run
    resp = await client.get(f"/v1/threads/{thread_id}/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == run_id

    # List runs
    resp = await client.get(f"/v1/threads/{thread_id}/runs")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1

    # List steps (initially empty since bg task was mocked)
    resp = await client.get(f"/v1/threads/{thread_id}/runs/{run_id}/steps")
    assert resp.status_code == 200
    assert resp.json()["object"] == "list"

    # Delete assistant
    resp = await client.delete(f"/v1/assistants/{assistant_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    # Delete thread
    resp = await client.delete(f"/v1/threads/{thread_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
