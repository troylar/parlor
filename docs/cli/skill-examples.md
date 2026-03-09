# Skill Examples

Complete, ready-to-use skill files demonstrating common patterns. Copy any of these into `.anteroom/skills/` or `~/.anteroom/skills/` and invoke with `/skill-name`.

## PR Summary

A simple skill that runs `gh` commands to summarize a pull request. No sub-agents — just sequential bash calls.

**Demonstrates:** `{args}` placeholder, bash tool, sequential workflow.

```yaml title=".anteroom/skills/pr-summary.yaml"
name: pr-summary
description: Summarize a pull request with file changes and test impact
prompt: |
  Summarize pull request #{args}.

  1. Fetch PR metadata with `gh pr view {args} --json title,body,author,labels,files`
  2. Get the diff stats with `gh pr diff {args} --stat`
  3. Check CI status with `gh pr checks {args} --json name,state,conclusion`
  4. Write a concise summary:
     - Title and author
     - What changed (group by area: src, tests, docs, config)
     - CI status
     - Any concerns or open questions from the PR body
```

**Usage:** `/pr-summary 85`

!!! tip
    The `{args}` placeholder is replaced with whatever follows the skill name. If the user types `/pr-summary 85`, every `{args}` becomes `85`.

!!! warning "Keep `{args}` outside fenced code blocks"
    Anteroom's `{args}` expansion **skips fenced code blocks** (`` ``` ... ``` ``). If you put `{args}` inside a bash fence, it stays as literal text. Use inline code (`` `command {args}` ``) or plain prose instead. The AI will still recognize and execute the commands.

---

## Code Review with Parallel Sub-Agents

A comprehensive review skill that launches concurrent sub-agents for bug scanning, security audit, and test verification. Each sub-agent works independently and reports back.

**Demonstrates:** `run_agent` for parallel execution, self-contained sub-agent prompts, `{args}` for PR number.

```yaml title=".anteroom/skills/review.yaml"
name: review
description: Review a PR with parallel bug, security, and test checks
prompt: |
  Review pull request #{args} for bugs, security issues, and test coverage.

  ## Step 1: Gather context

  Run `gh pr view {args} --json title,body,author,files` and
  `gh pr diff {args} --stat` to understand the PR.

  ## Step 2: Parallel review

  Use the `run_agent` tool to launch these three reviews simultaneously.
  Each prompt must be fully self-contained — sub-agents cannot see this
  conversation.

  **Sub-agent 1 — Bug scan:**
  Read the diff for PR #{args} using `gh pr diff {args}`. Check each
  changed line for: unchecked None returns, off-by-one errors, type
  mismatches, missing error handling, resource leaks, race conditions.
  Report only real issues with file:line references.

  **Sub-agent 2 — Security audit:**
  Read the diff for PR #{args} using `gh pr diff {args}`. Check for:
  SQL string concatenation, subprocess with shell=True and user input,
  path traversal, innerHTML with unsanitized input, hardcoded secrets,
  missing input validation. Report only confirmed vulnerabilities with
  file:line and attack vector.

  **Sub-agent 3 — Test verification:**
  Read the diff for PR #{args} using `gh pr diff {args} --name-only`.
  For each changed source file under src/, check if a corresponding
  test file exists under tests/. Run `gh pr checks {args}` to get CI
  status. Report: which files lack tests, CI pass/fail, any test
  failures.

  ## Step 3: Synthesize

  Collect results from all three sub-agents. Write a summary:
  - Bugs found (with severity)
  - Security issues (with attack vector)
  - Test coverage gaps
  - CI status
  - Overall verdict: APPROVE, REQUEST CHANGES, or NEEDS DISCUSSION
```

**Usage:** `/review 85`

!!! warning "Sub-agent prompts must be self-contained"
    Notice how each sub-agent prompt repeats the PR number and the `gh pr diff` command. Sub-agents can't see the parent conversation — they only see their own prompt.

---

## Codebase Exploration

Launches parallel sub-agents to explore a codebase from multiple angles: architecture, dependencies, and test coverage. Useful when onboarding to a new project.

**Demonstrates:** `run_agent` for parallel exploration, no `{args}` (uses working directory context).

```yaml title=".anteroom/skills/explore-codebase.yaml"
name: explore-codebase
description: Parallel codebase exploration — architecture, deps, and tests
prompt: |
  Explore this codebase to understand its structure and quality.

  Use the `run_agent` tool to launch these three explorations in parallel:

  **Sub-agent 1 — Architecture:**
  Explore the current directory structure. Run `find . -name '*.py'
  -not -path './.venv/*' | head -50` and `cat README.md` (if it exists).
  Identify: entry points, module organization, key abstractions, and
  the data flow between layers. Report a concise architecture summary.

  **Sub-agent 2 — Dependencies:**
  Read `pyproject.toml` or `requirements.txt` (whichever exists). List
  all direct dependencies with their purpose. Flag any known-vulnerable
  or deprecated packages. Check for optional dependency groups. Report
  the dependency summary.

  **Sub-agent 3 — Test coverage:**
  Run `find . -path '*/tests/*' -name '*.py' | wc -l` to count test
  files. Read 2-3 test files to understand testing patterns (fixtures,
  mocking, async). Run `python -m pytest --collect-only -q 2>/dev/null
  | tail -5` to count test cases. Report: test count, patterns used,
  and any obvious coverage gaps.

  After all sub-agents complete, synthesize a one-page project overview:
  - Architecture summary
  - Key dependencies and their roles
  - Test health: count, patterns, gaps
  - Suggested areas to investigate further
```

