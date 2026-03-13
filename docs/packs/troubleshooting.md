# Troubleshooting

Common problems and their solutions when working with packs and artifacts.

> **Tip:** For a comprehensive, AI-guided diagnostic, use `/pack-doctor` in the REPL. It runs health checks, interprets issues, checks source connectivity, validates lock files, and provides specific remediation steps.

---

### Pack install fails with "Artifact file not found"

**Symptom**: `aroom pack install` reports a missing file.

**Cause**: The manifest references an artifact, but the file doesn't exist at the expected path. Either the `file` field is wrong, or the file is missing from the type directory.

**Fix**: Check the [file resolution rules](manifest-format.md#file-resolution). If no `file` is specified, the artifact must exist at `{type_dir}/{name}.{yaml|md|txt|json}`. Verify the file exists and the extension matches one of the probed types.

---

### Pack source clone fails with "URL scheme not allowed"

**Symptom**: `aroom pack refresh` rejects the source URL.

**Cause**: The URL uses a blocked scheme. `ext::` and `file://` are rejected for security reasons.

**Fix**: Use `https://`, `ssh://`, `git://`, or SSH shorthand (`git@host:path`). See [Pack Sources: URL Scheme Allowlist](pack-sources.md#url-scheme-allowlist).

---

### Pack source clone hangs or times out

**Symptom**: `aroom pack refresh` takes 60+ seconds and fails.

**Cause**: Network connectivity issue, SSH key not loaded, or the git remote is unreachable.

**Fix**:
- Verify the URL is reachable: `git ls-remote {url}`
- For SSH: ensure your key is loaded: `ssh-add -l`
- Check firewall/proxy settings
- Clone timeout is 60 seconds; pull timeout is 30 seconds

---

### Skills from a pack don't appear in tab completion

**Symptom**: After installing a pack with skills, `/skill-name` doesn't tab-complete.

**Cause**: The skill registry hasn't reloaded, or the skill YAML is invalid.

**Fix**:
- Restart the REPL (`aroom chat`) to reload skills
- Check the skill YAML has valid `name`, `description`, and `prompt` fields
- Verify the artifact is registered: `aroom artifact list --type skill`

---

### Config overlay doesn't take effect

**Symptom**: A config_overlay artifact is installed but the setting isn't applied.

**Cause**: A higher-precedence config source overrides the overlay. Config precedence: defaults < team < packs < personal < space < project < env vars < CLI flags. Team-enforced fields always win.

**Fix**:
- Check if the field is team-enforced: look for `enforce:` in the team config
- Check if an environment variable overrides it (e.g., `AI_CHAT_SAFETY_APPROVAL_MODE`)
- Check if a CLI flag overrides it (e.g., `--approval-mode`)
- Run `aroom artifact check` to detect config conflicts

---

### Health check reports "lock_drift" but packs seem correct

**Symptom**: `aroom artifact check --project` reports lock drift errors.

**Cause**: Packs were updated or the background worker installed new versions, but the lock file wasn't regenerated.

**Fix**: Regenerate the lock file by reinstalling or updating the pack with `--project`, then commit the updated `.anteroom/anteroom.lock.yaml`.

---

### Background refresh stops working after repeated failures

**Symptom**: Pack sources stop updating. `aroom pack sources` shows the source is cached but stale.

**Cause**: After 10 consecutive failures, the background refresh worker auto-disables entirely (all sources stop refreshing, not just the failing one).

**Fix**:
- Run `aroom pack refresh` manually to force a refresh and reset the failure counter
- Check the underlying issue (network, auth, git remote)
- Restart Anteroom to re-enable the source

---

### "Pack is already installed" error on install

**Symptom**: `aroom pack install` says the pack is already installed.

**Cause**: A pack with the same namespace/name is already in the database.

**Fix**: Use `aroom pack update` instead:

```bash
$ aroom pack update ./my-pack/ --project
```

Or remove first, then reinstall:

```bash
$ aroom pack remove namespace/name
$ aroom pack install ./my-pack/
```

---

### Path traversal error in manifest

**Symptom**: `aroom pack install` reports "Path traversal detected".

**Cause**: An artifact's `file` field references a path outside the pack directory (e.g., `../../../etc/passwd`).

**Fix**: All `file` paths must be relative to the pack directory and must not escape it. Use paths like `skills/commit.yaml`, not `../shared/commit.yaml`.

---

### Artifact FQN is invalid

**Symptom**: `aroom artifact show @My-Team/skill/commit` returns a format error.

**Cause**: FQN format requires lowercase. The regex is: `^@[a-z0-9_-]+/[a-z_]+/[a-z0-9_][a-z0-9_.-]*$`

**Fix**: Use lowercase for all FQN components. `@my-team/skill/commit` is valid; `@My-Team/skill/commit` is not. See [FQN format rules](concepts.md#fully-qualified-names-fqn).

---

### Pack quarantined after refresh

**Symptom**: A pack that was previously active is suddenly detached/inactive after a background or manual refresh.

**Cause**: The refreshed pack introduced a compliance violation (e.g., a config overlay that conflicts with team-enforced fields). Anteroom detaches offending packs to restore a valid configuration.

**Diagnosis**:
- Run `aroom pack refresh` — if quarantine occurs, the CLI prints: `Quarantined N pack(s) due to compliance failure: <error>`
- Via API: `POST /api/packs/refresh` returns `quarantined` (list of pack IDs) and `quarantine_reason` (generic message; full details in server logs)
- Check server logs for the specific compliance rule that was violated

**Fix**:
1. Identify the quarantined pack: compare `aroom pack list` with your expected active packs
2. Fix the pack content in the source repository (e.g., correct the config overlay that violates compliance rules)
3. Run `aroom pack refresh` to pull the corrected version
4. Re-attach the pack: `aroom pack attach namespace/name`

See [Pack Sources: Quarantine](pack-sources.md#quarantine) for the full quarantine lifecycle.

---

### Too many artifacts warning

**Symptom**: Log warning about exceeding artifact cap.

**Cause**: More than 500 artifacts loaded in the registry. This is a performance guard — large artifact counts increase system prompt size and token usage.

**Fix**: Review installed packs and remove unnecessary ones. Use `aroom artifact check` to see the bloat report with top artifacts by size. Consider splitting large context artifacts or using RAG instead.
