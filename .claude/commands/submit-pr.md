---
name: submit-pr
description: Validate, audit, and create a pull request with full pre-submission checks
allowed-tools: Bash, Read, Edit, Grep, Glob, Task
---

# /submit-pr Skill

Run a full validation suite on the current branch and create a pull request with a well-structured description. This is the single skill for "validate and submit my work."

## Usage

```
/submit-pr                          # Submit PR against main
/submit-pr develop                  # Submit PR against a specific base branch
/submit-pr --skip-checks            # Skip validation (not recommended)
/submit-pr --draft                  # Create as draft PR
/submit-pr --checks-only            # Run all checks but don't create the PR
```

## Workflow

### Step 1: Pre-flight (parallel)

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

**C — Merge conflicts:**
```bash
git merge-tree $(git merge-base main HEAD) main HEAD
```

Verify:
- We're on a feature branch (not main)
- There are commits ahead of the base branch
- No uncommitted changes (warn if present, suggest committing first)
- Branch can merge cleanly (if not, list conflicting files)

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

### Step 3: Code Quality (parallel, unless --skip-checks)

If `--skip-checks` is passed, warn that this is not recommended and skip to Step 8.

Run all checks in parallel:

**A — Lint:**
```bash
ruff check src/ tests/ 2>&1 | tail -30
```

**B — Format:**
```bash
ruff format --check src/ tests/ 2>&1 | tail -30
```

**C — Unit tests:**
```bash
pytest tests/unit/ -v --tb=short 2>&1 | tail -80
```

**D — Type check:**
```bash
mypy src/ --ignore-missing-imports 2>&1 | tail -30
```

These are **blocking** — if any fail, abort. The user must fix issues before submitting.

### Step 4: Test Coverage for New Code

Check that new or modified Python source files have corresponding unit tests.

1. Get the list of added/modified Python files under `src/`:
   ```bash
   git diff --name-only --diff-filter=AM $BASE..HEAD -- 'src/**/*.py'
   ```
2. For each file (e.g., `src/anteroom/services/approvals.py`), check if a corresponding test file exists:
   - Expected location: `tests/unit/test_<module_name>.py` (e.g., `tests/unit/test_approvals.py`)
   - Also check: `tests/unit/test_<parent>_<module>.py` (e.g., `tests/unit/test_services_approvals.py`)
3. For modified files (not new), check if the test file was also modified — warn if source changed but tests didn't.
4. Flag any new Python modules under `src/` that have zero corresponding test files.

Report format:
```
🧪 Test Coverage:
  src/anteroom/services/approvals.py  -> ❌ MISSING (no test file found)
  src/anteroom/routers/chat.py        -> ⚠️ WARNING (source changed, tests unchanged)
  src/anteroom/tools/canvas.py        -> ✅ OK (tests/unit/test_canvas.py exists)
```

### Step 5: Deep Analysis (parallel Sonnet agents, unless --skip-checks)

Launch 5 parallel Sonnet agents:

**Agent A — Test Thoroughness:**

For any test files that exist (from Step 4), evaluate test quality:

1. Read the new/modified source files and their corresponding test files
2. For each source file, identify:
   - All public functions/methods and classes
   - Branching logic (if/else, try/except, match/case)
   - Error paths and edge cases (empty inputs, None values, timeouts, exceptions)
   - Async patterns (locks, futures, concurrent access)
   - External dependencies that should be mocked
3. Check test thoroughness:
   - **Coverage of public API**: Every public function/method should have at least one test. Flag untested functions.
   - **Happy path + error paths**: Tests should cover both success and failure cases. Flag functions with only happy-path tests.
   - **Edge cases**: For functions with branching logic, tests should exercise each branch. Flag branches with no coverage.
   - **Async/concurrency**: For async code with locks, futures, or timeouts, tests should verify concurrent behavior and timeout handling.
   - **Input validation**: If a function validates input, tests should cover valid and invalid inputs.
   - **Mocking**: External dependencies (DB, API calls, file I/O) should be mocked in unit tests.

Rate overall thoroughness: GOOD (>80% of paths covered), WEAK (50-80%), POOR (<50%).

**Agent B — CLAUDE.md Compliance:**

1. Get the diff: `git diff $BASE..HEAD`
2. Read `CLAUDE.md`
3. Check:
   - Commit messages follow format: `type(scope): description (#issue)`
   - Every commit references a GitHub issue
   - New modules are documented in CLAUDE.md if architecturally significant
   - Security patterns are followed (parameterized queries, input validation, no hardcoded secrets)
   - New endpoints have appropriate auth/CSRF protection

**Agent C — Documentation Freshness:**

1. Get the list of changed files: `git diff --name-only $BASE..HEAD`
2. Read `CLAUDE.md`, `README.md`, and `VISION.md`
3. Check each documentation surface:

