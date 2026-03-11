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
  provider: "openai"                            # AI backend: "openai", "anthropic", or "litellm" (100+ providers)
  max_output_tokens: 4096                       # Max generated tokens per response (default: 4096; required by some providers)
  allowed_domains: []                           # Egress domain allowlist (empty = no restriction)
  block_localhost_api: false                    # When true, reject localhost/127.0.0.1 as base_url

app:
  host: "127.0.0.1"      # Bind address
  port: 8080              # Server port
  data_dir: "~/.anteroom"   # Where DB + attachments live
  tls: false              # Set true for HTTPS with self-signed cert

rate_limit:
  max_requests: 120       # Max requests per window per IP (default: 120)
  window_seconds: 60      # Sliding window size in seconds (default: 60)
  exempt_paths:           # Paths exempt from rate limiting
    - /api/events         # SSE endpoint for real-time updates
  sse_retry_ms: 5000      # EventSource retry interval in milliseconds (default: 5000)

storage:
  retention_days: 0                    # Days to retain conversations; 0 = disabled (default: 0)
  retention_check_interval: 3600       # Seconds between retention policy checks (default: 3600 = 1 hour, clamped 300–86400)
  purge_attachments: true              # Delete attachment files when conversations are purged (default: true)
  purge_embeddings: true               # Delete vector embeddings when conversations are purged (default: true)
  encrypt_at_rest: false               # Enable database encryption via SQLCipher (default: false, requires sqlcipher3)
  encryption_kdf: "hkdf-sha256"        # Key derivation function for encryption (default: hkdf-sha256)

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
  max_consecutive_text_only: 3     # Stop after N text-only responses with no tool calls; 0 to disable (default: 3)
  max_line_repeats: 5              # Stop if a single response repeats the same line N+ times; 0 to disable (default: 5)
  context_warn_tokens: 80000       # Token count at which context warning is shown (default: 80000)
  context_auto_compact_tokens: 100000  # Token count at which auto-compaction triggers (default: 100000)
  retry_delay: 5.0                 # Seconds between CLI auto-retry countdown ticks (default: 5.0, clamped 1–60)
  max_retries: 3                   # Max CLI auto-retry attempts for retryable errors (default: 3, clamped 0–10)
  esc_hint_delay: 3.0              # Seconds before showing "esc to cancel" hint (default: 3.0, clamped 0+)
  stall_display_threshold: 5.0     # Seconds of chunk silence before showing "stalled" indicator (default: 5.0, clamped 1+)
  stall_warning_threshold: 15.0    # Seconds before showing full stall warning (default: 15.0, clamped 1+)
  stall_throughput_threshold: 30.0 # Chars/sec below which "slow" indicator shows during streaming (default: 30.0)
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
  prompt_injection:
    enabled: false                        # Enable prompt injection detection (default: false)
    action: "warn"                        # "block" | "warn" | "log" (default: warn)
    canary_length: 16                     # Bytes of randomness for canary tokens (default: 16)
    detect_encoding_attacks: true         # Detect base64, hex, unicode escaping (default: true)
    detect_instruction_override: true     # Detect instruction override patterns (default: true)
    heuristic_threshold: 0.7              # Minimum confidence to trigger action (default: 0.7)
    log_detections: true                  # Log detected attacks to security log (default: true)

embeddings:
  enabled: true
  model: "text-embedding-3-small"
  dimensions: 1536
  base_url: ""
  api_key: ""
  api_key_command: ""
  cache_dir: ""                        # Custom model cache directory; enables local_files_only when set

rag:
  enabled: true                        # Master toggle for RAG (default: true)
  max_chunks: 10                       # Maximum chunks to retrieve per query (default: 10)
  max_tokens: 2000                     # Token budget for RAG context (default: 2000)
  similarity_threshold: 0.5            # Maximum cosine distance; lower = stricter (default: 0.5)
  include_sources: true                # Search knowledge source chunks (default: true)
  include_conversations: true          # Search past conversation messages (default: true)
  exclude_current: true                # Exclude current conversation from results (default: true)
  retrieval_mode: "dense"              # "dense", "keyword", or "hybrid" (default: "dense")

