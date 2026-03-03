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

### Step 2: Eligibility and Context Detection

Check if the PR is eligible for review. Run:
```bash
gh pr view <PR> --json state,isDraft,author,title,body,reviews
gh api repos/{owner}/{repo}/issues/<PR>/comments --jq '.[].body'
```

Do NOT proceed if:
- (a) PR is closed/merged
- (b) PR is a draft
- (c) PR is automated (dependabot, renovate) or trivially obvious (single-line typo fix)
- (d) PR already has a **code review** comment (contains "### Code review" AND "Generated with Claude Code")

**Submit-PR detection:** Check if a `/submit-pr` validation comment exists. Look for comments containing "Generated with Claude Code" that also contain "PR Validation" or "PR Created". If found, set `submit_pr_ran = true` — this means CLAUDE.md compliance, docs freshness, vision alignment, security scan, and test thoroughness were already checked. The code-review should only run its 3 unique agents.

### Step 2b: PR Size Check

Determine the PR size for agent scaling:
```bash
gh pr diff <PR> --stat | tail -1
```
Parse the total insertions + deletions. If under 300 lines, set `small_pr = true`.

### Step 3: Gather Context (parallel Haiku agents)

Launch 3 parallel Haiku agents:

**Agent A — CLAUDE.md paths:** Find all relevant CLAUDE.md files. Get the modified file list with `gh pr diff <PR> --name-only`, then find CLAUDE.md files in the root and in directories containing those modified files.

**Agent B — PR summary:** Run `gh pr view <PR>` and `gh pr diff <PR>`. Return a summary of what changed, which files were modified, and the intent of the change.

**Agent C — Run unit tests:** If in a worktree with `.venv/`, use `.venv/bin/python -m pytest tests/unit/ -v --tb=short 2>&1 | tail -80`. Otherwise run `pytest tests/unit/ -v --tb=short 2>&1 | tail -80`. Return the results. Report pass/fail count and any failures.

### Step 4: Deep Review (conditional parallel agents)

**Agent selection depends on context from Step 2/2b:**

| Condition | Agents to run | Rationale |
|-----------|--------------|-----------|
| `submit_pr_ran = true` | #2 Bug scan, #4 Historical context, #5 Code comments | Submit-PR already ran compliance, docs, vision, security, UX tests |
| `small_pr = true` AND standalone | #1b UX tests, #2 Bug scan, #3 Security, #4 Historical context | Small PRs need fewer agents |
| Standalone, large PR | All 8 agents | Full review needed |

