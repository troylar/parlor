"""Spaces API endpoints."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from ..services.space_storage import (
    create_space as db_create_space,
)
from ..services.space_storage import (
    delete_space as db_delete_space,
)
from ..services.space_storage import (
    get_space,
    get_space_paths,
    list_spaces,
    update_space,
)
from ..services.spaces import is_local_space
from ..services.storage import (
    get_direct_space_source_links,
    get_space_sources,
    link_source_to_space,
    unlink_source_from_space,
)

router = APIRouter(tags=["spaces"])

_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


class SpaceCreateRequest(BaseModel):
    name: str
    instructions: str = ""
    model: str | None = None
    source_file: str = ""
    source_hash: str = ""

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _NAME_PATTERN.match(v):
            raise ValueError(
                f"Invalid space name: {v!r}. "
                "Must start with alphanumeric character, contain only alphanumeric, "
                "hyphens, and underscores, and be 1-64 characters."
            )
        return v

    @field_validator("source_file")
    @classmethod
    def validate_source_file(cls, v: str) -> str:
        if v:
            # Check for traversal in both Unix and Windows path separators
            parts = re.split(r"[/\\]", v)
            if ".." in parts:
                raise ValueError("Path traversal not allowed")
        return v


class SpaceUpdateRequest(BaseModel):
    name: str | None = None
    instructions: str | None = None
    model: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is not None and not _NAME_PATTERN.match(v):
            raise ValueError(
                f"Invalid space name: {v!r}. "
                "Must start with alphanumeric character, contain only alphanumeric, "
                "hyphens, and underscores, and be 1-64 characters."
            )
        return v


class SpaceSourceLinkRequest(BaseModel):
    source_id: str | None = None
    group_id: str | None = None
    tag_filter: str | None = None


def _get_db(request: Request) -> Any:
    return request.app.state.db


def _validate_uuid(value: str, label: str = "ID") -> None:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail=f"Invalid {label} format")


def _enrich_origin(space: dict[str, Any]) -> dict[str, Any]:
    sf = space.get("source_file", "")
    space["origin"] = "local" if (sf and is_local_space(sf)) else "global"
    return space


@router.get("/spaces")
async def api_list_spaces(request: Request) -> list[dict[str, Any]]:
    spaces = list_spaces(_get_db(request))
    for s in spaces:
        _enrich_origin(s)
    return spaces


@router.post("/spaces", status_code=201)
async def api_create_space(request: Request, body: SpaceCreateRequest) -> dict[str, Any]:
    db = _get_db(request)
    space = db_create_space(
        db,
        name=body.name,
        instructions=body.instructions,
        model=body.model,
        source_file=body.source_file,
        source_hash=body.source_hash,
    )
    return _enrich_origin(space)


@router.get("/spaces/{space_id}")
async def api_get_space(request: Request, space_id: str) -> dict[str, Any]:
    space = get_space(_get_db(request), space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")
    return _enrich_origin(space)


@router.patch("/spaces/{space_id}")
async def api_update_space(request: Request, space_id: str, body: SpaceUpdateRequest) -> dict[str, Any]:
    _validate_uuid(space_id, "space_id")
    db = _get_db(request)
    space = get_space(db, space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")

    updates: dict[str, Any] = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.instructions is not None:
        updates["instructions"] = body.instructions
    if "model" in body.model_fields_set:
        updates["model"] = body.model if body.model else None

    if not updates:
        return _enrich_origin(space)

    updated = update_space(db, space_id, **updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Space not found")
    return _enrich_origin(updated)


@router.delete("/spaces/{space_id}", status_code=204)
async def api_delete_space(request: Request, space_id: str) -> None:
    if not db_delete_space(_get_db(request), space_id):
        raise HTTPException(status_code=404, detail="Space not found")


@router.get("/spaces/{space_id}/paths")
async def api_get_space_paths(request: Request, space_id: str) -> list[dict[str, Any]]:
    db = _get_db(request)
    space = get_space(db, space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")
    return get_space_paths(db, space_id)


@router.post("/spaces/{space_id}/refresh")
async def api_refresh_space(request: Request, space_id: str) -> dict[str, Any]:
    db = _get_db(request)
    space = get_space(db, space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")

    source_file = space.get("source_file", "")
    if not source_file:
        raise HTTPException(status_code=400, detail="Space has no source file to refresh from")

    from ..services.spaces import compute_file_hash, parse_space_file

    path = Path(source_file)
    if not path.is_file():
        raise HTTPException(status_code=400, detail="Space source file not found on disk")

    try:
        cfg = parse_space_file(path)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid space file")

    new_hash = compute_file_hash(path)
    updates: dict[str, Any] = {
        "source_hash": new_hash,
        "instructions": cfg.instructions or "",
        "model": cfg.config.get("model") or None,
    }

    updated = update_space(db, space_id, **updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Space not found")
    result = _enrich_origin(updated)
    result["refreshed"] = True
    return result


@router.post("/spaces/sync")
async def api_sync_space(request: Request) -> dict[str, Any]:
    """Sync a space from a YAML file path into the database."""
    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type:
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")
    body = await request.json()
    file_path = body.get("file_path", "")
    if not file_path:
        raise HTTPException(status_code=400, detail="file_path is required")
    if ".." in re.split(r"[/\\]", file_path):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")

    path = Path(file_path).resolve()

    # Restrict to known base directories: global spaces dir or .anteroom/ dirs
    from ..services.spaces import get_spaces_dir

    global_dir = get_spaces_dir().resolve()
    anteroom_dir_name = ".anteroom"
    if not (str(path).startswith(str(global_dir)) or anteroom_dir_name in path.parts):
        raise HTTPException(
            status_code=400,
            detail="Path must be inside ~/.anteroom/ or a local .anteroom/ directory",
        )

    if not path.is_file():
        raise HTTPException(status_code=400, detail="File not found")

    from ..services.spaces import sync_space_from_file

    db = _get_db(request)
    try:
        space = sync_space_from_file(db, path, track_source=body.get("track_source", True))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid space configuration")

    return _enrich_origin(space)


@router.get("/spaces/{space_id}/export")
async def api_export_space(request: Request, space_id: str) -> dict[str, Any]:
    """Export a space as a YAML-compatible dict."""
    _validate_uuid(space_id, "space_id")
    db = _get_db(request)
    space = get_space(db, space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")

    from ..services.spaces import export_space_to_yaml

    try:
        cfg = export_space_to_yaml(db, space_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid space configuration")

    result: dict[str, Any] = {"name": cfg.name, "version": cfg.version}
    if cfg.instructions:
        result["instructions"] = cfg.instructions
    if cfg.config:
        result["config"] = cfg.config
    if cfg.repos:
        result["repos"] = cfg.repos
    if cfg.packs:
        result["packs"] = cfg.packs
    if cfg.sources:
        result["sources"] = [{"path": s.path, "url": s.url} for s in cfg.sources]
    return result


@router.get("/spaces/{space_id}/sources")
async def api_get_space_sources(request: Request, space_id: str, link_type: str | None = None) -> list[dict[str, Any]]:
    db = _get_db(request)
    space = get_space(db, space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")
    if link_type == "direct":
        return get_direct_space_source_links(db, space_id)
    return get_space_sources(db, space_id)


@router.post("/spaces/{space_id}/sources", status_code=201)
async def api_link_space_source(request: Request, space_id: str, body: SpaceSourceLinkRequest) -> dict[str, Any]:
    db = _get_db(request)
    space = get_space(db, space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")

    try:
        return link_source_to_space(
            db,
            space_id,
            source_id=body.source_id,
            group_id=body.group_id,
            tag_filter=body.tag_filter,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source link configuration")


@router.delete("/spaces/{space_id}/sources/{source_id}", status_code=204)
async def api_unlink_space_source(request: Request, space_id: str, source_id: str) -> None:
    db = _get_db(request)
    space = get_space(db, space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")
    unlink_source_from_space(db, space_id, source_id=source_id)


@router.get("/spaces/{space_id}/packs")
async def api_get_space_packs(request: Request, space_id: str) -> list[dict[str, Any]]:
    db = _get_db(request)
    space = get_space(db, space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")

    from ..services.pack_attachments import get_active_pack_ids_for_space

    pack_ids = get_active_pack_ids_for_space(db, space_id)
    if not pack_ids:
        return []

    packs = []
    for pid in pack_ids:
        row = db.execute(
            "SELECT id, namespace, name, version, description FROM packs WHERE id = ?",
            (pid,),
        ).fetchone()
        if row:
            if hasattr(row, "keys"):
                packs.append(dict(row))
            else:
                packs.append(
                    {
                        "id": row[0],
                        "namespace": row[1],
                        "name": row[2],
                        "version": row[3],
                        "description": row[4],
                    }
                )
    return packs
