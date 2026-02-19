"""API endpoints for knowledge sources, source groups, and project source linking."""

from __future__ import annotations

import uuid as uuid_mod
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from pydantic import ValidationError

from ..models import ProjectSourceLink, SourceCreate, SourceGroupCreate, SourceGroupUpdate, SourceUpdate
from ..services import storage

router = APIRouter(tags=["sources"])


def _get_db(request: Request) -> Any:
    db_name = request.query_params.get("db")
    if hasattr(request.app.state, "db_manager"):
        return request.app.state.db_manager.get(db_name)
    return request.app.state.db


def _validate_uuid(value: str, name: str = "id") -> None:
    try:
        uuid_mod.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {name} format")


def _get_identity(request: Request) -> tuple[str | None, str | None]:
    config = getattr(request.app.state, "config", None)
    if config and config.identity:
        return config.identity.user_id, config.identity.display_name
    return None, None


def _validate_json_content_type(request: Request) -> None:
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("application/json"):
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")


def _parse_body(model_cls: type, body: dict) -> Any:
    """Parse and validate a request body against a Pydantic model."""
    try:
        return model_cls(**body)
    except ValidationError as e:
        errors = [
            {"msg": err.get("msg", "Validation error"), "type": err.get("type", "value_error")} for err in e.errors()
        ]
        raise HTTPException(status_code=422, detail=errors)


# --- Sources ---


