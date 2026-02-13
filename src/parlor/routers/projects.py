"""Project CRUD endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..services import storage

router = APIRouter(tags=["projects"])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    instructions: str = Field(default="", max_length=50000)
    model: str | None = Field(default=None, max_length=200)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    instructions: str | None = Field(default=None, max_length=50000)
    model: str | None = Field(default=None, max_length=200)


def _validate_uuid(value: str) -> str:
    try:
        uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")
    return value


@router.get("/projects")
async def list_projects(request: Request):
    db = request.app.state.db
    return storage.list_projects(db)


@router.post("/projects", status_code=201)
async def create_project(body: ProjectCreate, request: Request):
    db = request.app.state.db
    return storage.create_project(db, name=body.name, instructions=body.instructions, model=body.model)


@router.get("/projects/{project_id}")
async def get_project(project_id: str, request: Request):
    _validate_uuid(project_id)
    db = request.app.state.db
    proj = storage.get_project(db, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    return proj


@router.patch("/projects/{project_id}")
async def update_project(project_id: str, body: ProjectUpdate, request: Request):
    _validate_uuid(project_id)
    db = request.app.state.db
    kwargs = {}
    if body.name is not None:
        kwargs["name"] = body.name
    if body.instructions is not None:
        kwargs["instructions"] = body.instructions
    if body.model is not None:
        kwargs["model"] = body.model
    proj = storage.update_project(db, project_id, **kwargs)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    return proj


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: str, request: Request):
    _validate_uuid(project_id)
    db = request.app.state.db
    deleted = storage.delete_project(db, project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    return Response(status_code=204)
