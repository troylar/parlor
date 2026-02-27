# Config Overlay

Spaces can override Anteroom configuration. This page explains how space config merges with other configuration layers.

## Precedence Stack

Configuration is loaded in layers. Higher layers override lower layers:

```
env vars / CLI flags       ← highest priority
    ▼
project config
    ▼
space config               ← space overlay sits here
    ▼
personal config
    ▼
team config
    ▼
defaults                   ← lowest priority
```

**Key rule:** Team-enforced fields always win, regardless of where they're set.

## How Merging Works

The `config` section of your space file is deep-merged into the configuration stack after personal config but before project config.

### Deep Merge

Nested dicts are merged recursively, not replaced:

```yaml title="personal config (~/.anteroom/config.yaml)"
ai:
  model: gpt-3.5-turbo
  temperature: 0.7
cli:
  verbose: false
```

```yaml title="space config (~/.anteroom/spaces/my-space.yaml)"
config:
  ai:
    model: gpt-4o
```

**Result:**

```yaml
ai:
  model: gpt-4o           # ← space overrides
  temperature: 0.7         # ← personal preserved
cli:
  verbose: false           # ← personal preserved
```

The space only overrides `ai.model`. All other personal settings remain intact.

### Scalar Replacement

For non-dict values, the higher-precedence layer wins completely:

```yaml title="space config"
config:
  ai:
    temperature: 0.3
```

This replaces `temperature: 0.7` from personal config. There's no "merge" for scalar values — it's a simple override.

## Team Enforcement

Team configs can **enforce** fields that cannot be overridden by any other layer, including spaces:

```yaml title="team config"
ai:
  model: gpt-4o
enforce:
  - ai.model
```

With this team config, setting `ai.model` in a space file has no effect — the team-enforced value always wins. Enforcement is re-applied after each merge step.

### Enforcement Example

```yaml title="space config"
config:
  ai:
    model: gpt-3.5-turbo    # ignored — team enforces gpt-4o
    temperature: 0.5         # applied — not enforced
  safety:
    approval_mode: auto      # applied — not enforced
```

## Project Overrides Space

Project config has higher precedence than space config. If both set the same field, the project config wins:

```yaml title="space config"
config:
  ai:
    model: gpt-4o
```

```yaml title="project config (.anteroom/config.yaml)"
ai:
  model: claude-3-opus
```

**Result:** `model` is `claude-3-opus` (project wins over space).

This makes sense — project config is more specific than space config, just as space config is more specific than personal config.

## Common Patterns

### Team-Wide Model

Set a default model for all spaces:

```yaml title="space config"
config:
  ai:
    model: gpt-4o
```

Team members using this space get `gpt-4o` unless their project config or CLI flags override it.

### Stricter Safety for Sensitive Projects

```yaml title="space config"
config:
  safety:
    approval_mode: ask
    read_only: true
```

### Custom RAG Settings

```yaml title="space config"
config:
  rag:
    max_chunks: 20
    similarity_threshold: 0.6
```

### Planning Mode by Default

```yaml title="space config"
config:
  planning:
    auto_mode: suggest
    auto_threshold_tools: 3
```

## What You Can Set

Any field from `AppConfig` and its nested dataclasses. See [Config Reference](config-reference.md) for the complete list. Common sections:

| Section | What It Controls |
|---------|-----------------|
| `ai` | Model, API connection, timeouts, temperature |
| `safety` | Approval mode, allowed/denied tools, sandbox settings |
| `cli` | Compaction thresholds, verbosity |
| `planning` | Auto-plan triggers |
| `rag` | RAG pipeline parameters |
| `embeddings` | Embedding provider settings |

## Debugging

To see which config values are active and where they came from, use the introspect tool:

```
> What is the current AI model and approval mode?
```

The AI can use the `introspect` tool to examine runtime configuration, including which layer set each value.

## Next Steps

- [Config Reference](config-reference.md) — all configurable fields
- [Space File Format](space-file-format.md) — `config` field syntax
- [Concepts](concepts.md) — how spaces work end-to-end
