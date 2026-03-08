# Knowledge & RAG

## Why Knowledge?

Anteroom can remember things across conversations. When you add knowledge sources --- documents, text notes, or file uploads --- or simply chat, Anteroom embeds that content into a vector index. On every new message, it searches for semantically relevant context and injects it into the AI's prompt automatically.

This is **Retrieval-Augmented Generation (RAG)**: the AI doesn't just respond from its training data, it draws on *your* data --- project docs, past conversations, uploaded files --- to give more relevant answers.

**What this gives you:**

- Past conversation context surfaces automatically in new chats
- Uploaded documents become searchable knowledge the AI can reference
- Space-scoped RAG keeps project knowledge separate
- Zero configuration required --- works out of the box with local embeddings

## What's What

| Concept | What It Is |
|---------|-----------|
| **Source** | A knowledge document (text, file upload, or URL reference) added to the knowledge base |
| **Source chunk** | A segment of a source, split at sentence boundaries for embedding |
| **Embedding** | A vector representation of text, used for similarity search |
| **Vector index** | The search index (usearch) that enables fast nearest-neighbor lookup |
| **RAG retrieval** | The process of finding relevant context and injecting it into the AI's prompt |
| **Retrieval mode** | How chunks are found: `dense` (vector similarity), `keyword` (FTS5 text search), or `hybrid` (both, merged with RRF) |
| **Reranking** | Optional second stage: a cross-encoder model re-scores retrieved chunks for improved relevance |
| **Embedding worker** | A background process that embeds new content asynchronously |

## How It Works

```
Data enters the system
      |
      v
+------------------+     +-------------------+
| Chat messages    |     | Knowledge sources |
| (user/assistant) |     | (text/URL/file)   |
+--------+---------+     +--------+----------+
         |                         |
         v                         v
   Embedding worker          Chunk + embed
   (background, async)       (on creation)
         |                         |
         v                         v
+------------------------------------------------+
|          Vector Index (usearch)                 |
|  Messages index    |   Source chunks index      |
+------------------------------------------------+
         |
         v (on each new message)
   RAG retrieval
   - Embed the query (dense) / tokenize (keyword) / both (hybrid)
   - Search indexes (vector, FTS5, or both)
   - Merge via Reciprocal Rank Fusion (hybrid mode)
   - Filter by space/conversation
   - Deduplicate
   - Rerank with cross-encoder (optional)
   - Trim to token budget
         |
         v
   Inject into system prompt
   (wrapped in defensive tags)
         |
         v
   AI sees relevant context
```

## Pages in This Section

| Page | What It Covers |
|------|---------------|
| [How RAG Works](how-rag-works.md) | The full pipeline: embedding, retrieval, prompt injection, and the AI's perspective |
| [Sources](sources.md) | Creating, managing, and organizing knowledge sources |
| [Configuration](config-reference.md) | All config knobs for embeddings and RAG |
| [API Reference](api-reference.md) | REST endpoints for sources, search, and embeddings |

## Quick Start

RAG works out of the box with zero configuration. Anteroom uses a local embedding model (fastembed) that runs entirely on your machine --- no API calls, no data leaves your environment.

To add knowledge:

=== "Web UI"

    Click the **+** button in the sources panel, paste text or upload a file.

=== "CLI"

    Sources are managed via the web UI or API. The CLI automatically uses RAG context from all sources linked to the current space.

=== "API"

    ```bash
    curl -X POST http://localhost:8080/api/sources \
      -H "Content-Type: application/json" \
      -d '{"type": "text", "title": "Project README", "content": "..."}'
    ```

To verify RAG is working, look for `[RAG: N relevant chunk(s) retrieved]` in the CLI, or check the `prompt_meta` SSE event in the web UI for `rag_status: "ok"`.
