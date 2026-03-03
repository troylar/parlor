# Commands

All slash commands available in the CLI REPL.

## Command Reference

| Command | Action |
|---|---|
| `/new` | Start a new conversation |
| `/last` | Resume the most recent conversation |
| `/list [N]` | Show recent conversations (default 20) |
| `/search <query>` | Search conversations by content |
| `/resume <N\|id\|slug>` | Resume by list number, conversation ID, or slug |
| `/slug [name]` | View or set the conversation slug |
| `/delete <N\|id\|slug>` | Delete a conversation (with confirmation) |
| `/rewind` | Rewind to a previous message, optionally undoing file changes via git |
| `/compact` | Summarize and compact message history to free context |
| `/model NAME` | Switch to a different model mid-session (omit NAME to see current) |
| `/upload <path>` | Upload a file to the knowledge base, auto-extracts text from PDFs/DOCX |
| `/tools` | List all available tools (built-in + MCP), sorted alphabetically |
| `/skills` | List available skills with descriptions and source (auto-reloads from disk) |
| `/reload-skills` | Reload skill files from disk |
| `/project list` | List all projects |
| `/project create <name>` | Create a new project |
| `/project select <name\|id>` | Set active project for new conversations |
| `/project edit <name\|id>` | Edit project instructions or model |
| `/project delete <name\|id>` | Delete a project (with confirmation) |
| `/project clear` | Deactivate the current project |
| `/project sources` | List sources linked to the active project |
| `/projects` | Shortcut for `/project list` |
| `/mcp` | Show MCP server status |
| `/mcp status <name>` | Detailed diagnostics for one server |
| `/usage` | Show token usage statistics (today, this week, this month, all time) |
| `/verbose` | Cycle verbosity: compact > detailed > verbose |
| `/detail` | Replay last turn's tool calls with full output |
| `/help` | Show all commands, input syntax, and keyboard shortcuts |
| `/quit`, `/exit` | Exit the REPL |

## Conversation Management

### /new

Creates a new conversation and clears message history.

### /last

Loads the most recent conversation (by creation time) with all its messages. Shows the last user/assistant exchange for context.

### /list

Shows recent conversations. Pass a number to control how many:

```
you> /list
you> /list 50
```

```
Recent conversations:
  1. Fix auth middleware bug (12 msgs) a1b2c3d4...
  2. Add user settings page (8 msgs) e5f6a7b8...
  3. Refactor database layer (23 msgs) c9d0e1f2...
  ... more available. Use /list 40 to show more.
  Use /resume <number> or /resume <id>
```

### /search

Search conversation titles and content using full-text search:

```
you> /search database migration
```

### /resume

Resume by list number (from `/list` output), full conversation ID, or slug:

```
you> /resume 3
you> /resume a1b2c3d4-e5f6-7890-abcd-ef1234567890
you> /resume auth-refactor
```

On resume, the last user/assistant exchange is shown for context.

### /slug

View or set the slug (human-readable alias) for the current conversation:

```
you> /slug
  Slug: auth-refactor

you> /slug jwt-implementation
  Slug set to: jwt-implementation
```

Slugs are auto-generated on creation with format `{word}-{word}`, but you can set a custom slug. Slugs must be unique within your database. After setting a slug, you can use `/resume <slug>` instead of `/resume <id>`.

### /delete

Delete a conversation by list number, ID, or slug. Prompts for confirmation:

```
you> /delete 3
  Delete "Fix auth middleware bug"? [y/N]

you> /delete auth-refactor
  Delete "Fix auth middleware bug"? [y/N]
```

If you delete the current conversation, a new one is started automatically.

### /rewind

Rewind to a previous message. Optionally revert file changes made by AI tools via `git checkout`.

### /upload

Upload a file to the knowledge base. Text is automatically extracted from PDFs and DOCX files, chunked, and indexed for semantic search. Supports 35+ file types including code, documents, images, and data files.

```
you> /upload ~/myfile.pdf
Uploaded myfile.pdf → source a1b2c3d4…
  application/pdf, 12,340 chars extracted
```