@router.get("/sources")
async def list_sources(
    request: Request,
    search: str | None = None,
    type: str | None = Query(default=None, pattern="^(file|text|url)$"),
    tag_id: str | None = None,
    group_id: str | None = None,
    project_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    db = _get_db(request)
    sources = storage.list_sources(
        db,
        search=search,
        source_type=type,
        tag_id=tag_id,
        group_id=group_id,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )
    return {"sources": sources}


@router.post("/sources", status_code=201)
async def create_source(request: Request) -> dict[str, Any]:
    _validate_json_content_type(request)
    body = await request.json()
    data = _parse_body(SourceCreate, body)
    db = _get_db(request)
    user_id, display_name = _get_identity(request)
    source = storage.create_source(
        db,
        source_type=data.type,
        title=data.title,
        content=data.content,
        url=data.url,
        user_id=user_id,
        user_display_name=display_name,
    )

    # Queue embedding if worker is available
    worker = getattr(request.app.state, "embedding_worker", None)
    if worker and source.get("content"):
        try:
            await worker.embed_source(source["id"])
        except Exception:
            pass  # Will be picked up by background worker

    return source


@router.post("/sources/upload", status_code=201)
async def upload_source(
    request: Request,
    file: UploadFile,
    title: str | None = None,
) -> dict[str, Any]:
    db = _get_db(request)
    data_dir = request.app.state.config.app.data_dir
    user_id, display_name = _get_identity(request)

    file_data = await file.read()
    source = storage.save_source_file(
        db,
        title=title or file.filename or "Untitled",
        filename=file.filename or "unnamed",
        mime_type=file.content_type or "application/octet-stream",
        data=file_data,
        data_dir=data_dir,
        user_id=user_id,
        user_display_name=display_name,
    )

    worker = getattr(request.app.state, "embedding_worker", None)
    if worker and source.get("content"):
        try:
            await worker.embed_source(source["id"])
        except Exception:
            pass

    return source


@router.get("/sources/{source_id}")
async def get_source(request: Request, source_id: str) -> dict[str, Any]:
    _validate_uuid(source_id, "source_id")
    db = _get_db(request)
    source = storage.get_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.patch("/sources/{source_id}")
async def update_source(request: Request, source_id: str) -> dict[str, Any]:
    _validate_json_content_type(request)
    _validate_uuid(source_id, "source_id")
    body = await request.json()
    data = _parse_body(SourceUpdate, body)
    db = _get_db(request)
    source = storage.update_source(
        db,
        source_id,
        title=data.title,
        content=data.content,
        url=data.url,
    )
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    # Re-embed if content changed
    worker = getattr(request.app.state, "embedding_worker", None)
    if worker and data.content is not None:
        try:
            await worker.embed_source(source_id)
        except Exception:
            pass

    return source


@router.delete("/sources/{source_id}")
async def delete_source(request: Request, source_id: str) -> dict[str, Any]:
    _validate_uuid(source_id, "source_id")
    db = _get_db(request)
    data_dir = request.app.state.config.app.data_dir
    if not storage.delete_source(db, source_id, data_dir=data_dir):
        raise HTTPException(status_code=404, detail="Source not found")
    return {"status": "deleted"}


# --- Source Tags ---


@router.post("/sources/{source_id}/tags/{tag_id}", status_code=201)
async def tag_source(request: Request, source_id: str, tag_id: str) -> dict[str, Any]:
    _validate_uuid(source_id, "source_id")
    _validate_uuid(tag_id, "tag_id")
    db = _get_db(request)
    if not storage.add_tag_to_source(db, source_id, tag_id):
        raise HTTPException(status_code=400, detail="Failed to tag source")
    return {"status": "tagged"}


@router.delete("/sources/{source_id}/tags/{tag_id}")
async def untag_source(request: Request, source_id: str, tag_id: str) -> dict[str, Any]:
    _validate_uuid(source_id, "source_id")
    _validate_uuid(tag_id, "tag_id")
    db = _get_db(request)
    storage.remove_tag_from_source(db, source_id, tag_id)
    return {"status": "untagged"}


# --- Source Groups ---


@router.get("/source-groups")
async def list_source_groups(request: Request) -> dict[str, Any]:
    db = _get_db(request)
    groups = storage.list_source_groups(db)
    return {"groups": groups}


@router.post("/source-groups", status_code=201)
async def create_source_group(request: Request) -> dict[str, Any]:
    _validate_json_content_type(request)
    body = await request.json()
    data = _parse_body(SourceGroupCreate, body)
    db = _get_db(request)
    user_id, display_name = _get_identity(request)
    group = storage.create_source_group(
        db,
        name=data.name,
        description=data.description,
        user_id=user_id,
        user_display_name=display_name,
    )
    return group


@router.patch("/source-groups/{group_id}")
async def update_source_group(request: Request, group_id: str) -> dict[str, Any]:
    _validate_json_content_type(request)
    _validate_uuid(group_id, "group_id")
    body = await request.json()
    data = _parse_body(SourceGroupUpdate, body)
    db = _get_db(request)
    group = storage.update_source_group(db, group_id, name=data.name, description=data.description)
    if not group:
        raise HTTPException(status_code=404, detail="Source group not found")
    return group


@router.delete("/source-groups/{group_id}")
async def delete_source_group(request: Request, group_id: str) -> dict[str, Any]:
    _validate_uuid(group_id, "group_id")
    db = _get_db(request)
    if not storage.delete_source_group(db, group_id):
        raise HTTPException(status_code=404, detail="Source group not found")
    return {"status": "deleted"}


@router.post("/source-groups/{group_id}/sources/{source_id}", status_code=201)
async def add_to_group(request: Request, group_id: str, source_id: str) -> dict[str, Any]:
    _validate_uuid(group_id, "group_id")
    _validate_uuid(source_id, "source_id")
    db = _get_db(request)
    if not storage.add_source_to_group(db, group_id, source_id):
        raise HTTPException(status_code=400, detail="Failed to add source to group")
    return {"status": "added"}


@router.delete("/source-groups/{group_id}/sources/{source_id}")
async def remove_from_group(request: Request, group_id: str, source_id: str) -> dict[str, Any]:
    _validate_uuid(group_id, "group_id")
    _validate_uuid(source_id, "source_id")
    db = _get_db(request)
    storage.remove_source_from_group(db, group_id, source_id)
    return {"status": "removed"}


# --- Project Sources ---


@router.get("/projects/{project_id}/sources")
async def get_project_sources(request: Request, project_id: str) -> dict[str, Any]:
    _validate_uuid(project_id, "project_id")
    db = _get_db(request)
    sources = storage.get_project_sources(db, project_id)
    return {"sources": sources}


@router.post("/projects/{project_id}/sources", status_code=201)
async def link_project_source(request: Request, project_id: str) -> dict[str, Any]:
    _validate_json_content_type(request)
    _validate_uuid(project_id, "project_id")
    body = await request.json()
    data = _parse_body(ProjectSourceLink, body)
    db = _get_db(request)

    proj = storage.get_project(db, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    link = storage.link_source_to_project(
        db,
        project_id,
        source_id=data.source_id,
        group_id=data.group_id,
        tag_filter=data.tag_filter,
    )
    return link


@router.delete("/projects/{project_id}/sources")
async def unlink_project_source(request: Request, project_id: str) -> dict[str, Any]:
    _validate_json_content_type(request)
    _validate_uuid(project_id, "project_id")
    body = await request.json()
    data = _parse_body(ProjectSourceLink, body)
    db = _get_db(request)
    storage.unlink_source_from_project(
        db,
        project_id,
        source_id=data.source_id,
        group_id=data.group_id,
        tag_filter=data.tag_filter,
    )
    return {"status": "unlinked"}
