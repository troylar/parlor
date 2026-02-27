# Pack & Artifact Commands

CLI reference for managing packs and artifacts.

## Pack Commands

### aroom pack list

List all installed packs.

```bash
$ aroom pack list
```

```
acme/python-conventions  v2.0.0  6 artifacts
acme/security-review     v1.1.0  3 artifacts
demo/my-first-pack       v1.0.0  1 artifact
```

### aroom pack install PATH

Install a pack from a local directory.

```bash
$ aroom pack install ./my-pack/
```

```
Installed pack demo/my-pack v1.0.0 (3 artifacts)
```

**Flags**:

| Flag | Description |
|------|-------------|
| `--project` | Copy the pack directory into `.anteroom/packs/{namespace}/{name}/` for version control |

**Errors**:

- `Pack acme/my-pack is already installed` — use `aroom pack update` instead
- `Artifact file not found: skills/missing.yaml` — referenced file doesn't exist
- `Path traversal detected` — a `file` field in the manifest escapes the pack directory

### aroom pack show NAMESPACE/NAME

Show details of an installed pack.

```bash
$ aroom pack show acme/python-conventions
```

```
Pack: acme/python-conventions
Version: 2.0.0
Description: Acme Python development standards
Installed: 2025-12-01 10:30:00
Artifacts:
  @acme/skill/commit       skill           v1
  @acme/skill/review       skill           v1
  @acme/rule/coding-standards  rule        v1
  @acme/rule/security-policy   rule        v1
  @acme/instruction/stack-overview  instruction  v1
  @acme/config_overlay/safety  config_overlay  v1
```

### aroom pack remove NAMESPACE/NAME

Remove an installed pack and its orphaned artifacts.

```bash
$ aroom pack remove acme/python-conventions
```

```
Removed pack acme/python-conventions (6 artifacts removed)
```

Artifacts shared with other packs are not deleted — only artifacts that belong exclusively to this pack are removed.

### aroom pack update PATH

Update an existing pack installation with new content.

```bash
$ aroom pack update ./my-pack/ --project
```

```
Updated pack acme/my-pack v1.0.0 -> v1.1.0 (3 artifacts)
```

**Flags**:

| Flag | Description |
|------|-------------|
| `--project` | Copy the updated pack into `.anteroom/packs/` |

### aroom pack attach NAMESPACE/NAME

Attach a pack to global or project scope. Attached packs have their artifacts active in the registry.

```bash
$ aroom pack attach acme/python-conventions
```

```
Attached acme/python-conventions (global)
```

**Flags**:

| Flag | Description |
|------|-------------|
| `--project` | Attach to current project only (scoped to working directory) |

### aroom pack detach NAMESPACE/NAME

Detach a pack from global or project scope.

```bash
$ aroom pack detach acme/python-conventions --project
```

```
Detached acme/python-conventions (project)
```

**Flags**:

| Flag | Description |
|------|-------------|
| `--project` | Detach from current project only |

### aroom pack add-source URL

Add a git pack source to your personal config (`~/.anteroom/config.yaml`).

```bash
$ aroom pack add-source https://github.com/acme/anteroom-packs.git
```

```
Added pack source: https://github.com/acme/anteroom-packs.git
Run aroom pack refresh to clone and install packs.
```

URL scheme validation: only `https://`, `ssh://`, and `git@host:path` are allowed. Plaintext `http://`, `file://`, and `ext::` are rejected.

### aroom pack sources

List configured pack sources and their cache status.

```bash
$ aroom pack sources
```

```
https://github.com/acme/packs.git  main  30min  cached (abc1234)
https://github.com/org/standards.git  main  0 (manual)  not cached
```

### aroom pack refresh

Manually trigger a refresh of all configured pack sources.

```bash
$ aroom pack refresh
```

```
Refreshing https://github.com/acme/packs.git... 2 packs updated
Refreshing https://github.com/org/standards.git... 1 pack installed
```

---

## Artifact Commands

### aroom artifact list

List all artifacts in the registry.

```bash
$ aroom artifact list
```

```
@core/skill/commit         skill           built_in  v1
@acme/skill/commit         skill           project   v2
@acme/rule/security        rule            project   v1
@local/skill/my-helper     skill           local     v1
```

**Flags**:

| Flag | Values | Description |
|------|--------|-------------|
| `--type` | `skill`, `rule`, `instruction`, `context`, `memory`, `mcp_server`, `config_overlay` | Filter by artifact type |
| `--namespace` | any string | Filter by namespace |
| `--source` | `built_in`, `global`, `team`, `project`, `local`, `inline` | Filter by source layer |