**Bug scan (#2) ALWAYS runs regardless of context — it is never skipped.**

**IMPORTANT for ALL agents:** Report ONLY failures. Do not report PASS or N/A items. Keep response under 500 words.

Every agent MUST:
1. Read the full diff (`gh pr diff <PR>`)
2. Check each item on its checklist
3. Report only FAIL items with file:line

---

**Agent #1 — CLAUDE.md compliance (Sonnet):** *(skipped when `submit_pr_ran`)*

Read CLAUDE.md files, check the diff:
- [ ] New modules follow existing naming conventions
- [ ] New routers use same middleware/auth pattern
- [ ] New tools follow ToolRegistry pattern (`_handlers` + `_definitions`)
- [ ] Parameterized queries only (no SQL concatenation)
- [ ] New endpoints have Content-Type validation for JSON bodies
- [ ] Async functions use `await` correctly
- [ ] New config fields have `AI_CHAT_` env var overrides documented
- [ ] Error responses don't expose internals
- [ ] Commits follow `type(scope): description (#issue)`

---

**Agent #1b — UX test coverage (Haiku):** *(skipped when `submit_pr_ran`)*

Check whether changed files have corresponding UX tests per `.claude/rules/ux-testing.md`:
- [ ] Web UI changes (`routers/`, `static/`) have Playwright tests in `tests/e2e/test_ui_*.py`
- [ ] CLI UX changes (`cli/repl.py`, `cli/commands.py`, `cli/layout.py`, `cli/renderer.py`) have integration tests in `tests/integration/`
- [ ] JS changes (`static/js/`) have JS unit tests
- [ ] Shared core changes (`services/agent_loop.py`, `tools/`) have UX tests for both interfaces
- [ ] Backend-only changes correctly skip UX tests

Report as `(ux-tests: <file> — <missing test type>)`.

---

**Agent #2 — Bug scan (Sonnet):** *(ALWAYS runs)*

Read the diff, check each line of changed code:
- [ ] **Return values**: Unchecked None returns?
- [ ] **Off-by-one**: Loop bounds, slice indices, range() endpoints
- [ ] **Type mismatches**: Wrong types in JSON parsing, API responses
- [ ] **Null/empty handling**: Empty lists, missing dict keys, blank strings, no rows
- [ ] **Resource leaks**: Files/connections not closed? Missing `async with`/`with`?
- [ ] **Concurrency**: Shared mutable state without locks? Race conditions?
- [ ] **Exception handling**: Too broad (`except Exception`)? Swallowed silently?
- [ ] **String formatting**: SQL/command concatenation with user input?
- [ ] **Import errors**: Missing, circular, or version-specific imports?
- [ ] **Logic inversion**: Negated conditions, `and`/`or` confusion?

---

**Agent #3 — Security audit (Sonnet):** *(skipped when `submit_pr_ran`)*

Read the diff and modified files:
- [ ] **SQL injection**: String formatting in queries?
- [ ] **Command injection**: `subprocess` with `shell=True` + user input?
- [ ] **Path traversal**: File ops with unsanitized `..`?
- [ ] **XSS**: `innerHTML` with unsanitized input?
- [ ] **CSRF**: State-changing endpoints missing protection?
- [ ] **Auth bypass**: New endpoints without auth?
- [ ] **Hardcoded secrets**: API keys/passwords/tokens in source?
- [ ] **Insecure defaults**: Debug mode, disabled auth, permissive CORS?
- [ ] **Input validation**: User input not validated server-side?
- [ ] **Info disclosure**: Internals in error messages?
- [ ] **Unsafe deserialization**: `pickle.loads`, `yaml.load`, `eval()`, `exec()`?
- [ ] **Cookie security**: Missing HttpOnly, Secure, SameSite?
- [ ] **Rate limiting**: New public endpoints without limits?
- [ ] **Content-Type**: Missing Content-Type validation?

---

**Agent #4 — Historical context (Haiku):** *(ALWAYS runs)*

IMPORTANT: Maximum 15 tool calls. Prioritize:
1. `git log -5` for each modified source file (not test files)
2. Deep-dive (blame, PR search) only on files flagged as security-critical
3. Stop after 15 tool calls even if not all files are checked.

Checklist:
- [ ] **Reverted fixes**: Does this undo/weaken a previous fix? Check `git log --oneline <file>` for `fix:` commits
- [ ] **Recurring patterns**: Similar bugs in this file before?
- [ ] **TODO/FIXME regression**: TODOs in modified areas addressed or ignored?
- [ ] **Previous review feedback**: Past PR comments on these files — concerns addressed?
- [ ] **Breaking assumptions**: Does the change violate documented assumptions in comments or commit messages?

---

**Agent #5 — Code comments and intent (Sonnet):** *(ALWAYS runs)*

Read code comments in modified files:
- [ ] **Invariant violations**: Comments stating "must be called after X" / "never modify without Y" — violated?
- [ ] **TODO completion**: TODOs this change should address but didn't?
- [ ] **Warning heeds**: `# WARNING:` / `# IMPORTANT:` comments — complied with?
- [ ] **Docstring accuracy**: Modified functions still match their docstrings?
- [ ] **Security comments**: `# SECURITY:` / `# SECURITY-REVIEW:` — properties maintained?

---

**Agent #6 — Vision and scope alignment (Haiku):** *(skipped when `submit_pr_ran`)*

Read `VISION.md` and the diff. Flag only issues **introduced by this PR**:
- [ ] Not a walled garden / ChatGPT clone / config burden / enterprise software / deployment project / model host
- [ ] New `pyproject.toml` dependencies justified
- [ ] New config options have sensible defaults
- [ ] Dual-interface parity (web-only or CLI-only justified?)
- [ ] Lean: could this be simpler? Unnecessary abstractions?

---

**Agent #7 — Documentation freshness (Haiku):** *(skipped when `submit_pr_ran`)*

Read `CLAUDE.md`, `README.md`, `VISION.md`, and relevant `docs/` pages. Check:
- [ ] New modules not in CLAUDE.md "Key Modules"?
- [ ] Modified modules with stale CLAUDE.md descriptions?
- [ ] New config/DB/event fields undocumented?
- [ ] Security model changes not reflected?
- [ ] New CLI commands/flags missing from README?
- [ ] docs/ pages stale for changed source files? (`cli/` → `docs/cli/`, `routers/` → `docs/api/`, `tools/` → `docs/cli/tools.md`, `config.py` → `docs/configuration/`, security → `docs/security/`)

Report as `(docs: <file> — <what's stale/missing>)`.

### Step 5: Confidence Scoring (parallel Haiku agents)

For each FAIL item from Step 4, launch a parallel Haiku agent that independently verifies the issue and scores confidence (0-100):

The verification agent MUST:
1. Re-read the specific code at the reported file:line
2. Determine if the issue is real by checking the actual behavior, not just pattern-matching
3. Check if the issue is pre-existing (in the base branch) or newly introduced by this PR
4. Score accordingly:

- **0:** False positive. Doesn't hold up on re-read, or is a pre-existing issue not introduced by this PR.
- **25:** Might be real, but ambiguous. Could be intentional. Stylistic only.
- **50:** Real issue, but minor — rarely hit in practice, or a nitpick.
- **75:** Real issue, likely hit in practice. The specific checklist item clearly fails. Important for functionality or security.
- **100:** Confirmed real issue with concrete evidence (e.g., missing null check on a value that can demonstrably be null, SQL concatenation with user input).

For CLAUDE.md issues, the agent MUST quote the specific CLAUDE.md passage that the code violates.
For security issues, the agent MUST describe the attack vector (how could this be exploited?).

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

### Step 8: Display Local Review Report

Before posting anything to GitHub, display the full review results locally in the chat so the user can see everything immediately:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🔍 Code Review — PR #<N>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  🔗 PR:       #<N> — <title>
  👤 Author:   <author>
  📊 Changes:  N files changed, +X -Y

🧪 Tests
  Result:       ✅ X passed / ❌ X passed, Y failed
  Failures:     <list failures if any>

🎯 Vision
  Guardrails:   ✅ PASS / ⚠️ <which guardrail triggered>
  Scope:        ✅ PASS / ⚠️ <details>
  Lean:         ✅ PASS / ⚠️ <details>

📖 Docs
  CLAUDE.md:    ✅ / ⚠️ <stale/missing sections>
  README.md:    ✅ / ⚠️ <details>
  docs/ pages:  ✅ / ⚠️ <stale/missing pages>

🔒 Security
  OWASP:        ✅ PASS / ⚠️ <vulnerability type>
  Injection:    ✅ / ⚠️ <details>
  Auth/CSRF:    ✅ / ⚠️ <details>

📝 CLAUDE.md Compliance
  Patterns:     ✅ / ⚠️ <non-compliant patterns>
  Conventions:  ✅ / ⚠️ <details>
```

If issues were found (score 80+), list each one with full context:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🚨 Issues Found: N
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. ❌ <brief description>
     Category:   <bug | security | CLAUDE.md | vision | docs>
     Confidence: <score>/100
     File:       <path>:<line range>
     Detail:     <explanation of why this is an issue>
     Suggestion: <what to do about it>

  2. ⚠️ <brief description>
     Category:   <category>
     Confidence: <score>/100
     File:       <path>:<line range>
     Detail:     <explanation>
     Suggestion: <fix>

────────────────────────────────────────────
```

If no issues found:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✅ No Issues Found
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Checked: <list agents that actually ran>
  Mode:    <"post-submit-pr (3 agents)" | "small PR (3 agents)" | "full (7 agents)">

────────────────────────────────────────────
```

### Step 9: Post Review Comment to GitHub

After displaying the local report, post a **condensed** version as a PR comment using `gh pr comment`.

#### GitHub Comment Format — Issues Found

```markdown
### Code review

**Tests:** X passed, Y failed (list failures if any)
**Vision:** PASS / WARN (brief note if any vision concerns)
**Docs:** UP TO DATE / NEEDS UPDATE (list files if any)

Found N issues:

1. <brief description> (CLAUDE.md says "<...>")

<link to file and line with full sha1 + line range>

2. <brief description> (security: <vulnerability type>)

<link to file and line with full sha1 + line range>

3. <brief description> (vision: <which "Is Not" guardrail or principle>)

<link to file and line with full sha1 + line range>

4. <brief description> (docs: <which file> — <what's stale/missing>)

<link to file and line with full sha1 + line range>

Generated with [Claude Code](https://claude.ai/code)

<sub>If this review was useful, react with a thumbs up. Otherwise, thumbs down.</sub>
```

#### GitHub Comment Format — No Issues

```markdown
### Code review

**Tests:** X passed, 0 failed
**Vision:** PASS
**Docs:** UP TO DATE / NEEDS UPDATE (list files if any)

No issues found. <If submit_pr_ran: "Checked: bugs, historical context, code intent (post-submit-pr mode — compliance, security, vision, docs already verified)." Otherwise if small_pr: "Checked: bugs, security, historical context (small PR mode)." Otherwise: "Checked: bugs, security, CLAUDE.md compliance, vision alignment, documentation freshness, historical context, code intent.">

Generated with [Claude Code](https://claude.ai/code)
```

### Step 10: Final Summary

After posting, print:

```
────────────────────────────────────────────
  💬 Review posted to PR #<N>
  🌐 <PR URL>
  👉 Next: address issues above, or /commit when ready
────────────────────────────────────────────
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
