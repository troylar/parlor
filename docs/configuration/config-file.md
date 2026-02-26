# Config File

The config file lives at `~/.anteroom/config.yaml`.

## Full Reference

```yaml
ai:
  base_url: "https://your-ai-endpoint/v1"    # Required
  api_key: "your-api-key"                     # Required (or use api_key_command)
  api_key_command: "vault read -field=key"    # Alternative: run a command to get the key
  model: "gpt-4"                              # Default model
  system_prompt: "You are a helpful assistant."
  verify_ssl: true                            # SSL cert verification (default: true)
  request_timeout: 120                        # API request timeout in seconds (default: 120, clamped 10–600)
  connect_timeout: 5                           # TCP connect timeout in seconds (default: 5, clamped 1–30)
  write_timeout: 30                            # Time to send request body in seconds (default: 30, clamped 5–120)
  pool_timeout: 10                             # Wait for free connection from pool in seconds (default: 10, clamped 1–60)
  first_token_timeout: 30                      # Max wait for first token after connect (default: 30, clamped 5–120)
  chunk_stall_timeout: 30                      # Max silence between chunks mid-stream in seconds (default: 30, clamped 10–600)
  retry_max_attempts: 3                        # Retries on transient errors; 0 disables (default: 3, clamped 0–10)
  retry_backoff_base: 1.0                      # Exponential backoff base in seconds (default: 1.0, clamped 0.1–30.0)
  temperature: null                             # Model temperature for randomness (0.0–2.0, null = provider default)
  top_p: null                                   # Nucleus sampling parameter (0.0–1.0, null = provider default)
  seed: null                                    # Random seed for deterministic output (null = provider default)

app:
  host: "127.0.0.1"      # Bind address
  port: 8080              # Server port
  data_dir: "~/.anteroom"   # Where DB + attachments live
  tls: false              # Set true for HTTPS with self-signed cert

session:
  store: "memory"                      # "memory" or "sqlite" (default: memory)
  max_concurrent_sessions: 0           # 0 = unlimited (default: 0)
  idle_timeout: 1800                   # Seconds (default: 1800 = 30 minutes)
  absolute_timeout: 43200              # Seconds (default: 43200 = 12 hours)
  allowed_ips: []                      # CIDR or exact addresses; empty = allow all (default: empty)
  log_session_events: false            # Log session lifecycle events (default: false)

cli:
  builtin_tools: true              # Enable built-in tools (default: true)
  max_tool_iterations: 50          # Max tool calls per response (default: 50)
  context_warn_tokens: 80000       # Token count at which context warning is shown (default: 80000)
  context_auto_compact_tokens: 100000  # Token count at which auto-compaction triggers (default: 100000)
  retry_delay: 5.0                 # Seconds between CLI auto-retry countdown ticks (default: 5.0, clamped 1–60)
  max_retries: 3                   # Max CLI auto-retry attempts for retryable errors (default: 3, clamped 0–10)
  esc_hint_delay: 3.0              # Seconds before showing "esc to cancel" hint (default: 3.0, clamped 0+)
  stall_display_threshold: 5.0     # Seconds of chunk silence before showing "stalled" indicator (default: 5.0, clamped 1+)
  stall_warning_threshold: 15.0    # Seconds before showing full stall warning (default: 15.0, clamped 1+)
  tool_output_max_chars: 2000      # Max chars per tool result before truncation (default: 2000, clamped 100+)
  file_reference_max_chars: 100000 # Max chars from @file references (default: 100000, clamped 1000+)
  model_context_window: 128000     # Model context window size for usage bar (default: 128000, clamped 1000+)
  usage:
    week_days: 7                   # Days for "this week" rolling window (default: 7)
    month_days: 30                 # Days for "this month" rolling window (default: 30)
    model_costs: {}                # Per-model costs: {model: {input: rate, output: rate}} (default: empty)
    budgets:
      enabled: false               # Enable token budget enforcement (default: false)
      max_tokens_per_request: 0    # Single request limit; 0 = unlimited (default: 0)
      max_tokens_per_conversation: 0  # Conversation limit; 0 = unlimited (default: 0)
      max_tokens_per_day: 0        # Daily limit; 0 = unlimited (default: 0)
      warn_threshold_percent: 80   # Warn at this % of limit (default: 80)
      action_on_exceed: block      # "block" to deny or "warn" to allow (default: "block")

identity:
  user_id: "auto-generated-uuid"
  display_name: "Your Name"
  public_key: "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"
  private_key: "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"

shared_databases:
  - name: "team-shared"
    path: "~/shared/team.db"

mcp_servers:
  - name: "my-tools"
    transport: "stdio"
    command: "npx"
    args: ["-y", "@my-org/mcp-tools"]
    env:
      API_KEY: "${MY_API_KEY}"
    tools_include: []                  # fnmatch allowlist (empty = include all)
    tools_exclude: []                  # fnmatch blocklist

  - name: "remote-tools"
    transport: "sse"
    url: "https://mcp-server.example.com/sse"

mcp_tool_warning_threshold: 40        # Warn when total MCP tools exceed this (0 = disabled)

safety:
  enabled: true
  approval_mode: "ask_for_writes"    # auto | ask_for_dangerous | ask_for_writes | ask
  approval_timeout: 120
  read_only: false                   # If true, only READ-tier tools are available
  bash:
    enabled: true
  write_file:
    enabled: true
  allowed_tools: []                  # Tools that skip approval (always auto-approved)
  denied_tools: []                   # Tools that are hard-blocked (never execute)
  tool_tiers: {}                     # Per-tool tier overrides, e.g. {my_mcp_tool: "read"}
  custom_patterns: []
  sensitive_paths: []
  subagent:
    max_concurrent: 5
    max_total: 10
    max_depth: 3
    max_iterations: 15
    timeout: 120
    max_output_chars: 4000
    max_prompt_chars: 32000
  tool_rate_limit:
    max_calls_per_minute: 0               # Max tool calls per minute (0 = unlimited)
    max_calls_per_conversation: 0         # Max tool calls per conversation (0 = unlimited)
    max_consecutive_failures: 5           # Max consecutive failed tool calls
    action: "block"                       # "block" to deny, "warn" to allow + log

embeddings:
  enabled: true
  model: "text-embedding-3-small"
  dimensions: 1536
  base_url: ""
  api_key: ""
  api_key_command: ""

# Project/team config only — shared references
references:
  instructions:
    - "team/instructions.md"
    - "team/coding-standards.md"
  rules:
    - "team/rules/no-eval.md"
  skills:
    - "team/skills/deploy.md"

# Project/team config only — required keys
required:
  - path: "ai.api_key"
    description: "Your API key"
  - path: "custom.db_password"
    description: "Database password"
```

