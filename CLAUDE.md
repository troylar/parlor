# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Anteroom is a self-hosted, private ChatGPT-style web UI and agentic CLI that connects to any OpenAI-compatible API. It provides two interfaces: a FastAPI web UI with vanilla JS frontend, and a Rich-based CLI REPL with built-in tools and MCP integration. Single-user, local-first, SQLite-backed.

## Development Commands

```bash
pip install -e ".[dev]"             # Install for development
aroom                               # Web UI at http://127.0.0.1:8080
aroom chat                          # CLI REPL
aroom chat --plan                   # Start in planning mode
aroom init                          # Interactive setup wizard
aroom --test                        # Validate AI connection
aroom --approval-mode auto          # Override safety mode
aroom --allowed-tools bash,write_file  # Pre-allow tools

pytest tests/ -v                    # All tests
pytest tests/unit/ -v               # Unit tests only
pytest tests/e2e/ -v                # E2e tests (requires uvx/npx)
ruff check src/ tests/              # Lint
ruff format src/ tests/             # Format (120 char line length)
```

## Architecture

### Dual Interface, Shared Core

Both the web UI and CLI share the same agent loop (`services/agent_loop.py`) and storage layer. Changes to tool handling, streaming, or message building affect both interfaces.

```
Web UI (routers/)  ──┐
                     ├──→ agent_loop.py → ai_service.py → OpenAI-compatible API
CLI (cli/)         ──┘         │
  repl.py (main loop)    tools/ + mcp_manager.py
  commands.py (/cmds)         │
  agent_turn.py          storage.py → SQLite
  event_handlers.py
  pickers.py, renderer.py
```

### Key Modules

#### Entry Points & Core
- **`__main__.py`** — Argparse dispatch: `init`, `config`, `chat`, `exec`, `db`, `usage` subcommands. Global flags: `--version`, `--test`, `--allowed-tools`, `--approval-mode`, `--port`, `--debug`, `--team-config`. Chat flags: `--trust-project`, `--no-project-context`, `--plan`
- **`app.py`** — FastAPI app factory, middleware stack (auth, rate limiting, CSRF, security headers, body size limit). Auth token derived from Ed25519 identity key via HMAC-SHA256
- **`config.py`** — YAML config loader with layered precedence: defaults < team < personal < project < env vars < CLI flags. Dataclass hierarchy: `AppConfig` → `AIConfig`, `AppSettings`, `CliConfig`, `PlanningConfig`, `SkillsConfig`, `McpServerConfig`, `SafetyConfig`, `SubagentConfig`, `EmbeddingsConfig`, `UsageConfig`, `ProxyConfig`, `ReferencesConfig`, `CodebaseIndexConfig`. Enforces locked fields from team `enforce` list. Config validated via `services/config_validator.py` before parsing
- **`identity.py`** — Ed25519 keypair generation, UUID4 user IDs, PEM serialization
- **`tls.py`** — Self-signed cert generation for localhost HTTPS

#### Services (Shared Core)
- **`services/agent_loop.py`** — Shared agentic loop: streams responses, parses tool calls, parallel execution via `asyncio.as_completed`, max 50 iterations. Cancel-aware. Auto-compacts at configurable token threshold. Supports prompt queuing, narration cadence, auto-plan threshold. Internal `_`-prefixed metadata keys stripped before sending to LLM
- **`services/ai_service.py`** — OpenAI SDK wrapper with streaming, token refresh on 401, split timeout architecture (6 timeouts: connect/write/pool/first_token/request/chunk_stall), cancel-aware at all phases, exponential backoff retry on transient errors. Emits `phase`, `retrying`, `tool_call_args_delta`, `usage` events. Error events include `retryable` flag
- **`services/storage.py`** — SQLite DAL with column-allowlisted SQL builder, parameterized queries, UUID IDs. Vector storage (graceful degradation without sqlite-vec). Source CRUD, tags, groups, project linking, text chunking, embeddings. Token usage tracking. Conversation slugs
- **`services/mcp_manager.py`** — MCP client lifecycle: parallel startup, per-server tool filtering, routes `call_tool()` to correct session. Each server gets own `AsyncExitStack`. Warns on tool-name collisions
- **`services/embeddings.py`** — Dual provider: `LocalEmbeddingService` (fastembed, offline-first, default) and `EmbeddingService` (OpenAI-compatible API)
- **`services/embedding_worker.py`** — Background worker for unembedded messages/source chunks. Exponential backoff, skip/fail sentinels, auto-disables after 10 consecutive failures
- **`services/rag.py`** — RAG pipeline: embed query, search similar messages/source chunks via sqlite-vec, filter by threshold, deduplicate, trim to token budget. Gracefully degrades
- **`services/codebase_index.py`** — Tree-sitter codebase index for token-efficient context injection. 10 languages. Graceful degradation. Optional: `pip install anteroom[index]`
- **`services/event_bus.py`** — Async pub/sub: in-process via `asyncio.Queue`, cross-process via SQLite `change_log` polling
- **`services/slug.py`** — Slug generation: unique `{word}-{word}` names for conversation resumption
- **`services/trust.py`** — Trust store for ANTEROOM.md files. SHA-256 hash verification. Fails closed
- **`services/team_config.py`** — Team config discovery, loading, merging (deep_merge with named-list support), enforcement
- **`services/config_validator.py`** — Schema validation for raw YAML config dicts. Collects all errors/warnings
- **`services/config_watcher.py`** — Mtime-based config file watcher for live reload
- **`services/discovery.py`** — Walk-up directory discovery. Searches `.anteroom/`, `.claude/`, `.parlor/` with precedence
- **`services/project_config.py`** — Project-scoped config discovery with SHA-256 trust verification
- **`services/required_keys.py`** — Required keys validation and interactive prompting

