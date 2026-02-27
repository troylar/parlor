# CLI Commands

Spaces are managed through two interfaces: the `aroom space` CLI subcommand and the `/space` REPL command. Both share the same underlying storage and logic.

## CLI: `aroom space`

Terminal commands for managing spaces outside of a chat session.

### `aroom space list`

List all registered spaces.

```bash
$ aroom space list
┌──────────────┬─────────────────────────────────────────┬──────────────────────────┐
│ Name         │ File Path                               │ Last Loaded              │
├──────────────┼─────────────────────────────────────────┼──────────────────────────┤
│ backend-api  │ /home/dev/.anteroom/spaces/backend-api…  │ 2025-01-15T10:30:00+00:00│
│ ml-pipeline  │ /home/dev/.anteroom/spaces/ml-pipeline…  │ 2025-01-15T09:15:00+00:00│
└──────────────┴─────────────────────────────────────────┴──────────────────────────┘
```

If no spaces exist, shows a hint:

```
No spaces found. Create one with: aroom space create <path>
```

### `aroom space create <path>`

Register a space from a YAML file.

```bash
$ aroom space create ~/.anteroom/spaces/backend-api.yaml
Created space: backend-api (id: a1b2c3d4...)
```

**What happens:**

1. Reads and parses the YAML file
2. Validates the space (name, URLs, sources, structure)
3. Checks for name conflicts
4. Creates the space record in the database with a SHA-256 file hash

**Errors:**

```bash
$ aroom space create nonexistent.yaml
Error: File not found: /path/to/nonexistent.yaml

$ aroom space create bad-name.yaml
Validation errors:
  - Invalid space name: '-bad-name'

$ aroom space create duplicate.yaml
Error: Space 'backend-api' already exists
```

### `aroom space show <name>`

Display details about a space.

```bash
$ aroom space show backend-api
backend-api
  ID:          a1b2c3d4-5678-9abc-def0-123456789abc
  File:        /home/dev/.anteroom/spaces/backend-api.yaml
  Hash:        a1b2c3d456789abc...
  Last loaded: 2025-01-15T10:30:00+00:00
  Created:     2025-01-10T08:00:00+00:00
```

### `aroom space delete <name>`

Remove a space and all its associated data.

```bash
$ aroom space delete backend-api
Deleted space: backend-api
```

**What's cleaned up:**

- Space record from `spaces` table
- All mapped paths from `space_paths` table
- Pack attachments scoped to this space
- Conversations and folders are unlinked (set to `space_id = NULL`), not deleted

### `aroom space refresh <name>`

Re-read the space file and update the stored hash.

```bash
$ aroom space refresh backend-api
Refreshed: backend-api (hash updated)
```

If the file hasn't changed:

```
Space file unchanged — nothing to refresh.
```

### `aroom space clone <name>`

Clone all repos defined in the space file.

```bash
$ aroom space clone backend-api
Repos root directory
  [/home/dev/.anteroom/spaces/backend-api/repos]: /home/dev/projects/acme
  Saved repos root to /home/dev/.anteroom/spaces/backend-api.local.yaml
  OK   https://github.com/acme/api-server.git → /home/dev/projects/acme/api-server
  OK   https://github.com/acme/shared-libs.git → /home/dev/projects/acme/shared-libs
```

**Repos root resolution:**

1. If the local config (`.local.yaml`) has `repos_root`, use it
2. Otherwise, prompt the user (default: `~/.anteroom/spaces/<name>/repos/`)
3. In non-interactive mode (piped stdin), use the default without prompting
4. Save the chosen root to the local config

**Clone behavior:**

- Uses `git clone --depth=1` for fast shallow clones
- Skips repos that already exist at the destination
- URL scheme validation rejects `ext::`, `file://`, and other unsafe schemes
- 120-second timeout per repo
- Credential-sanitized error messages on failure
- Successfully cloned paths are synced to `space_paths` in the database

### `aroom space map <name> <directory>`

Map a local directory to a space for auto-detection.

```bash
$ aroom space map backend-api /home/dev/projects/acme/custom-service
Mapped: /home/dev/projects/acme/custom-service → backend-api
```

