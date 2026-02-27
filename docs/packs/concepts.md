# Core Concepts

This page explains the ideas behind packs and artifacts. No commands here — just the mental model you need before using them.

## Artifacts

An **artifact** is a named, versioned piece of content that Anteroom loads into the agent's context. Every skill file, rule file, instruction file, and MCP server config you use is an artifact under the hood.

### The 7 Artifact Types

| Type | Purpose |
|------|---------|
| `skill` | A reusable prompt template invoked with `/skill-name` or by the AI via `invoke_skill` |
| `rule` | An always-on instruction injected into every agent turn (coding standards, security policies) |
| `instruction` | A static block of text prepended to the system prompt (project context, onboarding notes) |
| `context` | Dynamic context injected per-turn (API docs, reference material) |
| `memory` | Persistent memory across sessions (user preferences, learned patterns) |
| `mcp_server` | An MCP server configuration (command, args, env, tool filters) |
| `config_overlay` | A YAML fragment that merges into the running config (safety settings, model overrides) |

### Fully Qualified Names (FQN)

Every artifact has a unique identifier called a **fully qualified name**:

```
@namespace/type/name
```

Examples:

| FQN | Namespace | Type | Name |
|-----|-----------|------|------|
| `@acme/skill/commit` | acme | skill | commit |
| `@core/rule/security` | core | rule | security |
| `@myteam/config_overlay/safety` | myteam | config_overlay | safety |
| `@tools/mcp_server/filesystem` | tools | mcp_server | filesystem |
| `@local/instruction/onboarding` | local | instruction | onboarding |

**FQN format rules** (regex: `^@[a-z0-9_-]+/[a-z_]+/[a-z0-9_][a-z0-9_.-]*$`):

- Namespace: lowercase alphanumeric, hyphens, underscores
- Type: lowercase letters and underscores (must be one of the 7 types)
- Name: starts with letter or digit, then letters, digits, underscores, dots, hyphens

Invalid FQNs:

| Invalid FQN | Why |
|-------------|-----|
| `acme/skill/commit` | Missing `@` prefix |
| `@Acme/skill/commit` | Uppercase in namespace |
| `@acme/plugin/commit` | `plugin` is not a valid type |
| `@acme/skill/My Skill` | Spaces not allowed |
| `@acme/skill/.hidden` | Name cannot start with dot |

## The 6-Layer Precedence Stack

Anteroom resolves artifacts through a 6-layer stack. When two artifacts share the same type and name, the higher layer wins:

```
  INLINE     ← highest priority (5)
  LOCAL      ← (4)
  PROJECT    ← (3)
  TEAM       ← (2)
  GLOBAL     ← (1)
  BUILT_IN   ← lowest priority (0)
```

| Layer | Source | Typical Location |
|-------|--------|------------------|
| `built_in` | Ships with Anteroom | `src/anteroom/cli/default_skills/` |
| `global` | User's personal artifacts | `~/.anteroom/skills/`, `~/.anteroom/rules/` |
| `team` | Team config distribution | Team config `artifacts` section |
| `project` | Installed packs, project-level files | `.anteroom/packs/`, `.anteroom/skills/` |
| `local` | Working directory overrides | `.anteroom/skills/` (current dir only) |
| `inline` | Programmatic registration | Runtime-only, not persisted |

### Override Example

Suppose three sources all define a skill named `commit`:

| FQN | Source | Content |
|-----|--------|---------|
| `@core/skill/commit` | built_in | Generic commit message helper |
| `@acme/skill/commit` | project (from a pack) | Acme's commit conventions |
| `@local/skill/commit` | local | Your personal override |

The **local** version wins because layer 4 > layer 3 > layer 0. Running `/commit` uses your local version. Remove it, and the project pack's version takes over. Remove the pack, and the built-in activates.

## Packs

A **pack** is a directory containing a manifest (`pack.yaml`) and one or more artifact files. Packs are the distribution unit — they bundle related artifacts for installation, versioning, and sharing.

### Pack Structure

```
my-pack/
├── pack.yaml           # manifest (required)
├── skills/
│   ├── commit.yaml
│   └── review.yaml
├── rules/
│   └── security.md
└── config_overlays/
    └── safety.yaml
```

The manifest declares the pack's identity and lists its artifacts:

```yaml title="pack.yaml"
name: my-pack
namespace: acme
version: "1.0.0"
description: Acme development standards
artifacts:
  - type: skill
    name: commit
  - type: skill
    name: review
  - type: rule
    name: security
  - type: config_overlay
    name: safety
```

When no `file` field is specified, Anteroom looks for the artifact at `{type_dir}/{name}.{ext}` where `type_dir` maps to the conventional directory name and the extension is probed in order: `.yaml`, `.md`, `.txt`, `.json`.

### Pack Name Rules

Both `name` and `namespace` must match: `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$`

- Start with a letter or digit
- Up to 64 characters
- Letters, digits, dots, hyphens, underscores only

## Pack Sources (Git Distribution)

Packs can be distributed via git repositories. Configure a **pack source** in your config, and Anteroom clones the repo, scans for `pack.yaml` files, and installs all packs found:

```yaml title="~/.anteroom/config.yaml"
pack_sources:
  - url: https://github.com/acme/anteroom-packs.git
    branch: main
    refresh_interval: 30  # minutes; 0 = manual only
```

A **background worker** periodically pulls updates and installs new or changed packs. The flow:

```
git repo → clone/pull → scan for pack.yaml → parse manifest → install/update packs → artifact registry
```

Pack sources are cached at `~/.anteroom/cache/sources/{hash}/` where `{hash}` is the first 12 characters of the SHA-256 of the URL.

## Lock Files

A **lock file** (`.anteroom/anteroom.lock.yaml`) records the exact state of installed packs: versions, artifact content hashes, and git source refs. It enables:

- **Reproducibility**: teammates install identical artifacts
- **Tamper detection**: health check compares lock against DB
- **CI/CD gating**: validate lock file in pipelines

Commit the lock file to version control. Regenerate it after installing or updating packs.

## The Artifact Registry

The `ArtifactRegistry` is the in-memory index that the agent loop queries at runtime. On startup, it loads all artifacts from the database, resolves precedence conflicts, and provides fast lookup by FQN, type, namespace, or name search.

Key characteristics:

- **Atomic reload**: swaps the entire index at once, no partial states
- **500 artifact cap**: warns if exceeded (performance guard)
- **Precedence resolution**: higher-layer artifacts overwrite lower-layer ones on FQN collision
- **Filter and search**: list by type/namespace/source, or substring search by name

The registry is the single source of truth for what the agent sees. If an artifact isn't in the registry, it doesn't affect the agent.

## How It All Fits Together

```
Pack Sources (git repos)
        │
        ▼
    Clone / Pull
        │
        ▼
    Cache Directory (~/.anteroom/cache/sources/)
        │
        ▼
    Scan for pack.yaml manifests
        │
        ▼
    Parse & Validate Manifests
        │
        ▼
    Install Packs → SQLite (artifacts, packs, pack_artifacts tables)
        │
        ▼
    Artifact Registry (in-memory, precedence-resolved)
        │
        ▼
    Agent Loop (skills, rules, instructions, context, config overlays)
```

## Next Steps

- [Quickstart](quickstart.md) — install your first pack in 5 minutes
- [Artifact Types](artifact-types.md) — deep dive into each type
- [Manifest Format](manifest-format.md) — complete `pack.yaml` reference
