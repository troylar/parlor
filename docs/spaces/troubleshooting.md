# Troubleshooting

Common issues and fixes when working with spaces.

## Space Not Auto-Detected

**Symptom:** Starting `aroom chat` from a repo directory doesn't show the space name.

**Causes and fixes:**

1. **Paths not synced** — The directory isn't mapped to the space.

   ```bash
   $ aroom space show my-space
   ```

   If no paths are listed, map the directory:

   ```bash
   $ aroom space map my-space /path/to/your/repo
   ```

2. **Cloned but not synced** — You cloned repos manually (not via `aroom space clone`).

   Use `aroom space map` to register the path, or re-run `aroom space clone` to sync.

3. **Wrong directory** — You're in a directory that isn't a subdirectory of any mapped path. Auto-detection walks up parent directories, but only matches exact mapped paths.

4. **Multiple spaces overlap** — If two spaces map overlapping directories, the deepest (most specific) match wins. Check which spaces have which paths:

   ```bash
   $ aroom space show space-a
   $ aroom space show space-b
   ```

## Space File Validation Errors

**Symptom:** `aroom space create` rejects your file.

**Common errors:**

| Error | Cause | Fix |
|-------|-------|-----|
| `Invalid space name: '-bad'` | Name starts with non-alphanumeric | Start with a letter or digit |
| `Invalid space name: 'has spaces'` | Name contains spaces | Use hyphens or underscores |
| `repos: URL scheme not allowed: file://` | Unsafe URL scheme | Use `https://` or SSH URLs |
| `sources: path traversal not allowed` | Path contains `..` | Use absolute paths |
| `Space file exceeds 256KB limit` | File too large | Reduce content, move data elsewhere |
| `Space file must be a YAML mapping` | File is a YAML list or scalar | Wrap in a dict with `name:` key |

## Clone Failures

**Symptom:** `aroom space clone` fails for some repos.

**Common causes:**

| Error | Cause | Fix |
|-------|-------|-----|
| `Authentication failed` | Invalid credentials | Check SSH keys or HTTPS tokens |
| `Repository not found` | Wrong URL or no access | Verify URL and permissions |
| `git clone timed out (120s)` | Slow network or large repo | Retry; check network |
| `URL scheme not allowed` | `file://` or `ext::` URL | Use `https://` or SSH |

Each repo is cloned independently — one failure doesn't affect others.

## Refresh Not Working

**Symptom:** `/space refresh` says refreshed but nothing changed.

**Possible causes:**

1. **Config changes need restart** — Config overrides from the space file are applied at session start, not on refresh. Exit and restart `aroom chat` for config changes.

2. **YAML syntax error** — If the file has invalid YAML, the watcher silently ignores the change. Validate your YAML:

   ```bash
   $ python -c "import yaml; yaml.safe_load(open('~/.anteroom/spaces/my-space.yaml'))"
   ```

3. **Missing name field** — The watcher rejects files without a `name` field. Check your YAML has `name:` at the top level.

## Space Instructions Not Appearing

**Symptom:** The AI doesn't seem to follow your space instructions.

**Checks:**

1. **Space is active** — Run `/spaces` and check for `(active)` marker.

2. **Instructions field exists** — Verify the YAML has an `instructions` field:

   ```yaml
   instructions: |
     Your instructions here.
   ```

3. **YAML syntax** — Use block scalar syntax (`|`) for multi-line instructions. Incorrect indentation can cause silent truncation.

4. **Refresh after editing** — Run `/space refresh` to reload instructions from disk.

## Config Overrides Not Applied

**Symptom:** Space config values aren't taking effect.

**Check the precedence stack:**

1. **Project config wins** — If a project config sets the same field, it overrides space config.

2. **Team enforcement** — Enforced fields from team config cannot be overridden by any layer, including spaces.

3. **Type mismatch** — The `config` section must be a YAML dict. A non-dict value is silently ignored:

   ```yaml
   # Wrong — this is a string, not a dict
   config: "ai.model: gpt-4o"

   # Correct
   config:
     ai:
       model: gpt-4o
   ```

4. **Session restart needed** — Config overlay is applied at session start. Restart `aroom chat` after changing the config section.

## API Errors

**Symptom:** Web UI space operations fail.

| Status | Detail | Fix |
|--------|--------|-----|
| `404 Space not found` | Invalid space ID | Check the ID (use `GET /api/spaces` to list) |
| `409 Space already exists` | Name collision | Choose a different name |
| `400 Space file not found` | File deleted from disk | Recreate the YAML file |
| `400 Invalid space file` | YAML parse error | Fix YAML syntax |
| `422 Validation error` | Invalid name or path traversal | Fix the request body |

## Database Issues

**Symptom:** Spaces data seems corrupt or inconsistent.

**Reset a specific space:**

```bash
$ aroom space delete my-space
$ aroom space create ~/.anteroom/spaces/my-space.yaml
$ aroom space clone my-space
```

**Check the database directly:**

```bash
$ aroom db show
```

This lists tables and row counts. Check that `spaces`, `space_paths`, and `pack_attachments` have expected data.

## File Watcher Issues

**Symptom:** Hot reload doesn't pick up changes.

1. **Poll interval** — Default is 5 seconds. Changes may take up to 5 seconds to be detected.

2. **File system events** — The watcher uses mtime polling, not filesystem events. It works on all platforms but has a small delay.

3. **Concurrent edits** — If an editor writes to a temp file and renames (atomic write), the mtime change is still detected on the next poll.

4. **Network filesystems** — NFS and other network filesystems may have stale mtime caching. Use `/space refresh` for manual reload.

## Next Steps

- [Concepts](concepts.md) — understand the mental model
- [CLI Commands](commands.md) — full command reference
- [Hot Reload](hot-reload.md) — file watcher details
