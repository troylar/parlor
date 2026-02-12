"""Conversation CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from ..models import ConversationUpdate
from ..services import storage
from ..services.export import export_conversation_markdown

router = APIRouter(tags=["conversations"])


@router.get("/conversations")
async def list_conversations(request: Request, search: str | None = None):
    db = request.app.state.db
    return storage.list_conversations(db, search=search)


@router.post("/conversations", status_code=201)
async def create_conversation(request: Request):
    db = request.app.state.db
    return storage.create_conversation(db)


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request):
    db = request.app.state.db
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = storage.list_messages(db, conversation_id)
    return {**conv, "messages": messages}


@router.patch("/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, body: ConversationUpdate, request: Request):
    db = request.app.state.db
    conv = storage.update_conversation_title(db, conversation_id, body.title)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, request: Request):
    db = request.app.state.db
    data_dir = request.app.state.config.app.data_dir
    deleted = storage.delete_conversation(db, conversation_id, data_dir)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=204)


@router.get("/conversations/{conversation_id}/export")
async def export_conversation(conversation_id: str, request: Request):
    db = request.app.state.db
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = storage.list_messages(db, conversation_id)
    markdown = export_conversation_markdown(conv, messages)
    filename = conv["title"].replace('"', "").replace("'", "")[:50]
    return PlainTextResponse(
        content=markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}.md"'},
    )
