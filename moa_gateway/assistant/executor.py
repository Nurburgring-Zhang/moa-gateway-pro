"""Run executor — processes runs by calling LLM and handling tool calls."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .models import Message, Run, RunStep
from .storage import get_storage

logger = logging.getLogger(__name__)

# D12: in-process guard against the same run being executed twice at once
# (e.g. two racing submit_tool_outputs requests that both observed
# requires_action before either background task started).
_active_run_ids: set[str] = set()


# ---------------------------------------------------------------------------
# v4.1.0 (MemoraX Code port): cross-session memory integration.
# Both hooks are opt-in (settings.memory.retrieval_enabled / writeback_enabled)
# and never raise into the run path — the memory service APIs swallow their own
# errors by contract, and the guards below double-protect the executor.
# ---------------------------------------------------------------------------


def _memory_scope_from_run(run: Run) -> tuple[str, str]:
    """Resolve the MemoraX scope pair (base_user_id, repository_slug).

    Caller identity comes from run metadata (``user_id`` / ``base_user_id``);
    absent that, the assistant itself is the stable identity. The repository
    slug follows ``repository_slug`` / ``repo_key`` metadata with a stable
    per-assistant fallback, mirroring MemoraX ``effectiveUserId = f(baseUser,
    repoKey)`` scope isolation.
    """
    meta = run.metadata or {}
    base_user = str(meta.get("user_id") or meta.get("base_user_id") or "").strip()
    if not base_user:
        base_user = f"assistant:{run.assistant_id}"
    repo = str(meta.get("repository_slug") or meta.get("repo_key") or "").strip()
    if not repo:
        repo = f"assistant-{run.assistant_id}"
    return base_user, repo


def _latest_user_text(messages) -> str:
    """Return the newest non-empty user message text (recall query source)."""
    for msg in reversed(messages):
        if msg.role != "user":
            continue
        text = ""
        for part in msg.content:
            if part.get("type") == "text":
                text += part.get("text", {}).get("value", "")
        if text.strip():
            return text
    return ""


def _memory_recall_context(run: Run, messages) -> str | None:
    try:
        from ..config import get_settings

        if not get_settings().memory.retrieval_enabled:
            return None
        query = _latest_user_text(messages)
        if not query.strip():
            return None
        base_user, repo = _memory_scope_from_run(run)
        from ..memory import get_memory_service

        return get_memory_service().recall_for_turn(
            query, base_user_id=base_user, repository_slug=repo
        )
    except Exception as exc:  # noqa: BLE001 - memory must never break a run
        logger.warning("assistant memory recall skipped: %s", exc)
        return None


def _memory_writeback(run: Run, messages, reply_text: str) -> None:
    try:
        from ..config import get_settings

        if not get_settings().memory.writeback_enabled:
            return
        user_text = _latest_user_text(messages)
        if not (user_text.strip() and (reply_text or "").strip()):
            return
        base_user, repo = _memory_scope_from_run(run)
        from ..memory import get_memory_service

        get_memory_service().queue_writeback(
            base_user_id=base_user,
            repository_slug=repo,
            session_id=str(run.thread_id),
            messages=[
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": reply_text},
            ],
            correlation_id=str(run.id),
            client="assistant-runs",
        )
    except Exception as exc:  # noqa: BLE001 - memory must never break a run
        logger.warning("assistant memory writeback skipped: %s", exc)


def _acquire_run(run_id: str) -> bool:
    """Claim exclusive in-process execution rights for a run."""
    if run_id in _active_run_ids:
        return False
    _active_run_ids.add(run_id)
    return True


def _release_run(run_id: str) -> None:
    _active_run_ids.discard(run_id)


def _run_timeouts() -> tuple[float, float]:
    """(whole-run timeout, single LLM round-trip timeout) from settings."""
    try:
        from ..config import get_settings

        cfg = get_settings().assistant
        return float(cfg.run_timeout_seconds), float(cfg.llm_call_timeout_seconds)
    except Exception:  # noqa: BLE001 — executor must never crash on config
        return 300.0, 120.0


async def execute_run(run: Run) -> Run:
    """Execute a run: call LLM, handle tool calls, produce assistant message."""
    storage = get_storage()

    # D12: never execute the same run twice concurrently.
    if not _acquire_run(run.id):
        logger.warning("Run %s is already executing; skipping duplicate", run.id)
        return storage.get_run(run.id) or run

    run_timeout, _ = _run_timeouts()
    # T5.1: trace the whole run; child spans (_call_llm) nest under it when a
    # request trace context is present, otherwise a fresh trace is started.
    from ..observability.tracer import get_tracer

    with get_tracer().start_span("assistant.run", {"assistant.run.id": run.id}):
        try:
            try:
                return await asyncio.wait_for(_execute_run_inner(run), timeout=run_timeout)
            except asyncio.TimeoutError:
                logger.error("Run %s timed out after %.0fs", run.id, run_timeout)
                # Narrow race: the inner task may have landed a terminal state in
                # the same instant the timeout fired. Never overwrite it.
                on_disk = storage.get_run(run.id)
                if on_disk is not None and on_disk.status in (
                    "completed",
                    "requires_action",
                    "failed",
                    "cancelled",
                ):
                    return on_disk
                run.status = "failed"
                run.failed_at = int(time.time())
                run.last_error = {
                    "code": "timeout",
                    "message": f"run exceeded {run_timeout:.0f}s",
                }
                storage.save_run(run)
                return run
        finally:
            _release_run(run.id)


async def _execute_run_inner(run: Run) -> Run:
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

        # v4.1.0 (MemoraX Code): inject recalled cross-session memory context
        # (opt-in; None when disabled or nothing relevant was found).
        memory_ctx = _memory_recall_context(run, messages)
        if memory_ctx:
            llm_messages.append({"role": "system", "content": memory_ctx})

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

        # v4.1.0 (MemoraX Code): queue this turn for memory writeback (opt-in).
        _memory_writeback(run, messages, content_text)

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

    # The route flips requires_action -> in_progress and persists it *before*
    # scheduling this background task (so racing submits get a 400), therefore
    # both states are legitimate entry points here. Re-read from disk to make
    # the decision against persisted truth, not a possibly-stale object.
    fresh = storage.get_run(run.id)
    if fresh is not None:
        run = fresh
    if run.status not in ("requires_action", "in_progress"):
        raise ValueError(f"Run is not awaiting tool outputs: {run.status}")

    # D12: never execute the same run twice concurrently.
    if not _acquire_run(run.id):
        logger.warning("Run %s is already executing; skipping duplicate", run.id)
        return storage.get_run(run.id) or run

    run_timeout, _ = _run_timeouts()
    from ..observability.tracer import get_tracer

    with get_tracer().start_span("assistant.submit", {"assistant.run.id": run.id}):
        try:
            return await asyncio.wait_for(
                _submit_tool_outputs_inner(run, tool_outputs), timeout=run_timeout
            )
        except asyncio.TimeoutError:
            logger.error("Run %s timed out after %.0fs", run.id, run_timeout)
            # Same terminal-state race guard as execute_run above.
            on_disk = storage.get_run(run.id)
            if on_disk is not None and on_disk.status in (
                "completed",
                "requires_action",
                "failed",
                "cancelled",
            ):
                return on_disk
            run.status = "failed"
            run.failed_at = int(time.time())
            run.last_error = {
                "code": "timeout",
                "message": f"run exceeded {run_timeout:.0f}s",
            }
            storage.save_run(run)
            return run
        finally:
            _release_run(run.id)


async def _submit_tool_outputs_inner(run: Run, tool_outputs: list[dict[str, Any]]) -> Run:
    storage = get_storage()

    # Get original messages + tool call context
    assistant = storage.get_assistant(run.assistant_id)
    messages = storage.list_messages(run.thread_id, order="asc")

    # Build messages including tool results
    llm_messages: list[dict[str, Any]] = []
    if assistant and (assistant.instructions or run.instructions):
        llm_messages.append({"role": "system", "content": run.instructions or assistant.instructions or ""})

    # v4.1.0 (MemoraX Code): inject recalled cross-session memory context
    # (opt-in; None when disabled or nothing relevant was found).
    memory_ctx = _memory_recall_context(run, messages)
    if memory_ctx:
        llm_messages.append({"role": "system", "content": memory_ctx})

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

        # v4.1.0 (MemoraX Code): queue this turn for memory writeback (opt-in).
        _memory_writeback(run, messages, content_text)

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

    from ..internal_callback import internal_auth_headers, internal_gateway_url

    gateway_url = internal_gateway_url()
    _, llm_timeout = _run_timeouts()

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools

    # D12: internal callback must authenticate (env key -> settings fallback),
    # otherwise the loopback request 401s and the run fails.
    headers = internal_auth_headers()

    # trust_env=False: loopback calls must never be hijacked by HTTP(S)_PROXY
    # (aligned with yaml_workflow._http_post).
    from ..observability.tracer import get_tracer

    with get_tracer().start_span("assistant.llm_call", {"llm.model": model}):
        async with httpx.AsyncClient(timeout=llm_timeout, trust_env=False) as client:
            resp = await client.post(
                f"{gateway_url}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                # do not leak the internal gateway URL into run error messages
                raise RuntimeError(
                    f"internal gateway call failed: HTTP {e.response.status_code}"
                ) from None
            return resp.json()  # type: ignore[no-any-return]