#### Web UI (routers/)
- **`routers/chat.py`** — SSE chat streaming with dataclass-based architecture: `ChatRequestContext`, `WebConfirmContext`, `ToolExecutorContext`, `StreamContext`. Extracted functions: `_parse_chat_request()`, `_resolve_sources()`, `_build_tool_list()`, `_build_chat_system_prompt()`, `_web_confirm_tool()`, `_execute_web_tool()`, `_stream_chat_events()`. Supports prompt queuing (max 10), source injection (50K char limit), plan mode, sub-agents
- **`routers/sources.py`** — Sources API: CRUD, file upload, tags, groups, project linking
- **`routers/search.py`** — Semantic (vector) and hybrid (FTS5 + vector) search. Requires sqlite-vec
- **`routers/proxy.py`** — OpenAI-compatible proxy for external tools. Opt-in via `proxy.enabled`
- **`routers/approvals.py`** — Web UI safety gate approval flow. Atomic dict pop prevents TOCTOU races
- **`routers/events.py`** — SSE endpoint for real-time UI updates (canvas streaming, approvals)
- **`routers/usage.py`** — Token usage statistics endpoint with per-model aggregation and cost estimates
- **`routers/plan.py`** — Plan mode endpoints: read, approve, reject

#### CLI Modules
- **`cli/repl.py`** — Main REPL loop with prompt_toolkit, concurrent input/output via `patch_stdout()`. Orchestrates: system prompt building, project context detection, trust verification, plan mode workflow, skill auto-invocation. Delegates slash commands to `commands.py`, agent turns to `agent_turn.py`, events to `event_handlers.py`
- **`cli/commands.py`** — Slash command dispatch. `ReplSession` dataclass holds mutable state. `CommandResult` enum (CONTINUE/EXIT/FALL_THROUGH). `handle_slash_command()` handles 25+ commands: `/resume`, `/delete`, `/rename`, `/usage`, `/slug`, `/plan`, `/conventions`, `/model`, `/compact`, `/tools`, `/help`, etc.
- **`cli/agent_turn.py`** — Agent turn execution. `AgentTurnContext` dataclass. `run_agent_turn()` orchestrates: RAG context injection, agent loop invocation, error/cancel handling with auto-retry. `RagEmbeddingCache` for session-scoped embedding reuse
- **`cli/event_handlers.py`** — Agent loop event processing. `handle_repl_event()` dispatches thinking, content, tool_call, error, plan updates, and narration events to the renderer
- **`cli/pickers.py`** — Conversation picker helpers: `picker_relative_time()`, `picker_type_badge()`, `picker_format_preview()`, `resolve_conversation()`, `show_resume_info()`, `show_resume_picker()` (interactive prompt_toolkit picker with preview panel)
- **`cli/completer.py`** — `AnteroomCompleter`: tab completion for /commands, @file paths, and conversation slugs
- **`cli/keybindings.py`** — `KeybindingState` dataclass, `create_keybindings()`, `on_buffer_change()`, `patch_shift_enter()`
- **`cli/dialogs.py`** — Help dialog rendering
- **`cli/renderer.py`** — Rich terminal output: verbosity levels, thinking spinner with lifecycle phases, plan checklist rendering, inline diff rendering for file tools, tool call dedup, subagent rendering
- **`cli/exec_mode.py`** — Non-interactive mode for scripting/CI. JSON output, timeout, fail-closed approval. Exit codes: 0/1/124
- **`cli/plan.py`** — Planning mode helpers: `PLAN_MODE_ALLOWED_TOOLS`, plan file I/O, plan command parsing, `enter_plan_mode()`, `leave_plan_mode()`
- **`cli/instructions.py`** — ANTEROOM.md discovery (`.anteroom.md` > `ANTEROOM.md`, walk-up from cwd), global instructions, token estimation
- **`cli/skills.py`** — Skills registry: loads `.claude/commands/` files, auto-invocation via synthetic `invoke_skill` tool

