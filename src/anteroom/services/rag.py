"""Retrieval-augmented generation: retrieve relevant context for active conversations."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from ..config import RagConfig
from . import storage
from .context_trust import wrap_untrusted

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    """A single chunk of context retrieved by the RAG pipeline."""

    content: str
    source_type: str  # "message" or "source_chunk"
    source_label: str  # conversation title or source name
    distance: float
    conversation_id: str | None = None
    message_id: str | None = None
    source_id: str | None = None
    chunk_id: str | None = None


async def retrieve_context(
    query: str,
    db: Any,
    embedding_service: Any,
    config: RagConfig,
    current_conversation_id: str | None = None,
) -> list[RetrievedChunk]:
    """Embed the user query and retrieve the top-K most relevant chunks.

    Returns an empty list (never raises) when embeddings are unavailable,
    the query is too short, or any transient error occurs.
    """
    if not config.enabled:
        return []

    if not embedding_service:
        return []

    if len(query.strip()) < 10:
        return []

    try:
        embedding = await embedding_service.embed(query)
    except Exception:
        logger.debug("RAG: embedding failed, skipping retrieval", exc_info=True)
        return []

    if not embedding:
        return []

    chunks: list[RetrievedChunk] = []

    # Retrieve similar messages from past conversations
    if config.include_conversations:
        try:
            msg_results = storage.search_similar_messages(db, embedding, limit=config.max_chunks)
            for r in msg_results:
                if config.exclude_current and r.get("conversation_id") == current_conversation_id:
                    continue
                if r["distance"] > config.similarity_threshold:
                    continue
                conv_title = _get_conversation_title(db, r["conversation_id"])
                chunks.append(
                    RetrievedChunk(
                        content=r["content"] or "",
                        source_type="message",
                        source_label=conv_title,
                        distance=r["distance"],
                        conversation_id=r["conversation_id"],
                        message_id=r["message_id"],
                    )
                )
        except Exception:
            logger.debug("RAG: message search failed", exc_info=True)

    # Retrieve similar source chunks
    if config.include_sources:
        try:
            src_results = storage.search_similar_source_chunks(db, embedding, limit=config.max_chunks)
            for r in src_results:
                if r["distance"] > config.similarity_threshold:
                    continue
                source_title = _get_source_title(db, r["source_id"])
                chunks.append(
                    RetrievedChunk(
                        content=r["content"] or "",
                        source_type="source_chunk",
                        source_label=source_title,
                        distance=r["distance"],
                        source_id=r["source_id"],
                        chunk_id=r["chunk_id"],
                    )
                )
        except Exception:
            logger.debug("RAG: source chunk search failed", exc_info=True)

    # Sort by distance (most similar first) and deduplicate
    chunks.sort(key=lambda c: c.distance)
    chunks = _deduplicate(chunks)

    # Trim to token budget (chars/4 estimate)
    max_chars = config.max_tokens * 4
    trimmed: list[RetrievedChunk] = []
    total_chars = 0
    for chunk in chunks:
        chunk_chars = len(chunk.content)
        if total_chars + chunk_chars > max_chars:
            break
        trimmed.append(chunk)
        total_chars += chunk_chars

    return trimmed


def format_rag_context(chunks: list[RetrievedChunk]) -> str:
    """Format retrieved chunks as a context block for the system prompt."""
    if not chunks:
        return ""

    parts: list[str] = []
    for chunk in chunks:
        label = chunk.source_label
        # SECURITY-REVIEW: chunk.content is user-controlled (past messages / uploaded sources).
        # Wrapped in a defensive prompt envelope to mitigate indirect prompt injection.
        parts.append(wrap_untrusted(chunk.content, f"rag:{label}", "retrieved"))

    return (
        "\n\n## Retrieved Context (RAG)\n"
        "The following context was automatically retrieved from your knowledge base "
        "based on semantic similarity to the current message. Use it if relevant.\n\n" + "\n\n".join(parts)
    )


_RAG_SECTION_RE = re.compile(r"\n*## Retrieved Context \(RAG\).*?(?=\n## |\Z)", re.DOTALL)


def strip_rag_context(prompt: str) -> str:
    """Remove a previously injected RAG context block from the system prompt."""
    return _RAG_SECTION_RE.sub("", prompt)


def _deduplicate(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Collapse multiple messages from the same conversation, keeping the best match."""
    seen_conversations: set[str] = set()
    seen_sources: set[str] = set()
    result: list[RetrievedChunk] = []

    for chunk in chunks:
        if chunk.source_type == "message" and chunk.conversation_id:
            if chunk.conversation_id in seen_conversations:
                continue
            seen_conversations.add(chunk.conversation_id)
        elif chunk.source_type == "source_chunk" and chunk.chunk_id:
            if chunk.chunk_id in seen_sources:
                continue
            seen_sources.add(chunk.chunk_id)
        result.append(chunk)

    return result


def _get_conversation_title(db: Any, conversation_id: str) -> str:
    """Get a conversation's title for attribution."""
    try:
        conv = storage.get_conversation(db, conversation_id)
        if conv:
            return str(conv.get("title", "Untitled"))
    except Exception:
        pass
    return "Untitled"


def _get_source_title(db: Any, source_id: str) -> str:
    """Get a source's title for attribution."""
    try:
        src = storage.get_source(db, source_id)
        if src:
            return str(src.get("title", "Unknown source"))
    except Exception:
        pass
    return "Unknown source"
