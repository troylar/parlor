# Quickstart

Create, install, and use a pack in 5 minutes.

## Step 1: Create a Pack Directory

```bash
$ mkdir -p my-first-pack/skills
```

## Step 2: Write the Manifest

```yaml title="my-first-pack/pack.yaml"
name: my-first-pack
namespace: demo
version: "1.0.0"
description: My first Anteroom pack
artifacts:
  - type: skill
    name: explain
```

## Step 3: Create a Skill

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

## Step 4: Install the Pack

```bash
$ aroom pack install my-first-pack/
```

Expected output:

```
Installed pack demo/my-first-pack v1.0.0 (1 artifact)
```

## Step 5: Verify Installation

List installed packs:

```bash
$ aroom pack list
```

```
demo/my-first-pack  v1.0.0  1 artifact
```

List artifacts:

```bash
$ aroom artifact list --type skill
```

```
@demo/skill/explain  skill  project  v1
```

## Step 6: Use It

Start the CLI REPL:

```bash
$ aroom chat
```

Type `/explain` followed by code:

```
> /explain def fib(n): return n if n < 2 else fib(n-1) + fib(n-2)
```

The AI receives your skill's prompt with the code substituted for `{args}`.

## What Happened

1. `aroom pack install` parsed `pack.yaml` and validated the manifest
2. It read `skills/explain.yaml` and stored it as an artifact in the database
3. It created a pack record linking the artifact to `demo/my-first-pack`
4. The `ArtifactRegistry` loaded the artifact at `source=project` precedence
5. The `SkillRegistry` detected the new skill and made it available as `/explain`

## Project-Scoped Installation

To copy the pack into your project's `.anteroom/packs/` directory (for version control):

```bash
$ aroom pack install my-first-pack/ --project
```

This copies the pack directory to `.anteroom/packs/demo/my-first-pack/` so teammates get the same artifacts when they clone the repo.

## Alternative: AI-Guided Creation

Instead of creating pack files manually, use the `/new-pack` skill in the REPL for an interactive, AI-guided experience:

```
> /new-pack security rules for our Python projects
```

The AI walks you through choosing a namespace, designing artifacts, creating files, and validating the manifest. After creating a pack, use `/pack-lint` to validate it before install.

## Next Steps

- [Create a Pack from Scratch](tutorials/create-pack-from-scratch.md) — build a realistic multi-artifact pack
- [Manifest Format](manifest-format.md) — all manifest fields and options
- [Pack Commands](pack-commands.md) — full CLI reference
