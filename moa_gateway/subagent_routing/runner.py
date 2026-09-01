"""Production subagent runner: executes lite subagent tasks through ModelPool.

Closes the integration seam flagged in blind review alpha (F-1): without a
registered runner, ``invoke_lite_subagent`` could only report a dry-run
decision. The runner below reuses the same real pipeline as the skill hub
(``select_one`` + tier descent + ``pool.call``), so a forked subagent task is
served by the gateway's actual model endpoints, including the documented
credential-less provider semantics when no production key is configured.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_DEFAULT_TIER = "lite"


async def run_subagent_task(task: str, decision) -> str:
    """Execute a forked subagent task and return the model's answer text.

    ``decision`` is a :class:`~moa_gateway.subagent_routing.SubagentRouteDecision`;
    its ``model`` hint is logged but endpoint selection follows the lite tier
    with the standard tier-descent fallback used across the gateway.
    """
    from ..model_pool import ModelTier, get_model_pool

    tier = ModelTier(_DEFAULT_TIER)
    pool = get_model_pool()
    ep = pool.select_one(tier)
    while ep is None and tier.rank > 0:
        tier = tier.previous()
        ep = pool.select_one(tier)
    if ep is None:
        raise RuntimeError(
            "no model endpoint available for lite subagent execution "
            "(configure at least one endpoint in config.yaml)"
        )

    started = time.perf_counter()
    resp = await pool.call(
        ep.id,
        [{"role": "user", "content": task}],
        max_tokens=2048,
    )
    duration_ms = (time.perf_counter() - started) * 1000.0

    text = getattr(resp, "content", None)
    if not isinstance(text, str):
        text = str(resp)

    logger.info(
        "subagent runner executed on endpoint %s (hint=%s, source=%s, "
        "%.0fms, %d chars)",
        ep.id,
        decision.model,
        decision.model_source,
        duration_ms,
        len(text),
    )
    return text
