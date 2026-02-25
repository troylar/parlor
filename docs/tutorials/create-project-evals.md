# Create Project-Local Evals

Test AI behavior in your project's context with reusable eval scripts and demo recordings.

## Why Project-Local Evals?

Anteroom ships its own eval suite for testing the product itself. But when you're using Anteroom in a project, you want to test behavior specific to *your* codebase:

- Does the AI use `read_file` before suggesting changes to your config?
- Does it pick the right tools when asked about your API endpoints?
- Can you replay a successful conversation as a deterministic demo?

Project-local evals live in your project directory (not in Anteroom's repo) and test behavior in the context of your project's ANTEROOM.md instructions.

## Quick Start: promptfoo with Exec Provider

The fastest way to create a repeatable eval. No proxy setup or auth tokens needed — just `aroom exec`.

### 1. Create an eval config

```yaml title="evals/tool-selection.yaml"
description: "Verify AI uses the right tools for codebase exploration"

providers:
  - id: "exec:aroom exec --approval-mode auto --no-conversation --json --quiet --temperature 0 --seed 42 \"{{prompt}}\""
    label: anteroom-agent

prompts:
  - "{{task}}"

defaultTest:
  options:
    provider: openai:chat:gpt-4o
  assert:
    - type: latency
      threshold: 120000

tests:
  - vars:
      task: "List all Python files in the src directory"
    assert:
      - type: javascript
        value: |
          const parsed = JSON.parse(output);
          const tools = (parsed.tool_calls || []).map(t => t.name || t.tool_name);
          return tools.includes('glob_files');
      - type: javascript
        value: |
          const parsed = JSON.parse(output);
          return parsed.exit_code === 0;

  - vars:
      task: "Find all TODO comments in the codebase"
    assert:
      - type: javascript
        value: |
          const parsed = JSON.parse(output);
          const tools = (parsed.tool_calls || []).map(t => t.name || t.tool_name);
          return tools.includes('grep');

evaluateOptions:
  maxConcurrency: 1
  cache: true
  timeoutMs: 120000
```

### 2. Run the eval

```bash
cd your-project/
npx promptfoo eval --config evals/tool-selection.yaml
```

### 3. View results

```bash
npx promptfoo view
```

!!! tip
    Run evals from your project directory so `aroom exec` picks up your project's ANTEROOM.md instructions automatically.

## Exec JSON Output Format

`aroom exec --json` returns structured output you can assert against:

```json
{
  "output": "Here are the Python files...",
  "tool_calls": [
    {
      "id": "call_abc123",
      "tool_name": "glob_files",
      "status": "success",
      "output": null
    }
  ],
  "model": "gpt-4o",
  "exit_code": 0
}
```

| Field | Description |
|-------|-------------|
| `output` | The assistant's final response text |
| `tool_calls` | Array of tools the agent invoked (name and status) |
| `model` | The model used for generation |
| `exit_code` | `0` success, `1` error, `124` timeout, `130` cancelled |

!!! note
    Tool `arguments` are redacted by default for security. Pass `--verbose` to include them.

## Writing Assertions

### Check that a specific tool was used

```javascript
const parsed = JSON.parse(output);
const tools = (parsed.tool_calls || []).map(t => t.name || t.tool_name);
return tools.includes('read_file');
```

### Check that a tool was NOT used

```javascript
const parsed = JSON.parse(output);
const tools = (parsed.tool_calls || []).map(t => t.name || t.tool_name);
return !tools.includes('bash');
```

### Check tool ordering (read before write)

```javascript
const parsed = JSON.parse(output);
const tools = (parsed.tool_calls || []).map(t => t.name || t.tool_name);
const readIdx = tools.findIndex(t => ['read_file', 'glob_files', 'grep'].includes(t));
const writeIdx = tools.findIndex(t => ['write_file', 'edit_file'].includes(t));
return readIdx >= 0 && (writeIdx < 0 || readIdx < writeIdx);
```

### Check multiple tools were used

```javascript
const parsed = JSON.parse(output);
const tools = new Set((parsed.tool_calls || []).map(t => t.name || t.tool_name));
return tools.size >= 2;
```

### Check output content

```yaml
# Exact substring
- type: contains
  value: "expected text"

# Absence check
- type: not-contains
  value: "Internal Server Error"

# LLM judge (semantic evaluation)
- type: llm-rubric
  value: "The response should explain the function's purpose concisely"
```

## Turn a Conversation into an Eval

Had a successful conversation? Turn it into a repeatable test.

### 1. Find the conversation

```bash
aroom db conversations --limit 10
```

Look for the conversation slug or ID you want to capture.

### 2. Review the messages

```bash
aroom db messages <conversation-id-or-slug>
```

Note the user prompts and which tools the agent used.

### 3. Extract into an eval config

For each user prompt that produced good behavior, create a test case:

```yaml title="evals/captured-workflow.yaml"
description: "Captured from conversation: auth-refactor"

providers:
  - id: "exec:aroom exec --approval-mode auto --no-conversation --json --quiet --temperature 0 --seed 42 \"{{prompt}}\""
    label: anteroom-agent

prompts:
  - "{{task}}"

tests:
  # From first user message — agent should explore before changing
  - vars:
      task: "How does the authentication middleware work in this project?"
    assert:
      - type: javascript
        value: |
          const parsed = JSON.parse(output);
          const tools = (parsed.tool_calls || []).map(t => t.name || t.tool_name);
          return tools.includes('read_file') || tools.includes('grep');
      - type: llm-rubric
        value: "Explains the auth flow including middleware, token validation, and session handling"

  # From second user message — agent should read before modifying
  - vars:
      task: "Add rate limiting to the login endpoint"
    assert:
      - type: javascript
        value: |
          const parsed = JSON.parse(output);
          const tools = (parsed.tool_calls || []).map(t => t.name || t.tool_name);
          const readIdx = tools.findIndex(t => ['read_file', 'grep'].includes(t));
          const writeIdx = tools.findIndex(t => ['write_file', 'edit_file'].includes(t));
          return readIdx >= 0 && (writeIdx < 0 || readIdx < writeIdx);

evaluateOptions:
  maxConcurrency: 1
  cache: true
  timeoutMs: 120000
```

!!! tip
    Use the `/create-eval` skill in the Anteroom CLI to automate this. Just say: "Turn my last conversation into an eval."

### Using the `/create-eval` skill

The built-in `/create-eval` skill knows all the patterns above. Use it from the Anteroom CLI:

```
you> /create-eval test that the AI reads files before suggesting edits
you> /create-eval turn the auth-refactor conversation into an eval
you> /create-eval create a VHS demo from my last conversation
```

The skill generates the config file and writes it to your project's `evals/` or `demos/` directory.

## Turn a Conversation into a VHS Demo

[VHS](https://github.com/charmbracelet/vhs) creates reproducible terminal demo GIFs from `.tape` scripts.

### 1. Install VHS

```bash
# macOS
brew install charmbracelet/tap/vhs ffmpeg ttyd

# Go
go install github.com/charmbracelet/vhs@latest
```

### 2. Create a tape file from conversation prompts

Extract the user prompts from a conversation and wrap each one in an `aroom exec` call:

```bash title="demos/auth-workflow.tape"
Output demos/auth-workflow.gif

Require aroom

Set Shell "zsh"
Set FontSize 16
Set Width 1200
Set Height 700
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms

# Scene 1: Explore the auth system
Type "aroom exec --temperature 0 --seed 42 --approval-mode auto 'How does authentication work in this project?'"
Enter
Sleep 12s

# Scene 2: Add rate limiting
Type "aroom exec --temperature 0 --seed 42 --approval-mode auto 'Add rate limiting to the login endpoint'"
Enter
Sleep 12s

Sleep 2s
```

### 3. Record the demo

```bash
vhs demos/auth-workflow.tape
```

### Timing guidelines

| Prompt type | Sleep duration |
|-------------|---------------|
| Simple question (no tools) | `Sleep 6s` |
| Single-tool prompt | `Sleep 10s` |
| Multi-tool or complex prompt | `Sleep 12s` |
| End-of-tape trailing pause | `Sleep 2s` |

!!! warning
    Always use `--temperature 0 --seed 42 --approval-mode auto` for deterministic output. Without these flags, re-recording will produce different results.

## Shell Test Scripts

For simple checks without promptfoo, use a shell script:

```bash title="tests/test-behavior.sh"
#!/bin/bash
set -euo pipefail

PASS=0; FAIL=0

check() {
  local name="$1" result="$2"
  if [ "$result" = "true" ]; then
    echo "PASS: $name"; ((PASS++))
  else
    echo "FAIL: $name"; ((FAIL++))
  fi
}

# Test 1: AI should use glob_files for file listing
result=$(aroom exec --json --quiet --temperature 0 --seed 42 \
  --approval-mode auto --no-conversation \
  "List all Python files in the src directory")

used_glob=$(echo "$result" | python3 -c "
import sys, json
data = json.load(sys.stdin)
tools = [t.get('tool_name', '') for t in data.get('tool_calls', [])]
print('true' if 'glob_files' in tools else 'false')
")
check "uses glob_files for file listing" "$used_glob"

# Test 2: AI should read before suggesting changes
result=$(aroom exec --json --quiet --temperature 0 --seed 42 \
  --approval-mode auto --no-conversation \
  "What does the main config file do?")

used_read=$(echo "$result" | python3 -c "
import sys, json
data = json.load(sys.stdin)
tools = [t.get('tool_name', '') for t in data.get('tool_calls', [])]
print('true' if 'read_file' in tools else 'false')
")
check "reads config file when asked about it" "$used_read"

echo "---"
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
```

Run with:

```bash
bash tests/test-behavior.sh
```

## Directory Conventions

Keep project-local evals out of version control if they're personal, or check them in if they're shared:

```
your-project/
  evals/               # promptfoo configs
    tool-selection.yaml
    captured-workflow.yaml
  demos/               # VHS tape files
    auth-workflow.tape
  tests/               # shell test scripts
    test-behavior.sh
```

To gitignore personal evals:

```bash title=".gitignore"
# Personal eval results (configs can be checked in)
evals/output/
evals/.promptfoo/
```

## Deterministic Output

For reproducible evals, always configure deterministic settings:

| Flag | Purpose |
|------|---------|
| `--temperature 0` | Greedy decoding (most deterministic) |
| `--seed 42` | Provider-side determinism hint |
| `--approval-mode auto` | Bypass safety prompts for unattended runs |
| `--no-conversation` | Isolate each test case (no state bleed) |
| `--quiet` | Suppress stderr status output |

!!! note
    `temperature: 0` provides greedy decoding but exact reproducibility depends on the upstream provider. `seed` improves consistency but is not guaranteed across all providers. Use structural assertions (tool was used, pattern present) rather than exact string matches.

## Further Reading

- [Developer Testing Guide](../advanced/testing.md) — Anteroom's own multi-layer testing strategy
- [Build Custom Skills](build-custom-skills.md) — create project-specific skills
- [promptfoo documentation](https://github.com/promptfoo/promptfoo) — full eval framework reference
- [VHS documentation](https://github.com/charmbracelet/vhs) — terminal recording tool
