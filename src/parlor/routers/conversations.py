"""Conversation CRUD endpoints."""

from __future__ import annotations

import re
import unicodedata
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from ..models import ConversationUpdate
from ..services import storage
from ..services.export import export_conversation_markdown

router = APIRouter(tags=["conversations"])


def _validate_uuid(value: str) -> str:
    try:
        uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")
    return value


@router.get("/conversations")
async def list_conversations(request: Request, search: str | None = None, project_id: str | None = None):
    if project_id:
        _validate_uuid(project_id)
    db = request.app.state.db
    return storage.list_conversations(db, search=search, project_id=project_id)


@router.post("/conversations", status_code=201)
async def create_conversation(request: Request):
    db = request.app.state.db
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    project_id = body.get("project_id") if isinstance(body, dict) else None
    if project_id:
        _validate_uuid(project_id)
    return storage.create_conversation(db, project_id=project_id)


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request):
    _validate_uuid(conversation_id)
    db = request.app.state.db
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = storage.list_messages(db, conversation_id)
    return {**conv, "messages": messages}


@router.patch("/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, body: ConversationUpdate, request: Request):
    _validate_uuid(conversation_id)
    db = request.app.state.db
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if body.title is not None:
        conv = storage.update_conversation_title(db, conversation_id, body.title)
    if body.model is not None:
        conv = storage.update_conversation_model(db, conversation_id, body.model)
    return conv


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, request: Request):
    _validate_uuid(conversation_id)
    db = request.app.state.db
    data_dir = request.app.state.config.app.data_dir
    deleted = storage.delete_conversation(db, conversation_id, data_dir)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=204)


@router.get("/conversations/{conversation_id}/export")
async def export_conversation(conversation_id: str, request: Request):
    _validate_uuid(conversation_id)
    db = request.app.state.db
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = storage.list_messages(db, conversation_id)
    markdown = export_conversation_markdown(conv, messages)
    safe_title = "".join(c for c in conv["title"] if unicodedata.category(c)[0] not in ("C",))
    filename = re.sub(r"[^\w\s\-]", "", safe_title)[:50].strip() or "conversation"
    return PlainTextResponse(
        content=markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}.md"'},
    )
