# Workflow Definitions

Workflow definitions are YAML files that declare inputs, policies, steps, and optional notifications for one durable automation flow.

## Where Definitions Come From

You can run a workflow by:

- passing a filesystem path to a YAML file
- using a definition from `examples/workflows/`
- using a packaged definition shipped with Anteroom

Today, the reference example lives in `examples/workflows/issue_delivery.yaml`.

## Minimal Definition

```yaml
kind: workflow
id: hello_workflow
version: 0.1.0

inputs: {}

steps:
  - id: hello
    type: runner
    runner: shell
    command: "echo hello"
    timeout: 10
```

The engine requires:

- `kind: workflow`
- `id`
- `version`
- at least one step

## Top-Level Fields

| Field | Required | Description |
|---|---|---|
| `kind` | Yes | Must be `workflow` |
| `id` | Yes | Workflow identifier |
| `version` | Yes | Workflow definition version |
| `inputs` | No | Declared input schema |
| `policies` | No | Workflow-wide policy values |
| `steps` | Yes | Ordered list of step definitions |
| `notifications` | No | Optional hook configuration |

## Inputs

Declare inputs so the engine can validate required values before execution.

```yaml
inputs:
  issue_number:
    type: integer
    required: true
  environment:
    type: string
    required: false
```

The current engine enforces required presence. It does not yet provide a rich type system beyond workflow-authored conventions.

## Step Types

### Runner Steps

Runner steps execute work through one of the supported runner types.

#### Shell Runner

```yaml
- id: fast_checks
  type: runner
  runner: shell
  command: "ruff check src tests"
  timeout: 300
```

Use `shell` when you want shell parsing and shell syntax.

#### Python Script Runner

```yaml
- id: summarize_report
  type: runner
  runner: python_script
  command: "scripts/build_report.py"
  argv:
    - "{issue_number}"
    - "--format"
    - "json"
  timeout: 300
```

`python_script` executes the given script path through the current Python interpreter.

#### Agent Runner

```yaml
- id: draft_summary
  type: runner
  runner: cli_codex
  prompt: |
    Review the prior artifacts and produce a concise summary.
  context_from:
    - step: fetch_data
      field: result_summary
  tools:
    - read_file
    - bash
  timeout: 300
```

Current agent runners:

- `cli_claude`
- `cli_codex`

### Template Interpolation

The `command`, `argv`, and `prompt` fields support `{variable}` placeholders that are resolved from workflow `inputs` at execution time.

```yaml
inputs:
  project_name:
    type: string
    required: true

steps:
  - id: greet
    type: runner
    runner: shell
    command: "echo Building {project_name}"
```

Rules:

- Variables must reference declared `inputs` — undefined variables raise an error
- For **shell runners**, values are passed through `shlex.quote()` before interpolation to prevent shell injection
- For **python_script runners** and **agent prompts**, values are interpolated as plain strings (no shell quoting needed)

### Gate Steps

Gate steps evaluate a named condition.

```yaml
- id: gate_issue_current
  type: gate
  condition: issue_is_current
  if_false: blocked_issue_not_current
```

Fields:

| Field | Required | Description |
|---|---|---|
| `id` | Yes | Step identifier |
| `type` | Yes | Must be `gate` |
| `condition` | Yes | Name of registered gate function |
| `if_false` | No | Stop reason / summary when blocked |

### Loop Steps

Loop steps repeat nested steps up to a bounded round count.

```yaml
- id: review_loop
  type: loop
  max_rounds: 2
  steps:
    - id: review
      type: runner
      runner: cli_codex
      prompt: |
        Review the current changes and report findings.
    - id: fix_review_findings
      type: runner
      runner: cli_claude
      prompt: |
        Address the findings from the prior review step.
      context_from:
        - step: review
          field: result_findings
```

Nested steps are persisted with per-round identifiers so they appear in history and monitoring.

## Runner Fields

Not every field applies to every runner.

| Field | Applies to | Description |
|---|---|---|
| `runner` | all runner steps | Runner type |
| `command` | `shell`, `python_script` | Shell command string or script path |
| `argv` | `python_script` | Positional arguments |
| `prompt` | agent runners | User prompt |
| `system_prompt` | agent runners | Optional system override |
| `context_from` | agent runners | Pull data from earlier step results |
| `tools` | agent runners | Optional tool filter |
| `env` | opaque runners | Extra environment variables |
| `working_dir` | opaque runners | Working directory override |
| `timeout` | all runner steps | Per-step timeout in seconds |
| `approval_mode` | reserved policy field | Cannot be more permissive than effective safety config |

## `context_from`

`context_from` lets a later step consume normalized output from earlier steps.

```yaml
context_from:
  - step: fast_checks
    field: result_summary
  - step: review
    field: result_findings
```

Rules:

- references must point to earlier steps
- both `step` and `field` are required
- invalid references are rejected at definition load time

The field path is dotted and resolves against normalized step result data.

Examples:

- `result_summary`
- `result_artifacts.exit_code`
- `result_findings`

## Notifications

Workflows can define best-effort notification hooks.

```yaml
notifications:
  hooks:
    - transport: webhook
      url: https://events.example.com/workflows
      events:
        - run_completed
        - run_failed
    - transport: unix_socket
      path: /tmp/workflow-events.sock
      events:
        - all
```

Supported transports:

- `webhook`
- `unix_socket`

Webhook rules:

- URLs are validated against Anteroom's egress allowlist before execution starts
- `ai.allowed_domains` and `ai.block_localhost_api` apply
- invalid or blocked URLs fail closed at run startup

Delivery rules:

- hook failures are logged
- hook failures do not fail the workflow
- pending hook tasks are drained with a bounded timeout before process exit

## Validation Rules

The current loader validates at least:

- `kind`, `id`, and `version`
- at least one step exists
- runner steps have a valid payload
- gate steps provide `condition`
- `context_from` references only earlier steps
- approval mode cannot exceed effective safety policy

Runtime validation also checks:

- required inputs are present
- target lock can be acquired
- working directory exists for opaque runners
- hook config passes egress validation before the run starts

## Example: Research-Style Workflow

```yaml
kind: workflow
id: daily_briefing
version: 0.1.0

inputs:
  topic:
    type: string
    required: true

steps:
  - id: collect_sources
    type: runner
    runner: shell
    command: "python scripts/fetch_sources.py --topic {topic}"

  - id: summarize_sources
    type: runner
    runner: cli_codex
    prompt: |
      Summarize the collected sources for {topic}.
    context_from:
      - step: collect_sources
        field: result_summary

  - id: gate_summary_quality
    type: gate
    condition: always_pass
    if_false: blocked_summary_quality
```

This is why the engine should stay generic even if the first example happens to be delivery-focused.

## Current Limits

What definitions do not do yet:

- browser-side authoring
- arbitrary branching beyond registered conditions and loop control
- human-gate step types
- generic workflow catalog management in the web UI

## See Also

- [Workflow Concepts](concepts.md)
- [Workflow Commands](commands.md)
- [Workflow Monitoring](monitoring.md)