reranker:
  enabled: null                        # null = auto-detect (use if fastembed available)
  provider: "local"                    # "local" (fastembed TextCrossEncoder)
  model: "cross-encoder/ms-marco-MiniLM-L-6-v2"
  top_k: 5                            # Keep top-K chunks after reranking
  score_threshold: 0.0                 # Minimum relevance score (0 = no threshold)
  candidate_multiplier: 3             # Fetch top_k * multiplier candidates before reranking
  cache_dir: ""                        # Custom model cache directory; enables local_files_only when set

dlp:
  enabled: false                       # Set true to enable DLP scanning
  scan_output: true                    # Scan AI responses (default: true)
  scan_input: false                    # Scan user input (default: false)
  action: "redact"                     # "redact" | "block" | "warn" (default: redact)
  redaction_string: "[REDACTED]"
  log_detections: true                 # Log matches to security log (default: true)
  # patterns: []                        # Built-in patterns loaded by default
  custom_patterns: []                  # Add custom regex rules

output_filter:
  enabled: false                       # Set true to enable output filtering
  system_prompt_leak_detection: true   # Detect system prompt leaks via n-gram analysis (default: true)
  leak_threshold: 0.4                  # Minimum similarity threshold for leak detection (0.0-1.0, default: 0.4)
  action: "warn"                       # "warn" | "block" | "redact" (default: warn)
  redaction_string: "[FILTERED]"       # String to replace filtered content when action is "redact"
  log_detections: true                 # Log filter detections to security log (default: true)
  custom_patterns: []                  # Add custom forbidden pattern rules

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
| `provider` | string | `openai` | AI backend to use: `openai` (default, works with any OpenAI-compatible API), `anthropic` (native Anthropic Messages API, requires `pip install anteroom[anthropic]`), or `litellm` (100+ providers via LiteLLM — OpenRouter, Replicate, Together, Cohere, etc., requires `pip install anteroom[providers]`); env: `AI_CHAT_PROVIDER` |
| `max_output_tokens` | integer | `4096` | Maximum tokens to generate per response. Required by some providers (e.g. Anthropic). Ignored when the provider does not support the parameter; env: `AI_CHAT_MAX_OUTPUT_TOKENS` |
| `allowed_domains` | list[string] | `[]` | Egress domain allowlist for API requests (empty list = no restriction). Domains are matched case-insensitively as exact matches. Fails closed: unparseable URLs are rejected; env: `AI_CHAT_ALLOWED_DOMAINS` (comma-separated) |
| `block_localhost_api` | boolean | `false` | When true, reject localhost/127.0.0.1/[::1] as the API base_url. Useful in enterprise environments to prevent accidental connections to local services; env: `AI_CHAT_BLOCK_LOCALHOST_API` |

### app

| Field | Type | Default | Description |
|---|---|---|---|
| `host` | string | `127.0.0.1` | Bind address for the web server |
| `port` | integer | `8080` | Port for the web server; env: `AI_CHAT_PORT` |
| `data_dir` | string | `~/.anteroom` | Directory for database, attachments, config |
| `tls` | boolean | `false` | Enable HTTPS with self-signed certificate |

### rate_limit

HTTP request rate limiting to prevent abuse and SSE storms. Protects against EventSource reconnection floods.

| Field | Type | Default | Description |
|---|---|---|---|
| `max_requests` | integer | `120` | Maximum requests per IP address per time window (sliding window); env: `AI_CHAT_RATE_LIMIT_MAX_REQUESTS` |
| `window_seconds` | integer | `60` | Sliding window size in seconds; env: `AI_CHAT_RATE_LIMIT_WINDOW_SECONDS` |
| `exempt_paths` | list[string] | `["/api/events"]` | URL paths exempt from rate limiting (e.g., SSE endpoints); env: `AI_CHAT_RATE_LIMIT_EXEMPT_PATHS` (comma-separated) |
| `sse_retry_ms` | integer | `5000` | EventSource `retry:` field sent to browser clients (milliseconds). Controls reconnection backoff on 429 responses; env: `AI_CHAT_RATE_LIMIT_SSE_RETRY_MS` |

