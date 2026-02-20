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
  first_token_timeout: 30                      # Max wait for first token after connect (default: 30, clamped 5–120)
  retry_max_attempts: 3                        # Retries on transient errors; 0 disables (default: 3, clamped 0–10)
  retry_backoff_base: 1.0                      # Exponential backoff base in seconds (default: 1.0, clamped 0.1–30.0)

app:
  host: "127.0.0.1"      # Bind address
  port: 8080              # Server port
  data_dir: "~/.anteroom"   # Where DB + attachments live
  tls: false              # Set true for HTTPS with self-signed cert

cli:
  builtin_tools: true              # Enable built-in tools (default: true)
  max_tool_iterations: 50          # Max tool calls per response (default: 50)
  context_warn_tokens: 80000       # Token count at which context warning is shown (default: 80000)
  context_auto_compact_tokens: 100000  # Token count at which auto-compaction triggers (default: 100000)

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

  - name: "remote-tools"
    transport: "sse"
    url: "https://mcp-server.example.com/sse"

safety:
  enabled: true
  approval_mode: "ask_for_writes"    # auto | ask_for_dangerous | ask_for_writes | ask
  approval_timeout: 120
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

embeddings:
  enabled: true
  model: "text-embedding-3-small"
  dimensions: 1536
  base_url: ""
  api_key: ""
  api_key_command: ""
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
| `first_token_timeout` | integer | `30` | Max seconds to wait for first token after connect (clamped 5–120); env: `AI_CHAT_FIRST_TOKEN_TIMEOUT` |
| `retry_max_attempts` | integer | `3` | Retries on transient errors (timeout, connection); 0 disables (clamped 0–10); env: `AI_CHAT_RETRY_MAX_ATTEMPTS` |
| `retry_backoff_base` | float | `1.0` | Exponential backoff base delay in seconds (clamped 0.1–30.0); env: `AI_CHAT_RETRY_BACKOFF_BASE` |

### app

| Field | Type | Default | Description |
|---|---|---|---|
| `host` | string | `127.0.0.1` | Bind address for the web server |
| `port` | integer | `8080` | Port for the web server; env: `AI_CHAT_PORT` |
| `data_dir` | string | `~/.anteroom` | Directory for database, attachments, config |
| `tls` | boolean | `false` | Enable HTTPS with self-signed certificate |

### cli

| Field | Type | Default | Description |
|---|---|---|---|
| `builtin_tools` | boolean | `true` | Enable the 6 built-in tools |
| `max_tool_iterations` | integer | `50` | Max agentic loop iterations per turn |
| `context_warn_tokens` | integer | `80000` | Token count at which a context warning is shown in the CLI |
| `context_auto_compact_tokens` | integer | `100000` | Token count at which context is automatically compacted |

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

### safety

Controls the tool safety approval gate. Tools are assigned risk tiers (read, write, execute, destructive) and the approval mode determines which tiers require user confirmation before execution.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | boolean | `true` | Enable the safety gate globally |
| `approval_mode` | string | `ask_for_writes` | Which tiers require approval: `auto` (none), `ask_for_dangerous` (destructive only), `ask_for_writes` (write+execute+destructive), `ask` (same as ask_for_writes) |
| `approval_timeout` | integer | `120` | Seconds to wait for approval before blocking the operation (clamped 10–600) |
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
