# Workflow Commands

The workflow CLI gives you one-shot commands for starting, inspecting, recovering, and cancelling workflow runs.

## Command Overview

```bash
$ aroom workflow run <workflow> [options]
$ aroom workflow status <run_id>
$ aroom workflow list [--status ...] [--workflow ...]
$ aroom workflow history <run_id>
$ aroom workflow resume <run_id> [--from-step ...] [--definition ...]
$ aroom workflow cancel <run_id>
```

## `aroom workflow run`

Start a new workflow run from a workflow id or a YAML path.

```bash
$ aroom workflow run examples/workflows/issue_delivery.yaml --issue 123
```

### Arguments

| Argument | Required | Description |
|---|---|---|
| `workflow_name` | Yes | Workflow id or path to a YAML definition |
| `--issue` | No | Issue number for issue-oriented workflows |
| `--dry-run` | No | Show the resolved plan without executing |

### Dry Run

Use `--dry-run` to inspect the definition, target, and steps without creating a run:

```bash
$ aroom workflow run examples/workflows/issue_delivery.yaml --issue 123 --dry-run
```

### Live Progress

During execution, the CLI prints progress as workflow events arrive:

- `... <step_id>` when a step starts
- `done <step_id>` when a step succeeds
- `blocked <step_id>` when a step finishes with a blocked result
- `failed <step_id>` when a step errors

After execution finishes, the CLI also prints:

- a post-run step summary
- final workflow status
- the run id for follow-up commands

!!! info
    This is still a foreground command. The process owns the run until it exits, fails, or is interrupted.

## `aroom workflow status`

Show current state and step history for one run.

```bash
$ aroom workflow status 9f8c7b2a-...
```

The output includes:

- workflow id and version
- target kind and target ref
- current run status
- stop reason, if any
- current step id
- persisted step table

## `aroom workflow list`

List workflow runs from the current database.

```bash
$ aroom workflow list
$ aroom workflow list --status paused
$ aroom workflow list --workflow issue_delivery
```

### Filters

| Flag | Description |
|---|---|
| `--status` | Filter by run status |
| `--workflow` | Filter by workflow id |
| `--limit` | Limit row count |

`list` also performs on-demand stale-run recovery before showing results. If it recovers interrupted runs, it prints a recovery notice first.

## `aroom workflow history`

Show durable step and event history for one run.

```bash
$ aroom workflow history 9f8c7b2a-...
```

This is the best command when you want a full operational trail:

- step ids
- step types
- runner types
- result statuses
- durations
- event log sequence

## `aroom workflow resume`

Resume a paused or waiting-for-approval run from the last completed step.

```bash
$ aroom workflow resume 9f8c7b2a-...
```

### Options

| Flag | Description |
|---|---|
| `--from-step` | Override the resume point |
| `--definition` | Path to the YAML definition for custom workflows |

### Definition Resolution

Resume resolves definitions in this order:

1. explicit `--definition`
2. built-in or example workflow by ID

For custom workflows, you should pass `--definition`.

### What Resume Does

When a run is resumed, the engine:

- re-acquires the workflow lock
- rebuilds prior step results from persisted records
- skips completed steps
- restarts execution with fresh runner sessions

## `aroom workflow cancel`

Cancel a paused or waiting-for-approval workflow run.

```bash
$ aroom workflow cancel 9f8c7b2a-...
```

### Important Limitation

`cancel` works on `paused` or `waiting_for_approval` runs only.

If a run is actively executing in another terminal, stop it with `Ctrl-C` in that terminal. The engine does not yet support cross-process active cancellation.

## Exit and Recovery Behavior

### Process Interrupted

If the workflow process dies:

- stale `running` runs can be recovered
- interrupted steps are marked `interrupted`
- locks are released during recovery
- `resume` continues from the durable state

### Hook Delivery

If the workflow has notification hooks, Anteroom drains pending hook deliveries with a bounded timeout before the command exits.

## Example Session

```bash
$ aroom workflow run examples/workflows/issue_delivery.yaml --issue 123

Starting workflow: issue_delivery v0.1.0
Target: issue:123

  ... gate_issue_current
  done gate_issue_current
  ... start_work
  done start_work (15230ms)
  ... fast_checks
  failed fast_checks  Exit code 1

Workflow failed: step_failed:fast_checks
Run ID: 9f8c7b2a-...

$ aroom workflow status 9f8c7b2a-...
$ aroom workflow history 9f8c7b2a-...
```

## Current Scope

What the workflow CLI does today:

- run workflows
- inspect runs
- recover paused runs
- cancel paused runs

What it does not do today:

- browser-driven execution
- cross-process cancel for active runs
- workflow authoring from the CLI

## See Also

- [Workflow Definitions](definitions.md)
- [Workflow Monitoring](monitoring.md)
- [Workflow API Reference](api-reference.md)