## Sections

### ai

| Field | Type | Default | Description |
|---|---|---|---|
| `base_url` | string | --- | OpenAI-compatible API endpoint (required) |
| `api_key` | string | --- | API key (required unless using `api_key_command`) |
| `api_key_command` | string | --- | External command to obtain API key dynamically |
| `model` | string | `gpt-4` | Default model name |
| `system_prompt` | string | `You are a helpful assistant.` | System prompt for all conversations |
| `verify_ssl` | boolean | `true` | Verify SSL certificates when connecting to the API |
| `request_timeout` | integer | `120` | Overall stream timeout in seconds (clamped 10–600); env: `AI_CHAT_REQUEST_TIMEOUT` |
| `connect_timeout` | integer | `5` | TCP connect timeout in seconds (clamped 1–30); env: `AI_CHAT_CONNECT_TIMEOUT` |
| `write_timeout` | integer | `30` | Time to send request body in seconds (clamped 5–120); env: `AI_CHAT_WRITE_TIMEOUT` |
| `pool_timeout` | integer | `10` | Wait for free connection from pool in seconds (clamped 1–60); env: `AI_CHAT_POOL_TIMEOUT` |
| `first_token_timeout` | integer | `30` | Max seconds to wait for first token after connect (clamped 5–120); env: `AI_CHAT_FIRST_TOKEN_TIMEOUT` |
| `chunk_stall_timeout` | integer | `30` | Max silence between chunks mid-stream in seconds (clamped 10–600); env: `AI_CHAT_CHUNK_STALL_TIMEOUT` |
| `retry_max_attempts` | integer | `3` | Retries on transient errors (timeout, connection); 0 disables (clamped 0–10); env: `AI_CHAT_RETRY_MAX_ATTEMPTS` |
| `retry_backoff_base` | float | `1.0` | Exponential backoff base delay in seconds (clamped 0.1–30.0); env: `AI_CHAT_RETRY_BACKOFF_BASE` |
| `temperature` | float or null | `null` | Model temperature for response randomness (0.0–2.0; null = provider default); env: `AI_CHAT_TEMPERATURE` |
| `top_p` | float or null | `null` | Nucleus sampling parameter (0.0–1.0; null = provider default); env: `AI_CHAT_TOP_P` |
| `seed` | integer or null | `null` | Random seed for deterministic output (null = provider default); env: `AI_CHAT_SEED` |

