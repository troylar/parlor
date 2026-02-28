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
- **`__main__.py`** — Argparse dispatch: `init`, `config`, `chat`, `exec`, `db`, `usage`, `audit`, `projects`, `artifact`, `pack`, `space` subcommands. Global flags: `--version`, `--test`, `--allowed-tools`, `--approval-mode`, `--port`, `--debug`, `--team-config`, `--project`. Chat flags: `--trust-project`, `--no-project-context`, `--plan`. Audit flags: `audit {verify,purge}`. DB flags: `db {list,show,purge,encrypt}` for data retention. Artifact flags: `artifact {list,show,check,import,create}` with type/namespace/source filters; `--fix`, `--json-output`, `--instructions` for various commands. Pack flags: `pack {list,install,show,remove,update,sources,refresh,attach,detach,add-source}` with `--project` for attach/detach to scope attachment to current project only. Space flags: `space {list,create,load,show,delete,refresh,clone,map,move-root}` with `--space` on chat to force a specific space
- **`app.py`** — FastAPI app factory, middleware stack (auth, rate limiting, CSRF, security headers, body size limit). Auth token derived from Ed25519 identity key via HMAC-SHA256. Lifespan management: initializes encrypted database (if enabled), starts retention worker (if configured)
- **`config.py`** — YAML config loader with layered precedence: defaults < team < personal < project < env vars < CLI flags. Dataclass hierarchy: `AppConfig` → `AIConfig`, `AppSettings`, `CliConfig`, `PlanningConfig`, `SkillsConfig`, `McpServerConfig`, `SafetyConfig`, `SubagentConfig`, `EmbeddingsConfig`, `UsageConfig`, `ProxyConfig`, `ReferencesConfig`, `CodebaseIndexConfig`, `SessionConfig`, `StorageConfig`, `AuditConfig`, `DlpConfig`, `PackSourceConfig`. Enforces locked fields from team `enforce` list. Config validated via `services/config_validator.py` before parsing
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
- **`services/pack_sources.py`** — Git-based pack source cache management. Clone, pull, and cache git repos containing pack definitions. URL scheme allowlist (rejects `ext::`, `file://`). Credential sanitization in error messages. Deterministic cache paths via SHA-256. Pure sync functions shelling out to `git` binary
- **`services/packs.py`** — Pack management: YAML manifests, install/remove with reference counting, lock file support. `PackManifest` and `ManifestArtifact` frozen dataclasses. `parse_manifest()` with name/namespace format validation. `install_pack()` upserts artifacts and creates junction table entries. `remove_pack()` deletes orphaned artifacts not referenced by other packs. `resolve_pack()` resolves namespace/name to pack ID(s) with collision detection. `get_pack_by_id()`, `get_pack_by_source_path()`, `remove_pack_by_id()` for ID-based queries. Path traversal prevention on custom `file` fields. Enumerated SQL columns (no `SELECT *`)
- **`services/pack_refresh.py`** — Background worker for pack source refresh. `PackRefreshWorker` auto-clones and periodically pulls configured pack source repos, installs/updates packs when content changes. Per-source interval tracking, exponential backoff, start/stop lifecycle. `install_from_source()` scans a directory for `pack.yaml` manifests and installs/updates all packs found
- **`services/pack_lock.py`** — Lock file management for reproducible pack installations. `generate_lock()` builds lock data with content hashes from installed packs, enriched with `source_url` and `source_ref` for git-sourced packs. `write_lock()` / `read_lock()` for `.anteroom/anteroom.lock.yaml`. `validate_lock()` compares lock state against DB, reports mismatches
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
- **`services/artifacts.py`** — Universal artifact model. `Artifact` frozen dataclass with FQN (`@namespace/type/name`), 7 `ArtifactType` enum values (skill, rule, instruction, context, memory, mcp_server, config_overlay), 6 `ArtifactSource` enum values (built_in, global, team, project, local, inline). FQN validation/parsing via regex, SHA-256 `content_hash()` for deduplication
- **`services/artifact_storage.py`** — Artifact CRUD against SQLite. `create_artifact()`, `get_artifact()`, `get_artifact_by_fqn()`, `list_artifacts()` (with type/namespace/source filters), `update_artifact()` (auto-bumps version on content change), `delete_artifact()` (CASCADE to versions), `upsert_artifact()` (create-or-update by FQN), `list_artifact_versions()`. Parameterized queries, JSON metadata serialization
- **`services/artifact_registry.py`** — `ArtifactRegistry`: in-memory artifact index with 6-layer precedence resolution (built_in < global < team < project < local < inline). `load_from_db()` with atomic swap, `register()`/`unregister()` for programmatic manipulation, `get()` by FQN, `list()` with type/namespace/source filters, `search()` by name substring. `MAX_ARTIFACTS` cap (500)
- **`services/artifact_health.py`** — Health check engine for artifact ecosystem. `run_health_check()` analyzes all loaded artifacts and reports quality issues: config conflicts, skill collisions, shadows, empty artifacts, malformed entries, lock drift, orphaned artifacts, duplicate content, bloat. `HealthReport` dataclass with counts (artifact/pack/size/tokens), issue list with severity/category/message/details. `--fix` flag auto-resolves fixable issues. Pure functions, no I/O except optional DB read
- **`services/starter_packs.py`** — Pre-configured pack templates for onboarding. `get_starter_packs()` returns list of built-in starter pack manifests (python-dev, security-baseline, etc.) with name, description, artifact counts. Manifests loaded from `packs/<pack-name>/pack.yaml`. Used by `pack install` CLI command and web UI pack browser
- **`services/pack_attachments.py`** — Pack attachment state management. `PackAttachmentState` dataclass tracks which packs are active at global and project scopes. `get_attachment_state()` loads from config, `update_attachment_state()` writes to personal/project config. Syncs pack activation with rules/skills loading. Pure functions, no DB I/O
- **`services/local_artifacts.py`** — Local artifact discovery and import from project filesystem. `discover_local_artifacts()` scans `.anteroom/artifacts/`, `.anteroom/skills/`, `.claude/skills/`, `.claude/rules/` for YAML/MD files, parses them as artifacts. `import_to_db()` creates artifact records with `local` source. Used by artifact import flow and project setup. Path validation prevents traversal
- **`services/artifact_import.py`** — Bulk import of skills/rules/instructions into artifact database. `import_instructions()` converts ANTEROOM.md into artifact. `import_from_directory()` scans directory for importable files and batches DB writes. `BackfillArtifacts` batch processor for legacy conversion. Handles format detection (YAML vs Markdown), collision detection, and version initialization
- **`services/spaces.py`** — Space file parser and manager. `SpaceConfig` and `SpaceLocalConfig` frozen dataclasses. `parse_space_file()` with name validation (`^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$`), URL scheme validation, 256KB size limit. `validate_space()` checks URLs and paths. `get_space_config_overlay()` extracts config dict. `write_space_file()` / `write_local_file()` for YAML serialization. `list_space_files()` scans `~/.anteroom/spaces/`
- **`services/space_storage.py`** — Space DB CRUD: `create_space()`, `get_space()`, `get_space_by_name()`, `list_spaces()`, `update_space()` (column-allowlisted), `delete_space()` (cascades pack_attachments, nullifies conversations/folders). `get_spaces_by_name()` returns all matching spaces for collision detection. `resolve_space()` resolves name/ID with fallback to suggestions on collision. `sync_space_paths()` with deduplication, `resolve_space_by_cwd()` with parent directory walk-up for auto-detection. `count_space_conversations()`, `update_conversation_space()`, `get_space_local_dirs()`
- **`services/space_bootstrap.py`** — First-load cloning and pack installation. `clone_repos()` with shallow git clone (`--depth=1`), URL scheme validation, credential sanitization, 120s timeout. `install_space_packs()` queues namespace/name pack references. `bootstrap_space()` orchestrates clone + pack install with `BootstrapResult` aggregation
- **`services/space_watcher.py`** — Mtime-based space file watcher for hot-reload. `SpaceFileWatcher` polls a single YAML file, fires async/sync callback on valid changes. Invalid YAML ignored (previous config preserved). TOCTOU-safe with `try/except OSError`. Configurable interval (default 5s, min 1s, team-overridable via `space_refresh_interval`)

