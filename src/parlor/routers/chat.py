"""Chat streaming endpoint with SSE."""

from __future__ import annotations

import asyncio
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


def _get_ai_service(request: Request) -> AIService:
    config = request.app.state.config
    return AIService(config.ai)


@router.post("/conversations/{conversation_id}/chat")
async def chat(conversation_id: str, request: Request) -> EventSourceResponse:
    _validate_uuid(conversation_id)
    db = request.app.state.db
    conv = storage.get_conversation(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        message_text = str(form.get("message", ""))
        files = form.getlist("files")
        if len(files) > MAX_FILES_PER_REQUEST:
            raise HTTPException(status_code=400, detail=f"Maximum {MAX_FILES_PER_REQUEST} files per request")
    else:
        body = ChatRequest(**(await request.json()))
        message_text = body.message
        files = []

    user_msg = storage.create_message(db, conversation_id, "user", message_text)

    attachment_contents: list[dict[str, Any]] = []
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
                if f.content_type and f.content_type.startswith("text"):
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

    ai_service = _get_ai_service(request)

    # Build message history
    history = storage.list_messages(db, conversation_id)
    ai_messages: list[dict[str, Any]] = []
    for msg in history:
        content: Any = msg["content"]
        if msg["id"] == user_msg["id"] and attachment_contents:
            content = msg["content"]
            for att in attachment_contents:
                content += f"\n\n[Attached file: {att['filename']}]\n{att['content']}"
        ai_messages.append({"role": msg["role"], "content": content})

    # Get MCP tools if available
    mcp_manager = request.app.state.mcp_manager
    tools = mcp_manager.get_openai_tools() if mcp_manager else None

    is_first_message = len(history) <= 1
    first_user_text = message_text

    async def event_generator():
        nonlocal ai_messages, tools
        assistant_content = ""
        try:
            while True:
                tool_calls_pending: list[dict[str, Any]] = []

                async for event in ai_service.stream_chat(ai_messages, tools=tools, cancel_event=cancel_event):
                    etype = event["event"]
                    if etype == "token":
                        assistant_content += event["data"]["content"]
                        yield {"event": etype, "data": json.dumps(event["data"])}
                    elif etype == "tool_call":
                        tool_calls_pending.append(event["data"])
                        yield {
                            "event": "tool_call_start",
                            "data": json.dumps(
                                {
                                    "id": event["data"]["id"],
                                    "tool_name": event["data"]["function_name"],
                                    "server_name": "",
                                    "input": event["data"]["arguments"],
                                }
                            ),
                        }
                    elif etype == "error":
                        yield {"event": "error", "data": json.dumps(event["data"])}
                        return
                    elif etype == "done":
                        break

                if not tool_calls_pending:
                    break

                # Save assistant message with tool calls
                assistant_msg = storage.create_message(db, conversation_id, "assistant", assistant_content)
                ai_messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_content,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["function_name"],
                                    "arguments": json.dumps(tc["arguments"]),
                                },
                            }
                            for tc in tool_calls_pending
                        ],
                    }
                )

                # Execute tool calls via MCP
                for tc in tool_calls_pending:
                    if mcp_manager:
                        storage.create_tool_call(
                            db,
                            assistant_msg["id"],
                            tc["function_name"],
                            "",
                            tc["arguments"],
                            tc["id"],
                        )
                        try:
                            result = await mcp_manager.call_tool(tc["function_name"], tc["arguments"])
                            storage.update_tool_call(db, tc["id"], result, "success")
                            yield {
                                "event": "tool_call_end",
                                "data": json.dumps({"id": tc["id"], "output": result, "status": "success"}),
                            }
                            ai_messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc["id"],
                                    "content": json.dumps(result),
                                }
                            )
                        except Exception as e:
                            storage.update_tool_call(db, tc["id"], {"error": str(e)}, "error")
                            yield {
                                "event": "tool_call_end",
                                "data": json.dumps({"id": tc["id"], "output": {"error": str(e)}, "status": "error"}),
                            }
                            ai_messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc["id"],
                                    "content": json.dumps({"error": str(e)}),
                                }
                            )

                assistant_content = ""

            # Save final assistant message
            if assistant_content:
                storage.create_message(db, conversation_id, "assistant", assistant_content)

            # Auto-generate title for first message
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
    db = request.app.state.db
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
    db = request.app.state.db
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