### app

| Field | Type | Default | Description |
|---|---|---|---|
| `host` | string | `127.0.0.1` | Bind address for the web server |
| `port` | integer | `8080` | Port for the web server; env: `AI_CHAT_PORT` |
| `data_dir` | string | `~/.anteroom` | Directory for database, attachments, config |
| `tls` | boolean | `false` | Enable HTTPS with self-signed certificate |

### session

| Field | Type | Default | Description |
|---|---|---|---|
| `store` | string | `memory` | Session storage backend: `memory` (volatile, in-process) or `sqlite` (persistent); env: `AI_CHAT_SESSION_STORE` |
| `max_concurrent_sessions` | integer | `0` | Max concurrent sessions per user; `0` = unlimited. Enforced at middleware. When exceeded, returns 429 Too Many Sessions; env: `AI_CHAT_SESSION_MAX_CONCURRENT` |
| `idle_timeout` | integer | `1800` | Session idle timeout in seconds (default: 1800 = 30 minutes). Automatically cleaned up on next request; env: `AI_CHAT_SESSION_IDLE_TIMEOUT` |
| `absolute_timeout` | integer | `43200` | Absolute session lifetime in seconds (default: 43200 = 12 hours). After this period, session is invalidated regardless of activity; env: `AI_CHAT_SESSION_ABSOLUTE_TIMEOUT` |
| `allowed_ips` | list[string] | `[]` | IP allowlist for access control. Supports exact addresses (`192.168.1.5`) and CIDR ranges (`10.0.0.0/8`). Both IPv4 and IPv6. Empty list allows all IPs; env: `AI_CHAT_SESSION_ALLOWED_IPS` (comma-separated) |
| `log_session_events` | boolean | `false` | Log session lifecycle events (create, touch, delete) to audit log; env: `AI_CHAT_SESSION_LOG_EVENTS` |

### cli

| Field | Type | Default | Description |
|---|---|---|---|
| `builtin_tools` | boolean | `true` | Enable the 6 built-in tools |
| `max_tool_iterations` | integer | `50` | Max agentic loop iterations per turn |
| `context_warn_tokens` | integer | `80000` | Token count at which a context warning is shown in the CLI |
| `context_auto_compact_tokens` | integer | `100000` | Token count at which context is automatically compacted |
| `retry_delay` | float | `5.0` | Seconds between CLI auto-retry countdown ticks (clamped 1–60) |
| `max_retries` | integer | `3` | Max CLI auto-retry attempts for retryable errors (clamped 0–10) |
| `esc_hint_delay` | float | `3.0` | Seconds before showing "esc to cancel" hint |
| `stall_display_threshold` | float | `5.0` | Seconds of chunk silence before showing "stalled" indicator (clamped 1+) |
| `stall_warning_threshold` | float | `15.0` | Seconds before showing full stall warning (clamped 1+) |
| `tool_output_max_chars` | integer | `2000` | Max chars per tool result before truncation (clamped 100+) |
| `file_reference_max_chars` | integer | `100000` | Max chars from @file references (clamped 1000+) |
| `model_context_window` | integer | `128000` | Model context window size for usage bar (clamped 1000+) |

### identity

User identity for message attribution in shared databases. Auto-generated on first run via `aroom init` or on startup if missing.

| Field | Type | Description |
|---|---|---|
| `user_id` | string | UUID identifying this user (auto-generated, immutable) |
| `display_name` | string | Human-readable name shown on messages |
| `public_key` | string | Ed25519 public key in PEM format (auto-generated) |
| `private_key` | string | Ed25519 private key in PEM format (auto-generated) |

!!! warning "Back up your identity"
    The `identity` section contains a private key that proves ownership of your user ID. If you lose it, your messages in shared databases become unverifiable. Back up your `config.yaml` to preserve your identity across reinstalls.

### shared_databases

A list of additional SQLite databases. See [Shared Databases](../web-ui/shared-databases.md).

| Field | Type | Description |
|---|---|---|
| `name` | string | Display name (alphanumeric, hyphens, underscores) |
| `path` | string | Path to `.db`/`.sqlite`/`.sqlite3` file |

### mcp_servers

A list of MCP tool servers. See [MCP Servers](mcp-servers.md).

