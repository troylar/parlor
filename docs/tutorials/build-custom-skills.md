# Build Custom Skills

Create reusable prompt templates that integrate into the CLI REPL as `/commands`.

## Skill Format

Skills are YAML files with three fields:

```yaml
name: skill-name
description: Short description shown in /skills list
prompt: |
  The prompt template sent to the AI when the skill is invoked.
  This can be multi-line and reference tools, files, etc.
```

## Where to Put Skills

| Location | Scope | Priority |
|---|---|---|
| `.anteroom/skills/` | Project-specific | Highest |
| `~/.anteroom/skills/` | Global (all projects) | Medium |
| Built-in | Default skills | Lowest |

Project skills override global skills with the same name. Global skills override built-in skills.

Anteroom walks up from the working directory to find the nearest `.anteroom/skills/` directory, similar to [ANTEROOM.md](../cli/project-instructions.md) discovery.

## Example: Test Runner

```yaml title=".anteroom/skills/test.yaml"
name: test
description: Run tests and fix failures
prompt: |
  Run the test suite with pytest. If any tests fail:
  1. Read the failing test file
  2. Read the relevant source code
  3. Identify the root cause
  4. Fix the issue
  5. Re-run the tests to verify
```

Usage:

```
you> /test
you> /test just the auth module
```

## Example: Documentation Writer

```yaml title="~/.anteroom/skills/docs.yaml"
name: docs
description: Generate documentation for a module
prompt: |
  Read the specified source file(s) and generate comprehensive
  documentation including:
  - Module overview
  - Public API reference
  - Usage examples
  - Important implementation notes
```

## Example: Code Reviewer

```yaml title=".anteroom/skills/pr-review.yaml"
name: pr-review
description: Review staged changes like a PR
prompt: |
  Run `git diff --staged` and review the changes as if this were
  a pull request. Check for:
  - Bugs and logic errors
  - Security vulnerabilities
  - Performance issues
  - Missing error handling
  - Missing tests
  - Code style issues

  Format findings with severity (critical/warning/info) and
  specific file:line references.
```

## Skill Arguments

Anything typed after the skill name is appended to the prompt:

```
you> /test just the auth tests
you> /docs @src/services/agent_loop.py
you> /pr-review focus on the database changes
```

The extra text is concatenated after the skill's `prompt` field, giving the AI additional context.

## Listing Skills

```
you> /skills
```

Shows all available skills with their descriptions and source (default, global, or project).
