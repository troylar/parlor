# Team Configuration

Team configuration allows organizations to define shared settings that apply to all team members. A team config file uses the same YAML schema as personal config, with an optional `enforce` list to lock specific settings so they cannot be overridden.

## Why Team Config?

In organizations, you often need to ensure that every developer:

- Connects to the same API endpoint (e.g., a corporate proxy or self-hosted LLM)
- Uses an approved model
- Cannot disable safety approval gates
- Has access to shared MCP tool servers

Without team config, each developer must manually configure these settings and nothing prevents them from changing values. Team config solves this by providing a shared configuration layer with optional enforcement.

## How It Works

Team config sits between defaults and personal config in the [configuration precedence](index.md):

```
defaults → team config → personal config → env vars → CLI flags
```

The team config acts as a **base layer**. Personal config overlays on top (personal values win for non-enforced fields). Environment variables and CLI flags can further override.

The exception is the `enforce` list: any field listed there is **locked** to the team-specified value, regardless of what personal config, environment variables, or CLI flags say.

## Discovery

Anteroom searches for a team config file using this priority order (first match wins):

### 1. CLI Flag

```bash
aroom --team-config /path/to/team.yaml chat
```

If the file does not exist, Anteroom warns and proceeds without team config.

### 2. Environment Variable

```bash
export AI_CHAT_TEAM_CONFIG=/path/to/team.yaml
aroom chat
```

### 3. Personal Config Field

Add `team_config_path` to your personal `~/.anteroom/config.yaml`:

```yaml
team_config_path: /mnt/shared/anteroom/team.yaml

ai:
  api_key: sk-personal-key
```

### 4. Walk-Up from Current Directory

If none of the above are set, Anteroom walks up from the current working directory, checking each directory level for:

1. `.anteroom/team.yaml` (preferred)
2. `.claude/team.yaml` (Claude Code compatible)
3. `anteroom.team.yaml` (flat file alternative)

The `.anteroom` and `.claude` directories are interchangeable — Anteroom treats them identically. If both exist at the same directory level, `.anteroom/team.yaml` takes precedence.

The walk-up stops at the user's home directory (`$HOME`) to prevent traversal into system directories.

This is useful for monorepos where the team config lives at the repository root:

```
my-monorepo/
├── .anteroom/
│   └── team.yaml          ← Found when working anywhere in the repo
├── service-a/
│   └── src/
└── service-b/
    └── src/               ← Working here? Walk-up finds ../../.anteroom/team.yaml
```

## Trust Model

Team config files are verified before loading to prevent injection attacks from modified files on shared filesystems.

**How trust works:**

1. When a team config is encountered for the first time, Anteroom computes its SHA-256 hash.
2. In **interactive mode** (CLI chat, CLI exec with TTY): the user is prompted to confirm trust.
3. In **non-interactive mode** (web UI, piped input): untrusted configs are **silently skipped** (fail-closed).
4. Trust decisions are stored in `~/.anteroom/trusted_folders.json` with the file path and content hash.
5. If the file changes (hash mismatch), the user is prompted again to re-trust.

```
$ aroom chat
Found team config file: /mnt/shared/anteroom/team.yaml
Trust this file? [y/N] y
```

Once trusted, subsequent runs skip the prompt unless the file content changes.

## Setup Wizard Integration

When running `aroom init` with a team config, the setup wizard adapts to avoid redundant prompts:

```bash
aroom init --team-config /path/to/team.yaml
```

If the team config provides AI settings (`base_url`, `api_key`, `model`, `system_prompt`), the wizard skips those prompts and uses the team-provided values. You'll see a message like:

```
Using team-provided AI settings:
  Base URL: https://api.company.com/v1
  API key: sk-****...****
  Model: gpt-4-turbo
```

