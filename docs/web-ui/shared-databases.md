# Shared Databases

Connect multiple SQLite databases for team or topic-based separation. Each database is fully independent --- its own conversations, attachments, and history.

## Setup

Add databases to your `config.yaml`:

```yaml
shared_databases:
  - name: "team-shared"
    path: "~/shared/team.db"
  - name: "research"
    path: "~/projects/research/chat.db"
```

Or add them at runtime through the web UI's visual file browser.

## Features

- **Visual file browser** with directory navigation for selecting `.db`/`.sqlite`/`.sqlite3` files
- **Copy conversations** between databases (full message + tool call history)
- **Switch databases** from the sidebar --- active database is visually indicated
- Database names: letters, numbers, hyphens, underscores only
- "personal" database always exists and can't be removed

!!! warning "Security"
    Paths are restricted to your home directory. This prevents accessing databases outside your user directory.

## Copying Conversations

Use the copy action on any conversation to duplicate it (with all messages, tool calls, and metadata) to another connected database. This is useful for:

- Archiving completed work
- Sharing conversations between database contexts
- Backing up important conversations
