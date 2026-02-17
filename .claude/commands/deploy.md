---
name: deploy
description: Merge PR, verify CI, bump version, and publish to PyPI
allowed-tools: Bash, Read, Edit, Grep, Glob, WebFetch
---

# /deploy Skill

Deploy the current branch to PyPI after merging, CI verification, and version bump.

## Usage

```
/deploy              # auto-detect PR and version bump
/deploy patch        # force patch bump
/deploy minor        # force minor bump
/deploy major        # force major bump
```

## Workflow

### Step 1: Pre-flight Checks

1. Confirm we're on a feature branch (not main)
2. Find the open PR for this branch: `gh pr view --json number,title,state,mergeable`
3. If no PR exists, abort with message
4. Show the PR title and number, confirm with user before proceeding
5. **Verify every commit references a GitHub issue.** Run:
   ```bash
   gh pr view --json commits --jq '.commits[].messageHeadline'
   ```
   Every commit message MUST contain a `(#NNN)` issue reference. If any commit is missing one, warn the user and ask them to fix it before proceeding.

### Step 2: Verify Documentation

Before merging, audit **all documentation surfaces** for accuracy. Extract the primary issue number from the branch name or PR body for any doc-fix commits.

#### 2a. CLAUDE.md

1. **New modules** — check for any new `.py` files under `src/anteroom/` not mentioned in the "Key Modules" section. Add them.
2. **Stale descriptions** — for modified modules, verify the CLAUDE.md description still matches the actual code behavior.
3. **New config fields** — check `config.py` dataclasses for fields not documented in the "Configuration" section. Add them.
4. **New agent events** — check `agent_loop.py` for any `AgentEvent(kind=...)` values not mentioned. Document them.
5. **Architecture changes** — if the PR added middleware, new routers, or changed the security model, update those sections.
6. **Database changes** — check `db.py` for new tables or columns not in the "Database" section. Add them.
7. **Architecture diagram** — if new services or routers were added, update the ASCII diagram.
8. **Version in pyproject.toml** — note the pre-bump version for reference.

#### 2b. README.md

1. **New CLI commands or flags** not mentioned in README? Add them.
2. **Feature descriptions** that no longer match current behavior? Update them.
3. **Installation or quickstart instructions** still accurate? Verify.

#### 2c. VISION.md

1. **Current Direction** — does the PR add capabilities that should be reflected? Update if so.
2. **Scope boundaries** — any changes that affect what's in/out of scope? Flag for review.

#### 2d. MkDocs docs/ pages

For each changed source file, check the corresponding docs page(s):
- `src/anteroom/cli/` changes → `docs/cli/`
- `src/anteroom/routers/` changes → `docs/api/` and `docs/web-ui/`
- `src/anteroom/tools/` changes → `docs/cli/tools.md`
- `src/anteroom/config.py` changes → `docs/configuration/`
- Security changes → `docs/security/`
- `app.py` middleware changes → `docs/security/` and `docs/advanced/architecture.md`

Flag pages that reference behavior the PR changed but weren't updated. Flag new features with no corresponding docs page.

#### 2e. Commit doc fixes

If any updates are needed, commit them before merging. Use the PR's primary issue number:
```bash
git add CLAUDE.md README.md VISION.md docs/
git commit -m "docs: update documentation for vX.Y.Z release (#<primary issue>)"
git push
```

### Step 3: Merge PR

1. Merge the PR to main: `gh pr merge --squash --delete-branch`
2. Switch to main: `git checkout main && git pull`

### Step 4: Wait for CI

1. Get the latest commit SHA on main: `git rev-parse HEAD`
2. Poll CI status every 15 seconds, up to 10 minutes:
   ```
   gh run list --branch main --limit 1 --json status,conclusion,name,headSha
   ```
3. If CI fails, abort and show the failure URL:
   ```
   gh run list --branch main --limit 1 --json url,conclusion
   ```
4. If CI passes, continue

### Step 5: Determine Version Bump

Read `pyproject.toml` to get current version.

If the user passed a bump level (patch/minor/major), use that.

Otherwise, determine from the merged PR:
- Look at the PR title and commit messages on main since the last tag
- `feat:` or new files added -> **minor**
- `fix:`, `docs:`, `chore:`, `refactor:`, `test:` -> **patch**
- `BREAKING CHANGE` or `!:` in any commit -> **major**

