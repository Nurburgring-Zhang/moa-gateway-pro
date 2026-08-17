"""Helpers for internal loopback HTTP calls back into the gateway itself.

Workflows (yaml_workflow) and the assistant executor call the gateway's own
``/v1/*`` endpoints over HTTP. Those endpoints require a gateway API key, so
internal callbacks must inject an ``Authorization`` header -- otherwise every
internal call fails with 401 and the chain breaks (D2/D12).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def internal_gateway_url() -> str:
    """Base URL for loopback calls into this gateway.

    Resolution order: ``MOA_GATEWAY_URL`` env override -> local loopback on
    ``settings.server.port`` -> default 8910.
    """
    url = os.environ.get("MOA_GATEWAY_URL", "").strip()
    if url:
        return url.rstrip("/")
    try:
        from .config import get_settings

        return f"http://127.0.0.1:{get_settings().server.port}"
    except Exception:
        return "http://127.0.0.1:8910"


def internal_auth_headers() -> dict[str, str]:
    """Authorization headers for internal callback requests.

    Resolution order: ``MOA_GATEWAY_KEY`` env var ->
    ``settings.auth.gateway_api_keys[0]`` (which is guaranteed to exist at
    runtime: config, env, or the auto-generated startup key).

    Returns an empty dict only when no key can be resolved at all (e.g.
    before settings are loaded AND no env var) -- callers then hit a 401
    loudly instead of silently sending nothing.
    """
    key = os.environ.get("MOA_GATEWAY_KEY", "").strip()
    yaml_keys: list[str] = []
    try:
        from .config import get_settings

        yaml_keys = [str(k).strip() for k in get_settings().auth.gateway_api_keys if k]
    except Exception:
        logger.warning(
            "internal_auth_headers: failed to resolve settings; no yaml keys",
            exc_info=True,
        )
        yaml_keys = []
    if key and yaml_keys and key not in yaml_keys:
        # The auth layer only accepts keys listed in settings.auth.gateway_api_keys
        # (or mgw-* keys stored in the DB). An env key outside that list will
        # 401 -- warn loudly so config drift is easy to spot (review A-m1).
        logger.warning(
            "MOA_GATEWAY_KEY is not present in auth.gateway_api_keys; "
            "internal callbacks may fail with 401"
        )
    if not key and yaml_keys:
        key = yaml_keys[0]
    if not key:
        logger.warning(
            "internal_auth_headers: no gateway key resolved (env empty, yaml_keys=%d); "
            "loopback call will 401",
            len(yaml_keys),
        )
        return {}
    return {"Authorization": f"Bearer {key}"}
