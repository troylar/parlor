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
pytest tests/e2e/ -m real_ai -v    # Agent evals (requires API key)
ruff check src/ tests/              # Lint
ruff format src/ tests/             # Format (120 char line length)

# Evals and demos
npx promptfoo eval --config evals/promptfoo.yaml   # Prompt regression
npx promptfoo eval --config evals/agentic.yaml     # Agentic behavior
npx promptfoo redteam run --config evals/redteam.yaml  # Red teaming
cd demos && make demos              # Build demo GIFs (requires VHS)
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
- **`__main__.py`** — Argparse dispatch: `init`, `config`, `chat`, `exec`, `db`, `usage`, `audit` subcommands. Global flags: `--version`, `--test`, `--allowed-tools`, `--approval-mode`, `--port`, `--debug`, `--team-config`. Chat flags: `--trust-project`, `--no-project-context`, `--plan`. Audit flags: `audit {verify,purge}`. DB flags: `db {list,show,purge,encrypt}` for data retention (`purge` deletes old conversations; `encrypt` initializes encryption)
- **`app.py`** — FastAPI app factory, middleware stack (auth, rate limiting, CSRF, security headers, body size limit). Auth token derived from Ed25519 identity key via HMAC-SHA256. Lifespan management: initializes encrypted database (if enabled), starts retention worker (if configured)
- **`config.py`** — YAML config loader with layered precedence: defaults < team < personal < project < env vars < CLI flags. Dataclass hierarchy: `AppConfig` → `AIConfig`, `AppSettings`, `CliConfig`, `PlanningConfig`, `SkillsConfig`, `McpServerConfig`, `SafetyConfig`, `SubagentConfig`, `EmbeddingsConfig`, `UsageConfig`, `ProxyConfig`, `ReferencesConfig`, `CodebaseIndexConfig`, `SessionConfig`, `StorageConfig`, `AuditConfig`, `DlpConfig`. Enforces locked fields from team `enforce` list. Config validated via `services/config_validator.py` before parsing
- **`identity.py`** — Ed25519 keypair generation, UUID4 user IDs, PEM serialization
- **`tls.py`** — Self-signed cert generation for localhost HTTPS

