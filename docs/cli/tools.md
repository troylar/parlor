# Built-in Tools

Six tools ship out of the box with no MCP server required.

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

This disables all six built-in tools. MCP tools (if configured) still work.
