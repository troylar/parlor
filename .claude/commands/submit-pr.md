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

### Step 0: Detect Worktree

Determine if we're running inside a git worktree:

```bash
WORKTREE_PATH=$(git rev-parse --show-toplevel)
MAIN_WORKTREE=$(git worktree list --porcelain | head -1 | sed 's/worktree //')
```

If `$WORKTREE_PATH` != `$MAIN_WORKTREE`, we are in a worktree. **All file reads, edits, and commands MUST use the worktree path, not the main checkout.** Display the worktree path prominently in the pre-flight output:

```
  📂 Worktree:  /path/to/worktree
```

If not in a worktree, display:
```
  📂 Working directory: /path/to/repo
```

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

If `--skip-checks` is passed, warn that this is not recommended and skip to Step 9.

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

### Step 3b: Dependency Health (parallel, unless --skip-checks)

Run dependency health checks in parallel. Launch these concurrently with Steps 4 and 5 — do not wait for 3b to complete before starting Step 4.

**E — Vulnerability audit:**

First check if pip-audit is available:
```bash
which pip-audit 2>/dev/null
```

If not installed, skip with ⏭️ and emit: `⚠️ pip-audit not installed — install with: pip install anteroom[dev]`

If installed, run:
```bash
pip-audit 2>&1 | tail -40
```

This is **blocking** — if known vulnerabilities are found, abort. The user must fix or pin safe versions before submitting.

**F — Outdated dependencies:**
```bash
pip list --outdated --format=json 2>&1
```

Parse the JSON output. This is a **warning** (non-blocking). Report a compact summary:
```
⚠️ Outdated: N packages have newer versions available
   package-name  1.2.3 → 1.3.0
   other-pkg     0.9.1 → 1.0.0
```

If all packages are up to date, report ✅. Limit display to 10 most outdated packages; if more, show count.

**G — New dependency review (conditional):**

Only run this check if `pyproject.toml` is in the diff:
```bash
git diff --name-only $BASE..HEAD -- pyproject.toml
```

If `pyproject.toml` changed:

1. Extract added/changed dependency lines from the diff. Match any added line that looks like a PEP 508 dependency (not just `>=`):
   ```bash
   git diff $BASE..HEAD -- pyproject.toml | grep '^+' | grep -v '^+++' | grep -E '^\+\s*"[a-zA-Z]'
   ```
   Extract the package name from each line (the part before any version specifier: `>=`, `==`, `~=`, `!=`, `<`, `>`, `[`, or end of string).

2. For each new or changed package, query the PyPI JSON API with error handling:
   ```bash
   curl -s --max-time 5 "https://pypi.org/pypi/<package>/json" | python3 -c "
   import sys, json
   try:
       d = json.load(sys.stdin)
       info = d['info']
       version = info.get('version', 'unknown')
       upload_time = 'unknown'
       if d.get('urls'):
           upload_time = d['urls'][0].get('upload_time', 'unknown')[:10]
       print(f\"License: {info.get('license') or info.get('license_expression') or 'UNKNOWN'}\")
       print(f\"Latest: {version} ({upload_time})\")
       print(f\"Summary: {info.get('summary', 'N/A')}\")
   except Exception:
       print('ERROR: PyPI unreachable or invalid response')
   "
   ```
   If the query fails (ERROR output or curl timeout), report the package as `⚠️ [SKIP] PyPI unavailable` rather than failing.

3. Flag as warning if:
   - License is unknown or empty
   - Last release was more than 2 years ago (possibly abandoned)
   - Package has no summary (minimal metadata)
   - PyPI query failed

This is a **warning** (non-blocking). Report format:
```
📦 New/Changed Dependencies:
  aiohttp (>=3.12.14)  — License: Apache-2.0, Latest: 3.12.14 (2026-01-15) ✅
  obscure-pkg (>=1.0)  — License: UNKNOWN, Latest: 1.0.0 (2022-03-01) ⚠️ stale, no license
  air-gapped-pkg       — ⚠️ [SKIP] PyPI unavailable
```

If `pyproject.toml` was not changed, skip with ⏭️.

**H — Semgrep SAST scan:**

First check if semgrep is available:
```bash
which semgrep 2>/dev/null
```