Example:

```yaml
rate_limit:
  max_requests: 120
  window_seconds: 60
  exempt_paths:
    - /api/events
    - /health
  sse_retry_ms: 5000
```

When a client exceeds the rate limit, the server returns HTTP 429 (Too Many Requests) with the `Retry-After` header. For SSE connections, the server sends a `retry:` field directing the EventSource to wait before reconnecting. Paths in `exempt_paths` are not rate-limited, allowing critical endpoints like `/api/events` to handle high-frequency client reconnections.

### storage

Controls data retention policies and encryption at rest for the SQLite database.

| Field | Type | Default | Description |
|---|---|---|---|
| `retention_days` | integer | `0` | Number of days to retain conversations before automatic purge; `0` = disabled (retention disabled); env: `AI_CHAT_RETENTION_DAYS` |
| `retention_check_interval` | integer | `3600` | Seconds between retention policy checks (clamped 300–86400); env: `AI_CHAT_RETENTION_CHECK_INTERVAL` |
| `purge_attachments` | boolean | `true` | Delete attachment files from disk when conversations are purged (default: true); env: `AI_CHAT_PURGE_ATTACHMENTS` |
| `purge_embeddings` | boolean | `true` | Delete vector embeddings from the database when conversations are purged (default: true); env: `AI_CHAT_PURGE_EMBEDDINGS` |
| `encrypt_at_rest` | boolean | `false` | Enable database encryption via SQLCipher. Requires sqlcipher3: `pip install anteroom[sqlcipher]` (default: false); env: `AI_CHAT_ENCRYPT_AT_REST` |
| `encryption_kdf` | string | `hkdf-sha256` | Key derivation function for encryption (default: `hkdf-sha256`); env: `AI_CHAT_ENCRYPTION_KDF` |

#### Data Retention

When `retention_days` is set to a positive value, the retention worker automatically purges conversations (and all associated messages, tool_calls, and embeddings) older than the configured period. The retention check runs at the interval specified by `retention_check_interval`.

To manually purge conversations, use:

```bash
aroom db purge --older-than 30d      # purge conversations older than 30 days
aroom db purge --before 2025-01-01   # purge conversations before a specific date
aroom db purge --dry-run             # show what would be deleted
```

#### Encryption at Rest

When `encrypt_at_rest` is enabled:

- The database is encrypted using SQLCipher with a 256-bit key
- The key is derived from your Ed25519 identity key via HKDF-SHA256
- All database queries go through the encrypted connection transparently
- Attachments are NOT encrypted (only the database is)
- Requires `sqlcipher3` package: `pip install anteroom[sqlcipher]`

To initialize encryption on an existing database:

```bash
aroom db encrypt            # interactive setup
aroom db encrypt --key-from identity  # use identity key as encryption key
```

!!! note "One-way operation"
    Encryption is not reversible. Once enabled on a database, it cannot be decrypted without the original identity key. Back up your config before enabling.

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
| `max_consecutive_text_only` | integer | `3` | Stop after N consecutive text-only responses with no tool calls; 0 to disable |
| `max_line_repeats` | integer | `5` | Stop if a single response repeats the same line N+ times; 0 to disable |
| `context_warn_tokens` | integer | `80000` | Token count at which a context warning is shown in the CLI |
| `context_auto_compact_tokens` | integer | `100000` | Token count at which context is automatically compacted |
| `retry_delay` | float | `5.0` | Seconds between CLI auto-retry countdown ticks (clamped 1–60) |
| `max_retries` | integer | `3` | Max CLI auto-retry attempts for retryable errors (clamped 0–10) |
| `esc_hint_delay` | float | `3.0` | Seconds before showing "esc to cancel" hint |
| `stall_display_threshold` | float | `5.0` | Seconds of chunk silence before showing "stalled" indicator (clamped 1+) |
| `stall_warning_threshold` | float | `15.0` | Seconds before showing full stall warning (clamped 1+) |
| `stall_throughput_threshold` | float | `30.0` | Chars/sec below which "slow" indicator shows during streaming |
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