#### Web UI (routers/)
- **`routers/chat.py`** — SSE chat streaming with dataclass-based architecture: `ChatRequestContext`, `WebConfirmContext`, `ToolExecutorContext`, `StreamContext`. Extracted functions: `_parse_chat_request()`, `_resolve_sources()`, `_build_tool_list()`, `_build_chat_system_prompt()`, `_web_confirm_tool()`, `_execute_web_tool()`, `_stream_chat_events()`. Supports prompt queuing (max 10), source injection (50K char limit), plan mode, sub-agents
- **`routers/sources.py`** — Sources API: CRUD, file upload, tags, groups, project linking
- **`routers/search.py`** — Semantic (vector) and hybrid (FTS5 + vector) search. Requires sqlite-vec
- **`routers/proxy.py`** — OpenAI-compatible proxy for external tools. Opt-in via `proxy.enabled`
- **`routers/approvals.py`** — Web UI safety gate approval flow. Atomic dict pop prevents TOCTOU races
- **`routers/events.py`** — SSE endpoint for real-time UI updates (canvas streaming, approvals)
- **`routers/usage.py`** — Token usage statistics endpoint with per-model aggregation and cost estimates
- **`routers/plan.py`** — Plan mode endpoints: read, approve, reject
- **`routers/artifacts.py`** — Artifact API (read-only Phase 1): `GET /api/artifacts` (list with type/namespace/source filters), `GET /api/artifacts/{fqn}` (show with version history)
- **`routers/artifact_health.py`** — Artifact health check endpoint: `POST /api/artifact-health` (triggers health check, optionally auto-fixes issues). Accepts `fix` boolean flag. Returns `HealthReport` JSON
- **`routers/packs.py`** — Pack API (read-only Phase 2): `GET /api/packs` (list with artifact counts), `GET /api/packs/{namespace}/{name}` (show with artifact details), `GET /api/packs/by-id/{pack_id}` (show by pack ID), `DELETE /api/packs/by-id/{pack_id}` (remove by pack ID). Strips `source_path` from responses to prevent info disclosure
- **`routers/spaces.py`** — Spaces API: `GET/POST /api/spaces` (list/create), `GET/DELETE /api/spaces/{id}` (show/delete), `GET /api/spaces/{id}/paths` (mapped dirs), `POST /api/spaces/{id}/refresh` (re-parse YAML), `GET/POST/DELETE /api/spaces/{id}/sources` (source linking), `GET /api/spaces/{id}/packs` (attached packs). Pydantic validation on name pattern and path traversal. Error messages sanitized to prevent path disclosure

