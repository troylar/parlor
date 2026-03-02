# Built-in Tools

Twelve tools ship out of the box with no MCP server required. Three additional office tools (`docx`, `xlsx`, `pptx`) are available with the optional `anteroom[office]` install. A COM backend with extended actions for each office tool is available with `anteroom[office-com]` on Windows with Microsoft Office installed.

## Tool Reference

### read_file

Read file contents with line numbers.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | File path (relative to working directory or absolute) |
| `offset` | integer | no | Line number to start reading from (1-based, default: `1`) |
| `limit` | integer | no | Maximum number of lines to read (default: all lines) |

Output is numbered (`     1\tline content`) and truncated at 100,000 characters. Returns `total_lines` and `lines_shown` counts alongside the content.

!!! note "Security"
    Paths are validated against blocked system paths (`/etc/shadow`, `/etc/passwd`, `/proc/`, `/sys/`, `/dev/`). Symlinks are resolved and checked to prevent traversal escapes.

### write_file

Create or overwrite files. Parent directories are created automatically.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | File path (relative to working directory or absolute) |
| `content` | string | yes | Content to write to the file |

Returns bytes written and whether the file was `created` or `updated`.

!!! note "Security"
    Paths are validated against blocked system paths and the configurable `sensitive_paths` list (see [Tool Safety](../security/tool-safety.md)). Symlinks are resolved to prevent traversal.

### edit_file

Exact string replacement in files.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | File path (relative to working directory or absolute) |
| `old_text` | string | yes | Text to find (must match exactly once, or use `replace_all`) |
| `new_text` | string | yes | Replacement text |
| `replace_all` | boolean | no | Replace all occurrences (default: `false`) |

!!! warning
    `old_text` must be unique in the file unless `replace_all=true`. The tool returns an error if the text appears zero times or more than once.

!!! note "Security"
    Same path validation as `write_file` — blocked system paths, sensitive path checks, and symlink resolution.

### bash

Run shell commands.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `command` | string | yes | Shell command to execute |
| `timeout` | integer | no | Timeout in seconds (default: `120`, max: `600`) |

Returns stdout, stderr, and exit code. Stdout and stderr are each independently truncated at 100,000 characters (or `max_output_chars` if sandbox config is set).

The timeout maximum can be further restricted by `BashSandboxConfig.timeout` if a sandbox config is active.

!!! info "Safety"
    Destructive commands (16 hard-block patterns including `rm -rf`, `mkfs`, fork bombs, pipe-to-shell) are blocked by default. Additional restrictions are available via sandbox configuration: network access, package installation, blocked paths, and blocked command patterns. See [Bash Sandboxing](../security/bash-sandboxing.md) and [Tool Safety](../security/tool-safety.md) for details.

### glob_files

Find files matching a glob pattern.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `pattern` | string | yes | Glob pattern (e.g., `**/*.py`, `src/**/*.ts`) |
| `path` | string | no | Search root directory (default: working directory) |

Results are sorted by modification time (newest first). Maximum 500 results — the response includes a `truncated` flag when more matches exist. Only files are returned (directories are excluded). Symlinks that escape the search root are silently skipped.

### grep

Regex search across files with context lines and file-type filtering.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `pattern` | string | yes | Regex pattern |
| `path` | string | no | File or directory to search in (default: working directory) |
| `glob` | string | no | File filter glob (e.g., `*.py`, `**/*.ts`; default: all files) |
| `context` | integer | no | Lines of context before and after each match (default: `0`) |
| `case_insensitive` | boolean | no | Case-insensitive search (default: `false`) |

Maximum 200 matches. Files over 5MB are skipped. Total output truncated at 100,000 characters. Matching lines are prefixed with `>` and context lines with a space, both with 1-based line numbers.

When `path` points to a single file, the response contains a structured `matches` array. When `path` is a directory, the response is a formatted text block with `file:lineno` headers.

### create_canvas

Create a canvas panel alongside the chat to display rich content (code, markdown, reports). One canvas per conversation.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `title` | string | yes | Canvas title (e.g., `fibonacci.py`, `Project README`) |
| `content` | string | yes | Full content to display |
| `language` | string | no | Programming language for syntax highlighting (e.g., `python`, `javascript`) |