#### safety.prompt_injection

Controls prompt injection detection and defense. Detects when untrusted content (tool outputs, RAG results) attempts to override system instructions through canary token leakage, encoding attacks, and heuristic pattern matching.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | boolean | `false` | Enable prompt injection detection |
| `action` | string | `warn` | Action when injection is detected: `block` (reject content), `warn` (allow + log warning), `log` (silent audit) |
| `canary_length` | integer | `16` | Length in bytes of random canary tokens embedded in system prompt for leakage detection |
| `detect_encoding_attacks` | boolean | `true` | Detect obfuscated injection attempts (base64, hex, unicode escaping) |
| `detect_instruction_override` | boolean | `true` | Detect patterns that attempt to override or ignore system instructions |
| `heuristic_threshold` | float | `0.7` | Confidence threshold (0.0–1.0) for heuristic pattern matching; higher = fewer false positives |
| `log_detections` | boolean | `true` | Log all detected injections to the security logger |

Example to enable strict injection defense:

```yaml
safety:
  prompt_injection:
    enabled: true
    action: "block"                       # hard-block injections
    detect_encoding_attacks: true
    detect_instruction_override: true
    heuristic_threshold: 0.6              # lower threshold catches more attempts
    log_detections: true
```

See [Prompt Injection Defense](../security/prompt-injection-defense.md) for technical details and threat model.

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
| `cache_dir` | string | `""` | Custom fastembed model cache directory; when set, enables `local_files_only` mode to prevent network requests; env: `AI_CHAT_EMBEDDINGS_CACHE_DIR` |

### rag

Controls the RAG retrieval pipeline --- what gets searched, how many results, filtering thresholds, and retrieval strategy.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | boolean | `true` | Master toggle; `false` disables RAG entirely; env: `AI_CHAT_RAG_ENABLED` |
| `max_chunks` | integer | `10` | Maximum chunks to retrieve per query; env: `AI_CHAT_RAG_MAX_CHUNKS` |
| `max_tokens` | integer | `2000` | Token budget for injected RAG context (estimated as chars / 4); env: `AI_CHAT_RAG_MAX_TOKENS` |
| `similarity_threshold` | float | `0.5` | Maximum cosine distance; only applies in `dense` mode; env: `AI_CHAT_RAG_SIMILARITY_THRESHOLD` |
| `include_sources` | boolean | `true` | Search knowledge source chunks |
| `include_conversations` | boolean | `true` | Search past conversation messages |
| `exclude_current` | boolean | `true` | Exclude current conversation from message search |
| `retrieval_mode` | string | `dense` | Retrieval strategy: `dense` (vector similarity), `keyword` (FTS5), or `hybrid` (both via RRF); env: `AI_CHAT_RAG_RETRIEVAL_MODE` |

### reranker

Controls cross-encoder reranking of RAG results. When enabled, retrieved chunks are re-scored by a cross-encoder model for improved relevance before being injected into the prompt. Uses fastembed `TextCrossEncoder` locally by default (no external API needed).

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | boolean | `null` (auto-detect) | Enable reranking; `null` uses reranking when fastembed is available; env: `AI_CHAT_RERANKER_ENABLED` |
| `provider` | string | `local` | Provider: `local` (fastembed TextCrossEncoder); only local is supported; env: `AI_CHAT_RERANKER_PROVIDER` |
| `model` | string | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder model name; env: `AI_CHAT_RERANKER_MODEL` |
| `top_k` | integer | `5` | Keep top-K chunks after reranking (capped to `rag.max_chunks` at runtime); env: `AI_CHAT_RERANKER_TOP_K` |
| `score_threshold` | float | `0.0` | Minimum relevance score; cross-encoder logits can be negative; env: `AI_CHAT_RERANKER_SCORE_THRESHOLD` |
| `candidate_multiplier` | integer | `3` | Fetch `top_k * candidate_multiplier` candidates before reranking; env: `AI_CHAT_RERANKER_CANDIDATE_MULTIPLIER` |
| `cache_dir` | string | `""` | Custom fastembed model cache directory; when set, enables `local_files_only` mode to prevent network requests; env: `AI_CHAT_RERANKER_CACHE_DIR` |

