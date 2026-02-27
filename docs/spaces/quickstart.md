# Quickstart

Create and use your first space in 5 minutes.

## 1. Create a Space File

Create a YAML file in the spaces directory:

```bash
mkdir -p ~/.anteroom/spaces
```

```yaml title="~/.anteroom/spaces/my-project.yaml"
name: my-project
version: "1"

repos:
  - https://github.com/yourorg/your-repo.git

instructions: |
  You are working on the My Project codebase.
  Follow PEP 8 for Python code.
  Write tests for all new functions.

config:
  ai:
    model: gpt-4o
```

## 2. Register the Space

```bash
$ aroom space create ~/.anteroom/spaces/my-project.yaml
Created space: my-project (id: a1b2c3d4...)
```

## 3. Clone Repos

```bash
$ aroom space clone my-project
Repos root directory
  [/home/dev/.anteroom/spaces/my-project/repos]:
  Saved repos root to /home/dev/.anteroom/spaces/my-project.local.yaml
  OK   https://github.com/yourorg/your-repo.git → /home/dev/.anteroom/spaces/my-project/repos/your-repo
```

Press Enter to accept the default repos root, or type a custom path.

## 4. Start Chatting

Navigate to a cloned repo directory and start a chat:

```bash
$ cd ~/.anteroom/spaces/my-project/repos/your-repo
$ aroom chat
Space: my-project
>
```

Anteroom auto-detects the space from your working directory. Your instructions are injected into the system prompt, and the configured model is used.

## 5. Verify

Check the active space in the REPL:

```
> /space show
my-project
  File:  /home/dev/.anteroom/spaces/my-project.yaml
  Convs: 1
  Paths:
    https://github.com/yourorg/your-repo.git → /home/dev/.anteroom/spaces/my-project/repos/your-repo
```

## What Happened

1. **Space file** defines what repos, instructions, and config the workspace uses
2. **`aroom space create`** registered the space in the database and computed a file hash
3. **`aroom space clone`** cloned the repos and saved their local paths
4. **`aroom chat`** auto-detected the space from your working directory
5. **Instructions** were injected into the system prompt
6. **Config overrides** (model) were applied

## Next Steps

- **Add more repos**: Edit the YAML and run `aroom space clone my-project` again
- **Map existing directories**: `aroom space map my-project /path/to/existing/repo`
- **Link sources**: Add files or URLs for RAG context (see [Concepts](concepts.md#sources))
- **Add packs**: Activate team packs for consistent rules and skills
- **Edit instructions**: Update the YAML and run `/space refresh` to reload

## Quick Reference

| Task | Command |
|------|---------|
| List spaces | `aroom space list` |
| Show details | `aroom space show my-project` |
| Switch space | `/space switch other-project` |
| Refresh after edits | `/space refresh` |
| Clear space | `/space clear` |
| Delete space | `aroom space delete my-project` |

For the full command reference, see [CLI Commands](commands.md).
