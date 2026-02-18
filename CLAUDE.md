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
aroom --approval-mode auto          # Override safety mode for session
aroom --allowed-tools bash,write_file       # Pre-allow tools
aroom --approval-mode auto chat     # Works with subcommands too

# Testing
pytest tests/ -v                    # All tests
pytest tests/unit/ -v               # Unit tests only
pytest tests/e2e/ -v                # E2e tests (requires uvx/npx)
pytest -m e2e -v                    # Run only e2e-marked tests
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

- **`app.py`** — FastAPI app factory, middleware stack (auth, rate limiting, CSRF, security headers with conditional HSTS based on TLS config, body size limit). `_derive_auth_token()` derives a stable HMAC-SHA256 token from the Ed25519 identity key so browser cookies survive server restarts; falls back to a random token when no identity is present. `ensure_identity()` is called in `create_app()` before token derivation when identity is absent or its `private_key` is missing (covers first-run and corrupted-config cases). `BearerTokenMiddleware._make_401()` attaches a fresh `anteroom_session` cookie to 401 responses so browsers auto-recover without a redirect loop
- **`config.py`** — YAML config loading with env var overrides, dataclass hierarchy (`AppConfig` → `AIConfig`, `AppSettings`, `CliConfig`, `McpServerConfig`, `SharedDatabaseConfig`, `UserIdentity`, `SafetyConfig`, `SafetyToolConfig`, `SubagentConfig`, `EmbeddingsConfig`). `AppSettings.tls` controls HTTPS, HSTS, and secure cookies. `ensure_identity()` auto-generates Ed25519 keypair on first run; also repairs partial identity (user_id present but no private_key) by generating and persisting a fresh keypair
- **`identity.py`** — User identity generation: Ed25519 keypair via `cryptography`, UUID4 user IDs, PEM serialization
- **`services/agent_loop.py`** — Shared agentic loop: streams responses, parses tool calls, executes tools in parallel via `asyncio.as_completed`, loops up to `max_tool_iterations` (50). Auto-compacts at 100K tokens. Emits `"thinking"` event between tool execution and next API call for UI spinners. Accepts optional `message_queue` param for prompt queuing — checks queue after each `done` event and continues the loop if messages are pending
- **`services/ai_service.py`** — OpenAI SDK wrapper with streaming and transparent token refresh on 401. Emits `tool_call_args_delta` events during argument accumulation for real-time canvas streaming
- **`services/event_bus.py`** — Async pub/sub event bus: in-process delivery via `asyncio.Queue` per subscriber, cross-process delivery via SQLite `change_log` polling (1.5s interval). Channels: `conversation:{id}` and `global:{db_name}`. Used by `routers/events.py` SSE endpoint for real-time UI updates
- **`services/storage.py`** — SQLite DAL with column-allowlisted SQL builder, parameterized queries, UUID-based IDs. Includes vector storage methods (`store_embedding`, `search_similar_messages`) that gracefully degrade when sqlite-vec is unavailable
- **`services/embeddings.py`** — Embedding service: calls OpenAI-compatible embedding API, validates vectors, manages configuration from `EmbeddingsConfig`
- **`services/embedding_worker.py`** — Background worker that processes unembedded messages asynchronously, runs on a configurable interval
- **`routers/search.py`** — Search API: `/api/search/semantic` (vector similarity) and `/api/search/hybrid` (FTS5 + vector). Both endpoints require sqlite-vec
- **`tools/`** — ToolRegistry pattern: `_handlers` (async callables) + `_definitions` (OpenAI function schemas). Built-in tools: read_file, write_file, edit_file, bash, glob_files, grep, create_canvas, update_canvas, patch_canvas, run_agent. Safety gate integration: `call_tool()` checks tier-based approval via `tools/tiers.py` + pattern detection via `tools/safety.py`, accepts per-call `confirm_callback` for interface-specific approval flows. Session permissions (`_session_allowed` set) and config allowlists bypass approval checks
- **`tools/tiers.py`** — Tool risk tier system (Claude Code-style). `ToolTier` enum: READ (0), WRITE (1), EXECUTE (2), DESTRUCTIVE (3). `ApprovalMode` enum: AUTO (bypass all), ASK_FOR_DANGEROUS (only destructive), ASK_FOR_WRITES (default, write+execute+destructive), ASK (same as ask_for_writes). `should_require_approval()` returns tri-state: True (needs approval), False (auto-allow), None (hard deny from denied_tools). `DEFAULT_TOOL_TIERS` maps built-in tools; unknown/MCP tools default to EXECUTE
- **`tools/safety.py`** — Pure detection logic for destructive operations. `check_bash_command()` matches against compiled regex patterns (13 defaults + configurable custom patterns). `check_write_path()` detects writes to sensitive paths (`.env`, `.ssh`, `.gnupg`, etc.). Returns `SafetyVerdict` dataclass. No I/O, no side effects. Pattern detection runs even when tier check auto-allows (except in AUTO mode), ensuring destructive commands like `git reset --hard` always prompt
- **`tools/canvas.py`** — Canvas tools for AI to create/update rich content panels alongside chat. Supports streaming content updates during generation via SSE events (`canvas_stream_start`, `canvas_streaming`). `patch_canvas` applies incremental search/replace edits for token efficiency
- **`tools/subagent.py`** — Sub-agent tool (`run_agent`): spawns isolated child AI sessions for parallel task execution. Each sub-agent gets its own conversation context, deepcopy'd AIService config, and defensive system prompt. Guarded by `SubagentLimiter` (asyncio.Semaphore). All limits configurable via `safety.subagent` in config.yaml (`SubagentConfig` dataclass): `max_concurrent` (default 5), `max_total` (default 10), `max_depth` (default 3), `max_iterations` (default 15), `timeout` (default 120s, clamped 10-600), `max_output_chars` (default 4000), `max_prompt_chars` (default 32000). Wall-clock timeout per sub-agent via `asyncio.wait_for`. Module-level constants serve as fallback defaults when no config is provided. Generic error messages returned to parent; full traces logged server-side
- **`routers/approvals.py`** — `POST /api/approvals/{approval_id}/respond` endpoint for Web UI safety gate approval flow. Uses Pydantic request model with `scope` field (once/session/always), explicit `Content-Type: application/json` enforcement, regex-validated approval IDs, atomic dict pop to prevent TOCTOU races
- **`routers/events.py`** — `GET /events` SSE endpoint for real-time UI updates (canvas streaming, approval notifications) backed by `services/event_bus.py`
- **`cli/repl.py`** — REPL with prompt_toolkit, skills system, @file references, /commands. Uses concurrent input/output architecture with `patch_stdout()` — input prompt stays active while agent streams responses, with messages queued and processed in FIFO order. `_confirm_destructive` approval callback prints a dim collapsed summary after user responds; EOFError/KeyboardInterrupt (Escape or Ctrl+C) treated as denial — fails closed. Wires sub-agent context into tool executor (`SubagentLimiter`, `_cli_event_sink` for Rich rendering, `_subagent_counter` for unique agent IDs)
- **`tls.py`** — Self-signed certificate generation for localhost HTTPS using `cryptography` package
- **`routers/`** — FastAPI endpoints: conversations CRUD, SSE chat streaming, config, projects, canvas CRUD, document/note entry management. Chat endpoint supports prompt queuing: if a stream is active for a conversation, new messages are queued (max 10) and return `{"status": "queued"}` JSON instead of opening a new SSE stream. Stale stream detection checks `request.is_disconnected()` and stream age (approval_timeout + 30s); stale streams are cancelled and replaced rather than queued to. `_active_streams` stores `{started_at, request, cancel_event}` dicts. State-changing endpoints with JSON bodies enforce Content-Type validation. Chat endpoint also wires sub-agent context (`SubagentLimiter`, `_web_event_sink` buffering up to 500 events per agent, SSE `subagent_event` emission)

