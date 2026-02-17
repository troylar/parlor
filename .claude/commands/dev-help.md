---
name: dev-help
description: Show the developer workflow guide with all available skills and conventions
allowed-tools: Bash, Read, Grep, Glob
---

# /dev-help Skill

Display a comprehensive, visually formatted guide to Anteroom's developer workflow for experienced developers who are new to the project's command structure.

## Workflow

### Step 1: Gather Context

Read the following to ensure the help is accurate:
1. List all skill files: `ls .claude/commands/`
2. Read `VISION.md` for the project tagline and core principles
3. Read the branch name: `git branch --show-current`
4. Check for open issues assigned or recent: `gh issue list --limit 5 --json number,title,state`

### Step 2: Display the Guide

Print the following formatted guide:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🏠 Anteroom Developer Guide
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Anteroom is a lean, secure, self-hosted AI interface.
  pip install anteroom && aroom — that's the whole pitch.

  📖 Read VISION.md for the full product vision.
  📖 Read CLAUDE.md for architecture and conventions.


🔄 Development Lifecycle
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  The workflow follows this order:

  💭 Explore idea  →  /ideate
  💡 Create issue  →  /new-issue
  🚀 Start coding  →  /start-work <issue#>
  💾 Save work     →  /commit
  📤 Submit + review → /submit-pr  (auto-runs /code-review)
  🔍 Review others →  /code-review <pr#>
  📦 Ship          →  /deploy


📋 Skills Reference
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  /ideate <rough idea>
    Explore a feature idea before committing to an issue.
    Checks vision, feasibility, and alternatives.
    Presents lean vs. full approaches for discussion.
    No issue created until you're ready.

  /new-issue <description>
    Turn a refined idea into a structured GitHub issue.
    Checks vision alignment before creating.
    Warns if the idea conflicts with product identity.

  /start-work <issue#> [--no-worktree]
    Create a worktree + branch, explore code, plan implementation.
    Worktree is the default — keeps your current branch clean.
    Use --no-worktree for a traditional branch instead.
    Runs 3 parallel agents for deep code exploration.

  /commit
    Stage, validate, and commit with enforced conventions.
    Auto-detects type/scope from changes.
    Runs lint + format + tests on staged files only.
    Format: type(scope): description (#issue)

  /submit-pr [--draft] [--checks-only] [--skip-checks]
    Full validation suite + PR creation + auto code review.
    Runs 5 parallel Sonnet agents: test thoroughness,
    CLAUDE.md compliance, docs freshness, vision, security.
    After creating the PR, automatically runs /code-review.
    If issues found, offers to fix and re-review (max 2 rounds).
    Use --checks-only to validate without creating the PR.

  /pr-check --pr <N>
    Validate someone else's PR in a temp worktree.
    Same checks as /submit-pr but read-only.
    Use this when reviewing a collaborator's work.

  /code-review [<pr#>]
    Deep review with 7 parallel Sonnet agents using
    structured checklists (not open-ended scanning).
    Same checklist every run = consistent results.
    Posts condensed results as a PR comment.
    Shows full results locally in chat first.

  /deploy [patch|minor|major]
    Merge PR, wait for CI, bump version, publish to PyPI,
    create GitHub release with user-friendly notes.
    Audits all documentation before merging.

  /write-docs <page-path>
    Write or update MkDocs documentation pages.
    Cross-references source code for accuracy.


⚡ Rules (always active)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  These rules are enforced automatically in every session,
  even without running a skill:

  📌 No code without a GitHub issue
     Every change must reference an issue. No exceptions.

  📌 Commit format: type(scope): description (#issue)
     Enforced by rule + git hook. Commits without issue
     references are rejected.

  📌 Tests required for new code
     New modules need test files. Bug fixes need
     regression tests.

  📌 Security patterns (OWASP ASVS Level 2)
     Parameterized queries, input validation, no hardcoded
     secrets, no eval/exec with user input.

  📌 Vision alignment
     Features are checked against VISION.md principles.
     "What Anteroom Is Not" guardrails flag scope drift.

  📌 Consistent output formatting
     All skills use emoji + box-drawing for reports.


🎯 Vision Quick Reference
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Core principles:
    1. Zero-friction setup (pip install && go)
    2. Security is structural (OWASP ASVS Level 2)
    3. Lean over sprawling (earn your place)
    4. Two interfaces, one engine (web + CLI parity)
    5. Local-first, always (SQLite, no cloud)
    6. Extensible through standards (MCP, OpenAI API)
    7. Collaborative without complexity

  What Anteroom is NOT:
    ❌ A walled garden      ❌ A ChatGPT clone
    ❌ A config burden       ❌ Enterprise software
    ❌ A deployment project  ❌ A model host

  Litmus test for new features:
    → Works behind a corporate firewall?
    → Works with pip install?
    → Lean — could we do it with less?
    → Works in both web and CLI?
    → Would the team use it daily?


🛠️ Common Workflows
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Explore an idea:
    /ideate real-time collaborative canvas editing

  Start a new feature:
    /new-issue add dark mode support
    /start-work 92

  Save progress:
    /commit

  Ready to submit (includes auto code review):
    /submit-pr

  Review a teammate's PR:
    /pr-check --pr 86
    /code-review 86

  Ship a release:
    /deploy


📂 Project Structure
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  src/anteroom/
  ├── app.py              # FastAPI app factory
  ├── config.py           # YAML config + env overrides
  ├── db.py               # SQLite schema + migrations
  ├── cli/repl.py         # CLI REPL
  ├── routers/            # FastAPI endpoints
  ├── services/
  │   ├── agent_loop.py   # Shared agentic loop
  │   ├── ai_service.py   # OpenAI SDK wrapper
  │   └── storage.py      # SQLite DAL
  └── tools/              # Built-in agent tools

  tests/unit/             # Fully mocked unit tests
  tests/integration/      # Real SQLite tests
  docs/                   # MkDocs documentation site


────────────────────────────────────────────────────────────
  💡 Tip: Run any skill with no arguments for usage help.
  📖 Full details: VISION.md, CLAUDE.md
────────────────────────────────────────────────────────────
```
