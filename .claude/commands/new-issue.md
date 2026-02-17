---
name: new-issue
description: Create a well-structured GitHub issue from a feature idea or bug report
allowed-tools: Bash, Read, Grep, Glob, Task
---

# /new-issue Skill

Turn a feature idea, bug report, or task description into a structured GitHub issue.

## Usage

```
/new-issue add dark mode support
/new-issue the search endpoint returns 500 when query is empty
/new-issue refactor storage layer to support postgres
```

The argument is a natural-language description of the work. It can be a sentence, a paragraph, or bullet points.

## Workflow

### Step 1: Vision Alignment Check

Before anything else, evaluate the idea against the product vision.

1. Read `VISION.md` for the full product vision
2. Check against **"What Anteroom Is Not"** negative guardrails:
   - Does this make Anteroom a walled garden? (proprietary extensions, required infrastructure for extensibility)
   - Does this make Anteroom more like a ChatGPT clone? (chat-only feature, no agentic/tool value)
   - Does this make Anteroom a configuration burden? (feature that doesn't work without configuration)
   - Does this add enterprise software patterns? (license keys, SSO, admin panels)
   - Does this complicate deployment? (new infrastructure dependencies)
   - Does this make Anteroom a model host? (model management, benchmarking)
3. Check against **Out of Scope** (hard no): cloud/SaaS, model training, mobile native, complex deployment, admin dashboards, recreating IDE functionality
4. Run the **Litmus Test**:
   - Can someone in a locked-down enterprise use this?
   - Does it work with `pip install`?
   - Is it lean? Could we do this with less?
   - Does it work in both interfaces (or have a clear reason not to)?
   - Would the team use this daily?
5. Identify which **Core Principles** the idea supports (zero-friction, security, lean, dual-interface, local-first, extensible, collaborative)

**Report the alignment:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🎯 Vision Alignment
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Supports:     <which core principles>
  Guardrails:   ✅ / ⚠️ <any "Is Not" concerns>
  Scope:        ✅ / ❌ <if it hits an out-of-scope area>
  Litmus test:  ✅ / ⚠️ <flag any concerns>

────────────────────────────────────────────
```

- If the idea **conflicts** (❌): explain the conflict clearly, suggest an alternative that aligns, and ask the user how to proceed. Do not create the issue until resolved.
- If the idea triggers **warnings** (⚠️): explain the concern, ask the user to confirm. If they proceed, the issue will get the `vision-review` label.
- If the idea is **ambiguous**: ask the user to clarify how it serves the core use cases (enterprise behind firewall, collaborative teams, power users).
- If the idea **aligns** (✅): proceed to Step 2.

### Step 2: Understand the Request

Parse the user's input to determine:
- **Type**: `enhancement` (new feature), `bug` (something broken), `documentation`, `testing`, `refactor`
- **Scope**: Which parts of the codebase are likely affected
- **Urgency**: Is this blocking other work?

### Step 3: Explore Relevant Code (Sonnet agent)

Launch a Sonnet agent to understand the codebase context:

1. Read `CLAUDE.md` for architecture overview
2. Based on the described work, find relevant source files:
   - Search for related modules, functions, routers, tools
   - Read the key files to understand current implementation
3. Identify:
   - Which files will likely need changes
   - What existing patterns to follow
   - Any related existing issues: `gh issue list --search "<keywords>" --json number,title,state --limit 5`
   - Potential blockers or dependencies

### Step 4: Check for Duplicates

Search for existing issues that might cover this work:
```bash
gh issue list --search "<keywords from description>" --state all --json number,title,state,labels --limit 10
```

If a duplicate or closely related issue exists:
- Show the user the existing issue(s)
- Ask if they want to proceed with a new issue, update the existing one, or skip

### Step 5: Draft the Issue

Create a structured issue body:

```markdown
## Description

<1-3 sentences explaining what and why, written clearly enough for any contributor to understand>

## Context

<Brief explanation of the current state — what exists today, what's missing or broken>

## Affected Files

- `src/anteroom/<path>` — <what changes here>
- `src/anteroom/<path>` — <what changes here>
- `tests/unit/<path>` — <new or modified tests>

## Acceptance Criteria

- [ ] <Specific, testable criterion>
- [ ] <Another criterion>
- [ ] <Tests pass: `pytest tests/unit/ -v`>
- [ ] <Lint passes: `ruff check src/ tests/`>

## Implementation Notes

<Optional: suggested approach, patterns to follow, gotchas to watch for. Only include if genuinely helpful — omit if the approach is obvious.>
```

### Step 6: Determine Labels

Assign labels based on type:
- `enhancement` — new feature or capability
- `bug` — something broken or incorrect
- `documentation` — docs-only changes
- `testing` — test additions or improvements
- `refactor` — code restructuring without behavior change
- `security` — security-related changes

Check which labels exist in the repo first:
```bash
gh label list --json name --jq '.[].name'
```

Only use labels that exist. If a needed label doesn't exist, skip it rather than creating new labels.

### Step 7: Create the Issue

Show the user a preview of the issue title, labels, and body. Ask for confirmation.

Once confirmed:
```bash
gh issue create --title "<title>" --label "<label>" --body "$(cat <<'EOF'
<body content>
EOF
)"
```

### Step 8: Report

Print:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  📋 Issue Created
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  🔗 Issue:    #<N> — <title>
  🏷️ Labels:   <labels>
  🌐 URL:      <url>
  🎯 Vision:   ✅ supports <principles>

────────────────────────────────────────────
  👉 Next: /start-work <N>
────────────────────────────────────────────
```

## Guidelines

- **Title**: imperative mood, concise, under 70 characters. Example: "Add semantic search to CLI REPL", not "Semantic search feature"
- **Body**: written for a contributor who knows the codebase but not the specific context. No jargon without explanation.
- **Acceptance criteria**: specific and testable. "Works correctly" is not a criterion. "Returns 200 with matching results when query matches existing messages" is.
- **Don't over-specify**: if the implementation approach is obvious from the criteria, skip "Implementation Notes"
- **Related issues**: if this work depends on or relates to other issues, mention them in the description