**Examples**:

```bash
# List only skills
$ aroom artifact list --type skill

# List only project-level artifacts
$ aroom artifact list --source project

# List artifacts in the "acme" namespace
$ aroom artifact list --namespace acme
```

### aroom artifact show FQN

Show full details of a single artifact, including version history.

```bash
$ aroom artifact show @acme/skill/commit
```

```
FQN: @acme/skill/commit
Type: skill
Namespace: acme
Name: commit
Source: project
Version: 2
Content Hash: a1b2c3d4e5f6...

Content:
  name: commit
  description: Create a well-formatted commit
  prompt: |
    Review staged changes and create a commit message...

Version History:
  v2  2025-12-15 14:00:00  hash: a1b2c3d4...
  v1  2025-12-01 10:30:00  hash: 9f8e7d6c...
```

### aroom artifact check

Run health checks on all artifacts and packs.

```bash
$ aroom artifact check
```

```
Artifacts: 12  Packs: 3  Size: 24.5 KB  Tokens: ~6,125

Issues:
  WARN  skill_collision    Skill "commit" defined in @core/skill/commit and @acme/skill/commit
  WARN  empty_artifact     @acme/memory/notes has only 3 words
  INFO  shadow             @acme/skill/commit shadows @core/skill/commit
  INFO  bloat              Top artifacts by size: @acme/context/api-docs (8.2 KB)

Healthy: yes (0 errors, 2 warnings, 2 info)
```

**Flags**:

| Flag | Description |
|------|-------------|
| `--json` | Output results as JSON (for CI/CD integration) |
| `--fix` | Auto-fix fixable issues (currently: removes exact duplicate content) |
| `--project` | Include lock file validation for the current directory |

**JSON output**:

```bash
$ aroom artifact check --json
```

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

See [Health Check](health-check.md) for details on all 9 check types.

---

## REPL Commands

All `/pack` subcommands are available in the CLI REPL:

| Command | Description |
|---------|-------------|
| `/pack list` (or `/packs`) | List installed packs |
| `/pack show <ns/name>` | Show pack details and artifacts |
| `/pack install <path>` | Install a pack from a local directory |
| `/pack update <path>` | Update an installed pack from a local directory |
| `/pack remove <ns/name>` | Remove a pack |
| `/pack attach <ns/name> [--project]` | Attach a pack to global or project scope |
| `/pack detach <ns/name> [--project]` | Detach a pack from global or project scope |
| `/pack sources` | List configured pack sources |
| `/pack refresh` | Pull latest from all pack sources |
| `/pack add-source <url>` | Add a git pack source to config |

### /artifact-check

Run artifact health checks from within the CLI REPL.

```
> /artifact-check
```

Equivalent to `aroom artifact check` but runs inside the active session.

---

## AI-Guided Pack Skills

These built-in skills provide AI-guided workflows for pack management. Unlike CLI commands that execute directly, skills use the AI to walk you through multi-step processes with context-aware guidance.

### /new-pack

Scaffold a new pack interactively. The AI asks what you want to package, designs the artifact structure, creates files, validates the manifest, and shows how to install.

```
> /new-pack
> /new-pack security rules for our Python projects
```

### /pack-lint

Validate a pack directory before installation. Checks manifest parsing, artifact file existence and validity, naming conventions, unreferenced files, and file sizes — without touching the database.

```
> /pack-lint ./my-pack/
```

### /pack-publish

Guide sharing a pack via git. Validates the pack, checks git status, helps initialize or push a repo, and shows teammates how to configure `pack_sources` to consume the pack.

```
> /pack-publish ./my-pack/
```

### /pack-doctor

Comprehensive pack ecosystem diagnostics. Runs health checks, interprets every issue with context, checks pack source health, validates lock files, and provides specific remediation steps. Richer than `/artifact-check` — it covers sources, lock files, and attachment state.

```
> /pack-doctor
```

### /pack-update

Check configured pack sources for updates and pull the latest versions. Compares before/after state and reports what changed: new packs installed, existing packs updated, and unchanged packs. If no sources are configured, explains how to add one.

```
> /pack-update
> /pack-update acme/python-conventions
```

## Next Steps

- [Health Check](health-check.md) — details on all 9 health check types
- [Pack Sources](pack-sources.md) — git-based pack distribution
- [Config Reference](config-reference.md) — pack source configuration
