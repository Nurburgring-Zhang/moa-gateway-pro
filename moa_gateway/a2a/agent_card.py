"""moa_gateway.a2a.agent_card — A2A Agent Card (GET /.well-known/agent.json).

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT license):
  - source: src/app/.well-known/agent.json/route.ts
    (dynamically generated Agent Card, A2A protocol v0.3 conformance,
    capabilities block, skills list with id/name/description/tags/examples,
    authentication schemes, Cache-Control header).

Divergences (all card fields come from THIS gateway's runtime, per M5 plan):
  - name/description/version come from ``settings.a2a`` (A2AConfig).
  - the skills section is generated from the live SKILL_REGISTRY.
  - the ``x-gateway`` extension carries real runtime state: package version,
    capability-toggle table and model-pool snapshot (sanitized — credentials
    never leave the gateway, see skills.sanitize_outbound).
"""

from __future__ import annotations

import logging
from typing import Any

from .skills import SKILL_REGISTRY, sanitize_outbound

logger = logging.getLogger(__name__)

# A2A protocol revision this card conforms to (OmniRoute: v0.3).
PROTOCOL_VERSION = "0.3.0"


def build_agent_card(base_url: str = "") -> dict[str, Any]:
    """Build the Agent Card from live gateway runtime state."""
    from .. import __version__ as gateway_version
    from ..capability_toggles import get_all as get_toggles
    from ..config import get_settings
    from ..model_pool import get_model_pool

    settings = get_settings()
    a2a_cfg = settings.a2a
    base_url = (base_url or "").rstrip("/")

    try:
        pool_snapshot = sanitize_outbound(get_model_pool().snapshot())
    except Exception as e:  # card must stay valid even if the pool is down
        logger.warning("agent card: model pool snapshot unavailable: %s", e)
        pool_snapshot = {"error": "model pool unavailable"}

    toggles = {t["name"]: t["enabled"] for t in get_toggles()}

    return {
        # ---- A2A v0.3 Agent Card core fields ----
        "name": a2a_cfg.agent_name,
        "description": a2a_cfg.agent_description,
        "url": f"{base_url}/v1/a2a" if base_url else "/v1/a2a",
        "version": a2a_cfg.agent_version,
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {
            # message/stream (SSE) is not implemented on this port: the five
            # skills are synchronous internal calls.
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": [spec.to_card() for spec in SKILL_REGISTRY.values()],
        "authentication": {
            # POST /v1/a2a requires the gateway API key (Bearer or raw) —
            # same credential as /v1/chat/completions. The card itself is
            # public by A2A discovery semantics.
            "schemes": ["bearer"],
            "apiKeyHeader": "Authorization",
        },
        "provider": {
            "organization": "moa-gateway-pro",
            "url": "https://github.com/diegosouzapw/OmniRoute",  # A2A design lineage (MIT)
        },
        # ---- runtime extension: real gateway state ----
        "x-gateway": {
            "gateway_version": gateway_version,
            "agent_enabled": bool(a2a_cfg.enabled),
            "capability_toggles": toggles,
            "model_pool": pool_snapshot,
            "attribution": "A2A layer ported from OmniRoute (MIT)",
        },
    }
