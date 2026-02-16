# Quickstart

A 5-minute walkthrough of Anteroom's core features across both interfaces.

## Web UI

**1. Start the server:**

```bash
$ aroom
```

Your browser opens to `http://127.0.0.1:8080`.

**2. Start chatting.** Type a message and press Enter. The AI responds with token-by-token streaming.

**3. Organize.** Create a project from the sidebar to group related conversations with a custom system prompt. Add folders and color-coded tags to keep things tidy.

**4. Use the command palette.** Press `Cmd+K` (or `Ctrl+K`) to open the Raycast-style command palette. Switch themes, models, or jump to recent conversations instantly.

**5. Attach files.** Drag and drop files into the message input. 35+ file types are supported with magic-byte verification.

## CLI

**1. Start the REPL:**

```bash
$ aroom chat
```

```
Anteroom CLI - /path/to/your/project
  Model: gpt-4 | Tools: 6 | Instructions: loaded | Branch: main
  Type /help for commands, Ctrl+D to exit
```

**2. Ask a question:**

```
you> explain the auth middleware in @src/auth.py
```

The `@` prefix inlines the file contents into your prompt automatically.

**3. Let the agent work.** The AI can read files, write code, run commands, and search your codebase --- up to 50 tool iterations per turn. A thinking spinner shows elapsed time while it works.

**4. Use skills.** Type `/commit` to auto-generate a conventional commit, `/review` for a code review, or `/explain` for architecture analysis.

**5. Queue prompts.** Type your next message while the AI is still responding. It queues and processes when the current response finishes.

**6. Manage context.** Watch the token counter in the context footer. Use `/compact` to summarize and free space when it gets high.

## One-Shot Mode

Run a single prompt and exit:

```bash
$ aroom chat "list all Python files in src/"
$ aroom chat -c "now explain the main module"    # Continue last conversation
```

## Shared Database

Both interfaces share the same SQLite database. Conversations created in the CLI appear in the web UI sidebar, and vice versa. Tool calls, attachments, and message history are fully portable between interfaces.

## Next Steps

- [Web UI guide](../web-ui/index.md) --- full web interface documentation
- [CLI guide](../cli/index.md) --- full CLI documentation
- [Configuration](../configuration/index.md) --- customize your setup
- [Tutorials](../tutorials/connect-to-ollama.md) --- connect to specific providers
