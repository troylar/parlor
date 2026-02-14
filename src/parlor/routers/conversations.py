"""Conversation CRUD endpoints."""

from __future__ import annotations

import re
import unicodedata
import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response

from ..models import ConversationUpdate, FolderCreate, FolderUpdate, ForkRequest, MessageEdit, TagCreate, TagUpdate
from ..services import storage
from ..services.export import export_conversation_markdown

router = APIRouter(tags=["conversations"])


def _validate_uuid(value: str) -> str:
    try:
        uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")
    return value


def _get_db(request: Request):
    """Resolve database connection from optional ?db= query parameter."""
    db_name = request.query_params.get("db")
    if hasattr(request.app.state, "db_manager"):
        return request.app.state.db_manager.get(db_name)
    return request.app.state.db


@router.get("/conversations")
async def list_conversations(request: Request, search: str | None = None, project_id: str | None = None):
    if project_id:
        _validate_uuid(project_id)
    db = _get_db(request)
    return storage.list_conversations(db, search=search, project_id=project_id)


@router.post("/conversations", status_code=201)
async def create_conversation(request: Request):
    db = _get_db(request)
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
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = storage.list_messages(db, conversation_id)
    return {**conv, "messages": messages}


@router.patch("/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, body: ConversationUpdate, request: Request):
    _validate_uuid(conversation_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if body.title is not None:
        conv = storage.update_conversation_title(db, conversation_id, body.title)
    if body.model is not None:
        conv = storage.update_conversation_model(db, conversation_id, body.model)
    if body.folder_id is not None:
        folder_id = body.folder_id if body.folder_id != "" else None
        storage.move_conversation_to_folder(db, conversation_id, folder_id)
        conv = storage.get_conversation(db, conversation_id)
    return conv


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, request: Request):
    _validate_uuid(conversation_id)
    db = _get_db(request)
    data_dir = request.app.state.config.app.data_dir
    deleted = storage.delete_conversation(db, conversation_id, data_dir)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/fork", status_code=201)
async def fork_conversation(conversation_id: str, body: ForkRequest, request: Request):
    _validate_uuid(conversation_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = storage.list_messages(db, conversation_id)
    positions = [m["position"] for m in msgs]
    if body.up_to_position not in positions:
        raise HTTPException(status_code=400, detail="Invalid position")
    new_conv = storage.fork_conversation(db, conversation_id, body.up_to_position)
    return new_conv


@router.patch("/conversations/{conversation_id}/messages/{message_id}")
async def update_message(conversation_id: str, message_id: str, body: MessageEdit, request: Request):
    _validate_uuid(conversation_id)
    _validate_uuid(message_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    updated = storage.update_message_content(db, conversation_id, message_id, body.content)
    if not updated:
        raise HTTPException(status_code=404, detail="Message not found")
    return updated


@router.delete("/conversations/{conversation_id}/messages", status_code=204)
async def delete_messages_after(conversation_id: str, request: Request, after_position: int = Query(ge=0)):
    _validate_uuid(conversation_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    data_dir = request.app.state.config.app.data_dir
    storage.delete_messages_after_position(db, conversation_id, after_position, data_dir)
    return Response(status_code=204)


# --- Folders ---


@router.get("/folders")
async def list_folders(request: Request, project_id: str | None = None):
    if project_id:
        _validate_uuid(project_id)
    db = _get_db(request)
    return storage.list_folders(db, project_id=project_id)


@router.post("/folders", status_code=201)
async def create_folder(body: FolderCreate, request: Request):
    if body.parent_id:
        _validate_uuid(body.parent_id)
    if body.project_id:
        _validate_uuid(body.project_id)
    db = _get_db(request)
    return storage.create_folder(db, name=body.name, parent_id=body.parent_id, project_id=body.project_id)


@router.patch("/folders/{folder_id}")
async def update_folder(folder_id: str, body: FolderUpdate, request: Request):
    _validate_uuid(folder_id)
    if body.parent_id is not None and body.parent_id != "":
        _validate_uuid(body.parent_id)
    db = _get_db(request)
    parent_id = ... if body.parent_id is None else (body.parent_id or None)
    updated = storage.update_folder(
        db,
        folder_id,
        name=body.name,
        parent_id=parent_id,
        collapsed=body.collapsed,
        position=body.position,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Folder not found")
    return updated


@router.delete("/folders/{folder_id}", status_code=204)
async def delete_folder(folder_id: str, request: Request):
    _validate_uuid(folder_id)
    db = _get_db(request)
    deleted = storage.delete_folder(db, folder_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Folder not found")
    return Response(status_code=204)


# --- Tags ---


@router.get("/tags")
async def list_tags(request: Request):
    db = _get_db(request)
    return storage.list_tags(db)


@router.post("/tags", status_code=201)
async def create_tag(body: TagCreate, request: Request):
    db = _get_db(request)
    return storage.create_tag(db, name=body.name, color=body.color)


@router.patch("/tags/{tag_id}")
async def update_tag(tag_id: str, body: TagUpdate, request: Request):
    _validate_uuid(tag_id)
    db = _get_db(request)
    updated = storage.update_tag(db, tag_id, name=body.name, color=body.color)
    if not updated:
        raise HTTPException(status_code=404, detail="Tag not found")
    return updated


@router.delete("/tags/{tag_id}", status_code=204)
async def delete_tag(tag_id: str, request: Request):
    _validate_uuid(tag_id)
    db = _get_db(request)
    deleted = storage.delete_tag(db, tag_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Tag not found")
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/tags/{tag_id}", status_code=201)
async def add_tag(conversation_id: str, tag_id: str, request: Request):
    _validate_uuid(conversation_id)
    _validate_uuid(tag_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    storage.add_tag_to_conversation(db, conversation_id, tag_id)
    return storage.get_conversation_tags(db, conversation_id)


@router.delete("/conversations/{conversation_id}/tags/{tag_id}", status_code=204)
async def remove_tag(conversation_id: str, tag_id: str, request: Request):
    _validate_uuid(conversation_id)
    _validate_uuid(tag_id)
    db = _get_db(request)
    storage.remove_tag_from_conversation(db, conversation_id, tag_id)
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/copy", status_code=201)
async def copy_conversation(conversation_id: str, request: Request, target_db: str = Query(...)):
    _validate_uuid(conversation_id)
    source = _get_db(request)
    conv = storage.get_conversation(source, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not hasattr(request.app.state, "db_manager"):
        raise HTTPException(status_code=400, detail="Database manager not available")
    try:
        target = request.app.state.db_manager.get(target_db)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Target database '{target_db}' not found")
    copied = storage.copy_conversation_to_db(source, target, conversation_id)
    if not copied:
        raise HTTPException(status_code=500, detail="Failed to copy conversation")
    return copied


@router.get("/conversations/{conversation_id}/export")
async def export_conversation(conversation_id: str, request: Request):
    _validate_uuid(conversation_id)
    db = _get_db(request)
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
