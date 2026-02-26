# Tool Safety

A multi-layered safety system prevents accidental damage from AI tool use. Every tool call passes through a safety gate that checks risk tiers, approval requirements, hard-block patterns, and rate limits before execution.

## Safety Gate Flow

```
Tool Call Request
       │
       ▼
  ┌─────────────┐
  │ Denied tool? │──Yes──▶ BLOCKED (hard deny)
  └──────┬──────┘
         │ No
         ▼
  ┌─────────────┐
  │ Read-only?   │──Yes──▶ Only READ tier allowed
  └──────┬──────┘
         │ No
         ▼
  ┌──────────────┐
  │ Hard-block?   │──Yes──▶ BLOCKED (catastrophic command)
  └──────┬───────┘
         │ No
         ▼
  ┌──────────────┐
  │ Rate limit?   │──Yes──▶ BLOCKED or WARNED
  └──────┬───────┘
         │ No
         ▼
  ┌──────────────────┐
  │ Tier >= approval  │──Yes──▶ Approval prompt
  │   mode threshold? │         (user approves/denies)
  └──────┬───────────┘
         │ No
         ▼
    EXECUTE TOOL
```

## Risk Tiers

Every tool is assigned one of four risk tiers:

| Tier | Level | Description | Default Tools |
|------|-------|-------------|---------------|
| `READ` | 0 | Read-only operations | `read_file`, `glob_files`, `grep`, `ask_user`, `introspect`, `create_canvas`, `update_canvas`, `patch_canvas`, `invoke_skill` |
| `WRITE` | 1 | Modifies files or state | `write_file`, `edit_file` |
| `EXECUTE` | 2 | Runs arbitrary code | `bash`, `run_agent` |
| `DESTRUCTIVE` | 3 | Irreversible or dangerous | (promoted by pattern detection) |

Unknown tools and MCP tools default to `EXECUTE`.

### Tier Overrides

Override the default tier for any tool:

```yaml
safety:
  tool_tiers:
    bash: write              # downgrade bash from execute to write
    my_mcp_tool: read        # trust a specific MCP tool
    dangerous_tool: destructive  # upgrade to destructive
```

## Approval Modes

The approval mode controls which tiers require user confirmation before execution:

| Mode | Requires Approval For | Threshold | Use Case |
|------|----------------------|-----------|----------|
| `auto` | Nothing | 99 | Fully autonomous, no prompts |
| `ask_for_dangerous` | Destructive only | 3 | Trust most tools, catch dangerous ones |
| `ask_for_writes` (default) | Write + Execute + Destructive | 1 | Balanced safety |
| `ask` | Write + Execute + Destructive | 1 | Alias for `ask_for_writes` |

Set via config, env var, or CLI flag:

```yaml
safety:
  approval_mode: ask_for_writes
```

```bash
AI_CHAT_SAFETY_APPROVAL_MODE=auto aroom
aroom --approval-mode ask_for_dangerous
```

## Permission Scopes

When approving a tool call, three scopes are available:

| Scope | Behavior |
|---|---|
| **Allow Once** | Approve this single invocation only |
| **Allow for Session** | Approve this tool for the rest of the session (in-memory, lost on restart) |
| **Allow Always** | Persist to `safety.allowed_tools` in `config.yaml` (survives restarts) |

### Tool Allow/Deny Lists

```yaml
safety:
  allowed_tools:         # always skip approval
    - read_file
    - grep
  denied_tools:          # hard-blocked, never execute
    - dangerous_mcp_tool
```

Tools in `denied_tools` are blocked without any approval prompt. Tools in `allowed_tools` skip the approval gate entirely.

## Hard-Block Patterns

Catastrophic commands that are blocked unconditionally by `check_hard_block()`. These cannot be bypassed by any configuration, approval mode, or `allowed_tools` setting:

| # | Pattern | Description |
|---|---------|-------------|
| 1 | `rm -rf` / `rm -fr` | Recursive forced deletion |
| 2 | `mkfs` | Disk formatting |
| 3 | `dd if=/dev/zero` / `dd if=/dev/urandom` | Disk overwrite |
| 4 | Fork bomb syntax `:(){ ...\|...& }; :` | Fork bomb |
| 5 | `fork bomb` (literal) | Fork bomb keyword |
| 6 | `chmod -R 777 /` | Recursive chmod 777 on root |
| 7 | `curl\|sh`, `wget\|bash` | Pipe from network to shell |
| 8 | `curl\|sudo`, `wget\|sudo` | Pipe from network to sudo |
| 9 | `base64\|sh`, `base64\|bash` | Base64 decode piped to shell |
| 10 | `base64\|sudo` | Base64 decode piped to sudo |
| 11 | `python/perl/ruby -c ...os.system/popen/exec` | Scripted shell escape |
| 12 | `python/perl/ruby -c ...subprocess/__import__` | Scripted shell escape |
| 13 | `shred`, `srm` | Secure file erasure |
| 14 | `wipe -` | Secure file erasure (wipe) |
| 15 | `truncate -s 0` / `truncate --size=0` | File zeroing |
| 16 | `sudo rm` | sudo rm |

In interactive mode, the user sees an escalated warning and can choose to override. In auto mode with no approval channel, these are silently blocked.

## Destructive Command Detection

These patterns trigger an approval prompt (except in `auto` mode). Detected by `check_bash_command()` in `tools/safety.py`:

