# Add MCP Tools

Connect external tools to Anteroom via the Model Context Protocol (MCP).

## What is MCP?

MCP (Model Context Protocol) is a standard for connecting AI models to external tools. Anteroom supports both **stdio** (local process) and **SSE** (remote HTTP) MCP servers.

## Step 1: Find an MCP Server

MCP servers are available for many services. Common examples:

- File system access
- Database queries
- API integrations
- Web browsing

## Step 2: Configure

Add the MCP server to your `config.yaml`:

=== "stdio (local process)"

    ```yaml title="~/.anteroom/config.yaml"
    mcp_servers:
      - name: "filesystem"
        transport: "stdio"
        command: "npx"
        args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/dir"]
    ```

=== "SSE (remote server)"

    ```yaml title="~/.anteroom/config.yaml"
    mcp_servers:
      - name: "remote-tools"
        transport: "sse"
        url: "https://mcp-server.example.com/sse"
    ```

## Step 3: Add Environment Variables

If the MCP server needs credentials, use `${VAR}` expansion:

```yaml
mcp_servers:
  - name: "database"
    transport: "stdio"
    command: "npx"
    args: ["-y", "@my-org/mcp-database"]
    env:
      DATABASE_URL: "${DATABASE_URL}"
      API_KEY: "${MY_API_KEY}"
```

Set the actual values in your shell environment:

```bash
$ export DATABASE_URL="postgresql://localhost/mydb"
$ export MY_API_KEY="sk-..."
```

## Step 4: Verify

Restart Anteroom and check that the tools are available:

=== "CLI"

    ```
    you> /tools
    ```

    You should see your MCP tools listed alongside the built-in tools.

=== "Web UI"

    Check the sidebar footer for the connected server count and total tool count.

## Step 5: Use the Tools

The AI automatically discovers and uses MCP tools when they're relevant to your request. You don't need to invoke them explicitly --- just describe what you want to do.

```
you> query the database for all users created this week
you> list files in the project directory
```

## Multiple Servers

You can connect multiple MCP servers simultaneously:

```yaml
mcp_servers:
  - name: "filesystem"
    transport: "stdio"
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "."]

  - name: "database"
    transport: "stdio"
    command: "npx"
    args: ["-y", "@my-org/mcp-database"]

  - name: "remote-api"
    transport: "sse"
    url: "https://api.example.com/mcp/sse"
```

All tools from all servers are available in both the CLI and web UI.