If not installed, skip with ⏭️ and emit: `⚠️ semgrep not installed — install with: pip install anteroom[dev]`

If installed, run:
```bash
semgrep scan --config p/python --config p/security-audit --json src/ 2>&1
```

Parse the JSON output. Count findings by severity (error, warning, info).

This is **blocking** — if any error-severity findings are found, abort. Warning-severity findings are non-blocking but reported. Report format:
```
🔒 SAST (Semgrep): ✅ no findings / ❌ N error findings, M warnings
   rule-id: description (src/path/file.py:line)
```

### Step 4: Test Coverage for New Code

Check that new or modified source files have corresponding tests at all levels.

#### 4a: Unit Test Coverage

1. Get the list of added/modified Python files under `src/`:
   ```bash
   git diff --name-only --diff-filter=AM $BASE..HEAD -- 'src/**/*.py'
   ```
2. For each file (e.g., `src/anteroom/services/approvals.py`), check if a corresponding test file exists:
   - Expected location: `tests/unit/test_<module_name>.py` (e.g., `tests/unit/test_approvals.py`)
   - Also check: `tests/unit/test_<parent>_<module>.py` (e.g., `tests/unit/test_services_approvals.py`)
3. For modified files (not new), check if the test file was also modified — warn if source changed but tests didn't.
4. Flag any new Python modules under `src/` that have zero corresponding test files.

#### 4b: UX Test Coverage

Check whether changed files require UX-level tests (see `.claude/rules/ux-testing.md`):

1. **Web UI changes** — files under `routers/`, `static/js/`, `static/css/`, `static/index.html`:
   - Check for new/modified Playwright tests in `tests/e2e/test_ui_*.py`
   - Check for new/modified JS unit tests if `static/js/*.js` changed
   - Flag as ⚠️ if UI code changed but no UX tests added or modified

2. **CLI changes** — files under `cli/repl.py`, `cli/commands.py`, `cli/layout.py`, `cli/renderer.py`, `cli/event_handlers.py`, `cli/pickers.py`, `cli/dialogs.py`:
   - Check for new/modified integration tests in `tests/integration/test_repl_*.py`
   - Check for new/modified snapshot tests if `cli/layout.py` or `cli/renderer.py` changed
   - Flag as ⚠️ if CLI UX code changed but no UX tests added or modified

3. **Shared core changes** — files under `services/agent_loop.py`, `tools/`:
   - Both web UI and CLI UX tests should be checked
   - Flag as ⚠️ if shared code changed but neither interface has UX test coverage

Report format:
```
🧪 Test Coverage:
  Unit Tests:
    src/anteroom/services/approvals.py  -> ❌ MISSING (no test file found)
    src/anteroom/routers/chat.py        -> ⚠️ WARNING (source changed, tests unchanged)
    src/anteroom/tools/canvas.py        -> ✅ OK (tests/unit/test_canvas.py exists)

  UX Tests:
    src/anteroom/routers/chat.py        -> ⚠️ WARNING (router changed, no Playwright test changes)
    src/anteroom/static/js/chat.js      -> ⚠️ WARNING (JS changed, no JS unit test found)
    src/anteroom/cli/commands.py        -> ⚠️ WARNING (CLI UX changed, no integration test changes)
    src/anteroom/services/storage.py    -> ✅ OK (backend-only, no UX tests needed)
```

### Step 5: Deep Analysis (parallel agents, unless --skip-checks)

Launch 5 parallel agents. Use **Haiku** for Agents C and D (docs freshness, vision alignment — lightweight checks). Use **Sonnet** for Agents A, B, and E (test analysis, compliance, security — require deeper reasoning).

**IMPORTANT for ALL agents:** Report ONLY failures and warnings. Do not report passing checks. Keep response under 500 words.

**Agent A — Test Thoroughness (Sonnet):**

Read the new/modified source files and their corresponding test files. Check:
- [ ] Every public function/method has at least one test
- [ ] Both happy path and error paths covered
- [ ] Branching logic (if/else, try/except) has tests for each branch
- [ ] Async code with locks/futures/timeouts has concurrency tests
- [ ] External dependencies (DB, API, file I/O) are mocked
- [ ] Input validation functions tested with valid and invalid inputs

