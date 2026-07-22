"""WebUI static file serving endpoints."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

logger = logging.getLogger(__name__)

WEBUI_DIR = Path(__file__).parent.parent / "webui"

router = APIRouter(tags=["webui"])


@router.get("/", response_class=HTMLResponse)
async def index():
    idx = WEBUI_DIR / "index.html"
    if not idx.exists():
        return HTMLResponse("<h1>WebUI not found</h1>", status_code=500)
    return HTMLResponse(idx.read_text(encoding="utf-8"))


@router.get("/webui/{name}")
async def webui_assets(name: str):
    # Path traversal protection
    if "/" in name or "\\\\" in name or ".." in name or name.startswith("."):
        raise HTTPException(404, "not found")
    p = WEBUI_DIR / name
    try:
        base = WEBUI_DIR.resolve()
        resolved = p.resolve()
        # Prevent symlink following
        try:
            if p.is_symlink() or p.lstat() and (p.lstat().st_mode & 0o170000) == 0o120000:
                raise HTTPException(404, "not found")
        except (OSError, AttributeError):
            pass
        common = os.path.commonpath([str(resolved), str(base)])
        if common != str(base):
            raise HTTPException(404, "not found")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(404, "not found")
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(str(p))
