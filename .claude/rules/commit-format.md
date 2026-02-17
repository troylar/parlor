# Commit Message Format

Every commit in this repository MUST follow this format:

```
type(scope): description (#issue)
```

## Rules

- **type** must be one of: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`
- **scope** must be a module name: `cli`, `tools`, `routers`, `services`, `db`, `config`, `app`, `tls`, `identity`, `static`, `models`, `embeddings`, `mcp`, `search`, `canvas`
- **description** is lowercase, imperative mood, no period at the end
- **#issue** is a valid GitHub issue number — every commit MUST reference one

## Examples

Good:
- `feat(tools): add canvas patch tool (#83)`
- `fix(routers): handle missing conversation in chat endpoint (#91)`
- `test(services): add agent loop timeout tests (#88)`
- `docs: update CLAUDE.md architecture section (#95)`

Bad:
- `fixed bug` (no type, scope, or issue)
- `feat: add new feature` (missing issue reference)
- `feat(tools): Add Canvas Tool (#83)` (capitalized description)

## Scope Exception

`docs` type may omit scope when the change is project-wide (e.g., README, CLAUDE.md).

## When Creating Commits

Before running `git commit`, verify:
1. The message matches `type(scope): description (#issue)` format
2. The issue number exists: `gh issue view <N> --json state`
3. The scope matches the primary module being changed
4. The type accurately reflects the change (feat = new, fix = bug, etc.)

If the user provides a commit message that doesn't match this format, reformat it. If no issue number is provided, ask for one — do not commit without it.