Rate: GOOD (>80% paths), WEAK (50-80%), POOR (<50%).

**Agent B — CLAUDE.md Compliance (Sonnet):**

Get the diff (`git diff $BASE..HEAD`) and read `CLAUDE.md`. Check:
- [ ] Commit messages follow `type(scope): description (#issue)`
- [ ] Every commit references a GitHub issue
- [ ] New architecturally significant modules documented in CLAUDE.md
- [ ] Security patterns followed (parameterized queries, input validation, no hardcoded secrets)
- [ ] New endpoints have auth/CSRF protection

**Agent C — Documentation Freshness (Haiku, Authoritative):**

This is the authoritative doc review — identifies stale/missing docs AND applies fixes.

1. Get changed files: `git diff --name-only $BASE..HEAD`
2. Read `CLAUDE.md`, `README.md`, `VISION.md`, and relevant `docs/` pages
3. Check each surface — flag as MISSING or STALE:

**CLAUDE.md:** New modules not in "Key Modules"? Modified modules with stale descriptions? New config/DB/event fields undocumented? Security model changes not reflected?

**README.md:** The README serves as marketing — it must showcase what Anteroom can do. Check:
- New user-facing features (`feat:` commits) reflected in the "What makes it different" grid or "The full picture" table?
- New CLI commands/flags/modes reflected in the CLI REPL example or exec mode section?
- New tools added to the built-in tools list?
- New MCP capabilities or integration patterns mentioned?
- Feature descriptions stale or misleading after this PR's changes?
- Install instructions still accurate?
- If the PR adds a significant user-visible feature and the README doesn't mention it, flag as STALE.

**VISION.md:** New capabilities not in "Current Direction"? Scope boundary changes?

**docs/ pages:** For each changed source file, check corresponding docs:
- `src/anteroom/cli/` → `docs/cli/`, `routers/` → `docs/api/` + `docs/web-ui/`, `tools/` → `docs/cli/tools.md`, `config.py` → `docs/configuration/`, security → `docs/security/`, `app.py` → `docs/security/` + `docs/advanced/architecture.md`

**a-help skill** (`src/anteroom/cli/default_skills/a-help.yaml`): This is the inline quick-reference that ships with Anteroom. If any of the following source files changed, verify the corresponding a-help sections are current:
- `tools/__init__.py` or `tools/*.py` → Built-in Tools table (tool names, descriptions, tiers)
- `__main__.py` → CLI Flags table (all argparse flags)
- `cli/commands.py` or `cli/repl.py` → REPL Commands table (slash commands)
- `config.py` → Configuration Sections table (all dataclass fields and defaults)
- `docs/` → Documentation Index (file list and descriptions)
- If a-help is stale, update it directly. Cross-check against the actual source files, not just the diff.

4. **Apply fixes** for MISSING/STALE items directly.
5. Rate: UP TO DATE / FIXED (list files) / NEEDS MANUAL REVIEW.

**Agent D — Vision Alignment (Haiku):**

Read `VISION.md` and the diff (`git diff $BASE..HEAD`). Check:
- [ ] Not a walled garden / ChatGPT clone / config burden / enterprise software / deployment project / model host
- [ ] New `pyproject.toml` dependencies justified
- [ ] New config options have sensible defaults
- [ ] New infra requirements degrade gracefully
- [ ] Dual-interface parity (web-only or CLI-only justified?)
- [ ] Lean: could this be simpler? Unnecessary abstractions?

Flag only issues **introduced by this PR**, not pre-existing patterns.

**Agent E — Security Scan, OWASP ASVS Level 2 (Sonnet):**

Get the diff (`git diff $BASE..HEAD`) and read modified Python/JS files. Check:
- [ ] **SQL injection**: No string formatting in queries (parameterized only)
- [ ] **Command injection**: No `subprocess` with `shell=True` + user input
- [ ] **Path traversal**: File ops validate paths (no unsanitized `..`)
- [ ] **XSS**: No `innerHTML` with unsanitized input
- [ ] **CSRF**: State-changing endpoints have CSRF protection
- [ ] **Auth bypass**: New endpoints require authentication
- [ ] **Hardcoded secrets**: No API keys/passwords/tokens in source
- [ ] **Insecure defaults**: No debug mode, disabled auth, permissive CORS, skipped TLS verification
- [ ] **Input validation**: User input validated server-side at boundaries
- [ ] **Info disclosure**: Error messages don't reveal internals
- [ ] **Unsafe deserialization**: No `pickle.loads`, `yaml.load`, `eval()`, `exec()` with external input
- [ ] **Cookie security**: New cookies have HttpOnly, Secure, SameSite
- [ ] **Rate limiting**: New public endpoints have rate limits
- [ ] **Content-Type**: Endpoints validate Content-Type headers