Content is limited to 100,000 characters. Only one canvas is allowed per conversation — use `update_canvas` or `patch_canvas` to modify an existing canvas. The canvas is scoped to the conversation (no ID needed for subsequent operations).

### update_canvas

Replace the full content of the current conversation's canvas.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `content` | string | yes | Complete new content (replaces existing entirely) |
| `title` | string | no | New title (omit to keep existing) |

The canvas is looked up automatically by conversation — no ID parameter is needed. Content is limited to 100,000 characters. A canvas must already exist (use `create_canvas` first).

### patch_canvas

Apply incremental search-and-replace edits to the current conversation's canvas. More token-efficient than `update_canvas` for small changes.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `edits` | array | yes | List of `{"search": "...", "replace": "..."}` objects |

Each edit's `search` string must match exactly once in the current canvas content. Edits are applied sequentially — each edit operates on the result of the previous one. Maximum 50 edits per call. Content must stay under 100,000 characters after all edits are applied.

If an edit fails (zero matches or ambiguous multiple matches), the error includes the `edit_index` and `failed_edit` for debugging.

### run_agent

Launch an autonomous sub-agent to handle a complex or independent task in parallel. The sub-agent runs its own AI session with access to all built-in and MCP tools, then returns a summary of its work.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | yes | Self-contained instruction for the sub-agent (max 32,000 characters) |
| `model` | string | no | Model override (e.g., `gpt-4o-mini` for fast tasks; inherits parent model if omitted) |

Sub-agents run in isolated conversation contexts — they cannot see the parent's history. Multiple `run_agent` calls execute concurrently.

**Limits** (all configurable via `safety.subagent` in `config.yaml`):

| Limit | Default |
|---|---|
| Max concurrent sub-agents | 5 |
| Max total per request | 10 |
| Max nesting depth | 3 |
| Max iterations per sub-agent | 15 |
| Wall-clock timeout | 120s |
| Output truncation | 4,000 characters |

At maximum depth, `run_agent` is removed from the child's tool list to prevent deeper recursion. In read-only mode, sub-agents are restricted to READ-tier tools only.

The response includes `elapsed_seconds`, `tool_calls_made` (list of tool names used), `model_used`, and a `truncated` flag when output exceeds the limit.

### ask_user

Ask the user a question and pause execution to wait for their response. Use this when you need information to proceed, rather than asking in text output.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `question` | string | yes | The question to ask (be specific, ask one thing at a time) |
| `options` | array of strings | no | Fixed choices for the user to pick from (omit for freeform text input) |

When `options` is provided, the user selects from a fixed list instead of typing freeform text. Options are capped at 20 entries, each truncated to 256 characters.

In the CLI REPL, users type their answer at the prompt. In the web UI, an inline input field appears in the chat. In non-interactive mode (`aroom exec`), the tool returns an error instructing the AI to proceed with its best judgment.

