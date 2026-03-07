"""Pack API endpoints."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ..services import packs
from ..services.pack_sources import list_cached_sources

router = APIRouter(tags=["packs"])

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
_SAFE_ID_RE = re.compile(r"^[a-f0-9-]{32,36}$")


def _reload_registries(request: Request, db: Any) -> None:
    """Reload artifact registry, rule enforcer, and skill registry after pack changes."""
    registry = getattr(request.app.state, "artifact_registry", None)
    if registry is not None:
        registry.load_from_db(db)  # Web UI: reloads global attachments
    rule_enforcer = getattr(request.app.state, "rule_enforcer", None)
    if rule_enforcer is not None and registry is not None:
        from ..services.artifacts import ArtifactType

        rule_enforcer.load_rules(registry.list_all(artifact_type=ArtifactType.RULE))
    skill_registry = getattr(request.app.state, "skill_registry", None)
    if skill_registry is not None and registry is not None:
        skill_registry.load_from_artifacts(registry)


def _validate_pack_path_params(namespace: str, name: str) -> None:
    """Validate namespace and name path parameters against safe name regex."""
    if not _SAFE_NAME_RE.match(namespace):
        raise HTTPException(status_code=400, detail=f"Invalid namespace: {namespace!r}")
    if not _SAFE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"Invalid pack name: {name!r}")


def _resolve_or_409(db: Any, namespace: str, name: str) -> dict[str, Any]:
    """Resolve a pack by namespace/name, raising 409 on ambiguity or 404 if not found."""
    match, candidates = packs.resolve_pack(db, namespace, name)
    if match:
        return match
    if candidates:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"Multiple packs match {namespace}/{name}",
                "candidates": [{"id": c["id"], "version": c.get("version", "")} for c in candidates],
            },
        )
    raise HTTPException(status_code=404, detail="Pack not found")


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
    if any(r.changed for r in results):
        _reload_registries(request, db)
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


# --- by-id routes MUST come before {namespace}/{name} wildcard routes ---
# FastAPI uses first-match routing; if the wildcard routes are first,
# "by-id" is captured as the namespace parameter.


@router.get("/packs/by-id/{pack_id}")
async def get_pack_by_id(request: Request, pack_id: str) -> dict[str, Any]:
    """Get a pack by its unique ID."""
    if not _SAFE_ID_RE.match(pack_id):
        raise HTTPException(status_code=400, detail="Invalid pack ID format")
    db = request.app.state.db
    result = packs.get_pack_by_id(db, pack_id)
    if not result:
        raise HTTPException(status_code=404, detail="Pack not found")
    result.pop("source_path", None)
    for art in result.get("artifacts", []):
        art.pop("content", None)
    return result


@router.delete("/packs/by-id/{pack_id}")
async def remove_pack_by_id(request: Request, pack_id: str) -> dict[str, str]:
    """Remove a pack by its unique ID."""
    if not _SAFE_ID_RE.match(pack_id):
        raise HTTPException(status_code=400, detail="Invalid pack ID format")
    db = request.app.state.db
    removed = packs.remove_pack_by_id(db, pack_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Pack not found")
    _reload_registries(request, db)
    return {"status": "removed"}


# --- {namespace}/{name} wildcard routes ---


@router.post("/packs/{namespace}/{name}/attach")
async def attach_pack(request: Request, namespace: str, name: str, body: AttachRequest) -> dict[str, Any]:
    """Attach a pack to global or project scope."""
    _validate_pack_path_params(namespace, name)
    from ..services.pack_attachments import attach_pack as do_attach

    db = request.app.state.db
    pack = _resolve_or_409(db, namespace, name)

    try:
        result = do_attach(db, pack["id"], project_path=body.project_path)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    _reload_registries(request, db)
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

    db = request.app.state.db
    pack = _resolve_or_409(db, namespace, name)

    removed = do_detach(db, pack["id"], project_path=project_path)
    if not removed:
        raise HTTPException(status_code=404, detail="Attachment not found")
    _reload_registries(request, db)
    return {"status": "detached"}


@router.get("/packs/{namespace}/{name}/attachments")
async def list_pack_attachments(request: Request, namespace: str, name: str) -> list[dict[str, Any]]:
    """List attachments for a specific pack."""
    _validate_pack_path_params(namespace, name)
    from ..services.pack_attachments import list_attachments_for_pack

    db = request.app.state.db
    pack = _resolve_or_409(db, namespace, name)

    return list_attachments_for_pack(db, pack["id"])


@router.delete("/packs/{namespace}/{name}")
async def remove_pack(request: Request, namespace: str, name: str) -> dict[str, str]:
    """Remove an installed pack."""
    _validate_pack_path_params(namespace, name)
    db = request.app.state.db
    pack = _resolve_or_409(db, namespace, name)
    removed = packs.remove_pack_by_id(db, pack["id"])
    if not removed:
        raise HTTPException(status_code=404, detail="Pack not found")
    _reload_registries(request, db)
    return {"status": "removed"}


@router.get("/packs/{namespace}/{name}")
async def get_pack(request: Request, namespace: str, name: str) -> dict[str, Any]:
    """Get a pack with its full artifact list."""
    _validate_pack_path_params(namespace, name)
    db = request.app.state.db
    resolved = _resolve_or_409(db, namespace, name)
    pack = packs.get_pack_by_id(db, resolved["id"])
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")
    pack.pop("source_path", None)
    for art in pack.get("artifacts", []):
        art.pop("content", None)
    return pack
