# Tutorial: Install Your First Pack

Package existing skill and rule files into a pack, install it, and verify it works in both CLI and web UI.

## Prerequisites

- Anteroom installed (`pip install anteroom`)
- An existing skill or rule file (or we'll create one)

## Step 1: Create the Pack Structure

```bash
$ mkdir -p my-pack/skills my-pack/rules
```

## Step 2: Add a Skill

```yaml title="my-pack/skills/explain.yaml"
name: explain
description: Explain code in plain language
prompt: |
  Explain this code in plain, non-technical language.
  Focus on what it does, not how.

  {args}
```

## Step 3: Add a Rule

```markdown title="my-pack/rules/style-guide.md"
# Style Guide

- Use descriptive variable names (no single letters except loop indices)
- Prefer early returns to reduce nesting
- Maximum function length: 30 lines
```

## Step 4: Write the Manifest

```yaml title="my-pack/pack.yaml"
name: my-pack
namespace: personal
version: "1.0.0"
description: My personal development standards
artifacts:
  - type: skill
    name: explain
  - type: rule
    name: style-guide
```

## Step 5: Install

```bash
$ aroom pack install my-pack/
```

```
Installed pack personal/my-pack v1.0.0 (2 artifacts)
```

## Step 6: Verify

```bash
# Check the pack is listed
$ aroom pack list
personal/my-pack  v1.0.0  2 artifacts

# Check artifacts are registered
$ aroom artifact list
@personal/skill/explain      skill  project  v1
@personal/rule/style-guide   rule   project  v1
```

## Step 7: Use in CLI

```bash
$ aroom chat
```

```
> /explain def fib(n): return n if n < 2 else fib(n-1) + fib(n-2)
```

The AI explains the code using your skill's prompt. The style guide rule is automatically injected into every turn — no `/` command needed.

## Step 8: Verify in Web UI

```bash
$ aroom
```

Open `http://127.0.0.1:8080`. The rule is active on every conversation. Skills from packs are available to the AI via the `invoke_skill` tool.

## Project-Scoped Installation

To make the pack available to teammates who clone the repo:

```bash
$ aroom pack install my-pack/ --project
```

This copies the pack to `.anteroom/packs/personal/my-pack/`. Add it to version control:

```bash
$ git add .anteroom/packs/
$ git commit -m "chore: add personal dev standards pack"
```

## What Gets Committed

When using `--project`, these files are created in your project:

```
.anteroom/
├── packs/
│   └── personal/
│       └── my-pack/
│           ├── pack.yaml
│           ├── skills/
│           │   └── explain.yaml
│           └── rules/
│               └── style-guide.md
└── anteroom.lock.yaml    # if lock file generation is enabled
```

## Next Steps

- [Create a Pack from Scratch](create-pack-from-scratch.md) — build a multi-artifact pack
- [Share a Pack via Git](share-pack-via-git.md) — distribute to your team
