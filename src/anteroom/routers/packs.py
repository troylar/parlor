"""Pack API endpoints."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ..services import packs
from ..services.pack_sources import list_cached_sources

router = APIRouter(tags=["packs"])

_SAFE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _validate_pack_path_params(namespace: str, name: str) -> None:
    """Validate namespace and name path parameters against safe name regex."""
    if not _SAFE_NAME_RE.match(namespace):
        raise HTTPException(status_code=400, detail=f"Invalid namespace: {namespace!r}")
    if not _SAFE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"Invalid pack name: {name!r}")


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


class AttachRequest(BaseModel):
    project_path: str | None = None


@router.post("/packs/{namespace}/{name}/attach")
async def attach_pack(request: Request, namespace: str, name: str, body: AttachRequest) -> dict[str, Any]:
    """Attach a pack to global or project scope."""
    _validate_pack_path_params(namespace, name)
    from ..services.pack_attachments import attach_pack as do_attach
    from ..services.pack_attachments import resolve_pack_id

    db = request.app.state.db
    pack_id = resolve_pack_id(db, namespace, name)
    if not pack_id:
        raise HTTPException(status_code=404, detail="Pack not found")

    try:
        result = do_attach(db, pack_id, project_path=body.project_path)
    except ValueError:
        raise HTTPException(status_code=409, detail="Pack is already attached at this scope")
    return result


@router.delete("/packs/{namespace}/{name}/attach")
async def detach_pack(
    request: Request,
    namespace: str,
    name: str,
    project_path: str | None = Query(default=None),
) -> dict[str, str]:
    """Detach a pack from global or project scope."""
    _validate_pack_path_params(namespace, name)
    from ..services.pack_attachments import detach_pack as do_detach
    from ..services.pack_attachments import resolve_pack_id

    db = request.app.state.db
    pack_id = resolve_pack_id(db, namespace, name)
    if not pack_id:
        raise HTTPException(status_code=404, detail="Pack not found")

    removed = do_detach(db, pack_id, project_path=project_path)
    if not removed:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return {"status": "detached"}


@router.get("/packs/{namespace}/{name}/attachments")
async def list_pack_attachments(request: Request, namespace: str, name: str) -> list[dict[str, Any]]:
    """List attachments for a specific pack."""
    _validate_pack_path_params(namespace, name)
    from ..services.pack_attachments import (
        list_attachments_for_pack,
        resolve_pack_id,
    )

    db = request.app.state.db
    pack_id = resolve_pack_id(db, namespace, name)
    if not pack_id:
        raise HTTPException(status_code=404, detail="Pack not found")

    return list_attachments_for_pack(db, pack_id)


@router.delete("/packs/{namespace}/{name}")
async def remove_pack(request: Request, namespace: str, name: str) -> dict[str, str]:
    """Remove an installed pack."""
    _validate_pack_path_params(namespace, name)
    db = request.app.state.db
    removed = packs.remove_pack(db, namespace, name)
    if not removed:
        raise HTTPException(status_code=404, detail="Pack not found")
    return {"status": "removed"}


@router.get("/packs/{namespace}/{name}")
async def get_pack(request: Request, namespace: str, name: str) -> dict[str, Any]:
    """Get a pack with its full artifact list."""
    _validate_pack_path_params(namespace, name)
    db = request.app.state.db
    result = packs.get_pack(db, namespace, name)
    if not result:
        raise HTTPException(status_code=404, detail="Pack not found")
    result.pop("source_path", None)
    for art in result.get("artifacts", []):
        art.pop("content", None)
    return result
