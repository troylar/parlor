# Environment Variables

Every AI config option has an environment variable override with the `AI_CHAT_` prefix.

## Reference

| Variable | Default | Description |
|---|---|---|
| `AI_CHAT_BASE_URL` | --- | AI API endpoint (required) |
| `AI_CHAT_API_KEY` | --- | API key (required) |
| `AI_CHAT_MODEL` | `gpt-4` | Model name |
| `AI_CHAT_SYSTEM_PROMPT` | `You are a helpful assistant.` | System prompt |
| `AI_CHAT_VERIFY_SSL` | `true` | SSL certificate verification |

## Usage

Environment variables override config file values. This is useful for:

- CI/CD environments where you can't write config files
- Switching between API endpoints without editing config
- Keeping secrets out of config files

```bash
$ export AI_CHAT_BASE_URL="https://api.openai.com/v1"
$ export AI_CHAT_API_KEY="sk-..."
$ export AI_CHAT_MODEL="gpt-4-turbo"
$ aroom chat
```

## Precedence

Environment variables take highest priority:

1. Defaults (lowest)
2. Config file (`~/.anteroom/config.yaml`)
3. Environment variables (highest)
