# Developer Workflow

Anteroom uses Claude Code skills (`.claude/commands/`) to automate the development lifecycle. These skills enforce commit conventions, run validation suites, and handle deployments.

## Available Skills

| Skill | Purpose | Usage |
|-------|---------|-------|
| `/new-issue` | Create a structured GitHub issue from a description | `/new-issue add dark mode support` |
| `/start-work 83` | Set up a branch, worktree, and implementation plan | `/start-work 83` |
| `/commit` | Validate and create a conventional commit | `/commit` or `/commit fix(routers): handle empty query (#91)` |
| `/submit-pr` | Run full validation suite and create a PR | `/submit-pr` |
| `/code-review` | Review a PR for bugs, security, and compliance | `/code-review 85` |
| `/pr-check` | Validate an existing PR without modifying it | `/pr-check --pr 86` |
| `/deploy` | Merge, bump version, publish to PyPI | `/deploy` or `/deploy patch` |
| `/dev-help` | Show the full developer guide | `/dev-help` |

## Typical Workflow

```
/new-issue <description>     # 1. Create an issue
/start-work <N>              # 2. Branch + plan
... write code ...           # 3. Implement
/commit                      # 4. Commit (validates format, runs tests)
/submit-pr                   # 5. Validate + create PR + auto-review
/deploy                      # 6. Merge + publish (after PR approval)
```

## Issue and Commit Conventions

Every piece of work must be tied to a GitHub issue. This is enforced by the skills and by `.claude/rules/no-code-without-issue.md`.

### Branch Naming

```
issue-<N>-<short-description>
```

Examples: `issue-83-knowledge-notebooks`, `issue-110-mcp-approval-hang`

### Commit Format

```
type(scope): description (#issue)
```