| Pattern | What It Catches |
|---------|-----------------|
| `rm` | File deletion |
| `rmdir` | Directory deletion |
| `git push --force` / `-f` | Force push |
| `git reset --hard` | Hard reset |
| `git clean` | Working tree clean |
| `git checkout .` | Discard changes |
| `drop table` / `drop database` | SQL destruction |
| `truncate` | SQL truncation |
| `> /dev/` | Device redirection |
| `chmod 777` | Insecure permissions |
| `kill -9` | Process killing |

Custom patterns extend this list:

```yaml
safety:
  custom_patterns:
    - "heroku.*--force"
    - "kubectl delete"
```

## Write Path Safety

`check_write_path()` inspects the destination path for `write_file` and `edit_file` calls. Paths that trigger an approval prompt:

### Default Sensitive Paths

| Path | What It Protects |
|------|-----------------|
| `.env` | Environment secrets |
| `.ssh` | SSH keys |
| `.gnupg` | GPG keys |
| `.aws/credentials` | AWS credentials |
| `.config/gcloud` | Google Cloud credentials |

### System Blocked Paths

These are always blocked (not just approval-gated):

| Path | Type |
|------|------|
| `/etc/shadow` | Exact match |
| `/etc/passwd` | Exact match |
| `/etc/sudoers` | Exact match |
| `/proc/*` | Prefix match |
| `/sys/*` | Prefix match |
| `/dev/*` | Prefix match |

### Additional Protections

- **Null byte injection**: Rejected in all paths and commands
- **Path traversal**: Resolved paths checked after normalization
- **Symlink resolution**: `os.path.realpath()` resolves symlinks before path checks
- **Custom sensitive paths**: Extend via `safety.sensitive_paths`:

```yaml
safety:
  sensitive_paths:
    - "~/.config/gh"
    - "/opt/production/config"
```

## Web UI Approval Flow

When a tool call requires approval in the web UI:

1. Agent loop emits an `approval_required` SSE event with a unique `approval_id`
2. Browser renders an inline approve/deny prompt
3. User clicks Approve or Deny, sending `POST /api/approvals/{approval_id}/respond`
4. Atomic `dict.pop()` on the pending approvals store prevents TOCTOU races
5. A second response to the same ID is silently ignored

Safety properties:

- **Disconnect-aware**: Polling checks `request.is_disconnected()` every 1 second
- **Timeout**: Configurable (default 120s); expires → operation blocked
- **Fails closed**: No approval channel (headless mode) → operation blocked
- **Cap**: Max 100 pending approvals in memory

## CLI Approval Flow

In the CLI REPL, approval prompts are interactive `y/N` prompts via Rich. In exec mode (`aroom exec`), there is no interactive channel — all approval-gated operations are blocked (fails closed).

## Tool Rate Limiting

Prevent tool abuse, runaway agents, and denial-of-service:

| Limit | Default | Description |
|-------|---------|-------------|
| `max_calls_per_minute` | 0 (unlimited) | Cap tool calls across all conversations |
| `max_calls_per_conversation` | 0 (unlimited) | Cap accumulated calls within a conversation |
| `max_consecutive_failures` | 5 | Break infinite error loops |
| `action` | `"block"` | `"block"` (hard deny) or `"warn"` (log, allow) |

Rate limits are **shared across parent and sub-agent tool calls** within the same request, providing unified rate limiting.

```yaml
safety:
  tool_rate_limit:
    max_calls_per_minute: 30
    max_calls_per_conversation: 100
    max_consecutive_failures: 3
    action: block
```

## Egress Domain Allowlist

Restrict outbound API calls to a whitelist of approved domains:

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `ai.allowed_domains` | list[string] | `[]` | Egress domain allowlist (empty = no restriction). Domains matched case-insensitively as exact domain matches. Fails closed: unparseable URLs are rejected |
| `ai.block_localhost_api` | boolean | `false` | When true, reject localhost/127.0.0.1/[::1] as the API base_url. Prevents accidental connections to local development services |

Example to restrict API calls to specific production domains:

```yaml
ai:
  base_url: "https://api.example.com/v1"
  allowed_domains:
    - "api.example.com"
    - "api-backup.example.com"
  block_localhost_api: true              # prevent localhost fallback
```

When `allowed_domains` is configured, the system verifies that the `base_url` (and any proxy URLs) target only domains in the allowlist. If validation fails, the API call is rejected before any network activity occurs.

## Configuration Reference

### `safety.*`

| Field | Type | Default | Env Var | Description |
|---|---|---|---|---|
| `enabled` | bool | `true` | `AI_CHAT_SAFETY_ENABLED` | Enable safety system |
| `approval_mode` | string | `"ask_for_writes"` | `AI_CHAT_SAFETY_APPROVAL_MODE` | Approval mode |
| `approval_timeout` | int | `120` | — | Seconds to wait for approval (10–600) |
| `custom_patterns` | list[str] | `[]` | — | Additional destructive command patterns |
| `sensitive_paths` | list[str] | `[]` | — | Additional sensitive write paths |
| `allowed_tools` | list[str] | `[]` | — | Tools that skip approval |
| `denied_tools` | list[str] | `[]` | — | Tools that are hard-blocked |
| `tool_tiers` | dict | `{}` | — | Per-tool tier overrides |
| `read_only` | bool | `false` | `AI_CHAT_READ_ONLY` | Restrict to READ-tier tools only |

### `safety.tool_rate_limit.*`

| Field | Type | Default | Description |
|---|---|---|---|
| `max_calls_per_minute` | int | `0` | Per-minute cap (0 = unlimited) |
| `max_calls_per_conversation` | int | `0` | Per-conversation cap (0 = unlimited) |
| `max_consecutive_failures` | int | `5` | Consecutive failure cap |
| `action` | string | `"block"` | `"block"` or `"warn"` |
