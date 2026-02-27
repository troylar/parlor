# API Reference

HTTP endpoints for managing spaces in the web UI. All endpoints require authentication (session cookie) and are prefixed with `/api`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/spaces` | List all spaces |
| `POST` | `/api/spaces` | Create a space |
| `GET` | `/api/spaces/{id}` | Get a space |
| `DELETE` | `/api/spaces/{id}` | Delete a space |
| `GET` | `/api/spaces/{id}/paths` | List mapped paths |
| `POST` | `/api/spaces/{id}/refresh` | Refresh from YAML file |
| `GET` | `/api/spaces/{id}/sources` | List linked sources |
| `POST` | `/api/spaces/{id}/sources` | Link a source |
| `DELETE` | `/api/spaces/{id}/sources/{source_id}` | Unlink a source |
| `GET` | `/api/spaces/{id}/packs` | List attached packs |

---

## `GET /api/spaces`

List all registered spaces, sorted by name.

**Response:** `200 OK`

```json
[
  {
    "id": "a1b2c3d4-5678-9abc-def0-123456789abc",
    "name": "backend-api",
    "file_path": "/home/dev/.anteroom/spaces/backend-api.yaml",
    "file_hash": "abc123def456...",
    "last_loaded_at": "2025-01-15T10:30:00+00:00",
    "created_at": "2025-01-10T08:00:00+00:00",
    "updated_at": "2025-01-15T10:30:00+00:00"
  }
]
```

Returns an empty array if no spaces exist.

---

## `POST /api/spaces`

Create a new space.

**Request body:**

```json
{
  "name": "backend-api",
  "file_path": "/home/dev/.anteroom/spaces/backend-api.yaml",
  "file_hash": "abc123def456..."
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `string` | Yes | Space name (must match `^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$`) |
| `file_path` | `string` | Yes | Absolute path to the YAML file (no `..` segments) |
| `file_hash` | `string` | No | SHA-256 hash of the file contents |

**Response:** `201 Created`

```json
{
  "id": "a1b2c3d4-5678-9abc-def0-123456789abc",
  "name": "backend-api",
  "file_path": "/home/dev/.anteroom/spaces/backend-api.yaml",
  "file_hash": "abc123def456...",
  "last_loaded_at": "2025-01-15T10:30:00+00:00",
  "created_at": "2025-01-15T10:30:00+00:00",
  "updated_at": "2025-01-15T10:30:00+00:00"
}
```

**Errors:**

| Status | Detail | Cause |
|--------|--------|-------|
| `409` | `Space 'name' already exists` | Name collision |
| `422` | Validation error | Invalid name or path traversal |

---

## `GET /api/spaces/{id}`

Get a single space by ID.

**Response:** `200 OK`

```json
{
  "id": "a1b2c3d4-5678-9abc-def0-123456789abc",
  "name": "backend-api",
  "file_path": "/home/dev/.anteroom/spaces/backend-api.yaml",
  "file_hash": "abc123def456...",
  "last_loaded_at": "2025-01-15T10:30:00+00:00",
  "created_at": "2025-01-10T08:00:00+00:00",
  "updated_at": "2025-01-15T10:30:00+00:00"
}
```

**Errors:**

| Status | Detail | Cause |
|--------|--------|-------|
| `404` | `Space not found` | Invalid ID |

---

## `DELETE /api/spaces/{id}`

Delete a space and clean up associated data.

**Response:** `204 No Content`

**Cleanup behavior:**

- Deletes the space record, mapped paths, and space-scoped pack attachments
- Conversations and folders are unlinked (`space_id` set to `NULL`), not deleted

**Errors:**

| Status | Detail | Cause |
|--------|--------|-------|
| `404` | `Space not found` | Invalid ID |

---

## `GET /api/spaces/{id}/paths`

List all directories mapped to a space.

**Response:** `200 OK`

```json
[
  {
    "id": "path-uuid-here",
    "space_id": "a1b2c3d4-5678-9abc-def0-123456789abc",
    "repo_url": "https://github.com/acme/api-server.git",
    "local_path": "/home/dev/projects/acme/api-server"
  }
]
```

**Errors:**

| Status | Detail | Cause |
|--------|--------|-------|
| `404` | `Space not found` | Invalid space ID |

---

## `POST /api/spaces/{id}/refresh`

Re-parse the space YAML file and update the stored hash.

**Response:** `200 OK`

```json
{
  "id": "a1b2c3d4-5678-9abc-def0-123456789abc",
  "name": "backend-api",
  "file_hash": "new-hash-value...",
  "refreshed": true
}
```

**What happens:**

1. Reads the space's `file_path`
2. Parses the YAML file
3. Computes a new SHA-256 hash
4. Updates the database record

**Errors:**

| Status | Detail | Cause |
|--------|--------|-------|
| `404` | `Space not found` | Invalid space ID |
| `400` | `Space has no file_path` | Corrupt space record |
| `400` | `Space file not found` | File deleted from disk |
| `400` | `Invalid space file` | YAML parse error or validation failure |

Error messages intentionally omit file system paths to prevent information disclosure.

---

## `GET /api/spaces/{id}/sources`

List all sources linked to a space.

**Response:** `200 OK`

```json
[
  {
    "space_id": "a1b2c3d4-...",
    "source_id": "src-uuid-...",
    "group_id": null,
    "tag_filter": null,
    "created_at": "2025-01-15T10:30:00+00:00",
    "id": "src-uuid-...",
    "type": "file",
    "title": "API Design Guide",
    "content": "...",
    "mime_type": "text/markdown",
    "filename": "api-design.md",
    "url": null,
    "storage_path": null,
    "size_bytes": 4096,
    "content_hash": "abc123...",
    "user_id": "user-uuid-...",
    "user_display_name": "Developer",
    "source_created_at": "2025-01-10T08:00:00+00:00",
    "source_updated_at": "2025-01-15T10:00:00+00:00"
  }
]
```

Returns linked sources with their full metadata via a JOIN against the `sources` table.

**Errors:**

| Status | Detail | Cause |
|--------|--------|-------|
| `404` | `Space not found` | Invalid space ID |

---

## `POST /api/spaces/{id}/sources`

Link a source to a space. Three link types are supported.

**Request body:**

```json
{
  "source_id": "src-uuid-here",
  "group_id": null,
  "tag_filter": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `source_id` | `string \| null` | Link a specific source by ID |
| `group_id` | `string \| null` | Link all sources in a group |
| `tag_filter` | `string \| null` | Link all sources with this tag |

At least one field must be non-null.

**Response:** `201 Created`

```json
{
  "id": "link-uuid-here",
  "space_id": "a1b2c3d4-...",
  "source_id": "src-uuid-...",
  "group_id": null,
  "tag_filter": null,
  "created_at": "2025-01-15T10:30:00+00:00"
}
```

**Errors:**

| Status | Detail | Cause |
|--------|--------|-------|
| `404` | `Space not found` | Invalid space ID |
| `400` | Validation error | No link fields provided |

---

## `DELETE /api/spaces/{id}/sources/{source_id}`

Unlink a source from a space.

**Response:** `204 No Content`

**Errors:**

| Status | Detail | Cause |
|--------|--------|-------|
| `404` | `Space not found` | Invalid space ID |

---

## `GET /api/spaces/{id}/packs`

List packs attached to a space (includes global + space-scoped packs).

**Response:** `200 OK`

```json
[
  {
    "id": "pack-uuid-here",
    "namespace": "acme",
    "name": "python-standards",
    "version": "1.0.0",
    "description": "Python coding standards for ACME"
  }
]
```

Returns an empty array if no packs are attached.

**Errors:**

| Status | Detail | Cause |
|--------|--------|-------|
| `404` | `Space not found` | Invalid space ID |

---

## Conversations and Spaces

When creating a conversation via the chat endpoint, pass `space_id` in the request to associate it with a space:

```json
{
  "message": "Hello",
  "space_id": "a1b2c3d4-5678-9abc-def0-123456789abc"
}
```

The `space_id` is validated:

- Must be a valid UUID format
- Must reference an existing space

If the space is active, its instructions are automatically injected into the system prompt for the chat session.

## Next Steps

- [CLI Commands](commands.md) — terminal equivalents
- [Config Overlay](config-overlay.md) — how space config is applied
- [Troubleshooting](troubleshooting.md) — common API errors
