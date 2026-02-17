---
name: pr-check
description: Pre-validate current branch before opening a PR
allowed-tools: Bash, Read, Edit, Grep, Glob, Task
---

# /pr-check Skill

Validate a branch is ready to open (or merge) a PR. Catches issues before CI and reviewers do.

## Usage

```
/pr-check                              # Check current branch against main
/pr-check develop                      # Check against a specific base branch
/pr-check --branch feature/foo         # Check a local branch (without switching)
/pr-check --pr 86                      # Fetch and check a GitHub PR by number
/pr-check --worktree /path/to/wt       # Check code in an existing worktree
```

Arguments are parsed positionally/by flag:
- A bare argument (no `--` prefix) is the **base branch** (default: `main`).
- `--branch <name>` targets a local branch. A temporary worktree is created, used for all checks, and cleaned up afterward.
- `--pr <number>` fetches the PR's head ref into a temporary local branch, creates a worktree, runs all checks there, and cleans up afterward.
- `--worktree <path>` uses an existing worktree directory. No cleanup is performed.
- Flags can be combined: `/pr-check --pr 86 develop` checks PR #86 against `develop`.

## Workflow

### Step 0: Resolve Target

1. **Determine the base branch.** Use the bare positional argument if provided, otherwise default to `main`.
2. **Determine the working directory** based on the flags:

   | Flag | Action | Working directory |
   |------|--------|-------------------|
   | _(none)_ | Use current branch in current repo | Current repo root |
   | `--branch <name>` | Create temp worktree from local branch | Temp worktree path |
   | `--pr <N>` | `git fetch origin pull/<N>/head:pr-<N>-check`, create temp worktree | Temp worktree path |
   | `--worktree <path>` | Validate path exists and is a git worktree | Provided path |

3. **For temp worktrees:** create under `<repo-parent>/<repo-name>-pr-check-<id>`. Record the path so it can be cleaned up in Step 9.
4. **All subsequent git and tool commands must run inside the resolved working directory** (prefix with `cd <dir> &&`). Never modify the user's current working tree.

### Step 1: Determine Base Branch

If a base branch is provided as an argument, use it. Otherwise default to `main`.

### Step 2: Branch Status (parallel)

All commands below use `$DIR` as the resolved working directory from Step 0. Prefix every command with `cd $DIR &&`.

Run these checks in parallel:

**A — Uncommitted changes:**
```bash
cd $DIR && git status --short
```
Warn if there are uncommitted or untracked files. For `--pr` and `--branch` targets (clean worktrees), this should be empty — flag if not.

**B — Branch divergence:**
```bash
cd $DIR && git log --oneline $BASE..HEAD
cd $DIR && git diff --stat $BASE..HEAD
```
Summarize what commits and files will be in the PR.

**C — Merge conflicts:**
```bash
cd $DIR && git merge-tree $(git merge-base $BASE HEAD) $BASE HEAD
```
Check if the branch can merge cleanly. If not, list conflicting files.

### Step 3: Code Quality (parallel)

Run all checks in parallel. If the worktree doesn't have deps installed, run `pip install -e ".[dev]" -q` first.

**A — Lint:**
```bash
cd $DIR && ruff check src/ tests/ 2>&1 | tail -30
```

**B — Format:**
```bash
cd $DIR && ruff format --check src/ tests/ 2>&1 | tail -30
```

**C — Unit tests:**
```bash
cd $DIR && pytest tests/unit/ -v --tb=short 2>&1 | tail -80
```

**D — Type check (if mypy configured):**
```bash
cd $DIR && mypy src/ --ignore-missing-imports 2>&1 | tail -30
```

### Step 4: Test Coverage for New Code

Check that new or modified Python source files have corresponding unit tests.

1. Get the list of added/modified Python files under `src/`:
   ```bash
   cd $DIR && git diff --name-only --diff-filter=AM $BASE..HEAD -- 'src/**/*.py'
   ```
2. For each file (e.g., `src/anteroom/services/approvals.py`), check if a corresponding test file exists:
   - Expected location: `tests/unit/test_<module_name>.py` (e.g., `tests/unit/test_approvals.py`)
   - Also check: `tests/unit/test_<parent>_<module>.py` (e.g., `tests/unit/test_services_approvals.py`)
