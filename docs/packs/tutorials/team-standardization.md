# Tutorial: Team Standardization

Use team config + packs + config overlays to enforce development standards across a team. Walk through onboarding a new developer from scratch.

## The Scenario

Acme has 10 developers. They need:

- Consistent commit message format
- Security rules enforced on all AI interactions
- A locked-down safety mode (`ask_for_writes`)
- Standard MCP tool config
- All delivered via a single team config — no per-developer setup

## Step 1: Create the Team Pack Repository

```bash
$ mkdir -p acme-packs/acme-standards/{skills,rules,config_overlays}
```

```yaml title="acme-packs/acme-standards/pack.yaml"
name: acme-standards
namespace: acme
version: "1.0.0"
description: Acme mandatory development standards
artifacts:
  - type: skill
    name: commit
  - type: rule
    name: security-policy
  - type: config_overlay
    name: safety-defaults
```

```yaml title="acme-packs/acme-standards/skills/commit.yaml"
name: commit
description: Acme commit format
prompt: |
  Create a commit message following Acme's format:
  type(scope): description (#issue)

  Types: feat, fix, docs, refactor, test, chore
  Always reference the issue number.

  {args}
```

```markdown title="acme-packs/acme-standards/rules/security-policy.md"
# Acme Security Policy

- All database queries use parameterized placeholders
- Never use eval(), exec(), or compile() with user input
- All API endpoints require authentication
- Sensitive data encrypted at rest
- No hardcoded secrets — use environment variables
```

```yaml title="acme-packs/acme-standards/config_overlays/safety-defaults.yaml"
safety:
  approval_mode: ask_for_writes
  bash_sandbox:
    allow_network: false
    allow_package_install: false
    execution_timeout: 30
```

Push to git:

```bash
$ cd acme-packs
$ git init && git add . && git commit -m "feat: acme standards pack"
$ git remote add origin https://github.com/acme/anteroom-packs.git
$ git push -u origin main
```

## Step 2: Create the Team Config

```yaml title="acme-team-config.yaml"
# Pack sources — auto-install team packs
pack_sources:
  - url: https://github.com/acme/anteroom-packs.git
    branch: main
    refresh_interval: 30

# Enforce these fields — personal config cannot override
enforce:
  - pack_sources
  - safety.approval_mode
  - safety.bash_sandbox.allow_network
```

Distribute this file to team members (shared drive, config management, etc.).

## Step 3: Onboard a New Developer

The new developer needs three things:

### 1. Install Anteroom

```bash
$ pip install anteroom
```

### 2. Place the Team Config

```bash
$ mkdir -p ~/.anteroom
$ cp /path/to/acme-team-config.yaml ~/.anteroom/team-config.yaml
```

Or set via environment variable:

```bash
$ export AI_CHAT_TEAM_CONFIG=/path/to/acme-team-config.yaml
```

### 3. Start Anteroom

```bash
$ aroom chat
```

On first start:

1. Anteroom loads the team config
2. Clones the pack source repository
3. Installs `acme/acme-standards` pack (3 artifacts)
4. The security rule is active, the commit skill is available, and the safety config overlay is applied

### 4. Verify

```bash
$ aroom pack list
acme/acme-standards  v1.0.0  3 artifacts

$ aroom artifact list --namespace acme
@acme/skill/commit              skill           project  v1
@acme/rule/security-policy      rule            project  v1
@acme/config_overlay/safety-defaults  config_overlay  project  v1
```

The new developer has all team standards without editing a single config file.

## Step 4: Validate in CI

Add a CI step to ensure pack state is consistent:

```yaml title=".github/workflows/validate.yml"
- name: Validate pack health
  run: |
    pip install anteroom
    aroom artifact check --project --json | jq -e '.healthy'
```

## What's Enforced vs Configurable

| Setting | Enforced | Can Override |
|---------|----------|-------------|
| `pack_sources` | Yes | No — all devs use the same packs |
| `safety.approval_mode` | Yes | No — always `ask_for_writes` |
| `safety.bash_sandbox.allow_network` | Yes | No — network disabled |
| `ai.model` | No | Yes — devs can choose their model |
| `cli.compact_threshold` | No | Yes — personal preference |

## Updating Standards

When the team updates standards:

1. Edit the pack files in the git repo
2. Bump the version in `pack.yaml`
3. Push to git

All team members receive the update within 30 minutes (the configured `refresh_interval`).

## Next Steps

- [Automatic Updates](automatic-updates.md) — tune refresh behavior
- [CI/CD Integration](ci-cd-integration.md) — validate packs in pipelines
