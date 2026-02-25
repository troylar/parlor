# Anteroom Eval Suite

Prompt regression, agentic behavior testing, and red teaming using [promptfoo](https://github.com/promptfoo/promptfoo).

## Quick Start

```bash
# Install promptfoo (one-time)
npm install -g promptfoo
# or use npx (no install needed)

# Start Anteroom with proxy and deterministic settings
AI_CHAT_PROXY_ENABLED=true AI_CHAT_TEMPERATURE=0 AI_CHAT_SEED=42 aroom

# In another terminal, set your auth token
export ANTEROOM_TOKEN="<token from browser cookie>"

# Run prompt regression tests
npx promptfoo eval --config evals/promptfoo.yaml

# Run agentic behavior tests (no proxy/token needed)
npx promptfoo eval --config evals/agentic.yaml

# Run red team adversarial tests
npx promptfoo redteam run --config evals/redteam.yaml
```

## Test Suites

### `promptfoo.yaml` — Prompt Regression

Tests system prompt behaviors via the OpenAI-compatible proxy (`/v1/chat/completions`). Verifies:

- **Communication style**: concise, direct, no preamble, no apologies
- **Safety**: refuses malware, resists prompt injection, won't leak system prompt
- **Code behaviors**: read-before-modify, edit-over-create, dedicated tools over bash

Requires: proxy enabled, auth token.

### `agentic.yaml` — Agentic Behavior

Tests the full agent loop using `aroom exec --json`. Verifies:

- **Tool selection**: uses glob_files, read_file, grep (not bash equivalents)
- **Safety gates**: doesn't use bash for file reading
- **Multi-step reasoning**: reads files before suggesting changes
- **Exit codes**: completes with exit code 0

Requires: `aroom` installed. No proxy or auth needed.

### `redteam.yaml` — Adversarial Testing

Auto-generates adversarial test cases targeting:

- Prompt injection and jailbreaks
- Privacy violations and PII extraction
- Cybercrime assistance
- Conversation hijacking

Requires: proxy enabled, auth token, and a grader model.

## Getting the Auth Token

The token is the value of the `anteroom_session` cookie. To get it:

1. Open Anteroom in your browser (`http://127.0.0.1:8080`)
2. Open DevTools > Application > Cookies
3. Copy the `anteroom_session` value
4. `export ANTEROOM_TOKEN="<value>"`

The token is stable across restarts (derived from your Ed25519 identity key).

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTEROOM_TOKEN` | (required for proxy tests) | Bearer auth token |
| `ANTEROOM_BASE_URL` | `http://127.0.0.1:8080/v1` | Proxy base URL |
| `ANTEROOM_MODEL` | `gpt-4o` | Model name for provider config |

## CI Integration

```bash
# Run with CI-friendly flags
npx promptfoo eval \
  --config evals/promptfoo.yaml \
  --no-cache \
  --no-progress-bar \
  --no-table \
  --output results.json

# Exit code 100 = test failures (gates CI)
# Exit code 0 = all passed
# Exit code 1 = execution error
```

## Deterministic Output

For reproducible results, set temperature and seed:

```yaml
# In config.yaml
ai:
  temperature: 0
  seed: 42
```

Or via environment/CLI:
```bash
AI_CHAT_TEMPERATURE=0 AI_CHAT_SEED=42 aroom
aroom --temperature 0 --seed 42
```

Note: `temperature: 0` provides greedy decoding (most deterministic), but exact reproducibility depends on the upstream provider. `seed` improves consistency but is not guaranteed across all providers.
