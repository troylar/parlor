# Usage API

Token usage tracking and cost estimation endpoint.

## Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/usage` | Get token usage statistics |

## Get Usage Statistics

```
GET /api/usage
```

Query parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `period` | string | `null` | Specific period: `day`, `week`, `month`, or `all`. If omitted, returns all periods |
| `conversation_id` | UUID | `null` | Filter by conversation ID (optional) |

### Response

Returns usage statistics keyed by time period label. Each period includes:

- `prompt_tokens` — total prompt tokens
- `completion_tokens` — total completion tokens
- `total_tokens` — sum of prompt and completion tokens
- `message_count` — number of messages in the period
- `estimated_cost` — estimated cost based on configured `model_costs`
- `by_model` — breakdown by model name

### Example Request

```bash
curl http://127.0.0.1:8080/api/usage
```

### Example Response

```json
{
  "Today": {
    "prompt_tokens": 5420,
    "completion_tokens": 2150,
    "total_tokens": 7570,
    "message_count": 12,
    "estimated_cost": 0.0234,
    "by_model": [
      {
        "model": "gpt-4o",
        "prompt_tokens": 5420,
        "completion_tokens": 2150,
        "total_tokens": 7570,
        "message_count": 12
      }
    ]
  },
  "This week": {
    "prompt_tokens": 42100,
    "completion_tokens": 18900,
    "total_tokens": 61000,
    "message_count": 89,
    "estimated_cost": 0.1847,
    "by_model": [...]
  },
  "This month": {
    "prompt_tokens": 185400,
    "completion_tokens": 92300,
    "total_tokens": 277700,
    "message_count": 342,
    "estimated_cost": 0.8421,
    "by_model": [...]
  },
  "All time": {
    "prompt_tokens": 890250,
    "completion_tokens": 456800,
    "total_tokens": 1347050,
    "message_count": 1523,
    "estimated_cost": 4.0782,
    "by_model": [...]
  }
}
```

### Single Period

To get a single period, use the `period` query parameter:

```bash
curl "http://127.0.0.1:8080/api/usage?period=week"
```

```json
{
  "This week": {
    "prompt_tokens": 42100,
    ...
  }
}
```

### Single Conversation

To get usage for a specific conversation, use the `conversation_id` query parameter:

```bash
curl "http://127.0.0.1:8080/api/usage?conversation_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890"
```

## Configuration

Cost estimation requires configuring model costs in `~/.anteroom/config.yaml`:

```yaml
cli:
  usage:
    model_costs:
      gpt-4o: { input: 0.003, output: 0.006 }
      gpt-4-turbo: { input: 0.01, output: 0.03 }
      claude-3-sonnet: { input: 0.003, output: 0.015 }
    week_days: 7      # Days for "this week" rolling window
    month_days: 30    # Days for "this month" rolling window
```

Without `model_costs` configured, the `estimated_cost` field will be 0.0, but token counts are always tracked and returned.