#### Services (Shared Core)
- **`services/agent_loop.py`** — Shared agentic loop: streams responses, parses tool calls, parallel execution via `asyncio.as_completed`, max 50 iterations. Cancel-aware. Auto-compacts at configurable token threshold. Supports prompt queuing, narration cadence, auto-plan threshold. DLP scanning on streamed chunks and final assembled text (with `dlp_blocked` and `dlp_warning` events). Output content filtering with system prompt leak detection (with `output_filter_blocked` and `output_filter_warning` events). Internal `_`-prefixed metadata keys stripped before sending to LLM
- **`services/ai_service.py`** — OpenAI SDK wrapper with streaming, token refresh on 401, split timeout architecture (6 timeouts: connect/write/pool/first_token/request/chunk_stall), cancel-aware at all phases, exponential backoff retry on transient errors. Emits `phase`, `retrying`, `tool_call_args_delta`, `usage` events. Error events include `retryable` flag
- **`services/storage.py`** — SQLite DAL with column-allowlisted SQL builder, parameterized queries, UUID IDs. Vector storage (graceful degradation without sqlite-vec). Source CRUD, tags, groups, project CRUD and linking (`get_project_by_name`, `update_conversation_project`, `count_project_conversations`), text chunking, embeddings. Token usage tracking. Conversation slugs
- **`services/mcp_manager.py`** — MCP client lifecycle: parallel startup, per-server tool filtering, routes `call_tool()` to correct session. Each server gets own `AsyncExitStack`. Warns on tool-name collisions
- **`services/context_trust.py`** — Prompt injection defense: classifies content as trusted or untrusted. `wrap_untrusted()` wraps external content in defensive XML envelopes with origin attribution. `sanitize_trust_tags()` prevents envelope breakout. `trusted_section_marker()` / `untrusted_section_marker()` provide structural system prompt separation. Used by `rag.py`, `mcp_manager.py`, `agent_loop.py`, `routers/chat.py`, `cli/repl.py`
- **`services/embeddings.py`** — Dual provider: `LocalEmbeddingService` (fastembed, offline-first, default) and `EmbeddingService` (OpenAI-compatible API)
- **`services/embedding_worker.py`** — Background worker for unembedded messages/source chunks. Exponential backoff, skip/fail sentinels, auto-disables after 10 consecutive failures
- **`services/rag.py`** — RAG pipeline: embed query, search similar messages/source chunks via sqlite-vec, filter by threshold, deduplicate, trim to token budget. Gracefully degrades
- **`services/codebase_index.py`** — Tree-sitter codebase index for token-efficient context injection. 10 languages. Graceful degradation. Optional: `pip install anteroom[index]`
- **`services/audit.py`** — Structured JSONL audit log with HMAC-SHA256 chain tamper protection. Append-only writes with fcntl locking, daily/size rotation, retention purge. `AuditWriter` emits events from auth middleware and tool executors. `verify_chain()` validates integrity. SIEM-compatible (Splunk, ELK/OpenSearch)
- **`services/dlp.py`** — Data Loss Prevention scanner: detects sensitive patterns (SSN, credit card, email, phone, IBAN) via regex. `DlpScanner` class with configurable actions: `redact` (replace matches), `block` (reject), `warn` (allow + log). Scans both input and output. Built-in patterns are default; custom patterns via config. Pure functions, no I/O. Emits `dlp_blocked` and `dlp_warning` events via agent loop
- **`services/injection_detector.py`** — Prompt injection detection with canary token defense: detects when untrusted content (tool outputs, RAG results) attempts to override system instructions through canary token leakage, encoding attacks (base64, hex, unicode escaping), and heuristic pattern matching (instruction override, roleplay injection, prompt leak requests). `InjectionDetector` class with configurable actions: `block` (reject), `warn` (allow + log), `log` (silent audit). Per-content-chunk scanning with ReDoS-safe regex patterns and optional canary token validation. Emits `injection_detected` events. Pure functions, no I/O
- **`services/output_filter.py`** — Output content filter with system prompt leak detection (OWASP LLM07 mitigation). `OutputContentFilter` class scans LLM output for forbidden content and system prompt fragments via n-gram analysis. Configurable actions: `redact`, `block`, `warn`. Pure functions, no I/O. Per-request initialization with system prompt context. Emits `output_filter_blocked` and `output_filter_warning` events
- **`services/egress_allowlist.py`** — Egress domain allowlist for API call restrictions. `check_egress_allowed()` validates that outbound API requests target only approved domains. Supports domain-based filtering (case-insensitive exact match) and loopback/localhost blocking. Fails closed: unparseable URLs or empty hostnames are denied. Pure functions, no I/O
- **`services/event_bus.py`** — Async pub/sub: in-process via `asyncio.Queue`, cross-process via SQLite `change_log` polling
- **`services/slug.py`** — Slug generation: unique `{word}-{word}` names for conversation resumption
- **`services/trust.py`** — Trust store for ANTEROOM.md files. SHA-256 hash verification. Fails closed
- **`services/team_config.py`** — Team config discovery, loading, merging (deep_merge with named-list support), enforcement
- **`services/config_validator.py`** — Schema validation for raw YAML config dicts. Collects all errors/warnings
- **`services/compliance.py`** — Compliance rules engine: declarative config policy validation. `validate_compliance()` evaluates `ComplianceRule` entries (must_be, must_not_be, must_match, must_not_be_empty, must_contain) against the final merged `AppConfig`. Fails closed at startup. `aroom config validate` CLI subcommand. Redacts sensitive fields in violation output
- **`services/config_watcher.py`** — Mtime-based config file watcher for live reload
- **`services/discovery.py`** — Walk-up directory discovery. Searches `.anteroom/`, `.claude/`, `.parlor/` with precedence
- **`services/project_config.py`** — Project-scoped config discovery with SHA-256 trust verification
- **`services/required_keys.py`** — Required keys validation and interactive prompting
- **`services/tool_rate_limit.py`** — Tool call rate limiting: per-minute, per-conversation, and consecutive-failure caps. `ToolRateLimiter` tracks call timestamps, enforces configurable limits. Configurable action: `block` (return error) or `warn` (log warning, allow). Instantiated per-request (web UI) or per-session (CLI). Shared across parent and sub-agents for unified rate limiting
- **`services/session_store.py`** — Session persistence backends: `MemorySessionStore` (volatile, in-process) and `SQLiteSessionStore` (durable, survives restart). Protocol-based design with `create()`, `get()`, `touch()`, `delete()`, `count_active()`, `cleanup_expired()`. Session state: id, user_id, ip_address, created_at, last_activity_at. Timeouts (idle/absolute) configurable via `SessionConfig`
- **`services/ip_allowlist.py`** — IP allowlist checking with CIDR and exact address support. `check_ip_allowed()` validates client IPs against allowlist. Returns `True` if list is empty (no restrictions) or IP matches any entry. Both IPv4 and IPv6 supported. Fails closed on invalid input
- **`services/retention.py`** — Background worker for data retention policy enforcement. `RetentionWorker` runs on configurable interval (default 1 hour), purges conversations older than configured retention days via `purge_conversations_before()`. Cascade deletes messages, tool_calls, embeddings. Optionally deletes attachment files from disk. Exponential backoff on failures. `purge_orphaned_attachments()` cleans up orphaned attachment directories
- **`services/encryption.py`** — Encryption at rest for SQLite database via SQLCipher (optional dependency). `derive_db_key()` derives 256-bit key from Ed25519 identity key via HKDF-SHA256. `is_sqlcipher_available()` checks if sqlcipher3 is installed. `open_encrypted_db()` opens encrypted connection. Gracefully degrades to standard sqlite3 when unavailable or disabled

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
- **`cli/repl.py`** — Main REPL loop with prompt_toolkit, concurrent input/output via `patch_stdout()`. Orchestrates: system prompt building, project context detection, trust verification, plan mode workflow, skill auto-invocation. Delegates slash commands to `commands.py`, agent turns to `agent_turn.py`, events to `event_handlers.py`. Project management: `/project` (create/select/edit/delete/sources), `/projects` (list). Active project state with instruction injection and model override
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
- **`cli/skills.py`** — Skills registry: loads YAML skill files from default/global/project directories. Name validation (`[a-z0-9][a-z0-9_-]*`), `{args}` template interpolation, YAML error hints, collision warnings, `MAX_SKILLS` limit. `SkillRegistry` with `load()`/`reload()`, `resolve_input()`, `get_invoke_skill_definition()`. Auto-invocation via synthetic `invoke_skill` tool

