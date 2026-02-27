# Concepts

How Anteroom works under the hood.

## Dual Interface, Shared Core

Anteroom provides two interfaces --- a web UI and a CLI --- that share the same agent loop and storage layer. This is the central design pattern.

```
Web UI (routers/) ──┐
                    ├──→ agent_loop.py → ai_service.py → OpenAI-compatible API
CLI (cli/repl.py) ──┘         │
                         tools/ + mcp_manager.py
                              │
                         storage.py → SQLite (shared DB)
```

Changes to tool handling, streaming, or message building affect both interfaces. A conversation started in the CLI shows up in the web UI sidebar, complete with tool call history and attachments.

## Agent Loop

The agent loop (`services/agent_loop.py`) is Anteroom's core execution engine. When you send a message:

1. Your message is added to the conversation history
2. The history is sent to the AI API with available tool definitions
3. The AI responds with text and/or tool calls
4. Tool calls execute (in parallel when multiple are returned)
5. Tool results are added to the history
6. Steps 2--5 repeat until the AI responds with text only (no more tool calls)

This loop runs up to **50 iterations** per turn by default (configurable via `cli.max_tool_iterations`). The AI decides when to stop calling tools.

## Parallel Tool Execution

When the AI returns multiple tool calls in a single response, they execute concurrently via `asyncio.as_completed` rather than sequentially. This means a response that calls `read_file` on three different files runs all three reads simultaneously.

## Prompt Queue

Both interfaces support prompt queuing. You can submit new messages while the AI is still responding. Messages queue (up to 10) and process in FIFO order when the current response finishes.

In the web UI, queued messages return `{"status": "queued"}` instead of opening a new SSE stream. In the CLI, the input prompt stays active at the bottom of the terminal while output streams above it.

## Auto-Compact

Anteroom tracks token usage using tiktoken (`cl100k_base` encoding). When the conversation exceeds 100,000 tokens, auto-compact triggers before the next prompt. The full history is summarized into a single message, preserving key decisions, file paths, code changes, and task state.

See [Context Management](../cli/context-management.md) for details.

## Tool Resolution

Built-in tools and MCP tools work side by side. When the AI calls a tool:

1. **Built-in tools are checked first** --- if the tool name matches a registered built-in (`read_file`, `write_file`, etc.), it executes locally
2. **MCP tools are checked second** --- if no built-in matches, the call is forwarded to the appropriate MCP server

Use `/tools` in the CLI to see all available tools from both sources.

## Storage

Everything is stored in SQLite with WAL journaling and FTS5 full-text search. The database schema includes tables for conversations, messages, attachments, tool calls, projects, folders, and tags. All IDs are UUIDs. File permissions are locked down to owner-only (`0600` for files, `0700` for directories).

## Configuration Hierarchy

Configuration follows a layered approach where each layer can override the previous one:

```
┌─────────────────────────────────────────────────────────────┐
│  Enforced team config fields (cannot be overridden)         │  ← Highest
├─────────────────────────────────────────────────────────────┤
│  CLI flags (--port, --approval-mode, etc.)                  │
├─────────────────────────────────────────────────────────────┤
│  Environment variables (AI_CHAT_*)                          │
├─────────────────────────────────────────────────────────────┤
│  Personal config file (~/.anteroom/config.yaml)             │
├─────────────────────────────────────────────────────────────┤
│  Team config file (.anteroom/team.yaml)                     │
├─────────────────────────────────────────────────────────────┤
│  Built-in defaults                                          │  ← Lowest
└─────────────────────────────────────────────────────────────┘
```

### Processing Order

When Anteroom starts, it builds its configuration by:

1. **Loading personal config** from `~/.anteroom/config.yaml`
2. **Discovering team config** (CLI flag → env var → personal config field → walk-up from cwd)
3. **Trust-verifying team config** (SHA-256 hash check, prompt on first encounter)
4. **Deep-merging** team config (base) with personal config (overlay)
5. **Applying enforcement** — re-applying team values for enforced fields
6. **Applying environment variables** — `AI_CHAT_*` vars override the merged result
7. **Building typed config objects** — validation, defaults, clamping
8. **Applying CLI flag overrides** — flags like `--port`, `--approval-mode`

The exception to the normal override chain is **team config enforcement**: any field listed in the team config's `enforce` list is locked to the team value, regardless of what personal config, env vars, or CLI flags say.

See [Configuration](../configuration/index.md) for the full reference and [Team Configuration](../configuration/team-config.md) for enforcement details.

## Project Context

Anteroom loads project-specific context from multiple sources:

### Instructions (ANTEROOM.md / CLAUDE.md)

An instruction file in your project root injects context into every conversation. Anteroom walks up from the working directory to find the nearest match, checking for `.anteroom.md`, `ANTEROOM.md`, `.claude.md`, and `CLAUDE.md` (in that order). A global `~/.anteroom/ANTEROOM.md` applies to all projects.

Instruction files go through **trust verification** before loading — see [Project Instructions](../cli/project-instructions.md).

### Skills

Skills are reusable prompt templates invoked with `/name` in the REPL. They load from three layers:

1. **Built-in** — bundled with Anteroom (`/commit`, `/review`, `/explain`, `/a-help`)
2. **Global** — `~/.anteroom/skills/*.yaml` (available everywhere)
3. **Project** — `.anteroom/skills/*.yaml` or `.claude/skills/*.yaml` (project-specific, walk-up discovery)

Higher layers override lower layers when skill names collide. See [Skills](../cli/skills.md).

### Rules

Rules are auto-loaded instruction files that apply every session without explicit invocation. They live in `.anteroom/rules/` or `.claude/rules/` at the project level. Unlike skills (which you invoke with `/name`), rules are injected into the system context automatically.

Rules are useful for enforcing conventions that should always be active:
- Commit message formats
- Code style requirements
- Security patterns
- Test requirements

### Artifact Registry

Skills, rules, instructions, and other content types are all **artifacts** in Anteroom's unified model. The artifact registry is the in-memory index that resolves what the agent sees at runtime. It loads artifacts from the database on startup and resolves precedence conflicts using a 6-layer stack: built_in < global < team < project < local < inline.

Artifacts can be installed individually or bundled into **packs** — versioned directories with a `pack.yaml` manifest. Packs can be distributed via git repositories (pack sources) that Anteroom clones and auto-refreshes in the background. This is how teams standardize development conventions: publish a pack with skills, rules, and config overlays, and every team member gets the same artifacts automatically.

See [Packs & Artifacts](../packs/index.md) for the full guide, including the 7 artifact types, manifest format, and tutorials.

### Directory Equivalence

Anteroom treats `.anteroom` and `.claude` directories as interchangeable throughout the system:

| Feature | `.anteroom` | `.claude` |
|---|---|---|
| Instructions | `.anteroom.md`, `ANTEROOM.md` | `.claude.md`, `CLAUDE.md` |
| Skills | `.anteroom/skills/` | `.claude/skills/` |
| Rules | `.anteroom/rules/` | `.claude/rules/` |
| Team config | `.anteroom/team.yaml` | `.claude/team.yaml` |

If both directories exist, `.anteroom` takes precedence. The legacy `.parlor` directory is also supported for backward compatibility.

This means if you have an existing Claude Code project structure (`.claude/` directory with skills, rules, and a `CLAUDE.md`), Anteroom picks it up automatically with no configuration changes.