#### CLI Modules
- **`cli/repl.py`** — Main REPL loop with prompt_toolkit, concurrent input/output via `patch_stdout()`. Orchestrates: system prompt building, project context detection, trust verification, plan mode workflow, skill auto-invocation. Fullscreen mode support: HSplit layout with persistent header (model, dir, git branch), scrolling output pane, footer toolbar, and input prompt. Delegates slash commands to `commands.py`, agent turns to `agent_turn.py`, events to `event_handlers.py`. Project management: `/project` (create/select/edit/delete/sources), `/projects` (list). Pack management: `/pack` (list/show/install/update/remove/attach/detach/sources/refresh/add-source), `/packs` (alias for `/pack list`). URL scheme validation on `/pack add-source` (rejects `ext::`, `file://`, `http://`). Active project state with instruction injection and model override. Handles `_drain_input_to_msg_queue()` for queued messages with skill expansion support. Skill reload: atomic `invoke_skill` tool schema update, tab-completion skill name refresh with descriptions. Skill args delimited with `<skill_args>` tags for injection defense. Space management: `/space` (list/switch/show/refresh/clear/create/load), `/spaces` (alias for `/space list`). Space auto-detection from cwd with parent walk-up. Space instruction injection via `<space_instructions>` XML tags with `sanitize_trust_tags()`. Resume loads space context from conversation
- **`cli/layout.py`** — Full-screen terminal layout with HSplit structure: persistent header showing model/directory/git branch, scrolling output pane with mouse-scroll support and auto-scroll-to-bottom, separator lines, status footer, and input area. `OutputControl` auto-scrolls output to latest content via cursor positioning. `AnteroomLayout` class builds the layout tree. `OutputPaneWriter` provides async write interface for streaming output. `format_header()` renders header text with metadata. `create_anteroom_style()` defines Rich styles for layout elements
- **`cli/commands.py`** — Slash command dispatch. `ReplSession` dataclass holds mutable state. `CommandResult` enum (CONTINUE/EXIT/FALL_THROUGH). `handle_slash_command()` handles 25+ commands: `/resume`, `/delete`, `/rename`, `/usage`, `/slug`, `/plan`, `/conventions`, `/model`, `/compact`, `/tools`, `/help`, etc.
- **`cli/agent_turn.py`** — Agent turn execution. `AgentTurnContext` dataclass. `run_agent_turn()` orchestrates: RAG context injection, agent loop invocation, error/cancel handling with auto-retry. `RagEmbeddingCache` for session-scoped embedding reuse
- **`cli/event_handlers.py`** — Agent loop event processing. `handle_repl_event()` dispatches thinking, content, tool_call, error, plan updates, and narration events to the renderer
- **`cli/pickers.py`** — Conversation picker helpers: `picker_relative_time()`, `picker_type_badge()`, `picker_format_preview()`, `resolve_conversation()`, `show_resume_info()`, `show_resume_picker()` (interactive prompt_toolkit picker with preview panel)
- **`cli/completer.py`** — `AnteroomCompleter`: tab completion for /commands, @file paths, and conversation slugs
- **`cli/dialogs.py`** — Help dialog rendering
- **`cli/renderer.py`** — Rich terminal output: verbosity levels, thinking spinner with lifecycle phases, plan checklist rendering, inline diff rendering for file tools, tool call dedup, subagent rendering. Fullscreen mode support: `use_fullscreen_output(layout, invalidate_fn)` switches renderer to full-screen mode (redirects all console output to layout's output pane, updates status line instead of raw ANSI manipulation), `is_fullscreen()` detects fullscreen mode to adapt output handling (e.g., suppress cursor manipulation inside OutputPaneWriter)
- **`cli/exec_mode.py`** — Non-interactive mode for scripting/CI. JSON output, timeout, fail-closed approval. Exit codes: 0/1/124
- **`cli/plan.py`** — Planning mode helpers: `PLAN_MODE_ALLOWED_TOOLS`, plan file I/O, plan command parsing, `enter_plan_mode()`, `leave_plan_mode()`
- **`cli/instructions.py`** — ANTEROOM.md discovery (`.anteroom.md` > `ANTEROOM.md`, walk-up from cwd), global instructions, token estimation
- **`cli/skills.py`** — Skills registry: loads YAML skill files from `cli/default_skills/` (built-in), `~/.anteroom/skills/` (global), and `.anteroom/skills/` or `.claude/skills/` (project-level, walk-up discovery). Strict name validation (`[a-z0-9][a-z0-9_-]*`, rejects reserved slash-command names). Code-fence-aware `{args}` template interpolation (replaces outside fenced code blocks only). YAML error hints for common issues (flow mapping, colon escaping). Collision detection and warnings for skill shadowing. `MAX_SKILLS` hard limit (100). `SkillRegistry` class: `load(working_dir)` with atomic swap, `reload()`, `resolve_input(user_input)` returns (is_skill, expanded_prompt), `get_skill_descriptions()`, `get_invoke_skill_definition()`. Prompt size validation (`MAX_PROMPT_SIZE` 50KB). Auto-invocation via synthetic `invoke_skill` tool with OpenAI function schema

#### Tools
- **`tools/`** — ToolRegistry: `_handlers` + `_definitions`. Built-in: read_file, write_file, edit_file, bash, glob_files, grep, create_canvas, update_canvas, patch_canvas, run_agent, ask_user, introspect. Optional (with `anteroom[office]`): docx, xlsx, pptx. Safety gate: tier check → pattern detection → hard-block. File-modifying tools return `_old_content`/`_new_content` for diff rendering (stripped before LLM)
- **`tools/tiers.py`** — Risk tiers: READ/WRITE/EXECUTE/DESTRUCTIVE. Approval modes: AUTO/ASK_FOR_DANGEROUS/ASK_FOR_WRITES/ASK. Unknown/MCP tools default to EXECUTE
- **`tools/bash.py`** — Shell command execution with configurable sandboxing. `_check_sandbox()` enforces network/package/path/command restrictions before execution. Accepts `_sandbox_config: BashSandboxConfig` from `call_tool()`. On Windows: assigns subprocess to Win32 Job Object for kernel-level resource limits; rewrites multiline `python -c` commands to temp `.py` files to avoid `cmd.exe` truncation; resolves `python3` → `python` when `python3` is unavailable. Configurable timeout caps, output truncation, and audit logging via `security_logger`
- **`tools/security.py`** — Security utilities: hard-block patterns, path validation, `check_network_command()`, `check_package_install()`, `check_blocked_path()`, `check_custom_patterns()` for sandbox enforcement. Cross-platform: Unix tools, PowerShell, Windows package managers
- **`tools/sandbox_win32.py`** — Win32 Job Object sandbox via ctypes (no dependencies). `create_job_object()`, `assign_process()`, `terminate_job()`, `close_job()`, `setup_job_for_process()`. Enforces memory, process count, and CPU time limits. No-op on non-Windows. All functions return success/failure, never raise
- **`tools/safety.py`** — Pure detection: `check_bash_command()` (regex patterns), `check_write_path()` (sensitive paths). Returns `SafetyVerdict` with `is_hard_blocked`
- **`tools/canvas.py`** — Canvas create/update/patch with SSE streaming support
- **`tools/subagent.py`** — `run_agent` tool: isolated child AI sessions, same safety gates. Guarded by `SubagentLimiter`. Configurable via `safety.subagent`
- **`tools/introspect.py`** — Lets AI examine its own runtime context. READ tier (auto-allowed)
- **`tools/office_com.py`** — Shared COM lifecycle manager for Office tools on Windows. Singleton ComAppManager caches COM Application objects per prog_id, handles CoInitialize/CoUninitialize per thread via asyncio.to_thread(). Optional: requires pywin32 on Windows
- **`tools/office_docx.py`** — DOCX (Word) tool via python-docx (lib) or COM. WRITE tier. Graceful degradation. Actions: create, read, edit, track_changes (COM), comments (COM), headers_footers, insert_image, styles, export_pdf (COM), page_setup, sections, bookmarks (COM), toc (COM), find_regex
- **`tools/office_xlsx.py`** — XLSX (Excel) tool via openpyxl (lib) or COM. WRITE tier. Graceful degradation. Actions: create, read, edit, format_cells, merge_cells, freeze_panes, auto_filter, print_area, named_ranges, data_validation, conditional_format, comments, hyperlinks, images, protect, group_rows_cols, print_settings, charts, export_pdf (COM), sort (COM), pivot_tables (COM), sparklines (COM), slicers (COM)
- **`tools/office_pptx.py`** — PPTX (PowerPoint) tool via python-pptx (lib) or COM. WRITE tier. Graceful degradation. Actions: create, read, edit (with table_edits, shape_edits, notes_edits, delete_slides, duplicate_slides, template_fill, table_format, paragraph_edits, placeholder_edits, image_replacements sub-features), transitions (COM), animations (COM), insert_image, insert_shape, format_shape, master_layout, reorder_slides, embed_chart (COM), embed_table, export_pdf (COM), hyperlinks, headers_footers, sections (COM), group_shapes (COM), audio_video (COM), smartart (COM)

### Security Model

Single-user local app, OWASP ASVS Level 2. Auth: HttpOnly session cookies + CSRF double-submit + Origin validation. Stable auth token from Ed25519 key via HMAC-SHA256. Session store (memory or SQLite-backed) tracks creation time, last activity, and client IP for session validation and lifecycle management. IP allowlisting (CIDR or exact) gates access at middleware. Concurrent session limits prevent token reuse abuse. Session timeouts: 12-hour absolute, 30-minute idle. Middleware: rate limiting (120 req/min), body size (15MB), security headers. Tool safety: 4 risk tiers, 4 approval modes, 3 permission scopes (once/session/always). Path traversal and hard-block detection. Bash sandboxing: configurable network/package/path/command restrictions, timeout caps, output limits, audit logging. MCP tools gated at parent and sub-agent levels. Fails closed: no approval channel = blocked. Encryption at rest: optional SQLCipher integration (opt-in via `encrypt_at_rest`), key derived from Ed25519 identity key via HKDF-SHA256. Data retention: configurable policy with background worker, purges conversations and attachments older than retention days; cascades delete related messages, tool calls, embeddings.

### Database

SQLite with WAL journaling, FTS5 for search, foreign keys enforced. Optional SQLCipher encryption at rest (via `encrypt_at_rest` config). Schema in `db.py`. `init_db()` signature: `init_db(db_path, vec_dimensions=384, encryption_key=None)`. Key tables: conversations (with `type`, `slug`, and `working_dir` columns), messages (with token usage tracking), tool_calls (`approval_decision` audit), sources/source_chunks/source_tags/source_groups, canvases, message_embeddings, source_chunk_embeddings, artifacts (FQN-namespaced versioned entities with content-addressable hashing), artifact_versions (immutable version history with CASCADE delete), packs (UNIQUE namespace+name, version, source_path), pack_artifacts (junction table with CASCADE on pack delete, reference counting for shared artifacts), spaces (id, name, file_path, file_hash, UNIQUE name), space_paths (mapped directories for auto-detection, FK to spaces CASCADE), space_sources (junction table linking sources/groups/tags to spaces, FK CASCADE), pack_attachments (space_id column for space-scoped packs, UNIQUE INDEX with COALESCE). Conversations and folders have optional space_id columns. Optional sqlite-vec for vector similarity search. Retention worker cascades deletes conversations, messages, tool_calls, embeddings, and optionally attachment files.

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
- **`PackSourceConfig`** — Git-based pack source repos: `url` (git remote URL), `branch` (default "main"), `refresh_interval` (minutes, default 30, 0 = manual only). Top-level `pack_sources` list in config. URL scheme validation (rejects ext::, file://)

### Developer Workflow

Claude Code skills (`.claude/commands/`) and auto-loaded rules (`.claude/rules/`) enforce development standards. See `VISION.md` for product identity and scope guardrails.

**Skills**: `/ideate`, `/new-issue`, `/start-work`, `/commit`, `/submit-pr`, `/pr-check`, `/code-review`, `/deploy`, `/write-docs`, `/dev-help`, `/next`, `/triage`, `/cleanup`, `/a-help`

**Rules**: commit format, issue requirement, output formatting, vision alignment, security patterns, test requirements, feature parity

### Deployment

PyPI: `anteroom`. Deploy via `/deploy` skill (merge PR, CI, version bump, build, `twine upload`).

**Optional Dependencies** (declared in `pyproject.toml`):
- **`office`** — `python-docx>=1.0`, `openpyxl>=3.1.0`, `python-pptx>=1.0`. Required for built-in docx/xlsx/pptx tools. Enable with: `pip install anteroom[office]`
- **`office-com`** — `python-docx>=1.0`, `openpyxl>=3.1.0`, `python-pptx>=1.0`, `pywin32>=306` (Windows only). Full COM backend for native Office automation. Enable with: `pip install anteroom[office-com]`
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
