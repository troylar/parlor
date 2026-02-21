# Skills

Skills are reusable prompt templates invoked with `/name` in the REPL.

## Built-in Skills

Four skills ship by default:

| Skill | What it does |
|---|---|
| `/commit` | Runs `git diff`, stages relevant files, creates a conventional commit |
| `/review` | Reviews `git diff` for bugs, security issues, performance, error handling, missing tests |
| `/explain` | Reads referenced code and explains architecture, data flow, components, design patterns |
| `/docs` | Look up Anteroom documentation — config, CLI, tools, skills, architecture |

## Using Skills

Type the skill name at the prompt:

```
you> /commit
you> /review just the auth changes
you> /explain @src/services/agent_loop.py focus on the event system
```

Anything after the skill name is appended to the skill's prompt template as extra context.

## Custom Skills

Add YAML files to create your own skills:

- `~/.anteroom/skills/` --- global skills (available everywhere)
- `.anteroom/skills/` --- project-specific skills (available only in that project)

### YAML Format

```yaml
name: test
description: Run tests and fix failures
prompt: |
  Run the test suite. If any tests fail, read the failing test
  and the relevant source code, then fix the issue.
```

```yaml
name: deploy
description: Build and deploy to staging
prompt: |
  1. Run the full test suite
  2. Build the production bundle
  3. Deploy to the staging environment
  4. Verify the deployment is healthy
```

### Fields

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Skill name (used as the `/command`) |
| `description` | Yes | Short description shown in `/skills` list |
| `prompt` | Yes | Prompt template sent to the AI |

## Precedence

Project skills (`.anteroom/skills/`) override global skills (`~/.anteroom/skills/`), which override default built-in skills.

Anteroom walks up from the working directory to find the nearest `.anteroom/skills/` directory, similar to how [ANTEROOM.md](project-instructions.md) is discovered.

## Listing Skills

Use `/skills` to see all available skills with their descriptions and source (default, global, or project):

```
you> /skills
```
