# Configuration

Anteroom is configured through a YAML file with environment variable overrides.

## Quick Reference

| Setting | Config Key | Env Var | Default |
|---|---|---|---|
| API endpoint | `ai.base_url` | `AI_CHAT_BASE_URL` | --- (required) |
| API key | `ai.api_key` | `AI_CHAT_API_KEY` | --- (required) |
| Model | `ai.model` | `AI_CHAT_MODEL` | `gpt-4` |
| System prompt | `ai.system_prompt` | `AI_CHAT_SYSTEM_PROMPT` | `You are a helpful assistant.` |
| SSL verification | `ai.verify_ssl` | `AI_CHAT_VERIFY_SSL` | `true` |
| Host | `app.host` | --- | `127.0.0.1` |
| Port | `app.port` | --- | `8080` |
| Data directory | `app.data_dir` | --- | `~/.anteroom` |
| TLS | `app.tls` | --- | `false` |
| Built-in tools | `cli.builtin_tools` | --- | `true` |
| Max tool iterations | `cli.max_tool_iterations` | --- | `50` |

## Configuration Sources

Configuration follows a layered approach with later sources overriding earlier ones:

1. **Defaults** --- sensible defaults for all settings
2. **Config file** --- `~/.anteroom/config.yaml`
3. **Environment variables** --- `AI_CHAT_*` prefix

## Pages

- [Config File](config-file.md) --- full `config.yaml` reference
- [Environment Variables](environment-variables.md) --- all `AI_CHAT_*` env vars
- [MCP Servers](mcp-servers.md) --- MCP server setup (stdio + SSE)
- [TLS](tls.md) --- HTTPS with self-signed certificates
