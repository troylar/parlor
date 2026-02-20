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
| `AI_CHAT_REQUEST_TIMEOUT` | `120` | Overall stream timeout in seconds (clamped 10–600) |
| `AI_CHAT_CONNECT_TIMEOUT` | `5` | TCP connect timeout in seconds (clamped 1–30) |
| `AI_CHAT_FIRST_TOKEN_TIMEOUT` | `30` | Max seconds to wait for first token after connect (clamped 5–120) |
| `AI_CHAT_RETRY_MAX_ATTEMPTS` | `3` | Retries on transient errors; 0 disables (clamped 0–10) |
| `AI_CHAT_RETRY_BACKOFF_BASE` | `1.0` | Exponential backoff base delay in seconds (clamped 0.1–30.0) |
| `AI_CHAT_PORT` | `8080` | Port for the web server |
| `AI_CHAT_USER_ID` | --- | Override user identity UUID |
| `AI_CHAT_DISPLAY_NAME` | --- | Override user display name |
| `AI_CHAT_PUBLIC_KEY` | --- | Override user public key (PEM) |
| `AI_CHAT_PRIVATE_KEY` | --- | Override user private key (PEM) |
| `AI_CHAT_SAFETY_ENABLED` | `true` | Enable/disable the tool safety gate |
| `AI_CHAT_SAFETY_APPROVAL_MODE` | `ask_for_writes` | Approval mode: `auto`, `ask_for_dangerous`, `ask_for_writes`, `ask` |

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
