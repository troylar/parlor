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

### Step 4: Deep Review (7 parallel Sonnet agents)

Launch 7 parallel Sonnet agents. Each agent works through a **structured checklist** against the diff — not open-ended scanning. This ensures consistent, reproducible results across runs.

Every agent MUST:
1. Read the full diff (`gh pr diff <PR>`)
2. Walk through every checklist item, one by one
3. For each item, report: PASS (no issue), FAIL (issue found with file:line), or N/A (not applicable to this diff)
4. Only report FAIL items in the results — do not report PASS or N/A items

---

**Agent #1 — CLAUDE.md compliance checklist:**

Read all relevant CLAUDE.md files, then check each item against the diff:

- [ ] New Python modules under `src/anteroom/` follow existing naming conventions
- [ ] New routers use the same middleware/auth pattern as existing routers
- [ ] New tools follow the ToolRegistry pattern (`_handlers` + `_definitions`)
- [ ] Database queries use parameterized queries (no string concatenation)
- [ ] New endpoints have Content-Type validation for JSON bodies
- [ ] Async functions use `await` correctly (no fire-and-forget without `asyncio.create_task`)
- [ ] New config fields have env var overrides with `AI_CHAT_` prefix documented
- [ ] Error responses don't expose internal details (stack traces, SQL errors)
- [ ] Commit messages follow `type(scope): description (#issue)` format

---

**Agent #2 — Bug scan checklist:**

Read the diff, then check each line of changed code against this checklist:

- [ ] **Return values**: Are return values checked? Any function that can return None used without a None check?
- [ ] **Off-by-one**: Loop bounds, slice indices, range() calls — do they include/exclude the right endpoints?
- [ ] **Type mismatches**: String where int expected? List where dict expected? Especially in JSON parsing and API responses.
- [ ] **Null/empty handling**: What happens if a list is empty, a dict key is missing, a string is blank, or a query returns no rows?
- [ ] **Resource leaks**: Are files, DB connections, HTTP sessions properly closed? Are `async with` / `with` used for context managers?
- [ ] **Concurrency**: Any shared mutable state accessed without locks? Race conditions in async code?
- [ ] **Exception handling**: Are exceptions caught too broadly (`except Exception`)? Are they swallowed silently? Is cleanup code in `finally` blocks?
- [ ] **String formatting**: f-strings with user input that could break? SQL or command strings built with concatenation?
- [ ] **Import errors**: Missing imports, circular imports, imports that only work in certain Python versions?
- [ ] **Logic inversion**: Negated conditions that should be positive (or vice versa)? `and`/`or` confusion?

---

**Agent #3 — Security audit checklist:**

Read the diff and modified files, then check each item:

- [ ] **SQL injection**: Any raw SQL with string formatting or concatenation? (Must use parameterized queries)
- [ ] **Command injection**: Any `subprocess`, `os.system`, or tool execution with user-controlled input not sanitized?
- [ ] **Path traversal**: File operations using user input without path validation? (`..` not blocked, symlinks not resolved)
- [ ] **XSS**: User input rendered in HTML/JS without encoding? `innerHTML` with unsanitized data?
- [ ] **CSRF**: State-changing endpoints missing CSRF protection? Origin header validation bypassed?
- [ ] **Auth bypass**: New endpoints accessible without authentication? Missing session checks?
- [ ] **Hardcoded secrets**: API keys, passwords, tokens in source code? (Check string literals and comments)
- [ ] **Insecure defaults**: Debug mode enabled? Auth disabled? Permissive CORS? TLS verification skipped?
- [ ] **Input validation**: User input accepted without type/length/range validation at system boundaries?
- [ ] **Information disclosure**: Error messages that reveal internal paths, versions, or stack traces?
- [ ] **Unsafe deserialization**: `pickle.loads`, `yaml.load` (not safe_load), `eval()`, `exec()` with external input?
- [ ] **Cookie security**: New cookies missing HttpOnly, Secure, or SameSite flags?
- [ ] **Rate limiting**: New public endpoints without rate limiting?
- [ ] **Content-Type**: Endpoints accepting request bodies without Content-Type validation?

---

**Agent #4 — Historical context checklist:**

Read git blame and history for each modified file:

