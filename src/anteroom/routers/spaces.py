"""Spaces API endpoints."""

from __future__ import annotations

import re
import sqlite3
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
    get_space_by_name,
    get_space_paths,
    list_spaces,
)
from ..services.storage import (
    get_space_sources,
    link_source_to_space,
    unlink_source_from_space,
)

router = APIRouter(tags=["spaces"])

_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


class SpaceCreateRequest(BaseModel):
    name: str
    file_path: str
    file_hash: str = ""

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

    @field_validator("file_path")
    @classmethod
    def validate_file_path(cls, v: str) -> str:
        if ".." in v.split("/"):
            raise ValueError("Path traversal not allowed")
        return v


def _get_db(request: Request) -> Any:
    return request.app.state.db


@router.get("/spaces")
async def api_list_spaces(request: Request) -> list[dict[str, Any]]:
    return list_spaces(_get_db(request))


@router.post("/spaces", status_code=201)
async def api_create_space(request: Request, body: SpaceCreateRequest) -> dict[str, Any]:
    db = _get_db(request)

    existing = get_space_by_name(db, body.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Space {body.name!r} already exists")

    try:
        return db_create_space(db, name=body.name, file_path=body.file_path, file_hash=body.file_hash)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Space {body.name!r} already exists")


@router.get("/spaces/{space_id}")
async def api_get_space(request: Request, space_id: str) -> dict[str, Any]:
    space = get_space(_get_db(request), space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")
    return space


class SpaceSourceLinkRequest(BaseModel):
    source_id: str | None = None
    group_id: str | None = None
    tag_filter: str | None = None


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

    file_path = space.get("file_path", "")
    if not file_path:
        raise HTTPException(status_code=400, detail="Space has no file_path")

    from ..services.spaces import file_hash, parse_space_file

    path = Path(file_path)
    if not path.is_file():
        raise HTTPException(status_code=400, detail="Space file not found")

    try:
        cfg = parse_space_file(path)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid space file")

    new_hash = file_hash(path)
    from ..services.space_storage import update_space

    update_space(db, space_id, file_hash=new_hash)
    return {"id": space_id, "name": cfg.name, "file_hash": new_hash, "refreshed": True}


@router.get("/spaces/{space_id}/sources")
async def api_get_space_sources(request: Request, space_id: str) -> list[dict[str, Any]]:
    db = _get_db(request)
    space = get_space(db, space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")
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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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
