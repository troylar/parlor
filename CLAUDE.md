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
aroom --port 9090                   # Override port (also: AI_CHAT_PORT=9090)
aroom chat                          # CLI REPL
aroom init                          # Interactive setup wizard
aroom --test                        # Validate AI connection
aroom --version                     # Show version
aroom chat --model gpt-4o           # Override model
aroom --approval-mode auto          # Override safety mode for session
aroom --allowed-tools bash,write_file       # Pre-allow tools
aroom --approval-mode auto chat     # Works with subcommands too
aroom chat --trust-project          # Auto-trust project ANTEROOM.md without prompting
aroom chat --no-project-context     # Skip loading project-level ANTEROOM.md
aroom chat --plan                   # Start in planning mode (explore, then /plan approve)

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

- **`__main__.py`** — CLI entry point. Argparse dispatch: `init`, `config`, `chat`, `exec`, `db`, `usage` subcommands. Global flags: `--version`, `--test`, `--allowed-tools`, `--approval-mode`, `--port`, `--debug`. Chat flags: `--trust-project`, `--no-project-context`, `--plan`. Usage subcommand: `--period` (day/week/month/all), `--conversation`, `--json`. Debug logging: `--debug` flag or `AI_CHAT_LOG_LEVEL` env var (DEBUG/INFO/WARNING/ERROR/CRITICAL) configures Python logging to stderr; also sets uvicorn log level in web UI mode
- **`app.py`** — FastAPI app factory, middleware stack (auth, rate limiting, CSRF, security headers, body size limit). Auth token derived from Ed25519 identity key via HMAC-SHA256 for stable cookies across restarts
- **`config.py`** — YAML config with env var overrides (`AI_CHAT_` prefix). Dataclass hierarchy: `AppConfig` → `AIConfig`, `AppSettings`, `CliConfig`, `PlanningConfig`, `McpServerConfig`, `SafetyConfig`, `SubagentConfig`, `EmbeddingsConfig`, `UsageConfig`, `ProxyConfig`. Auto-generates Ed25519 identity on first run. `McpServerConfig` supports per-server `tools_include`/`tools_exclude` (fnmatch patterns). `UsageConfig` controls token usage tracking: `week_days` (default 7), `month_days` (default 30), `model_costs` (dict of model→input/output rates) for cost estimation
- **`identity.py`** — Ed25519 keypair generation, UUID4 user IDs, PEM serialization
- **`services/agent_loop.py`** — Shared agentic loop for both interfaces. Streams responses, parses tool calls, parallel execution via `asyncio.as_completed`, max 50 iterations. Cancel-aware. Auto-compacts at configurable token threshold. Supports prompt queuing, narration cadence, and auto-plan threshold. Forwards usage events from ai_service. Internal `_`-prefixed metadata keys stripped before sending to LLM
- **`services/ai_service.py`** — OpenAI SDK wrapper with streaming, token refresh on 401, split timeout architecture (connect/write/pool/first_token/request/chunk_stall), cancel-aware at all phases, exponential backoff retry on transient errors. Emits `phase`, `retrying`, `tool_call_args_delta`, `usage` events. Requests `stream_options={"include_usage": True}` for token tracking. Error events include `retryable` flag
- **`services/event_bus.py`** — Async pub/sub: in-process via `asyncio.Queue`, cross-process via SQLite `change_log` polling. Channels: `conversation:{id}` and `global:{db_name}`
- **`services/storage.py`** — SQLite DAL with column-allowlisted SQL builder, parameterized queries, UUID IDs. Vector storage (graceful degradation without sqlite-vec). Source CRUD, tags, groups, project linking, text chunking, embeddings. Token usage: `update_message_usage()` persists per-message token counts, `get_usage_stats()` aggregates by model with time/conversation filters
- **`services/embeddings.py`** — Dual provider: `LocalEmbeddingService` (fastembed, offline-first, default) and `EmbeddingService` (OpenAI-compatible API)
- **`services/embedding_worker.py`** — Background worker for unembedded messages/source chunks. Exponential backoff, skip/fail sentinels, auto-disables after 10 consecutive failures
- **`services/mcp_manager.py`** — MCP client lifecycle: connects servers (stdio/SSE), per-server tool filtering, routes `call_tool()` to correct session. Warns on tool-name collisions. Each server gets own `AsyncExitStack`
- **`services/trust.py`** — Trust store for ANTEROOM.md files. JSON registry at `~/.anteroom/trusted_folders.json` with SHA-256 hash verification. Supports recursive trust. Fails closed
- **`routers/`** — FastAPI endpoints: conversations CRUD, SSE chat streaming, config, projects, canvas CRUD, sources CRUD with groups/project linking, search (semantic + hybrid), approvals, events SSE, OpenAI-compatible proxy, usage stats. Chat endpoint supports prompt queuing (max 10), source injection (50K char limit), plan mode, sub-agents
- **`routers/usage.py`** — `GET /api/usage` endpoint for token usage statistics. Query params: `period` (day/week/month/all), `conversation_id` (UUID-validated). Returns per-model token aggregation with cost estimates
- **`tools/`** — ToolRegistry: `_handlers` + `_definitions`. Built-in: read_file, write_file, edit_file, bash, glob_files, grep, create_canvas, update_canvas, patch_canvas, run_agent, ask_user. Safety gate: tier check (`tiers.py`) → pattern detection (`safety.py`) → hard-block (`security.py`). File-modifying tools return `_old_content`/`_new_content` for diff rendering (stripped before LLM)
- **`tools/tiers.py`** — Risk tiers: READ/WRITE/EXECUTE/DESTRUCTIVE. Approval modes: AUTO/ASK_FOR_DANGEROUS/ASK_FOR_WRITES/ASK. Unknown/MCP tools default to EXECUTE
- **`tools/safety.py`** — Pure detection: `check_bash_command()` (regex patterns), `check_write_path()` (sensitive paths). Returns `SafetyVerdict` with `is_hard_blocked`. No I/O
- **`tools/canvas.py`** — Canvas create/update/patch with SSE streaming support
- **`tools/subagent.py`** — `run_agent` tool: isolated child AI sessions with own context, same safety gates. Guarded by `SubagentLimiter` (semaphore). Configurable limits via `safety.subagent`
- **`cli/renderer.py`** — Rich terminal output: verbosity levels, thinking spinner with lifecycle phases, plan checklist rendering, inline diff rendering for file tools, tool call dedup, subagent rendering
- **`cli/exec_mode.py`** — Non-interactive mode for scripting/CI. JSON output, timeout, fail-closed approval. Exit codes: 0/1/124
- **`cli/plan.py`** — Planning mode: explore-only tools, plan file at `~/.anteroom/plans/{conv_id}.md`. `/plan` commands: on/start/approve/status/edit/reject/off. Inline prompt: `/plan <prompt>`
- **`cli/instructions.py`** — ANTEROOM.md discovery: `.anteroom.md` > `ANTEROOM.md`, walks up from cwd. Global from `~/.anteroom/`. Token estimation (chars/4)
- **`cli/repl.py`** — REPL with prompt_toolkit, concurrent input/output via `patch_stdout()`. Cancel vs error handling (esc cancels, retryable errors auto-retry). Plan mode two-phase workflow. Sub-agent wiring. Project context auto-detection. `/usage` command for token usage stats
- **`tls.py`** — Self-signed cert generation for localhost HTTPS

