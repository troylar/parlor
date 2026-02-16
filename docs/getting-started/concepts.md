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

Configuration follows a layered approach:

1. **Defaults** --- sensible defaults for all settings
2. **Config file** --- `~/.anteroom/config.yaml` overrides defaults
3. **Environment variables** --- `AI_CHAT_*` prefix overrides config file values

See [Configuration](../configuration/index.md) for the full reference.
