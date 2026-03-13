# Tutorial: Automatic Updates

Configure background refresh for pack sources, understand failure behavior, and pin packs when stability matters.

> **Tip:** For on-demand updates with AI-guided feedback, use `/pack-update` in the REPL. It shows source status, pulls latest versions, and reports what changed.

## How Auto-Refresh Works

When `refresh_interval > 0`, Anteroom runs a background worker that:

1. Checks every 60 seconds which sources are due for refresh
2. Pulls changes from the git remote
3. If the commit SHA changed, re-scans for `pack.yaml` files
4. Installs new packs or updates existing ones

## Step 1: Configure Refresh Interval

```yaml title="~/.anteroom/config.yaml"
pack_sources:
  - url: https://github.com/acme/anteroom-packs.git
    branch: main
    refresh_interval: 30  # check every 30 minutes
```

The minimum interval is 5 minutes. Values below 5 are clamped up automatically.

## Step 2: Verify Auto-Pull

After Anteroom has been running for at least one refresh interval:

```bash
$ aroom pack sources
https://github.com/acme/anteroom-packs.git  main  30min  cached (abc1234)
```

The `cached (abc1234)` shows the current git ref. After a successful pull, this updates to the new ref.

## Step 3: Monitor Updates

When the worker detects changes and installs/updates packs, you'll see it reflected in:

```bash
$ aroom pack list  # shows updated versions
$ aroom artifact list  # shows new/updated artifacts
```

## Failure Backoff

If a pull fails (network error, auth problem, etc.):

| Consecutive Failures | Wait Before Retry |
|---------------------|-------------------|
| 1 | 2x the normal interval |
| 2 | 4x |
| 3 | 8x |
| ... | doubles each time |
| 10 | **Worker auto-disabled (all sources)** |

Backoff is tracked per-source, but after 10 consecutive background refresh loop failures the worker stops entirely — all sources stop refreshing until Anteroom restarts. This prevents a broken source from consuming resources.

## Manual Override

Force a refresh regardless of interval or failure state:

```bash
$ aroom pack refresh
```

Or via the API:

```bash
$ curl -X POST http://localhost:8080/api/packs/refresh
```

Manual refresh bypasses the backoff counter and re-enables disabled sources.

## Quarantine on Compliance Failure

If a refreshed pack causes a compliance violation during config rebuild, Anteroom quarantines the offending packs:

- Changed packs are **detached** (removed from the active attachment set)
- Config is rebuilt without the quarantined packs
- The CLI reports: `Quarantined N pack(s) due to compliance failure: <error>`
- The API returns `quarantined` and `quarantine_reason` fields in the refresh response

Quarantined packs remain installed but inactive. To recover: fix the pack content in the source repo, refresh again, and re-attach with `aroom pack attach`.

See [Pack Sources: Quarantine](../pack-sources.md#quarantine) for the full lifecycle.

## Disabling Auto-Refresh

Set `refresh_interval: 0` for manual-only sources:

```yaml
pack_sources:
  - url: https://github.com/acme/stable-packs.git
    branch: main
    refresh_interval: 0  # manual only — never auto-refreshes
```

With interval 0, the source is only refreshed when you explicitly run `aroom pack refresh`.

## Pinning to a Branch

Use a release branch instead of `main` for stability:

```yaml
pack_sources:
  - url: https://github.com/acme/anteroom-packs.git
    branch: v2.x  # only get v2.x releases
    refresh_interval: 60
```

The pack source always tracks the configured branch. Switching branches requires updating the config and running a fresh refresh.

## Mixed Strategies

Different sources can have different refresh strategies:

```yaml
pack_sources:
  # Team standards — auto-refresh frequently
  - url: https://github.com/acme/team-standards.git
    branch: main
    refresh_interval: 15

  # Security packs — auto-refresh daily
  - url: https://github.com/acme/security-packs.git
    branch: main
    refresh_interval: 1440  # 24 hours

  # Experimental — manual only
  - url: https://github.com/acme/experimental.git
    branch: dev
    refresh_interval: 0
```

## Next Steps

- [Pack Sources](../pack-sources.md) — detailed lifecycle and cache behavior
- [CI/CD Integration](ci-cd-integration.md) — validate pack state in pipelines
