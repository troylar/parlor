# API Reference

HTTP endpoints for managing artifacts and packs via the web UI or programmatic access. All endpoints require authentication.

## Artifact Endpoints

### GET /api/artifacts

List all artifacts with optional filtering.

**Query Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `type` | string | Filter by artifact type (`skill`, `rule`, `instruction`, `context`, `memory`, `mcp_server`, `config_overlay`) |
| `namespace` | string | Filter by namespace |
| `source` | string | Filter by source layer (`built_in`, `global`, `team`, `project`, `local`, `inline`) |

**Example**:

```bash
# List all artifacts
$ curl http://localhost:8080/api/artifacts

# List only skills
$ curl http://localhost:8080/api/artifacts?type=skill

# List project-level artifacts in the "acme" namespace
$ curl "http://localhost:8080/api/artifacts?source=project&namespace=acme"
```

**Response** (200):

```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "fqn": "@acme/skill/commit",
    "type": "skill",
    "namespace": "acme",
    "name": "commit",
    "source": "project",
    "version": 2,
    "content": "name: commit\ndescription: ...\nprompt: ...",
    "content_hash": "a1b2c3d4e5f6...",
    "metadata": {},
    "created_at": "2025-12-01T10:30:00Z",
    "updated_at": "2025-12-15T14:00:00Z"
  }
]
```

---

### GET /api/artifacts/{fqn}

Show a single artifact with its version history.

**Path Parameter**: FQN (e.g., `@acme/skill/commit`)

**Example**:

```bash
$ curl http://localhost:8080/api/artifacts/@acme/skill/commit
```

**Response** (200):

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "fqn": "@acme/skill/commit",
  "type": "skill",
  "namespace": "acme",
  "name": "commit",
  "source": "project",
  "version": 2,
  "content": "name: commit\ndescription: ...\nprompt: ...",
  "content_hash": "a1b2c3d4e5f6...",
  "metadata": {},
  "created_at": "2025-12-01T10:30:00Z",
  "updated_at": "2025-12-15T14:00:00Z",
  "versions": [
    {
      "id": "660e8400-e29b-41d4-a716-446655440001",
      "version": 2,
      "content_hash": "a1b2c3d4e5f6...",
      "created_at": "2025-12-15T14:00:00Z"
    },
    {
      "id": "660e8400-e29b-41d4-a716-446655440000",
      "version": 1,
      "content_hash": "9f8e7d6c5b4a...",
      "created_at": "2025-12-01T10:30:00Z"
    }
  ]
}
```

**Errors**:

| Status | Reason |
|--------|--------|
| 400 | Invalid FQN format |
| 404 | Artifact not found |

---

### GET /api/artifacts/check

Run artifact health checks.

**Example**:

```bash
$ curl http://localhost:8080/api/artifacts/check
```

**Response** (200):

```json
{
  "healthy": true,
  "artifact_count": 12,
  "pack_count": 3,
  "total_size_bytes": 25088,
  "estimated_tokens": 6125,
  "error_count": 0,
  "warn_count": 2,
  "info_count": 2,
  "issues": [
    {
      "severity": "warn",
      "category": "skill_collision",
      "message": "Skill \"commit\" defined in @core/skill/commit and @acme/skill/commit",
      "fixable": false
    }
  ]
}
```

Sensitive keys (`source_path`, `error`) are stripped from issue details in the API response.

---

## Pack Endpoints

### GET /api/packs

List all installed packs.

**Example**:

```bash
$ curl http://localhost:8080/api/packs
```

**Response** (200):

```json
[
  {
    "id": "770e8400-e29b-41d4-a716-446655440000",
    "name": "python-conventions",
    "namespace": "acme",
    "version": "2.0.0",
    "description": "Acme Python development standards",
    "installed_at": "2025-12-01T10:30:00Z",
    "updated_at": "2025-12-15T14:00:00Z",
    "artifact_count": 6
  }
]
```

`source_path` is stripped from responses to prevent information disclosure.

---

### GET /api/packs/sources

List configured pack sources with cache status.

**Example**:

```bash
$ curl http://localhost:8080/api/packs/sources
```

**Response** (200):

```json
[
  {
    "url": "https://github.com/acme/anteroom-packs.git",
    "branch": "main",
    "refresh_interval": 30,
    "cached": true,
    "ref": "abc1234"
  },
  {
    "url": "https://github.com/org/standards.git",
    "branch": "main",
    "refresh_interval": 0,
    "cached": false,
    "ref": null
  }
]
```

---

### POST /api/packs/refresh

Manually trigger a refresh of all configured pack sources.

**Example**:

```bash
$ curl -X POST http://localhost:8080/api/packs/refresh
```

**Response** (200):

```json
{
  "sources": [
    {
      "url": "https://github.com/acme/anteroom-packs.git",
      "success": true,
      "packs_installed": 0,
      "packs_updated": 2,
      "packs_attached": 0,
      "changed": true,
      "error": ""
    }
  ],
  "quarantined": [],
  "quarantine_reason": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `sources` | array | Per-source refresh results |
| `sources[].url` | string | Source URL |
| `sources[].success` | bool | Whether the pull/clone succeeded |
| `sources[].packs_installed` | int | Number of newly installed packs |
| `sources[].packs_updated` | int | Number of updated packs |
| `sources[].packs_attached` | int | Number of newly attached packs |
| `sources[].changed` | bool | Whether the git ref changed |
| `sources[].error` | string | Error message (empty on success) |
| `quarantined` | array | Pack IDs detached due to compliance failure |
| `quarantine_reason` | string or null | Human-readable reason (intentionally generic — details are in server logs) |

When a refreshed pack causes a compliance violation during config rebuild, Anteroom detaches the offending packs (quarantine), rebuilds config without them, and returns the quarantine details in this response. The `quarantine_reason` is kept generic to avoid leaking internal policy details to API consumers.

---

### GET /api/packs/{namespace}/{name}

Show a single pack with its artifact list.

**Path Parameters**: `namespace` and `name`

**Example**:

```bash
$ curl http://localhost:8080/api/packs/acme/python-conventions
```

**Response** (200):

```json
{
  "id": "770e8400-e29b-41d4-a716-446655440000",
  "name": "python-conventions",
  "namespace": "acme",
  "version": "2.0.0",
  "description": "Acme Python development standards",
  "installed_at": "2025-12-01T10:30:00Z",
  "updated_at": "2025-12-15T14:00:00Z",
  "artifacts": [
    {
      "fqn": "@acme/skill/commit",
      "type": "skill",
      "name": "commit",
      "source": "project",
      "version": 2,
      "content_hash": "a1b2c3d4e5f6..."
    }
  ]
}
```

`source_path` is stripped. Artifact `content` is omitted from pack detail responses to reduce payload size.

**Errors**:

| Status | Reason |
|--------|--------|
| 404 | Pack not found |

## Next Steps

- [Pack Commands](pack-commands.md) — CLI equivalents
- [Health Check](health-check.md) — details on health check categories
