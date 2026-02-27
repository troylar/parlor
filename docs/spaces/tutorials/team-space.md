# Tutorial: Set Up a Team Space

Create a shared workspace that your entire team can use. Everyone gets the same repos, instructions, packs, and config — but with machine-specific local paths.

## Goal

Set up a `backend-team` space that:

- Clones three shared repositories
- Enforces Python coding standards via packs
- Injects team-specific instructions
- Sets a consistent AI model

## Step 1: Create the Space File

```yaml title="~/.anteroom/spaces/backend-team.yaml"
name: backend-team
version: "1"

repos:
  - https://github.com/acme/api-server.git
  - https://github.com/acme/shared-libs.git
  - https://github.com/acme/api-docs.git

packs:
  - acme/python-standards
  - acme/security-baseline

instructions: |
  You are working on the ACME backend team's codebase.

  Conventions:
  - Follow PEP 8 and use type hints on all public functions
  - All API endpoints need OpenAPI documentation
  - Database queries use parameterized placeholders only
  - Tests required for all new public functions

  Architecture:
  - api-server: FastAPI application, main product
  - shared-libs: Shared utilities consumed by multiple services
  - api-docs: OpenAPI specs and generated docs

config:
  ai:
    model: gpt-4o
  safety:
    approval_mode: ask_for_writes
```

## Step 2: Share the File

Commit the space file to a team repo or shared drive. Each team member copies it to their `~/.anteroom/spaces/` directory.

The space file is portable — it contains no machine-specific paths. Each developer's local config handles the differences.

## Step 3: Register and Clone

Each team member runs:

```bash
$ aroom space create ~/.anteroom/spaces/backend-team.yaml
Created space: backend-team (id: a1b2c3d4...)

$ aroom space clone backend-team
Repos root directory
  [/home/alice/.anteroom/spaces/backend-team/repos]: /home/alice/work/acme
  Saved repos root to /home/alice/.anteroom/spaces/backend-team.local.yaml
  OK   https://github.com/acme/api-server.git → /home/alice/work/acme/api-server
  OK   https://github.com/acme/shared-libs.git → /home/alice/work/acme/shared-libs
  OK   https://github.com/acme/api-docs.git → /home/alice/work/acme/api-docs
```

Each person chooses their own repos root. Alice uses `/home/alice/work/acme`, Bob uses `/Users/bob/projects/acme`. The local config stores this per-machine.

## Step 4: Verify

```bash
$ cd /home/alice/work/acme/api-server
$ aroom chat
Space: backend-team
> /space show
backend-team
  File:  /home/alice/.anteroom/spaces/backend-team.yaml
  Convs: 0
  Paths:
    https://github.com/acme/api-server.git → /home/alice/work/acme/api-server
    https://github.com/acme/shared-libs.git → /home/alice/work/acme/shared-libs
    https://github.com/acme/api-docs.git → /home/alice/work/acme/api-docs
```

## Step 5: Add Extra Local Repos (Optional)

If a team member works on additional repos not in the space file:

```bash
$ aroom space map backend-team /home/alice/work/acme/internal-tool
Mapped: /home/alice/work/acme/internal-tool → backend-team
```

This is machine-local — it doesn't affect other team members.

## Updating the Space

When the team updates the space file (new repo, changed instructions), each member:

1. Copies the updated file to `~/.anteroom/spaces/backend-team.yaml`
2. Runs `/space refresh` in an active REPL session, or `aroom space refresh backend-team` from the terminal

The local config (`.local.yaml`) is untouched — only the shared space file changes.

## What Each Team Member Has

| File | Shared? | Contents |
|------|---------|----------|
| `backend-team.yaml` | Yes | Repos, packs, instructions, config |
| `backend-team.local.yaml` | No | `repos_root`, local paths |

## Next Steps

- [Multi-Repo Project](multi-repo.md) — manage repos across a space
- [Space with Custom Config](custom-config.md) — advanced config overrides
