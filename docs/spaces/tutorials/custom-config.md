# Tutorial: Space with Custom Config

Override Anteroom's configuration per-space for different workflows.

## Scenario

You have two workspaces with different needs:

- **production-debug** — conservative settings for debugging production issues
- **prototype** — fast iteration with relaxed safety

## Production Debug Space

```yaml title="~/.anteroom/spaces/production-debug.yaml"
name: production-debug
version: "1"

repos:
  - https://github.com/acme/api-server.git

instructions: |
  You are debugging a production issue.

  Rules:
  - Do NOT modify production code directly
  - Read and analyze logs, code, and configs only
  - Suggest fixes but wait for explicit approval
  - Be conservative — prefer minimal, safe changes
  - Always consider rollback strategies

config:
  ai:
    model: gpt-4o
    temperature: 0.2
  safety:
    approval_mode: ask
    read_only: true
  planning:
    auto_mode: suggest
```

**What this does:**

- `temperature: 0.2` — more deterministic responses for analysis
- `approval_mode: ask` — ask before every tool use
- `read_only: true` — block all write operations
- `auto_mode: suggest` — suggest creating a plan before complex tasks

## Prototype Space

```yaml title="~/.anteroom/spaces/prototype.yaml"
name: prototype
version: "1"

instructions: |
  Rapid prototyping mode.
  Prioritize speed over polish.
  Use simple implementations first.
  Skip tests unless explicitly asked.

config:
  ai:
    model: gpt-4o-mini
    temperature: 0.8
  safety:
    approval_mode: auto
  planning:
    auto_mode: off
  subagent:
    max_total: 15
    max_concurrency: 8
```

**What this does:**

- `model: gpt-4o-mini` — cheaper, faster model for iteration
- `temperature: 0.8` — more creative responses
- `approval_mode: auto` — no prompts for tool approval
- `auto_mode: off` — skip planning, just do it
- Higher sub-agent limits for parallel work

## Switching Between Spaces

From the REPL:

```
> /space switch production-debug
Active space: production-debug
```

Config overrides from the new space take effect. However, some config values are applied at session start — for full config changes, exit and restart:

```bash
$ aroom chat --space prototype
```

## Config Precedence in Action

Your personal config:

```yaml title="~/.anteroom/config.yaml"
ai:
  model: gpt-3.5-turbo
  temperature: 0.7
safety:
  approval_mode: ask_for_writes
```

With `production-debug` active:

| Field | Personal | Space | Result |
|-------|----------|-------|--------|
| `model` | `gpt-3.5-turbo` | `gpt-4o` | **`gpt-4o`** (space wins) |
| `temperature` | `0.7` | `0.2` | **`0.2`** (space wins) |
| `approval_mode` | `ask_for_writes` | `ask` | **`ask`** (space wins) |
| `read_only` | (unset) | `true` | **`true`** (space adds) |

If your project also has a config:

```yaml title=".anteroom/config.yaml"
ai:
  model: claude-3-opus
```

Then `model` becomes `claude-3-opus` — project config overrides space config.

## Team Enforcement

If your team config enforces the model:

```yaml title="team config"
ai:
  model: gpt-4o
enforce:
  - ai.model
```

Then no space, project, or personal config can change the model. The team-enforced value always wins.

## Common Config Patterns

### Security-Focused

```yaml
config:
  safety:
    approval_mode: ask
    denied_tools:
      - bash
    read_only: true
```

### High-Performance

```yaml
config:
  ai:
    max_tools: 64
  subagent:
    max_concurrency: 8
    max_total: 20
    timeout: 300
```

### Minimal RAG

```yaml
config:
  rag:
    max_chunks: 5
    max_tokens: 1000
    similarity_threshold: 0.7
  embeddings:
    enabled: false
```

## Next Steps

- [Config Overlay](../config-overlay.md) — full precedence rules
- [Config Reference](../config-reference.md) — all configurable fields
- [Team Space](team-space.md) — share configs across a team