### dlp

Controls Data Loss Prevention scanning for sensitive patterns (SSN, credit card, email, phone, IBAN). See [DLP documentation](../security/dlp.md) for detailed configuration and use cases.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | boolean | `false` | Enable DLP scanning |
| `scan_output` | boolean | `true` | Scan AI responses for sensitive data |
| `scan_input` | boolean | `false` | Scan user input for sensitive data |
| `action` | string | `redact` | Action on match: `redact` (replace with `redaction_string`), `block` (reject), or `warn` (allow + log) |
| `redaction_string` | string | `[REDACTED]` | String to replace matched patterns when action is `redact` |
| `log_detections` | boolean | `true` | Log all DLP detections to the security logger |
| `patterns` | list | `[]` | Built-in patterns are loaded automatically (SSN, credit card, email, phone, IBAN) |
| `custom_patterns` | list | `[]` | Custom regex patterns. Each is a dict with `name`, `pattern` (regex), and `description` |

### output_filter

Controls output content filtering to detect and prevent system prompt leaks and forbidden content in LLM responses. See [Output Filter documentation](../security/output-filter.md) for detailed configuration and use cases.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | boolean | `false` | Enable output content filtering |
| `system_prompt_leak_detection` | boolean | `true` | Detect system prompt leaks via n-gram analysis of LLM output against the system prompt |
| `leak_threshold` | float | `0.4` | Minimum n-gram similarity threshold (0.0–1.0) for flagging a potential leak |
| `action` | string | `warn` | Action on filter match: `warn` (allow + log), `redact` (replace with `redaction_string`), or `block` (reject) |
| `redaction_string` | string | `[FILTERED]` | String to replace filtered content when action is `redact` |
| `log_detections` | boolean | `true` | Log all filter detections to the security logger |
| `custom_patterns` | list | `[]` | Custom regex patterns for forbidden content. Each is a dict with `name`, `pattern` (regex), and `description` |

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

### pack_sources

Configure git repositories containing packs for automatic cloning and refresh.

```yaml
pack_sources:
  - url: https://github.com/acme/anteroom-packs.git
    branch: main
    refresh_interval: 30  # minutes; 0 = manual only
    auto_attach: true     # automatically attach packs from this source
    priority: 50          # 1-100, lower wins for conflict resolution
```

| Field | Type | Default | Description |
|---|---|---|---|
| `url` | string | (required) | Git remote URL. Accepts `https://`, `ssh://`, `git://`, `http://`, SSH shorthand. Rejects `ext::` and `file://` |
| `branch` | string | `"main"` | Git branch to clone and track |
| `refresh_interval` | integer | `30` | Minutes between auto-refresh. `0` = manual only. Minimum: 5 (values below 5 are clamped) |
| `auto_attach` | boolean | `true` | When `true`, new packs from this source are automatically attached at the global scope on install. When `false`, packs must be manually attached via `aroom pack attach` |
| `priority` | integer | `50` | Conflict resolution priority (1-100). Lower number wins when multiple sources provide conflicting packs. Must be in range 1-100 |

The background worker clones sources on first encounter, then pulls periodically. After 10 consecutive failures, a source is auto-disabled until restart. See [Pack Sources](../packs/pack-sources.md) for the full lifecycle.

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