### Step 6: Commit Documentation Fixes

If Agent C in Step 5 flagged documentation as FIXED (it applied updates to CLAUDE.md, README.md, VISION.md, or docs/ pages):

1. Stage and commit the doc fixes. Extract the primary issue number from the branch name:
   ```bash
   git add CLAUDE.md README.md VISION.md docs/ src/anteroom/cli/default_skills/a-help.yaml
   git commit -m "docs: update documentation for current changes (#<primary issue>)"
   ```
2. Update the validation report to show docs as fixed rather than stale.

If Agent C rated docs as UP TO DATE, skip this step. If NEEDS MANUAL REVIEW, flag in the report but do not block PR creation.

### Step 7: GitHub Issue Check

Verify all commits reference a GitHub issue:
```bash
git log --oneline $BASE..HEAD
```

For each commit, check that it contains `(#N)` where N is a valid issue number. For any referenced issues, verify they exist:
```bash
gh issue view <N> --json state,title --jq '"\(.state): \(.title)"'
```

### Step 8: Display Validation Report

Display the full validation results locally in the chat:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🔍 PR Validation
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  📂 Worktree: <worktree path> (or "main checkout" if not in a worktree)
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

📦 Dependency Health
  Vulnerabilities: ✅ / ❌ N vulnerabilities found
  Outdated:        ✅ / ⚠️ N packages outdated
  New Deps:        ✅ / ⚠️ N new deps to review / ⏭️ no pyproject.toml changes
  SAST (Semgrep):  ✅ / ❌ N findings / ⏭️ not installed

🧪 Test Coverage
  Unit Tests:     ✅ / ❌ N new modules missing tests
  UX Tests:       ✅ / ⚠️ N UI changes missing UX tests
  Thoroughness:   GOOD / WEAK / POOR

📝 Compliance
  CLAUDE.md:      ✅ / ⚠️ N issues

🔒 Security
  OWASP ASVS:    ✅ / ⚠️ N issues

📖 Documentation
  CLAUDE.md:      ✅ / ✅ fixed / ⚠️ needs manual review
  README.md:      ✅ / ✅ fixed / ⚠️ <details>
  VISION.md:      ✅ / ✅ fixed / ⚠️ <details>
  docs/ pages:    ✅ / ✅ fixed / ⚠️ N pages need review

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
- Known vulnerabilities found by pip-audit
- Semgrep error-severity SAST findings
- Missing test files for new modules
- POOR test thoroughness

**Warnings** (Result: READY with warnings):
- Uncommitted changes
- Missing issue references on some commits
- WEAK test thoroughness
- UI code changed without corresponding UX tests
- Outdated dependencies
- New dependencies missing license or stale (>2 years since last release)
- Documentation needs updates
- Vision alignment concerns

If `--checks-only` was passed, stop here. Do not create the PR.

If Result is NOT READY, abort and show what to fix. Do not create the PR.

### Step 9: Generate PR Description

The PR description is the reviewer's primary tool. It must be rich enough that a reviewer can understand every change without reading the code first. Optimize for reviewer efficiency: a great PR description means fewer questions, faster approval, and better reviews.

#### 9a: Gather Context

```bash
git log --format='%s%n%b' main..HEAD
git diff --stat main..HEAD
git diff main..HEAD
```

Also read the primary issue body to understand the original intent and acceptance criteria:
```bash
gh issue view <N> --json body,title --jq '{title,body}'
```

#### 9b: Build Per-File Change Manifest

For every changed file, read the full diff hunk and write a detailed entry. This is the most important part of the PR description — it's what reviewers will scan first to orient themselves.

For each file in `git diff --name-only main..HEAD`:

