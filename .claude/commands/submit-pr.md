---
name: submit-pr
description: Create a pull request with auto-generated summary, issue references, and pre-validation
allowed-tools: Bash, Read, Edit, Grep, Glob, Task
---

# /submit-pr Skill

Validate the current branch and create a pull request with a well-structured description.

## Usage

```
/submit-pr                          # Submit PR against main
/submit-pr develop                  # Submit PR against a specific base branch
/submit-pr --skip-checks            # Skip pr-check (not recommended)
/submit-pr --draft                  # Create as draft PR
```

## Workflow

### Step 1: Pre-flight

Run in parallel:

**A — Branch status:**
```bash
git branch --show-current
git status --short
git log --oneline main..HEAD
```

**B — Remote status:**
```bash
git remote -v
git rev-list --left-right --count main...HEAD
```

Verify:
- We're on a feature branch (not main)
- There are commits ahead of the base branch
- No uncommitted changes (warn if present, suggest committing first)

### Step 2: Extract Issue References

From the branch name and all commits, collect issue references:

```bash
git branch --show-current
git log --oneline main..HEAD
```

- Extract all `#N` references from commit messages
- Extract issue number from branch name (`issue-<N>-...`)
- Deduplicate
- Verify each issue exists:
  ```bash
  gh issue view <N> --json state,title --jq '"\(.state): \(.title)"'
  ```
- The primary issue (from branch name) gets `Closes #N` treatment
- Secondary issues get `Addresses #N` or `Related to #N`

If NO issue references are found, abort: "Every PR must reference at least one GitHub issue. Run `/new-issue` to create one."

### Step 3: Run Pre-checks (unless --skip-checks)

Run a lightweight gate check (not the full `/pr-check` — that can be run separately for deep analysis):

1. Lint: `ruff check src/ tests/`
2. Format: `ruff format --check src/ tests/`
3. Tests: `pytest tests/unit/ -v --tb=short 2>&1 | tail -80`
4. Type check: `mypy src/ --ignore-missing-imports 2>&1 | tail -30`

These are the blocking checks — if any fail, abort. The user must fix issues before submitting.

For the full audit (test thoroughness, security scan, CLAUDE.md compliance, vision alignment), recommend running `/pr-check` first.

If `--skip-checks` is passed, warn that this is not recommended and proceed.

### Step 4: Generate PR Description

Analyze all commits and changed files to generate the PR body:

```bash
git log --format='%s%n%b' main..HEAD
git diff --stat main..HEAD
git diff main..HEAD
```

Structure the PR body:

```markdown
## Summary

<2-4 bullet points describing what this PR does and why, written for a reviewer>

## Changes

<Grouped by area — routers, services, tools, tests, etc.>

### <Area>
- `file.py` — <what changed and why>

## Issue References

Closes #<primary issue>
Addresses #<secondary issue>

## Test Plan

- [ ] Unit tests pass: `pytest tests/unit/ -v`
- [ ] Lint passes: `ruff check src/ tests/`
- [ ] <Specific test scenarios relevant to this PR>
- [ ] <Manual verification steps if applicable>

## Security Considerations

<Only include if the PR touches auth, sessions, input handling, DB queries, or tool execution. Otherwise omit this section.>

## Vision Alignment

<Include a brief note on which core principles this PR supports. If the PR adds new dependencies, config options, or infrastructure requirements, note them here with justification. Omit this section for trivial changes (typos, small fixes).>

---
Generated with [Claude Code](https://claude.ai/code)
```

### Step 5: Push and Create PR

```bash
git push -u origin $(git branch --show-current)
```

Then create the PR:

```bash
gh pr create --title "<title>" --body "$(cat <<'EOF'
<generated body>
EOF
)" <--draft if requested>
```

**Title rules:**
- Under 70 characters
- Format: `<type>: <description> (#<primary issue>)`
- Derived from the primary issue title or the commit summary
- Examples: `feat: add semantic search to CLI (#83)`, `fix: handle empty query in search endpoint (#91)`

### Step 6: Post-creation

```bash
gh pr view --json number,url,title
```

Report:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🚀 PR Created
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  🔗 PR:       #<N> — <title>
  🌐 URL:      <url>
  🔀 Base:     main <- <branch>
  📎 Issues:   Closes #<N>, Addresses #<N>
  📌 Status:   <ready | draft>
  🧪 Checks:   ✅ lint, tests, format, types
  🎯 Vision:   ✅ supports <principles>

────────────────────────────────────────────
  👉 Next steps:
    1. Wait for CI or request review
    2. Self-review: /code-review <N>
────────────────────────────────────────────
```

## Guidelines

- Never create a PR without at least one issue reference
- Never create a PR with failing tests (unless --skip-checks)
- Keep the PR title concise — details go in the body
- Group changes logically in the description
- If the PR is large (>500 lines changed), suggest breaking it up
- Security section only when relevant — don't add boilerplate
