# Config, Models, and Databases API

## Configuration

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/config` | Get current config + MCP server statuses |
| `PATCH` | `/api/config` | Update model and/or system prompt |
| `POST` | `/api/config/validate` | Test API connection, list models |

### Get Config

```
GET /api/config
```

Returns the current configuration including MCP server connection statuses. The API key is returned as a boolean (`has_api_key`) --- never the actual value.

### Update Config

```
PATCH /api/config
```

Updates the model and/or system prompt. Changes persist to `config.yaml` and take effect immediately.

### Validate Connection

```
POST /api/config/validate
```

Tests the API connection and lists available models. Equivalent to `aroom --test` on the command line.

## Models

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/models` | List available models (sorted alphabetically) |

## Databases

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/databases` | List all connected databases |
| `POST` | `/api/databases` | Add database (name, path) |
| `DELETE` | `/api/databases/:name` | Remove database connection |
| `GET` | `/api/browse?path=` | Browse filesystem for `.db`/`.sqlite`/`.sqlite3` files |

## MCP Tools

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/mcp/tools` | List all available MCP tools with schemas |