#### Tools
- **`tools/`** — ToolRegistry: `_handlers` + `_definitions`. Built-in: read_file, write_file, edit_file, bash, glob_files, grep, create_canvas, update_canvas, patch_canvas, run_agent, ask_user, introspect. Safety gate: tier check → pattern detection → hard-block. File-modifying tools return `_old_content`/`_new_content` for diff rendering (stripped before LLM)
- **`tools/tiers.py`** — Risk tiers: READ/WRITE/EXECUTE/DESTRUCTIVE. Approval modes: AUTO/ASK_FOR_DANGEROUS/ASK_FOR_WRITES/ASK. Unknown/MCP tools default to EXECUTE
- **`tools/bash.py`** — Shell command execution with configurable sandboxing. `_check_sandbox()` enforces network/package/path/command restrictions before execution. Accepts `_sandbox_config: BashSandboxConfig` from `call_tool()`. On Windows, assigns subprocess to Win32 Job Object for kernel-level resource limits. Configurable timeout caps, output truncation, and audit logging via `security_logger`
- **`tools/security.py`** — Security utilities: hard-block patterns, path validation, `check_network_command()`, `check_package_install()`, `check_blocked_path()`, `check_custom_patterns()` for sandbox enforcement. Cross-platform: Unix tools, PowerShell, Windows package managers
- **`tools/sandbox_win32.py`** — Win32 Job Object sandbox via ctypes (no dependencies). `create_job_object()`, `assign_process()`, `terminate_job()`, `close_job()`, `setup_job_for_process()`. Enforces memory, process count, and CPU time limits. No-op on non-Windows. All functions return success/failure, never raise
- **`tools/safety.py`** — Pure detection: `check_bash_command()` (regex patterns), `check_write_path()` (sensitive paths). Returns `SafetyVerdict` with `is_hard_blocked`
- **`tools/canvas.py`** — Canvas create/update/patch with SSE streaming support
- **`tools/subagent.py`** — `run_agent` tool: isolated child AI sessions, same safety gates. Guarded by `SubagentLimiter`. Configurable via `safety.subagent`
- **`tools/introspect.py`** — Lets AI examine its own runtime context. READ tier (auto-allowed)

