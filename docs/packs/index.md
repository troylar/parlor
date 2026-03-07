# Packs & Artifacts

## Why Packs?

Your team has coding standards, security policies, prompt templates, and model preferences. Without packs, everyone configures these independently &mdash; or doesn't. Packs let you bundle all of it into a single installable package and share it via git. Install once, stay in sync automatically.

**Common examples:**

- A **compliance pack** with regulatory rules and review skills
- A **DevOps pack** with deployment skills and safety overrides
- A **writing pack** with editorial skills and style instructions

You don't need to create packs to benefit from them &mdash; install one that someone else built, and your AI immediately follows those conventions.

## What's What

| Concept | What It Is |
|---------|-----------|
| **Artifact** | A named, versioned piece of content (skill, rule, instruction, etc.) loaded into the agent's context |
| **Pack** | A directory with a `pack.yaml` manifest and one or more artifact files |
| **Pack Source** | A git repository containing packs, auto-cloned and refreshed by Anteroom |
| **Artifact Registry** | The in-memory index that resolves which artifacts the agent sees, with 6-layer precedence |
| **Lock File** | A snapshot of installed pack state for reproducibility and tamper detection |
| **Health Check** | 9 automated checks for conflicts, quality, bloat, and drift |

## How It Works

```
Git Repos (pack sources)
      │
      ▼
  Clone / Pull → Cache (~/.anteroom/cache/sources/)
      │
      ▼
  Scan for pack.yaml → Parse & Validate
      │
      ▼
  Install → SQLite (artifacts, packs, pack_artifacts)
      │
      ▼
  Artifact Registry (6-layer precedence resolution)
      │
      ▼
  Agent Loop (skills invocable, rules enforced, config merged)
```

## 7 Artifact Types

| Type | Role |
|------|------|
| [skill](artifact-types.md#skill) | Reusable prompt template (`/skill-name`) |
| [rule](artifact-types.md#rule) | Always-on instruction injected every turn |
| [instruction](artifact-types.md#instruction) | Static system prompt context |
| [context](artifact-types.md#context) | Dynamic reference material |
| [memory](artifact-types.md#memory) | Persistent cross-session knowledge |
| [mcp_server](artifact-types.md#mcp_server) | MCP server connection config |
| [config_overlay](artifact-types.md#config_overlay) | YAML config fragment merged at runtime |

## Quick Links

### Getting Started

- [Quickstart](quickstart.md) — zero to packs in 5 minutes
- [Core Concepts](concepts.md) — the mental model behind packs and artifacts
- [How Packs Work](how-packs-work.md) — deep dive: lifecycle, config layering, DB storage, conflict resolution, rule enforcement

### Reference

- [Artifact Types](artifact-types.md) — all 7 types with examples
- [Manifest Format](manifest-format.md) — `pack.yaml` field reference
- [Pack Commands](pack-commands.md) — CLI and REPL commands
- [Config Reference](config-reference.md) — `pack_sources` configuration
- [API Reference](api-reference.md) — HTTP endpoints

### AI-Guided Skills

Built-in skills for AI-guided pack workflows:

| Skill | Purpose |
|-------|---------|
| `/new-pack` | Scaffold a new pack interactively |
| `/pack-lint` | Validate a pack directory before install |
| `/pack-publish` | Guide sharing a pack via git |
| `/pack-doctor` | Diagnose ecosystem issues with guided remediation |
| `/pack-update` | Check for and pull latest versions from sources |

These complement the CLI commands — skills walk you through multi-step workflows with context-aware AI guidance.

### Distribution & Operations

- [Pack Sources](pack-sources.md) — git-based distribution
- [Lock File](lock-file.md) — reproducibility and tamper detection
- [Health Check](health-check.md) — 9 automated quality checks
- [Troubleshooting](troubleshooting.md) — common problems and fixes

### Tutorials

- [Install Your First Pack](tutorials/install-first-pack.md)
- [Create a Pack from Scratch](tutorials/create-pack-from-scratch.md)
- [Share a Pack via Git](tutorials/share-pack-via-git.md)
- [Team Standardization](tutorials/team-standardization.md)
- [Automatic Updates](tutorials/automatic-updates.md)
- [Manage Conflicts](tutorials/manage-conflicts.md)
- [Health Check Diagnosis](tutorials/health-check-diagnosis.md)
- [CI/CD Integration](tutorials/ci-cd-integration.md)
