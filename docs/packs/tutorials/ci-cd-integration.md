# Tutorial: CI/CD Integration

Set up a GitHub Actions workflow that validates artifact health, checks lock file integrity, and gates deployments on pack consistency.

## What We're Building

A CI pipeline that:

1. Installs Anteroom
2. Runs artifact health checks with `--json` output
3. Validates the lock file matches installed state
4. Fails the build if any ERROR-severity issues exist

## Step 1: The Complete Workflow

```yaml title=".github/workflows/validate-packs.yml"
name: Validate Packs

on:
  push:
    paths:
      - '.anteroom/**'
      - 'pack.yaml'
  pull_request:
    paths:
      - '.anteroom/**'
      - 'pack.yaml'

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install Anteroom
        run: pip install anteroom

      - name: Run artifact health check
        run: |
          aroom artifact check --project --json > health-report.json
          cat health-report.json | jq .

      - name: Validate health
        run: |
          jq -e '.healthy' health-report.json

      - name: Check for errors
        if: failure()
        run: |
          echo "Artifact health check failed!"
          jq '.issues[] | select(.severity == "error")' health-report.json
```

## Step 2: Understanding the Gate

The key line is:

```bash
jq -e '.healthy' health-report.json
```

- `jq -e` exits with code 1 if the result is `false` or `null`
- `.healthy` is `true` only when there are zero ERROR-severity issues
- WARNs and INFOs don't fail the build

## Step 3: Lock File Validation

The `--project` flag includes lock drift detection. If your `.anteroom/anteroom.lock.yaml` doesn't match the installed pack state, `lock_drift` ERRORs appear and `healthy` becomes `false`.

Ensure the lock file is committed:

```bash
$ git add .anteroom/anteroom.lock.yaml
$ git commit -m "chore: update pack lock file"
```

## Step 4: Stricter Checks

To fail on warnings too (not just errors):

```yaml
      - name: Strict validation (no warnings)
        run: |
          WARNS=$(jq '.warn_count' health-report.json)
          ERRORS=$(jq '.error_count' health-report.json)
          if [ "$WARNS" -gt 0 ] || [ "$ERRORS" -gt 0 ]; then
            echo "Found $ERRORS errors and $WARNS warnings"
            jq '.issues[]' health-report.json
            exit 1
          fi
```

## Step 5: Auto-Fix in CI

For duplicate content issues (the only auto-fixable type), you can run `--fix` in CI and commit the result:

```yaml
      - name: Auto-fix and commit
        run: |
          aroom artifact check --fix --project --json > health-report.json
          FIXED=$(jq '.issues[] | select(.category == "duplicate_content")' health-report.json | wc -l)
          if [ "$FIXED" -gt 0 ]; then
            git config user.name "github-actions[bot]"
            git config user.email "github-actions[bot]@users.noreply.github.com"
            git add .anteroom/
            git commit -m "chore: auto-fix artifact duplicates" || true
            git push
          fi
```

## Step 6: Pack Refresh in CI

If your CI environment needs packs from a git source:

```yaml
      - name: Configure pack source
        run: |
          mkdir -p ~/.anteroom
          cat > ~/.anteroom/config.yaml << 'EOF'
          pack_sources:
            - url: https://github.com/acme/anteroom-packs.git
              branch: main
              refresh_interval: 0
          EOF

      - name: Refresh packs
        run: aroom pack refresh

      - name: Validate
        run: aroom artifact check --project --json | jq -e '.healthy'
```

## Example JSON Output

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
    },
    {
      "severity": "info",
      "category": "bloat",
      "message": "12 artifacts, 25.1 KB total, ~6,125 estimated tokens",
      "fixable": false
    }
  ]
}
```

## Next Steps

- [Health Check Reference](../health-check.md) — all 9 check types
- [Lock File](../lock-file.md) — lock file format and validation
- [Team Standardization](team-standardization.md) — enforce packs across a team
