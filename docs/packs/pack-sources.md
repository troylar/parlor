# Pack Sources

Pack sources let you distribute packs via git repositories. Configure a source URL, and Anteroom clones the repo, scans for `pack.yaml` files, and installs all packs found.

## Configuration

Add pack sources to your config file:

```yaml title="~/.anteroom/config.yaml"
pack_sources:
  - url: https://github.com/acme/anteroom-packs.git
    branch: main
    refresh_interval: 30
    auto_attach: true
    priority: 50
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | (required) | Git remote URL |
| `branch` | string | `"main"` | Branch to track |
| `refresh_interval` | int | `30` | Minutes between auto-refresh. `0` = manual only |
| `auto_attach` | bool | `true` | Automatically attach new packs from this source on install. Set to `false` for opt-in attachment |
| `priority` | int | `50` | Conflict resolution priority (1-100). Lower number wins when multiple sources provide the same pack |

Minimum `refresh_interval` is 5 minutes (values below 5 are clamped up).

## URL Scheme Allowlist

Anteroom accepts these URL schemes:

| Scheme | Example | Notes |
|--------|---------|-------|
| `https://` | `https://github.com/org/packs.git` | Recommended |
| `ssh://` | `ssh://git@github.com/org/packs.git` | For key-based auth |
| `git://` | `git://github.com/org/packs.git` | Unauthenticated |
| `http://` | `http://internal.example.com/packs.git` | Warns about MITM risk |
| SSH shorthand | `git@github.com:org/packs.git` | Also accepted |

**Blocked schemes**: `ext::*` and `file://` are rejected to prevent local code execution and path traversal attacks.

## Lifecycle

### Initial Clone

When a pack source is first encountered (or `aroom pack refresh` is run manually):

1. Validate the URL scheme
2. Check the cache for an existing clone
3. If not cached: `git clone --depth 1 -b {branch} {url} {cache_path}`
4. Create `.source_url` and `.source_branch` metadata files in the cache
5. Scan the cloned directory for all `pack.yaml` files (recursive)
6. Parse, validate, and install each pack found

Clone timeout: **60 seconds**.

### Subsequent Pulls

On refresh (automatic or manual):

1. `git pull --ff-only` in the cached directory
2. Compare HEAD before and after — if the commit SHA changed, `changed=True`
3. If changed: re-scan for `pack.yaml` files and install/update packs

Pull timeout: **30 seconds**.

### Background Worker

When `refresh_interval > 0`, a background worker (`PackRefreshWorker`) runs:

- **Poll interval**: checks every 60 seconds which sources are due for refresh
- **Per-source tracking**: each source has its own last-refresh timestamp
- **Failure backoff**: consecutive failures double the wait time (backoff multiplier: 2.0)
- **Auto-disable**: after 10 consecutive failures, the source is disabled until restart
- **Graceful shutdown**: `stop()` cancels the background task

```
Source configured (interval: 30 min)
    │
    ▼
Worker checks every 60s: "Is this source due?"
    │
    ├── Not due → skip
    │
    └── Due → ensure_source() (clone or pull)
            │
            ├── Success + changed → install_from_source() → reset failure count
            ├── Success + no change → reset failure count
            └── Failure → increment failure count, apply backoff
                    │
                    └── 10 consecutive failures → auto-disable
```

## Cache Layout

Cloned sources are cached at:

```
~/.anteroom/cache/sources/
├── a1b2c3d4e5f6/          # SHA-256(url)[:12]
│   ├── .source_url         # Original URL
│   ├── .source_branch      # Tracked branch
│   ├── python-conventions/
│   │   ├── pack.yaml
│   │   └── skills/
│   └── security-review/
│       ├── pack.yaml
│       └── rules/
└── f6e5d4c3b2a1/
    └── ...
```

The cache directory name is deterministic: the first 12 hex characters of the SHA-256 hash of the source URL.

## SSH Setup

For private repositories using SSH keys:

1. Ensure your SSH key is added to the agent: `ssh-add ~/.ssh/id_ed25519`
2. Use SSH URL format:

```yaml
pack_sources:
  - url: git@github.com:acme/private-packs.git
    branch: main
```

Anteroom shells out to the `git` binary, so any SSH configuration in `~/.ssh/config` is respected.

## Private Repos (HTTPS)

For HTTPS access to private repos, configure git credential storage:

```bash
# Cache credentials in memory for 1 hour
$ git config --global credential.helper 'cache --timeout=3600'

# Or use the macOS keychain
$ git config --global credential.helper osxkeychain
```

Then use the HTTPS URL:

```yaml
pack_sources:
  - url: https://github.com/acme/private-packs.git
```

**Security note**: Anteroom sanitizes credentials from error messages — if a clone fails, the URL in the error output has `user:pass@` stripped.

## Manual Refresh

Trigger a refresh of all configured sources:

```bash
$ aroom pack refresh
```

Or via the API:

```bash
$ curl -X POST http://localhost:8080/api/packs/refresh
```

This refreshes all sources regardless of their `refresh_interval` or failure state.

## Multi-Pack Repositories

A single git repository can contain multiple packs. Anteroom scans recursively for `pack.yaml` files:

```
acme-packs/
├── python-conventions/
│   ├── pack.yaml
│   ├── skills/
│   └── rules/
├── security-review/
│   ├── pack.yaml
│   └── rules/
└── frontend-standards/
    ├── pack.yaml
    └── skills/
```

All three packs are discovered and installed from a single source.

## Viewing Source Status

```bash
$ aroom pack sources
```

Shows each configured source with its cache status and current git ref.

Via the API:

```bash
$ curl http://localhost:8080/api/packs/sources
```

Returns: URL, branch, refresh_interval, cached (bool), ref (short SHA or null).

## Next Steps

- [Lock File](lock-file.md) — pin pack versions for reproducibility
- [Automatic Updates Tutorial](tutorials/automatic-updates.md) — configure background refresh
- [Config Reference](config-reference.md) — all pack source config fields
