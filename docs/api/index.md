# API Reference

Anteroom exposes a REST API for all operations. All endpoints require authentication via session cookie + CSRF token.

## Authentication

Every request must include:

1. The session cookie (set automatically by the browser)
2. The CSRF token header on state-changing requests (`POST`, `PATCH`, `PUT`, `DELETE`)

## Base URL

```
http://127.0.0.1:8080/api
```

## Endpoints

| Section | Description |
|---|---|
| [Conversations](conversations.md) | CRUD, streaming chat, fork, rewind, export, copy |
| [Messages](messages.md) | Edit, delete, attachments |
| [Projects](projects.md) | Projects, folders, tags |
| [Config](config.md) | Configuration, models, databases, MCP tools |

## Common Patterns

- All IDs are UUIDs
- List endpoints support query parameters for filtering
- Responses are JSON unless otherwise noted (SSE for streaming)
- Errors return `{"detail": "error message"}` with appropriate HTTP status codes

## Rate Limiting

All endpoints are rate-limited to 120 requests per minute per IP address.
