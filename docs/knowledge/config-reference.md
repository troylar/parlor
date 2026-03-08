# Configuration Reference

All configuration knobs for embeddings and RAG.

## embeddings

Controls the embedding provider and model used for vector search.

```yaml title="~/.anteroom/config.yaml"
embeddings:
  enabled: true                          # null=auto-detect, true=force on, false=disable
  provider: "local"                      # "local" (fastembed) or "api" (OpenAI-compatible)
  model: "text-embedding-3-small"        # Model name for API provider
  dimensions: 0                          # 0=auto-detect from model
  local_model: "BAAI/bge-small-en-v1.5"  # Model name for local provider
  base_url: ""                           # API endpoint (for API provider)
  api_key: ""                            # API key (for API provider)
  api_key_command: ""                    # Shell command to fetch API key dynamically
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool/null | `null` | Tri-state: `null` = auto-detect (enable if provider works), `true` = force on, `false` = disable |
| `provider` | string | `"local"` | `"local"` for fastembed (offline, no API calls) or `"api"` for OpenAI-compatible endpoint |
| `model` | string | `"text-embedding-3-small"` | Model name when using API provider |
| `dimensions` | integer | `0` | Embedding dimensions; `0` = auto-detect from model (384 for local, 1536 for OpenAI) |
| `local_model` | string | `"BAAI/bge-small-en-v1.5"` | Fastembed model name; downloaded automatically on first use |
| `base_url` | string | `""` | API endpoint URL (uses main `ai.base_url` if empty) |
| `api_key` | string | `""` | API key (uses main `ai.api_key` if empty) |
| `api_key_command` | string | `""` | Shell command to fetch API key dynamically (runs on each request) |
| `cache_dir` | string | `""` | Custom fastembed model cache directory; when set, also enables `local_files_only` mode to prevent network requests. Useful for air-gapped environments |

**Environment variables:** `AI_CHAT_EMBEDDINGS_ENABLED`, `AI_CHAT_EMBEDDINGS_PROVIDER`, `AI_CHAT_EMBEDDINGS_MODEL`, `AI_CHAT_EMBEDDINGS_DIMENSIONS`, `AI_CHAT_EMBEDDINGS_LOCAL_MODEL`, `AI_CHAT_EMBEDDINGS_BASE_URL`, `AI_CHAT_EMBEDDINGS_API_KEY`, `AI_CHAT_EMBEDDINGS_API_KEY_COMMAND`, `AI_CHAT_EMBEDDINGS_CACHE_DIR`

### Auto-detection

When `enabled` is `null` (the default), Anteroom probes the embedding provider on startup:

- **Local provider**: checks if fastembed is importable
- **API provider**: sends a test embedding request

If the probe succeeds, embeddings are enabled. If it fails, embeddings are silently disabled and RAG returns no results.

### Dimension Auto-detection

When `dimensions` is `0`:

| Provider | Model | Dimensions |
|----------|-------|-----------|
| local | `BAAI/bge-small-en-v1.5` | 384 |
| local | `BAAI/bge-base-en-v1.5` | 768 |
| local | `BAAI/bge-large-en-v1.5` | 1024 |
| api | (any) | 1536 |

---

## rag

Controls the RAG retrieval pipeline --- what gets searched, how many results, and filtering thresholds.

```yaml title="~/.anteroom/config.yaml"
rag:
  enabled: true                  # Master toggle for RAG
  max_chunks: 10                 # Maximum chunks to retrieve per query
  max_tokens: 2000               # Token budget for RAG context
  similarity_threshold: 0.5      # Maximum cosine distance (lower = stricter)
  include_sources: true          # Search knowledge source chunks
  include_conversations: true    # Search past conversation messages
  exclude_current: true          # Exclude current conversation from results
  retrieval_mode: "dense"        # "dense", "keyword", or "hybrid"
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Master toggle; `false` disables RAG entirely |
| `max_chunks` | integer | `10` | Maximum number of chunks to retrieve per query |
| `max_tokens` | integer | `2000` | Token budget for injected RAG context (estimated as chars / 4) |
| `similarity_threshold` | float | `0.5` | Maximum cosine distance; results above this threshold are dropped. Lower values = stricter matching. Only applies in `dense` mode |
| `include_sources` | bool | `true` | Whether to search knowledge source chunks |
| `include_conversations` | bool | `true` | Whether to search past conversation messages |
| `exclude_current` | bool | `true` | Whether to exclude the current conversation from message search results |
| `retrieval_mode` | string | `"dense"` | Retrieval strategy: `"dense"` (vector similarity), `"keyword"` (FTS5 text search), or `"hybrid"` (both, merged via Reciprocal Rank Fusion) |

**Environment variables:** `AI_CHAT_RAG_ENABLED`, `AI_CHAT_RAG_MAX_CHUNKS`, `AI_CHAT_RAG_MAX_TOKENS`, `AI_CHAT_RAG_SIMILARITY_THRESHOLD`, `AI_CHAT_RAG_RETRIEVAL_MODE`

