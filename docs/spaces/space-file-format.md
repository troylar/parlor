# Space File Format

The space file is the single source of truth for a workspace. It's a YAML file stored in `~/.anteroom/spaces/` that defines everything Anteroom needs to configure itself for a project, team, or workflow.

## File Locations

| File | Purpose |
|------|---------|
| `~/.anteroom/spaces/<name>.yaml` | Space definition (shared, versioned) |
| `~/.anteroom/spaces/<name>.local.yaml` | Machine-specific overrides (never shared) |

Both files must be valid YAML mappings. The space file is limited to **256 KB**.

## Minimal Example

```yaml
name: my-project
version: "1"
```

Only `name` is required. All other fields are optional and default to empty.

## Full Example

```yaml
name: backend-api
version: "1"

repos:
  - https://github.com/acme/api-server.git
  - https://github.com/acme/shared-libs.git

pack_sources:
  - url: https://github.com/acme/anteroom-packs.git
    branch: main

packs:
  - acme/python-standards
  - acme/security-baseline

sources:
  - path: /docs/api-design.md
  - url: https://wiki.acme.com/coding-standards

instructions: |
  You are working on the ACME backend API.
  Follow REST conventions. All endpoints must have OpenAPI docs.

config:
  ai:
    model: gpt-4o
  safety:
    approval_mode: ask
```

## Field Reference

### `name` (required)

The space's unique identifier.

| Property | Value |
|----------|-------|
| Type | `string` |
| Required | Yes |
| Pattern | `^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$` |

Rules:
- Must start with an alphanumeric character
- May contain alphanumeric characters, hyphens (`-`), and underscores (`_`)
- 1 to 64 characters long
- Must be unique across all spaces

**Valid:** `backend-api`, `team_frontend`, `ml-pipeline-v2`, `a`

**Invalid:** `-starts-with-dash`, `has spaces`, `special!chars`, `a-very-long-name-that-exceeds-sixty-four-characters-which-is-the-maximum-allowed`

### `version` (optional)

Schema version for forward compatibility.

| Property | Value |
|----------|-------|
| Type | `string` |
| Default | `"1"` |

Currently only version `"1"` is supported. Always quoted to prevent YAML interpreting it as a number.

### `repos` (optional)

Git repository URLs to associate with this space.

| Property | Value |
|----------|-------|
| Type | `list[string]` |
| Default | `[]` |

```yaml
repos:
  - https://github.com/acme/api-server.git
  - https://github.com/acme/shared-libs.git
```

URL scheme validation rejects unsafe schemes:

| Scheme | Allowed? |
|--------|----------|
| `https://` | Yes |
| `git@` (SSH) | Yes |
| `http://` | No (insecure) |
| `file://` | No (local access) |
| `ext::` | No (arbitrary command execution) |

Use `aroom space clone <name>` to clone repos after creating the space.

### `pack_sources` (optional)

Git repositories containing pack definitions.

| Property | Value |
|----------|-------|
| Type | `list[string \| object]` |
| Default | `[]` |

**String shorthand** (uses `main` branch):

```yaml
pack_sources:
  - https://github.com/acme/anteroom-packs.git
```

**Object form** (specify branch):

```yaml
pack_sources:
  - url: https://github.com/acme/anteroom-packs.git
    branch: main
  - url: https://github.com/acme/extra-packs.git
    branch: release
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | `string` | (required) | Git remote URL |
| `branch` | `string` | `"main"` | Branch to track |

Same URL scheme validation as `repos`.

### `packs` (optional)

Named packs to activate when this space is active.

| Property | Value |
|----------|-------|
| Type | `list[string]` |
| Default | `[]` |
| Format | `namespace/name` |

```yaml
packs:
  - acme/python-standards
  - acme/security-baseline
```

Each entry must be in `namespace/name` format. Packs are resolved from installed packs in the database. Space-scoped pack attachments are unioned with global and project-scoped attachments.

### `sources` (optional)

Files or URLs to link as context sources.

| Property | Value |
|----------|-------|
| Type | `list[string \| object]` |
| Default | `[]` |

**String shorthand** (treated as file path):

```yaml
sources:
  - /docs/api-design.md
  - /docs/coding-standards.md
```

**Object form**:

```yaml
sources:
  - path: /docs/api-design.md
  - url: https://wiki.acme.com/standards
  - path: /docs/runbook.md
    url: null
```

| Field | Type | Description |
|-------|------|-------------|
| `path` | `string \| null` | Local file path |
| `url` | `string \| null` | Remote URL |

At least one of `path` or `url` must be set. Path traversal (`..` segments) is rejected.

### `instructions` (optional)

Text injected into the system prompt when the space is active.

| Property | Value |
|----------|-------|
| Type | `string` |
| Default | `""` |

```yaml
instructions: |
  You are working on the ACME backend API.
  Follow REST conventions.
  All endpoints must have OpenAPI documentation.
```

Instructions are wrapped in `<space_instructions>` XML tags and sanitized via `sanitize_trust_tags()` to prevent prompt injection. They appear in both the CLI REPL and web UI chat.

Use YAML block scalar syntax (`|`) for multi-line instructions.

### `config` (optional)

Configuration overrides merged into Anteroom's config stack.

| Property | Value |
|----------|-------|
| Type | `dict` |
| Default | `{}` |

```yaml
config:
  ai:
    model: gpt-4o
    temperature: 0.7
  safety:
    approval_mode: ask
  cli:
    verbose: true
```

The `config` dict is deep-merged into the configuration stack at the space layer (between personal and project). See [Config Overlay](config-overlay.md) for precedence rules.

Any field from `AppConfig` and its nested dataclasses can be set here. See [Config Reference](config-reference.md) for the complete list.

## Local Config File

The companion `.local.yaml` file stores machine-specific settings.

### Location

For a space file at `~/.anteroom/spaces/backend-api.yaml`, the local config is:

```
~/.anteroom/spaces/backend-api.local.yaml
```

### Fields

```yaml
repos_root: /home/dev/projects/acme/repos
paths:
  api-server: /home/dev/projects/acme/repos/api-server
  shared-libs: /home/dev/projects/acme/repos/shared-libs
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `repos_root` | `string` | `""` | Directory where `aroom space clone` puts repositories |
| `paths` | `dict[string, string]` | `{}` | Mapping of repo names to local filesystem paths |

### When It's Created

The local config is created automatically when you:

1. Run `aroom space clone <name>` — saves the repos root you chose
2. Run `aroom space move-root <name> <path>` — updates the repos root

You can also create it manually.

### Why It Exists

Different team members clone repos to different directories. The local config keeps machine-specific paths out of the shared space file, so the space YAML can be committed to version control while each developer has their own local paths.

## Validation

Anteroom validates space files at multiple points:

| When | What's Checked |
|------|----------------|
| `aroom space create` | Full validation: name, URLs, sources, structure |
| `parse_space_file()` | File exists, size limit, valid YAML, name present and valid |
| `validate_space()` | URL schemes, path traversal, name pattern |
| Hot reload | Valid YAML, dict structure, name present |

### Validation Errors

```
Invalid space name: '-bad-name'
repos: URL scheme not allowed: file:///local/repo
sources: path traversal not allowed: ../../etc/passwd
Space file exceeds 256KB limit: /path/to/huge.yaml
Space file must be a YAML mapping: /path/to/list.yaml
```

## Next Steps

- [Quickstart](quickstart.md) — create your first space
- [Config Overlay](config-overlay.md) — how space config merges
- [CLI Commands](commands.md) — manage spaces from the terminal
