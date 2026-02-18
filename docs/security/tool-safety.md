# Tool Safety

A multi-layered safety system prevents accidental damage from AI tool use. Tools are assigned risk tiers, and an approval mode determines which tiers require user confirmation before execution.

## Risk Tiers

Every tool is assigned one of four risk tiers:

| Tier | Level | Description | Examples |
|------|-------|-------------|----------|
| `read` | 0 | Read-only operations | `read_file`, `glob_files`, `grep` |
| `write` | 1 | Modifies files or state | `write_file`, `edit_file` |
| `execute` | 2 | Runs arbitrary code | `bash`, unknown/MCP tools |
| `destructive` | 3 | Irreversible or dangerous | (promoted by pattern detection) |

Unknown tools and MCP tools default to the `execute` tier. Override per-tool tiers in config via `safety.tool_tiers`.

## Approval Modes

The approval mode controls which tiers require user confirmation:

| Mode | Requires Approval For | Use Case |
|------|----------------------|----------|
| `auto` | Nothing | Fully autonomous, no prompts |
| `ask_for_dangerous` | Destructive only | Trust most tools, catch dangerous ones |
| `ask_for_writes` (default) | Write + Execute + Destructive | Balanced safety |
| `ask` | Same as `ask_for_writes` | Alias |

Set the mode in config (`safety.approval_mode`), via environment variable (`AI_CHAT_SAFETY_APPROVAL_MODE`), or CLI flag (`--approval-mode`).

## Permission Scopes

When approving a tool, three scopes are available:

- **Allow Once** — approve this single invocation
- **Allow for Session** — approve this tool for the rest of the session (in-memory)
- **Allow Always** — persist approval to `config.yaml` via the `allowed_tools` list

Tools in the `allowed_tools` config list always skip approval. Tools in the `denied_tools` list are hard-blocked and never execute.

## Destructive Command Detection

The following patterns in bash commands trigger confirmation regardless of approval mode (except `auto`):

- `rm`, `rmdir`
- `git push --force`, `git push -f`
- `git reset --hard`
- `git clean`
- `git checkout .`
- `drop table`, `drop database`
- `truncate`
- `> /dev/`
- `chmod 777`
- `kill -9`

## Path and Command Blocking

Hardcoded blocks that cannot be bypassed:

### Blocked Paths

- `/etc/shadow`
- `/etc/passwd`
- `/etc/sudoers`
- Anything under `/proc/`, `/sys/`, `/dev/` (follows symlinks)

### Blocked Commands

- `rm -rf /`
- `mkfs`
- `dd if=/dev/zero`
- Fork bombs

### Additional Protections

- **Null byte injection**: Rejected in all paths, commands, and glob patterns
- **Path traversal**: Blocked in all file operations
- **Symlink resolution**: `os.path.realpath` is used to resolve symlinks before path checks

## Write Path Safety

`check_write_path()` in `tools/safety.py` inspects the destination path for `write_file` calls and returns a `SafetyVerdict` before any bytes are written. Paths that trigger confirmation include:

- `.env` files and directories
- `.ssh/` and `.gnupg/` directories
- System paths under `/etc/`, `/proc/`, `/sys/`, and `/dev/`
- Any path added to `safety.sensitive_paths` in config

Like `check_bash_command()`, this function is pure — no I/O, no side effects.

## Web UI Approval Flow

When a destructive operation is detected in the Web UI, the agent loop pauses and emits an `approval_required` SSE event containing a unique `approval_id`. The browser renders an inline approve/deny prompt inside the tool call panel.

The user responds by clicking Approve or Deny, which sends:

```
POST /api/approvals/{approval_id}/respond
Content-Type: application/json

{"approved": true, "scope": "once"}
```

The approval ID is regex-validated on receipt. The handler uses an atomic `dict.pop()` on the in-memory `pending_approvals` store (capped at 100 entries) to prevent TOCTOU races — a second response to the same ID is silently ignored.

The waiting agent loop side uses an `asyncio.Event` with disconnect-aware polling: every 1 second, the loop checks `request.is_disconnected()` and exits immediately if the client has left, rather than blocking for the full configurable timeout (default 120 s). If the timeout expires with no response, the operation is blocked (fails closed). If no approval channel exists — for example, when running headless — the operation is also blocked.

## Configuration

Safety gate behavior is controlled by the `safety` section in `config.yaml`. See [Config File](../configuration/config-file.md#safety) for the full field reference.

Quick example — disable safety entirely (not recommended):

```yaml
safety:
  enabled: false
```

Add a custom bash pattern and a sensitive path:

```yaml
safety:
  custom_patterns:
    - "heroku.*--force"
  sensitive_paths:
    - "~/.config/gh"
```

## Sub-agent Safety

The `run_agent` tool spawns isolated child AI sessions for parallel task execution. Multiple layers of protection prevent abuse:

### Concurrency and Resource Limits

`SubagentLimiter` enforces per-request caps:

- **Max concurrent**: 5 sub-agents running at the same time
- **Max total**: 10 sub-agents per root request
- **Semaphore timeout**: 30 seconds waiting for a slot before failing
- **Max nesting depth**: 3 levels (sub-agents can spawn sub-agents, up to 3 deep)
- **Prompt size cap**: 32,000 characters per sub-agent prompt
- **Output truncation**: 4,000 characters max per sub-agent response
- **Max iterations**: 15 per sub-agent turn (parent loop allows 50)
- **Wall-clock timeout**: 120 seconds per sub-agent (clamped 10–600)

All limits are configurable via `safety.subagent` in `config.yaml`. See [Config File Reference](../configuration/config-file.md#safetysubagent).

### Input Validation

- **Model ID**: Regex-validated against `^[a-zA-Z0-9._:/-]{1,128}$` to prevent injection
- **Empty prompts**: Rejected before any processing
- **Depth tracking**: Each child increments a depth counter; at max depth, `run_agent` is removed from the child's available tools

### Isolation

- Each sub-agent gets a deep-copied `AIService` config (no shared mutable state with parent)
- Sub-agents have their own message history — they cannot see the parent conversation
- A defensive system prompt constrains sub-agent behavior (no destructive operations unless explicitly instructed)

### Safety Gate Propagation

The parent's `confirm_callback` is threaded through to child tool executors. Destructive operations inside sub-agents trigger the same approval flow (CLI prompt or Web UI inline approval) as direct tool calls.

### Error Handling

- Generic error messages are returned to the AI — internal limits and implementation details are not exposed
- If no limiter context is provided, the operation is blocked (fails closed)
- Sub-agent execution failures are caught and logged server-side

## MCP Tool Safety

MCP tool arguments are also protected:

- **SSRF protection**: DNS resolution validates that target URLs don't point to private IP addresses
- **Shell metacharacter rejection**: Tool arguments are sanitized to prevent command injection
