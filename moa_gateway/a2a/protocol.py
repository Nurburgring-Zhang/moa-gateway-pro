"""moa_gateway.a2a.protocol — JSON-RPC 2.0 dispatcher for POST /v1/a2a.

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT license):
  - source: src/app/a2a/route.ts (jsonrpc/id/method/params validation, error
    envelope {jsonrpc, id, error:{code, message, data}}, methods message/send,
    tasks/get, tasks/cancel, task lifecycle around skill execution)
  - source: src/lib/a2a/taskExecution.ts (executeA2ATaskWithState: working ->
    completed/failed with artifact persistence)

Divergences / extensions required by the M5 plan:
  - methods: skills/list, skills/invoke, message/send, tasks/get (+tasks/cancel)
  - full JSON-RPC 2.0 batch support (array requests -> array responses)
  - notifications (requests without ``id``) get no response object, per spec
  - every skill error message is passed through sanitize_outbound so no
    credential fragment can leak into a JSON-RPC error body (OmniRoute
    ERROR_SANITIZATION hard rule).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .skills import SKILL_REGISTRY, SkillExecutionError, sanitize_outbound
from .task_manager import (
    A2ATaskManager,
    InvalidTransitionError,
    TaskTransitionError,
    get_task_manager,
)

logger = logging.getLogger(__name__)

# ---- JSON-RPC 2.0 error codes (spec section 5.1) ----
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# HTTP status mapping for single (non-batch) responses. Transport-level
# client errors get 4xx, server faults 500, protocol-success 200.
_HTTP_STATUS: dict[int, int] = {
    PARSE_ERROR: 400,
    INVALID_REQUEST: 400,
    METHOD_NOT_FOUND: 404,
    INVALID_PARAMS: 400,
    INTERNAL_ERROR: 500,
}

DEFAULT_MESSAGE_SKILL = "chat-completion"


def _error_obj(
    req_id: Any, code: int, message: str, data: Any | None = None
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _result_obj(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


# ============ Message normalization (OmniRoute toMessageArray port) ============


def normalize_messages(raw: Any) -> list[dict[str, str]] | None:
    """Accept messages[] | message{content|parts[]} | str; return [{role, content}]."""
    if isinstance(raw, str):
        text = raw.strip()
        return [{"role": "user", "content": text}] if text else None

    if isinstance(raw, list):
        out: list[dict[str, str]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            role = entry.get("role")
            content = entry.get("content")
            if isinstance(role, str) and role.strip() and isinstance(content, str) and content:
                out.append({"role": role.strip(), "content": content})
        return out or None

    if isinstance(raw, dict):
        role = raw.get("role") if isinstance(raw.get("role"), str) and raw.get("role", "").strip() else "user"
        content = raw.get("content")
        if isinstance(content, str) and content.strip():
            return [{"role": role, "content": content}]
        parts = raw.get("parts")
        if isinstance(parts, list):
            chunks: list[str] = []
            for part in parts:
                if isinstance(part, str):
                    chunks.append(part)
                elif isinstance(part, dict):
                    text = part.get("text") or part.get("content")
                    if isinstance(text, str) and text.strip():
                        chunks.append(text)
            text = "\n".join(c for c in chunks if c.strip())
            if text:
                return [{"role": role, "content": text}]
    return None


# ============ Skill execution with task lifecycle ============


async def _run_skill(
    manager: A2ATaskManager,
    skill_id: str,
    messages: list[dict[str, str]],
    metadata: dict[str, Any],
    owner: str | None,
) -> dict[str, Any]:
    """Create task -> working -> handler -> completed/failed (persisted)."""
    spec = SKILL_REGISTRY[skill_id]
    task = manager.create_task(skill_id, messages, metadata=metadata, owner=owner)
    manager.update_task(task.id, "working")
    try:
        result = await spec.handler(task)
    except SkillExecutionError as e:
        msg = str(sanitize_outbound(str(e)))
        manager.update_task(
            task.id, "failed", artifacts=[{"type": "error", "content": msg}], message=msg
        )
        return {
            "error": {
                "code": INTERNAL_ERROR,
                "message": f"Skill execution failed: {msg}",
                "data": {"task_id": task.id, "skill": skill_id},
            }
        }
    except Exception as e:
        logger.exception("A2A skill %s crashed", skill_id)
        msg = f"internal error in skill '{skill_id}'"
        manager.update_task(
            task.id, "failed", artifacts=[{"type": "error", "content": msg}], message=str(e)[:500]
        )
        return {
            "error": {
                "code": INTERNAL_ERROR,
                "message": f"Skill execution failed: {msg}",
                "data": {"task_id": task.id, "skill": skill_id},
            }
        }
    artifacts = sanitize_outbound(result.get("artifacts") or [])
    metadata_out = sanitize_outbound(result.get("metadata") or {})
    manager.update_task(task.id, "completed", artifacts=artifacts)
    return {
        "result": {
            "task": {"id": task.id, "state": "completed", "skill": skill_id},
            "artifacts": artifacts,
            "metadata": metadata_out,
        }
    }


# ============ Method handlers ============


async def _method_skills_list(
    manager: A2ATaskManager, params: Any, owner: str | None
) -> dict[str, Any]:
    return {"result": {"skills": [s.to_card() for s in SKILL_REGISTRY.values()]}}


async def _method_skills_invoke(
    manager: A2ATaskManager, params: Any, owner: str | None
) -> dict[str, Any]:
    if not isinstance(params, dict):
        return {"error": {"code": INVALID_PARAMS, "message": "Invalid params: object required"}}
    skill_id = params.get("skill") or params.get("name")
    if not isinstance(skill_id, str) or skill_id not in SKILL_REGISTRY:
        return {
            "error": {
                "code": METHOD_NOT_FOUND,
                "message": f"Unknown skill: {skill_id!r}",
                "data": {"available": sorted(SKILL_REGISTRY)},
            }
        }
    metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
    arguments = params.get("arguments")
    if isinstance(arguments, dict):
        metadata = {**metadata, "arguments": arguments}
    source = params.get("messages") or params.get("message") or params.get("input")
    if source is None and isinstance(arguments, dict):
        source = arguments.get("query")
    messages = normalize_messages(source)
    if messages is None:
        return {
            "error": {
                "code": INVALID_PARAMS,
                "message": "Invalid params: provide messages[], message.content or arguments.query",
            }
        }
    return await _run_skill(manager, skill_id, messages, metadata, owner)


async def _method_message_send(
    manager: A2ATaskManager, params: Any, owner: str | None
) -> dict[str, Any]:
    if not isinstance(params, dict):
        return {"error": {"code": INVALID_PARAMS, "message": "Invalid params: object required"}}
    messages = normalize_messages(params.get("message") or params.get("messages"))
    if messages is None:
        return {
            "error": {
                "code": INVALID_PARAMS,
                "message": "Invalid params: provide message.content or message.parts[]",
            }
        }
    metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
    skill_id = metadata.get("skill") if isinstance(metadata.get("skill"), str) else None
    if skill_id and skill_id not in SKILL_REGISTRY:
        return {
            "error": {
                "code": METHOD_NOT_FOUND,
                "message": f"Unknown skill: {skill_id!r}",
                "data": {"available": sorted(SKILL_REGISTRY)},
            }
        }
    skill_id = skill_id or DEFAULT_MESSAGE_SKILL
    return await _run_skill(manager, skill_id, messages, metadata, owner)


async def _method_tasks_get(
    manager: A2ATaskManager, params: Any, owner: str | None
) -> dict[str, Any]:
    if not isinstance(params, dict):
        return {"error": {"code": INVALID_PARAMS, "message": "Invalid params: object required"}}
    task_id = params.get("id") or params.get("taskId")
    if not isinstance(task_id, str) or not task_id:
        return {"error": {"code": INVALID_PARAMS, "message": "Invalid params: id required"}}
    task = manager.get_task(task_id, owner)
    if task is None:
        return {"error": {"code": METHOD_NOT_FOUND, "message": f"Task not found: {task_id}"}}
    return {"result": {"task": sanitize_outbound(task.to_dict())}}


async def _method_tasks_cancel(
    manager: A2ATaskManager, params: Any, owner: str | None
) -> dict[str, Any]:
    if not isinstance(params, dict):
        return {"error": {"code": INVALID_PARAMS, "message": "Invalid params: object required"}}
    task_id = params.get("id") or params.get("taskId")
    if not isinstance(task_id, str) or not task_id:
        return {"error": {"code": INVALID_PARAMS, "message": "Invalid params: id required"}}
    try:
        task = manager.cancel_task(task_id, owner)
    except InvalidTransitionError as e:
        # OmniRoute precedent: a cancel refused by the state machine is an
        # internal/semantic failure, not a missing method or bad params.
        return {"error": {"code": INTERNAL_ERROR, "message": str(e)}}
    except TaskTransitionError as e:
        return {"error": {"code": METHOD_NOT_FOUND, "message": str(e)}}
    except Exception as e:
        logger.warning("tasks/cancel failed for %s: %s", task_id, e)
        return {"error": {"code": INTERNAL_ERROR, "message": f"cancel failed: {e}"}}
    return {"result": {"task": {"id": task.id, "state": task.state}}}


_METHODS = {
    "skills/list": _method_skills_list,
    "skills/invoke": _method_skills_invoke,
    "message/send": _method_message_send,
    "tasks/get": _method_tasks_get,
    "tasks/cancel": _method_tasks_cancel,
}


# ============ Single-request validation + dispatch ============


async def _dispatch_single(
    body: Any, manager: A2ATaskManager, owner: str | None
) -> dict[str, Any] | None:
    """Dispatch one JSON-RPC request object; None = notification (no reply)."""
    if not isinstance(body, dict):
        return _error_obj(None, INVALID_REQUEST, "Invalid request: not an object")
    req_id = body.get("id")
    if body.get("jsonrpc") != "2.0":
        return _error_obj(req_id, INVALID_REQUEST, "Invalid request: jsonrpc must be '2.0'")
    method = body.get("method")
    if not isinstance(method, str) or not method:
        return _error_obj(req_id, INVALID_REQUEST, "Invalid request: missing method")

    handler = _METHODS.get(method)
    if handler is None:
        if req_id is None:  # unknown-method notification: silently drop per spec
            return None
        return _error_obj(req_id, METHOD_NOT_FOUND, f"Method not found: {method}")

    is_notification = "id" not in body
    try:
        outcome = await handler(manager, body.get("params"), owner)
    except Exception:
        logger.exception("A2A method %s crashed", method)
        if is_notification:
            return None
        return _error_obj(req_id, INTERNAL_ERROR, "Internal error")
    if is_notification:
        return None
    if "error" in outcome:
        err = outcome["error"]
        return _error_obj(req_id, err["code"], err["message"], err.get("data"))
    return _result_obj(req_id, outcome["result"])


# ============ Public entry point ============


async def handle_raw_body(
    raw: bytes, owner: str | None = None, manager: A2ATaskManager | None = None
) -> tuple[Any, int]:
    """Parse + dispatch a raw JSON-RPC body.

    Returns ``(payload, http_status)``; payload is None for a lone
    notification (caller should answer 204 No Content).
    """
    manager = manager or get_task_manager()
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error_obj(None, PARSE_ERROR, "Parse error: invalid JSON"), 400

    if isinstance(body, list):
        if not body:
            return _error_obj(None, INVALID_REQUEST, "Invalid request: empty batch"), 400
        responses: list[dict[str, Any]] = []
        for item in body:
            resp = await _dispatch_single(item, manager, owner)
            if resp is not None:
                responses.append(resp)
        if not responses:  # batch of pure notifications
            return None, 204
        return responses, 200

    resp = await _dispatch_single(body, manager, owner)
    if resp is None:
        return None, 204
    status = 200
    if "error" in resp:
        status = _HTTP_STATUS.get(resp["error"]["code"], 200)
    return resp, status
