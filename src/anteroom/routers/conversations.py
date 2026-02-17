"""Conversation CRUD endpoints."""

from __future__ import annotations

import asyncio
import re
import unicodedata
import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response

from ..models import (
    CanvasCreate,
    CanvasUpdate,
    ConversationUpdate,
    DocumentContent,
    EntryCreate,
    FolderCreate,
    FolderUpdate,
    ForkRequest,
    MessageEdit,
    RewindRequest,
    RewindResponse,
    TagCreate,
    TagUpdate,
)
from ..services import storage
from ..services.export import export_conversation_markdown
from ..services.rewind import rewind_conversation as rewind_service

router = APIRouter(tags=["conversations"])


def _validate_uuid(value: str) -> str:
    try:
        uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")
    return value


def _require_json(request: Request) -> None:
    """Reject requests without application/json Content-Type on state-changing endpoints."""
    ct = request.headers.get("content-type", "")
    if not ct.startswith("application/json"):
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")


def _get_db(request: Request):
    """Resolve database connection from optional ?db= query parameter."""
    db_name = request.query_params.get("db")
    if hasattr(request.app.state, "db_manager"):
        return request.app.state.db_manager.get(db_name)
    return request.app.state.db


def _get_db_name(request: Request) -> str:
    """Return validated database name from query param."""
    import re

    db_name = request.query_params.get("db") or "personal"
    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", db_name):
        return "personal"
    return db_name


def _get_identity(request: Request) -> tuple[str | None, str | None]:
    identity = getattr(request.app.state.config, "identity", None)
    if identity:
        return identity.user_id, identity.display_name
    return None, None


def _get_event_bus(request: Request):
    return getattr(request.app.state, "event_bus", None)


def _get_client_id(request: Request) -> str:
    """Return validated client ID from header. Must be a valid UUID or empty."""
    raw = request.headers.get("x-client-id", "")
    if not raw:
        return ""
    try:
        uuid.UUID(raw)
        return raw
    except ValueError:
        return ""


@router.get("/conversations")
async def list_conversations(
    request: Request,
    search: str | None = None,
    project_id: str | None = None,
    type: str | None = Query(default=None, pattern=r"^(chat|note|document)$"),
):
    if project_id:
        _validate_uuid(project_id)
    db = _get_db(request)
    return storage.list_conversations(db, search=search, project_id=project_id, conversation_type=type)


@router.post("/conversations", status_code=201)
async def create_conversation(request: Request):
    _require_json(request)
    db = _get_db(request)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    project_id = body.get("project_id") if isinstance(body, dict) else None
    if project_id:
        _validate_uuid(project_id)
    conv_type = body.get("type", "chat") if isinstance(body, dict) else "chat"
    if conv_type not in ("chat", "note", "document"):
        raise HTTPException(status_code=400, detail="Invalid conversation type")
    title = body.get("title", "New Conversation") if isinstance(body, dict) else "New Conversation"
    if len(title) > 200:
        raise HTTPException(status_code=400, detail="Title must be 200 characters or fewer")
    uid, uname = _get_identity(request)
    conv = storage.create_conversation(
        db, title=title, project_id=project_id, user_id=uid, user_display_name=uname, conversation_type=conv_type
    )

    event_bus = _get_event_bus(request)
    if event_bus:
        asyncio.create_task(
            event_bus.publish(
                f"global:{_get_db_name(request)}",
                {
                    "type": "conversation_created",
                    "data": {
                        "conversation_id": conv["id"],
                        "title": conv["title"],
                        "client_id": _get_client_id(request),
                    },
                },
            )
        )

    return conv


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

        event_bus = _get_event_bus(request)
        if event_bus:
            asyncio.create_task(
                event_bus.publish(
                    f"global:{_get_db_name(request)}",
                    {
                        "type": "title_changed",
                        "data": {
                            "conversation_id": conversation_id,
                            "title": body.title,
                            "client_id": _get_client_id(request),
                        },
                    },
                )
            )

    if body.type is not None:
        conv = storage.update_conversation_type(db, conversation_id, body.type)
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
    # Clean up embeddings before deleting conversation
    try:
        storage.delete_embeddings_for_conversation(db, conversation_id)
    except Exception:
        pass  # Non-critical; table may not exist

    # SECURITY-REVIEW: conversation_id is UUID-validated above; path is data_dir/attachments/<uuid>
    deleted = storage.delete_conversation(db, conversation_id, data_dir)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")

    event_bus = _get_event_bus(request)
    if event_bus:
        asyncio.create_task(
            event_bus.publish(
                f"global:{_get_db_name(request)}",
                {
                    "type": "conversation_deleted",
                    "data": {
                        "conversation_id": conversation_id,
                        "client_id": _get_client_id(request),
                    },
                },
            )
        )

    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/entries", status_code=201)
