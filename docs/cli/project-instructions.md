# Project Instructions (ANTEROOM.md)

Create a `ANTEROOM.md` in your project root to inject context into every CLI conversation.

## Example

```markdown title="ANTEROOM.md"
# Project: my-app

## Tech Stack
- Python 3.12, FastAPI, SQLAlchemy
- PostgreSQL 16, Redis 7

## Conventions
- All functions must have type hints
- Use conventional commits
- Tests required for all new features
```

## Discovery

Anteroom walks up from the current working directory to find the nearest `ANTEROOM.md`. A global `~/.anteroom/ANTEROOM.md` applies to all projects. Both are loaded if found --- global instructions come first, then project-specific ones.

## System Prompt Construction

The CLI builds a system prompt from three sources, concatenated in this order:

1. **Working directory context**: `"You are an AI coding assistant working in: /path/to/project"` + tool usage guidance
2. **ANTEROOM.md instructions** (global + project, if found)
3. **`system_prompt`** from `config.yaml`

## Best Practices

!!! tip
    Keep your `ANTEROOM.md` focused on information the AI needs to write correct code:

    - Tech stack and versions
    - Coding conventions and style rules
    - Testing patterns and requirements
    - File structure and naming conventions
    - Common patterns or idioms used in the project
