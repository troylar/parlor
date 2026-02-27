# Changelog

Release highlights for every Anteroom version. For full details including developer notes and upgrade instructions, see the linked GitHub Release.


---

## February 27, 2026

### v1.74.0

**New:**

- Spaces: named YAML workspaces bundling repos, packs, sources, instructions, and config overrides (#532)
- CLI `aroom space` subcommands and `/space` REPL commands for full space lifecycle (#551, #552)
- Space config overlay merges between personal and project layers with team enforcement (#553)
- Hot-reload file watcher detects space YAML changes automatically (#553)
- Web UI REST API for spaces with chat integration (#554)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.74.0)

### v1.73.2

**Fixed:**

- Pack install copies only manifest-referenced files; updates are atomic with rollback (#546)
- Health check no longer deletes pack-referenced artifacts or produces false orphan positives (#541, #545)
- CLI pack inputs validated against injection; API strips internal paths from responses (#545, #546)
- Lock file validation is now bidirectional — catches installed packs missing from lock (#546)
- Credential sanitization, `SELECT *` removal, and duplicate registry init cleaned up (#542, #543, #544)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.73.2)

### v1.73.1

**Fixed:**

- ArtifactRegistry now wired into runtime — 6-layer precedence is no longer dead code (#528)
- Pack install/remove operations wrapped in atomic transactions with proper rollback (#529)
- FQN regex rejects leading hyphens/dots and enforces 63-char segment limits (#530)
- Symlink rejection in pack manifest validation prevents TOCTOU attacks (#531)
- Credential sanitization extended to ssh:// and git:// URLs in error messages (#531)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.73.1)

### v1.73.0

**New:**

- `/pack` REPL commands: list, show, install, remove, sources, refresh, add-source — manage packs without leaving the REPL (#525)
- `/new-pack` skill (renamed from `/pack-create`): guided AI-driven pack authoring with manifest scaffolding and validation (#527)
- Welcome banner now shows skill/pack counts and first-run hint (#526)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.73.0)

### v1.72.1

**Fixed:**

- Artifact upsert race condition: concurrent creation no longer crashes with IntegrityError (#522)
- Malformed JSON metadata and invalid YAML in pack artifacts no longer crash reads/installs (#522)
- Pack refresh worker now uses exponential backoff on failures instead of retrying at normal interval (#522)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.72.1)

---

## February 26, 2026

### v1.72.0

**New:**

- Pack ecosystem: starter packs, pack attachments (global/project scope), local artifacts, and artifact import/migration (#507)
- Built-in `python-dev` and `security-baseline` starter packs auto-install at startup (#507)
- CLI commands: `pack attach`, `pack detach`, `artifact import`, `artifact create` (#507)

**Improved:**

- Path traversal prevention on artifact names and project paths (#507)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.72.0)

### v1.71.1

**New:**

- Comprehensive packs & artifacts documentation: 20 new pages covering concepts, quickstart, all 7 artifact types, manifest format, CLI/API reference, pack sources, lock files, health checks, and troubleshooting (#519)
- 8 end-to-end tutorials: install packs, create from scratch, share via git, team standardization, auto-updates, conflict management, health check diagnosis, CI/CD integration (#519)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.71.1)

### v1.71.0

**New:**

- Artifact health check: analyze all loaded artifacts for conflicts, shadows, duplicates, bloat, and quality issues (#508)
- Three interfaces: CLI (`aroom artifact check`), REPL (`/artifact-check`), and API (`GET /api/artifacts/check`) (#508)
- Auto-fix mode removes exact duplicate artifacts; JSON output for CI/CD integration (#508)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.71.0)

### v1.70.0

**New:**

- Pack distribution: configure Git-based pack sources with auto-clone, background refresh, and per-source intervals (#506)
- CLI `aroom pack sources` and `aroom pack refresh` commands for source management (#506)
- Lock file provenance: entries include `source_url` and `source_ref` for traceability (#506)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.70.0)

### v1.69.0

**New:**

- Pack management system: install, remove, update, and list artifact packs via CLI and REST API (#505)
- YAML manifests with namespaced naming, reference counting, and lock file generation for reproducible installs (#505)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.69.0)

### v1.68.0

**New:**

- Universal artifact system: skills, rules, instructions, context, memories, MCP servers, and config overlays as first-class versioned entities (#504)
- 6-layer precedence resolution for artifact loading (`built_in` through `inline`) (#504)
- CLI `aroom artifact list/show` commands and read-only API endpoints (#504)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.68.0)

### v1.67.0

**New:**

- Pack source git cache: clone, pull, and manage remote git repositories as pack sources (#509)
- URL scheme allowlist and credential sanitization for secure git operations (#509)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.67.0)

### v1.66.2

**Fixed:**

- Custom skill files with `.yml` extension now discovered alongside `.yaml` (#510)
- `/skills` output now shows searched directories with skill counts for debugging discovery issues (#510)
- `/new-skill` explicitly requires `.yaml` extension and verifies after writing (#510)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.66.2)

### v1.66.1

**Fixed:**

- Skill system hardening: reload safety, queue handling, code-fence-aware `{args}`, built-in command protection (#498)
- Default skills (`/commit`, `/review`, `/explain`, `/new-skill`) now use inline `{args}` placeholders (#498)
- Queued skill invocations no longer silently dropped while AI is responding (#498)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.66.1)

### v1.66.0

**New:**

- CLI `--project` flag for loading project context into chat, exec, and REPL sessions (#391)
- `aroom projects` command lists all projects with model, instructions, and last updated (#391)
- Resuming a project-linked conversation auto-loads project context (#391)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.66.0)

### v1.65.2

**New:**

- `/new-skill` built-in skill: interactive guide for creating custom skills with best practices (#489)
- `{args}` template variable support in skill prompts (#489)
- `/reload-skills` command and auto-reload on `/skills` for hot-reload without restart (#489)

**Fixed:**

- YAML parse errors when skills use `{args}` template variables (#489)
- Actionable error messages with line/column numbers for skill YAML syntax errors (#489)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.65.2)

### v1.65.0

**New:**

- CLI project management: `/project create`, `select`, `edit`, `delete`, `sources` commands with active project state (#344)
- Project instructions injected into system prompts with model override support (#344)

**Improved:**

- Introspect tool now triggers on context window and token budget questions (#344)
- Project name/instructions sanitized via `sanitize_trust_tags()` before system prompt injection (#344)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.65.0)

### v1.64.0

**New:**

- Working directory persistence: CLI conversations remember their project directory and restore it on resume (#274)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.64.0)

### v1.63.1

**Fixed:**

- Built-in `/a-help` skill updated with all current tools, CLI flags, REPL commands, config sections, and docs index (#487)

**Improved:**

- `/submit-pr` now automatically checks a-help.yaml for staleness during documentation freshness audit (#487)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.63.1)

### v1.63.0

**New:**

- Egress domain allowlist: restrict which external domains can be contacted for API calls, with team-enforceable policies (#453)
- SSRF prevention: block loopback, RFC-1918 private, link-local (cloud IMDS), multicast, and reserved addresses (#453)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.63.0)

### v1.62.0

**New:**

- Compliance rules engine: declarative config policy validation with 5 operators (must_be, must_not_be, must_match, must_not_be_empty, must_contain) (#447)
- Fail-closed at startup when compliance rules are violated, preventing misconfigured deployments (#447)
- `aroom config validate` CLI command for pre-deploy policy checks (#447)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.62.0)

### v1.61.0

**New:**

- Output content filter with system prompt leak detection: scans LLM responses for forbidden content and system prompt fragments via n-gram overlap analysis (OWASP LLM07 mitigation) (#449)
- Custom pattern blocking catches forbidden patterns during streaming before tokens reach the user (#449)
- Three configurable actions: warn, block, or redact — works across web UI, CLI REPL, and exec mode (#449)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.61.0)

### v1.60.0

**New:**

- Prompt injection detection with canary tokens: multi-layered defense scanning tool outputs for canary leakage, encoding attacks, and instruction override attempts (#448)
- Three detection techniques: CSPRNG canary tokens, base64/zero-width/homoglyph encoding attacks, and 6 ReDoS-safe heuristic patterns (#448)
- Configurable action modes (block/warn/log) with adjustable confidence thresholds, disabled by default (#448)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.60.0)

### v1.59.0

**New:**

- Data Loss Prevention (DLP) scanning pipeline: detects sensitive data patterns (SSN, credit card, email, phone, IBAN) in AI responses with configurable redact/block/warn actions (#445)
- Custom DLP regex patterns via config for domain-specific sensitive data detection (#445)
- ReDoS-safe pattern validation with static analysis rejection of pathological regex (#445)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.59.0)

### v1.58.0

**New:**

- Data retention policy: auto-purge old conversations with configurable `storage.retention_days`, background worker, and `aroom db purge` CLI command (#455)
- Encryption at rest: SQLCipher-based database encryption with HKDF-SHA256 key derivation from Ed25519 identity key, `aroom db encrypt` migration (#455)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.58.0)

### v1.57.1

**Improved:**

- Security documentation overhauled: 3 new pages (bash sandboxing, audit log, prompt injection defense), 4 rewritten pages, all config tables cross-verified against source (#469)
- README rewrite: fixed stale counts (ASVS L2, 2900+ tests, 12 tools, 4 approval modes), expanded security and feature sections (#469)
- SECURITY.md updated from ASVS v4.0 Level 1 to v5.0 Level 2 with 27-row threat model (#469)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.57.1)

### v1.57.0

**New:**

- Context trust tagging: all LLM context classified as trusted or untrusted with defensive XML envelopes to prevent indirect prompt injection (#366)
- Per-MCP-server trust levels configurable via `trust_level` field (default: untrusted) (#366)
- Structural system prompt separation with `[SYSTEM INSTRUCTIONS - TRUSTED]` / `[EXTERNAL CONTEXT - UNTRUSTED]` markers (#366)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.57.0)

### v1.56.0

**New:**

- Win32 Job Object sandbox: kernel-level memory, process count, and CPU time limits for bash commands on Windows (#297)
- Auto-detects Windows, no-op on macOS/Linux, zero new dependencies (ctypes only) (#297)
- Graceful degradation: if OS sandbox setup fails, Tier 1 config-level restrictions still apply (#297)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.56.0)

### v1.55.1

**Fixed:**

- File uploads (PDF, DOCX, text) now have their contents sent to the AI in web UI chat (#464)
- Source references preserved when files are attached via the upload button (#464)
- Extracted document text truncated at 50K chars to prevent oversized token consumption (#464)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.55.1)

### v1.55.0

**New:**

- Bash execution sandboxing: configurable network, package install, path, and command restrictions for the AI agent's bash tool (#450)
- Cross-platform detection covering Unix tools, PowerShell cmdlets, and Windows package managers (#450)
- Execution timeouts, output limits, and optional audit logging for all bash commands (#450)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.55.0)

### v1.54.0

**New:**

- Tool call rate limiting: per-minute, per-conversation, and consecutive failure limits to prevent runaway agent loops (#451)
- Two action modes (block or warn) with limits shared across parent and child sub-agents (#451)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.54.0)

### v1.53.0

**New:**

- Session hardening: pluggable session stores (memory or SQLite), configurable idle/absolute timeouts, concurrent session limits (#452)
- IP allowlisting with CIDR support — restrict access to specific networks or addresses (#452)
- Session IP binding — sessions locked to the IP that created them, invalidated on mismatch (#452)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.53.0)

### v1.52.0

**New:**

- Read-only mode: lock Anteroom to read-only operations via config, env var, or `--read-only` CLI flag (#454)
- Defense-in-depth: two-layer security with tool-list filtering and execution-time hard-deny backstop (#454)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.52.0)

---

## February 25, 2026

### v1.51.1

**New:**

- Structured JSONL audit log with HMAC-SHA256 chain tamper protection for enterprise security compliance (#444)
- `aroom audit verify` and `aroom audit purge` CLI commands for log integrity and retention management (#444)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.51.1)

### v1.51.0

**New:**

- Token budget enforcement for denial-of-wallet prevention — configurable per-request, per-conversation, and per-day limits with block/warn modes (#446)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.51.0)

### v1.50.3

**Fixed:**

- Canvas tools no longer prompt for approval — reclassified from WRITE to READ tier (#441)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.50.3)

### v1.50.2

**Fixed:**

- ask_user tool in web UI no longer hangs after clicking an option — SSE keepalive pings prevent stream death during long waits (#439)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.50.2)

### v1.50.1

**Fixed:**

- Hung stream no longer blocks web UI after page refresh — disconnect polling auto-cancels orphaned streams (#436)
- Stop button immediately resets client state for responsive UI during cancellation (#436)
- Page-load recovery detects active streams and shows Stop button (#436)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.50.1)

### v1.50.0

**New:**

- Web UI thinking indicator now shows lifecycle phases — connecting, waiting, streaming with char count, and stall detection (#435)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.50.0)

### v1.49.1

**Fixed:**

- Canvas streaming now renders with proper syntax highlighting from the start (#432)
- Canvas content no longer leaks before approval prompt (#432)
- Thinking animation dots render below the SYSTEM header, not above it (#434)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.49.1)

### v1.49.0

**New:**

- `/create-eval` built-in skill — generate promptfoo evals, shell tests, and VHS demos from descriptions or conversations (#430)
- Tutorial for project-local evals with copy-paste examples for all patterns (#430)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.49.0)

### v1.48.1

**New:**

- Comprehensive developer testing guide covering all 7 test layers — unit through red teaming (#428)
- Documents promptfoo eval setup, agent behavioral evals, VHS demo recordings, and deterministic output configuration (#428)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.48.1)

### v1.48.0

**New:**

- Auto-detect embedding support at startup — probes endpoint once instead of failing every 30s for enterprise APIs without embeddings (#109)
- Zero-config for both paths: embedding-capable APIs work automatically, others silently skip

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.48.0)

## February 24, 2026

### v1.47.0

**New:**

- Parallel MCP server startup — all servers connect simultaneously with live animated status display (#383)
- Subprocess stderr suppressed for clean terminal output during MCP connection (#383)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.47.0)

### v1.46.0

**New:**

- Tree-sitter codebase index — AI automatically understands your project's functions, classes, and import structure (#212)
- Supports 10 languages with token-budgeted output and graceful degradation without tree-sitter installed (#212)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.46.0)

### v1.44.0

**New:**

- Interactive conversation picker — type `/resume` with no argument to browse conversations with a live preview panel (#381)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.44.0)

### v1.43.3

**Fixed:**

- REPL autocomplete now includes `/slug`, `/upload`, `/usage`, and `/rename` commands (#377)
- `/help` dialog now shows `/upload` and `/usage` entries (#377)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.43.3)

### v1.43.2

**Fixed:**

- Resume hint now appears on all exit paths including Ctrl+C (#376)
- Added double Ctrl+C exit: first press clears buffer, second press exits with resume hint (#376)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.43.2)

### v1.43.1

*Maintenance release — bumped CodeQL CI action from v3 to v4.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.43.1)

### v1.43.0

**New:**

- AI-powered skill auto-invocation — say "commit my changes" or "review this PR" instead of typing `/commit` or `/review` (#267)
- Configurable via `cli.skills.auto_invoke` (default true); explicit `/command` still takes priority (#267)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.43.0)

### v1.42.0

**New:**

- Human-readable conversation slugs — conversations get auto-generated names like `bold-azure-cliff` for easy resume and reference (#367)
- Rename slugs with `/slug my-project` in CLI, with uniqueness suggestions when names are taken (#367)
- Slug display in CLI `/list` and web UI sidebar (#367)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.42.0)

### v1.41.0

**New:**

- Resume hint on CLI exit — shows `aroom chat -c` and `aroom chat -r <id>` commands when leaving a conversation with messages (#370)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.41.0)

### v1.40.0

**New:**

- Automatic cleanup of empty conversations on startup — keeps your conversation list clutter-free in both web UI and CLI (#363)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.40.0)

### v1.39.0

**New:**

- **Plan mode in web UI**: Toggle planning mode, review AI-generated plans in a side panel, approve to execute or reject to start over — full parity with CLI (#340)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.39.0)

### v1.38.0

**New:**

- **RAG pipeline**: Conversations now automatically retrieve relevant context from past conversations and knowledge sources via semantic similarity search (#349)
- Configurable similarity threshold, token budget, and chunk limits — works zero-config with sensible defaults

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.38.0)

### v1.37.1

**Fixed:**

- CLI paste preview no longer shows raw ANSI escape codes — collapsed multiline input renders colors correctly via Rich (#159)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.37.1)

## February 23, 2026

### v1.37.0

**New:**

- **Introspect tool**: AI can now examine its own runtime context — config, tools, MCP servers, safety gates, skills, and token budget — to answer self-awareness questions accurately (#332)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.37.0)

### v1.36.1

**Fixed:**

- Init wizard now skips redundant prompts when team config provides AI settings (#333)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.36.1)

### v1.36.0

**New:**

- **Named-list merge for team config**: MCP servers and shared databases merge by `name` field instead of replacing wholesale — overlay just the fields you need (#330)
- **Disable team items**: Set `enabled: false` in personal config to opt out of team-defined MCP servers or databases (#330)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.36.0)

### v1.35.0

**New:**

- **Project-scoped configuration**: Per-project `config.yaml` files auto-discovered via walk-up search from cwd, with deep merge, trust verification, and team enforcement (#325)
- **Required keys**: Project/team configs can declare required values with interactive prompting and masked input for secrets (#325)
- **Shared references**: Declare instructions, rules, and skills that load automatically per project (#325)
- **Live config reload**: Config files monitored via mtime polling — changes apply without restarting (#325)
- **Team config bootstrap**: `aroom init --team-config` trusts and configures team config in one step (#325)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.35.0)

### v1.34.1

**New:**

- **Renamed `/docs` to `/a-help`**: Built-in help skill renamed to avoid conflicts with project skills (#326)
- **Expanded `/a-help` config docs**: Inline reference now covers config layers, team config, enforce/required keys, onboarding, and directory equivalence (#326)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.34.1)

### v1.34.0

**New:**

- **Graceful tool limit handling**: Automatically caps tools per API request (default 128) to prevent errors when many MCP servers are connected (#311)
- **Configurable tool limit**: Set `ai.max_tools` in config or `AI_CHAT_MAX_TOOLS` env var to adjust the cap (#311)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.34.0)

### v1.33.0

**New:**

- **Team config with enforcement**: Shared YAML config with `enforce` list to lock settings (API endpoint, model, safety mode) across all team members (#316)
- **.claude directory support**: `.anteroom` and `.claude` directories are now interchangeable for instructions, skills, team config, and rules (#316)
- **Web UI enforcement**: Config API rejects changes to enforced fields (HTTP 403) and exposes locked fields to the UI (#316)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.33.0)

### v1.32.0

**New:**

- **Structured options for ask_user**: AI can present multiple-choice options as numbered list (CLI) or buttons (web UI), with freeform fallback (#312)
- **Cancel support**: Esc in CLI or Cancel button in web UI sends unambiguous cancelled signal to the AI (#312)
- **Web UI ask_user rendering**: Fully functional styled prompt cards replace previously silent/broken SSE events (#312)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.32.0)

### v1.31.0

**New:**

- **Debug logging for MCP troubleshooting**: `aroom --debug chat` or `AI_CHAT_LOG_LEVEL=DEBUG` enables debug logging to stderr for diagnosing MCP server connections and tool routing (#313)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.31.0)

### v1.30.0

**New:**

- **Token usage tracking & cost estimation**: Track token consumption per model with `/usage` command in CLI, `aroom usage` subcommand, and `GET /api/usage` web endpoint (#226)
- **Configurable cost rates**: Set per-model input/output token rates for cost estimation via `cli.usage.model_costs` config (#226)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.30.0)

### v1.29.0

**New:**

- **Document upload with text extraction**: Upload PDFs and DOCX files to the knowledge base with automatic text extraction, chunking, and semantic search indexing (#179)
- **CLI `/upload` command**: Upload files directly from the CLI REPL with MIME detection and size validation (#179)
- **Project-scoped source search**: Filter semantic search results to sources linked to a specific project (#179)

**Fixed:**

- Bumped `pypdf` minimum to >=6.7.1 to address 6 known vulnerabilities (#179)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.29.0)

### v1.28.0

**New:**

- **Per-server MCP tool filtering**: Control which tools each MCP server exposes using `tools_include`/`tools_exclude` with fnmatch glob patterns (#306)
- **MCP tool warning threshold**: Warns when total MCP tools exceed a configurable limit (default 40) with per-server breakdown (#306)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.28.0)

---

## February 22, 2026

### v1.27.0

**New:**

- **Ask User tool**: AI now pauses mid-turn to ask clarifying questions instead of guessing and continuing, saving tokens and producing better results (#299)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.27.0)

### v1.26.1

**Fixed:**

- **Aggressive plan suggestions silenced**: Default `auto_mode` changed from `"suggest"` to `"off"` and threshold raised from 5 to 15 tool calls, eliminating noise on routine tasks (#302)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.26.1)

### v1.26.0

**New:**

- **Live plan checklist**: `/plan approve` now renders a real-time checklist above the thinking spinner, tracking each implementation step through pending, in-progress, and complete states (#166)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.26.0)

### v1.25.0

**New:**

- **Inline diff rendering in CLI**: File edits and creations now show Claude Code-style color-coded diffs with line numbers, red/green backgrounds, and context collapsing (#281)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.25.0)

### v1.24.6

**Fixed:**

- **Changelog rendering on ReadTheDocs**: Section headers were rendering inline with bullet points — added blank lines for proper MkDocs Material list rendering (#295)
- Completed ~45 truncated changelog entries from initial backfill (#295)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.24.6)

### v1.24.5

**Improved:**

- **Changelog readability**: Release entries now grouped by type — New, Fixed, Improved — for easier scanning (#295)
- **Changelog navigation**: Moved to top-level nav in docs instead of hidden under Advanced (#295)
- Deploy skill auto-generates future entries in the new type-segregated format (#295)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.24.5)

### v1.24.4

**New:**

- **Changelog with release highlights**: Every release now has a highlights entry in `docs/advanced/changelog.md`, viewable on ReadTheDocs (#290)
- Backfilled all 80 existing releases; `/deploy` skill auto-appends new entries going forward

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.24.4)

### v1.24.3

**Improved:**

- **Replaced Snyk with Semgrep + CodeQL** for open-source SAST scanning — no more external tokens or Node.js required in CI (#289)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.24.3)

### v1.24.2

**Fixed:**

- Fixed MCP tool argument validation that incorrectly blocked legitimate text content (newlines, parentheses, semicolons, etc.) when passed to MCP servers like filesystem or database tools (#291)
- Improved MCP tool error messages to include server name and tool context for easier debugging (#291)
- Sanitized MCP error output so raw server exceptions are logged server-side only, not exposed to the user (#291)

**Improved:**

- Updated README.md MCP safety description to reflect current behavior (#291)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.24.2)

### v1.24.1

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.24.1)

### v1.24.0

**New:**

- **OpenAI-compatible proxy endpoint**: External tools using the OpenAI SDK can route requests through Anteroom to the upstream API (#285)
- Endpoints: `GET /v1/models` and `POST /v1/chat/completions` with full streaming support
- Opt-in via `proxy.enabled: true` — disabled by default for security

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.24.0)

### v1.23.0

**New:**

- **Iterative plan refinement**: `/plan edit` opens in `$EDITOR`, `/plan reject` triggers AI revision (#270, #271)
- **Inline planning**: `/plan <prompt>` enters planning mode in one command (#265)
- Auto-plan suggestions when tasks exceed tool-call threshold (#265)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.23.0)

### v1.22.1

**New:**

- **Planning Mode**: AI can now generate a structured step-by-step plan before executing tasks. Start planning mode with `aroom chat --plan` or `/plan on` during a session (#264)
- **Plan Editing**: Open your plan in `$VISUAL`/`$EDITOR` with `/plan edit` to review and modify it before approving execution. (#270)

**Improved:**

- Added a feature parity development rule ensuring all new features work equivalently in both the CLI and web UI (#275)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.22.1)


---

## February 21, 2026

### v1.22.0

**New:**

- **Built-in `/docs` skill**: Look up Anteroom documentation without leaving the CLI — covers config, flags, tools, skills, and architecture (#262)
- Embeds quick-reference tables for instant answers; consults 42 documentation files for deeper questions

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.22.0)

### v1.21.0

**New:**

- **Non-interactive exec mode**: `aroom exec "prompt"` for scripting and CI pipelines (#232)
- Supports stdin piping, `--json` output, timeout control, and conversation persistence

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.21.0)

### v1.20.1

**Fixed:**

- Fixed stale thinking line text persisting after stream retry — the "Stream timed out" and "retrying in Ns" text no longer flashes or leaves ghost content on the thinking line (#253)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.20.1)

### v1.20.0

**New:**

- **ANTEROOM.md Project Conventions**: Anteroom now formally supports `ANTEROOM.md` as a project-level conventions file that the AI follows consistently across both CLI and web UI (#215)
- Auto-discovers conventions walking up from your working directory (#215)
- **Web UI now loads ANTEROOM.md** — previously CLI-only, conventions now apply in both interfaces (#215)

**Improved:**

- Removed legacy `PARLOR.md` instruction filename support — use `ANTEROOM.md` going forward (#215)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.20.0)


---

## February 20, 2026

### v1.19.0

**New:**

- **Trust Prompts for Project Instructions**: Anteroom now prompts you before loading project-level `ANTEROOM.md` files into the AI context. This prevents prompt injection from untrusted project directories (#219)
- Trust decisions are persisted with SHA-256 content hash verification — you only need to approve once per project (#219)
- If the file changes, you'll be re-prompted to review and approve the new content (#219)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.19.0)

### v1.18.4

**Fixed:**

- **Fixed CLI hang after pressing Escape** — pressing Escape to cancel a running command could leave the CLI unresponsive, requiring a force-quit. The REPL now cleanly cancels the active operation and returns to the prompt (#243)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.18.4)

### v1.18.3

**Fixed:**

- **API error handling during streaming** — Previously, HTTP errors from the AI provider (like 500 Internal Server Error, 502 Bad Gateway, or 404 Not Found) could crash the stream or produce confusing output. Now handled gracefully with clear error messages (#241)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.18.3)

### v1.18.2

**Fixed:**

- Fixed the CLI thinking indicator ("Thinking...") briefly flashing then dropping to a blank line on the very first message in a new REPL session. Subsequent messages were unaffected (#239)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.18.2)

### v1.18.1

**Fixed:**

- **Fixed timeout enforcement on API connections** — The configured `request_timeout` (default 120s) was not enforced as a hard deadline during the initial API connection phase, allowing slow connections to hang indefinitely (#237)
- **Fixed Escape key ignored during connecting phase** — Pressing Escape while the API was connecting had no effect until the connection completed or timed out. Now cancels immediately (#237)
- **Fixed cancel-during-retry loop** — If the user pressed Escape during a retry backoff delay, the retry loop could re-enter the connection attempt instead of returning to the prompt (#237)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.18.1)

### v1.18.0

**New:**

- **Configurable Timeouts and Thresholds**: Every timeout, threshold, and limit that was previously hardcoded is now a config field with a sensible default (#235)
- `write_timeout` — time to send request body (default: 30s)
- `pool_timeout` — wait for free connection from pool (default: 10s)

**Fixed:**

- **Escape during stalled stream now cancels cleanly** — Previously, pressing Escape while a stream was stalled would trigger a retry countdown instead of cancelling. Now returns to the prompt immediately (#235)
- **Stalled streams abort faster** — Added per-chunk stall timeout (default 30s) so streams that go silent mid-response are aborted sooner instead of waiting for the full request timeout (#235)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.18.0)

### v1.17.1

**Fixed:**

- Fixed the CLI thinking spinner showing stale phase text ("waiting for first token", "streaming · N chars") and "esc to cancel" hint on the final line after a response completes (#231)
- Fixed the per-phase timer not always appearing during the "waiting for first token" phase. The timer now starts immediately when thinking begins, rather than waiting for the first phase transition (#231)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.17.1)

### v1.17.0

**New:**

- **Real-Time Connection Health Monitor for CLI**: The CLI thinking spinner now shows live connection status so you always know what's happening during AI interactions (#221)
- **Phase tracking**: See "connecting", "connected · waiting for first token", and "streaming · N chars" as the request progresses (#221)
- **Per-phase timing**: Each phase shows how long it's been active (e.g., "waiting for first token (5s)")

**Fixed:**

- Fixed error messages leaking internal API details — now shows generic "AI request error" instead of raw provider messages (#221)
- Fixed exception class names appearing in retry event payloads (#221)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.17.0)

### v1.16.3

**Fixed:**

- **Fixed confusing "HARD BLOCKED" message after approving dangerous commands.** Previously, when you approved a dangerous command like `rm -rf`, the system would still show a "HARD BLOCKED" error. Now bypasses the redundant safety check after explicit approval (#217)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.16.3)

### v1.16.2

**Fixed:**

- **Smarter API timeouts**: Replaced the single 120-second timeout with three phase-aware timeouts — connect (5s), first-token (30s), and stream (120s). This prevents long waits when the API is unreachable (#213)
- **Automatic retry on transient errors**: When the API times out or drops a connection, Anteroom now automatically retries up to 3 times with exponential backoff (#213)
- **Fixed phantom thinking timer after timeout**: Previously, if the API timed out, the thinking spinner would restart and keep counting up even after the error message was shown (#213)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.16.2)

### v1.16.1

**Fixed:**

- CLI no longer prints noisy tracebacks when pressing Ctrl+C with MCP servers connected. Shutdown errors are now suppressed from terminal output and logged at debug level (#208)
- Fixed a test that would fail when `AI_CHAT_API_KEY` was set in the shell environment (#208)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.16.1)

### v1.16.0

**New:**

- **Granular Request Lifecycle Phases in Thinking Indicator**: The thinking spinner now shows exactly where time is being spent during AI responses, making it easy to diagnose slow connections (#203)
- **Connecting** — shown while establishing connection to the AI API (#203)
- **Waiting for first token** — shown after the request is sent, while waiting for the model to start responding (#203)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.16.0)

### v1.15.0

**New:**

- **Rich Markdown in Resume Recap**: When resuming a conversation with `/last` or `/resume`, the assistant's last message is now rendered with full Rich Markdown formatting (#199)
- Long assistant messages truncate at line boundaries to preserve markdown structure
- Truncation limit increased from 300 to 500 characters for better context

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.15.0)

### v1.14.11

**Fixed:**

- **Thinking spinner no longer freezes during API stalls** — The CLI thinking timer previously stuck at "1s" when the API was slow to respond between tool calls. Now uses a background ticker task that updates independently (#197)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.11)

### v1.14.10

**New:**

- **`--port` flag**: Override the configured port directly from the command line
- **`AI_CHAT_PORT` environment variable**: Set a default port via environment variable, useful for scripts and containerized setups
- **Smarter browser launch**: The browser now waits until the server is actually ready before opening, preventing "connection refused" errors on slower startups

**Fixed:**

- **Port-in-use errors now show actionable guidance** — when port 8080 (or your configured port) is already taken, Anteroom now prints a clear message with the `--port` flag and `AI_CHAT_PORT` env var as alternatives (#193)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.10)

### v1.14.9

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.9)

### v1.14.8

**Fixed:**

- **Fixed thinking indicator hanging indefinitely** — When an API stalls mid-stream (no chunks arriving), Anteroom now detects the stall and times out gracefully instead of spinning forever (#191)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.8)

### v1.14.7

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.7)


---

## February 19, 2026

### v1.14.6

**Fixed:**

- API connection and authentication errors now show clear, actionable messages instead of raw Python tracebacks or generic "internal error" text (#121)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.6)

### v1.14.5

**Fixed:**

- **Fixed stacking approval prompts in CLI.** When multiple MCP tools needed approval at the same time, prompts would stack on top of each other and spam "terminal" output. Now serialized with proper queueing (#187)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.5)

### v1.14.4

**New:**

- **ESC cancel hint on CLI thinking line**: When the AI is thinking for more than 3 seconds, a muted "esc to cancel" hint now appears on the thinking line. This makes the cancel shortcut discoverable without cluttering the UI (#185)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.4)

### v1.14.3

**Fixed:**

- **Embedding worker no longer retries unembeddable messages forever.** Previously, short messages (< 10 characters), messages that returned no embedding, and messages that repeatedly failed to store would be retried indefinitely. Now marked as skipped after detection (#183)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.3)

### v1.14.2

**Fixed:**

- Fixed Ctrl+C causing unhandled `ExceptionGroup` errors during MCP server shutdown (#174). The MCP SDK uses `anyio` TaskGroups internally, which raise `ExceptionGroup` on cancellation. Now caught and handled cleanly

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.2)

### v1.14.1

**Fixed:**

- **File uploads now accept common document formats** — Uploading Office documents (.docx, .xlsx, .pptx, .doc, .xls, .ppt), Markdown files, JSON, YAML, TOML, and other common formats no longer fails with a MIME type validation error (#176)
- **Markdown and text files upload correctly even when browsers send no MIME type** — When a browser sends `application/octet-stream` (no MIME type detected), the server now infers the type from the file extension (#176)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.1)

### v1.14.0

**New:**

- **Knowledge Sources**: A global knowledge store for your projects — upload files, save text notes, and bookmark URLs that persist across conversations. Sources can be tagged, grouped, and linked to projects (#180)
- Create text, URL, and file-based knowledge sources (#180)
- Full web UI for browsing, creating, editing, and deleting sources (#181)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.14.0)

### v1.13.0

**New:**

- **Local Embeddings — No API Key Required**: Anteroom now generates vector embeddings locally using [fastembed](https://github.com/qdrant/fastembed), an ONNX-based embedding library that runs entirely offline (#172)
- Default model: `BAAI/bge-small-en-v1.5` (384 dimensions, ~50MB download on first use)
- Install with: `pip install anteroom[embeddings]`

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.13.0)

### v1.12.3

**Fixed:**

- MCP server connection failures are now logged cleanly without a raw traceback. When an MCP server rejects a connection or fails the handshake, Anteroom presents a clear error message and continues starting up (#170)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.12.3)

### v1.12.2

**Fixed:**

- **Fixed CLI crash when reviewing codebases with special tokens**: The CLI would crash with a tiktoken error when message content contained special token patterns. Now handled gracefully (#168)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.12.2)

### v1.12.1

**Fixed:**

- **Narration cadence now actually works**: The narration cadence feature (introduced in v1.11.0) was not producing any output for modern models like GPT-4o that return empty content with tool calls. Fixed to inject narration prompts correctly (#169)
- Narration now fires reliably regardless of model behavior (#169)
- Default cadence unchanged: every 5 tool calls (`ai.narration_cadence: 5`)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.12.1)

### v1.12.0

**New:**

- **Configurable Tool Call Dedup**: When the AI makes many consecutive tool calls of the same type (e.g., editing 10 files in a row), they're now automatically collapsed into a summary line for cleaner output (#59)
- CLI: consecutive same-type tool calls collapse with a count summary (e.g., "... edited 5 files total") (#59)
- Web UI: consecutive same-type tool calls group into a collapsible `<details>` element with count (e.g., "edit_file × 5") (#59)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.12.0)


---

## February 18, 2026

### v1.11.0

**New:**

- **Progress Updates During Long Agentic Runs**: When the AI executes many tool calls in sequence (editing files, running tests, exploring code), it now gives periodic progress summaries so you know what's happening (#157)
- Configurable via `ai.narration_cadence` in config.yaml (default: every 5 tool calls) (#157)
- Set to `0` to disable and restore the previous silent behavior

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.11.0)

### v1.10.2

**Fixed:**

- **API timeout recovery**: After a timeout, the next request no longer hangs indefinitely. Previously, a timeout would leave the httpx connection pool in a broken state. Now properly cleaned up (#155)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.10.2)

### v1.10.0

**New:**

- **Claude Code-Quality System Instructions**: The default system prompt for `aroom chat` has been completely rewritten to match the quality and structure of professional agentic coding assistants (#153)
- **Tool preference hierarchy**: The AI now strongly prefers dedicated tools (read_file, edit_file, grep, glob_files) over bash for file operations, reducing errors and improving reliability (#153)
- **Code modification guidelines**: Instructions to read before editing, match codebase conventions, avoid over-engineering, and produce working code — not prototypes (#153)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.10.0)

### v1.9.4

**Fixed:**

- **Tool call notifications no longer disappear mid-session.** In multi-iteration agent loops (where the AI calls tools, thinks, then calls more tools), tool call panels in the Web UI were being cleared between iterations. Now preserved across the full response (#151)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.9.4)

### v1.9.3

**Fixed:**

- **Embedding worker no longer retries forever** when the embedding API returns a permanent error (e.g., model not found, invalid credentials). Previously, the worker would retry with exponential backoff indefinitely (#149)
- **Permanent errors** (404 model not found, 422 unprocessable, failed auth) immediately disable the worker with a clear log message
- **Transient errors** (429 rate limit, 503 server error, timeouts) trigger exponential backoff: 30s → 60s → 120s → up to 300s

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.9.3)

### v1.9.2

**Fixed:**

- Fixed CLI completion menu using white-on-black colors that clashed with the dark terminal theme — now uses the dark palette (gold highlight, chrome text on dark background) (#147)
- Added above-cursor positioning attempt for the completion menu to reduce clipping when the prompt is near the terminal bottom (best-effort — full fix coming in a future release) (#147)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.9.2)

### v1.9.1

**Improved:**

- Optimized `/code-review` and `/submit-pr` Claude Code skills to eliminate redundant agent work during deploy cycles, reducing token usage by ~170k per deploy (#145)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.9.1)

### v1.9.0

**New:**

- **Sub-Agent Loading Indicator**: When a sub-agent is running in the Web UI, you now see a distinctive loading state instead of the generic tool call panel (#143)
- A pulsing accent border that animates while the sub-agent works
- A prompt preview showing what the sub-agent is doing

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.9.0)

### v1.8.0

**New:**

- **MCP Tools in Sub-Agents**: Sub-agents spawned via `run_agent` can now access MCP (Model Context Protocol) tools from connected servers. Previously, child agents only had access to built-in tools (#100)
- MCP tool definitions are merged into the child agent's tool list (#100)
- Child agents can call MCP tools through real MCP servers (e.g., time, filesystem, databases)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.8.0)

### v1.7.0

**Fixed:**

- **CLI text readability on dark terminals**: Rich's `[dim]` style (SGR 2 faint) was nearly invisible on most dark terminal themes, making tool results, approval prompts, and metadata unreadable (#140)
- Replaced all `[dim]` and `grey62` markup with a defined color palette that meets WCAG AA contrast ratios (#140)
- Four named constants: `GOLD` (accents), `SLATE` (labels), `MUTED` (secondary text), `CHROME` (UI chrome)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.7.0)

### v1.6.0

**New:**

- **Sub-Agent Orchestration**: The AI can now spawn parallel child agents using the `run_agent` tool to break complex tasks into independent subtasks. Each sub-agent runs in its own conversation context (#95)
- Sub-agents execute in parallel with concurrency control via `asyncio.Semaphore` (#95)
- Configurable limits: max concurrent (5), max total (10), max depth (3), max iterations (15), wall-clock timeout (120s)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.6.0)

### v1.5.1

**Improved:**

- Remove stale test count tracking from CLAUDE.md and skill definitions — the hardcoded count went stale constantly and skills wasted cycles checking/updating it (#138)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.5.1)

### v1.5.0

**New:**

- **Issue Lifecycle Management**: Three new Claude Code skills for managing the issue → branch → PR → deploy lifecycle, plus seven GitHub labels for tracking priority and status (#136)
- `/next` — Prioritized work queue sorted by priority labels and VISION.md direction areas. Shows what to work on next with rationale (#136)
- `/triage` — Set priority on individual issues or AI-reassess all open issues against VISION.md. Optionally updates ROADMAP.md (#136)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.5.0)

### v1.4.11

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.11)

### v1.4.10

**Fixed:**

- **Stale Auth Cookie Recovery on Upgrade**: Users upgrading from pre-identity versions (before v1.4.5) could get stuck in an authentication loop where the browser had an outdated session cookie (#128)
- Server now attaches a fresh session cookie to 401 responses, so browsers auto-recover without a manual page refresh (#128)
- Partial identity configs (user_id present but missing private_key) are now auto-repaired on server startup (#128)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.10)

### v1.4.9

**New:**

- **CLI Startup Progress Feedback**: The CLI no longer sits silently during bootstrap. Dim animated spinners now show activity during the three slow startup phases (#122):
- MCP server connections (#122)
- AI service validation (#122)

**Fixed:**

- Fixed compatibility with newer OpenAI models (e.g., gpt-5.2) that reject the deprecated `max_tokens` parameter in `aroom --test`. Now uses `max_completion_tokens` (#122)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.9)

### v1.4.8

**Fixed:**

- Fixed the web UI "stuck thinking" animation that would never dismiss after a response completed. The thinking indicator was being created multiple times but only one instance was tracked for dismissal (#128)
- Fixed 401 authentication errors for users upgrading from pre-identity versions. The chat stream now properly handles expired sessions instead of showing an opaque error (#128)
- Fixed SSE EventSource reconnect loop — persistent auth failures (3+ consecutive) now trigger session recovery instead of reconnecting indefinitely. (#128)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.8)

### v1.4.7

**Fixed:**

- **CI: Snyk security scan now passes green** — the Snyk SCA scan was crashing due to a dependency resolver bug in the Snyk Docker container (not an actual vulnerability in Anteroom). Switched to a workaround configuration (#126)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.7)

### v1.4.6

**Fixed:**

- Fixed Windows mapped network drive paths resolving to blocked UNC paths. On Windows, accessing files on mapped drives (e.g., `X:\test` where `X:` maps to a network share) no longer triggers a false path-traversal block (#124)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.6)

### v1.4.5

**Improved:**

- Hardened deploy workflow to handle recurring merge failures — auto-rebases, waits for CI, uses `--admin` only when non-required checks fail (#120)
- Fixed pre-push hook blocking version bump pushes with `--no-verify` (#120)
- Fixed zsh glob expansion error on `*.egg-info` cleanup (#120)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.5)

### v1.4.4

**Fixed:**

- Fixed Rich markup injection in approval output — tool names containing brackets or colons (common with MCP tools) are now properly escaped (#111)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.4)

### v1.4.3

**Fixed:**

- **UI hangs after MCP tool approval (#110)**: After approving MCP tool calls, the web UI could become completely unresponsive. This release fixes five interrelated issues (#110)
- **Stale stream detection** — when a browser tab disconnects or times out, the server now detects the stale SSE stream and cleans it up instead of blocking new requests (#110)
- **Thinking spinner stuck forever** — the "thinking" animation now correctly dismisses on all completion paths including errors and canvas operations (#110)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.3)


---

## February 17, 2026

### v1.4.2

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.2)

### v1.4.1

**Fixed:**

- **MCP tool approval flow no longer stalls after clicking Allow.** Previously, after approving an MCP tool in the web UI, there was no visual feedback that the tool was executing. Now shows a progress indicator immediately (#108)
- **CLI approval prompt now accepts keyboard input.** The tool approval prompt in the CLI REPL was unresponsive — you couldn't type y/n/a/s. Fixed by integrating with prompt_toolkit's input handling (#108)

**Improved:**

- CLI banner now correctly shows **ANTEROOM** instead of the old project name, with the updated tagline "the secure AI gateway."

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.1)

### v1.4.0

**New:**

- **Tool Approval System**: A Claude Code-style safety gate for AI tool execution. Every tool is assigned a risk tier (read, write, execute, destructive), and the approval mode determines which tiers require user confirmation (#106)
- **4 risk tiers**: read (safe), write (modifies files), execute (runs code), destructive (irreversible)
- **4 approval modes**: `auto` (no prompts), `ask_for_dangerous` (destructive only), `ask_for_writes` (default — write+execute+destructive), `ask` (alias)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.4.0)

### v1.3.1

**Fixed:**

- **New Chat button broken** — Clicking "New Chat" in the Web UI failed with a 415 error when no project was selected. The Content-Type header was only sent for project-scoped requests (#104)
- **CSP inline script blocked** — The Content Security Policy hash for the theme initialization script was stale, causing the browser to block it. Updated to match the current script content (#104)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.3.1)

### v1.3.0

**New:**

- **Canvas Tools with Real-Time Streaming**: Anteroom now includes a canvas panel for AI-generated content alongside chat. When the AI writes code, documents, or structured content, it streams into a side panel in real time (#89)
- **Create canvas** — AI can open a canvas panel with any content (#89)
- **Update canvas** — Full content replacement for major revisions (#89)

**Improved:**

- Note and document conversation types for non-chat content (#89)
- Canvas CRUD API endpoints for programmatic access (#89)
- Product vision document (VISION.md) establishing project guardrails (#88)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.3.0)


---

## February 16, 2026

### v1.2.0

**New:**

- **Semantic Search**: Anteroom now supports vector similarity search across your conversation history, powered by sqlite-vec. Search finds semantically related messages even when exact keywords don't match (#82)
- Semantic search API endpoints: `/api/search/semantic` and `/api/search/hybrid` (#82)
- Background embedding worker processes messages automatically using any OpenAI-compatible embedding API (#82)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.2.0)

### v1.1.0

**New:**

- **Cryptographic Identity (#68)**: Every Anteroom user now gets a unique cryptographic identity — a UUID paired with an Ed25519 keypair. This is the foundation for future features like message signing and multi-user attribution (#68)
- UUID + Ed25519 keypair generated automatically on first run or via `aroom init` (#68)
- Private key stored securely in `config.yaml` (file permissions set to 0600) (#68)

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v1.1.0)


---

## February 15, 2026

### v0.9.1

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.9.1)

### v0.9.0

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.9.0)

### v0.8.3

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.8.3)

### v0.8.2

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.8.2)

### v0.8.1

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.8.1)

### v0.8.0

**New:**

- **Verbosity system**: Three display modes for tool calls — compact (default), detailed, and verbose
- **`/detail` command**: Replay last turn's tool calls with full arguments and output on demand
- **Live tool spinners**: Each tool call shows an animated spinner while executing

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.8.0)

### v0.7.2

**Fixed:**

- Connection failures now show descriptive error context instead of generic messages

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.7.2)

### v0.7.1

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.7.1)


---

## February 14, 2026

### v0.7.0

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.7.0)

### v0.6.9

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.6.9)

### v0.6.8

*Maintenance release — see GitHub Release for details.*

[GitHub Release](https://github.com/troylar/anteroom/releases/tag/v0.6.8)
