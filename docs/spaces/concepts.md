# Concepts

Spaces organize your Anteroom experience into named workspaces. Each space bundles together the repos, packs, sources, instructions, and config overrides you need for a specific project, team, or workflow.

## Spaces

A space is defined by a single YAML file. The default workflow is local-first: `aroom space create <name>` (or `aroom space init`) creates a `.anteroom/space.yaml` file in your current project directory. For zero-config setup, `space init` derives the space name from the directory name.

Spaces can also be managed globally at `~/.anteroom/spaces/<name>.yaml` for workspaces not tied to a single project directory.

When a space is registered and activated, Anteroom loads its configuration and injects its context into every conversation. The space file is portable — it contains no machine-specific paths.

```yaml title=".anteroom/space.yaml"
name: backend-api
version: "1"

repos:
  - https://github.com/acme/api-server.git
  - https://github.com/acme/shared-libs.git

instructions: |
  You are working on the ACME backend API. Follow REST conventions.
  All endpoints must have OpenAPI documentation.

config:
  ai:
    model: gpt-4o
```

## Space Names

Space names must match `^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$`:

- Start with an alphanumeric character
- Contain only alphanumeric characters, hyphens, and underscores
- 1 to 64 characters long

**Valid names:** `backend-api`, `team_frontend`, `ml-pipeline-v2`

**Invalid names:** `-starts-with-dash`, `has spaces`, `a` (valid — 1 char minimum)

## Space File Location

By default, `aroom space create` and `aroom space init` create a local space file in your project directory. Global spaces under `~/.anteroom/spaces/` are for workspaces not tied to a specific directory.

| Location | Best For | How to Create | Origin |
|----------|----------|---------------|--------|
| `<project>/.anteroom/space.yaml` | Project-local spaces (default) | `aroom space create <name>` or `aroom space init` | `local` |
| `~/.anteroom/spaces/<name>.yaml` | Global personal workspaces | Create the file manually, then `aroom space load <path>` | `global` |
| Inside a git repo | Team-shared spaces | `aroom space create` from the repo root | `local` |

A space file NOT under `~/.anteroom/spaces/` is considered "local". The `aroom space list` command shows the origin of each space.

When a space file lives inside a git repo, changes to the space can be committed and shared via `git pull`. The companion `.local.yaml` file (machine-specific) should be added to `.gitignore`.

## Local Config

Each space has a companion `.local.yaml` file for machine-specific settings that should not be shared (e.g., where repos are cloned on your filesystem). The local file is placed next to the space file:

```yaml title="backend-api.local.yaml"
repos_root: /home/dev/projects/acme/repos
paths:
  api-server: /home/dev/projects/acme/repos/api-server
```

The local config must **never** be committed to version control. If the space file is in a git repo, add `*.local.yaml` to `.gitignore`. It stores:

| Field | Purpose |
|-------|---------|
| `repos_root` | Directory where `aroom space clone` puts repositories |
| `paths` | Mapping of repo names to local filesystem paths |

## Space Paths and Auto-Detection

When you clone repos or map directories to a space, Anteroom records those paths in the database. If you start `aroom chat` from within a mapped directory (or any subdirectory), Anteroom auto-detects the active space.

```
~/projects/acme/api-server/        → auto-detects "backend-api" space
~/projects/acme/api-server/src/    → also auto-detects (walks up parents)
~/projects/other/                  → no space auto-detected
```

## Config Overlay

The `config` section of a space file is merged into Anteroom's configuration stack. The precedence order (highest wins):

```
env vars / CLI flags
    ▼
project config
    ▼
space config        ← space overlay sits here
    ▼
personal config
    ▼
team config
    ▼
defaults
```

Team-enforced fields always override everything, including space config.

### Example

```yaml title="~/.anteroom/spaces/secure-team.yaml"
name: secure-team
config:
  ai:
    model: gpt-4o
  safety:
    approval_mode: ask
```

If your personal config sets `model: gpt-3.5-turbo`, the space overrides it to `gpt-4o`. But if a project config also sets a model, the project config wins (higher precedence).

## Instructions

The `instructions` field is injected into the system prompt when the space is active. Instructions are wrapped in `<space_instructions>` XML tags and sanitized to prevent prompt injection:

```yaml
instructions: |
  Follow the ACME coding standards.
  Always use type hints in Python code.
  Prefer composition over inheritance.
```

Instructions are injected in both the CLI REPL and the web UI chat.

## Sources

Sources linked to a space are automatically available for RAG context injection. You can link sources three ways:

| Link Type | Description |
|-----------|-------------|
| **Direct** | Link a specific source by ID |
| **Group** | Link all sources in a source group |
| **Tag filter** | Link all sources with a specific tag |

When a conversation starts in an active space, space-linked sources are automatically injected alongside any explicitly selected sources.

## Repos

The `repos` field lists git repository URLs. Use `aroom space clone <name>` to clone them:

```yaml
repos:
  - https://github.com/acme/api-server.git
  - https://github.com/acme/shared-libs.git
```

Repos are cloned into the repos root directory (default: `~/.anteroom/spaces/<name>/repos/`). You can change this per-machine via the local config or the `aroom space move-root` command.

URL scheme validation rejects `ext::`, `file://`, and other unsafe schemes.

## Packs

Spaces can reference named packs to activate:

```yaml
packs:
  - acme/python-standards
  - acme/security-baseline
```

Pack sources can also be defined at the space level:

```yaml
pack_sources:
  - url: https://github.com/acme/anteroom-packs.git
    branch: main
```

Space-scoped pack attachments are separate from global and directory-scoped attachments. When resolving active packs, Anteroom unions all three scopes: global + space + directory.

## Space-Scoped Artifacts

When a space is active, the artifact registry loads only artifacts from packs that are relevant to that space:

- **Globally attached packs** — always included
- **Space-scoped packs** — attached with `aroom pack attach <pack> --space`
- **Standalone artifacts** — not linked to any pack (always included)
- **Unattached pack artifacts** — excluded (installing a pack without attaching it does not activate its skills or rules)

In the **web UI**, registries are built per-request via `_get_request_registries()`, so concurrent requests in different spaces see the correct artifacts without interfering with each other. Rule enforcement uses a `rule_enforcer_override` parameter rather than mutating shared state.

In the **CLI**, registries are built at session start for the auto-detected or manually selected space.

## Database Model

Spaces are stored in SQLite with the following tables:

| Table | Purpose |
|-------|---------|
| `spaces` | Space metadata (id, name, file_path, file_hash) |
| `space_paths` | Mapped directories for auto-detection |
| `space_sources` | Junction table linking sources/groups/tags to spaces |
| `pack_attachments` | Pack scoping (includes `space_id` column) |
| `conversations` | Each conversation can have a `space_id` |
| `folders` | Each folder can have a `space_id` |

Foreign keys with `ON DELETE CASCADE` ensure cleanup when a space is deleted.

## File Watcher (Hot Reload)

Anteroom monitors the active space file for changes using mtime polling. When the file changes:

1. The new YAML is parsed and validated
2. If valid, the callback fires with the parsed config
3. If invalid (bad YAML, missing name), the change is ignored
4. The previous valid config remains active

The poll interval defaults to 5 seconds and is clamped to a minimum of 1 second. It can be overridden via team config with `space_refresh_interval`.

## Next Steps

- [Quickstart](quickstart.md) — create your first space
- [Space File Format](space-file-format.md) — all fields and validation rules
- [CLI Commands](commands.md) — manage spaces from the terminal
