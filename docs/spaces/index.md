# Spaces

## Why Spaces?

Different projects need different AI configurations. Your frontend project might use GPT-4o with React coding standards, while your data pipeline uses Claude with PEP 8 rules and access to internal docs. Spaces let you save these configurations and switch between them instantly.

**What a space gives you:**

- **Custom instructions** injected into every conversation
- **Per-space model selection** (or use the global default)
- **Linked sources** for RAG context (docs, URLs, code)
- **Config overrides** for safety settings, tool access, and more

Create a space for a project directory and Anteroom auto-detects it when you `cd` in. No flags needed.

## What's What

| Concept | What It Is |
|---------|-----------|
| **Space** | A named workspace defined by a YAML file |
| **Space file** | A YAML file defining the workspace (local to project by default, or global in `~/.anteroom/spaces/`) |
| **Local config** | `<name>.local.yaml` — machine-specific overrides (repos root, paths) |
| **Space paths** | Directories mapped to a space for auto-detection |
| **Config overlay** | Config values from the space file merged into Anteroom's config |
| **Space sources** | Sources (files, URLs, groups, tags) linked to a space |

## How It Works

```
Space File (.yaml)
    │
    ├─→ repos         → git clone into repos root
    ├─→ pack_sources   → git-based pack distribution
    ├─→ packs          → install named packs
    ├─→ sources        → link files/URLs as context
    ├─→ instructions   → injected into system prompt
    └─→ config         → merged as config overlay layer
```

When you activate a space:

1. Anteroom loads the space file
2. Instructions are injected into the system prompt
3. Config overrides are applied (between personal and project layers)
4. Linked sources are available for RAG and context injection
5. Space-scoped packs are activated

## Two Interfaces

Spaces work in both the CLI and the web UI:

| Feature | CLI | Web UI |
|---------|-----|--------|
| List spaces | `aroom space list` | Space picker sidebar |
| Create space | `aroom space create <name>` | — (CLI operation) |
| Init space (auto-name) | `aroom space init` | — (CLI operation) |
| Switch space | `/space switch <name>` | Click space in sidebar |
| Clone repos | `aroom space clone <name>` | — (terminal operation) |
| View sources | `/space show` | `GET /api/spaces/{id}/sources` |
| Refresh | `/space refresh` | `POST /api/spaces/{id}/refresh` |

## Quick Links

### Getting Started

- [Quickstart](quickstart.md) — create and use your first space in 5 minutes
- [Concepts](concepts.md) — understand the mental model

### Reference

- [Space File Format](space-file-format.md) — all fields, validation rules, examples
- [CLI Commands](commands.md) — `aroom space` and `/space` REPL commands
- [API Reference](api-reference.md) — HTTP endpoints for spaces
- [Config Overlay](config-overlay.md) — how space config merges with other layers
- [Config Reference](config-reference.md) — configuration fields

### Operations

- [Repo Management](repo-management.md) — cloning, mapping, and moving repos
- [Hot Reload](hot-reload.md) — file watcher and manual refresh
- [Troubleshooting](troubleshooting.md) — common issues and fixes

### Tutorials

- [Set Up a Team Space](tutorials/team-space.md) — shared workspace for a team
- [Multi-Repo Project](tutorials/multi-repo.md) — manage repos across a space
- [Space with Custom Config](tutorials/custom-config.md) — override model, safety, and more
