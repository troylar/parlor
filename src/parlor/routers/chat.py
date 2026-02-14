"""Chat streaming endpoint with SSE."""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import uuid as uuid_mod
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from ..models import ChatRequest
from ..services import storage
from ..services.ai_service import AIService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

MAX_FILES_PER_REQUEST = 10

SAFE_INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

_cancel_events: dict[str, set[asyncio.Event]] = defaultdict(set)


def _validate_uuid(value: str) -> str:
    try:
        uuid_mod.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")
    return value


def _get_db(request: Request):
    """Resolve database connection from optional ?db= query parameter."""
    db_name = request.query_params.get("db")
    if hasattr(request.app.state, "db_manager"):
        return request.app.state.db_manager.get(db_name)
    return request.app.state.db


def _get_ai_service(request: Request, model_override: str | None = None) -> AIService:
    config = request.app.state.config
    if model_override:
        ai_config = copy.copy(config.ai)
        ai_config.model = model_override
        return AIService(ai_config)
    return AIService(config.ai)


@router.post("/conversations/{conversation_id}/chat")
async def chat(conversation_id: str, request: Request) -> EventSourceResponse:
    _validate_uuid(conversation_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    content_type = request.headers.get("content-type", "")
    regenerate = False
    if "multipart/form-data" in content_type:
        form = await request.form()
        message_text = str(form.get("message", ""))
        files = form.getlist("files")
        if len(files) > MAX_FILES_PER_REQUEST:
            raise HTTPException(status_code=400, detail=f"Maximum {MAX_FILES_PER_REQUEST} files per request")
    else:
        body = ChatRequest(**(await request.json()))
        message_text = body.message
        regenerate = body.regenerate
        files = []

    if regenerate:
        existing = storage.list_messages(db, conversation_id)
        if not existing:
            raise HTTPException(status_code=400, detail="No messages to regenerate from")

    user_msg = None
    attachment_contents: list[dict[str, Any]] = []

    if not regenerate:
        user_msg = storage.create_message(db, conversation_id, "user", message_text)

        if files:
            data_dir = request.app.state.config.app.data_dir
            for f in files:
                if hasattr(f, "read"):
                    file_data = await f.read()
                    att = storage.save_attachment(
                        db,
                        user_msg["id"],
                        conversation_id,
                        f.filename or "unnamed",
                        f.content_type or "application/octet-stream",
                        file_data,
                        data_dir,
                    )
                    if f.content_type and f.content_type.startswith("image/"):
                        b64_data = base64.b64encode(file_data).decode("ascii")
                        attachment_contents.append(
                            {
                                "type": "image_url",
                                "image_url": f"data:{f.content_type};base64,{b64_data}",
                            }
                        )
                    elif f.content_type and f.content_type.startswith("text"):
                        try:
                            attachment_contents.append(
                                {
                                    "type": "text",
                                    "filename": f.filename,
                                    "content": file_data.decode("utf-8", errors="replace"),
                                }
                            )
                        except Exception:
                            pass

    cancel_event = asyncio.Event()
    _cancel_events[conversation_id].add(cancel_event)

    # Resolve model override: conversation model > project model > global default
    model_override = conv.get("model") or None
    project_instructions: str | None = None
    project_id = conv.get("project_id")
    if project_id:
        project = storage.get_project(db, project_id)
        if project:
            if not model_override and project.get("model"):
                model_override = project["model"]
            if project.get("instructions"):
                project_instructions = project["instructions"]

    ai_service = _get_ai_service(request, model_override=model_override)

    # Build message history
    history = storage.list_messages(db, conversation_id)
    ai_messages: list[dict[str, Any]] = []
    for msg in history:
        content: Any = msg["content"]
        if user_msg and msg["id"] == user_msg["id"] and attachment_contents:
            parts: list[dict[str, Any]] = [{"type": "text", "text": msg["content"]}]
            for att in attachment_contents:
                if att["type"] == "image_url":
                    parts.append({"type": "image_url", "image_url": {"url": att["image_url"]}})
                elif att["type"] == "text":
                    parts.append({"type": "text", "text": f"[Attached file: {att['filename']}]\n{att['content']}"})
            content = parts
        ai_messages.append({"role": msg["role"], "content": content})

    # Get MCP tools if available
    mcp_manager = request.app.state.mcp_manager
    tools = mcp_manager.get_openai_tools() if mcp_manager else None

    is_first_message = not regenerate and len(history) <= 1
    first_user_text = message_text

    async def _mcp_tool_executor(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not mcp_manager:
            raise ValueError(f"No tool executor available for '{tool_name}'")
        return await mcp_manager.call_tool(tool_name, arguments)

    from ..services.agent_loop import run_agent_loop

    async def event_generator():
        nonlocal ai_messages
        current_assistant_msg = None
        _pending_tool_inputs: dict[str, Any] = {}
        try:
            async for agent_event in run_agent_loop(
                ai_service=ai_service,
                messages=ai_messages,
                tool_executor=_mcp_tool_executor,
                tools_openai=tools,
                cancel_event=cancel_event,
                extra_system_prompt=project_instructions,
            ):
                kind = agent_event.kind
                data = agent_event.data

                if kind == "token":
                    yield {"event": "token", "data": json.dumps(data)}

                elif kind == "tool_call_start":
                    # Track input args for DB persistence
                    _pending_tool_inputs[data["id"]] = data["arguments"]
                    yield {
                        "event": "tool_call_start",
                        "data": json.dumps(
                            {
                                "id": data["id"],
                                "tool_name": data["tool_name"],
                                "server_name": "",
                                "input": data["arguments"],
                            }
                        ),
                    }

                elif kind == "assistant_message":
                    current_assistant_msg = storage.create_message(
                        db, conversation_id, "assistant", data["content"]
                    )

                elif kind == "tool_call_end":
                    if current_assistant_msg and mcp_manager:
                        tool_input = _pending_tool_inputs.pop(data["id"], {})
                        storage.create_tool_call(
                            db,
                            current_assistant_msg["id"],
                            data["tool_name"],
                            mcp_manager.get_tool_server_name(data["tool_name"]),
                            tool_input,
                            data["id"],
                        )
                        storage.update_tool_call(
                            db, data["id"], data["output"], data["status"]
                        )
                    yield {
                        "event": "tool_call_end",
                        "data": json.dumps(
                            {"id": data["id"], "output": data["output"], "status": data["status"]}
                        ),
                    }

                elif kind == "error":
                    yield {"event": "error", "data": json.dumps(data)}

                elif kind == "done":
                    if is_first_message and conv["title"] == "New Conversation":
                        title = await ai_service.generate_title(first_user_text)
                        storage.update_conversation_title(db, conversation_id, title)
                        yield {"event": "title", "data": json.dumps({"title": title})}
                    yield {"event": "done", "data": json.dumps({})}

        except Exception:
            logger.exception("Chat stream error")
            yield {"event": "error", "data": json.dumps({"message": "An internal error occurred"})}
        finally:
            _cancel_events.get(conversation_id, set()).discard(cancel_event)
            if not _cancel_events.get(conversation_id):
                _cancel_events.pop(conversation_id, None)

    return EventSourceResponse(event_generator())


@router.post("/conversations/{conversation_id}/stop")
async def stop_generation(conversation_id: str, request: Request):
    _validate_uuid(conversation_id)
    db = _get_db(request)
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    events = _cancel_events.get(conversation_id, set())
    for event in events:
        event.set()
    return {"status": "stopped"}


@router.get("/attachments/{attachment_id}")
async def get_attachment(attachment_id: str, request: Request):
    _validate_uuid(attachment_id)
    db = _get_db(request)
    att = storage.get_attachment(db, attachment_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    data_dir = request.app.state.config.app.data_dir
    file_path = (data_dir / att["storage_path"]).resolve()
    if not file_path.is_relative_to(data_dir.resolve()):
        raise HTTPException(status_code=403, detail="Access denied")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Attachment file missing")
    from fastapi.responses import FileResponse

    media_type = att["mime_type"]
    disposition = "inline" if media_type in SAFE_INLINE_TYPES else "attachment"
    return FileResponse(
        str(file_path),
        media_type=media_type,
        filename=att["filename"],
        headers={"Content-Disposition": f'{disposition}; filename="{att["filename"]}"'},
    )
