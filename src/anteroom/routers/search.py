"""Semantic and keyword search endpoints."""

from __future__ import annotations

import uuid as uuid_mod
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..services import storage

router = APIRouter(tags=["search"])


def _get_db(request: Request) -> Any:
    db_name = request.query_params.get("db")
    if hasattr(request.app.state, "db_manager"):
        return request.app.state.db_manager.get(db_name)
    return request.app.state.db


@router.get("/search/semantic")
async def semantic_search(
    request: Request,
    q: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Search messages by semantic similarity."""
    if conversation_id:
        try:
            uuid_mod.UUID(conversation_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid conversation_id format")

    embedding_service = getattr(request.app.state, "embedding_service", None)
    if not embedding_service:
        raise HTTPException(status_code=503, detail="Embedding service not available")

    vec_enabled = getattr(request.app.state, "vec_enabled", False)
    if not vec_enabled:
        raise HTTPException(status_code=503, detail="Vector search not available (sqlite-vec not loaded)")

    query_embedding = await embedding_service.embed(q)
    if query_embedding is None:
        raise HTTPException(status_code=500, detail="Failed to generate query embedding")

    db = _get_db(request)
    results = storage.search_similar_messages(db, query_embedding, limit=limit, conversation_id=conversation_id)

    # Group by conversation
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        grouped[r["conversation_id"]].append(
            {
                "id": r["message_id"],
                "content": r["content"],
                "role": r["role"],
                "distance": r["distance"],
            }
        )

    conversations = []
    for conv_id, messages in grouped.items():
        conv = storage.get_conversation(db, conv_id)
        title = conv["title"] if conv else "Unknown"
        conv_type = conv.get("type", "chat") if conv else "chat"
        conversations.append({"conversation_id": conv_id, "title": title, "type": conv_type, "messages": messages})

    # Also search source chunks
    source_results = storage.search_similar_source_chunks(db, query_embedding, limit=limit)
    source_grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in source_results:
        source_grouped[r["source_id"]].append(
            {
                "chunk_id": r["chunk_id"],
                "content": r["content"],
                "chunk_index": r["chunk_index"],
                "distance": r["distance"],
            }
        )

    source_entries = []
    for src_id, chunks in source_grouped.items():
        src = storage.get_source(db, src_id)
        title = src["title"] if src else "Unknown"
        source_entries.append({"source_id": src_id, "title": title, "chunks": chunks})

    return {"results": conversations, "source_results": source_entries}


@router.get("/search")
async def unified_search(
    request: Request,
    q: str = Query(..., min_length=1),
    mode: str = Query(default="auto", pattern="^(auto|keyword|semantic)$"),
    limit: int = Query(default=20, ge=1, le=100),
    type: str | None = Query(default=None, pattern="^(chat|note|document)$"),
) -> dict[str, Any]:
    """Unified search: auto selects semantic if available, falls back to keyword."""
    db = _get_db(request)
    vec_enabled = getattr(request.app.state, "vec_enabled", False)
    embedding_service = getattr(request.app.state, "embedding_service", None)

    use_semantic = False
    if mode == "semantic":
        if not vec_enabled or not embedding_service:
            raise HTTPException(status_code=503, detail="Semantic search not available")
        use_semantic = True
    elif mode == "auto":
        use_semantic = bool(vec_enabled and embedding_service)

    if use_semantic and embedding_service:
        query_embedding = await embedding_service.embed(q)
        if query_embedding is not None:
            results = storage.search_similar_messages(db, query_embedding, limit=limit)
            source_results = storage.search_similar_source_chunks(db, query_embedding, limit=limit)
            return {
                "mode": "semantic",
                "results": [
                    {
                        "message_id": r["message_id"],
                        "conversation_id": r["conversation_id"],
                        "content": r["content"],
                        "role": r["role"],
                        "distance": r["distance"],
                    }
                    for r in results
                ],
                "source_results": [
                    {
                        "chunk_id": r["chunk_id"],
                        "source_id": r["source_id"],
                        "content": r["content"],
                        "chunk_index": r["chunk_index"],
                        "distance": r["distance"],
                    }
                    for r in source_results
                ],
            }

    # Keyword search fallback
    conversations = storage.list_conversations(db, search=q, limit=limit, conversation_type=type)
    return {
        "mode": "keyword",
        "results": [
            {
                "conversation_id": c["id"],
                "title": c["title"],
                "type": c.get("type", "chat"),
                "message_count": c.get("message_count", 0),
            }
            for c in conversations
        ],
    }
