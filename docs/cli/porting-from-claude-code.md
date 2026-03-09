# Porting Skills from Claude Code

If you have Claude Code command files (`.claude/commands/*.md`) and want to use them in Anteroom, this guide explains the format differences, what translates directly, and what needs adaptation.

## Key Differences

Claude Code commands are **Markdown files with YAML frontmatter**. Anteroom skills are **pure YAML files** with a `prompt` field containing the instructions.

| Aspect | Claude Code | Anteroom |
|--------|------------|----------|
| File format | Markdown (`.md`) with YAML frontmatter | YAML (`.yaml` / `.yml`) |
| Location | `.claude/commands/` | `.anteroom/skills/` or `.claude/skills/` |
| Prompt body | Markdown body after frontmatter | `prompt:` field (use `\|` for multi-line) |
| Tool restrictions | `allowed-tools` frontmatter | Not supported — AI uses all available tools |
| Model directives | "Launch Haiku/Sonnet agents" in prose | Use actual model IDs in `run_agent` calls |
| Sub-agents | `Agent` tool (Claude Code built-in) | `run_agent` tool (Anteroom built-in) |

## Field Mapping

| Claude Code frontmatter | Anteroom YAML field | Notes |
|------------------------|-------------------|-------|
| Filename stem (`commit.md` → `commit`) | `name: commit` | Must match `[a-z0-9][a-z0-9_-]*` |
| `description:` | `description:` | Same — short text for `/skills` list |
| `allowed-tools:` | *(ignored)* | Anteroom doesn't restrict tools per skill |
| Markdown body | `prompt: \|` | The entire instruction text |

## Tool Name Mapping

Claude Code and Anteroom have different tool names. When the AI reads your skill prompt, it maps the *intent* to whatever tools are available — so Claude Code tool names in prose often work. But using Anteroom's native names is more reliable.

| Claude Code tool | Anteroom tool | Notes |
|-----------------|--------------|-------|
| `Read` | `read_file` | Same behavior — reads file contents |
| `Edit` | `edit_file` | Same — exact string replacement |
| `Write` | `write_file` | Same — create or overwrite files |
| `Bash` | `bash` | Same — shell command execution |
| `Grep` | `grep` | Same — regex search across files |
| `Glob` | `glob_files` | Same — file pattern matching |
| `Agent` | `run_agent` | Similar concept, different mechanics (see below) |
| `WebFetch` | *(not built-in)* | Use MCP tools if available |
| `WebSearch` | *(not built-in)* | Use MCP tools if available |

## Sub-Agents: `Agent` vs `run_agent`

This is the biggest difference. Claude Code's `Agent` tool is a built-in that spawns sub-processes with model selection. Anteroom's `run_agent` tool is similar but works differently:

**What carries over:**

- Parallel execution — the AI can issue multiple `run_agent` calls in one response, and Anteroom runs them concurrently
- Model overrides — `run_agent` accepts an optional `model` parameter
- Tool access — sub-agents get all built-in and MCP tools

**What doesn't carry over:**

- **Model names** — Claude Code uses family names like "Haiku" and "Sonnet". Anteroom's `model` parameter must be a real model ID accepted by your configured API (e.g., `gpt-4o-mini`, `gpt-4o`, `claude-sonnet-4-20250514`). If omitted, the sub-agent inherits the parent's model.
- **Isolated context** — Anteroom sub-agents cannot see the parent conversation. Each `run_agent` prompt must be fully self-contained with all necessary context (file paths, issue numbers, search terms).
- **The word "agent" is not a directive** — writing "Launch parallel agents" in a skill prompt is just text. The AI decides whether to call `run_agent` based on the available tools and the prompt's intent. Explicit `run_agent` language is more reliable.

**Limits** (configurable via `safety.subagent` in `config.yaml`):

| Limit | Default |
|-------|---------|
| Max concurrent | 5 |
| Max total per request | 10 |
| Max nesting depth | 3 |
| Timeout per sub-agent | 120s |
| Output truncation | 4,000 chars |

## Step-by-Step Migration

### Before: Claude Code command file

```markdown title=".claude/commands/pr-summary.md"
---
name: pr-summary
description: Summarize a pull request
allowed-tools: Bash, Read, Grep
---

# /pr-summary

Summarize the given pull request.

## Usage

/pr-summary 85

## Steps

1. Fetch the PR details:
   ```bash
   gh pr view {args} --json title,body,files
   ```

2. Launch parallel agents to analyze:

   **Agent A (Haiku):** Read the diff and list changed files.
   **Agent B (Haiku):** Check if tests were modified.

3. Write a summary covering what changed and why.
```

### After: Anteroom skill file

```yaml title=".anteroom/skills/pr-summary.yaml"
name: pr-summary
description: Summarize a pull request
prompt: |
  Summarize the given pull request.

  ## Usage
  /pr-summary 85

  ## Steps

  1. Fetch the PR details with `gh pr view {args} --json title,body,files`.

  2. Use the `run_agent` tool to analyze these tasks in parallel.
     Each sub-agent prompt must be self-contained.

     Sub-agent 1: Run `gh pr diff {args}` and list the changed files
     with a one-line summary of each change.

     Sub-agent 2: Run `gh pr diff {args} --name-only` and check if
     any test files were modified. Report which tests changed.

  3. Write a summary covering what changed and why.
```

### What changed

1. **Format**: Markdown with frontmatter → pure YAML with `prompt: |`
2. **`allowed-tools`**: Removed — Anteroom doesn't support per-skill tool restrictions
3. **"Agent A (Haiku)"**: Replaced with explicit `run_agent` language and self-contained prompts
4. **Model names**: Removed "Haiku" — sub-agents inherit the parent model (or specify a real model ID)
5. **`{args}`**: Works the same — user arguments replace the placeholder (but see the code-fence gotcha below)

!!! warning "Gotchas"
    **These are the four things that silently fail when porting without changes:**

    1. **`allowed-tools` frontmatter is ignored.** Anteroom's skill loader reads `name`, `description`, and `prompt` — nothing else. Extra frontmatter fields are silently discarded.
    2. **Model family names don't resolve.** `run_agent(model="haiku")` will fail at the API level. Use real model IDs like `gpt-4o-mini` or omit `model` to inherit the parent's model.
    3. **Sub-agent prompts must be self-contained.** Unlike Claude Code where agents share some context, Anteroom sub-agents see *only* their prompt. Include file paths, PR numbers, search terms — everything the sub-agent needs.
    4. **`{args}` inside fenced code blocks is not expanded.** Anteroom's `{args}` expansion skips content inside `` ``` ... ``` `` fences. If your Claude Code command has `{args}` inside bash fences, move the command to inline code (`` `command {args}` ``) or plain prose when porting.

## What Just Works

Not everything needs changing. These patterns translate directly:

- **Bash commands** (`gh`, `git`, `pytest`, `ruff`) — same shell, same tools
- **`{args}` placeholder** — same expansion behavior (but not inside fenced code blocks — use inline code instead)
- **Multi-step workflows** — the AI follows numbered steps naturally
- **File reading/writing** — tool names differ but the AI maps intent correctly
- **Checklists and review criteria** — the AI interprets markdown checklists as instructions

## See Also

- [Skills](skills.md) — full skill authoring reference
- [Skill Examples](skill-examples.md) — complete example skills with commentary
- [Built-in Tools: run_agent](tools.md#run_agent) — sub-agent tool reference