The response includes `answer` (the user's text) and optionally `cancelled: true` if the user dismissed the prompt.

### introspect

Examine the AI's own runtime context — config, tools, safety settings, instructions, skills, and token budget. The AI calls this automatically when you ask about your setup. READ tier — auto-allowed in all approval modes.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `section` | string | no | Which section to inspect (omit for a summary of all sections) |

Valid `section` values:

| Section | What it returns |
|---|---|
| `config` | AI model, base URL, system prompt (truncated), app host/port/TLS, config file paths |
| `instructions` | Whether ANTEROOM.md instructions are loaded, their sources, and total token count |
| `tools` | Built-in tool names and count, MCP server status and tool lists, denied/allowed tools |
| `safety` | Approval mode, allowed/denied tools, custom bash pattern count, sub-agent limits |
| `skills` | Total loaded skills, grouped by source (default vs. project) |
| `budget` | Estimated token cost of tool definitions, instructions, and system prompt; context window usage percentage |

Example prompts that trigger this tool:

- "What model am I using?"
- "What tools are available?"
- "What's my approval mode?"
- "How much context am I using?"
- "What instructions are loaded?"
- "What skills do I have?"

!!! note "Security"
    Secrets are automatically redacted: any field containing `key`, `secret`, `password`, `token`, or `passphrase` in its name is replaced with `****`. The API key is always redacted unconditionally.

## How Tools Work

All file tools resolve paths relative to the working directory (or accept absolute paths). Every tool returns structured JSON that the AI uses to inform its next action.

Built-in tools are checked before MCP tools. If a tool name matches a built-in, it executes locally with no network overhead. Use `/tools` in the REPL to see all available tools from both sources.

## Risk Tiers

Each tool is assigned a risk tier that determines when approval is required:

| Tier | Tools | Behavior |
|---|---|---|
| **READ** | `read_file`, `glob_files`, `grep`, `create_canvas`, `update_canvas`, `patch_canvas`, `ask_user`, `introspect` | Auto-allowed in all approval modes |
| **WRITE** | `write_file`, `edit_file`, `docx`*, `xlsx`*, `pptx`* | Requires approval in `ask_for_writes` and `ask` modes |
| **EXECUTE** | `bash`, `run_agent` | Requires approval in `ask_for_dangerous`, `ask_for_writes`, and `ask` modes |

\* Optional — requires `pip install anteroom[office]`

See [Tool Safety](../security/tool-safety.md) for full details on approval modes and tier overrides.

## Disabling Tools

```bash
$ aroom chat --no-tools
```

Or in `config.yaml`:

```yaml
cli:
  builtin_tools: false
```

This disables all built-in tools (including optional office tools). MCP tools (if configured) still work.

## Optional Office Tools

Install `anteroom[office]` to enable three additional tools for creating, reading, and editing MS Office files:

```bash
$ pip install anteroom[office]
```

### COM Backend

Some actions are marked **(COM only)**. These require Windows with Microsoft Office installed and the `pywin32` library. Install with:

```bash
$ pip install anteroom[office-com]
```

All other actions work cross-platform via the library backend (`python-docx`, `openpyxl`, `python-pptx`).

### docx

Create, read, and edit Word documents (.docx). Supports 15 actions.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `action` | string | yes | Action to perform (see actions below) |
| `path` | string | yes | File path (relative to working directory or absolute) |
| `content_blocks` | array | no | Content blocks for create/edit. Each: `{type: "heading"\|"paragraph"\|"table", text?, level?, rows?}` |
| `replacements` | array | no | Find/replace pairs for edit: `[{old: str, new: str}]` |
| `template_data` | object | no | Key-value pairs for template_fill action |

**Available actions:**

| Action | Description |
|---|---|
| `create` | Create a document from heading, paragraph, and table blocks |
| `read` | Extract text with per-paragraph formatting annotations (bold, italic, font size, style), table cell multi-paragraph structure, headers/footers, section layout, and document properties |
| `edit` | Find-replace across paragraphs and optionally append new blocks |
| `track_changes` | Accept, reject, or list tracked changes (COM only) |
| `comments` | Add, read, resolve, or delete comments (COM only) |
| `headers_footers` | Set or read document headers and footers |
| `insert_image` | Insert an image into the document |
| `styles` | List, set, or apply paragraph and character styles |
| `export_pdf` | Export the document to PDF (COM only) |
| `page_setup` | Set margins, page orientation, and paper size |
| `sections` | List, add, or delete document sections |
| `bookmarks` | Add, read, or delete bookmarks (COM only) |
| `toc` | Insert or update a table of contents (COM only) |
| `find_regex` | Find text using regex patterns |
| `template_fill` | Replace {{key}} placeholders with values from template_data across all content, headers, and footers |

Max 200 content blocks per call. Output truncated at 100,000 characters.

### xlsx

Create, read, and edit Excel spreadsheets (.xlsx). Supports 28 actions.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `action` | string | yes | Action to perform (see actions below) |
| `path` | string | yes | File path (relative to working directory or absolute) |
| `sheets` | array | no | Sheets for create: `[{name, headers?, rows}]` |
| `sheet_name` | string | no | Sheet to target (default: active sheet) |
| `cell_range` | string | no | Cell range to operate on, e.g. `A1:C10` |
| `updates` | array | no | Cell updates for edit: `[{cell: "A1", value: 42}]` |
| `append_rows` | array | no | Rows to append: `[[value, ...]]` |
| `add_sheets` | array | no | New sheets to add: `[{name, rows?}]` |
| `template_data` | object | no | Key-value pairs for template_fill action |

**Available actions:**

| Action | Description |
|---|---|
| `create` | Create a workbook with named sheets, headers, and row data |
| `read` | Read cell data as JSON with formulas, merged ranges, cell formatting (bold/fill/number format), column widths, row heights, data validations, conditional formatting rules, named ranges, freeze pane state, auto-filter references, and hidden sheets |
| `edit` | Update cells, append rows, or add sheets |
| `format_cells` | Apply fonts, borders, fills, alignment, and number formats |
| `merge_cells` | Merge or unmerge cell ranges |
| `freeze_panes` | Freeze rows or columns for scrolling |
| `auto_filter` | Apply or remove auto-filter on columns |
| `print_area` | Set the print area range |
| `named_ranges` | Create, list, or delete named ranges |
| `data_validation` | Add dropdown or range validation to cells |
| `conditional_format` | Apply conditional formatting rules |
| `comments` | Add, read, or delete cell comments |
| `hyperlinks` | Add or read hyperlinks in cells |
| `images` | Insert images into worksheets |
| `protect` | Protect or unprotect sheets and workbooks |
| `group_rows_cols` | Group or ungroup rows and columns |
| `print_settings` | Configure print layout, margins, and headers |
| `charts` | Create column, bar, line, pie, scatter, or area charts |
| `export_pdf` | Export the workbook to PDF (COM only) |
| `sort` | Sort data ranges (COM only) |
| `pivot_tables` | Create pivot tables (COM only) |
| `sparklines` | Add sparkline charts (COM only) |
| `slicers` | Add data slicers (COM only) |
| `template_fill` | Replace {{key}} placeholders with values from template_data across all sheets |
| `manage_sheets` | List, rename, hide, show, delete, or reorder sheets |
| `resize` | Set column widths and row heights |
| `insert_delete` | Insert or delete rows and columns |
| `copy_range` | Copy cell ranges between sheets |

Max 10,000 rows. Output truncated at 100,000 characters.

### pptx

Create, read, and edit PowerPoint presentations (.pptx). Supports 19 actions.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `action` | string | yes | Action to perform (see actions below) |
| `path` | string | yes | File path (relative to working directory or absolute) |
| `slides` | array | no | Slides for create/edit: `[{title?, content?, bullets?, notes?, layout?}]` |
| `replacements` | array | no | Find/replace pairs for edit: `[{old: str, new: str}]` |

**Available actions:**

| Action | Description |
|---|---|
| `create` | Create a presentation with slides (title, content, bullets, notes) |
| `read` | Extract slide text and speaker notes |
| `edit` | Find-replace text across slides and optionally append new slides |
| `transitions` | Apply slide transitions (COM only) |
| `animations` | Add shape animations (COM only) |
| `insert_image` | Insert an image onto a slide |
| `insert_shape` | Add shapes (rectangle, oval, triangle, etc.) |
| `format_shape` | Style shapes with fill, line, shadow, and text formatting |
| `master_layout` | List or apply slide master layouts |
| `reorder_slides` | Move or delete slides |
| `embed_chart` | Embed charts on slides (COM only) |
| `embed_table` | Insert tables onto slides |
| `export_pdf` | Export the presentation to PDF (COM only) |
| `hyperlinks` | Add hyperlinks to shapes |
| `headers_footers` | Set slide footers, date/time, and slide numbers |
| `sections` | Manage presentation sections (COM only) |
| `group_shapes` | Group or ungroup shapes (COM only) |
| `audio_video` | Insert audio or video media (COM only) |
| `smartart` | Insert SmartArt graphics (COM only) |

Max 100 slides. Output truncated at 100,000 characters.

!!! note "Graceful Degradation"
    If the office libraries are not installed, these tools are not registered — they won't appear in the tool list. If you attempt to call them directly, the tool returns an install instruction.