### Security Model

Single-user local app with OWASP ASVS Level 1. Auth via HttpOnly session cookies + CSRF double-submit with Origin header validation (defense-in-depth). Auth token is stable across restarts: derived from Ed25519 private key via HMAC-SHA256 (`_derive_auth_token()`), falls back to random token when no identity exists. On 401, `_make_401()` sets a fresh session cookie in the response so browsers auto-recover without a redirect loop; partial identity configs (user_id but no private_key) are auto-repaired on startup. Security middleware in `app.py` handles: rate limiting (120 req/min), body size (15MB), security headers (CSP, HSTS, X-Frame-Options), HMAC-SHA256 token comparison, session absolute timeout. Tool safety in `tools/security.py` blocks path traversal and hard-blocks destructive commands. Tool approval system (Claude Code-style): 4 risk tiers (read/write/execute/destructive), 4 approval modes (auto/ask_for_dangerous/ask_for_writes/ask), 3 permission scopes (Allow Once, Allow for Session, Allow Always). MCP tools are also gated at the `_tool_executor` level in both `chat.py` and `cli/repl.py`. Config-based `allowed_tools` and `denied_tools` lists override tier checks. Session permissions are in-memory; "Always" permissions persist to `config.yaml` via `write_allowed_tool()`. The `tool_calls` table tracks `approval_decision` for audit. The Web UI flow uses `asyncio.Event` with disconnect-aware polling (1s interval, checks `request.is_disconnected()`, configurable timeout default 120s), event bus SSE for notifications, and `routers/approvals.py` for response handling. In-memory `pending_approvals` dict on `app.state` (capped at 100 entries). Fails closed: no approval channel = operation blocked.