- [ ] **Reverted fixes**: Does this change undo or weaken a previous bug fix? Check `git log --oneline <file>` for fix commits.
- [ ] **Recurring patterns**: Has this file had similar bugs before? Check recent `fix:` commits touching these files.
- [ ] **TODO/FIXME regression**: Are there TODO or FIXME comments in the modified areas? Does this change address or ignore them?
- [ ] **Previous review feedback**: Check `gh pr list --search "<file>" --state merged --limit 3` for previous PR comments on these files. Are past concerns addressed?
- [ ] **Breaking change context**: Was the modified code written with specific assumptions (documented in comments or commit messages) that this change violates?

---

**Agent #5 — Code comments and intent checklist:**

Read code comments in modified files:

- [ ] **Invariant violations**: Are there comments stating invariants (e.g., "this must be called after X", "never modify this without Y") that the change violates?
- [ ] **TODO completion**: Are there TODOs that this change was supposed to address but didn't fully resolve?
- [ ] **Warning heeds**: Are there `# WARNING:` or `# IMPORTANT:` comments in the modified area? Does the change comply?
- [ ] **Docstring accuracy**: Do modified functions still match their docstrings? Are parameter types still correct?
- [ ] **Security comments**: Are there `# SECURITY:` or `# SECURITY-REVIEW:` comments? Does the change maintain the security properties described?

---

**Agent #6 — Vision and scope alignment checklist:**

Read `VISION.md`, then check each item:

- [ ] **Not a walled garden**: Does this change introduce proprietary extension mechanisms, required infrastructure for extensibility, or Anteroom-specific plugin formats?
- [ ] **Not a ChatGPT clone**: Does this change add a chat-only feature with no agentic/tool value?
- [ ] **Not a configuration burden**: Does this change add features that don't work without configuration? Are there sensible defaults for every new option?
- [ ] **Not enterprise software**: Does this change add license management, SSO, admin panels, or compliance features?
- [ ] **Not a deployment project**: Does this change add Docker, Kubernetes, or external infrastructure requirements to core functionality?
- [ ] **Not a model host**: Does this change add model management, serving, benchmarking, or training features?
- [ ] **Scope check**: Does this change touch cloud/SaaS, model training, mobile native, complex deployment, admin dashboards, or IDE recreation?
- [ ] **New dependencies**: Any new entries in `pyproject.toml` dependencies? Is each justified?
- [ ] **New config options**: Any new config fields? Does each have a sensible default?
- [ ] **Dual-interface**: If web-only, is CLI omission justified? If CLI-only, is web omission justified?
- [ ] **Lean**: Could this change be simpler? Are there new abstractions for one-time operations?

Flag only if the PR **introduces** scope drift — not for pre-existing patterns.

---

**Agent #7 — Documentation freshness checklist:**

Read `CLAUDE.md`, `README.md`, `VISION.md`, and relevant `docs/` pages:

- [ ] **CLAUDE.md — Key Modules**: New Python modules under `src/anteroom/` not listed?
- [ ] **CLAUDE.md — Module descriptions**: Modified modules whose description no longer matches reality?
- [ ] **CLAUDE.md — Architecture diagram**: New routers, tools, or services that change the diagram?
- [ ] **CLAUDE.md — Configuration**: New config fields in `config.py` not documented?
- [ ] **CLAUDE.md — Database**: New tables or columns in `db.py` not documented?
- [ ] **CLAUDE.md — Agent events**: New `AgentEvent(kind=...)` values not documented?
- [ ] **CLAUDE.md — Security Model**: Auth, middleware, or security changes not reflected?
- [ ] **README.md — CLI**: New commands or flags not mentioned?
- [ ] **README.md — Features**: Feature descriptions that no longer match current behavior?
- [ ] **VISION.md — Current Direction**: New capabilities not reflected?
- [ ] **docs/ pages**: For each changed source file, check corresponding docs:
  - `src/anteroom/cli/` → `docs/cli/`
  - `src/anteroom/routers/` → `docs/api/` and `docs/web-ui/`
  - `src/anteroom/tools/` → `docs/cli/tools.md`
  - `src/anteroom/config.py` → `docs/configuration/`
  - Security changes → `docs/security/`
  - `app.py` middleware → `docs/security/` and `docs/advanced/architecture.md`
- [ ] **New features**: Any new feature with no corresponding docs page?

Report stale or missing documentation as issues with `(docs: <which file> — <what's stale/missing>)`.

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

  Checked: bugs, security, CLAUDE.md compliance,
           vision alignment, documentation freshness

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

No issues found. Checked for bugs, security, CLAUDE.md compliance, vision alignment, and documentation freshness.

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
