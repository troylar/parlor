"""Retrieval-augmented generation: retrieve relevant context for active conversations."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from ..config import RagConfig, RerankerConfig
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
    conversation_type: str | None = None  # "chat", "note", or "document"


async def retrieve_context(
    query: str,
    db: Any,
    embedding_service: Any,
    config: RagConfig,
    current_conversation_id: str | None = None,
    *,
    space_id: str | None = None,
    vec_manager: Any | None = None,
    reranker_service: Any | None = None,
    reranker_config: RerankerConfig | None = None,
) -> list[RetrievedChunk]:
    """Embed the user query and retrieve the top-K most relevant chunks.

    When *space_id* is given, message results are filtered to conversations
    belonging to that space, and source results are filtered to sources
    linked to that space.

    Returns an empty list (never raises) when embeddings are unavailable,
    the query is too short, or any transient error occurs.
    """
    if not config.enabled:
        return []

    if len(query.strip()) < 10:
        return []

    mode = getattr(config, "retrieval_mode", "dense")
    use_dense = mode in ("dense", "hybrid")
    use_keyword = mode in ("keyword", "hybrid")

    # Widen candidate pool when reranker is active
    use_reranker = reranker_service is not None and reranker_config is not None and reranker_config.enabled is not False
    retrieval_limit = config.max_chunks
    if use_reranker and reranker_config is not None:
        retrieval_limit = config.max_chunks * reranker_config.candidate_multiplier

    # Dense retrieval requires an embedding service; keyword-only does not.
    embedding: list[float] | None = None
    if use_dense:
        if not embedding_service:
            if not use_keyword:
                return []
            use_dense = False
        else:
            try:
                embedding = await embedding_service.embed(query)
            except Exception:
                logger.debug("RAG: embedding failed, skipping retrieval", exc_info=True)
                if not use_keyword:
                    return []
                use_dense = False

            if not embedding:
                if not use_keyword:
                    return []
                use_dense = False
    elif not use_keyword:
        # Neither dense nor keyword — nothing to do
        return []

    dense_msg: list[dict[str, Any]] = []
    dense_src: list[dict[str, Any]] = []
    kw_msg: list[dict[str, Any]] = []
    kw_src: list[dict[str, Any]] = []

    # Dense (vector) retrieval
    if use_dense:
        if config.include_conversations:
            try:
                dense_msg = storage.search_similar_messages(
                    db,
                    embedding,
                    limit=retrieval_limit,
                    space_id=space_id,
                    vec_index=vec_manager.messages if vec_manager else None,
                )
            except Exception:
                logger.debug("RAG: dense message search failed", exc_info=True)

        if config.include_sources:
            try:
                dense_src = storage.search_similar_source_chunks(
                    db,
                    embedding,
                    limit=retrieval_limit,
                    space_id=space_id,
                    vec_index=vec_manager.source_chunks if vec_manager else None,
                )
            except Exception:
                logger.debug("RAG: dense source chunk search failed", exc_info=True)

    # Keyword (FTS5) retrieval
    if use_keyword:
        if config.include_conversations:
            try:
                kw_msg = storage.search_keyword_messages(
                    db,
                    query,
                    limit=retrieval_limit,
                    space_id=space_id,
                )
            except Exception:
                logger.debug("RAG: keyword message search failed", exc_info=True)

        if config.include_sources:
            try:
                kw_src = storage.search_keyword_source_chunks(
                    db,
                    query,
                    limit=retrieval_limit,
                    space_id=space_id,
                )
            except Exception:
                logger.debug("RAG: keyword source chunk search failed", exc_info=True)

    # Merge results
    if mode == "hybrid":
        msg_results = _rrf_merge_messages(dense_msg, kw_msg)
        src_results = _rrf_merge_source_chunks(dense_src, kw_src)
    elif mode == "keyword":
        msg_results = kw_msg
        src_results = kw_src
    else:
        msg_results = dense_msg
        src_results = dense_src

    chunks: list[RetrievedChunk] = []

    # Build message chunks
    # Apply similarity threshold only in pure dense mode; hybrid distances are
    # synthetic RRF scores not comparable to cosine distances, and keyword mode
    # has no meaningful distance metric.
    apply_threshold = mode == "dense"
    if config.include_conversations:
        for r in msg_results:
            if config.exclude_current and r.get("conversation_id") == current_conversation_id:
                continue
            if apply_threshold and r["distance"] > config.similarity_threshold:
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
                    conversation_type=r.get("conversation_type", "chat"),
                )
            )

    # Build source chunks
    if config.include_sources:
        for r in src_results:
            if apply_threshold and r["distance"] > config.similarity_threshold:
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

    # Sort by distance (most similar first) and deduplicate
    chunks.sort(key=lambda c: c.distance)
    chunks = _deduplicate(chunks)

    # Cross-encoder reranking (optional second stage)
    if reranker_service and reranker_config and reranker_config.enabled is not False and chunks:
        # Cap reranker top_k to max_chunks so reranking never returns more than the RAG limit
        effective_top_k = min(reranker_config.top_k, config.max_chunks)
        capped_config = RerankerConfig(
            enabled=reranker_config.enabled,
            provider=reranker_config.provider,
            model=reranker_config.model,
            top_k=effective_top_k,
            score_threshold=reranker_config.score_threshold,
            candidate_multiplier=reranker_config.candidate_multiplier,
        )
        chunks = await _rerank_chunks(query, chunks, reranker_service, capped_config)

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

    type_labels = {"note": "[note]", "document": "[doc]"}
    parts: list[str] = []
    for chunk in chunks:
        label = chunk.source_label
        type_tag = type_labels.get(chunk.conversation_type or "", "")
        if type_tag:
            label = f"{type_tag} {label}"
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


_RRF_K = 60  # Standard RRF constant


def _rrf_merge_messages(dense: list[dict[str, Any]], keyword: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge dense and keyword message results via reciprocal rank fusion."""
    scores: dict[str, float] = {}
    by_id: dict[str, dict[str, Any]] = {}

    for rank, r in enumerate(dense):
        mid = r["message_id"]
        scores[mid] = scores.get(mid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        by_id[mid] = r

    for rank, r in enumerate(keyword):
        mid = r["message_id"]
        scores[mid] = scores.get(mid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        if mid not in by_id:
            by_id[mid] = r

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    results: list[dict[str, Any]] = []
    for mid, score in merged:
        entry = dict(by_id[mid])
        # Synthetic distance: higher RRF score → lower distance. Cap at [0, 1].
        entry["distance"] = max(0.0, 1.0 - score * _RRF_K)
        results.append(entry)
    return results


def _rrf_merge_source_chunks(dense: list[dict[str, Any]], keyword: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge dense and keyword source chunk results via reciprocal rank fusion."""
    scores: dict[str, float] = {}
    by_id: dict[str, dict[str, Any]] = {}

    for rank, r in enumerate(dense):
        cid = r["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        by_id[cid] = r

    for rank, r in enumerate(keyword):
        cid = r["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        if cid not in by_id:
            by_id[cid] = r

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    results: list[dict[str, Any]] = []
    for cid, score in merged:
        entry = dict(by_id[cid])
        entry["distance"] = max(0.0, 1.0 - score * _RRF_K)
        results.append(entry)
    return results


async def _rerank_chunks(
    query: str,
    chunks: list[RetrievedChunk],
    reranker_service: Any,
    reranker_config: RerankerConfig,
) -> list[RetrievedChunk]:
    """Re-score chunks with a cross-encoder and return the top results."""
    documents = [c.content for c in chunks]
    try:
        scored = await reranker_service.rerank(query, documents, top_k=reranker_config.top_k)
    except Exception:
        logger.debug("RAG: reranking failed, returning unranked results", exc_info=True)
        return chunks

    result: list[RetrievedChunk] = []
    for idx, score in scored:
        if idx < 0 or idx >= len(chunks):
            continue
        if score < reranker_config.score_threshold:
            continue
        chunk = chunks[idx]
        # Replace distance with reranker score (inverted: higher score = lower distance)
        result.append(
            RetrievedChunk(
                content=chunk.content,
                source_type=chunk.source_type,
                source_label=chunk.source_label,
                distance=max(0.0, 1.0 - score),
                conversation_id=chunk.conversation_id,
                message_id=chunk.message_id,
                source_id=chunk.source_id,
                chunk_id=chunk.chunk_id,
                conversation_type=chunk.conversation_type,
            )
        )
    # Hard cap: never return more than top_k, even if the reranker ignores the limit
    if result:
        return result[: reranker_config.top_k]
    return chunks


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