Files are searchable via semantic search (web UI) and can be injected into chat context using source_ids. Text extraction requires optional dependencies:

```bash
pip install anteroom[docs]  # adds PDF and DOCX support
```

Without these optional dependencies, PDFs and DOCX files upload as binary documents (web UI can still display them as attachments, but text extraction is skipped).

## Model Switching

```
you> /model gpt-4-turbo
  Switched to model: gpt-4-turbo

you> /model
  Current model: gpt-4-turbo
  Usage: /model <model_name>
```

The new model applies to all subsequent turns. Conversation history carries over.

You can also set the model from the command line:

```bash
aroom chat --model gpt-4-turbo
aroom chat --model gpt-4o "explain this code"
```

## Usage Tracking

### /usage

Show token usage and estimated costs across multiple time periods:

```
you> /usage
  Today:
    Prompt tokens: 5,420 | Completion tokens: 2,150 | Total: 7,570
    Messages: 12 | Estimated cost: $0.0234
    By model:
      gpt-4o: 7,570 tokens ($0.0234)

  This week:
    Prompt tokens: 42,100 | Completion tokens: 18,900 | Total: 61,000
    Messages: 89 | Estimated cost: $0.1847

  This month:
    Prompt tokens: 185,400 | Completion tokens: 92,300 | Total: 277,700
    Messages: 342 | Estimated cost: $0.8421

  All time:
    Prompt tokens: 890,250 | Completion tokens: 456,800 | Total: 1,347,050
    Messages: 1,523 | Estimated cost: $4.0782
```

Token counts are automatically tracked for each message. Cost estimation requires configuring model costs in `~/.anteroom/config.yaml`:

```yaml
cli:
  usage:
    model_costs:
      gpt-4o: { input: 0.003, output: 0.006 }       # per-million-token rates
      gpt-4-turbo: { input: 0.01, output: 0.03 }
    week_days: 7      # Days for "this week" rolling window (default: 7)
    month_days: 30    # Days for "this month" rolling window (default: 30)
```

## Display Modes

### /verbose

Cycles through three verbosity levels for tool call output:

- **compact** (default): One-line result per tool call
- **detailed**: Result line plus brief output summary
- **verbose**: Full tool name, arguments, and output

### /detail

Replays the last turn's tool calls with full input arguments and output. Useful for debugging when compact mode hides too much.

## CLI Flags

### Global Flags (work in chat and exec modes)

#### --approval-mode

Override the safety approval mode for this session:

```bash
aroom --approval-mode auto                 # Skip all approvals
aroom --approval-mode ask_for_dangerous    # Only prompt for destructive commands
aroom --approval-mode ask_for_writes       # Prompt for write+execute+destructive (default)
aroom --approval-mode auto chat            # Works with subcommands
aroom --approval-mode auto exec "task"     # Works with exec
```

#### --debug

Enable debug logging to stderr. Useful for troubleshooting MCP server connections, tool routing, and session lifecycle:

```bash
aroom --debug chat                        # Debug logs while chatting
aroom --debug                             # Debug logs for web UI + uvicorn
AI_CHAT_LOG_LEVEL=DEBUG aroom chat        # Same via env var
AI_CHAT_LOG_LEVEL=INFO aroom              # Less verbose
aroom --debug chat 2>debug.log            # Redirect logs to file
```

Priority: `--debug` flag > `AI_CHAT_LOG_LEVEL` env var > default (WARNING).

Valid `AI_CHAT_LOG_LEVEL` values: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.

#### --allowed-tools

Pre-allow specific tools to skip approval prompts for this session:

```bash
aroom --allowed-tools bash,write_file      # Auto-approve bash and write_file
aroom --allowed-tools bash chat            # Works with subcommands
aroom --allowed-tools bash exec "task"     # Works with exec
```

### Exec Mode Flags

#### --json

Output structured JSON instead of plain text to stdout. Includes response text, tool calls, and exit code:

```bash
aroom exec "summarize results" --json
```

#### --no-conversation