Per-server configuration:

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | --- | Display name for the server |
| `transport` | string | --- | Transport type: `"stdio"` (local process) or `"sse"` (remote HTTP) |
| `command` | string | --- | (stdio only) Command to launch the MCP server process |
| `args` | list | `[]` | (stdio only) Command arguments |
| `url` | string | --- | (SSE only) Server-Sent Events endpoint URL |
| `env` | map | `{}` | (stdio only) Environment variables for the process; supports `${VAR}` expansion |
| `timeout` | float | `30.0` | Connection timeout in seconds |
| `tools_include` | list | `[]` | Fnmatch patterns for tools to include (empty = include all) |
| `tools_exclude` | list | `[]` | Fnmatch patterns for tools to exclude |

### mcp_tool_warning_threshold

| Field | Type | Default | Description |
|---|---|---|---|
| `mcp_tool_warning_threshold` | integer | `40` | Emit a warning when total MCP tools across all servers exceed this threshold (0 = disabled) |

### safety

Controls the tool safety approval gate. Tools are assigned risk tiers (read, write, execute, destructive) and the approval mode determines which tiers require user confirmation before execution.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | boolean | `true` | Enable the safety gate globally |
| `approval_mode` | string | `ask_for_writes` | Which tiers require approval: `auto` (none), `ask_for_dangerous` (destructive only), `ask_for_writes` (write+execute+destructive), `ask` (same as ask_for_writes) |
| `approval_timeout` | integer | `120` | Seconds to wait for approval before blocking the operation (clamped 10–600) |
| `read_only` | boolean | `false` | If true, only READ-tier tools are available; all WRITE, EXECUTE, and DESTRUCTIVE tools are blocked; env: `AI_CHAT_READ_ONLY` |
| `bash.enabled` | boolean | `true` | Enable `bash` tool (set false to hard-block) |
| `write_file.enabled` | boolean | `true` | Enable `write_file` tool (set false to hard-block) |
| `allowed_tools` | list | `[]` | Tools that always skip approval regardless of tier |
| `denied_tools` | list | `[]` | Tools that are hard-blocked and never execute |
| `tool_tiers` | dict | `{}` | Per-tool tier overrides, e.g. `{my_mcp_tool: "read"}`. Valid tiers: `read`, `write`, `execute`, `destructive` |
| `custom_patterns` | list | `[]` | Additional regex patterns that trigger confirmation for bash commands |
| `sensitive_paths` | list | `[]` | Additional path prefixes that trigger confirmation for file writes |
| `subagent.*` | object | see below | Sub-agent execution limits (nested under `safety.subagent`) |

#### safety.subagent

Controls limits for the `run_agent` sub-agent tool. All fields are optional — sensible defaults apply when omitted.

| Field | Type | Default | Description |
|---|---|---|---|
| `max_concurrent` | integer | `5` | Maximum sub-agents running simultaneously |
| `max_total` | integer | `10` | Maximum sub-agents spawned per root request |
| `max_depth` | integer | `3` | Maximum nesting depth (sub-agents spawning sub-agents) |
| `max_iterations` | integer | `15` | Maximum agentic loop iterations per sub-agent |
| `timeout` | integer | `120` | Wall-clock timeout in seconds per sub-agent (clamped 10–600) |
| `max_output_chars` | integer | `4000` | Maximum output characters returned to parent |
| `max_prompt_chars` | integer | `32000` | Maximum prompt characters accepted |

#### safety.tool_rate_limit

Controls tool call rate limiting to prevent tool abuse and excessive iterations. All limits default to 0 (unlimited). Set a positive value to enable.

| Field | Type | Default | Description |
|---|---|---|---|
| `max_calls_per_minute` | integer | `0` | Maximum tool calls allowed per minute (0 = unlimited); applies globally across all conversations |
| `max_calls_per_conversation` | integer | `0` | Maximum tool calls allowed per conversation thread (0 = unlimited). Counts accumulated calls from root and all sub-agents |
| `max_consecutive_failures` | integer | `5` | Maximum consecutive tool failures before rate limit triggers. Useful to break infinite error loops |
| `action` | string | `block` | Action when rate limit is exceeded: `block` (deny request with error) or `warn` (log warning, allow execution) |

Example configuration to prevent runaway agents:

```yaml
safety:
  tool_rate_limit:
    max_calls_per_minute: 30              # max 30 tool calls per minute
    max_calls_per_conversation: 100       # max 100 total calls per conversation
    max_consecutive_failures: 3           # block after 3 consecutive failures
    action: "block"                       # hard block when limits exceeded
```

See [Tool Safety](../security/tool-safety.md) for the full list of built-in patterns and the approval flow.

### embeddings