The wizard still prompts for:
- **User identity** (name, display name) — always required
- **Any `required` keys declared by the team** — if the team config specifies fields that must be set
- Connection test (optional)
- System prompt (only if team doesn't provide one)

This speeds up onboarding for new team members: the team config handles API details, and each person only needs to configure their identity.

## Configuration Merging

When team and personal configs are combined, Anteroom uses a **deep merge** with three distinct strategies depending on the type of value being merged:

### 1. Dicts merge recursively

Nested dictionaries are merged key by key. Personal config keys overlay team config keys; keys only present in team config are preserved.

### 2. Named lists merge by `name`

A **named list** is a list of dictionaries where every item has a `name` field --- for example, `mcp_servers` and `shared_databases`. These are merged by matching items on their `name` field:

- **Same name in both**: the personal item's fields are merged into the team item (personal wins for individual fields)
- **Only in team**: the item is kept unchanged
- **Only in personal**: the item is appended

This is the key feature that allows teams to define shared MCP servers with base settings, while individual users overlay just the fields they need (like API tokens in `env`).

### 3. Plain lists replace wholesale

Lists of simple values (strings, numbers) or lists of dicts without `name` keys are replaced entirely by the personal config value. For example, `denied_tools: ["bash"]` in personal config replaces the team's `denied_tools: ["bash", "rm"]`.

### Example: Scalar and Dict Merging

Team config:
```yaml
ai:
  base_url: https://api.company.com/v1
  model: gpt-4

safety:
  approval_mode: ask_for_writes
  denied_tools:
    - bash
```

Personal config:
```yaml
ai:
  api_key: sk-personal
  model: gpt-4o

safety:
  denied_tools: []
```

Merged result (before enforcement):
```yaml
ai:
  base_url: https://api.company.com/v1   # from team (not in personal)
  api_key: sk-personal                    # from personal
  model: gpt-4o                           # personal overrides team

safety:
  approval_mode: ask_for_writes           # from team (not in personal)
  denied_tools: []                        # plain list — personal replaces team
```

### Example: MCP Server Merging (Named Lists)

This is the most common use case for named-list merging. The team defines which MCP servers to use and how to connect to them, while individual users add their own credentials.

Team config:
```yaml
mcp_servers:
  - name: github
    transport: stdio
    command: uvx mcp-server-github
  - name: slack
    transport: stdio
    command: uvx mcp-server-slack
```

Personal config:
```yaml
mcp_servers:
  - name: github
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"
  - name: my-local-tool
    transport: stdio
    command: /usr/local/bin/my-tool
```

Merged result:
```yaml
mcp_servers:
  # github: command from team, env from personal (merged by name)
  - name: github
    transport: stdio
    command: uvx mcp-server-github
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"

  # slack: unchanged from team (no personal overlay)
  - name: slack
    transport: stdio
    command: uvx mcp-server-slack

  # my-local-tool: personal-only server, appended
  - name: my-local-tool
    transport: stdio
    command: /usr/local/bin/my-tool
```

### Disabling Team-Defined Items

To opt out of a team-defined MCP server or shared database without removing it from the team config, set `enabled: false` in your personal config:

```yaml
# Personal config — disable a noisy team server
mcp_servers:
  - name: noisy-monitoring
    enabled: false
```

The server will be skipped during startup. This only affects your local instance --- other team members still get the server unless they also disable it.

> **Note:** `enabled: false` is evaluated during config parsing, not during merging. The item still appears in the merged YAML but is filtered out before Anteroom connects to it.

## Enforcement

To lock settings so they cannot be overridden, add an `enforce` list to the team config. Each entry is a dot-path to a config field.

```yaml
ai:
  base_url: https://api.company.com/v1
  model: gpt-4

safety:
  approval_mode: ask_for_writes

enforce:
  - ai.base_url
  - ai.model
  - safety.approval_mode
```

### What Enforcement Does

After all merging is complete (team + personal + env vars), Anteroom re-applies the team value for each enforced field. This means:

| Source | Non-enforced field | Enforced field |
|---|---|---|
| Personal config | Overrides team value | **Ignored** |
| Environment variable | Overrides config file | **Ignored** |
| CLI flag (`--approval-mode`) | Overrides env var | **Ignored** (with warning) |
| Web UI PATCH `/api/config` | Applies change | **Rejected** (HTTP 403) |

When a CLI flag targets an enforced field, Anteroom prints a warning:

```
WARNING: --approval-mode ignored; 'safety.approval_mode' is enforced by team config.
```

### Dot-Path Format

Enforce paths use dot notation to reference nested YAML fields:

| Dot-path | YAML field |
|---|---|
| `ai.base_url` | `ai: { base_url: ... }` |
| `ai.model` | `ai: { model: ... }` |
| `safety.approval_mode` | `safety: { approval_mode: ... }` |
| `app.port` | `app: { port: ... }` |

**Validation rules:**

- Only lowercase letters, digits, and underscores (`[a-z0-9_]`)
- Segments separated by dots
- Maximum 4 segments (e.g., `a.b.c.d`)
- Invalid paths are silently ignored with a log warning

### Web UI Enforcement

The web UI config API also respects enforcement:

- `GET /api/config` returns an `enforced_fields` list so the UI can show which settings are locked
- `PATCH /api/config` returns HTTP 403 if you try to change an enforced field (e.g., changing `model` when `ai.model` is enforced)

## Examples

### Lock API Endpoint and Safety Policy

The most common use case: ensure everyone uses the corporate API and cannot disable safety gates.

```yaml
ai:
  base_url: https://api.company.com/v1
  model: gpt-4-turbo

safety:
  approval_mode: ask_for_writes

enforce:
  - ai.base_url
  - ai.model
  - safety.approval_mode
```

Users can still set their own `api_key`, `system_prompt`, and other non-enforced settings.

### Share MCP Servers

Provide team-wide access to shared MCP tool servers. Individual users can overlay their own credentials without replacing the server definitions:

**Team config:**
```yaml
mcp_servers:
  - name: postgres
    transport: stdio
    command: node /opt/mcp-servers/postgres.js
    args:
      - --db-host=db.company.com
      - --db-port=5432
  - name: slack
    transport: stdio
    command: /opt/mcp-servers/slack.sh
```

**Personal config** (overlay just the tokens):
```yaml
mcp_servers:
  - name: slack
    env:
      SLACK_BOT_TOKEN: "${SLACK_BOT_TOKEN}"
```

The result: both servers are available, with the Slack server getting the user's token from their environment. To lock the server list so users cannot remove servers, enforce it:

```yaml
enforce:
  - mcp_servers
```

### Set Defaults Without Enforcing

Team config without an `enforce` list provides sensible defaults that users can override:

```yaml
ai:
  base_url: https://api.company.com/v1
  model: gpt-4

safety:
  approval_mode: ask_for_writes

# No enforce list — all values are overridable
```

### Multi-Team Setup

Different teams can use different configs via environment variables:

```bash
# Team A
export AI_CHAT_TEAM_CONFIG=/etc/anteroom/team-a.yaml
aroom chat

# Team B
export AI_CHAT_TEAM_CONFIG=/etc/anteroom/team-b.yaml
aroom chat
```

### CI/CD Environments

In CI, use the environment variable and note that trust prompting is skipped in non-interactive mode:

```bash
export AI_CHAT_TEAM_CONFIG=/etc/anteroom/ci-config.yaml
# Must pre-trust the file, or the CI config will be silently skipped.
# Trust is stored per-user in ~/.anteroom/trusted_folders.json
aroom exec "Run the test suite"
```

## Recommended File Locations

| Scenario | Location | Discovery Method |
|---|---|---|
| Git monorepo | `.anteroom/team.yaml` in repo root | Walk-up (automatic) |
| Shared filesystem (NFS) | `/mnt/team-config/anteroom.yaml` | Env var or personal config field |
| System directory (Linux) | `/etc/anteroom/team.yaml` | Env var |
| Per-team on shared host | `/etc/anteroom/team-{name}.yaml` | Env var per team |

## Troubleshooting

### Team config is not loading

1. **Check discovery**: Run with `--debug` to see team config discovery logs:
   ```bash
   aroom --debug chat
   ```
   Look for lines like `Team config path from --team-config does not exist` or `Skipping untrusted team config`.

2. **Check trust**: If the file exists but is not trusted, you'll see `Skipping untrusted team config (non-interactive)` in debug output. Trust the file interactively first:
   ```bash
   aroom chat  # Will prompt to trust
   ```

3. **Check walk-up**: Walk-up stops at `$HOME`. If your team config is above your home directory, use an explicit path instead.

### Enforced field is not working

1. **Check the dot-path**: Paths must be lowercase with max 4 segments. `AI.base_url` (uppercase) is invalid. Run with `--debug` to see `Ignoring invalid enforce dot-path` warnings.

2. **Check the field exists in team config**: If you enforce `ai.model` but don't set `ai.model` in the team config, the enforcement is skipped with a warning.

3. **Check the web UI**: The `GET /api/config` endpoint returns `enforced_fields` --- verify the field appears there.