**CLAUDE.md — Key Modules & Architecture:**
- New Python modules under `src/anteroom/` not listed in the "Key Modules" section? Flag as MISSING.
- Modified modules whose CLAUDE.md description no longer matches reality? Flag as STALE.
- New routers, tools, or services that change the architecture diagram? Flag as STALE.
- New config fields in `config.py` not documented in the "Configuration" section? Flag as MISSING.
- New database tables or columns in `db.py` not documented in the "Database" section? Flag as MISSING.
- New `AgentEvent(kind=...)` values in `agent_loop.py` not documented? Flag as MISSING.
- Changes to middleware, auth, or security not reflected in "Security Model"? Flag as STALE.

**README.md:**
- New CLI commands or flags not mentioned in README? Flag as MISSING.
- Feature descriptions that no longer match current behavior? Flag as STALE.

**VISION.md:**
- New capabilities that should be reflected in "Current Direction"? Flag as MISSING.
- Changes that affect scope boundaries (in/out of scope)? Flag for review.

**MkDocs docs/ pages:**
- For each changed source file, identify the corresponding docs page(s):
  - `src/anteroom/cli/` changes → check `docs/cli/`
  - `src/anteroom/routers/` changes → check `docs/api/` and `docs/web-ui/`
  - `src/anteroom/tools/` changes → check `docs/cli/tools.md`
  - `src/anteroom/config.py` changes → check `docs/configuration/`
  - Security changes → check `docs/security/`
  - `app.py` middleware changes → check `docs/security/` and `docs/advanced/architecture.md`
- Flag docs pages that reference behavior the PR changed but weren't updated.
- Flag new features with no corresponding docs page.

Rate overall documentation: UP TO DATE / NEEDS UPDATE (list specific files).

**Agent D — Vision Alignment:**

1. Read `VISION.md`
2. Read the diff: `git diff $BASE..HEAD`
3. Check against **negative guardrails** ("What Anteroom Is Not"):
   - Does this change make Anteroom more like a walled garden, ChatGPT clone, configuration burden, enterprise software, deployment project, or model host?
4. Check for **complexity creep**:
   - New dependencies added to `pyproject.toml`? Are they justified?
   - New config options added? Could a default work instead?
   - New infrastructure requirements? Do they degrade gracefully?
5. Check **dual-interface parity**:
   - Web-only feature without CLI consideration?
   - CLI-only feature without web consideration?
6. Check **lean principle**:
   - Could this change be simpler?
   - Are there new abstractions for one-time operations?
   - Are there settings where defaults would suffice?

**Agent E — Security Scan (OWASP ASVS Level 2):**

1. Get the diff: `git diff $BASE..HEAD`
2. Read modified Python and JavaScript files
3. Check against ASVS Level 2 categories:

   **V2 — Authentication:**
   - New endpoints use established auth (no custom auth schemes)
   - No credentials in logs, URLs, or error messages

   **V3 — Session Management:**
   - Cookies set with HttpOnly, Secure, SameSite
   - Session invalidation on auth state changes

   **V4 — Access Control:**
   - Server-side auth/authz on every new endpoint
   - Deny by default, least privilege
   - IDOR protection (validate ownership)

   **V5 — Input Validation:**
   - All input validated server-side
   - Parameterized queries (no SQL concatenation)
   - No eval/exec/Function with user input
   - Context-appropriate output encoding

   **V6 — Cryptography:**
   - No custom crypto, use standard algorithms
   - Secrets from env/vault, never hardcoded
   - CSPRNG for tokens/keys

   **V7 — Error Handling & Logging:**
   - No stack traces or internal details exposed to users
   - Security events logged (auth, access denied, privilege changes)
   - No sensitive data in logs

   **V13 — API Security:**
   - Rate limiting on new endpoints
   - Content-Type validation
   - CSRF protection on state-changing endpoints

   **V14 — Configuration:**
   - Security headers present (CSP, X-Content-Type-Options, X-Frame-Options)
   - No server version headers exposed

   Also check for:
   - Command injection in tool/bash handling
   - Path traversal
   - XSS in HTML/JS output
   - Insecure defaults

### Step 6: GitHub Issue Check

Verify all commits reference a GitHub issue:
```bash
git log --oneline $BASE..HEAD
```

For each commit, check that it contains `(#N)` where N is a valid issue number. For any referenced issues, verify they exist:
```bash
gh issue view <N> --json state,title --jq '"\(.state): \(.title)"'
```

### Step 7: Display Validation Report

Display the full validation results locally in the chat:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🔍 PR Validation
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  🔀 Target:   <branch> → main
  📊 Commits:  N commits, M files changed

