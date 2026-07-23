"""Built-in MCP tools for MoA Gateway.

9 tools:
  - moa_list_models       : List models from real model pool
  - moa_check_quota      : Check usage stats from real storage
  - moa_route_preview    : Preview routing tier inference
  - discover_free_models : Trigger free model discovery engine
  - list_free_models     : List auto-discovered free model endpoints
  - apply_prompt_template: Load and render prompt templates
  - apply_param_template : Get recommended params for task type
  - run_agent_loop       : Start agent loop (requires LLM callback)
  - search_web           : Web search via agent skill
"""
from __future__ import annotations

import logging

from .protocol import ToolDefinition
from .registry import ToolRegistry

logger = logging.getLogger(__name__)


def register_builtin_tools(registry: ToolRegistry) -> None:
    """Register all 9 built-in tools with appropriate role restrictions."""

    # 1. moa_list_models — available to all authenticated users
    registry.register(
        tool=ToolDefinition(
            name="moa_list_models",
            description="List all available LLM models and their status from the model pool.",
            inputSchema={
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "description": "Filter by provider name (optional).",
                    }
                },
            },
        ),
        handler=_handle_list_models,
        allowed_roles={"admin", "operator", "user"},
    )

    # 2. moa_check_quota — available to user+
    registry.register(
        tool=ToolDefinition(
            name="moa_check_quota",
            description="Check API usage stats, endpoint counts, and key status from storage.",
            inputSchema={
                "type": "object",
                "properties": {
                    "api_key": {
                        "type": "string",
                        "description": "API key to check (defaults to current user).",
                    }
                },
            },
        ),
        handler=_handle_check_quota,
        allowed_roles={"admin", "operator", "user"},
    )

    # 3. moa_route_preview — operator+ only
    registry.register(
        tool=ToolDefinition(
            name="moa_route_preview",
            description="Preview routing decision: infer model tier from a prompt without executing.",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The prompt text to analyze for tier inference.",
                    }
                },
                "required": ["prompt"],
            },
        ),
        handler=_handle_route_preview,
        allowed_roles={"admin", "operator"},
    )

    # 4. discover_free_models — operator+ (triggers network calls)
    registry.register(
        tool=ToolDefinition(
            name="discover_free_models",
            description="Trigger free model discovery across all registered platforms.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        handler=_handle_discover_free_models,
        allowed_roles={"admin", "operator"},
    )

    # 5. list_free_models — available to user+
    registry.register(
        tool=ToolDefinition(
            name="list_free_models",
            description="List auto-discovered free model endpoints from the model pool.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        handler=_handle_list_free_models,
        allowed_roles={"admin", "operator", "user"},
    )

    # 6. apply_prompt_template — available to user+
    registry.register(
        tool=ToolDefinition(
            name="apply_prompt_template",
            description="Load and render a prompt template with variables.",
            inputSchema={
                "type": "object",
                "properties": {
                    "template_id": {
                        "type": "string",
                        "description": "The template name/ID to load.",
                    },
                    "variables": {
                        "type": "object",
                        "description": "Variables to substitute in the template.",
                    },
                },
                "required": ["template_id"],
            },
        ),
        handler=_handle_apply_prompt_template,
        allowed_roles={"admin", "operator", "user"},
    )

    # 7. apply_param_template — available to user+
    registry.register(
        tool=ToolDefinition(
            name="apply_param_template",
            description="Get recommended generation parameters for a task type.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_type": {
                        "type": "string",
                        "description": "Task type: chat, code, creative, analysis, etc.",
                    }
                },
                "required": ["task_type"],
            },
        ),
        handler=_handle_apply_param_template,
        allowed_roles={"admin", "operator", "user"},
    )

    # 8. run_agent_loop — operator+ (executes agent logic)
    registry.register(
        tool=ToolDefinition(
            name="run_agent_loop",
            description="Start an agent loop (react/plan_execute). Requires LLM callback.",
            inputSchema={
                "type": "object",
                "properties": {
                    "messages": {
                        "type": "array",
                        "description": "Initial messages for the agent.",
                        "items": {"type": "object"},
                    },
                    "loop_name": {
                        "type": "string",
                        "description": "Loop name: react or plan_execute.",
                        "default": "react",
                    },
                    "max_iterations": {
                        "type": "integer",
                        "description": "Maximum iterations.",
                        "default": 10,
                    },
                },
            },
        ),
        handler=_handle_run_agent_loop,
        allowed_roles={"admin", "operator"},
    )

    # 9. search_web — available to user+
    registry.register(
        tool=ToolDefinition(
            name="search_web",
            description="Search the web and return formatted results.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query string.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results.",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        handler=_handle_search_web,
        allowed_roles={"admin", "operator", "user"},
    )


# ===================== Tool Handlers =====================


async def _handle_list_models(args: dict) -> dict:
    """List available models from the real model pool."""
    provider = args.get("provider", "")
    try:
        from ..model_pool import get_model_pool

        pool = get_model_pool()
        snap = pool.snapshot()
        endpoints = snap.get("endpoints", [])
        if provider:
            endpoints = [ep for ep in endpoints if ep.get("provider") == provider]
        models = [
            {
                "id": ep.get("id", ""),
                "model": ep.get("model", ""),
                "provider": ep.get("provider", ""),
                "tier": ep.get("tier", ""),
                "enabled": ep.get("enabled", False),
            }
            for ep in endpoints
        ]
        return {"models": models, "total": len(models)}
    except Exception as e:
        logger.warning("moa_list_models failed: %s", e)
        return {"models": [], "total": 0}


async def _handle_check_quota(args: dict) -> dict:
    """Check API usage quota and system stats from real storage."""
    try:
        from ..storage import get_storage

        storage = get_storage()
        endpoints = storage.list_endpoints()
        api_keys = storage.list_api_keys()
        recent_logs = storage.list_logs(limit=100)

        # Try to get pool stats
        pool_calls = 0
        try:
            from ..model_pool import get_model_pool

            pool = get_model_pool()
            snap = pool.snapshot()
            pool_calls = sum(ep.get("total_calls", 0) for ep in snap.get("endpoints", []))
        except Exception:
            pass

        return {
            "quota": {
                "total_endpoints": len(endpoints),
                "enabled_endpoints": sum(1 for e in endpoints if e.get("enabled")),
                "total_api_keys": len(api_keys),
                "active_api_keys": sum(1 for k in api_keys if k.get("enabled")),
                "total_requests": len(recent_logs) + pool_calls,
                "recent_log_count": len(recent_logs),
                "pool_total_calls": pool_calls,
            }
        }
    except Exception as e:
        logger.warning("moa_check_quota failed: %s", e)
        return {"quota": {"error": str(e)}, "total_requests": 0}


async def _handle_route_preview(args: dict) -> dict:
    """Preview routing decision: infer model tier from a prompt."""
    prompt = args.get("prompt", "")
    try:
        from ..discovery.discovery_engine import infer_tier

        tier = infer_tier(prompt, 0)
        return {"prompt": prompt[:100], "recommended_tier": tier}
    except Exception as e:
        logger.warning("moa_route_preview failed: %s", e)
        return {"prompt": prompt[:100], "recommended_tier": "standard", "error": str(e)}


async def _handle_discover_free_models(args: dict) -> dict:
    """Trigger free model discovery across all registered platforms."""
    try:
        from ..discovery.discovery_engine import FreeModelDiscoveryEngine, infer_tier

        engine = FreeModelDiscoveryEngine()
        models = await engine.discover_all()
        platform_ids = {m.platform_id for m in models}
        return {
            "discovered": len(models),
            "platforms": len(platform_ids),
            "models": [
                {
                    "platform": m.platform_id,
                    "model_id": m.model_id,
                    "display_name": m.display_name,
                    "tier": infer_tier(m.model_id, m.context_window),
                    "context_window": m.context_window,
                }
                for m in models[:20]
            ],
        }
    except Exception as e:
        logger.warning("discover_free_models failed: %s", e)
        return {"discovered": 0, "platforms": 0, "models": [], "error": str(e)}


async def _handle_list_free_models(args: dict) -> dict:
    """List auto-discovered free model endpoints from the model pool."""
    try:
        from ..model_pool import get_model_pool

        pool = get_model_pool()
        free = []
        for _ep_id, ep in pool.endpoints.items():
            tags = getattr(ep.config, "tags", []) or []
            is_auto = "auto-discovered" in tags
            is_free_tier = ep.tier.value == "free"
            if is_auto or is_free_tier:
                free.append(
                    {
                        "id": ep.id,
                        "provider": ep.config.provider,
                        "model": ep.config.model,
                        "tier": ep.tier.value,
                        "enabled": ep.config.enabled,
                        "tags": tags,
                        "auto_discovered": is_auto,
                    }
                )
        return {"free_models": free, "total": len(free)}
    except Exception as e:
        logger.warning("list_free_models failed: %s", e)
        return {"free_models": [], "total": 0}


async def _handle_apply_prompt_template(args: dict) -> dict:
    """Load and render a prompt template with variables."""
    template_id = args.get("template_id", "")
    variables = args.get("variables", {})
    try:
        from ..prompts import get_prompt

        content = get_prompt(template_id, **variables)
        return {"template_id": template_id, "rendered": content}
    except Exception as e:
        logger.warning("apply_prompt_template failed: %s", e)
        return {"template_id": template_id, "rendered": "", "error": str(e)}


async def _handle_apply_param_template(args: dict) -> dict:
    """Get recommended generation parameters for a task type."""
    task_type = args.get("task_type", "chat")
    try:
        from ..param_templates.manager import ParamTemplateManager

        mgr = ParamTemplateManager()
        template = mgr.get_template(task_type)
        return {"task_type": task_type, "params": template}
    except ImportError:
        # Fallback: return sensible defaults
        defaults = {
            "chat": {"temperature": 0.7, "max_tokens": 2048, "top_p": 1.0},
            "code": {"temperature": 0.2, "max_tokens": 4096, "top_p": 0.95},
            "creative": {"temperature": 0.9, "max_tokens": 2048, "top_p": 1.0},
            "analysis": {"temperature": 0.3, "max_tokens": 4096, "top_p": 0.9},
            "summarize": {"temperature": 0.3, "max_tokens": 1024, "top_p": 0.9},
        }
        return {
            "task_type": task_type,
            "params": defaults.get(task_type, defaults["chat"]),
            "source": "fallback_defaults",
        }
    except Exception as e:
        logger.warning("apply_param_template failed: %s", e)
        return {"task_type": task_type, "params": {}, "error": str(e)}


async def _handle_run_agent_loop(args: dict) -> dict:
    """Start an agent loop (requires LLM callback)."""
    messages = args.get("messages", [])
    loop_name = args.get("loop_name", "react")
    max_iterations = args.get("max_iterations", 10)
    try:
        from ..agent_loop.harness import AgentHarness

        # AgentHarness requires an llm_call callback to register loops.
        # Without one, loops are not registered, so we return a status.
        harness = AgentHarness()
        available_loops = harness.list_loops()
        if available_loops:
            return {
                "status": "ready",
                "loop_name": loop_name,
                "max_iterations": max_iterations,
                "message_count": len(messages),
                "available_loops": available_loops,
            }
        return {
            "status": "agent_loop_requires_llm_callback",
            "loop_name": loop_name,
            "max_iterations": max_iterations,
            "message_count": len(messages),
            "available_loops": [],
        }
    except Exception as e:
        logger.warning("run_agent_loop failed: %s", e)
        return {
            "status": "error",
            "loop_name": loop_name,
            "max_iterations": max_iterations,
            "message_count": len(messages),
            "error": str(e),
        }


async def _handle_search_web(args: dict) -> dict:
    """Search the web and return formatted results."""
    query = args.get("query", "")
    max_results = args.get("max_results", 5)
    try:
        from ..agent_loop.skills.web_search import web_search

        result = await web_search(query, max_results=max_results)
        return {"query": query, "results": result}
    except Exception as e:
        logger.warning("search_web failed: %s", e)
        return {"query": query, "results": "", "error": str(e)}
