"""Built-in MCP tools for MoA Gateway."""
from __future__ import annotations

from .protocol import ToolDefinition
from .registry import ToolRegistry


def register_builtin_tools(registry: ToolRegistry) -> None:
    """Register default built-in tools with appropriate role restrictions."""

    # --- moa_list_models: available to all authenticated users ---
    registry.register(
        tool=ToolDefinition(
            name="moa_list_models",
            description="List all available LLM models and their status.",
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

    # --- moa_check_quota: available to user+ ---
    registry.register(
        tool=ToolDefinition(
            name="moa_check_quota",
            description="Check API usage quota and remaining balance.",
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

    # --- moa_route_preview: operator+ only ---
    registry.register(
        tool=ToolDefinition(
            name="moa_route_preview",
            description="Preview routing decision for a given model request without executing.",
            inputSchema={
                "type": "object",
                "properties": {
                    "model": {
                        "type": "string",
                        "description": "Model name to route.",
                    },
                    "strategy": {
                        "type": "string",
                        "description": "Routing strategy (round_robin, weighted, latency).",
                        "enum": ["round_robin", "weighted", "latency"],
                    },
                },
                "required": ["model"],
            },
        ),
        handler=_handle_route_preview,
        allowed_roles={"admin", "operator"},
    )


# ---------- Tool Handlers ----------


async def _handle_list_models(provider: str = "") -> dict:
    """List available models (stub - connects to model pool in production)."""
    models = [
        {"id": "gpt-4o", "provider": "openai", "status": "active"},
        {"id": "claude-3.5-sonnet", "provider": "anthropic", "status": "active"},
        {"id": "deepseek-chat", "provider": "deepseek", "status": "active"},
        {"id": "qwen-max", "provider": "alibaba", "status": "active"},
    ]
    if provider:
        models = [m for m in models if m["provider"] == provider]
    return {"models": models, "total": len(models)}


async def _handle_check_quota(api_key: str = "") -> dict:
    """Check quota usage (stub - connects to rate limiter in production)."""
    return {
        "quota": {
            "total_requests": 10000,
            "used_requests": 3421,
            "remaining_requests": 6579,
            "reset_at": "2025-02-01T00:00:00Z",
        }
    }


async def _handle_route_preview(model: str, strategy: str = "round_robin") -> dict:
    """Preview routing decision (stub - connects to router in production)."""
    endpoints = {
        "gpt-4o": [
            {"url": "https://api.openai.com/v1", "weight": 0.7},
            {"url": "https://backup.openai-proxy.com/v1", "weight": 0.3},
        ],
        "claude-3.5-sonnet": [
            {"url": "https://api.anthropic.com/v1", "weight": 1.0},
        ],
    }
    matched = endpoints.get(model, [{"url": "https://fallback.example.com/v1", "weight": 1.0}])
    return {
        "model": model,
        "strategy": strategy,
        "selected_endpoint": matched[0]["url"] if matched else None,
        "candidates": matched,
    }
