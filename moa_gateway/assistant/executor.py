"""Run executor — processes runs by calling LLM and handling tool calls."""
from __future__ import annotations

import logging
import time
from typing import Any

from .models import Run, RunStep, Message
from .storage import get_storage

logger = logging.getLogger(__name__)


async def execute_run(run: Run) -> Run:
    """Execute a run: call LLM, handle tool calls, produce assistant message."""
    storage = get_storage()

    # Update status to in_progress
    run.status = "in_progress"
    run.started_at = int(time.time())
    storage.save_run(run)

    try:
        # Get assistant info
        assistant = storage.get_assistant(run.assistant_id)
        if not assistant:
            raise ValueError(f"Assistant {run.assistant_id} not found")

        # Get thread messages
        messages = storage.list_messages(run.thread_id, order="asc")

        # Build LLM request
        llm_messages: list[dict[str, Any]] = []
        if assistant.instructions or run.instructions:
            llm_messages.append({
                "role": "system",
                "content": run.instructions or assistant.instructions or "",
            })

        for msg in messages:
            content_text = ""
            for part in msg.content:
                if part.get("type") == "text":
                    content_text += part.get("text", {}).get("value", "")
            llm_messages.append({"role": msg.role, "content": content_text})

        # Determine tools
        tools_for_llm: list[dict[str, Any]] = []
        for tool in (run.tools or assistant.tools):
            if tool.get("type") == "function":
                tools_for_llm.append(tool)

        # Call LLM
        model = run.model or assistant.model
        response = await _call_llm(model, llm_messages, tools_for_llm, assistant.temperature)

        # Process response
        choice = response.get("choices", [{}])[0]
        message_data = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "stop")

        # Handle tool calls
        if finish_reason == "tool_calls" or message_data.get("tool_calls"):
            tool_calls = message_data.get("tool_calls", [])

            # Create run step for tool calls
            step = RunStep(
                run_id=run.id,
                thread_id=run.thread_id,
                type="tool_calls",
                status="in_progress",
                step_details={"type": "tool_calls", "tool_calls": tool_calls},
            )
            storage.save_step(step)

            # Set run to requires_action
            run.status = "requires_action"
            run.required_action = {
                "type": "submit_tool_outputs",
                "submit_tool_outputs": {
                    "tool_calls": tool_calls,
                },
            }
            storage.save_run(run)
            return run

        # Normal completion — create assistant message
        content_text = message_data.get("content", "")
        assistant_msg = Message(
            thread_id=run.thread_id,
            role="assistant",
            content=[{"type": "text", "text": {"value": content_text}}],
            assistant_id=run.assistant_id,
            run_id=run.id,
        )
        storage.save_message(assistant_msg)

        # Create run step for message creation
        step = RunStep(
            run_id=run.id,
            thread_id=run.thread_id,
            type="message_creation",
            status="completed",
            step_details={"type": "message_creation", "message_creation": {"message_id": assistant_msg.id}},
            completed_at=int(time.time()),
            usage=response.get("usage"),
        )
        storage.save_step(step)

        # Mark run as completed
        run.status = "completed"
        run.completed_at = int(time.time())
        run.usage = response.get("usage")
        storage.save_run(run)

        return run

    except Exception as e:
        logger.error("Run execution failed: %s", e)
        run.status = "failed"
        run.failed_at = int(time.time())
        run.last_error = {"code": "server_error", "message": str(e)}
        storage.save_run(run)
        return run


async def submit_tool_outputs(run: Run, tool_outputs: list[dict[str, Any]]) -> Run:
    """Submit tool outputs and continue the run."""
    storage = get_storage()

    if run.status != "requires_action":
        raise ValueError(f"Run is not in requires_action state: {run.status}")

    # Get original messages + tool call context
    assistant = storage.get_assistant(run.assistant_id)
    messages = storage.list_messages(run.thread_id, order="asc")

    # Build messages including tool results
    llm_messages: list[dict[str, Any]] = []
    if assistant and (assistant.instructions or run.instructions):
        llm_messages.append({"role": "system", "content": run.instructions or assistant.instructions or ""})

    for msg in messages:
        content_text = ""
        for part in msg.content:
            if part.get("type") == "text":
                content_text += part.get("text", {}).get("value", "")
        llm_messages.append({"role": msg.role, "content": content_text})

    # Add tool outputs as tool role messages
    for output in tool_outputs:
        llm_messages.append({
            "role": "tool",
            "tool_call_id": output.get("tool_call_id", ""),
            "content": output.get("output", ""),
        })

    # Clear required_action and continue
    run.status = "in_progress"
    run.required_action = None
    storage.save_run(run)

    # Re-call LLM with tool results
    try:
        model = run.model or (assistant.model if assistant else "gpt-4o")
        temperature = assistant.temperature if assistant else 1.0
        response = await _call_llm(model, llm_messages, [], temperature)

        choice = response.get("choices", [{}])[0]
        content_text = choice.get("message", {}).get("content", "")

        # Create assistant message
        assistant_msg = Message(
            thread_id=run.thread_id,
            role="assistant",
            content=[{"type": "text", "text": {"value": content_text}}],
            assistant_id=run.assistant_id,
            run_id=run.id,
        )
        storage.save_message(assistant_msg)

        # Complete run
        run.status = "completed"
        run.completed_at = int(time.time())
        run.usage = response.get("usage")
        storage.save_run(run)

        return run

    except Exception as e:
        run.status = "failed"
        run.failed_at = int(time.time())
        run.last_error = {"code": "server_error", "message": str(e)}
        storage.save_run(run)
        return run


async def _call_llm(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    temperature: float,
) -> dict[str, Any]:
    """Call LLM via internal gateway."""
    import httpx
    import os

    gateway_url = os.environ.get("MOA_GATEWAY_URL", "http://localhost:8910")
    gateway_key = os.environ.get("MOA_GATEWAY_KEY", "")

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools

    headers: dict[str, str] = {}
    if gateway_key:
        headers["Authorization"] = f"Bearer {gateway_key}"

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{gateway_url}/v1/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()
