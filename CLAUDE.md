# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Anteroom is a self-hosted, private ChatGPT-style web UI and agentic CLI that connects to any OpenAI-compatible API. It provides two interfaces: a FastAPI web UI with vanilla JS frontend, and a Rich-based CLI REPL with built-in tools and MCP integration. Single-user, local-first, SQLite-backed.

## Development Commands

```bash
# Install for development
pip install -e ".[dev]"

# Run the app
aroom                               # Web UI at http://127.0.0.1:8080
aroom chat                          # CLI REPL
aroom init                          # Interactive setup wizard
aroom --test                        # Validate AI connection
aroom --version                     # Show version
aroom chat --model gpt-4o           # Override model

# Testing
pytest tests/ -v                    # All tests (~528 tests)
pytest tests/unit/ -v               # Unit tests only
pytest tests/unit/test_tools.py -v  # Single test file
pytest tests/unit/test_tools.py::test_name -v  # Single test
pytest --cov=anteroom --cov-report=html  # With coverage

# Linting & formatting
ruff check src/ tests/              # Lint
ruff check src/ tests/ --fix        # Lint with auto-fix
ruff format src/ tests/             # Format (120 char line length)
```

## Architecture

### Dual Interface, Shared Core

Both the web UI and CLI share the same agent loop (`services/agent_loop.py`) and storage layer. This is the central design pattern — changes to tool handling, streaming, or message building affect both interfaces.

```
Web UI (routers/) ──┐
                    ├──→ agent_loop.py → ai_service.py → OpenAI-compatible API
CLI (cli/repl.py) ──┘         │
                         tools/ + mcp_manager.py
                              │
                         storage.py → SQLite (shared DB)
```

### Key Modules

- **`app.py`** — FastAPI app factory, middleware stack (auth, rate limiting, CSRF, security headers with conditional HSTS based on TLS config, body size limit)
- **`config.py`** — YAML config loading with env var overrides, dataclass hierarchy (`AppConfig` → `AIConfig`, `AppSettings`, `CliConfig`, `McpServerConfig`, `UserIdentity`). `AppSettings.tls` controls HTTPS, HSTS, and secure cookies. `ensure_identity()` auto-generates Ed25519 keypair on first run
- **`identity.py`** — User identity generation: Ed25519 keypair via `cryptography`, UUID4 user IDs, PEM serialization
- **`services/agent_loop.py`** — Shared agentic loop: streams responses, parses tool calls, executes tools in parallel via `asyncio.as_completed`, loops up to `max_tool_iterations` (50). Auto-compacts at 100K tokens. Emits `"thinking"` event between tool execution and next API call for UI spinners. Accepts optional `message_queue` param for prompt queuing — checks queue after each `done` event and continues the loop if messages are pending
- **`services/ai_service.py`** — OpenAI SDK wrapper with streaming and transparent token refresh on 401
- **`services/storage.py`** — SQLite DAL with column-allowlisted SQL builder, parameterized queries, UUID-based IDs. Includes vector storage methods (`store_embedding`, `search_similar_messages`) that gracefully degrade when sqlite-vec is unavailable
- **`services/embeddings.py`** — Embedding service: calls OpenAI-compatible embedding API, validates vectors, manages configuration from `EmbeddingsConfig`
- **`services/embedding_worker.py`** — Background worker that processes unembedded messages asynchronously, runs on a configurable interval
- **`routers/search.py`** — Search API: `/api/search/semantic` (vector similarity) and `/api/search/hybrid` (FTS5 + vector). Both endpoints require sqlite-vec
- **`tools/`** — ToolRegistry pattern: `_handlers` (async callables) + `_definitions` (OpenAI function schemas). Built-in tools: read_file, write_file, edit_file, bash, glob_files, grep
- **`cli/repl.py`** — REPL with prompt_toolkit, skills system, @file references, /commands. Uses concurrent input/output architecture with `patch_stdout()` — input prompt stays active while agent streams responses, with messages queued and processed in FIFO order
- **`tls.py`** — Self-signed certificate generation for localhost HTTPS using `cryptography` package
- **`routers/`** — FastAPI endpoints: conversations CRUD, SSE chat streaming, config, projects. Chat endpoint supports prompt queuing: if a stream is active for a conversation, new messages are queued (max 10) and return `{"status": "queued"}` JSON instead of opening a new SSE stream

### Security Model

Single-user local app with OWASP ASVS Level 1. Auth via HttpOnly session cookies + CSRF double-submit. Security middleware in `app.py` handles: rate limiting (120 req/min), body size (15MB), security headers (CSP, HSTS, X-Frame-Options), HMAC-SHA256 token comparison. Tool safety in `tools/security.py` blocks path traversal and destructive commands.

### Database

SQLite with WAL journaling, FTS5 for search, foreign keys enforced. Schema defined in `db.py`. Tables: users, conversations, messages, attachments, tool_calls, projects, folders, tags, message_embeddings. All entity tables carry `user_id` and `user_display_name` columns for identity attribution. Optional sqlite-vec extension enables vector similarity search via `message_embeddings` virtual table.

### Configuration

Config file at `~/.anteroom/config.yaml` (falls back to `~/.parlor/config.yaml` for backward compat). Environment variables override config values with `AI_CHAT_` prefix (e.g., `AI_CHAT_BASE_URL`, `AI_CHAT_API_KEY`, `AI_CHAT_MODEL`, `AI_CHAT_USER_ID`, `AI_CHAT_DISPLAY_NAME`). Token provider pattern (`api_key_command`) enables dynamic API key refresh via external commands. TLS is disabled by default (`app.tls: false`); set to `true` to enable HTTPS with a self-signed certificate. User identity (Ed25519 keypair + UUID) is auto-generated on first run and stored in the `identity` config section. `EmbeddingsConfig` controls vector embeddings: `enabled`, `model`, `dimensions`, `base_url`, `api_key`, `api_key_command`.

### Deployment

PyPI package: `anteroom`. Deploy via `/deploy` Claude Code skill which handles: merge PR, wait for CI, version bump, build, and `twine upload`. Requires `build` and `twine` installed. Credentials via `~/.pypirc` or `TWINE_USERNAME`/`TWINE_PASSWORD` env vars.

## Testing Patterns

- **Async tests** use `@pytest.mark.asyncio` with `asyncio_mode = "auto"` in pyproject.toml
- **Unit tests** are fully mocked (no I/O), integration tests use real SQLite databases
- Tests are in `tests/unit/`, `tests/integration/`, `tests/contract/`
- Coverage target: 80%+

## CI

GitHub Actions (`.github/workflows/test.yml`): test matrix across Python 3.10-3.14, ruff lint+format check, pytest with coverage, pip-audit, Snyk SCA+SAST.
