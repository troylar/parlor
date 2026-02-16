# CLI

An agentic terminal REPL --- like Claude Code, but connected to **your own API**. Read files, write code, run commands, search your codebase, all from the terminal.

```bash
$ aroom chat
```

```
Anteroom CLI - /path/to/your/project
  Model: gpt-4 | Tools: 6 | Instructions: loaded | Branch: main
  Type /help for commands, Ctrl+D to exit
```

## Modes

=== "Interactive REPL"

    ```bash
    $ aroom chat                       # Start fresh
    $ aroom chat -c                    # Continue last conversation
    $ aroom chat -r <id>               # Resume specific conversation
    $ aroom chat -p /path/to/project   # Set project root
    $ aroom chat --no-tools            # Disable built-in tools
    ```

=== "One-Shot"

    ```bash
    $ aroom chat "explain main.py"
    $ aroom chat -c "now add rate limiting"
    $ aroom chat -r a1b2c3d4 "fix the failing test"
    ```

    One-shot mode creates a conversation in the database, generates a title, and exits after the response. The AI still has access to all tools and can run multiple agentic iterations.

## How It Works

1. You type a prompt at the `you>` prompt
2. A thinking spinner with elapsed timer appears while the AI generates
3. When the AI calls tools, the spinner pauses and tool calls display inline
4. The full response renders as Rich Markdown --- syntax-highlighted code, tables, headers, lists
5. A context footer shows token usage, response size, elapsed time, and remaining headroom

## Agentic Loop

The AI runs in an agentic loop: it can call tools, inspect results, call more tools, and continue reasoning --- up to 50 iterations per turn. Multiple tool calls in a single response execute in parallel.

## Prompt Queuing

Type and submit messages while the AI is working. They queue (up to 10) and process in FIFO order. The prompt stays active at the bottom of the terminal while output streams above it.

## Cross-Platform

Works on macOS, Linux, and Windows:

- Signal handling uses `asyncio.add_signal_handler` on Unix and falls back gracefully on Windows
- Path resolution uses `os.path.realpath` for consistent behavior
- The prompt toolkit input handles platform-specific terminal differences

## Features

| Feature | Page |
|---|---|
| Built-in tools | [Tools](tools.md) |
| Reusable prompt templates | [Skills](skills.md) |
| Inline file contents | [File References](file-references.md) |
| Token tracking & compact | [Context Management](context-management.md) |
| All /commands | [Commands](commands.md) |
| ANTEROOM.md project context | [Project Instructions](project-instructions.md) |
