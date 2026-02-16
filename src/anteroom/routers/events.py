"""SSE broadcast endpoint for real-time collaboration."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])

_DB_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _validate_db_name(name: str) -> str:
    if not _DB_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid database name")
    return name


def _validate_uuid(value: str) -> str:
    try:
        uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")
    return value


@router.get("/events")
async def event_stream(request: Request, db: str = "personal", conversation_id: str | None = None, client_id: str = ""):
    """Long-lived SSE connection for real-time updates.

    Subscribes to:
    - ``global:{db}`` always (conversation list changes)
    - ``conversation:{conversation_id}`` when viewing a specific conversation
    """
    _validate_db_name(db)
    if conversation_id:
        _validate_uuid(conversation_id)

    if not hasattr(request.app.state, "event_bus"):
        from sse_starlette.sse import EventSourceResponse

        async def empty():
            yield {"event": "error", "data": json.dumps({"message": "Event bus not available"})}

        return EventSourceResponse(empty())

    event_bus = request.app.state.event_bus
    global_channel = f"global:{db}"
    global_queue = event_bus.subscribe(global_channel)

    conv_channel: str | None = None
    conv_queue: asyncio.Queue[dict[str, Any]] | None = None
    if conversation_id:
        conv_channel = f"conversation:{conversation_id}"
        conv_queue = event_bus.subscribe(conv_channel)

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    break

                # Check both queues with a short timeout
                event = None
                try:
                    event = global_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

                if event is None and conv_queue is not None:
                    try:
                        event = conv_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass

                if event is None:
                    # Wait briefly before checking again
                    await asyncio.sleep(0.05)
                    # Send keepalive every ~15s (300 * 0.05s)
                    continue

                yield {"event": event.get("type", "message"), "data": json.dumps(event.get("data", {}))}
        finally:
            event_bus.unsubscribe(global_channel, global_queue)
            if conv_channel and conv_queue:
                event_bus.unsubscribe(conv_channel, conv_queue)

    return EventSourceResponse(generate())
