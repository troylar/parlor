# Conversations API

## Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/conversations` | List conversations |
| `POST` | `/api/conversations` | Create a conversation |
| `GET` | `/api/conversations/:id` | Get conversation with messages, attachments, and tool calls |
| `PATCH` | `/api/conversations/:id` | Update title, folder, or model |
| `DELETE` | `/api/conversations/:id` | Delete conversation and all attachments |
| `GET` | `/api/conversations/:id/export` | Export as Markdown |
| `POST` | `/api/conversations/:id/chat` | Stream chat response (SSE) |
| `POST` | `/api/conversations/:id/stop` | Cancel active generation |
| `POST` | `/api/conversations/:id/fork` | Fork at a message position |
| `POST` | `/api/conversations/:id/rewind` | Rewind to a position, optionally revert file changes |
| `POST` | `/api/conversations/:id/copy` | Copy to another database |

## List Conversations

```
GET /api/conversations
```

Query parameters:

| Parameter | Type | Description |
|---|---|---|
| `search` | string | Full-text search across messages and titles |
| `project_id` | UUID | Filter by project |
| `db` | string | Database name (default: `personal`) |

## Stream Chat

```
POST /api/conversations/:id/chat
```

Sends a message and streams the AI response via Server-Sent Events (SSE).

If a stream is already active for the conversation, the message is queued (up to 10) and the endpoint returns:

```json
{"status": "queued"}
```

## Fork

```
POST /api/conversations/:id/fork
```

Creates a new conversation branching from the specified message position.

## Rewind

```
POST /api/conversations/:id/rewind
```

Rolls back to the specified message position. Optionally reverts file changes via `git checkout`.

## Copy

```
POST /api/conversations/:id/copy?target_db=team-shared
```

Duplicates the conversation (with all messages and tool calls) to another database.
