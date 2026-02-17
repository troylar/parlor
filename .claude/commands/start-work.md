---
name: start-work
description: Begin work on a GitHub issue — create branch, explore code, plan implementation
allowed-tools: Bash, Read, Edit, Grep, Glob, Task
---

# /start-work Skill

Set up everything needed to begin implementing a GitHub issue.

## Usage

```
/start-work 83                  # Start work on issue #83
/start-work 83 --plan-only      # Just create the plan, don't create a branch
```

The argument is a GitHub issue number.

## Workflow

### Step 1: Fetch and Validate the Issue

```bash
gh issue view <N> --json number,title,body,labels,state,assignees
```

- If the issue is closed, warn the user and ask if they want to proceed
- If the issue is assigned to someone else, warn the user

### Step 1b: Vision Alignment Check

Read `VISION.md` and evaluate the issue against the product vision.

1. Check against the **"What Anteroom Is Not"** negative guardrails:
   - Does this make Anteroom a walled garden? (proprietary extension system, required infrastructure for extensibility)
   - Does this make Anteroom more like a ChatGPT clone? (chat-only feature with no agentic/tool value)
   - Does this make Anteroom a configuration burden? (feature that doesn't work without configuration, missing sensible defaults)
   - Does this add enterprise software patterns? (license keys, SSO, admin panels)
   - Does this complicate deployment? (new infrastructure dependencies, Docker requirements)
   - Does this make Anteroom a model host? (model management, benchmarking, serving)

2. Check against **Out of Scope** (hard no): cloud/SaaS, model training, mobile native, complex deployment, admin dashboards, recreating IDE functionality

3. Run the **Litmus Test**:
   - Can someone in a locked-down enterprise use this?
   - Does it work with `pip install`?
   - Is it lean?
   - Does it work in both interfaces?
   - Would the team use this daily?

4. Check for **complexity creep**: Does this issue add new dependencies, new config options, or new infrastructure requirements? If so, is each one justified?

**Report:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🎯 Vision Alignment: #<N>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Supports:     <which core principles this advances>
  Guardrails:   ✅ / ⚠️ <any "Is Not" concerns>
  Litmus test:  ✅ / ⚠️ <any concerns>
  Scope:        ✅ / ❌
  Complexity:   ✅ / ⚠️ <new deps, config, or infra>

────────────────────────────────────────────
```

- If **[FAIL]**: explain the conflict, suggest alternatives, ask the user how to proceed. Do not create a branch.
- If **[WARN]**: show the concern, ask the user to confirm before proceeding.
- If all **[PASS]**: continue to Step 2.

### Step 2: Check for Existing Work

Check if work has already started on this issue:

```bash
git branch --list "issue-<N>-*"
gh pr list --search "head:issue-<N>" --json number,title,state,headRefName
```

If a branch or PR already exists, show it and ask the user how to proceed:
- Continue on the existing branch
- Start fresh (new branch)
- Abort

### Step 3: Create Branch

Generate a branch name from the issue:
- Format: `issue-<N>-<short-description>`
- `<short-description>`: 2-4 words from the issue title, kebab-case, max 50 chars total
- Example: issue #83 "Add knowledge notebook support" → `issue-83-knowledge-notebooks`

```bash
git checkout main && git pull origin main
git checkout -b issue-<N>-<short-description>
```

If `--plan-only` was passed, skip branch creation.

### Step 4: Deep Code Exploration (parallel agents)

Launch parallel agents to understand the codebase context for this issue:

**Agent A — Architecture context (Sonnet):**
1. Read `CLAUDE.md` for architecture overview
2. Identify which layer(s) this issue touches (routers, services, tools, CLI, static, DB)
3. List the key files and patterns relevant to this change

**Agent B — Existing implementation (Sonnet):**
1. Based on the issue description and affected files, read the current code
2. Understand existing patterns: how similar features are implemented
3. Identify integration points and dependencies
4. Note any TODOs, FIXMEs, or comments related to this work

**Agent C — Test landscape (Sonnet):**
1. Find test files related to the affected modules
2. Understand testing patterns: fixtures, mocking approach, async test setup
3. Identify what new tests will be needed
4. Check current test count: `pytest tests/unit/ --collect-only -q 2>&1 | tail -3`

### Step 5: Create Implementation Plan

Based on the exploration, create a structured plan:

```markdown
## Implementation Plan: #<N> — <title>

### Summary
<1-2 sentences on what this change does>

### Files to Create
- `src/anteroom/<path>` — <purpose>
- `tests/unit/test_<name>.py` — <what it tests>

### Files to Modify
- `src/anteroom/<path>` — <what changes and why>
- `src/anteroom/<path>` — <what changes and why>

### Implementation Steps
1. <First thing to do — be specific about what code to write/change>
2. <Next step>
3. <Continue...>
N. Run tests: `pytest tests/unit/ -v`
N+1. Run lint: `ruff check src/ tests/`

### Testing Strategy
- <What to test, how to test it>
- <Edge cases to cover>
- <Integration points to verify>

### Risks & Considerations
- <Anything tricky, breaking changes, migration needs>
```

### Step 6: Report

Print:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🚀 Ready to Work: #<N> — <title>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  🔀 Branch:   issue-<N>-<description>
  📋 Plan:     <number> steps across <number> files
  🧪 Tests:    <number> existing, <number> new needed
  🎯 Vision:   ✅ supports <principles>

<The implementation plan from Step 5>

────────────────────────────────────────────
  👉 Next: say "go" to start, or adjust the plan
────────────────────────────────────────────
```

## Guidelines

- The plan should be detailed enough that another developer (or Claude session) could follow it
- Don't start coding — this skill only sets up the context and plan
- If the issue description is vague or missing acceptance criteria, flag what's unclear and suggest criteria
- If the issue requires changes to the DB schema, call that out prominently
- If the issue touches security-sensitive code (auth, sessions, crypto, tools), note OWASP requirements
