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

app:
  host: "127.0.0.1"      # Bind address
  port: 8080              # Server port
  data_dir: "~/.anteroom"   # Where DB + attachments live
  tls: false              # Set true for HTTPS with self-signed cert

cli:
  builtin_tools: true      # Enable built-in tools (default: true)
  max_tool_iterations: 50  # Max tool calls per response (default: 50)

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

### app

| Field | Type | Default | Description |
|---|---|---|---|
| `host` | string | `127.0.0.1` | Bind address for the web server |
| `port` | integer | `8080` | Port for the web server |
| `data_dir` | string | `~/.anteroom` | Directory for database, attachments, config |
| `tls` | boolean | `false` | Enable HTTPS with self-signed certificate |

### cli

| Field | Type | Default | Description |
|---|---|---|---|
| `builtin_tools` | boolean | `true` | Enable the 6 built-in tools |
| `max_tool_iterations` | integer | `50` | Max agentic loop iterations per turn |

### shared_databases

A list of additional SQLite databases. See [Shared Databases](../web-ui/shared-databases.md).

| Field | Type | Description |
|---|---|---|
| `name` | string | Display name (alphanumeric, hyphens, underscores) |
| `path` | string | Path to `.db`/`.sqlite`/`.sqlite3` file |

### mcp_servers

A list of MCP tool servers. See [MCP Servers](mcp-servers.md).

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