Use this when you have a repo that wasn't cloned via `aroom space clone` but should be associated with the space. Starting `aroom chat` from this directory (or any subdirectory) will auto-detect the space.

**Validation:**

- Directory must exist
- Duplicate paths are silently skipped

### `aroom space move-root <name> <path>`

Change where repos are stored for a space (per-machine setting).

```bash
$ aroom space move-root backend-api /home/dev/new-location
Repos root updated: /home/dev/new-location
```

Updates the `repos_root` in the local config file (`.local.yaml`). The directory must exist. This does **not** move any files — it only changes where future `aroom space clone` operations will place repos.

## REPL: `/space`

In-session commands for managing spaces during a chat.

### `/space` or `/space list` or `/spaces`

List all spaces with conversation counts.

```
> /spaces
Spaces:
  backend-api — 12 conversations (active) a1b2c3d4...
  ml-pipeline — 3 conversations 5e6f7a8b...
```

The active space is highlighted with `(active)`.

### `/space switch <name>`

Switch to a different space within the current session.

```
> /space switch ml-pipeline
Active space: ml-pipeline
```

**What happens:**

1. Looks up the space by name
2. Sets it as the active space
3. Updates the current conversation's `space_id`
4. Injects the space's instructions into the system prompt

### `/space show` or `/space show <name>`

Display space details. Defaults to the active space if no name is given.

```
> /space show
backend-api
  File:  /home/dev/.anteroom/spaces/backend-api.yaml
  Convs: 12
  Paths:
    https://github.com/acme/api-server.git → /home/dev/projects/acme/api-server
    https://github.com/acme/shared-libs.git → /home/dev/projects/acme/shared-libs
```

### `/space refresh`

Re-read the active space's YAML file and update instructions.

```
> /space refresh
Refreshed: backend-api
```

Reloads the space file, updates the stored hash, and re-injects instructions if they changed. If the file has invalid YAML, the refresh is silently skipped and the previous config remains active.

### `/space clear`

Deactivate the current space.

```
> /space clear
Cleared space: backend-api
```

Removes the space association from the current conversation and strips space instructions from the system prompt.

### `/space create <path>`

Register a new space from within the REPL.

```
> /space create ~/.anteroom/spaces/new-project.yaml
Created space: new-project (a1b2c3d4...)
```

Same validation as `aroom space create`.

## Auto-Detection

When you start `aroom chat` from a directory mapped to a space (either via `clone` or `map`), Anteroom auto-detects the space:

```bash
$ cd /home/dev/projects/acme/api-server
$ aroom chat
Space: backend-api
>
```

Auto-detection walks up parent directories, so subdirectories also match:

```
~/projects/acme/api-server/        → detects "backend-api"
~/projects/acme/api-server/src/    → detects "backend-api" (walks up)
~/projects/acme/api-server/src/controllers/  → detects "backend-api" (walks up)
~/projects/other/                  → no space detected
```

The deepest (most specific) mapped path wins if multiple spaces overlap.

## CLI Flag: `--space`

Force a specific space when starting a chat session:

```bash
$ aroom chat --space ml-pipeline
```

This overrides auto-detection.

## Command Summary

| Action | CLI | REPL |
|--------|-----|------|
| List spaces | `aroom space list` | `/spaces` or `/space list` |
| Create space | `aroom space create <path>` | `/space create <path>` |
| Show details | `aroom space show <name>` | `/space show [name]` |
| Delete space | `aroom space delete <name>` | — |
| Refresh file | `aroom space refresh <name>` | `/space refresh` |
| Clone repos | `aroom space clone <name>` | — (terminal operation) |
| Map directory | `aroom space map <name> <dir>` | — (terminal operation) |
| Move repos root | `aroom space move-root <name> <path>` | — (terminal operation) |
| Switch space | — | `/space switch <name>` |
| Clear space | — | `/space clear` |

## Next Steps

- [Quickstart](quickstart.md) — try these commands hands-on
- [API Reference](api-reference.md) — HTTP equivalents for web UI
- [Repo Management](repo-management.md) — deep dive on cloning and mapping
