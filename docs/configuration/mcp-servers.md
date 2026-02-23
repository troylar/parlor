# MCP Servers

Connect **stdio** or **SSE-based** MCP (Model Context Protocol) servers to give the AI access to external tools.

## Configuration

Add MCP servers to your `config.yaml`:

```yaml
mcp_servers:
  - name: "my-tools"
    transport: "stdio"
    command: "npx"
    args: ["-y", "@my-org/mcp-tools"]
    env:
      API_KEY: "${MY_API_KEY}"
      DEBUG: "true"

  - name: "remote-tools"
    transport: "sse"
    url: "https://mcp-server.example.com/sse"
```

## Server Types

=== "stdio"

    Launches a local process and communicates via stdin/stdout.

    | Field | Type | Required | Description |
    |---|---|---|---|
    | `name` | string | Yes | Display name for the server |
    | `transport` | string | Yes | Must be `"stdio"` |
    | `command` | string | Yes | Command to run |
    | `args` | list | No | Command arguments |
    | `env` | map | No | Environment variables for the process |
    | `tools_include` | list | No | Fnmatch patterns for tools to include (empty = include all) |
    | `tools_exclude` | list | No | Fnmatch patterns for tools to exclude |

=== "SSE"

    Connects to a remote MCP server via Server-Sent Events.

    | Field | Type | Required | Description |
    |---|---|---|---|
    | `name` | string | Yes | Display name for the server |
    | `transport` | string | Yes | Must be `"sse"` |
    | `url` | string | Yes | SSE endpoint URL |
    | `tools_include` | list | No | Fnmatch patterns for tools to include (empty = include all) |
    | `tools_exclude` | list | No | Fnmatch patterns for tools to exclude |

## Tool Filtering

Control which tools from an MCP server are available to the AI using fnmatch patterns:

```yaml
mcp_servers:
  - name: "filesystem"
    transport: "stdio"
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem"]
    tools_include:
      - "read_*"      # Include only read_* tools
      - "list_*"

  - name: "database"
    transport: "stdio"
    command: "npx"
    args: ["-y", "@my-org/mcp-database"]
    tools_exclude:
      - "*drop*"      # Exclude any tools with 'drop' in the name
      - "truncate*"
```

Rules:
- `tools_include` — allowlist: if specified (non-empty), only matching tools are available
- `tools_exclude` — blocklist: matching tools are hidden
- If both are specified, `tools_include` takes precedence
- Empty or omitted = all tools available
- Patterns use fnmatch syntax (`*` = any chars, `?` = single char, `[abc]` = character set)

## Environment Variable Expansion

Environment variables in `env` values support `${VAR}` expansion:

```yaml
env:
  API_KEY: "${MY_API_KEY}"
  DATABASE_URL: "${DB_URL}"
```

This lets you reference secrets from your shell environment without hardcoding them in the config file.

## How MCP Tools Work

MCP tools work alongside built-in tools. When the AI calls a tool:

1. Built-in tools are checked first
2. If no built-in matches, the call is forwarded to the appropriate MCP server

Use `/tools` in the CLI to see all available tools from both sources.

## Web UI Integration

In the web UI:

- Tool calls render as expandable detail panels with input and output
- Spinner animation while tools execute
- Connected server count and total tool count shown in sidebar footer

## Security

- **SSRF protection** --- DNS resolution validates that MCP server URLs don't point to private IP addresses
- **Shell metacharacter rejection** --- tool arguments are sanitized to prevent injection
- MCP servers configured in `config.yaml` are available in both CLI and web UI