Controls vector embeddings for semantic search. Requires an OpenAI-compatible embedding endpoint and the sqlite-vec extension.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | boolean | `true` | Enable background embedding of messages |
| `model` | string | `text-embedding-3-small` | Embedding model name |
| `dimensions` | integer | `1536` | Vector dimensions (must match the model) |
| `base_url` | string | `""` | Embedding API endpoint (falls back to `ai.base_url` if empty) |
| `api_key` | string | `""` | API key for the embedding endpoint |
| `api_key_command` | string | `""` | External command to obtain the embedding API key dynamically |

### usage

Controls token usage tracking and cost estimation for the CLI. All fields are optional with sensible defaults.

| Field | Type | Default | Description |
|---|---|---|---|
| `week_days` | integer | `7` | Number of days to include in "this week" rolling window |
| `month_days` | integer | `30` | Number of days to include in "this month" rolling window |
| `model_costs` | dict | `{}` | Model pricing for cost estimation, keyed by model name with `input` and `output` rates (per-million-token) |

Example with cost estimation:

```yaml
cli:
  usage:
    week_days: 7
    month_days: 30
    model_costs:
      gpt-4o: { input: 0.003, output: 0.006 }
      gpt-4-turbo: { input: 0.01, output: 0.03 }
      claude-3-sonnet: { input: 0.003, output: 0.015 }
```

The `/usage` command displays token counts and estimated costs across multiple time periods. Without `model_costs` configured, only token counts are shown.

#### usage.budgets

Nested under `usage` — controls token budget enforcement for denial-of-wallet prevention.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | boolean | `false` | Enable token budget enforcement |
| `max_tokens_per_request` | integer | `0` | Maximum tokens for a single request (0 = unlimited) |
| `max_tokens_per_conversation` | integer | `0` | Maximum tokens accumulated in one conversation (0 = unlimited) |
| `max_tokens_per_day` | integer | `0` | Maximum tokens consumed in a calendar day (0 = unlimited) |
| `warn_threshold_percent` | integer | `80` | Warn when usage exceeds this percentage of any limit (0-100) |
| `action_on_exceed` | string | `block` | What to do when a limit is exceeded: `block` (deny request) or `warn` (allow but notify) |

Example:

```yaml
cli:
  usage:
    model_costs:
      gpt-4o: { input: 0.003, output: 0.006 }
    budgets:
      enabled: true
      max_tokens_per_request: 100000     # single request cap
      max_tokens_per_conversation: 500000 # conversation cap
      max_tokens_per_day: 2000000         # daily cap
      warn_threshold_percent: 80          # warn at 80%
      action_on_exceed: block             # hard block when exceeded
```

Budgets are checked at the start of each request. When a limit is exceeded with `action_on_exceed: block`, the request is rejected with a `budget_exceeded` error. With `action_on_exceed: warn`, the request proceeds but a `budget_warning` event is emitted.

### references

Paths to shared instruction, rule, and skill files. Typically used in team or project configs to share development standards. See [Project Configuration](project-config.md).

| Field | Type | Default | Description |
|---|---|---|---|
| `instructions` | list | `[]` | Paths to instruction files (markdown) loaded as context |
| `rules` | list | `[]` | Paths to rule files (markdown) loaded as behavioral constraints |
| `skills` | list | `[]` | Paths to skill files (markdown) loaded as reusable prompts |

Paths are resolved relative to the config file that declares them. Non-string entries are silently filtered out.

### required

A list of config keys that must be present in the user's personal config. Typically used in team or project configs. See [Project Configuration](project-config.md#required-keys).

Each entry is a dict with:

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Dot-separated config path (e.g., `ai.api_key`) |
| `description` | string | no | Human-readable description shown during prompting |

In interactive mode, missing required keys trigger a prompt. Sensitive fields (containing `key`, `secret`, `password`, `token`, `passphrase`) use masked input. Values are saved to the personal config with 0600 permissions.

In non-interactive mode, missing keys produce an error message listing each missing path and its equivalent `AI_CHAT_*` environment variable.

## API Key Command

The `api_key_command` field runs an external command to obtain API keys with automatic transparent refresh:

- Command is executed via `subprocess.run()` with `shlex.split()` --- no `shell=True`, preventing shell injection
- 30-second execution timeout prevents hanging commands
- Token is cached in memory only, never written to disk or logged
- On HTTP 401, the command is re-run automatically and the request is retried

```yaml
ai:
  api_key_command: "aws secretsmanager get-secret-value --secret-id anteroom-key --query SecretString --output text"
```
