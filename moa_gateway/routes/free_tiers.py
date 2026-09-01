"""HTTP surface for the free-tier model catalog (M4).

Endpoints (all gated by API key + the ``free_tiers`` capability):

- ``GET  /v1/free-tiers``        — filter + paginate the 456-entry catalog.
- ``GET  /v1/free-tiers/{key}``  — one catalog entry by its pool-dedup key.

Read-only over the ported OmniRoute catalog (no gateway state is mutated).
When ``settings.free_tiers.enabled`` is False the surfaces return 503 —
the module is opt-in and cannot influence pre-existing routes.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import require_api_key
from ..capability_toggles import require_capability
from ..config import get_settings
from ..free_tiers.catalog import CatalogValidationError, FreeTierCatalog, load_catalog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/free-tiers", tags=["free_tiers"])

_CAPABILITY = "free_tiers"

_CATALOG: FreeTierCatalog | None = None
_CATALOG_LOCK = threading.Lock()


def _shared_catalog() -> FreeTierCatalog:
    """Process-wide lazy singleton so the bundled JSON is parsed once.

    Tests override via ``app.state.free_tier_catalog`` (see _catalog).
    """
    global _CATALOG
    if _CATALOG is None:
        with _CATALOG_LOCK:
            if _CATALOG is None:
                _CATALOG = load_catalog(get_settings().free_tiers.catalog_path or None)
    return _CATALOG


def _catalog(request: Request) -> FreeTierCatalog:
    """app.state override (tests) wins; production uses the shared singleton."""
    catalog = getattr(request.app.state, "free_tier_catalog", None)
    return catalog if catalog is not None else _shared_catalog()


def _require_enabled() -> None:
    if not get_settings().free_tiers.enabled:
        raise HTTPException(
            status_code=503,
            detail="free_tiers capability is disabled (settings.free_tiers.enabled=false)",
        )


@router.get("")
async def list_free_tiers(
    request: Request,
    provider: str | None = None,
    regime: str | None = None,
    q: str | None = None,
    tos: str | None = None,
    exclude_tos_avoid: bool = False,
    page: int = 1,
    page_size: int = 50,
    _auth: dict[str, Any] = Depends(require_api_key),
    _cap: None = Depends(require_capability(_CAPABILITY)),
) -> dict[str, Any]:
    _require_enabled()
    try:
        return _catalog(request).query(
            provider=provider,
            regime=regime,
            q=q,
            tos=tos,
            exclude_tos_avoid=exclude_tos_avoid,
            page=page,
            page_size=page_size,
        )
    except CatalogValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{key}")
async def get_free_tier(
    key: str,
    request: Request,
    _auth: dict[str, Any] = Depends(require_api_key),
    _cap: None = Depends(require_capability(_CAPABILITY)),
) -> dict[str, Any]:
    _require_enabled()
    entry = _catalog(request).get(key)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown free-tier key {key!r}")
    return entry