#### Tools
- **`tools/`** — ToolRegistry: `_handlers` + `_definitions`. Built-in: read_file, write_file, edit_file, bash, glob_files, grep, create_canvas, update_canvas, patch_canvas, run_agent, ask_user, introspect. Safety gate: tier check → pattern detection → hard-block. File-modifying tools return `_old_content`/`_new_content` for diff rendering (stripped before LLM)
- **`tools/tiers.py`** — Risk tiers: READ/WRITE/EXECUTE/DESTRUCTIVE. Approval modes: AUTO/ASK_FOR_DANGEROUS/ASK_FOR_WRITES/ASK. Unknown/MCP tools default to EXECUTE
- **`tools/safety.py`** — Pure detection: `check_bash_command()` (regex patterns), `check_write_path()` (sensitive paths). Returns `SafetyVerdict` with `is_hard_blocked`
- **`tools/canvas.py`** — Canvas create/update/patch with SSE streaming support
- **`tools/subagent.py`** — `run_agent` tool: isolated child AI sessions, same safety gates. Guarded by `SubagentLimiter`. Configurable via `safety.subagent`
- **`tools/introspect.py`** — Lets AI examine its own runtime context. READ tier (auto-allowed)

### Security Model

Single-user local app, OWASP ASVS Level 1. Auth: HttpOnly session cookies + CSRF double-submit + Origin validation. Stable auth token from Ed25519 key via HMAC-SHA256. Middleware: rate limiting (120 req/min), body size (15MB), security headers. Tool safety: 4 risk tiers, 4 approval modes, 3 permission scopes (once/session/always). Path traversal and hard-block detection. MCP tools gated at parent and sub-agent levels. Fails closed: no approval channel = blocked.

### Database

SQLite with WAL journaling, FTS5 for search, foreign keys enforced. Schema in `db.py`. Key tables: conversations (with `type` and `slug` columns), messages (with token usage tracking), tool_calls (`approval_decision` audit), sources/source_chunks/source_tags/source_groups, canvases, message_embeddings, source_chunk_embeddings. Optional sqlite-vec for vector similarity search.

### Configuration

Config at `~/.anteroom/config.yaml` (backward compat: `~/.parlor/config.yaml`). Env vars override with `AI_CHAT_` prefix. Dynamic API key refresh via `api_key_command`. Ed25519 identity auto-generated on first run.

**Precedence:** defaults < team < personal < project < env vars < CLI flags (team-enforced fields override all). Project configs require SHA-256 trust verification. Live reload via config watcher.

Key config sections (see `config.py` dataclasses for all fields and defaults):
- **`AIConfig`** — API connection, 6 timeouts, retry settings, narration cadence, max_tools (default 128), temperature (None = provider default), top_p (None = provider default), seed (None = provider default)
- **`SafetyConfig`** — Approval mode (default ask_for_writes), allowed/denied tools, custom bash patterns, per-tool tier overrides
- **`CliConfig`** — Context compaction thresholds, tool dedup, retry behavior, visual thresholds
- **`PlanningConfig`** — Auto-trigger: `auto_mode` (off/suggest/auto), `auto_threshold_tools`
- **`SkillsConfig`** — `auto_invoke` (default true) enables AI skill invocation
- **`SubagentConfig`** — Limits: concurrency (5), total (10), depth (3), iterations (15), timeout (120s)
- **`EmbeddingsConfig`** — Dual provider (local fastembed default or API). Tri-state `enabled`: None=auto-detect, True=force, False=disable
- **`RagConfig`** — RAG pipeline: `max_chunks` (10), `max_tokens` (2000), `similarity_threshold` (0.5)
- **`CodebaseIndexConfig`** — Tree-sitter index: `map_tokens` (1000), auto-detect languages. Optional dependency
- **`ProxyConfig`** — OpenAI-compatible proxy (opt-in), CORS allowlist
- **`McpServerConfig`** — Per-server `tools_include`/`tools_exclude` (fnmatch)

### Developer Workflow

Claude Code skills (`.claude/commands/`) and auto-loaded rules (`.claude/rules/`) enforce development standards. See `VISION.md` for product identity and scope guardrails.

**Skills**: `/ideate`, `/new-issue`, `/start-work`, `/commit`, `/submit-pr`, `/pr-check`, `/code-review`, `/deploy`, `/write-docs`, `/dev-help`, `/next`, `/triage`, `/cleanup`, `/a-help`

**Rules**: commit format, issue requirement, output formatting, vision alignment, security patterns, test requirements, feature parity

### Deployment

PyPI: `anteroom`. Deploy via `/deploy` skill (merge PR, CI, version bump, build, `twine upload`).

## Testing Patterns

- **Async tests**: `@pytest.mark.asyncio` with `asyncio_mode = "auto"`
- **Unit tests**: fully mocked (no I/O). **Integration**: real SQLite. **E2e**: real servers, mock AI
- Tests in `tests/unit/`, `tests/integration/`, `tests/contract/`, `tests/e2e/`
- **Markers**: `e2e`, `real_ai`. Coverage target: 80%+

## CI

GitHub Actions: Python 3.10-3.14 matrix, ruff lint+format, pytest with coverage, pip-audit, Semgrep SAST, CodeQL.
