# Architecture

Anteroom's internal architecture and module map.

## Module Map

```
src/anteroom/
├── app.py                    # FastAPI app factory, middleware stack
├── config.py                 # YAML config loading, dataclass hierarchy
├── db.py                     # SQLite schema definition
├── tls.py                    # Self-signed certificate generation
├── services/
│   ├── agent_loop.py         # Shared agentic loop (core engine)
│   ├── ai_service.py         # OpenAI SDK wrapper with streaming
│   ├── storage.py            # SQLite DAL with parameterized queries
│   ├── embeddings.py         # Embedding API client and vector management
│   ├── embedding_worker.py   # Background worker: embeds new messages asynchronously
│   └── event_bus.py          # In-process pub/sub for SSE fan-out
├── routers/                  # FastAPI endpoint handlers
│   ├── approvals.py          # POST /api/approvals/{id}/respond — safety gate responses
│   └── events.py             # SSE event bus endpoint for real-time notifications
├── tools/
│   ├── __init__.py           # ToolRegistry pattern
│   ├── security.py           # Path blocking, command confirmation (hard blocks)
│   ├── safety.py             # Configurable approval gate: check_bash_command(), check_write_path()
│   ├── canvas.py             # Canvas tools: create_canvas, update_canvas, patch_canvas
│   └── subagent.py           # Sub-agent tool: isolated child AI sessions for parallel execution
├── cli/
│   ├── repl.py               # REPL with prompt_toolkit
│   └── default_skills/       # Built-in skill YAML files
├── static/                   # Frontend assets (JS, CSS, fonts)
└── templates/                # Jinja2 HTML templates
```

## Agent Loop

The agent loop (`services/agent_loop.py`) is the core execution engine shared by both interfaces.

```
User message
    │
    ▼
Build message history + tool definitions
    │
    ▼
Send to AI API (streaming) ◄──────────────┐
    │                                       │
    ▼                                       │
Parse response                              │
    │                                       │
    ├── Text only ──► Done                  │
    │                                       │
    └── Tool calls ──► Execute tools ───────┘
                       (parallel via asyncio.as_completed)
```

Key behaviors:

- **Max iterations**: 50 per turn (configurable)
- **Parallel execution**: Multiple tool calls in one response run concurrently
- **Auto-compact**: Triggers at 100K tokens
- **Thinking events**: Emitted between tool execution and next API call
- **Prompt queue**: Accepts optional `message_queue` param; checks queue after each `done` event
- **Sub-agents**: `run_agent` tool spawns isolated child loops with depth limiting (max 3), concurrency control (max 5 concurrent, 20 total), and defensive system prompts

## Storage Layer

SQLite with WAL journaling, FTS5 for search, and foreign keys enforced.

- **Column-allowlisted SQL builder**: Only known columns can appear in queries
- **Parameterized queries**: All values are bound, never concatenated
- **UUID-based IDs**: All entities use UUID primary keys

Tables: `users`, `conversations`, `messages`, `attachments`, `tool_calls`, `projects`, `folders`, `tags`, `conversation_tags`, `change_log`, `canvases`, `message_embeddings`.

## Tool Registry

The `ToolRegistry` pattern maintains two parallel structures:

- `_handlers`: Dict of async callables keyed by tool name
- `_definitions`: Dict of OpenAI function schemas keyed by tool name

Built-in tools are registered at startup. MCP tools are added when MCP servers connect.

## AI Service

The `AIService` wraps the OpenAI Python SDK with:

- Async streaming support
- Transparent token refresh on HTTP 401 (re-runs `api_key_command`)
- Client rebuild after token refresh

## Middleware Stack

```
Request
  │
  ├── BearerTokenMiddleware (auth)
  ├── RateLimitMiddleware (120/min per IP)
  ├── MaxBodySizeMiddleware (15 MB)
  ├── SecurityHeadersMiddleware (CSP, HSTS, etc.)
  ├── CSRF validation (double-submit)
  │
  ▼
Router handler
```
