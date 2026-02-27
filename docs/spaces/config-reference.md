# Config Reference

The `config` section of a space file accepts any field from Anteroom's configuration hierarchy. This page lists the most commonly used fields for space overrides.

For the complete configuration reference, see the main [Configuration documentation](../configuration/index.md).

## AI Settings

```yaml
config:
  ai:
    model: gpt-4o
    temperature: 0.7
    top_p: 0.9
    max_tools: 64
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | `string` | (from personal) | Model identifier |
| `temperature` | `float \| null` | `null` | Sampling temperature (null = provider default) |
| `top_p` | `float \| null` | `null` | Nucleus sampling (null = provider default) |
| `seed` | `int \| null` | `null` | Random seed for reproducibility |
| `max_tools` | `int` | `128` | Maximum tools per request |

## Safety Settings

```yaml
config:
  safety:
    approval_mode: ask
    allowed_tools:
      - read_file
      - grep
    read_only: true
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `approval_mode` | `string` | `ask_for_writes` | `auto`, `ask_for_dangerous`, `ask_for_writes`, `ask` |
| `allowed_tools` | `list[string]` | `[]` | Tools pre-approved without prompting |
| `denied_tools` | `list[string]` | `[]` | Tools blocked entirely |
| `read_only` | `bool` | `false` | Block all write operations |

## CLI Settings

```yaml
config:
  cli:
    verbose: true
    compact_threshold: 100000
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `verbose` | `bool` | `false` | Show detailed output |
| `compact_threshold` | `int` | `120000` | Token count that triggers auto-compaction |

## Planning Settings

```yaml
config:
  planning:
    auto_mode: suggest
    auto_threshold_tools: 3
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `auto_mode` | `string` | `off` | `off`, `suggest`, `auto` |
| `auto_threshold_tools` | `int` | `5` | Tool count that triggers plan suggestion |

## RAG Settings

```yaml
config:
  rag:
    max_chunks: 15
    max_tokens: 3000
    similarity_threshold: 0.6
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_chunks` | `int` | `10` | Maximum context chunks per query |
| `max_tokens` | `int` | `2000` | Token budget for RAG context |
| `similarity_threshold` | `float` | `0.5` | Minimum cosine similarity for chunk inclusion |

## Embeddings Settings

```yaml
config:
  embeddings:
    enabled: true
    provider: local
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool \| null` | `null` | `null` = auto-detect, `true` = force, `false` = disable |
| `provider` | `string` | `local` | `local` (fastembed) or `api` (OpenAI-compatible) |

## Sub-Agent Settings

```yaml
config:
  subagent:
    max_concurrency: 3
    max_total: 5
    max_depth: 2
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_concurrency` | `int` | `5` | Concurrent sub-agents |
| `max_total` | `int` | `10` | Total sub-agents per session |
| `max_depth` | `int` | `3` | Maximum nesting depth |
| `max_iterations` | `int` | `15` | Iterations per sub-agent |
| `timeout` | `int` | `120` | Seconds before timeout |

## Example: Secure Team Space

A space for a security-conscious team:

```yaml
name: secure-team
version: "1"

instructions: |
  Follow OWASP ASVS Level 2 for all code.
  All database queries must use parameterized placeholders.
  No hardcoded secrets.

config:
  ai:
    model: gpt-4o
  safety:
    approval_mode: ask
    denied_tools:
      - bash
  subagent:
    max_depth: 1
    max_total: 3
```

## Example: Fast Prototyping Space

A space for rapid iteration:

```yaml
name: prototype
version: "1"

instructions: |
  Prioritize speed over polish.
  Use simple implementations first.

config:
  ai:
    model: gpt-4o-mini
    temperature: 0.8
  safety:
    approval_mode: auto
  planning:
    auto_mode: off
```

## Next Steps

- [Config Overlay](config-overlay.md) — how config layers merge
- [Space File Format](space-file-format.md) — full YAML reference