### Database

SQLite with WAL journaling, FTS5 for search, foreign keys enforced. Schema defined in `db.py`. Tables: users, conversations, messages, attachments, tool_calls (with `approval_decision` audit column), projects, folders, tags, conversation_tags, message_embeddings, canvases, change_log. Conversations have a `type` column (`chat`, `note`, `document`) controlling behavior. All entity tables carry `user_id` and `user_display_name` columns for identity attribution. Optional sqlite-vec extension enables vector similarity search via the `vec_messages` virtual table (created with `vec0`); `message_embeddings` is a companion regular table storing metadata (content hash, chunk index).

### Configuration

Config file at `~/.anteroom/config.yaml` (falls back to `~/.parlor/config.yaml` for backward compat). Environment variables override config values with `AI_CHAT_` prefix (e.g., `AI_CHAT_BASE_URL`, `AI_CHAT_API_KEY`, `AI_CHAT_MODEL`, `AI_CHAT_USER_ID`, `AI_CHAT_DISPLAY_NAME`). Token provider pattern (`api_key_command`) enables dynamic API key refresh via external commands. TLS is disabled by default (`app.tls: false`); set to `true` to enable HTTPS with a self-signed certificate. User identity (Ed25519 keypair + UUID) is auto-generated on first run and stored in the `identity` config section. `EmbeddingsConfig` controls vector embeddings: `enabled`, `model`, `dimensions`, `base_url`, `api_key`, `api_key_command`. `SafetyConfig` controls tool safety gates: `enabled` flag, `approval_mode` (auto/ask_for_dangerous/ask_for_writes/ask, default ask_for_writes, env: `AI_CHAT_SAFETY_APPROVAL_MODE`), `approval_timeout` (seconds, default 120, clamped 10–600), per-tool `SafetyToolConfig` entries with `enabled` boolean, `allowed_tools` (list, always auto-approved), `denied_tools` (list, hard-blocked), `tool_tiers` (dict mapping tool names to tier strings for overrides). Global `custom_patterns` (list of regex strings for bash) and `sensitive_paths` (list of path strings for write_file) are top-level fields. `SubagentConfig` (nested under `safety.subagent`) controls sub-agent limits: `max_concurrent` (default 5), `max_total` (default 10), `max_depth` (default 3), `max_iterations` (default 15), `timeout` (seconds, default 120, clamped 10–600), `max_output_chars` (default 4000), `max_prompt_chars` (default 32000). All fields optional with sensible defaults.

### Developer Workflow

This project uses Claude Code skills (`.claude/commands/`) and auto-loaded rules (`.claude/rules/`) to enforce development standards. See `VISION.md` for product identity and scope guardrails. See `ROADMAP.md` for the prioritized roadmap organized by VISION.md direction areas.

**Skills** (invoke with `/command`): `/ideate`, `/new-issue`, `/start-work`, `/commit`, `/submit-pr`, `/pr-check`, `/code-review`, `/deploy`, `/write-docs`, `/dev-help`, `/next`, `/triage`, `/cleanup`. Run `/dev-help` for a full guide.

**Rules** (auto-loaded every session): commit format, issue requirement, output formatting, product vision alignment, security patterns, test requirements.

### Deployment

PyPI package: `anteroom`. Deploy via `/deploy` Claude Code skill which handles: merge PR, wait for CI, version bump, build, and `twine upload`. Requires `build` and `twine` installed. Credentials via `~/.pypirc` or `TWINE_USERNAME`/`TWINE_PASSWORD` env vars.

## Testing Patterns

- **Async tests** use `@pytest.mark.asyncio` with `asyncio_mode = "auto"` in pyproject.toml
- **Unit tests** are fully mocked (no I/O), integration tests use real SQLite databases
- **E2e tests** start real servers with real MCP servers but mock the AI service. Require `uvx` and/or `npx` on PATH; tests skip gracefully when unavailable
- Tests are in `tests/unit/`, `tests/integration/`, `tests/contract/`, `tests/e2e/`
- **Pytest markers**: `e2e` (end-to-end tests requiring real services), `real_ai` (tests that call a real AI backend, require API key)
- Coverage target: 80%+

## CI

GitHub Actions (`.github/workflows/test.yml`): test matrix across Python 3.10-3.14, ruff lint+format check, pytest with coverage, pip-audit, Snyk SCA (enforced, production deps only) + SAST (informational, SARIF uploaded for visibility but non-blocking due to false positives in taint analysis).
