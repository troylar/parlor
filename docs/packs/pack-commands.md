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

### /artifact-check

Run artifact health checks from within the CLI REPL.

```
> /artifact-check
```

Equivalent to `aroom artifact check` but runs inside the active session.

## Next Steps

- [Health Check](health-check.md) — details on all 9 health check types
- [Pack Sources](pack-sources.md) — git-based pack distribution
- [Config Reference](config-reference.md) — pack source configuration