1. **Read the file's diff**: understand what lines were added, removed, or modified
2. **Classify the change type**: new file, modified, deleted
3. **Describe the intent**: not just "what changed" but "why it changed" and "what it enables"
4. **Note reviewer focus areas**: anything subtle, security-sensitive, or architecturally significant

#### 9c: Structure the PR Body

```markdown
## Summary

<2-4 sentences describing what this PR does, why it's needed, and the approach taken. Write for a reviewer who hasn't read the issue — they should understand the full picture from this section alone.>

## Issue References

Closes #<primary issue> — <issue title>
Addresses #<secondary issue> — <issue title>

## How It Works

<1-2 paragraphs explaining the design/approach at a high level. What's the data flow? What are the key decisions? What alternatives were considered? This section helps reviewers understand the "shape" of the change before diving into files.>

## File-by-File Changes

<For every changed file, provide a detailed entry. Group by area. This section should be thorough enough that a reviewer can understand each file's changes without opening the diff.>

### Database
| File | Type | Description |
|------|------|-------------|
| `src/anteroom/db.py` | Modified | <2-3 sentences: what changed, why, any migration notes. e.g., "Added `metadata TEXT` column to messages table schema. Both the CREATE TABLE definition (for fresh installs) and the ALTER TABLE migration (for existing databases) are updated. Column is nullable with no default."> |

### Services
| File | Type | Description |
|------|------|-------------|
| `src/anteroom/services/storage.py` | Modified | <Detailed description of each function touched: new params, new functions, changed behavior. e.g., "`create_message()` now accepts optional `metadata: dict` param, serialized to JSON. New `update_message_metadata()` function for post-stream updates. `list_messages()` deserializes JSON metadata with graceful fallback on invalid JSON. `fork_conversation()` and `copy_conversation_to_db()` both preserve metadata through their INSERT statements."> |

### Web UI
| File | Type | Description |
|------|------|-------------|
| `src/anteroom/routers/chat.py` | Modified | <What changed in the router, what SSE events are affected, what data flow changed> |
| `src/anteroom/static/js/chat.js` | Modified | <What DOM changes, what events are handled, any new rendering logic> |

### CLI
| File | Type | Description |
|------|------|-------------|
| `src/anteroom/cli/renderer.py` | Modified | <What rendering changed, any new display elements> |
| `src/anteroom/cli/repl.py` | Modified | <What REPL behavior changed, resume path changes, command changes> |

### Tests
| File | Type | Description |
|------|------|-------------|
| `tests/unit/test_storage_metadata.py` | **New** | <N tests covering: list what's tested. e.g., "13 tests: create with/without metadata, update metadata, list with JSON deserialization (including invalid JSON edge case), fork preserves metadata, copy preserves metadata"> |
| `tests/unit/test_rag_provenance.py` | Modified | <What tests were added and what they verify> |

### Documentation
| File | Type | Description |
|------|------|-------------|
| `CLAUDE.md` | Modified | <What was updated and why> |

## Reviewer Guide

<Help the reviewer focus their attention. Call out:>

**Key areas to scrutinize:**
- <e.g., "JSON serialization round-trip in `storage.py` — ensure no data loss through serialize/deserialize cycle">
- <e.g., "Metadata preservation in `fork_conversation()` vs `copy_conversation_to_db()` — different serialization approaches due to different data sources (raw SQL vs deserialized dicts)">
- <e.g., "Dual rendering path in `renderer.py` — objects (live streaming) vs dicts (persisted metadata)">

**What's NOT changing:**
- <e.g., "No changes to the agent loop, tool execution, or auth flow">
- <This helps reviewers scope their review and not waste time checking unchanged areas>

**Schema/migration notes:**
- <If DB schema changed: document the migration path, backward compatibility, and what happens to existing data>

## Test Plan

- [x] Unit tests pass: `pytest tests/unit/ -v` (<N> passed)
- [x] Lint passes: `ruff check src/ tests/`
- [x] Format passes: `ruff format --check src/ tests/`
- [ ] <Specific test scenarios relevant to this PR, e.g., "Create conversation with RAG sources, resume it, verify sources display in both CLI and web UI">
- [ ] <Manual verification steps if applicable>

**Test coverage summary:**
- <N> new test files, <M> new tests total
- Key scenarios covered: <list the important test categories>
- Known gaps: <any areas not covered and why, e.g., "No Playwright E2E test for web UI rendering — would require mock RAG pipeline">

## Security Considerations

<Only include if the PR touches auth, sessions, input handling, DB queries, or tool execution. Otherwise omit this section entirely.>

<If included, be specific: "All new SQL uses parameterized queries. JSON deserialization uses `json.loads` with try/except fallback. No user input reaches the metadata column directly — only internal `rag_sources` dicts from the RAG pipeline.">

## Vision Alignment

<Include a brief note on which core principles this PR supports. If the PR adds new dependencies, config options, or infrastructure requirements, note them here with justification. Omit this section for trivial changes (typos, small fixes).>

---
Generated with [Claude Code](https://claude.ai/code)
```

