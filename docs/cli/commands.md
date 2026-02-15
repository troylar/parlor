# Commands

All slash commands available in the CLI REPL.

## Command Reference

| Command | Action |
|---|---|
| `/new` | Start a new conversation |
| `/last` | Resume the most recent conversation |
| `/list` | Show 20 most recent conversations with message counts |
| `/resume N` | Resume by list number or full conversation ID |
| `/rewind` | Rewind to a previous message, optionally undoing file changes via git |
| `/compact` | Summarize and compact message history to free context |
| `/model NAME` | Switch to a different model mid-session (omit NAME to see current) |
| `/tools` | List all available tools (built-in + MCP), sorted alphabetically |
| `/skills` | List available skills with descriptions and source |
| `/help` | Show all commands, input syntax, and keyboard shortcuts |
| `/quit`, `/exit` | Exit the REPL |

## Conversation Management

### /new

Creates a new conversation and clears message history.

### /last

Loads the most recent conversation (by creation time) with all its messages.

### /list

Shows the 20 most recent conversations:

```
Recent conversations:
  1. Fix auth middleware bug (12 msgs) a1b2c3d4...
  2. Add user settings page (8 msgs) e5f6a7b8...
  3. Refactor database layer (23 msgs) c9d0e1f2...
  Use /resume <number> or /resume <id>
```

### /resume

Resume by list number (from `/list` output) or by full conversation ID:

```
you> /resume 3
you> /resume a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

### /rewind

Rewind to a previous message. Optionally revert file changes made by AI tools via `git checkout`.

## Model Switching

```
you> /model gpt-4-turbo
  Switched to model: gpt-4-turbo

you> /model
  Current model: gpt-4-turbo
  Usage: /model <model_name>
```

The new model applies to all subsequent turns. Conversation history carries over.

## Input

| Action | Key |
|---|---|
| Submit message | `Enter` |
| Insert newline | `Alt+Enter` |
| Cancel AI response | `Escape` |
| Clear input / exit | `Ctrl+C` |
| Exit (EOF) | `Ctrl+D` |
| Autocomplete | `Tab` |

## Tab Completion

Tab completion works for three categories:

- **Commands**: Type `/` then `Tab` to see all slash commands
- **Skills**: Type `/` then `Tab` to see skill names
- **File paths**: Type `@` then `Tab` to browse files and directories

## Paste Collapsing

When you paste more than 6 lines, the terminal display collapses to show the first 3 lines plus `... (N more lines)`. The full content is still sent to the AI.

## Command History

Input history is persisted to `~/.parlor/cli_history`. Previous commands are available with up/down arrow keys across sessions.