### Security Model

Single-user local app, OWASP ASVS Level 1. Auth: HttpOnly session cookies + CSRF double-submit + Origin validation. Stable auth token derived from Ed25519 key via HMAC-SHA256. Middleware: rate limiting (120 req/min), body size (15MB), security headers (CSP, HSTS, X-Frame-Options). Tool safety: 4 risk tiers, 4 approval modes, 3 permission scopes (once/session/always). Path traversal and hard-block detection (rm -rf, fork bombs). MCP tools gated at both parent and sub-agent levels. Session permissions in-memory; "Always" persists to config. `tool_calls` table tracks `approval_decision` for audit. Fails closed: no approval channel = blocked.

### Database

SQLite with WAL journaling, FTS5 for search, foreign keys enforced. Schema defined in `db.py`. Tables: users, conversations, messages, attachments, tool_calls (with `approval_decision` audit column), projects, folders, tags, conversation_tags, message_embeddings, canvases, change_log, sources, source_chunks, source_tags, source_groups, source_group_members, project_sources (3-mode linking via CHECK constraint: source_id, group_id, or tag_filter), source_attachments (dual citizenship bridge), source_chunk_embeddings. Conversations have a `type` column (`chat`, `note`, `document`) controlling behavior. All entity tables carry `user_id` and `user_display_name` columns for identity attribution. Messages include token usage tracking columns: `prompt_tokens`, `completion_tokens`, `total_tokens`, and `model` (for cost estimation). Optional sqlite-vec extension enables vector similarity search via the `vec_messages` virtual table (created with `vec0`); `message_embeddings` is a companion regular table storing metadata (content hash, chunk index). Vector schema is parameterized via `_make_vec_schema(dimensions)` to support different embedding dimensions; the table is recreated if dimensions change between app runs via `_ensure_vec_schema_matches_config()`.

