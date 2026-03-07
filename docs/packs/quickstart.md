# Quickstart

Use an existing pack in 2 minutes, or create your own in 5.

## Use a Pack

Anteroom ships with example packs you can install immediately.

### Step 1: Install an Example Pack

```bash
# Find the built-in pack path using the public API
PACK_PATH=$(python -c "from anteroom.services.starter_packs import get_built_in_pack_path; print(get_built_in_pack_path('code-review'))")

# Install and attach
aroom pack install "$PACK_PATH" --attach
```

### Step 2: See What You Got

```bash
$ aroom pack list
```

```
anteroom/code-review  v1.0.0  4 artifacts
```

```bash
$ aroom artifact list --type skill
```

```
@anteroom/skill/review     skill  project  v1
@anteroom/skill/changelog  skill  project  v1
```

### Step 3: Use It

```bash
$ aroom chat
```

```
> /review @src/auth.py
```

The AI receives the pack's review skill prompt with your file contents.

### Available Example Packs

| Pack | What It Contains |
|------|-----------------|
| `code-review` | 2 skills (review, changelog) + 2 rules (coding standards) |
| `writing-assistant` | 3 skills (summarize, rewrite, proofread) |
| `strict-safety` | 2 rules (no-destructive-commands, confirm-before-deploy) + 1 config overlay |

These are separate from the **starter packs** (`python-dev`, `security-baseline`) which auto-install at first run. Example packs are opt-in.

---

## Create a Pack

Build your own pack when you want custom skills, rules, or config for your team.

### Step 1: Create a Pack Directory

```bash
$ mkdir -p my-first-pack/skills
```

### Step 2: Write the Manifest

```yaml title="my-first-pack/pack.yaml"
name: my-first-pack
namespace: demo
version: "1.0.0"
description: My first Anteroom pack
artifacts:
  - type: skill
    name: explain
```

### Step 3: Create a Skill

```yaml title="my-first-pack/skills/explain.yaml"
name: explain
description: Explain code in plain language
prompt: |
  Explain the following code in plain, non-technical language.
  Focus on what it does, not how it does it.

  {args}
```

Your directory should look like:

```
my-first-pack/
├── pack.yaml
├── skills/
│   └── explain.yaml
```

### Step 4: Install the Pack

```bash
$ aroom pack install my-first-pack/
```

Expected output:

```
Installed pack demo/my-first-pack v1.0.0 (1 artifact)
```

### Step 5: Use It

```bash
$ aroom chat
```

```
> /explain def fib(n): return n if n < 2 else fib(n-1) + fib(n-2)
```

### Alternative: AI-Guided Creation

Use the `/new-pack` skill in the REPL for an interactive experience:

```
> /new-pack security rules for our Python projects
```

The AI walks you through choosing a namespace, designing artifacts, creating files, and validating the manifest. After creating a pack, use `/pack-lint` to validate it before install.

### Project-Scoped Installation

To copy the pack into your project's `.anteroom/packs/` directory (for version control):

```bash
$ aroom pack install my-first-pack/ --project
```

This copies the pack to `.anteroom/packs/demo/my-first-pack/` so teammates get the same artifacts when they clone the repo.

## Next Steps

- [Create a Pack from Scratch](tutorials/create-pack-from-scratch.md) &mdash; build a realistic multi-artifact pack
- [How Packs Work](how-packs-work.md) &mdash; deep dive into the full lifecycle
- [Manifest Format](manifest-format.md) &mdash; all manifest fields and options
- [Pack Commands](pack-commands.md) &mdash; full CLI reference
