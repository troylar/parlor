# Tutorial: Health Check Diagnosis

Run a health check on a messy artifact ecosystem, interpret every issue type, fix each one, and verify clean.

> **Tip:** For an AI-guided version of this workflow, use `/pack-doctor` in the REPL. It runs the health check, interprets every issue, checks pack sources and lock files, and walks you through remediation steps.

## The Setup

You have several packs installed from different sources, and things have gotten messy. Let's diagnose and fix everything.

## Step 1: Run the Full Check

```bash
$ aroom artifact check --project
```

```
Artifacts: 18  Packs: 4  Size: 42.1 KB  Tokens: ~10,525

Issues:
  ERROR  config_conflict    Field "safety.approval_mode" set to "auto" by @dev/config_overlay/fast
                            and "ask_for_writes" by @acme/config_overlay/safety
  ERROR  malformed          @acme/config_overlay/broken-yaml: invalid YAML content
  ERROR  lock_drift         Pack acme/python-conventions: lock version 1.0.0, installed version 2.0.0
  WARN   skill_collision    Skill "commit" defined in @core/skill/commit and @acme/skill/commit
  WARN   empty_artifact     @acme/memory/notes has only 3 words
  WARN   orphaned           @old/rule/deprecated is not linked to any pack (source: project)
  WARN   duplicate_content  @acme/rule/security and @acme/rule/security-v2 have identical content
  INFO   shadow             @acme/skill/commit (project) shadows @core/skill/commit (built_in)
  INFO   bloat              18 artifacts, 42.1 KB total, ~10,525 estimated tokens
                            Top by size: @acme/context/api-docs (18.2 KB)

Healthy: no (3 errors, 4 warnings, 2 info)
```

Three ERRORs need fixing. Let's address each one.

## Step 2: Fix Config Conflict

**Issue**: Two config overlays set `safety.approval_mode` to different values.

**Diagnosis**: The `dev/fast-mode` pack sets it to `auto`, and the `acme/standards` pack sets it to `ask_for_writes`. These are mutually exclusive.

**Fix**: Remove the less-important pack:

```bash
$ aroom pack remove dev/fast-mode
```

Or create a local override that explicitly sets the desired value:

```yaml title=".anteroom/config_overlays/safety-override.yaml"
safety:
  approval_mode: ask_for_writes
```

## Step 3: Fix Malformed YAML

**Issue**: `@acme/config_overlay/broken-yaml` contains invalid YAML.

**Diagnosis**: Inspect the artifact:

```bash
$ aroom artifact show @acme/config_overlay/broken-yaml
```

Look for YAML syntax errors (missing colons, bad indentation, tabs instead of spaces).

**Fix**: Edit the artifact file in the pack source, fix the YAML, bump the pack version, and push. Or remove the artifact if it's not needed:

```bash
$ aroom pack remove acme/broken-pack
```

## Step 4: Fix Lock Drift

**Issue**: The lock file says `acme/python-conventions` is v1.0.0, but v2.0.0 is installed.

**Diagnosis**: Someone updated the pack without regenerating the lock file.

**Fix**: Regenerate the lock file by reinstalling or updating the pack:

```bash
$ aroom pack update ./python-conventions/ --project
```

Then commit the updated lock file:

```bash
$ git add .anteroom/anteroom.lock.yaml
$ git commit -m "chore: update pack lock file"
```

## Step 5: Address Warnings

### Empty Artifact

```bash
$ aroom artifact show @acme/memory/notes
```

If it's a placeholder, either add real content or remove it from the pack.

### Orphaned Artifact

`@old/rule/deprecated` isn't linked to any pack. It was probably left behind when a pack was removed.

If it's not needed, it can be cleaned up by removing it from the database (or it will be cleaned up when you reinstall packs).

### Duplicate Content

Two artifacts have identical content. Use `--fix` to auto-remove the duplicate:

```bash
$ aroom artifact check --fix
```

```
Fixed 1 duplicate content issue
```

### Skill Collision

`@acme/skill/commit` overrides `@core/skill/commit`. This is intentional — the team pack provides a customized commit skill. No action needed.

## Step 6: Verify Clean

```bash
$ aroom artifact check --project
```

```
Artifacts: 15  Packs: 3  Size: 38.5 KB  Tokens: ~9,625

Issues:
  WARN   skill_collision  Skill "commit" defined in @core/skill/commit and @acme/skill/commit
  INFO   shadow           @acme/skill/commit (project) shadows @core/skill/commit (built_in)
  INFO   bloat            15 artifacts, 38.5 KB total, ~9,625 estimated tokens

Healthy: yes (0 errors, 1 warning, 2 info)
```

Zero errors. The remaining warning and info items are expected.

## Quick Reference: Issue → Fix

| Category | Severity | Fix |
|----------|----------|-----|
| `config_conflict` | ERROR | Remove one conflicting pack or create a local override |
| `malformed` | ERROR | Fix YAML syntax or remove the broken artifact |
| `lock_drift` | ERROR | Regenerate lock file after updating packs |
| `skill_collision` | WARN | Intentional: no action. Unintentional: rename one skill |
| `empty_artifact` | WARN | Add content or remove the placeholder |
| `orphaned` | WARN | Remove or re-link to a pack |
| `duplicate_content` | WARN | Run `--fix` to auto-remove duplicates |
| `shadow` | INFO | Informational — expected precedence behavior |
| `bloat` | INFO | Review large artifacts, consider trimming context |

## Next Steps

- [Health Check Reference](../health-check.md) — all 9 checks in detail
- [CI/CD Integration](ci-cd-integration.md) — automate health checks
