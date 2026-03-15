# Workflow Quickstart

This quickstart shows the shortest path from "I have a workflow definition" to "I can run it, inspect it, and recover it."

## Before You Start

You need:

- an Anteroom install with the workflow feature available
- a database under your normal Anteroom data directory
- a workflow definition, either:
  - a path to your own YAML file
  - a reference workflow in `examples/workflows/`
  - a packaged workflow id

If your workflow uses agent runners such as `cli_claude` or `cli_codex`, you also need the normal AI and tool configuration that those runners depend on.

## 1. Inspect a Workflow Without Running It

Use `--dry-run` first.

```bash
$ aroom workflow run issue_delivery --issue 123 --dry-run
```

You can use a bare name like `issue_delivery` (resolved from `examples/workflows/` or built-in directories) or a full path like `./my_workflow.yaml`.

Dry run shows:

- the resolved workflow id and version
- the target kind and target ref
- the input payload
- the ordered step list

Use this when you want to verify that:

- the right workflow definition is being resolved
- required inputs are present
- the step order looks correct

## 2. Start a Workflow

```bash
$ aroom workflow run issue_delivery --issue 123
```

During execution, the CLI prints live step progress:

```text
Starting workflow: issue_delivery v0.1.0
Target: issue:123

  ... gate_issue_current
  done gate_issue_current
  ... start_work
  done start_work (15230ms)
  ... fast_checks
  failed fast_checks  Exit code 1
```

At the end, Anteroom prints:

- the persisted step summary
- the final run status
- the run id

Save the run id. The follow-up commands all use it.

## 3. Check Status

```bash
$ aroom workflow status <run_id>
```

Use `status` when you want the current snapshot:

- workflow id and version
- target
- run status
- stop reason
- current step
- persisted step table

## 4. Review Full History

```bash
$ aroom workflow history <run_id>
```

Use `history` when you want the operational trail:

- step execution records
- result statuses
- durations
- durable workflow events

This is the better command when you are debugging why a run blocked or failed.

## 5. List Runs

```bash
$ aroom workflow list
$ aroom workflow list --status paused
$ aroom workflow list --workflow issue_delivery
```

`list` is also the first recovery tool. Before rendering results, it checks for stale `running` runs and repairs them into a resumable state when needed.

## 6. Resume a Paused Run

```bash
$ aroom workflow resume <run_id>
```

Optional overrides:

```bash
$ aroom workflow resume <run_id> --from-step fast_checks
$ aroom workflow resume <run_id> --definition path/to/workflow.yaml
```

Resume works by:

- recovering stale runs if needed
- reloading the definition
- rebuilding prior step results from the database
- skipping completed steps
- restarting from the correct point with fresh runner sessions

For custom workflows, pass `--definition` so Anteroom knows which YAML file to reload.

## 7. Cancel a Paused Run

```bash
$ aroom workflow cancel <run_id>
```

Current limitation:

- `cancel` only works for paused or waiting-for-approval runs
- active foreground runs are stopped with `Ctrl-C` in the terminal that started them

## 8. Watch the Same Run Over the API

Current web support for workflows is read-only.

List runs:

```bash
$ curl http://localhost:8080/api/workflow-runs
```

Get one run:

```bash
$ curl http://localhost:8080/api/workflow-runs/<run_id>
```

Stream live events:

```bash
$ curl -N "http://localhost:8080/api/events?workflow_run_id=<run_id>"
```

That SSE stream is fed by the same underlying workflow events that the CLI uses for progress output.

## 9. Add Notifications

Definitions can attach best-effort notification hooks:

```yaml
notifications:
  hooks:
    - transport: webhook
      url: https://events.example.com/workflows
      events:
        - run_completed
        - run_failed
```

Hook delivery is:

- best-effort
- bounded by timeout
- validated against the configured egress policy before the run starts

## Typical First Workflow Pattern

Most teams start with one of these shapes:

- shell-only automation pipeline
- agent plus shell validation pipeline
- gated review loop with bounded retries

Minimal shell example:

```yaml
kind: workflow
id: hello_workflow
version: 0.1.0

steps:
  - id: hello
    type: runner
    runner: shell
    command: "echo hello"
```

## What to Read Next

- [Workflow Concepts](concepts.md) for the runtime model
- [Workflow Definitions](definitions.md) for the YAML contract
- [Workflow Commands](commands.md) for the full CLI reference
- [Workflow Monitoring](monitoring.md) for SSE, hooks, and API visibility