async def create_entry(conversation_id: str, body: EntryCreate, request: Request):
    _require_json(request)
    _validate_uuid(conversation_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.get("type") not in ("note", "document"):
        raise HTTPException(status_code=400, detail="Entries can only be added to note or document conversations")
    uid, uname = _get_identity(request)
    msg = storage.create_message(db, conversation_id, "user", body.content, user_id=uid, user_display_name=uname)

    _embedding_worker = getattr(request.app.state, "embedding_worker", None)
    if _embedding_worker:
        asyncio.create_task(_embedding_worker.embed_message(msg["id"], body.content, conversation_id))

    event_bus = _get_event_bus(request)
    if event_bus:
        asyncio.create_task(
            event_bus.publish(
                f"conversation:{conversation_id}",
                {
                    "type": "new_message",
                    "data": {
                        "conversation_id": conversation_id,
                        "message_id": msg["id"],
                        "role": "user",
                        "content": body.content,
                        "position": msg["position"],
                        "client_id": _get_client_id(request),
                    },
                },
            )
        )

    return msg


@router.post("/conversations/{conversation_id}/fork", status_code=201)
async def fork_conversation(conversation_id: str, body: ForkRequest, request: Request):
    _require_json(request)
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


@router.delete("/conversations/{conversation_id}/messages/{message_id}", status_code=204)
async def delete_message(conversation_id: str, message_id: str, request: Request):
    _validate_uuid(conversation_id)
    _validate_uuid(message_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.get("type") not in ("note", "document"):
        raise HTTPException(
            status_code=400, detail="Individual message deletion is only supported for note and document conversations"
        )
    deleted = storage.delete_message(db, conversation_id, message_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Message not found")
    return Response(status_code=204)


@router.put("/conversations/{conversation_id}/document")
async def replace_document(conversation_id: str, body: DocumentContent, request: Request):
    _require_json(request)
    _validate_uuid(conversation_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.get("type") != "document":
        raise HTTPException(status_code=400, detail="Only document conversations support full content replacement")
    uid, uname = _get_identity(request)
    msg = storage.replace_document_content(db, conversation_id, body.content, user_id=uid, user_display_name=uname)
    return msg


@router.delete("/conversations/{conversation_id}/messages", status_code=204)
async def delete_messages_after(conversation_id: str, request: Request, after_position: int = Query(ge=0)):
    _validate_uuid(conversation_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    data_dir = request.app.state.config.app.data_dir
    # SECURITY-REVIEW: after_position is int via Query(ge=0); all queries use parameterized ?
    storage.delete_messages_after_position(db, conversation_id, after_position, data_dir)
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/rewind")
async def rewind_conversation(conversation_id: str, body: RewindRequest, request: Request) -> RewindResponse:
    _validate_uuid(conversation_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msgs = storage.list_messages(db, conversation_id)
    positions = [m["position"] for m in msgs]
    if body.to_position not in positions:
        raise HTTPException(status_code=400, detail="Invalid position")

    data_dir = request.app.state.config.app.data_dir
    # SECURITY-REVIEW: to_position validated against known positions; parameterized queries throughout
    result = await rewind_service(
        db=db,
        conversation_id=conversation_id,
        to_position=body.to_position,
        undo_files=body.undo_files,
        data_dir=data_dir,
    )

    return RewindResponse(
        deleted_messages=result.deleted_messages,
        reverted_files=result.reverted_files,
        skipped_files=result.skipped_files,
    )


# --- Canvas ---
# Rate limiting: canvas endpoints are covered by the global 120 req/min middleware in app.py


@router.post("/conversations/{conversation_id}/canvas", status_code=201)
async def create_canvas(conversation_id: str, body: CanvasCreate, request: Request):
    _require_json(request)
    _validate_uuid(conversation_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    existing = storage.get_canvas_for_conversation(db, conversation_id)
    if existing:
        raise HTTPException(status_code=409, detail="Canvas already exists for this conversation")
    uid, uname = _get_identity(request)
    canvas = storage.create_canvas(
        db,
        conversation_id,
        title=body.title,
        content=body.content,
        language=body.language,
        user_id=uid,
        user_display_name=uname,
    )
    return canvas


@router.get("/conversations/{conversation_id}/canvas")
async def get_canvas(conversation_id: str, request: Request):
    _validate_uuid(conversation_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    canvas = storage.get_canvas_for_conversation(db, conversation_id)
    if not canvas:
        raise HTTPException(status_code=404, detail="No canvas for this conversation")
    return canvas


@router.patch("/conversations/{conversation_id}/canvas")
async def update_canvas(conversation_id: str, body: CanvasUpdate, request: Request):
    _require_json(request)
    _validate_uuid(conversation_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    canvas = storage.get_canvas_for_conversation(db, conversation_id)
    if not canvas:
        raise HTTPException(status_code=404, detail="No canvas for this conversation")
    if body.content is None and body.title is None:
        raise HTTPException(status_code=400, detail="At least one of 'content' or 'title' must be provided")
    updated = storage.update_canvas(db, canvas["id"], content=body.content, title=body.title)
    if not updated:
        raise HTTPException(status_code=404, detail="Canvas not found")
    return updated


@router.delete("/conversations/{conversation_id}/canvas", status_code=204)
async def delete_canvas(conversation_id: str, request: Request):
    _validate_uuid(conversation_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    canvas = storage.get_canvas_for_conversation(db, conversation_id)
    if not canvas:
        raise HTTPException(status_code=404, detail="No canvas for this conversation")
    storage.delete_canvas(db, canvas["id"])
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
    uid, uname = _get_identity(request)
    return storage.create_folder(
        db,
        name=body.name,
        parent_id=body.parent_id,
        project_id=body.project_id,
        user_id=uid,
        user_display_name=uname,
    )


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
    # SECURITY-REVIEW: folder_id is UUID-validated above; parameterized queries in storage
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
    uid, uname = _get_identity(request)
    return storage.create_tag(db, name=body.name, color=body.color, user_id=uid, user_display_name=uname)


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