### Step 10: Push and Create PR

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

### Step 10b: Transition Issue Labels

After the PR is created, transition the primary issue's label from `in-progress` to `ready-for-review`:

1. Extract the primary issue number from the branch name (`issue-<N>-...`)
2. Ensure the `ready-for-review` label exists:
   ```bash
   gh label create "ready-for-review" --color "0075CA" --description "PR submitted" --force
   ```
3. Transition the label:
   ```bash
   gh issue edit <N> --remove-label "in-progress" --add-label "ready-for-review"
   ```

### Step 11: Post-creation Report

```bash
gh pr view --json number,url,title
```

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🚀 PR Created
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  📂 Worktree: <worktree path> (or "main checkout" if not in a worktree)
  🔗 PR:       #<N> — <title>
  🌐 URL:      <url>
  🔀 Base:     main ← <branch>
  📎 Issues:   Closes #<N> — <issue title> (https://github.com/troylar/anteroom/issues/<N>)
               Addresses #<N> — <issue title> (https://github.com/troylar/anteroom/issues/<N>)
  📌 Status:   <ready | draft>
  🧪 Checks:   ✅ lint, format, tests, types
  📦 Deps:     ✅ / ❌ N vulns / ⚠️ N outdated, N new to review
  🔒 SAST:     ✅ / ❌ N Semgrep findings / ⏭️ not installed
  🔒 Security: ✅ / ⚠️ N issues
  📖 Docs:     ✅ up to date / ✅ N fixes committed / ⚠️ N need manual review
  🎯 Vision:   ✅ supports <principles>

────────────────────────────────────────────
  🔍 Running code review automatically...
  ⏳ CI checks will be verified after code review + bug hunter
────────────────────────────────────────────
```

### Step 12: Automatic Code Review

Immediately after PR creation, run the full `/code-review` workflow on the new PR. Display the local review report in chat and post the condensed comment to the PR.

### Step 13: Fix Loop (if issues found)

If the code review finds issues (score 80+):

1. Display all issues with full context
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
   - Re-run the code review
   - If new issues are found, repeat (max 2 fix rounds to avoid infinite loops)
   - Post an updated review comment to the PR

4. **If "Skip":**
   - Proceed without fixing. The review comment is already posted.

### Step 14: Bug Hunter (Opus, fix-all)

After all code review fixes are committed, run a deep correctness scan on the **final** committed code. This is the last gate — it catches logic bugs, race conditions, and edge cases that pattern-based checks miss.

Launch a single **Opus** agent with full read access to the codebase:

**Prompt the agent with:**

> You are a senior code reviewer doing a final deep-dive bug hunt. Your job is to find and fix every issue — no triage, no deferral. If you spot it, you fix it.
>
> Read every changed source file in full (not just the diff — you need surrounding context to understand call chains). For each file, also read its callers and callees to understand how the code is used.
>
> Hunt for:
> - **Logic errors**: incorrect conditions, wrong variable references, inverted checks
> - **Race conditions**: shared mutable state accessed from async code, TOCTOU issues
> - **Unhandled edge cases**: empty collections, None values, missing dict keys, zero-length strings
> - **Off-by-one errors**: incorrect loop bounds, wrong slice indices, fencepost errors
> - **Incorrect assumptions**: `[-1]` on lists that may have multiple concurrent entries, assuming dict ordering, assuming single-threaded execution
> - **State management bugs**: stale references after mutation, forgotten cleanup, missing resets between iterations
> - **Resource leaks**: unclosed files/connections, missing `finally` blocks, async context managers not awaited
> - **Error handling gaps**: bare `except:`, swallowed exceptions that hide bugs, error paths that leave state inconsistent
> - **Incorrect error messages**: error text that doesn't match what actually went wrong
> - **Dead code or unreachable branches**: conditions that can never be true, imports that are never used (beyond what ruff catches)
> - **Type mismatches**: passing wrong types that duck-typing hides until runtime
> - **Concurrency issues**: missing locks, incorrect lock ordering, deadlock potential
>
> For every issue you find:
> 1. Fix the code
> 2. If the fix is non-trivial, add a regression test
> 3. Run `ruff check` and `ruff format` on changed files
> 4. Run `pytest tests/unit/ -x -q` to verify nothing breaks
>
> Report what you found and fixed. Be thorough — check every changed file.

**Get the changed files for the agent:**
```bash
git diff --name-only main..HEAD -- 'src/**/*.py'
```

**After the agent completes:**

1. If it made fixes, stage and commit:
   ```bash
   git add <fixed files>
   git commit -m "fix(<scope>): address bug hunter findings (#<issue>)"
   ```
2. Push: `git push`
3. Re-run the Bug Hunter agent on the newly committed code (max 2 total rounds to prevent infinite loops)
4. If round 2 finds more issues, fix and commit again. Do not run a third round.

**Report format in the final summary:**
```
🔍 Bug Hunter
  Round 1:  N issues found, N fixed
  Round 2:  N issues found, N fixed (or "clean — no issues")
  Files:    <list of files that were fixed>