**Usage:** `/explore-codebase`

---

## Deploy Checklist

A sequential skill that validates readiness before deploying. No sub-agents — runs checks one at a time to ensure each passes before proceeding.

**Demonstrates:** Sequential bash workflow, conditional logic, no sub-agents.

```yaml title=".anteroom/skills/deploy-check.yaml"
name: deploy-check
description: Pre-deploy validation — tests, lint, type check, and build
prompt: |
  Run the pre-deploy checklist. Stop at the first failure.

  1. Check for uncommitted changes:
     ```bash
     git status --porcelain
     ```
     If output is non-empty, warn and ask whether to continue.

  2. Run the test suite:
     ```bash
     python -m pytest tests/unit/ -v --tb=short 2>&1 | tail -30
     ```
     If any tests fail, stop and report the failures.

  3. Run the linter:
     ```bash
     ruff check src/ tests/
     ```
     If any issues found, stop and report them.

  4. Run type checking:
     ```bash
     mypy src/ --ignore-missing-imports 2>&1 | tail -20
     ```
     If errors found, stop and report them.

  5. Build the package:
     ```bash
     python -m build 2>&1 | tail -10
     ```
     If the build fails, stop and report the error.

  6. If all checks pass, report:
     - Test count and pass rate
     - Lint status
     - Type check status
     - Build artifact location
     - Confirmation that the project is ready to deploy
```

**Usage:** `/deploy-check`

---

## Security Audit with Model Override

A security-focused skill that uses a sub-agent with an explicit model override for cost-efficient deep analysis.

**Demonstrates:** `run_agent` with `model` parameter, single sub-agent, `{args}` for target path.

```yaml title=".anteroom/skills/security-audit.yaml"
name: security-audit
description: OWASP security review of a file or directory
prompt: |
  Perform an OWASP-focused security audit of: {args}

  ## Step 1: Identify files

  Use `glob_files` to find all source files matching the target path.
  If {args} is a directory, include all `.py` and `.js` files recursively.

  ## Step 2: Deep analysis

  Use the `run_agent` tool for the detailed file-by-file review.
  Use a cost-efficient model for this intensive scan:

  run_agent(
    prompt="Review these files for OWASP Top 10 vulnerabilities:
      Target: {args}

      For each file, check:
      - A03 Injection: SQL concatenation, command injection, XSS
      - A01 Broken Access Control: missing auth checks, IDOR, path traversal
      - A02 Cryptographic Failures: weak algorithms, hardcoded secrets
      - A04 Insecure Design: missing input validation, unsafe defaults
      - A05 Security Misconfiguration: debug mode, permissive CORS
      - A07 Authentication Failures: credential exposure, session issues
      - A08 Data Integrity: unsafe deserialization, missing integrity checks
      - A09 Logging Failures: sensitive data in logs, missing audit events

      Read each file with read_file and check line by line.
      Report each finding with: file, line number, OWASP category,
      severity (Critical/High/Medium/Low), description, and fix.",
    model="gpt-4o-mini"
  )

  ## Step 3: Summary

  Compile the sub-agent's findings into a security report:
  - Critical and High findings (must fix)
  - Medium findings (should fix)
  - Low findings (consider fixing)
  - Overall security posture assessment
```

**Usage:** `/security-audit src/anteroom/routers/`

!!! note "Model override"
    The `model: "gpt-4o-mini"` parameter tells Anteroom to use a cheaper model for the sub-agent. This is useful for intensive scanning tasks where cost matters. The model ID must match your configured API — if you're using Azure OpenAI, use your deployment name instead. If omitted, the sub-agent inherits the parent's model.

---

## Writing Effective Skill Prompts

### Tips

- **Be explicit about tools.** Instead of "search the codebase", write "use `grep` to search" or "use `glob_files` to find".
- **Number your steps.** The AI follows numbered workflows more reliably than prose paragraphs.
- **Include example commands.** Bash commands in fenced code blocks are executed more reliably than described commands.
- **Use `{args}` for user input.** It's cleaner than asking the AI to parse the user's message. Keep `{args}` outside fenced code blocks — expansion skips fenced content.

### Sub-Agent Tips

- **Self-contained prompts.** Repeat context in every sub-agent prompt — they can't see each other or the parent.
- **Keep prompts focused.** One sub-agent per concern (bugs, security, tests) is better than one sub-agent doing everything.
- **Cap tool calls.** If a sub-agent might make many tool calls, add "limit yourself to 10 tool calls" to prevent runaway execution.
- **Check sub-agent output.** The parent AI receives sub-agent results as tool call responses and should synthesize them, not just pass them through.

## See Also

- [Skills](skills.md) — full skill authoring reference
- [Porting from Claude Code](porting-from-claude-code.md) — migration guide for Claude Code commands
- [Built-in Tools: run_agent](tools.md#run_agent) — sub-agent tool reference and limits