- **type**: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`
- **scope**: module name (`cli`, `tools`, `routers`, `services`, `db`, `config`, `app`, `static`, `commands`)
- **#issue**: required GitHub issue reference

Examples:

- `feat(tools): add canvas patch tool (#83)`
- `fix(routers): handle missing conversation in chat endpoint (#91)`
- `test(services): add agent loop timeout tests (#88)`

## The `/commit` Skill

`/commit` does more than format a message --- it runs a full pre-commit suite:

1. **Stages files** --- never uses `git add -A`; adds specific files, skipping `.env` and credentials
2. **Generates or validates** the commit message from the diff
3. **Runs lint and format** on staged files only (scoped, not the full repo)
4. **Runs unit tests** (`pytest tests/unit/ -x -q`)
5. **Auto-fixes** lint and format issues on staged files
6. **Checks complexity** --- warns about new dependencies, config options, or infrastructure
7. **Commits** with `Co-Authored-By` trailer

If tests fail, the commit is aborted. If lint fails, it auto-fixes and re-stages.

## The `/submit-pr` Skill

`/submit-pr` is the primary quality gate. It runs 14 steps:

1. **Pre-flight** --- branch status, merge conflicts, uncommitted changes
2. **Issue references** --- every commit must reference a GitHub issue
3. **Code quality** --- lint, format, tests, type check (blocking)
4. **Test coverage** --- checks that new modules have test files
5. **Deep analysis** --- 5 parallel agents check:
    - Test thoroughness (GOOD/WEAK/POOR)
    - CLAUDE.md compliance
    - Documentation freshness (authoritative --- applies fixes)
    - Vision alignment
    - Security scan (OWASP ASVS Level 2)
6. **Commit doc fixes** --- if Agent C found stale docs, they're committed automatically
7. **Issue check** --- verifies all issue references are valid
8. **Validation report** --- comprehensive pass/fail/warn summary
9. **PR description** --- auto-generated from commits and diff
10. **Push and create PR**
11. **Post-creation report**
12. **Automatic code review** --- runs `/code-review` on the new PR
13. **Fix loop** --- if code review finds issues, offers to fix and re-review (max 2 rounds)
14. **Final summary**

### Blocking vs Warning

| Blocking (NOT READY) | Warning (READY) |
|---|---|
| Tests, lint, or format fail | Uncommitted changes |
| Security issues found | WEAK test thoroughness |
| Missing test files for new modules | Documentation needs manual review |
| POOR test thoroughness | Vision alignment concerns |

## The `/deploy` Skill

`/deploy` handles the full release pipeline:

1. **Pre-flight** --- confirms feature branch, finds PR, verifies issue references
2. **Quick doc check** --- lightweight staleness check (test count, new modules)
3. **Merge PR** --- rebases on main, waits for CI, merges with `--admin` if only non-required checks fail
4. **Determine version bump** --- from commit types (`feat:` = minor, `fix:` = patch, `BREAKING CHANGE` = major)
5. **Tag and push** --- commits version bump, creates tag, pushes with `--no-verify`
6. **Build and publish** --- `python -m build` + `twine upload`
7. **GitHub release** --- auto-generated user-friendly release notes
8. **Verify** --- confirms package is available on PyPI

### Merge Strategy

The deploy skill handles branch protection automatically:

- Rebases the feature branch on `origin/main` before merge
- Waits for all CI checks to resolve
- If only non-required checks fail (e.g., informational SAST scans), merges with `--admin`
- If required checks fail (tests, lint), aborts

## The `/code-review` Skill

`/code-review` runs 7 parallel agents against the PR diff:

1. CLAUDE.md compliance
2. Bug scan (return values, off-by-one, null handling, concurrency, etc.)
3. Security audit (OWASP ASVS checklist)
4. Historical context (reverted fixes, recurring patterns)
5. Code comments and intent (invariant violations, warning heeds)
6. Vision alignment
7. Documentation freshness

Each finding is independently verified with a confidence score (0--100). Only findings scoring 80+ are included in the review. The review is posted as a GitHub comment.

## Documentation Surfaces

When modifying code, keep these documentation surfaces up to date. The `/submit-pr` skill checks all of them automatically.

| Surface | What to check |
|---|---|
| `CLAUDE.md` | Key Modules, Architecture diagram, Configuration, Database, Security Model, test count |
| `README.md` | CLI commands, feature descriptions, installation |
| `VISION.md` | Current Direction, scope boundaries |
| `docs/cli/` | CLI changes |
| `docs/api/` and `docs/web-ui/` | Router changes |
| `docs/cli/tools.md` | Tool changes |
| `docs/configuration/` | Config changes |
| `docs/security/` | Security changes |
| `docs/advanced/architecture.md` | Middleware, new services |

## Testing Best Practices

- **New modules** must have a test file at `tests/unit/test_<name>.py`
- **Bug fixes** must include a regression test
- **Modified functions** need updated tests if behavior changed
- **Unit tests** mock all external dependencies (DB, API, file I/O)
- **Async tests** use `@pytest.mark.asyncio` with `asyncio_mode = "auto"`
- Run tests before committing: `pytest tests/unit/ -v --tb=short`

## Security Patterns

All code must follow OWASP ASVS Level 2. Key rules:

- Parameterized SQL queries only (no string concatenation)
- No `eval()`, `exec()`, `pickle.loads()` with user input
- No `innerHTML` with unsanitized data
- No hardcoded secrets
- Cookies: `HttpOnly`, `Secure`, `SameSite=Strict`
- All endpoints behind auth middleware
- Content-Type validation on JSON endpoints
- Rate limiting on public endpoints

See `docs/security/` for the full security model.

## Worktrees

`/start-work` creates git worktrees by default, keeping your main branch clean:

```
anteroom/               # main repo (main branch)
anteroom-issue-83/      # worktree for issue 83
anteroom-issue-110/     # worktree for issue 110
```

Each worktree is an independent working directory with its own branch. Use `--no-worktree` if you prefer traditional branches.

## Vision Alignment

Before creating issues or starting work, the skills check against `VISION.md` guardrails:

- **Not a walled garden** --- extensibility through MCP and standards
- **Not a ChatGPT clone** --- features must serve agentic/tool use cases
- **Not a configuration burden** --- every option needs a sensible default
- **Not enterprise software** --- no license keys, SSO, or admin panels
- **Not a deployment project** --- setup must take under 2 minutes
- **Not a model host** --- Anteroom talks to models, doesn't run them

Features that conflict with these guardrails are flagged before work begins.
