# Lock File

The lock file (`.anteroom/anteroom.lock.yaml`) records the exact state of installed packs — versions, artifact content hashes, and git source refs. It enables reproducible installations and tamper detection.

## Why Use a Lock File

- **Reproducibility**: teammates install identical artifacts from the same lock state
- **Tamper detection**: the [health check](health-check.md) compares lock file hashes against the database
- **CI/CD gating**: validate that pack state matches expectations in pipelines
- **Audit trail**: track exactly which pack versions and artifact content are deployed

## Format

```yaml title=".anteroom/anteroom.lock.yaml"
version: 1
packs:
  - name: python-conventions
    namespace: acme
    version: "2.0.0"
    source_path: /Users/you/.anteroom/cache/sources/a1b2c3d4e5f6/python-conventions
    artifacts:
      - fqn: "@acme/skill/commit"
        content_hash: "a1b2c3d4e5f67890abcdef1234567890abcdef1234567890abcdef1234567890"
        type: skill
        name: commit
      - fqn: "@acme/skill/review"
        content_hash: "f6e5d4c3b2a17890abcdef1234567890abcdef1234567890abcdef1234567890"
        type: skill
        name: review
      - fqn: "@acme/rule/coding-standards"
        content_hash: "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
        type: rule
        name: coding-standards
    source_url: "https://github.com/acme/anteroom-packs.git"
    source_ref: "abc1234def5678"
```

### Fields

| Field | Description |
|-------|-------------|
| `version` | Lock file format version (currently `1`) |
| `packs` | List of installed packs |
| `packs[].name` | Pack name |
| `packs[].namespace` | Pack namespace |
| `packs[].version` | Pack version string |
| `packs[].source_path` | Local filesystem path where the pack was installed from |
| `packs[].artifacts` | List of artifacts in the pack |
| `packs[].artifacts[].fqn` | Artifact fully qualified name |
| `packs[].artifacts[].content_hash` | SHA-256 hash of the artifact content |
| `packs[].artifacts[].type` | Artifact type |
| `packs[].artifacts[].name` | Artifact name |
| `packs[].source_url` | Git source URL (present if pack came from a git source) |
| `packs[].source_ref` | Git commit SHA (present if pack came from a git source) |

## Generating a Lock File

The lock file is generated automatically when packs are installed or updated. You can also generate it manually via the health check with `--fix`.

## Committing the Lock File

**Commit `.anteroom/anteroom.lock.yaml` to version control.** This ensures all team members and CI pipelines work with the same pack state.

```bash
$ git add .anteroom/anteroom.lock.yaml
$ git commit -m "chore: update pack lock file"
```

## Detecting Drift

The health check compares the lock file against the current database state:

```bash
$ aroom artifact check --project
```

If any pack version or artifact content hash differs from the lock file, the check reports `lock_drift` issues:

```
ERROR  lock_drift  Pack acme/python-conventions: lock version 2.0.0, installed version 2.1.0
WARN   lock_drift  Artifact @acme/skill/commit: content hash mismatch
```

## When the Lock File Gets Stale

The lock file becomes stale when:

- A pack is installed, updated, or removed without regenerating the lock
- A pack source refreshes and installs new versions
- An artifact's content is modified directly in the database

Re-run the health check with `--fix` to detect and report drift, or regenerate the lock by reinstalling packs.

## CI/CD Gate Example

Use the lock file to gate deployments:

```yaml title=".github/workflows/validate-packs.yml"
- name: Validate pack lock file
  run: |
    aroom artifact check --project --json | jq -e '.healthy'
```

If any `lock_drift` errors exist, `healthy` is `false` and the `jq -e` check fails the pipeline. See the [CI/CD Integration tutorial](tutorials/ci-cd-integration.md) for a complete workflow.

## Next Steps

- [Health Check](health-check.md) — all 9 checks including lock drift
- [CI/CD Integration Tutorial](tutorials/ci-cd-integration.md) — lock validation in pipelines