📋 Branch Status
  Uncommitted:    ✅ / ⚠️ N files
  Merge:          ✅ / ⚠️ N conflicts
  Issues:         ✅ / ⚠️ N commits missing references

📋 Code Quality
  Lint:           ✅ / ❌ N errors
  Format:         ✅ / ❌ N files
  Tests:          ✅ / ❌ N passed, M failed
  Type Check:     ✅ / ❌ / ⏭️

🧪 Test Coverage
  Test Files:     ✅ / ❌ N new modules missing tests
  Thoroughness:   GOOD / WEAK / POOR

📝 Compliance
  CLAUDE.md:      ✅ / ⚠️ N issues

🔒 Security
  OWASP ASVS:    ✅ / ⚠️ N issues

📖 Documentation
  CLAUDE.md:      ✅ / ⚠️ N sections stale or missing
  README.md:      ✅ / ⚠️ <details>
  VISION.md:      ✅ / ⚠️ <details>
  docs/ pages:    ✅ / ⚠️ N pages need updates

🎯 Vision Alignment
  Guardrails:     ✅ / ⚠️ <details>
  Complexity:     ✅ / ⚠️ <new deps, config>
  Interface:      ✅ / ⚠️ <parity>
  Lean:           ✅ / ⚠️ <simplicity>

Details:
  - [list any failures or warnings with specifics]

────────────────────────────────────────────
  ✅ Result: READY  /  ❌ Result: NOT READY
────────────────────────────────────────────
```

**Blocking** (Result: NOT READY):
- Tests, lint, or format fail
- Security issues found
- Missing test files for new modules
- POOR test thoroughness

**Warnings** (Result: READY with warnings):
- Uncommitted changes
- Missing issue references on some commits
- WEAK test thoroughness
- Documentation needs updates
- Vision alignment concerns

If `--checks-only` was passed, stop here. Do not create the PR.

If Result is NOT READY, abort and show what to fix. Do not create the PR.

### Step 8: Generate PR Description

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

## Documentation

<If the validation report flagged stale or missing docs, list them here as action items. Otherwise omit this section.>

---
Generated with [Claude Code](https://claude.ai/code)
```

### Step 9: Push and Create PR

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

### Step 10: Post-creation Report

```bash
gh pr view --json number,url,title
```

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🚀 PR Created
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  🔗 PR:       #<N> — <title>
  🌐 URL:      <url>
  🔀 Base:     main ← <branch>
  📎 Issues:   Closes #<N>, Addresses #<N>
  📌 Status:   <ready | draft>
  🧪 Checks:   ✅ lint, format, tests, types
  🔒 Security: ✅ / ⚠️ N issues
  📖 Docs:     ✅ up to date / ⚠️ N updates needed
  🎯 Vision:   ✅ supports <principles>

────────────────────────────────────────────
  🔍 Running code review automatically...
────────────────────────────────────────────
```

### Step 11: Automatic Code Review

Immediately after PR creation, run the full `/code-review` workflow on the new PR (Steps 1–10 of the code-review skill). Display the local review report in chat and post the condensed comment to the PR.

### Step 12: Fix Loop (if issues found)

If the code review finds issues (score 80+):

1. Display all issues with full context (as per code-review Step 8)
2. Ask the user:

```
────────────────────────────────────────────
  🔧 Code review found N issues.

  Options:
    → Fix all — auto-fix what I can, then re-review
    → Fix specific — tell me which to fix
    → Skip — leave as-is for a human reviewer
────────────────────────────────────────────
```

3. **If "Fix all" or "Fix specific":**
   - Apply fixes to the code
   - Run lint + format + tests to verify fixes don't break anything
   - Stage and commit: `fix(scope): address code review feedback (#<issue>)`
   - Push: `git push`
   - Re-run the code review (Steps 1–10 of code-review skill)
   - If new issues are found, repeat (max 2 fix rounds to avoid infinite loops)
   - Post an updated review comment to the PR

4. **If "Skip":**
   - Proceed without fixing. The review comment is already posted.

### Step 13: Final Summary

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✅ PR Ready for Review
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  🔗 PR:          #<N> — <title>
  🌐 URL:         <url>
  🔍 Code Review: ✅ clean / ⚠️ N issues remaining
  🔄 Fix Rounds:  <0-2> rounds applied

────────────────────────────────────────────
  👉 Next: wait for CI, or request human review
────────────────────────────────────────────
```

## Guidelines

- Never create a PR without at least one issue reference
- Never create a PR with failing tests (unless --skip-checks)
- Keep the PR title concise — details go in the body
- Group changes logically in the description
- If the PR is large (>500 lines changed), suggest breaking it up
- Security section only when relevant — don't add boilerplate
- Documentation warnings don't block PR creation, but flag them in the PR body
