---
name: code-review
description: Code review a pull request with security audit and test verification
allowed-tools: Bash, Read, Edit, Grep, Glob, Task, WebFetch
---

# /code-review Skill

Review a pull request for bugs, security issues, CLAUDE.md compliance, and test results.

## Usage

```
/code-review 85        # Review PR #85
/code-review           # Review PR for current branch
```

## Workflow

### Step 1: Resolve PR Number

If a PR number is provided as an argument, use it. Otherwise, detect the PR for the current branch:
```bash
gh pr view --json number --jq '.number'
```

### Step 2: Eligibility Check (Haiku agent)

Check if the PR is eligible for review. Run:
```bash
gh pr view <PR> --json state,isDraft,author,title,body,reviews
```

Do NOT proceed if:
- (a) PR is closed/merged
- (b) PR is a draft
- (c) PR is automated (dependabot, renovate) or trivially obvious (single-line typo fix)
- (d) PR already has a code review comment containing "Generated with Claude Code"

### Step 3: Gather Context (parallel Haiku agents)

Launch 3 parallel Haiku agents:

**Agent A — CLAUDE.md paths:** Find all relevant CLAUDE.md files. Get the modified file list with `gh pr diff <PR> --name-only`, then find CLAUDE.md files in the root and in directories containing those modified files.

**Agent B — PR summary:** Run `gh pr view <PR>` and `gh pr diff <PR>`. Return a summary of what changed, which files were modified, and the intent of the change.

**Agent C — Run unit tests:** Run `pytest tests/unit/ -v --tb=short 2>&1 | tail -80` and return the results. Report pass/fail count and any failures.

### Step 4: Deep Review (6 parallel Sonnet agents)

Launch 6 parallel Sonnet agents. Each returns a list of issues with the reason each was flagged (CLAUDE.md adherence, bug, security, historical context, vision, etc.):

**Agent #1 — CLAUDE.md compliance:** Audit changes against all relevant CLAUDE.md files. Note that CLAUDE.md is guidance for Claude writing code, so not all instructions apply to external contributor PRs. Focus on architectural patterns, security requirements, and coding conventions that are universally applicable.

**Agent #2 — Bug scan:** Read the PR diff, do a shallow scan for obvious bugs. Focus on the changes themselves, not surrounding code. Look for logic errors, off-by-one, null/undefined handling, resource leaks. Ignore likely false positives.

**Agent #3 — Security audit:** Read the PR diff and the modified files. Check for:
- OWASP Top 10 vulnerabilities (injection, XSS, CSRF bypass, etc.)
- Path traversal or command injection in tool/file handling
- Missing input validation or sanitization at system boundaries
- Hardcoded secrets or credentials
- Insecure defaults (e.g., disabled auth, permissive CORS)
- Missing or weakened security headers
- Unsafe deserialization or eval usage
- Any deviation from the security model described in CLAUDE.md

**Agent #4 — Historical context:** Read the git blame and history of modified code. Check previous PRs that touched these files and any comments on them. Identify bugs in light of that historical context or recurring issues.

**Agent #5 — Code comments and intent:** Read code comments in the modified files. Make sure the changes comply with guidance in comments (TODOs, warnings, invariants). Check that the change doesn't break documented assumptions.

**Agent #6 — Vision and scope alignment:** Read `VISION.md` and evaluate whether the PR's changes align with the product vision. Check for:
- **Negative guardrails**: Does this PR make Anteroom more like a walled garden, ChatGPT clone, configuration burden, enterprise software, deployment project, or model host?
- **Scope creep**: Does the PR add features in out-of-scope areas (cloud, model training, mobile, complex deployment, admin dashboards, IDE recreation)?
- **Complexity creep**: Does the PR add new dependencies, config options, or infrastructure requirements without clear justification?
- **Lean principle**: Could this change be simpler? Does it add settings where defaults would suffice? Does it add abstractions for one-time use?
- **Dual-interface parity**: If the PR adds a web feature, does it consider the CLI (and vice versa)? If not, is the omission justified?
- **Enterprise usability**: Would this work behind a corporate firewall with no external internet access (beyond the configured LLM API)?

Flag issues only if the PR introduces genuine scope drift — not for pre-existing patterns or minor imperfections.

### Step 5: Confidence Scoring (parallel Haiku agents)

For each issue found in Step 4, launch a parallel Haiku agent that scores confidence (0-100):

- **0:** False positive. Doesn't stand up to scrutiny, or is a pre-existing issue.
- **25:** Might be real, but may be a false positive. Unverified. Stylistic issues not explicitly in CLAUDE.md.
- **50:** Verified real issue, but a nitpick or rarely hit in practice. Not very important relative to the rest of the PR.
- **75:** Verified real issue, very likely hit in practice. The PR's approach is insufficient. Important for functionality or directly mentioned in CLAUDE.md.
- **100:** Confirmed real issue, will happen frequently. Evidence directly confirms this.

For issues flagged due to CLAUDE.md, the agent must verify the CLAUDE.md actually calls out that specific issue.

### False Positive Examples (filter these out)

- Pre-existing issues not introduced by this PR
- Something that looks like a bug but isn't
- Pedantic nitpicks a senior engineer wouldn't flag
- Issues a linter, typechecker, or CI would catch (imports, types, formatting)
- General code quality (lack of tests, poor docs) unless required in CLAUDE.md
- Issues silenced in code (lint ignore comments)
- Intentional functionality changes related to the broader change
- Real issues on lines the PR did not modify

### Step 6: Filter

Keep only issues scoring 80+. If no issues meet this threshold, skip to Step 8.

### Step 7: Re-check Eligibility (Haiku agent)

Repeat the eligibility check from Step 2 to confirm the PR is still open and reviewable.

### Step 8: Post Review Comment

Use `gh pr comment` to post the review. Include test results summary.

#### Format — Issues Found

```markdown
### Code review

**Tests:** X passed, Y failed (list failures if any)
**Vision:** PASS / WARN (brief note if any vision concerns)

Found N issues:

1. <brief description> (CLAUDE.md says "<...>")

<link to file and line with full sha1 + line range>

2. <brief description> (security: <vulnerability type>)

<link to file and line with full sha1 + line range>

3. <brief description> (vision: <which "Is Not" guardrail or principle>)

<link to file and line with full sha1 + line range>

Generated with [Claude Code](https://claude.ai/code)

<sub>If this review was useful, react with a thumbs up. Otherwise, thumbs down.</sub>
```

#### Format — No Issues

```markdown
### Code review

**Tests:** X passed, 0 failed
**Vision:** PASS

No issues found. Checked for bugs, security, CLAUDE.md compliance, and vision alignment.

Generated with [Claude Code](https://claude.ai/code)
```

### Link Format

Links MUST use full git SHA and the correct repo name:
```
https://github.com/troylar/parlor/blob/<full-40-char-sha>/path/to/file.py#L10-L15
```

Rules:
- Full git SHA (not abbreviated, not `$(git rev-parse HEAD)`)
- `#` after filename
- Line range: `L[start]-L[end]`
- At least 1 line of context before and after the flagged line

## Notes

- Do NOT run builds or typechecks — CI handles those separately
- Use `gh` for all GitHub interactions, not web fetch
- Cite and link every issue (including CLAUDE.md references)
- Keep the comment brief and actionable