Skip saving the conversation to the database. Tool audit logs are still retained for compliance:

```bash
aroom exec "run analysis" --no-conversation
```

#### --no-tools

Disable all built-in and MCP tools (direct AI reasoning only):

```bash
aroom exec "explain concepts" --no-tools
```

#### --timeout

Set wall-clock timeout in seconds (default 120, clamped 10-600). Returns exit code 124 on timeout:

```bash
aroom exec "train model" --timeout 300     # 5 minutes
```

#### --quiet / --verbose

Control feedback on stderr:

```bash
aroom exec "task" --quiet                  # Suppress all stderr progress
aroom exec "task" --verbose                # Show full tool call details
```

#### --no-project-context

Skip loading project-level ANTEROOM.md instructions:

```bash
aroom exec "task" --no-project-context
```

## Input

| Action | Key |
|---|---|
| Submit message | `Enter` |
| Insert newline | `Alt+Enter` or `Shift+Enter` |
| Cancel AI response | `Escape` |
| Clear input / exit | `Ctrl+C` |
| Exit (EOF) | `Ctrl+D` |
| Autocomplete | `Tab` |

## Pack Management

### aroom pack

Manage installed packs.

| Subcommand | Action |
|---|---|
| `aroom pack list` | List all installed packs |
| `aroom pack install PATH [--project]` | Install a pack from a local directory |
| `aroom pack show NAMESPACE/NAME` | Show pack details and artifacts |
| `aroom pack remove NAMESPACE/NAME` | Remove a pack and its orphaned artifacts |
| `aroom pack update PATH [--project]` | Update an existing pack |
| `aroom pack sources` | List configured pack sources with cache status |
| `aroom pack refresh` | Manually refresh all pack sources |

The `--project` flag copies the pack into `.anteroom/packs/` for version control.

### aroom artifact

Manage artifacts in the registry.

| Subcommand | Action |
|---|---|
| `aroom artifact list [--type TYPE] [--namespace NS] [--source SOURCE]` | List artifacts with optional filters |
| `aroom artifact show FQN` | Show artifact details and version history |
| `aroom artifact check [--json] [--fix] [--project]` | Run health checks on all artifacts |

Filter values for `--type`: `skill`, `rule`, `instruction`, `context`, `memory`, `mcp_server`, `config_overlay`

Filter values for `--source`: `built_in`, `global`, `team`, `project`, `local`, `inline`

See [Pack Commands](../packs/pack-commands.md) for detailed examples and output.

## Server Management

### aroom start

Start the web UI server in the background. The server detaches from the terminal so you can close the console without stopping it. A PID file is written to `~/.anteroom/anteroom-{port}.pid` and server output is logged to `~/.anteroom/aroom.log`.

```bash
aroom start                 # start on default port (8080), opens browser
aroom start --no-browser    # start without opening browser
aroom start --port 9090     # start on a custom port
```

If a server is already running on the same port, the command exits with an error.

### aroom stop

Stop a background web UI server. Reads the PID file, sends a graceful shutdown signal, and cleans up.

```bash
aroom stop                  # stop server on default port
aroom stop --port 9090      # stop server on a custom port
```

Handles stale PID files (process already exited) gracefully.

### aroom status

Show whether the web UI server is running, its PID, port, uptime, and log file location.

```bash
aroom status                # check default port
aroom status --port 9090    # check a custom port
```

Cross-platform: works on macOS, Linux, and Windows.

## Tab Completion

Tab completion works for four categories:

- **Commands**: Type `/` then `Tab` to see all slash commands with descriptions
- **Skills**: Type `/` then `Tab` to see skill names with one-line descriptions and source locations
- **Conversation slugs**: Type `/resume <slug>` to complete recent conversation slugs
- **File paths**: Type `@` then `Tab` to browse files and directories

## Paste Collapsing

When you paste more than 6 lines, the terminal display collapses to show the first 3 lines plus `... (N more lines)`. The full content is still sent to the AI.

## Command History

Input history is persisted to `~/.anteroom/cli_history`. Previous commands are available with up/down arrow keys across sessions.
