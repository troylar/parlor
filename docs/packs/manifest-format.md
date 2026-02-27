# Manifest Format

Every pack requires a `pack.yaml` manifest file in its root directory. The manifest declares the pack's identity and lists its artifacts.

## Minimal Example

The simplest possible pack — one skill:

```yaml title="pack.yaml"
name: my-pack
namespace: myteam
artifacts:
  - type: skill
    name: greet
```

With this manifest and a file at `skills/greet.yaml`, you have a valid pack.

## Full Example

A pack using all available fields:

```yaml title="pack.yaml"
name: python-conventions
namespace: acme
version: "2.1.0"
description: Acme Python development standards and tooling
artifacts:
  - type: skill
    name: commit
  - type: skill
    name: review
  - type: rule
    name: coding-standards
  - type: rule
    name: security-policy
  - type: instruction
    name: project-context
  - type: config_overlay
    name: safety-defaults
  - type: mcp_server
    name: filesystem
    file: mcp/fs-server.yaml
```

## Field Reference

### Top-Level Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | Yes | — | Pack name. Must match `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$` |
| `namespace` | string | Yes | — | Pack namespace. Same format rules as `name` |
| `version` | string | No | `"0.0.0"` | Semantic version string |
| `description` | string | No | `""` | Human-readable description |
| `artifacts` | list | Yes | — | List of artifact entries |

### Artifact Entry Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `type` | string | Yes | — | One of: `skill`, `rule`, `instruction`, `context`, `memory`, `mcp_server`, `config_overlay` |
| `name` | string | Yes | — | Artifact name (used in FQN as `@namespace/type/name`) |
| `file` | string | No | Auto-detected | Relative path to the artifact file |

## Name Validation

Both `name` and `namespace` must match this pattern:

```
^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$
```

- Starts with a letter or digit
- Contains only letters, digits, dots, hyphens, underscores
- Maximum 64 characters

Valid names: `my-pack`, `acme.tools`, `python_conventions`, `v2`

Invalid names: `-starts-with-dash`, `.hidden`, `has spaces`, `AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` (65 chars)

## File Resolution

When an artifact entry omits the `file` field, Anteroom resolves it automatically:

1. Look in the [type directory](artifact-types.md#type-to-directory-mapping) for the artifact type
2. Probe these extensions in order: `.yaml`, `.md`, `.txt`, `.json`
3. Use the first match

For example, an artifact with `type: skill` and `name: commit` resolves to:

```
skills/commit.yaml  → skills/commit.md  → skills/commit.txt  → skills/commit.json
```

### Explicit File Paths

Use the `file` field when your artifact lives in a non-standard location:

```yaml
artifacts:
  - type: mcp_server
    name: filesystem
    file: mcp/fs-server.yaml
```

File paths are relative to the pack directory. **Path traversal is blocked** — paths like `../outside.yaml` or `/etc/passwd` are rejected during validation. Anteroom uses `Path.resolve().is_relative_to()` to enforce this.

## Directory Layout Convention

The standard pack layout follows the type-to-directory mapping:

```
my-pack/
├── pack.yaml
├── skills/
│   ├── commit.yaml
│   └── review.yaml
├── rules/
│   ├── coding-standards.md
│   └── security-policy.md
├── instructions/
│   └── project-context.md
├── context/
│   └── api-docs.md
├── memories/
│   └── team-prefs.md
├── mcp_servers/
│   └── filesystem.yaml
└── config_overlays/
    └── safety-defaults.yaml
```

You don't need all directories — only include the ones your pack uses.

## Annotated Examples

### Example 1: Single-Skill Pack

The minimal useful pack:

```
greet-pack/
├── pack.yaml
└── skills/
    └── greet.yaml
```

```yaml title="pack.yaml"
name: greet-pack
namespace: demo
version: "1.0.0"
description: A simple greeting skill
artifacts:
  - type: skill
    name: greet
```

```yaml title="skills/greet.yaml"
name: greet
description: Greet the user
prompt: |
  Say hello to the user in a friendly way. If they provided a name,
  use it: {args}
```

### Example 2: Python Conventions Pack

A realistic team standards pack:

```
python-conventions/
├── pack.yaml
├── skills/
│   ├── commit.yaml
│   └── review.yaml
├── rules/
│   ├── coding-standards.md
│   └── security-policy.md
├── instructions/
│   └── stack-overview.md
└── config_overlays/
    └── safety.yaml
```

```yaml title="pack.yaml"
name: python-conventions
namespace: acme
version: "2.0.0"
description: Acme Python team development standards
artifacts:
  - type: skill
    name: commit
  - type: skill
    name: review
  - type: rule
    name: coding-standards
  - type: rule
    name: security-policy
  - type: instruction
    name: stack-overview
  - type: config_overlay
    name: safety
```

```yaml title="config_overlays/safety.yaml"
safety:
  approval_mode: ask_for_writes
  bash_sandbox:
    allow_network: false
```

### Example 3: Multi-Pack Repository

A git repo containing multiple packs:

```
acme-packs/
├── python-conventions/
│   ├── pack.yaml
│   ├── skills/
│   └── rules/
├── security-review/
│   ├── pack.yaml
│   ├── skills/
│   └── rules/
└── frontend-standards/
    ├── pack.yaml
    ├── skills/
    └── rules/
```

Each subdirectory with a `pack.yaml` is discovered and installed independently when used as a [pack source](pack-sources.md).

## Validation

Run `aroom pack install PATH` to validate a manifest. Common validation errors:

| Error | Cause |
|-------|-------|
| `name is required` | Missing `name` field in manifest |
| `namespace is required` | Missing `namespace` field in manifest |
| `Invalid pack name: ...` | Name doesn't match the allowed pattern |
| `Invalid pack namespace: ...` | Namespace doesn't match the allowed pattern |
| `Unknown artifact type: ...` | Type is not one of the 7 valid types |
| `Artifact file not found: ...` | Referenced file doesn't exist |
| `Path traversal detected: ...` | File path escapes the pack directory |

You can also use `aroom artifact check` to validate installed artifacts:

```bash
$ aroom artifact check
```

See [Health Check](health-check.md) for details on all validation checks.

## Next Steps

- [Quickstart](quickstart.md) — create and install your first pack
- [Artifact Types](artifact-types.md) — details on each artifact type
- [Pack Commands](pack-commands.md) — CLI reference for managing packs