3. For modified files (not new), check if the test file was also modified — warn if source changed but tests didn't.
4. Flag any new Python modules under `src/` that have zero corresponding test files.

Report format:
```
Test Coverage Check:
  src/anteroom/services/approvals.py  -> MISSING (no test file found)
  src/anteroom/routers/approvals.py   -> MISSING (no test file found)
  src/anteroom/routers/chat.py        -> WARNING (source changed, tests unchanged)
  src/anteroom/tools/canvas.py        -> OK (tests/unit/test_canvas.py exists)
```

### Step 5: Test Thoroughness Audit (Sonnet agent)

For any test files that exist (from Step 4), launch a Sonnet agent to evaluate test quality:

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

Report format:
```
Test Thoroughness:
  services/approvals.py:
    ApprovalManager.request()   -> OK (2 tests: success, duplicate)
    ApprovalManager.wait()      -> WEAK (1 test: success only, missing: timeout, cancellation)
    ApprovalManager.resolve()   -> MISSING (no tests)

  routers/approvals.py:
    respond_approval()          -> WEAK (1 test: success only, missing: invalid ID, manager unavailable)
```

Rate overall thoroughness: GOOD (>80% of paths covered), WEAK (50-80%), POOR (<50%).

### Step 5b: CLAUDE.md Compliance (Sonnet agent)

Launch a Sonnet agent to check the diff against CLAUDE.md:

1. Get the diff: `cd $DIR && git diff $BASE..HEAD`
2. Read `$DIR/CLAUDE.md`
3. Check:
   - Commit messages follow format: `type(scope): description (#issue)`
   - Every commit references a GitHub issue
   - New modules are documented in CLAUDE.md if architecturally significant
   - Security patterns are followed (parameterized queries, input validation, no hardcoded secrets)
   - New endpoints have appropriate auth/CSRF protection

### Step 5c: Vision Alignment (Sonnet agent)

Launch a Sonnet agent to check the diff against the product vision:

1. Read `$DIR/VISION.md`
2. Read the diff: `cd $DIR && git diff $BASE..HEAD`
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

Report format:
```
Vision Alignment:
  Negative guardrails:  [PASS] / [WARN] <details>
  Complexity:           [PASS] / [WARN] <new deps, config, infra>
  Interface parity:     [PASS] / [WARN] <web-only or CLI-only>
  Lean:                 [PASS] / [WARN] <unnecessary complexity>
```

### Step 6: Security Scan — OWASP ASVS Level 2 (Sonnet agent)

Launch a Sonnet agent to scan the diff against OWASP ASVS Level 2 requirements:

1. Get the diff: `cd $DIR && git diff $BASE..HEAD`
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

### Step 7: GitHub Issue Check

Verify all commits reference a GitHub issue:
```bash
cd $DIR && git log --oneline $BASE..HEAD
```

For each commit, check that it contains `(#N)` where N is a valid issue number. For any referenced issues, verify they exist:
```bash
gh issue view <N> --json state,title --jq '"\(.state): \(.title)"'
```

### Step 8: Report

Print a summary report:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🔍 PR Pre-Check
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  🔀 Target:   feature/my-branch -> main
  📂 Source:   current branch | --branch | --pr N | --worktree
  📁 Dir:      /path/to/dir
  📊 Commits:  N commits, M files changed

📋 Branch Status
  Uncommitted:    ✅ / ⚠️ N files
  Merge:          ✅ / ⚠️ N conflicts

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
  Security:       ✅ / ⚠️ N issues
  Issues:         ✅ / ⚠️ N commits missing references

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

👉 Next steps:
  - <what to fix or do next>
```

If any critical checks fail (tests, lint, security, missing test files, POOR thoroughness), report `Ready to PR: NO`.
If only warnings (uncommitted changes, missing issue refs, WEAK thoroughness), report `Ready to PR: YES (with warnings)`.

### Step 9: Cleanup

If a **temporary worktree** was created (via `--branch` or `--pr`):

1. Remove the worktree: `git worktree remove <path>`
2. Delete the temporary local branch: `git branch -D <branch-name>`
3. Confirm cleanup in the report footer.

If `--worktree` was used (user-provided), do **not** remove anything.
