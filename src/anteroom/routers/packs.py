"""Pack API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..services import packs
from ..services.pack_sources import list_cached_sources

router = APIRouter(tags=["packs"])


@router.get("/packs")
async def list_packs(request: Request) -> list[dict[str, Any]]:
    """List all installed packs with artifact counts."""
    db = request.app.state.db
    result = packs.list_packs(db)
    for p in result:
        p.pop("source_path", None)
    return result


@router.get("/packs/sources")
async def list_sources(request: Request) -> list[dict[str, Any]]:
    """List configured pack sources with cache status."""
    config = request.app.state.config
    sources = getattr(config, "pack_sources", [])
    data_dir = config.app.data_dir

    cached = list_cached_sources(data_dir)
    cached_urls = {c.url: c for c in cached}

    result: list[dict[str, Any]] = []
    for src in sources:
        cached_src = cached_urls.get(src.url)
        result.append(
            {
                "url": src.url,
                "branch": src.branch,
                "refresh_interval": src.refresh_interval,
                "cached": cached_src is not None,
                "ref": cached_src.ref[:12] if cached_src and cached_src.ref else None,
            }
        )
    return result


@router.post("/packs/refresh")
async def refresh_sources(request: Request) -> list[dict[str, Any]]:
    """Manually trigger a refresh of all configured pack sources."""
    config = request.app.state.config
    sources = getattr(config, "pack_sources", [])
    if not sources:
        return []

    db = request.app.state.db
    data_dir = config.app.data_dir

    from ..services.pack_refresh import PackRefreshWorker

    worker = PackRefreshWorker(db=db, data_dir=data_dir, sources=sources)
    results = worker.refresh_all()
    return [
        {
            "url": r.url,
            "success": r.success,
            "packs_installed": r.packs_installed,
            "packs_updated": r.packs_updated,
            "changed": r.changed,
            "error": r.error,
        }
        for r in results
    ]


@router.get("/packs/{namespace}/{name}")
async def get_pack(request: Request, namespace: str, name: str) -> dict[str, Any]:
    """Get a pack with its full artifact list."""
    db = request.app.state.db
    result = packs.get_pack(db, namespace, name)
    if not result:
        raise HTTPException(status_code=404, detail="Pack not found")
    result.pop("source_path", None)
    for art in result.get("artifacts", []):
        art.pop("content", None)
    return result
