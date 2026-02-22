"""OpenAI-compatible proxy endpoints.

Exposes /v1/chat/completions and /v1/models so external tools that speak the
OpenAI SDK can route requests through Anteroom to the configured upstream API.

This is a passthrough proxy — requests are forwarded as-is with minimal
transformation.  Auth uses the same Bearer token as the rest of Anteroom.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..services.ai_service import create_ai_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["proxy"])


@router.get("/models")
async def list_models(request: Request) -> JSONResponse:
    """Return the configured model as an OpenAI-compatible models list."""
    config = request.app.state.config
    model = config.ai.model
    return JSONResponse(
        content={
            "object": "list",
            "data": [
                {
                    "id": model,
                    "object": "model",
                    "created": 0,
                    "owned_by": "anteroom-proxy",
                }
            ],
        }
    )


@router.post("/chat/completions", response_model=None)
async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    """Proxy a chat completion request to the upstream OpenAI-compatible API."""
    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type:
        return JSONResponse(status_code=415, content={"error": {"message": "Content-Type must be application/json"}})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {"message": "Invalid JSON body"}})

    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": {"message": "Request body must be a JSON object"}})

    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) == 0:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "'messages' is required and must be a non-empty array"}},
        )

    config = request.app.state.config
    ai_service = create_ai_service(config.ai)

    model = body.get("model", config.ai.model)
    stream = body.get("stream", False)

    # Build kwargs for the upstream call, forwarding supported parameters
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }
    for param in ("temperature", "top_p", "max_tokens", "stop", "frequency_penalty", "presence_penalty", "n"):
        if param in body:
            kwargs[param] = body[param]

    try:
        if stream:
            return await _handle_streaming(ai_service, kwargs)
        else:
            return await _handle_non_streaming(ai_service, kwargs)
    except Exception as e:
        logger.exception("Proxy upstream error")
        return JSONResponse(
            status_code=502,
            content={"error": {"message": f"Upstream API error: {type(e).__name__}"}},
        )


async def _handle_non_streaming(ai_service, kwargs: dict) -> JSONResponse:
    """Forward a non-streaming request and return the response."""
    response = await ai_service.client.chat.completions.create(**kwargs)
    return JSONResponse(content=response.model_dump())


async def _handle_streaming(ai_service, kwargs: dict) -> StreamingResponse:
    """Forward a streaming request and relay SSE chunks."""
    stream = await ai_service.client.chat.completions.create(**kwargs)

    async def event_generator():
        try:
            async for chunk in stream:
                data = chunk.model_dump()
                yield f"data: {json.dumps(data)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception:
            logger.exception("Proxy stream error")
            error_chunk = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "choices": [],
                "error": {"message": "Upstream stream error"},
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