```

If the Bug Hunter finds nothing in round 1, report:
```
🔍 Bug Hunter:   ✅ clean — no issues found
```

### Step 15: Wait for CI

After all local checks, code review, and bug hunter are complete, wait for GitHub Actions CI to finish before declaring the PR ready.

Poll every 15 seconds, up to 10 minutes:
```bash
gh pr checks <PR> --json name,state,conclusion
```

Parse the JSON output. Check until all checks have resolved (no `PENDING`, `QUEUED`, or `IN_PROGRESS` states remain).

**Evaluate results:**
- **All checks pass**: proceed to Final Summary with CI ✅
- **Some checks fail**: report which checks failed with links. The PR is NOT READY.
- **Timeout (10 minutes)**: report which checks are still pending. Show as ⚠️ with a note to check back.

If any required checks fail, display the failure details:
```bash
gh pr checks <PR> --json name,state,conclusion,detailsUrl --jq '.[] | select(.conclusion == "FAILURE") | "\(.name): \(.detailsUrl)"'
```

### Step 16: Final Summary

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✅ PR Ready for Review  /  ❌ PR NOT Ready
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  🔗 PR:          #<N> — <title>
  🌐 URL:         <url>
  🔍 Code Review: ✅ clean / ⚠️ N issues remaining
  🔍 Bug Hunter:  ✅ clean / 🔧 N issues fixed in M rounds
  🔄 Fix Rounds:  <0-2> code review + <0-2> bug hunter
  🧪 CI:          ✅ all checks passed / ❌ N checks failed / ⚠️ N checks still pending

<If CI failed:>
  Failed checks:
    - <check name>: <details URL>
    - <check name>: <details URL>

────────────────────────────────────────────
<If all passed:>
  👉 Next: /senior-review <N> for sign-off, then /deploy
<If CI failed:>
  👉 Next: fix CI failures, push, then re-check with /pr-check
<If CI timed out:>
  👉 Next: check CI status with: gh pr checks <PR>
────────────────────────────────────────────
```

## Guidelines

- **Worktree venv**: When running in a worktree, use `.venv/bin/python -m pytest` and `.venv/bin/ruff` (or activate the venv first). Editable installs are per-venv — using the system Python will import from the wrong worktree.
- Never create a PR without at least one issue reference
- Never create a PR with failing tests (unless --skip-checks)
- Keep the PR title concise — details go in the body
- Group changes logically in the description
- If the PR is large (>500 lines changed), suggest breaking it up
- Security section only when relevant — don't add boilerplate
- Documentation issues are auto-fixed and committed before PR creation when possible
- Documentation items that need manual review don't block PR creation, but are flagged in the PR body
