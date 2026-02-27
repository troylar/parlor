# Health Check

The artifact health check (`aroom artifact check`) runs 9 automated checks against your installed artifacts and packs. It detects conflicts, quality issues, bloat, and drift.

## Running the Health Check

```bash
# Basic check
$ aroom artifact check

# Include lock file validation
$ aroom artifact check --project

# Auto-fix fixable issues
$ aroom artifact check --fix

# JSON output for CI/CD
$ aroom artifact check --json

# All flags combined
$ aroom artifact check --project --fix --json
```

From the REPL:

```
> /artifact-check
```

## The 9 Checks

| # | Category | Severity | Fixable | What It Detects |
|---|----------|----------|---------|-----------------|
| 1 | `config_conflict` | ERROR | No | Config overlay artifacts from different sources setting the same field to different values |
| 2 | `skill_collision` | WARN | No | Skills with the same name from different sources |
| 3 | `shadow` | INFO | No | Higher-precedence artifact shadowing a lower-precedence one |
| 4 | `empty_artifact` | WARN | No | Artifacts with fewer than 10 words of content |
| 5 | `malformed` | ERROR/WARN | No | Invalid FQN format, unknown type/source, invalid YAML in config_overlay/mcp_server |
| 6 | `lock_drift` | ERROR/WARN | No | Mismatch between lock file and current database state |
| 7 | `bloat` | INFO | No | Summary of total artifact count, size, and estimated tokens |
| 8 | `orphaned` | WARN | No | Artifacts not linked to any pack and not from built_in/local/inline sources |
| 9 | `duplicate_content` | WARN | Yes | Artifacts with identical content (same SHA-256 hash) |

## Check Details

### 1. Config Overlay Conflicts

Detects when two config overlays from different packs set the same configuration field to different values. This is an ERROR because it creates ambiguity about which value takes effect.

**Example output**:

```
ERROR  config_conflict  Field "safety.approval_mode" set to "auto" by @acme/config_overlay/safety
                        and "ask_for_writes" by @security/config_overlay/strict
```

**Resolution**: Remove one of the conflicting overlays, or create a higher-precedence local overlay that explicitly sets the field.

### 2. Skill Name Collisions

Detects when multiple artifacts define a skill with the same name. The higher-precedence one wins, but this warns you that a collision exists.

**Example output**:

```
WARN   skill_collision  Skill "commit" defined in @core/skill/commit and @acme/skill/commit
                        Active: @acme/skill/commit (project)
```

**Resolution**: This is often intentional (a pack overriding a built-in skill). If unintentional, rename one of the skills.

### 3. Shadow Warnings

Reports when a higher-precedence artifact shadows a lower-precedence one. Informational — no action needed unless the shadow is unintentional.

**Example output**:

```
INFO   shadow  @acme/skill/commit (project) shadows @core/skill/commit (built_in)
```

### 4. Empty Artifacts

Flags artifacts with very little content (fewer than 10 words). These are likely placeholders or mistakes.

**Example output**:

```
WARN   empty_artifact  @acme/memory/notes has only 3 words
```

### 5. Malformed Artifacts

Validates artifact integrity:

- FQN matches the required format (`@namespace/type/name`)
- Type is one of the 7 valid artifact types
- Source is one of the 6 valid source layers
- Config overlays and MCP server artifacts contain valid YAML

**Example output**:

```
ERROR  malformed  @acme/config_overlay/bad-yaml: invalid YAML content
WARN   malformed  Artifact with invalid FQN format: "acme/skill/test"
```

### 6. Lock Drift

Compares the lock file (`.anteroom/anteroom.lock.yaml`) against the database. Only runs with `--project` flag.

**Example output**:

```
ERROR  lock_drift  Pack acme/python-conventions: lock version 2.0.0, installed version 2.1.0
WARN   lock_drift  Artifact @acme/skill/commit: content hash mismatch
```

**Resolution**: Regenerate the lock file or revert to the locked pack version.

### 7. Bloat

Reports aggregate statistics about all artifacts. Always returns one INFO issue with the top 5 artifacts by size.

**Example output**:

```
INFO   bloat  12 artifacts, 24.5 KB total, ~6,125 estimated tokens
              Top by size: @acme/context/api-docs (8.2 KB), @acme/instruction/onboarding (4.1 KB), ...
```

Token estimation: bytes / 4.

### 8. Orphaned Artifacts

Finds artifacts that are not linked to any pack and didn't come from built_in, local, or inline sources. These may be leftovers from removed packs.

**Example output**:

```
WARN   orphaned  @acme/rule/old-standard is not linked to any pack (source: project)
```

### 9. Duplicate Content

Detects artifacts with identical content (same SHA-256 hash). This is the only **fixable** check — running with `--fix` removes the duplicates.

**Example output**:

```
WARN   duplicate_content  @acme/rule/security and @acme/rule/security-v2 have identical content
                          (hash: a1b2c3d4...)
```

**With `--fix`**: removes the lower-precedence duplicate.

## Understanding the Report

```
Artifacts: 12  Packs: 3  Size: 24.5 KB  Tokens: ~6,125

Issues:
  ERROR  config_conflict    Field "safety.approval_mode" conflict between 2 overlays
  WARN   skill_collision    Skill "commit" defined in 2 sources
  WARN   empty_artifact     @acme/memory/notes has only 3 words
  WARN   duplicate_content  2 artifacts with identical content
  INFO   shadow             @acme/skill/commit shadows @core/skill/commit
  INFO   bloat              12 artifacts, 24.5 KB total, ~6,125 estimated tokens

Healthy: no (1 error, 3 warnings, 2 info)
```

- **Healthy** = no ERROR-severity issues
- ERRORs must be fixed for a healthy ecosystem
- WARNs should be reviewed but don't block
- INFOs are purely informational

## JSON Output

```bash
$ aroom artifact check --json
```

```json
{
  "healthy": false,
  "artifact_count": 12,
  "pack_count": 3,
  "total_size_bytes": 25088,
  "estimated_tokens": 6125,
  "error_count": 1,
  "warn_count": 3,
  "info_count": 2,
  "issues": [
    {
      "severity": "error",
      "category": "config_conflict",
      "message": "Field \"safety.approval_mode\" conflict between 2 overlays",
      "fixable": false
    },
    {
      "severity": "warn",
      "category": "skill_collision",
      "message": "Skill \"commit\" defined in 2 sources",
      "fixable": false
    }
  ]
}
```

## CI/CD Integration

Gate your pipeline on artifact health:

```bash
$ aroom artifact check --project --json | jq -e '.healthy'
```

Exit code 0 = healthy, non-zero = unhealthy. See the [CI/CD Integration tutorial](tutorials/ci-cd-integration.md) for a complete GitHub Actions workflow.

## The --fix Flag

Currently, `--fix` resolves one issue type:

| Category | Fix Action |
|----------|-----------|
| `duplicate_content` | Removes the lower-precedence duplicate artifact |

After fixing, the report includes a count of fixed issues. Other issue types require manual resolution.

## Next Steps

- [Health Check Diagnosis Tutorial](tutorials/health-check-diagnosis.md) — walk through fixing every issue type
- [CI/CD Integration Tutorial](tutorials/ci-cd-integration.md) — automated health checks in pipelines
- [Troubleshooting](troubleshooting.md) — common problems and fixes
