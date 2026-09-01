"""Lite-model pairing registry for subagent routing.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License).
Source: ``lib/clacky/providers.rb`` — the per-provider ``lite_models`` tables,
the legacy global ``lite_model`` field, and the ``Providers.lite_model``
resolution semantics (including the "unlisted primary with a non-empty table
is already lite-class -> None" rule).

The default tables below are verbatim ports of OpenClacky's preset data; the
registry additionally exposes a runtime registration API so the gateway can
pair its own endpoint models without touching preset data.
"""

from __future__ import annotations

import threading
from typing import Any

__all__ = [
    "DEFAULT_LITE_TABLES",
    "DEFAULT_GLOBAL_LITE",
    "LiteModelRegistry",
    "get_lite_registry",
]

# Per-primary lite pairing (port of each preset's "lite_models" hash).
# Keys are "strong" primary models, values are the lite sidekick to
# auto-inject for subagents. Weak models (haiku / flash / mini) ARE the lite
# tier themselves, so they are intentionally NOT listed as keys — no
# injection happens when the primary is already lite-class.
DEFAULT_LITE_TABLES: dict[str, dict[str, str]] = {
    "openclacky": {
        "abs-claude-fable-5": "abs-claude-haiku-4-5",
        "abs-claude-opus-5": "abs-claude-haiku-4-5",
        "abs-claude-opus-4-8": "abs-claude-haiku-4-5",
        "abs-claude-opus-4-7": "abs-claude-haiku-4-5",
        "abs-claude-opus-4-6": "abs-claude-haiku-4-5",
        "abs-claude-sonnet-5": "abs-claude-haiku-4-5",
        "abs-claude-sonnet-4-6": "abs-claude-haiku-4-5",
        "abs-claude-sonnet-4-5": "abs-claude-haiku-4-5",
        "dsk-deepseek-v4-pro": "dsk-deepseek-v4-flash",
        "or-gemini-3-1-pro": "or-gemini-3-6-flash",
    },
    "openrouter": {
        "anthropic/claude-sonnet-4-6": "anthropic/claude-haiku-4-5",
        "anthropic/claude-opus-4-8": "anthropic/claude-haiku-4-5",
        "anthropic/claude-opus-4-7": "anthropic/claude-haiku-4-5",
        "anthropic/claude-opus-4-6": "anthropic/claude-haiku-4-5",
        "openai/gpt-5.6-sol": "openai/gpt-5.6-luna",
        "openai/gpt-5.6-terra": "openai/gpt-5.6-luna",
        "openai/gpt-5.5": "openai/gpt-5.4-mini",
        "openai/gpt-5.4": "openai/gpt-5.4-mini",
    },
    "openai": {
        "gpt-5.6-sol": "gpt-5.6-luna",
        "gpt-5.6-terra": "gpt-5.6-luna",
        "gpt-5.5": "gpt-5.4-mini",
        "gpt-5.4": "gpt-5.4-mini",
    },
    "qwen": {
        "qwen3.7-max": "qwen3.6-flash",
        "qwen3.6-plus": "qwen3.6-flash",
        "qwen3.6-max": "qwen3.6-flash",
        "qwen3.6-27b": "qwen3.6-flash",
        "qwen-plus-latest": "qwen3.6-flash",
    },
    # OpenClacky's "custom" (Ollama-style) preset table.
    "custom": {
        "glm-5.2": "glm-5.1",
        "kimi-k3": "kimi-k2.6",
        "gemma4": "nemotron-3-nano",
        "qwen3.5": "gemini-3-flash-preview",
        "minimax-m3": "minimax-m2.7",
        "kimi-k2.7-code": "kimi-k2.6",
        "deepseek-v4-pro": "deepseek-v4-flash",
        "mistral-large-3": "nemotron-3-nano",
        "gpt-oss": "nemotron-3-nano",
        "gemini-3-flash-preview": "nemotron-3-nano",
    },
    "orcarouter": {
        "anthropic/claude-sonnet-5": "anthropic/claude-haiku-4.5",
        "anthropic/claude-opus-4.8": "anthropic/claude-haiku-4.5",
        "openai/gpt-5.5": "openai/gpt-5.4-mini",
        "openai/gpt-5.4": "openai/gpt-5.4-mini",
    },
}

# Old-style provider-wide lite field (port of preset["lite_model"]).
DEFAULT_GLOBAL_LITE: dict[str, str] = {
    "deepseek": "deepseek-v4-flash",
}


class LiteModelRegistry:
    """Thread-safe primary->lite pairing store with OpenClacky resolution
    semantics."""

    def __init__(
        self,
        lite_tables: dict[str, dict[str, str]] | None = None,
        global_lite: dict[str, str] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._tables: dict[str, dict[str, str]] = {
            provider: dict(pairs)
            for provider, pairs in (lite_tables or DEFAULT_LITE_TABLES).items()
        }
        self._global: dict[str, str] = dict(global_lite or DEFAULT_GLOBAL_LITE)

    # -- registration API (runtime pairing of gateway endpoint models) ----

    def register_provider(
        self,
        provider_id: str,
        lite_models: dict[str, str] | None = None,
        lite_model: str | None = None,
    ) -> None:
        """Register (or replace) one provider's pairing data."""
        with self._lock:
            if lite_models is not None:
                self._tables[provider_id] = dict(lite_models)
            if lite_model is not None:
                self._global[provider_id] = lite_model

    def register_pair(self, provider_id: str, primary_model: str, lite_model: str) -> None:
        """Add a single primary->lite pair (creates the table if needed)."""
        with self._lock:
            self._tables.setdefault(provider_id, {})[primary_model] = lite_model

    def unregister_pair(self, provider_id: str, primary_model: str) -> bool:
        with self._lock:
            table = self._tables.get(provider_id)
            if table and primary_model in table:
                del table[primary_model]
                return True
            return False

    # -- resolution (port of Providers.lite_model) ------------------------

    def resolve(self, provider_id: str, primary_model: str | None = None) -> str | None:
        """Return the lite model for a provider + primary, or None.

        Resolution order (verbatim port):
        1. the provider's ``lite_models`` table entry for *primary_model*;
        2. when a non-empty table exists but the primary is unlisted, the
           primary is already lite-class -> ``None`` (never fall through);
        3. the provider-wide legacy ``lite_model`` field.
        """
        with self._lock:
            table = self._tables.get(provider_id)
            if primary_model and isinstance(table, dict):
                mapped = table.get(primary_model)
                if mapped:
                    return mapped
                if table:
                    return None
            return self._global.get(provider_id)

    def table_for(self, provider_id: str) -> dict[str, str]:
        with self._lock:
            return dict(self._tables.get(provider_id, {}))

    def providers(self) -> list[str]:
        with self._lock:
            return sorted(set(self._tables) | set(self._global))

    def snapshot(self) -> dict[str, Any]:
        """Full read-only view (for GET /v1/subagent/config)."""
        with self._lock:
            return {
                "lite_tables": {p: dict(t) for p, t in sorted(self._tables.items())},
                "global_lite": dict(sorted(self._global.items())),
            }


_registry: LiteModelRegistry | None = None
_registry_lock = threading.Lock()


def get_lite_registry() -> LiteModelRegistry:
    """Process-wide registry with the OpenClacky default tables."""
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = LiteModelRegistry()
        return _registry