### Configuration

Config at `~/.anteroom/config.yaml` (backward compat: `~/.parlor/config.yaml`). Env vars override with `AI_CHAT_` prefix (e.g., `AI_CHAT_BASE_URL`, `AI_CHAT_API_KEY`, `AI_CHAT_MODEL`, `AI_CHAT_PORT`). Dynamic API key refresh via `api_key_command`. TLS disabled by default. Ed25519 identity auto-generated on first run.

Key config sections (see `config.py` dataclasses for all fields, defaults, and env var mappings):
- **`AIConfig`** — API connection, 6 timeout fields (connect/write/pool/first_token/request/chunk_stall), retry settings, narration cadence
- **`SafetyConfig`** — Tool approval mode (default ask_for_writes), allowed/denied tool lists, custom bash patterns, sensitive paths, per-tool tier overrides
- **`CliConfig`** — Context compaction thresholds, tool dedup, retry behavior, visual thresholds, output limits
- **`PlanningConfig`** — Planning mode auto-trigger: `auto_mode` (off/suggest/auto), `auto_threshold_tools`
- **`UsageConfig`** — Token usage tracking: `week_days` (default 7), `month_days` (default 30), `model_costs` (per-model input/output rates for cost estimation)
- **`SubagentConfig`** — Sub-agent limits: concurrency, total, depth, iterations, timeout, output/prompt size
- **`EmbeddingsConfig`** — Dual provider (local fastembed default, or OpenAI-compatible API)
- **`ProxyConfig`** — OpenAI-compatible proxy (opt-in), CORS allowlist
- **`McpServerConfig`** — Per-server tool filtering via `tools_include`/`tools_exclude` (fnmatch)

### Developer Workflow

This project uses Claude Code skills (`.claude/commands/`) and auto-loaded rules (`.claude/rules/`) to enforce development standards. See `VISION.md` for product identity and scope guardrails. See `ROADMAP.md` for the prioritized roadmap organized by VISION.md direction areas.

**Skills** (invoke with `/command`): `/ideate`, `/new-issue`, `/start-work`, `/commit`, `/submit-pr`, `/pr-check`, `/code-review`, `/deploy`, `/write-docs`, `/dev-help`, `/next`, `/triage`, `/cleanup`, `/docs`. Run `/dev-help` for a full guide.

**Rules** (auto-loaded every session): commit format, issue requirement, output formatting, product vision alignment, security patterns, test requirements, feature parity.

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

GitHub Actions (`.github/workflows/test.yml`): test matrix across Python 3.10-3.14, ruff lint+format check, pytest with coverage, pip-audit, Semgrep SAST (p/python + p/security-audit rulesets, SARIF uploaded to GitHub Security tab). Separate CodeQL workflow (`.github/workflows/codeql-analysis.yml`) runs deep semantic analysis on push/PR to main and weekly schedule.