### Security Model

Single-user local app, OWASP ASVS Level 2. Auth: HttpOnly session cookies + CSRF double-submit + Origin validation. Stable auth token from Ed25519 key via HMAC-SHA256. Session store (memory or SQLite-backed) tracks creation time, last activity, and client IP for session validation and lifecycle management. IP allowlisting (CIDR or exact) gates access at middleware. Concurrent session limits prevent token reuse abuse. Session timeouts: 12-hour absolute, 30-minute idle. Middleware: rate limiting (120 req/min), body size (15MB), security headers. Tool safety: 4 risk tiers, 4 approval modes, 3 permission scopes (once/session/always). Path traversal and hard-block detection. Bash sandboxing: configurable network/package/path/command restrictions, timeout caps, output limits, audit logging. MCP tools gated at parent and sub-agent levels. Fails closed: no approval channel = blocked. Encryption at rest: optional SQLCipher integration (opt-in via `encrypt_at_rest`), key derived from Ed25519 identity key via HKDF-SHA256. Data retention: configurable policy with background worker, purges conversations and attachments older than retention days; cascades delete related messages, tool calls, embeddings.

### Database

SQLite with WAL journaling, FTS5 for search, foreign keys enforced. Optional SQLCipher encryption at rest (via `encrypt_at_rest` config). Schema in `db.py`. `init_db()` signature: `init_db(db_path, vec_dimensions=384, encryption_key=None)`. Key tables: conversations (with `type`, `slug`, and `working_dir` columns), messages (with token usage tracking), tool_calls (`approval_decision` audit), sources/source_chunks/source_tags/source_groups, canvases, message_embeddings, source_chunk_embeddings. Optional sqlite-vec for vector similarity search. Retention worker cascades deletes conversations, messages, tool_calls, embeddings, and optionally attachment files.

### Configuration

Config at `~/.anteroom/config.yaml` (backward compat: `~/.parlor/config.yaml`). Env vars override with `AI_CHAT_` prefix. Dynamic API key refresh via `api_key_command`. Ed25519 identity auto-generated on first run.

**Precedence:** defaults < team < personal < project < env vars < CLI flags (team-enforced fields override all). Project configs require SHA-256 trust verification. Live reload via config watcher.

