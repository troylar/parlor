# Hot Reload

Anteroom monitors the active space file for changes and can reload configuration without restarting.

## How It Works

The `SpaceFileWatcher` uses mtime polling to detect when a space YAML file changes on disk:

```
Space File on Disk
    │
    ├── mtime changed?
    │       │
    │   No ─┘ (sleep interval, check again)
    │
    │   Yes ──→ Read file
    │              │
    │         Valid YAML? ──→ Has 'name'? ──→ Fire callback
    │              │              │
    │           No ─┘          No ─┘
    │         (ignore,        (ignore,
    │          keep old)       keep old)
    │
    └── repeat
```

## Automatic Watching

When a space is active during a chat session, Anteroom automatically starts a file watcher. Changes to the space file are picked up within the polling interval.

### What Gets Reloaded

- **Instructions** — updated and re-injected into the system prompt
- **File hash** — updated in the database

### What Doesn't Get Reloaded

- **Config overrides** — applied only at session start
- **Repo list** — requires explicit `aroom space clone`
- **Pack changes** — requires explicit `aroom space clone` or pack install
- **Source links** — managed separately via the API or REPL

## Manual Refresh

Trigger an immediate reload without waiting for the poll interval:

**CLI REPL:**

```
> /space refresh
Refreshed: backend-api
```

**CLI command:**

```bash
$ aroom space refresh backend-api
Refreshed: backend-api (hash updated)
```

**Web UI API:**

```bash
$ curl -X POST http://localhost:8080/api/spaces/{id}/refresh
```

Manual refresh re-reads the file, validates it, and updates the database hash. In the REPL, it also re-injects instructions.

## Poll Interval

The default polling interval is **5 seconds**.

### Configuration

The interval is set via team config with `space_refresh_interval`:

```yaml title="team config"
space_refresh_interval: 10  # seconds
```

### Constraints

| Constraint | Value | Reason |
|------------|-------|--------|
| Minimum | 1 second | Prevents excessive disk I/O |
| Default | 5 seconds | Balance between responsiveness and resource usage |
| Maximum | None (no hard cap) | Team config controls |

The minimum is enforced programmatically — values below 1 second are clamped to 1.

## Error Handling

The file watcher is designed to be resilient:

### Invalid YAML

If you save a space file with syntax errors, the watcher logs a warning and keeps the previous valid config:

```
WARNING Space file /path/to/space.yaml has invalid YAML — ignoring change
```

### Missing Name

If the YAML is valid but the `name` field is missing or empty:

```
WARNING Space file /path/to/space.yaml missing name — ignoring change
```

### File Deleted

If the file is removed while being watched, the watcher silently skips the check. It continues polling in case the file is recreated.

### File System Races

The watcher handles TOCTOU (time-of-check-time-of-use) race conditions by catching `OSError` at each file operation. If a file disappears between `stat()` and `read_text()`, the error is caught and the check is skipped.

### Callback Failures

If the change callback raises an exception, it's caught and logged. The watcher continues running:

```
ERROR Space file change callback failed for /path/to/space.yaml
```

## Lifecycle

| Event | Watcher Behavior |
|-------|-----------------|
| Space activated | Watcher starts (background asyncio task) |
| Space deactivated | Watcher stops (task cancelled) |
| Session ends | Watcher stops |
| File not found at start | Watcher starts with no initial mtime |

The watcher is idempotent — calling `start()` multiple times has no effect. Calling `stop()` when not running is a no-op.

## Comparison with Config Watcher

| Feature | Space Watcher | Config Watcher |
|---------|--------------|----------------|
| Target | Single space YAML file | Main config file |
| Interval | 5s (team-configurable) | 5s |
| Invalid file handling | Ignore, keep previous | Ignore, keep previous |
| Change callback | Async or sync | Sync |
| Scope | Per-session | Global |

Both use the same mtime-polling approach but operate independently.

## Next Steps

- [CLI Commands](commands.md) — `/space refresh` command
- [Config Overlay](config-overlay.md) — what config changes mean
- [Troubleshooting](troubleshooting.md) — reload issues