### Tuning the Threshold

The `similarity_threshold` is a cosine distance (not cosine similarity). Lower values mean stricter matching:

| Value | Effect |
|-------|--------|
| `0.3` | Very strict --- only highly relevant content surfaces |
| `0.5` | Default --- good balance of relevance and recall |
| `0.7` | Loose --- more content surfaces, may include less relevant results |
| `1.0` | Everything matches (not recommended) |

### Token Budget

The `max_tokens` setting controls how much RAG context is injected into the system prompt. The estimate uses `characters / 4` as a rough token approximation.

If retrieved chunks exceed the budget, the least relevant chunks (highest distance) are dropped until the budget is met.

---

## reranker

Controls the optional cross-encoder reranking stage. When enabled, retrieved chunks are re-scored by a cross-encoder model for improved relevance before being injected into the prompt. Uses fastembed `TextCrossEncoder` locally --- no external API needed.

```yaml title="~/.anteroom/config.yaml"
reranker:
  enabled: null                        # null=auto-detect, true=force, false=disable
  provider: "local"                    # Only "local" (fastembed) is supported
  model: "cross-encoder/ms-marco-MiniLM-L-6-v2"
  top_k: 5                            # Keep top-K after reranking
  score_threshold: 0.0                 # Minimum relevance score (0 = no threshold)
  candidate_multiplier: 3             # Widen initial retrieval by this factor
  cache_dir: ""                        # Custom model cache directory for offline use
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool/null | `null` | Tri-state: `null` = auto-detect (enable if fastembed available), `true` = force on, `false` = disable |
| `provider` | string | `"local"` | Provider: only `"local"` (fastembed TextCrossEncoder) is currently supported |
| `model` | string | `"cross-encoder/ms-marco-MiniLM-L-6-v2"` | Cross-encoder model name (~23MB, downloaded on first use) |
| `top_k` | integer | `5` | Keep top-K chunks after reranking; capped to `rag.max_chunks` at runtime |
| `score_threshold` | float | `0.0` | Minimum relevance score; cross-encoder logits can be negative, so `0.0` means no threshold |
| `candidate_multiplier` | integer | `3` | Fetch `top_k * candidate_multiplier` candidates before reranking; wider pool gives the reranker more to choose from |
| `cache_dir` | string | `""` | Custom fastembed model cache directory; when set, also enables `local_files_only` mode. Useful for air-gapped environments |

**Environment variables:** `AI_CHAT_RERANKER_ENABLED`, `AI_CHAT_RERANKER_PROVIDER`, `AI_CHAT_RERANKER_MODEL`, `AI_CHAT_RERANKER_TOP_K`, `AI_CHAT_RERANKER_SCORE_THRESHOLD`, `AI_CHAT_RERANKER_CANDIDATE_MULTIPLIER`, `AI_CHAT_RERANKER_CACHE_DIR`

### Auto-detection

When `enabled` is `null` (the default), Anteroom creates the reranker service but probes it on first use. If the cross-encoder model loads successfully, reranking is enabled. If it fails (e.g., fastembed not installed), reranking is silently disabled and retrieval results are returned in their original order.

### How It Interacts with RAG

The reranker sits between retrieval and token trimming in the pipeline:

1. RAG retrieves `max_chunks * candidate_multiplier` candidates (widened pool)
2. The cross-encoder scores each candidate against the original query
3. Candidates below `score_threshold` are dropped
4. The top `top_k` results are kept (capped to `rag.max_chunks`)
5. Token trimming applies to the reranked results

If reranking fails at runtime, the original retrieval order is used as a fallback.

---

## storage (RAG-related fields)

```yaml title="~/.anteroom/config.yaml"
storage:
  purge_embeddings: true    # Delete embeddings when conversations are purged
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `purge_embeddings` | bool | `true` | When `retention_days` is set and conversations are purged, also delete their embeddings from the vector index |

---

## Example Configurations

### Offline / Air-gapped

```yaml
embeddings:
  provider: "local"
  cache_dir: "/opt/anteroom/models"  # Pre-downloaded models, no network access
reranker:
  cache_dir: "/opt/anteroom/models"  # Same or separate directory
```

### Hybrid Retrieval with Reranking

```yaml
rag:
  retrieval_mode: "hybrid"       # Dense + keyword, merged via RRF
  max_chunks: 10
reranker:
  enabled: true
  top_k: 5
  candidate_multiplier: 3       # Retrieve 30 candidates, rerank to top 5
```

### OpenAI Embeddings

```yaml
embeddings:
  provider: "api"
  model: "text-embedding-3-small"
  base_url: "https://api.openai.com/v1"
  api_key: "sk-..."
```

### Disable RAG

```yaml
rag:
  enabled: false
```

### Strict Matching with Small Context

```yaml
rag:
  similarity_threshold: 0.3
  max_chunks: 5
  max_tokens: 1000
```

### Sources Only (No Conversation History)

```yaml
rag:
  include_conversations: false
  include_sources: true
```
