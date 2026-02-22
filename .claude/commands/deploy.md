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

### Step 1b: PR Queue Context

Show the user all other open PRs so they understand the deploy queue:

```bash
gh pr list --state open --json number,title,headRefName,mergeable --jq '.[] | "\(.number)\t\(.mergeable)\t\(.title)"'
```

Display as a compact table:
```
📋 Open PRs:
  #192 ✅ MERGEABLE  fix: handle API connection errors (#121)    ← deploying this one
  #190 ✅ MERGEABLE  feat: add planning mode (#165)
  #189 ⚠️ UNKNOWN    feat: webhook agent backend (#176)
```

This gives the user visibility into what else is in flight and what might be affected by this merge.

### Step 2: Quick Documentation Check

Run a lightweight staleness check. The full documentation audit happens during `/submit-pr` — this step only catches things that slipped through or changed since PR creation.

1. **New modules** — check for any new `.py` files under `src/anteroom/` not listed in the "Key Modules" section of CLAUDE.md. Flag if any are missing.
2. **Version** — note the pre-bump version in `pyproject.toml` for reference.

If updates are needed, commit them before merging:
```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for vX.Y.Z release (#<primary issue>)"
git push
```

### Step 3: Rebase, Validate Freshness, and Merge

#### 3a. Check if main has moved

Before rebasing, detect whether `main` has advanced since the PR branch was last based on it:

```bash
git fetch origin main
MERGE_BASE=$(git merge-base HEAD origin/main)
MAIN_HEAD=$(git rev-parse origin/main)
```

If `$MERGE_BASE` != `$MAIN_HEAD`, main has moved — the PR's CI results were validated against a stale base. Report this:
```
  ⚠️ main has moved: <N> new commit(s) since this branch was last rebased.
     Rebasing and waiting for CI to re-validate against current main.
```

If `$MERGE_BASE` == `$MAIN_HEAD`, main is unchanged — the existing CI results are still valid. Report this:
```
  ✅ main is unchanged since CI ran — results are fresh.
```

#### 3b. Rebase

Always rebase to ensure a clean merge, even if main hasn't moved (catches local drift):

```bash
git rebase origin/main
git push --force-with-lease
```

#### 3c. Wait for CI

If main had moved (Step 3a detected new commits), the rebase will trigger new CI runs. Wait for them:

Poll every 15 seconds, up to 10 minutes:
```bash
gh pr checks <PR> --json name,state
```

Check until all checks have resolved (no `PENDING`, `QUEUED`, or `IN_PROGRESS` states remain).

If main had NOT moved, CI from the prior push is still valid — check that existing results are all passing rather than waiting for new runs.

#### 3d. Evaluate check results

- **All checks pass**: proceed to merge
- **Only non-required checks fail** (e.g., informational SAST scans): proceed with `--admin` — log which checks were bypassed and why
- **Required checks fail** (tests, lint): abort and show the failure URL. Do not proceed.

#### 3e. Merge (worktree-aware)

When running from a worktree, `gh pr merge --delete-branch` fails because git cannot delete a branch checked out in a worktree or switch to `main` when it's checked out elsewhere. Handle this:

1. Merge the PR on GitHub (without local branch deletion):
   ```bash
   gh pr merge <PR> --squash
   ```
   Add `--admin` if bypassing non-required checks (see 3d).

2. The remote branch is deleted by GitHub automatically.

3. Clean up the local worktree and branch:
   ```bash
   # If running from a worktree, remove it first
   WORKTREE_PATH=$(git rev-parse --show-toplevel)
   MAIN_WORKTREE=$(git worktree list --porcelain | grep -A0 'worktree ' | head -1 | sed 's/worktree //')
   if [ "$WORKTREE_PATH" != "$MAIN_WORKTREE" ]; then
       BRANCH=$(git branch --show-current)
       cd "$MAIN_WORKTREE"
       git worktree remove "$WORKTREE_PATH"
       git branch -d "$BRANCH" 2>/dev/null || true
   fi
   ```

4. Pull the merged changes into main:
   ```bash
   cd <main worktree path>
   git checkout main && git pull
   ```
   If main has unstaged changes, stash before pulling: `git stash && git pull && git stash pop`

### Step 3f: Post-Merge Queue Check

After merging, check whether other open PRs are affected:

```bash
gh pr list --state open --json number,title,headRefName,mergeable
```

For each remaining open PR, report its mergeable status:
```
📋 Remaining PRs after merge:
  #190 ✅ MERGEABLE  feat: add planning mode (#165)
  #189 ❌ CONFLICTING feat: webhook agent backend (#176) — needs rebase
```

If any PRs are now `CONFLICTING`:
```
  ⚠️ <N> open PR(s) now have merge conflicts after this merge.
     They will need rebasing before they can be deployed.
```

If all PRs are still `MERGEABLE`:
```
  ✅ All remaining PRs are still mergeable.
```

Note: GitHub's mergeable status may take a few seconds to update after a merge. If any PR shows `UNKNOWN`, wait 10 seconds and re-check once.

### Step 4: Determine Version Bump

Read `pyproject.toml` to get current version.

If the user passed a bump level (patch/minor/major), use that.

Otherwise, determine from the merged PR:
- Look at the PR title and commit messages on main since the last tag
- `feat:` or new files added -> **minor**
- `fix:`, `docs:`, `chore:`, `refactor:`, `test:` -> **patch**
- `BREAKING CHANGE` or `!:` in any commit -> **major**

Bump the version in `pyproject.toml` using semantic versioning.

### Step 5: Create Version Commit and Tag

```bash
git add pyproject.toml
git commit -m "chore: bump version to X.Y.Z"
git tag vX.Y.Z
git push origin main --tags --no-verify
```

Note: `--no-verify` bypasses the pre-push hook that blocks direct pushes to main. This is intentional — the deploy skill is the authorized context for pushing version bumps directly to main.

### Step 6: Build and Publish

1. Clean previous builds:
   ```bash
   rm -rf dist/ build/
   rm -rf src/*.egg-info 2>/dev/null || true
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

### Step 7: Create GitHub Release

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

### Step 7b: Clean Up Issue Labels

For each issue closed by the merged PR:

1. Get closing issues:
   ```bash
   gh pr view <PR_NUMBER> --json closingIssuesReferences --jq '.closingIssuesReferences[].number'
   ```

2. For each closing issue, remove workflow labels:
   ```bash
   gh issue edit <N> --remove-label "in-progress" --remove-label "ready-for-review"
   ```

3. If any closing issue is still OPEN (not auto-closed by the merge), close it:
   ```bash
   gh issue close <N> --comment "Closed via deploy of vX.Y.Z"
   ```

### Step 7c: Suggest Cleanup

Check for stale local branches:
```bash
git branch --list "issue-*"
```

For each, check if the corresponding issue is CLOSED. Count the stale ones.

If any stale branches exist, add to the deploy report:
```
  💡 Tip: Run /cleanup to remove N stale branches
```

### Step 8: Verify

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
  🔄 Freshness: ✅ CI ran against current main / ⚠️ rebased and re-validated
  📖 Docs:     ✅ up to date / ⚠️ N updates committed
  📦 PyPI:     https://pypi.org/project/anteroom/X.Y.Z/
  🏷️ Tag:      vX.Y.Z
  🏷️ Release:  https://github.com/troylar/anteroom/releases/tag/vX.Y.Z
  📊 Version:  X.Y.Z-1 → X.Y.Z (<type> bump)
  📋 Queue:    N open PRs remaining (N mergeable, N conflicting)

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