Bump the version in `pyproject.toml` using semantic versioning.

### Step 6: Create Version Commit and Tag

```bash
git add pyproject.toml
git commit -m "chore: bump version to X.Y.Z"
git tag vX.Y.Z
git push origin main --tags
```

### Step 7: Build and Publish

1. Clean previous builds:
   ```bash
   rm -rf dist/ build/ *.egg-info src/*.egg-info
   ```
2. Build:
   ```bash
   python -m build
   ```
3. Check the build:
   ```bash
   twine check dist/*
   ```
4. Publish to PyPI:
   ```bash
   twine upload dist/*
   ```
   This uses credentials from `~/.pypirc` or `TWINE_USERNAME`/`TWINE_PASSWORD` env vars.

### Step 8: Create GitHub Release

Generate release notes from the PR and all referenced issues. The release notes should be **user-friendly** — written for someone who uses Anteroom, not just developers.

#### Gathering information

1. Get all issues closed by this PR and any issues referenced in commits:
   ```bash
   gh pr view <PR_NUMBER> --json closingIssuesReferences --jq '.closingIssuesReferences[].number'
   git log <PREVIOUS_TAG>..HEAD --oneline
   ```
2. For each referenced issue, get its title and labels:
   ```bash
   gh issue view <ISSUE_NUMBER> --json title,labels
   ```
3. Categorize issues by their labels or commit type prefix:
   - `feat:` or label `enhancement` → **New Features**
   - `fix:` or label `bug` → **Bug Fixes**
   - Everything else (docs, chore, refactor, test) → only include if user-visible

#### Writing the release notes

Use this structure:

```markdown
## New Features

### Feature Name
User-friendly description of what this does and why they'd care.
- Key detail with issue reference (#NN)
- Another detail (#NN)

### Another Feature
...

## Bug Fixes

- Description of what was broken and that it's fixed now (#NN)
- Another fix (#NN)

## Other Improvements

- User-visible improvements that aren't features or fixes (#NN)

## For Developers

- Technical changes: new modules, API changes, schema changes
- New environment variables or config fields
- Test count changes

## Upgrading

\`\`\`bash
pip install --upgrade anteroom
\`\`\`

Note any manual steps needed (usually none — migrations are automatic).
```

**Rules:**
- EVERY bullet point that corresponds to a GitHub issue MUST include the issue reference as `(#NN)`
- Omit empty sections (e.g., if no bug fixes, skip that section)
- Lead with what users care about, put developer details at the bottom
- Write feature descriptions in plain language, not commit-message-speak

#### Creating the release

```bash
gh release create vX.Y.Z --title "vX.Y.Z" --notes "<generated notes>"
```

### Step 9: Verify

1. Wait 30 seconds for PyPI to index
2. Check the package is available:
   ```bash
   pip install anteroom==X.Y.Z --dry-run 2>&1 | head -5
   ```
3. Report success with the new version number and PyPI URL

## Error Handling

- If merge fails: show error, do not proceed
- If CI fails: show failure URL, do not proceed
- If build fails: show error, do not proceed
- If upload fails: the tag and version commit are already pushed; show error and suggest manual `twine upload dist/*`
- Never force-push or amend commits on main
- If commits are missing issue references: warn the user, do not proceed until fixed

## Output

On success:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  📦 Deployed anteroom vX.Y.Z
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  🔗 PR:       #NN (merged)
  🧪 CI:       ✅ passed
  📖 Docs:     ✅ up to date / ⚠️ N updates committed
  📦 PyPI:     https://pypi.org/project/anteroom/X.Y.Z/
  🏷️ Tag:      vX.Y.Z
  🏷️ Release:  https://github.com/troylar/anteroom/releases/tag/vX.Y.Z
  📊 Version:  X.Y.Z-1 → X.Y.Z (<type> bump)

────────────────────────────────────────────
  ✅ Deploy complete
────────────────────────────────────────────
```

On failure:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ❌ Deploy Failed
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Step:    <which step failed>
  Error:   <error message>

────────────────────────────────────────────
  👉 Next: <what to do to fix it>
────────────────────────────────────────────
```
