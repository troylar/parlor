# Workflow Monitoring

Workflow monitoring gives you live progress, durable history, and external event delivery without turning the workflow engine into a web-only system.

## Monitoring Surfaces

Workflows currently expose state through four channels:

1. CLI progress output during execution
2. durable SQLite records for runs, steps, and events
3. read-only REST endpoints
4. SSE and outbound notification hooks

These surfaces all come from the same engine events.

## CLI Progress

`aroom workflow run` and `aroom workflow resume` emit live progress while the run is executing.

Typical output looks like:

```text
Starting workflow: issue_delivery v0.1.0
Target: issue:123

  ... gate_issue_current
  done gate_issue_current
  ... start_work
  done start_work (18432ms)
  ... fast_checks
  failed fast_checks  Exit code 1
```

This comes from engine event callbacks, not from polling after the run finishes.

After the live stream, the CLI also prints a persisted post-run summary and the run id.

## Durable Records

The monitoring model starts with SQLite tables:

| Table | Purpose |
|---|---|
| `workflow_runs` | run-level state |
| `workflow_steps` | step execution history |
| `workflow_events` | durable event log |
| `workflow_locks` | target concurrency guard |

That durable state powers:

- `workflow status`
- `workflow history`
- recovery and resume
- the read-only API

## Event Bus

Workflow events are also published to Anteroom's existing event bus on:

```text
workflow:{run_id}
```

This matters because the workflow command and the web app may be separate processes. The event bus persists events into `change_log`, and the web app's poller reads them back out for SSE delivery.

That is the bridge that makes CLI-started workflows visible to the web side.

## Event Types

The engine emits workflow events such as:

- `run_started`
- `run_resumed`
- `run_paused`
- `run_completed`
- `run_failed`
- `step_started`
- `step_finished`
- `step_failed`

On the SSE channel, these are published with `workflow_` prefixes, for example:

- `workflow_run_started`
- `workflow_step_finished`

## Notification Hooks

Workflows can optionally notify external consumers.

Supported transports:

- `webhook`
- `unix_socket`

### Webhooks

Webhook delivery is:

- HTTP `POST`
- JSON payload
- best-effort
- bounded by timeout

Webhook URLs are validated against Anteroom's egress policy before execution:

- `ai.allowed_domains`
- `ai.block_localhost_api`

### Unix Socket Hooks

Unix socket hooks send JSON datagrams to a local Unix socket path.

This is useful when you want local observability or integration without opening network listeners.

### Delivery Guarantees

Hooks are intentionally lightweight:

- hook failures are logged
- hook failures do not fail the workflow
- pending hook deliveries are drained with a bounded timeout before process exit

!!! warning
    Hooks are best-effort notifications, not a transactional outbox. If you need a guaranteed audit source, use the durable workflow tables.

## Current Web Scope

The web layer for workflows is intentionally read-only in the current release.

You have:

- REST endpoints
- SSE streaming

You do not have yet:

- dedicated browser pages for workflow runs
- browser-side workflow execution
- browser-side run control actions

## Typical Monitoring Flows

### Local Developer Flow

1. Start a workflow from the CLI
2. Watch live CLI progress
3. Use `workflow status` or `workflow history` for follow-up inspection
4. Optionally subscribe to SSE from the web side

### External Observer Flow

1. Configure hooks in the workflow definition
2. Start the workflow from the CLI
3. Receive step/run events in your webhook consumer or local socket listener

### Web Observer Flow

1. Query `/api/workflow-runs`
2. Open `/api/workflow-runs/{run_id}`
3. Subscribe to `/api/events?workflow_run_id=<run_id>`

## What Monitoring Is For

Monitoring is not just UI polish. It supports:

- operator awareness
- recovery after interruption
- audit-friendly reconstruction of what happened
- external automation integrations
- CLI/web parity on the same underlying run state

## See Also

- [Workflow Commands](commands.md)
- [Workflow API Reference](api-reference.md)
- [Workflow Definitions](definitions.md)