Key config sections (see `config.py` dataclasses for all fields and defaults):
- **`AIConfig`** — API connection, 6 timeouts, retry settings, narration cadence, max_tools (default 128), temperature (None = provider default), top_p (None = provider default), seed (None = provider default), egress domain allowlist (`allowed_domains`, `block_localhost_api`)
- **`SafetyConfig`** — Approval mode (default ask_for_writes), allowed/denied tools, custom bash patterns, per-tool tier overrides, read-only mode, tool rate limiting (per-minute, per-conversation, consecutive failures). Nested `BashSandboxConfig`: execution timeout (1-600s), output limits (min 1000 chars), path/command blocking, network/package restrictions, audit logging. Nested `OsSandboxConfig`: Win32 Job Object limits — `max_memory_mb` (512), `max_processes` (10), `cpu_time_limit` (None). Auto-detects Windows
- **`CliConfig`** — Context compaction thresholds, tool dedup, retry behavior, visual thresholds
- **`PlanningConfig`** — Auto-trigger: `auto_mode` (off/suggest/auto), `auto_threshold_tools`
- **`SkillsConfig`** — `auto_invoke` (default true) enables AI skill invocation
- **`SubagentConfig`** — Limits: concurrency (5), total (10), depth (3), iterations (15), timeout (120s)
- **`EmbeddingsConfig`** — Dual provider (local fastembed default or API). Tri-state `enabled`: None=auto-detect, True=force, False=disable
- **`RagConfig`** — RAG pipeline: `max_chunks` (10), `max_tokens` (2000), `similarity_threshold` (0.5)
- **`CodebaseIndexConfig`** — Tree-sitter index: `map_tokens` (1000), auto-detect languages. Optional dependency
- **`ProxyConfig`** — OpenAI-compatible proxy (opt-in), CORS allowlist
- **`McpServerConfig`** — Per-server `tools_include`/`tools_exclude` (fnmatch), `trust_level` (default `"untrusted"`; controls defensive prompt envelope wrapping for tool outputs)
- **`SessionConfig`** — Session management: `store` (memory/sqlite), `max_concurrent_sessions` (0 = unlimited), `idle_timeout` (1800s), `absolute_timeout` (43200s), `allowed_ips` (CIDR or exact; empty = allow all), `log_session_events` (bool)
- **`StorageConfig`** — Data retention and encryption: `retention_days` (0 = disabled), `retention_check_interval` (default 3600s), `purge_attachments` (default true), `purge_embeddings` (default true), `encrypt_at_rest` (default false, requires sqlcipher3), `encryption_kdf` (default hkdf-sha256)
- **`AuditConfig`** — Structured audit log: `enabled` (default false), `log_path`, `tamper_protection` (hmac/none), `rotation` (daily/size), `retention_days` (90), `redact_content` (true), per-event-type toggles
- **`OutputFilterConfig`** — Output content filtering: `enabled` (default false), `system_prompt_leak_detection` (default true), `leak_threshold` (0.0-1.0, default 0.4), `custom_patterns` (regex list for forbidden patterns), `action` (redact/block/warn, default warn), `redaction_string` (default `[FILTERED]`), `log_detections` (default true). Nested `OutputFilterPatternConfig`: `name`, `pattern` (regex), `description`
- **`ComplianceConfig`** — Declarative compliance rules: `rules` (list of `ComplianceRule`). Each rule: `field` (dot-path), `must_be`, `must_not_be`, `must_match` (regex), `must_not_be_empty`, `must_contain`, `message`. Evaluated at startup; non-compliant configs fail closed

### Developer Workflow

Claude Code skills (`.claude/commands/`) and auto-loaded rules (`.claude/rules/`) enforce development standards. See `VISION.md` for product identity and scope guardrails.

**Skills**: `/ideate`, `/new-issue`, `/start-work`, `/commit`, `/submit-pr`, `/pr-check`, `/code-review`, `/deploy`, `/write-docs`, `/dev-help`, `/next`, `/triage`, `/cleanup`, `/a-help`

**Rules**: commit format, issue requirement, output formatting, vision alignment, security patterns, test requirements, feature parity

### Deployment

PyPI: `anteroom`. Deploy via `/deploy` skill (merge PR, CI, version bump, build, `twine upload`).

**Optional Dependencies** (declared in `pyproject.toml`):
- **`encryption`** — `sqlcipher3>=0.5.0`. Required only if `config.storage.encrypt_at_rest: true`. Enable with: `pip install anteroom[encryption]`

## Testing Patterns

- **Unit tests** (`tests/unit/`, ~2,500 tests): fully mocked, no I/O. `@pytest.mark.asyncio` with `asyncio_mode = "auto"`
- **Integration** (`tests/integration/`): real SQLite databases
- **E2e** (`tests/e2e/`): real servers, mock AI. Markers: `e2e`, `requires_mcp`
- **Agent evals** (`tests/e2e/test_agent_evals.py`): 10 tests with real AI via `aroom exec --json`. Marker: `real_ai`. Auto-skip without API key. Uses `--temperature 0 --seed 42` for reproducibility
- **Prompt regression** (`evals/`): promptfoo suites via OpenAI-compatible proxy. `promptfoo.yaml` (11 prompt regression tests), `agentic.yaml` (6 exec-mode tests), `redteam.yaml` (adversarial). Run: `npx promptfoo eval --config evals/promptfoo.yaml`
- **Demo recordings** (`demos/`): VHS tape scripts producing reproducible GIFs. 3 demos: quickstart, tools, exec-mode. Run: `cd demos && make demos`
- Coverage target: 80%+. See `docs/advanced/testing.md` for full guide

## CI

GitHub Actions: Python 3.10-3.14 matrix, ruff lint+format, pytest with coverage, pip-audit, Semgrep SAST, CodeQL.
