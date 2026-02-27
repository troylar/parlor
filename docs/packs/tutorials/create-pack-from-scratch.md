# Tutorial: Create a Pack from Scratch

Build a realistic Python conventions pack with 2 skills, 2 rules, 1 instruction, and 1 config overlay. Test each artifact individually.

## What We're Building

A `python-conventions` pack that enforces Acme's Python development standards:

| Artifact | Type | Purpose |
|----------|------|---------|
| `commit` | skill | Generate properly formatted commit messages |
| `review` | skill | Review code for quality and security |
| `coding-standards` | rule | Enforce Python style rules on every turn |
| `security-policy` | rule | Enforce security patterns on every turn |
| `stack-overview` | instruction | Provide project context to the agent |
| `safety` | config_overlay | Set approval mode to `ask_for_writes` |

## Step 1: Create Directory Structure

```bash
$ mkdir -p python-conventions/{skills,rules,instructions,config_overlays}
```

## Step 2: Write the Skills

```yaml title="python-conventions/skills/commit.yaml"
name: commit
description: Create a conventional commit message
prompt: |
  Review the staged changes and create a commit message following
  conventional commits format: type(scope): description

  Types: feat, fix, docs, refactor, test, chore
  Scope: the primary module changed
  Description: imperative mood, lowercase, no period

  {args}
```

```yaml title="python-conventions/skills/review.yaml"
name: review
description: Review code for quality and security
prompt: |
  Review this code for:
  1. Python style (PEP 8, type hints, docstrings for public APIs)
  2. Security (OWASP Top 10, injection, auth issues)
  3. Test coverage gaps
  4. Performance concerns

  Be specific. Reference line numbers. Suggest fixes.

  {args}
```

## Step 3: Write the Rules

```markdown title="python-conventions/rules/coding-standards.md"
# Python Coding Standards

- All functions must have type hints for parameters and return values
- Use `from __future__ import annotations` for forward references
- Prefer `pathlib.Path` over `os.path`
- Use f-strings for string formatting (never `%` or `.format()`)
- Maximum line length: 120 characters
- Use `dataclass` or `NamedTuple` for data containers, not plain dicts
- Prefer early returns to reduce nesting depth
```

```markdown title="python-conventions/rules/security-policy.md"
# Security Policy

- Never use `eval()`, `exec()`, or `compile()` with user input
- Always use parameterized queries for database access
- Validate all input at system boundaries
- Never log secrets, tokens, or passwords
- Use `secrets` module for random token generation, not `random`
- File uploads: validate MIME type, extension, and size
```

## Step 4: Write the Instruction

```markdown title="python-conventions/instructions/stack-overview.md"
# Stack Overview

This is a Python 3.11+ project using:
- FastAPI for the web API
- SQLAlchemy 2.0 with async sessions
- PostgreSQL 16
- pytest with async support
- ruff for linting, black for formatting
- mypy in strict mode

Directory layout:
- `src/app/` — FastAPI application
- `src/models/` — SQLAlchemy models
- `src/services/` — Business logic
- `tests/` — pytest test suite
```

## Step 5: Write the Config Overlay

```yaml title="python-conventions/config_overlays/safety.yaml"
safety:
  approval_mode: ask_for_writes
  bash_sandbox:
    allow_network: false
    allow_package_install: false
```

## Step 6: Write the Manifest

```yaml title="python-conventions/pack.yaml"
name: python-conventions
namespace: acme
version: "1.0.0"
description: Acme Python team development standards and tooling
artifacts:
  - type: skill
    name: commit
  - type: skill
    name: review
  - type: rule
    name: coding-standards
  - type: rule
    name: security-policy
  - type: instruction
    name: stack-overview
  - type: config_overlay
    name: safety
```

## Step 7: Verify Directory Layout

```
python-conventions/
├── pack.yaml
├── skills/
│   ├── commit.yaml
│   └── review.yaml
├── rules/
│   ├── coding-standards.md
│   └── security-policy.md
├── instructions/
│   └── stack-overview.md
└── config_overlays/
    └── safety.yaml
```

## Step 8: Install

```bash
$ aroom pack install python-conventions/
```

```
Installed pack acme/python-conventions v1.0.0 (6 artifacts)
```

## Step 9: Verify Each Artifact

```bash
# List all artifacts from this pack
$ aroom artifact list --namespace acme
@acme/skill/commit              skill           project  v1
@acme/skill/review              skill           project  v1
@acme/rule/coding-standards     rule            project  v1
@acme/rule/security-policy      rule            project  v1
@acme/instruction/stack-overview instruction     project  v1
@acme/config_overlay/safety     config_overlay  project  v1

# Inspect a specific artifact
$ aroom artifact show @acme/skill/commit
```

## Step 10: Test the Skills

```bash
$ aroom chat
```

```
> /commit
```

The agent reviews staged changes and produces a conventional commit message following your template.

```
> /review src/app/auth.py
```

The agent reviews the file against your quality and security criteria.

## Step 11: Run Health Check

```bash
$ aroom artifact check
```

```
Artifacts: 6  Packs: 1  Size: 2.1 KB  Tokens: ~525

Issues:
  INFO  bloat  6 artifacts, 2.1 KB total, ~525 estimated tokens

Healthy: yes (0 errors, 0 warnings, 1 info)
```

## Next Steps

- [Share a Pack via Git](share-pack-via-git.md) — publish this pack for your team
- [Team Standardization](team-standardization.md) — enforce pack usage across a team
