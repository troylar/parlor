# Built-in Tools

Ten tools ship out of the box with no MCP server required.

## Tool Reference

### read_file

Read file contents with line numbers.

| Parameter | Type | Description |
|---|---|---|
| `path` | string | File path (relative to working directory or absolute) |
| `offset` | integer | 1-based start line |
| `limit` | integer | Max lines to read |

Output is truncated at 100KB.

### write_file

Create or overwrite files. Parent directories are created automatically.

| Parameter | Type | Description |
|---|---|---|
| `path` | string | File path |
| `content` | string | File contents |

Returns bytes written.

### edit_file

Exact string replacement in files.

| Parameter | Type | Description |
|---|---|---|
| `path` | string | File path |
| `old_text` | string | Text to find (must match exactly once, or use `replace_all`) |
| `new_text` | string | Replacement text |
| `replace_all` | boolean | Replace all occurrences (default: `false`) |

!!! warning
    `old_text` must be unique in the file unless `replace_all=true`. The tool returns an error if the text appears more than once (or zero times).

### bash

Run shell commands.

| Parameter | Type | Description |
|---|---|---|
| `command` | string | Shell command to execute |
| `timeout` | integer | Timeout in seconds (default: 120, max: 600) |

Returns stdout, stderr, and exit code. Output is truncated at 100KB.

!!! info "Safety"
    Destructive commands trigger an interactive confirmation prompt. See [Tool Safety](../security/tool-safety.md) for details.

### glob_files

Find files matching a glob pattern.

| Parameter | Type | Description |
|---|---|---|
| `pattern` | string | Glob pattern (e.g., `**/*.py`) |
| `path` | string | Search root directory |

Results are sorted by modification time (newest first). Maximum 500 results.

### grep

Regex search across files with context lines and file-type filtering.

| Parameter | Type | Description |
|---|---|---|
| `pattern` | string | Regex pattern |
| `path` | string | Search root directory |
| `glob` | string | File filter (e.g., `*.py`) |
| `context` | integer | Lines of context around matches |
| `case_insensitive` | boolean | Case-insensitive search |

Maximum 200 matches. Files over 5MB are skipped.

### create_canvas

Create a named canvas panel alongside the chat to display rich content (markdown, code, reports).

| Parameter | Type | Description |
|---|---|---|
| `canvas_id` | string | Unique identifier for the canvas |
| `title` | string | Display title shown in the panel header |
| `content` | string | Initial content (markdown supported) |
| `content_type` | string | `markdown`, `code`, or `text` (default: `markdown`) |

Emits `canvas_created` and `canvas_stream_start`/`canvas_streaming` SSE events during generation.

### update_canvas

Replace the full content of an existing canvas.

| Parameter | Type | Description |
|---|---|---|
| `canvas_id` | string | Canvas to update |
| `content` | string | New content (replaces existing) |
| `title` | string | Updated title (optional) |

Emits a `canvas_updated` SSE event.

### patch_canvas

Apply incremental search-and-replace edits to an existing canvas. More token-efficient than `update_canvas` for small changes.

| Parameter | Type | Description |
|---|---|---|
| `canvas_id` | string | Canvas to patch |
| `edits` | array | List of `{"old_text": "...", "new_text": "..."}` objects |

Each edit must match exactly once. Emits a `canvas_patched` SSE event.

### run_agent

Launch an autonomous sub-agent to handle a complex or independent task in parallel. The sub-agent runs its own AI session with access to all built-in tools and returns a summary of its work.

| Parameter | Type | Description |
|---|---|---|
| `prompt` | string | Detailed, self-contained instruction for the sub-agent |
| `model` | string | Optional model override (e.g. `gpt-4o-mini` for fast tasks) |

Sub-agents run in isolated conversation contexts — they cannot see the parent's history. Multiple `run_agent` calls execute concurrently. Guarded by concurrency limits (max 5 concurrent, 10 total per request), depth limits (max 3 levels of nesting), iteration limits (15 per sub-agent vs 50 for the parent), and a wall-clock timeout (120s per sub-agent). All limits are configurable via `safety.subagent` in `config.yaml`.

## How Tools Work

All file tools resolve paths relative to the working directory (or accept absolute paths). Every tool returns structured JSON that the AI uses to inform its next action.

Built-in tools are checked before MCP tools. If a tool name matches a built-in, it executes locally with no network overhead. Use `/tools` in the REPL to see all available tools from both sources.

## Disabling Tools

```bash
$ aroom chat --no-tools
```

Or in `config.yaml`:

```yaml
cli:
  builtin_tools: false
```

This disables all ten built-in tools. MCP tools (if configured) still work.
