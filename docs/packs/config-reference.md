# Config Reference

Pack source configuration lives in your Anteroom config file under the `pack_sources` key.

## Configuration

```yaml title="~/.anteroom/config.yaml"
pack_sources:
  - url: https://github.com/acme/anteroom-packs.git
    branch: main
    refresh_interval: 30

  - url: git@github.com:acme/private-packs.git
    branch: release
    refresh_interval: 0  # manual only
```

## PackSourceConfig Fields

| Field | Type | Default | Env Override | Description |
|-------|------|---------|-------------|-------------|
| `url` | string | (required) | — | Git remote URL. Accepts `https://`, `ssh://`, `git://`, and SSH shorthand (`git@host:path`). Rejects `http://`, `ext::`, and `file://` |
| `branch` | string | `"main"` | — | Git branch to clone and track |
| `refresh_interval` | int | `30` | — | Minutes between automatic refreshes. Set to `0` to disable auto-refresh (manual only). Minimum: 5 minutes (values below 5 are clamped) |
| `auto_attach` | bool | `true` | — | Automatically attach new packs from this source on install. Set to `false` for opt-in attachment via `aroom pack attach` |
| `priority` | int | `50` | — | Conflict resolution priority (1-100). Lower number wins when multiple sources provide conflicting packs |

## Examples

### Single Public Source

```yaml
pack_sources:
  - url: https://github.com/acme/anteroom-packs.git
```

Uses all defaults: `main` branch, 30-minute refresh.

### Multiple Sources with Different Intervals

```yaml
pack_sources:
  - url: https://github.com/acme/standards.git
    branch: main
    refresh_interval: 60  # hourly

  - url: git@github.com:acme/security-packs.git
    branch: production
    refresh_interval: 5  # every 5 minutes

  - url: https://github.com/acme/experimental.git
    branch: dev
    refresh_interval: 0  # manual only
```

### Team Config Distribution

In a team config file, pack sources ensure all team members use the same packs:

```yaml title="team-config.yaml"
pack_sources:
  - url: https://github.com/acme/team-packs.git
    branch: main
    refresh_interval: 30

enforce:
  - pack_sources  # prevent personal config from overriding
```

## Related Configuration

| Config Section | Relevance |
|---------------|-----------|
| [Safety](../security/tool-safety.md) | Config overlays from packs can modify safety settings |
| [Skills](../cli/skills.md) | Skill artifacts from packs are registered in the skill registry |
| [MCP Servers](../configuration/config-file.md#mcp) | mcp_server artifacts configure MCP connections |
| [Team Config](../configuration/team-config.md) | Team configs can enforce pack sources |

## Next Steps

- [Pack Sources](pack-sources.md) — detailed pack source behavior and lifecycle
- [API Reference](api-reference.md) — HTTP endpoints for pack management
