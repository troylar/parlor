# Quickstart

Create and use your first space in 5 minutes.

## 1. Navigate to Your Project

```bash
cd ~/projects/my-project
```

## 2. Create a Space

Use `space create` with a name, or `space init` to derive the name from the directory:

```bash
# Option A: explicit name
$ aroom space create my-project
Created space: my-project
  Space file: /home/dev/projects/my-project/.anteroom/space.yaml

# Option B: derive name from directory (zero-arg)
$ aroom space init
Created space: my-project
  Space file: /home/dev/projects/my-project/.anteroom/space.yaml
```

This creates a `.anteroom/space.yaml` file in your current directory with a self-documenting YAML template.

## 3. Edit the Template

The generated space file contains commented examples for every section. Uncomment and edit what you need:

```yaml title=".anteroom/space.yaml"
name: my-project
version: "1"

# repos:
#   - https://github.com/yourorg/your-repo.git

instructions: |
  You are working on the My Project codebase.
  Follow PEP 8 for Python code.
  Write tests for all new functions.

# config:
#   ai:
#     model: gpt-4o
```

## 4. Start Chatting

Start a chat from anywhere within your project directory:

```bash
$ aroom chat
Space: my-project
>
```

Anteroom auto-detects the space from your working directory (walking up parent directories). Your instructions are injected into the system prompt, and any configured model is used.

## 5. Verify

Check the active space in the REPL:

```
> /space show
my-project
  File:  /home/dev/projects/my-project/.anteroom/space.yaml
  Origin: local
  Convs: 1
```

## What Happened

1. **`aroom space create`** (or `space init`) created a local `.anteroom/space.yaml` file in your project directory
2. **The template** is self-documenting with commented sections you can enable
3. **`aroom chat`** auto-detected the space from your working directory
4. **Instructions** were injected into the system prompt
5. **Config overrides** (if any) were applied

## Next Steps

- **Add repos**: Uncomment the `repos` section, then run `aroom space clone my-project`
- **Map existing directories**: `aroom space map my-project /path/to/existing/repo`
- **Link sources**: Add files or URLs for RAG context (see [Concepts](concepts.md#sources))
- **Add packs**: Activate team packs for consistent rules and skills
- **Edit instructions**: Update the YAML and run `/space refresh` to reload

## Quick Reference

| Task | Command |
|------|---------|
| Create space (named) | `aroom space create <name>` |
| Create space (auto-name) | `aroom space init` |
| List spaces | `aroom space list` |
| Show details | `aroom space show my-project` |
| Switch space | `/space switch other-project` |
| Refresh after edits | `/space refresh` |
| Clear space | `/space clear` |
| Delete space | `aroom space delete my-project` |

For the full command reference, see [CLI Commands](commands.md).
