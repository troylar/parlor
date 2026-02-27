# Repo Management

Spaces can manage multiple git repositories. This page covers cloning, mapping, and organizing repos.

## Defining Repos

Add repo URLs to the space file:

```yaml
repos:
  - https://github.com/acme/api-server.git
  - https://github.com/acme/shared-libs.git
  - https://github.com/acme/frontend.git
```

Repos are not cloned automatically when a space is created — you must run `aroom space clone` explicitly.

## Cloning

### First Clone

```bash
$ aroom space clone backend-api
Repos root directory
  [/home/dev/.anteroom/spaces/backend-api/repos]: /home/dev/projects/acme
  Saved repos root to /home/dev/.anteroom/spaces/backend-api.local.yaml
  OK   https://github.com/acme/api-server.git → /home/dev/projects/acme/api-server
  OK   https://github.com/acme/shared-libs.git → /home/dev/projects/acme/shared-libs
```

### What Happens

1. **Repos root resolution**: checks local config, prompts user, or uses default
2. **Shallow clone**: `git clone --depth=1` for each repo URL
3. **Skip existing**: repos already at the destination are skipped
4. **Path sync**: successfully cloned paths are saved to the database
5. **Local config update**: chosen repos root saved to `.local.yaml`

### Repos Root

The repos root is where cloned repos live on disk. Resolution order:

| Priority | Source | Example |
|----------|--------|---------|
| 1 | Local config `repos_root` | `~/.anteroom/spaces/backend-api.local.yaml` |
| 2 | User prompt (TTY only) | Interactive input at clone time |
| 3 | Default | `~/.anteroom/spaces/<name>/repos/` |

In non-interactive mode (piped stdin), the default is used without prompting.

### Clone Errors

```
FAIL https://github.com/acme/private-repo.git: git clone failed: Authentication failed
FAIL ext://malicious: URL scheme not allowed: ext::
FAIL https://slow.example.com/repo.git: git clone timed out (120s)
```

Failed clones don't affect other repos — each is cloned independently. Credential details are sanitized from error messages.

## Mapping Directories

Map existing directories that weren't cloned via `aroom space clone`:

```bash
$ aroom space map backend-api /home/dev/projects/acme/custom-service
Mapped: /home/dev/projects/acme/custom-service → backend-api
```

### Use Cases

- You already have repos cloned to specific locations
- You work on repos not listed in the space file
- You want to associate non-git directories with a space

### How It Works

1. Validates the directory exists
2. Checks for duplicate paths
3. Appends to the space's mapped paths in the database

Mapped paths enable [auto-detection](concepts.md#space-paths-and-auto-detection): starting `aroom chat` from a mapped directory (or any subdirectory) activates the space automatically.

## Moving the Repos Root

Change where future clones go:

```bash
$ aroom space move-root backend-api /home/dev/new-location
Repos root updated: /home/dev/new-location
```

This updates the `repos_root` field in the local config (`.local.yaml`). It does **not** move any files on disk. Existing cloned repos stay where they are.

### Workflow for Relocating Repos

1. Move the repos on disk: `mv /old/path/* /new/path/`
2. Update the repos root: `aroom space move-root my-space /new/path`
3. Re-run clone to update paths: `aroom space clone my-space`

## Auto-Detection

When paths are mapped (either via clone or manual mapping), Anteroom can auto-detect which space you're working in:

```bash
$ cd /home/dev/projects/acme/api-server
$ aroom chat
Space: backend-api    # auto-detected
>
```

Auto-detection walks up parent directories:

```
/home/dev/projects/acme/api-server/           → match
/home/dev/projects/acme/api-server/src/       → match (parent)
/home/dev/projects/acme/api-server/src/api/   → match (grandparent)
/home/dev/other/                              → no match
```

If multiple spaces have overlapping paths, the deepest (most specific) match wins.

## URL Scheme Validation

Repo URLs are validated to prevent unsafe operations:

| Scheme | Allowed | Reason |
|--------|---------|--------|
| `https://` | Yes | Standard secure git access |
| `git@` (SSH) | Yes | Standard SSH access |
| `http://` | No | Insecure transport |
| `file://` | No | Local filesystem access |
| `ext::` | No | Arbitrary command execution |

Validation happens at both parse time and clone time.

## Path Deduplication

When syncing paths to the database, duplicate `local_path` entries are automatically deduplicated. The first occurrence wins. This prevents mapping the same directory twice to the same space.

## Database Schema

Mapped paths are stored in the `space_paths` table:

| Column | Type | Description |
|--------|------|-------------|
| `id` | `TEXT` | UUID primary key |
| `space_id` | `TEXT` | Foreign key to `spaces` |
| `repo_url` | `TEXT` | Git remote URL (empty for manual mappings) |
| `local_path` | `TEXT` | Absolute path on the local filesystem |
| `created_at` | `TEXT` | ISO 8601 timestamp |

The `space_id` foreign key cascades on delete — when a space is deleted, all its path mappings are removed.

## Next Steps

- [Quickstart](quickstart.md) — try cloning repos hands-on
- [CLI Commands](commands.md) — full command reference
- [Concepts](concepts.md#space-paths-and-auto-detection) — auto-detection details
