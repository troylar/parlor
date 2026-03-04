"""SSE broadcast endpoint for real-time collaboration."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from anteroom.config import RateLimitConfig

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
async def event_stream(
    request: Request, db: str = "personal", conversation_id: str | None = None, client_id: str = ""
) -> Any:
    """Long-lived SSE connection for real-time updates.

    Subscribes to:
    - ``global:{db}`` always (conversation list changes)
    - ``conversation:{conversation_id}`` when viewing a specific conversation
    """
    from sse_starlette.sse import EventSourceResponse

    _validate_db_name(db)
    if conversation_id:
        _validate_uuid(conversation_id)

    rl_config: RateLimitConfig = getattr(request.app.state, "rate_limit_config", RateLimitConfig())
    sse_retry_ms: int = rl_config.sse_retry_ms

    if not hasattr(request.app.state, "event_bus"):

        async def empty() -> Any:
            yield {"event": "error", "data": json.dumps({"message": "Event bus not available"}), "retry": sse_retry_ms}

        return EventSourceResponse(empty())

    event_bus = request.app.state.event_bus
    global_channel = f"global:{db}"
    global_queue = event_bus.subscribe(global_channel)

    conv_channel: str | None = None
    conv_queue: asyncio.Queue[dict[str, Any]] | None = None
    if conversation_id:
        conv_channel = f"conversation:{conversation_id}"
        conv_queue = event_bus.subscribe(conv_channel)

    async def generate() -> Any:
        # Send retry: hint and connected event immediately so the browser
        # knows the backoff interval regardless of queue state.
        yield {"event": "connected", "data": "{}", "retry": sse_retry_ms}

        # Build a set of queue-get tasks to await concurrently instead of
        # busy-polling with sleep(0.05).  This eliminates ~20 wakeups/sec
        # per connected client and lets the event loop stay idle.
        queues = [global_queue]
        if conv_queue is not None:
            queues.append(conv_queue)

        active_tasks: set[asyncio.Task[Any]] = set()
        try:
            while True:
                # Create a get task for each queue we're subscribed to
                get_tasks = {asyncio.create_task(q.get()): q for q in queues}
                disconnect_task = asyncio.create_task(asyncio.sleep(5.0))
                all_tasks = set(get_tasks.keys()) | {disconnect_task}
                active_tasks = all_tasks

                done, pending = await asyncio.wait(all_tasks, return_when=asyncio.FIRST_COMPLETED)

                # Cancel pending tasks
                for p in pending:
                    p.cancel()
                    try:
                        await p
                    except (asyncio.CancelledError, Exception):
                        pass
                active_tasks = set()

                # If only the sleep completed, check disconnect and loop
                if disconnect_task in done and not (done - {disconnect_task}):
                    if await request.is_disconnected():
                        break
                    continue

                # Yield all events that arrived
                for task in done:
                    if task is disconnect_task:
                        continue
                    try:
                        event = task.result()
                    except (asyncio.CancelledError, Exception):
                        continue
                    yield {
                        "event": event.get("type", "message"),
                        "data": json.dumps(event.get("data", {})),
                    }
        finally:
            for t in active_tasks:
                t.cancel()
            event_bus.unsubscribe(global_channel, global_queue)
            if conv_channel and conv_queue:
                event_bus.unsubscribe(conv_channel, conv_queue)

    return EventSourceResponse(generate())
