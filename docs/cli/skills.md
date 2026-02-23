# Skills

Skills are reusable prompt templates invoked with `/name` in the REPL. They let you define common workflows as single commands — commit helpers, code reviewers, deploy scripts, or any task you run repeatedly.

## Built-in Skills

Four skills ship by default:

| Skill | What it does |
|---|---|
| `/commit` | Runs `git diff`, stages relevant files, creates a conventional commit |
| `/review` | Reviews `git diff` for bugs, security issues, performance, error handling, missing tests |
| `/explain` | Reads referenced code and explains architecture, data flow, components, design patterns |
| `/a-help` | Look up Anteroom documentation — config layers, CLI, tools, skills, architecture |

Built-in skills are bundled in the package at `cli/default_skills/`. They can be overridden by user-defined skills with the same name.

## Using Skills

Type the skill name at the prompt:

```
you> /commit
you> /review just the auth changes
you> /explain @src/services/agent_loop.py focus on the event system
```

Anything after the skill name is appended to the skill's prompt template as extra context. For example, `/review just the auth changes` sends the review skill's full prompt plus `Additional context: just the auth changes`.

Use `/skills` to list all available skills with their source:

```
you> /skills
```

## Custom Skills

Create YAML files to define your own skills. Skills are loaded from three locations:

### Skill Directories

| Location | Scope | Priority |
|---|---|---|
| Built-in (`cli/default_skills/`) | All users | Lowest — overridden by any user skill |
| `~/.anteroom/skills/` | Global — all projects | Overrides built-in skills |
| `.anteroom/skills/` or `.claude/skills/` (project) | Project-specific | Highest — overrides global and built-in |

### Directory Equivalence

The `.anteroom` and `.claude` directories are interchangeable for skills. Anteroom checks both when walking up from the working directory:

- `.anteroom/skills/` — Anteroom's native directory
- `.claude/skills/` — Claude Code compatible directory

If both exist at the same directory level, `.anteroom/skills/` takes precedence (first match wins). The legacy `.parlor/skills/` directory is also supported for backward compatibility.

### Discovery

Project-level skills use **walk-up discovery**: Anteroom starts at the current working directory and walks up the directory tree, checking each level for a skills directory. The first match wins — it does not merge skills from multiple directory levels.

```
my-monorepo/
├── .anteroom/
│   └── skills/
│       └── deploy.yaml       ← Found for all subdirectories
├── service-a/
│   └── .anteroom/
│       └── skills/
│           └── test.yaml     ← Found when working in service-a/
└── service-b/
    └── src/                  ← Walk-up finds ../../.anteroom/skills/
```

Global skills at `~/.anteroom/skills/` are always loaded regardless of project skills.

### YAML Format

Each skill file is a single YAML document with three fields:

```yaml title="~/.anteroom/skills/test.yaml"
name: test
description: Run tests and fix failures
prompt: |
  Run the test suite. If any tests fail, read the failing test
  and the relevant source code, then fix the issue.
```

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Skill name (used as the `/command`). Defaults to the filename stem if omitted. |
| `description` | Yes | Short description shown in `/skills` list |
| `prompt` | Yes | Prompt template sent to the AI. Use YAML `|` for multi-line. |

### More Examples

```yaml title=".anteroom/skills/deploy.yaml"
name: deploy
description: Build and deploy to staging
prompt: |
  1. Run the full test suite
  2. Build the production bundle
  3. Deploy to the staging environment
  4. Verify the deployment is healthy
```

```yaml title="~/.anteroom/skills/security-review.yaml"
name: security-review
description: OWASP security review of recent changes
prompt: |
  Review the git diff for security issues against OWASP ASVS Level 2:
  - SQL injection (string concatenation in queries)
  - Command injection (shell=True with user input)
  - Path traversal (unsanitized file paths)
  - XSS (innerHTML with user input)
  - Hardcoded secrets
  - Missing input validation
  Report each finding with file, line, severity, and fix.
```

## Precedence

When multiple skills share the same name, the highest-priority source wins:

```
Built-in → Global (~/.anteroom/skills/) → Project (.anteroom/skills/ or .claude/skills/)
  lowest              middle                     highest
```

This means:
- A project skill named `commit` overrides the built-in `/commit`
- A global skill named `commit` also overrides the built-in
- A project skill overrides a global skill of the same name

### Load Warnings

If a skill file has errors (invalid YAML, missing `prompt` field, invalid format), Anteroom skips it and records a warning. Use `/skills` to see if any skills failed to load.

## How Skills Work Internally

When you type `/commit fix the auth bug`:

1. The REPL checks if `commit` is a registered skill name
2. If found, the skill's `prompt` is used as the message
3. The extra text (`fix the auth bug`) is appended as `Additional context: fix the auth bug`
4. The expanded prompt is sent to the AI as a normal message
5. The AI processes it like any other message — it can use tools, write files, run commands

Skills are purely prompt templates — they don't have special permissions or capabilities beyond what you'd get by typing the same text manually.

## Compatibility

The `.anteroom` and `.claude` directories are fully interchangeable for skills:

- A project using `.claude/skills/` works with Anteroom automatically
- A project using `.anteroom/skills/` follows the same behavior
- Walk-up discovery checks both `.anteroom/skills/` and `.claude/skills/` at each directory level
- `.anteroom/skills/` takes precedence over `.claude/skills/` if both exist at the same level
