# Workflow API Reference

The workflow API is read-only in the current release and exposes run state, step history, durable events, and SSE subscriptions.

## REST Endpoints

### `GET /api/workflows`

List available workflow definitions from built-in and example directories. The `source` field is `"built_in"` or `"example"`.

```bash
$ curl http://localhost:8080/api/workflows
```

Example response:

```json
[
  {
    "id": "issue_delivery",
    "source": "example",
    "path": "/path/to/examples/workflows/issue_delivery.yaml"
  }
]
```

### `GET /api/workflow-runs`

List workflow runs.

```bash
$ curl "http://localhost:8080/api/workflow-runs?status=paused&limit=10"
```

Query parameters:

| Parameter | Description |
|---|---|
| `status` | Filter by run status |
| `workflow_id` | Filter by workflow id |
| `limit` | Page size, 1-100 |
| `offset` | Pagination offset |
| `db` | Optional shared database name when `db_manager` is present |

Example response:

```json
[
  {
    "id": "uuid",
    "workflow_id": "issue_delivery",
    "workflow_version": "0.1.0",
    "status": "paused",
    "target_kind": "issue",
    "target_ref": "123",
    "current_step_id": "fast_checks",
    "attempt_count": 4,
    "stop_reason": "process_interrupted",
    "created_at": "2026-03-15T10:00:00+00:00",
    "updated_at": "2026-03-15T10:04:00+00:00",
    "started_at": "2026-03-15T10:00:05+00:00",
    "completed_at": null,
    "heartbeat_at": null
  }
]
```

### `GET /api/workflow-runs/{run_id}`

Return run detail plus step history and any pending approval record.

```bash
$ curl http://localhost:8080/api/workflow-runs/<run_id>
```

Example response:

```json
{
  "id": "uuid",
  "workflow_id": "issue_delivery",
  "workflow_version": "0.1.0",
  "status": "running",
  "target_kind": "issue",
  "target_ref": "123",
  "current_step_id": "start_work",
  "attempt_count": 2,
  "stop_reason": null,
  "steps": [
    {
      "id": "uuid",
      "step_id": "gate_issue_current",
      "step_type": "gate",
      "runner_type": null,
      "status": "completed",
      "result_status": "success",
      "result_summary": "Gate 'issue_is_current' passed",
      "duration_ms": 6
    }
  ],
  "pending_approval": null
}
```

When the run status is `waiting_for_approval`, `pending_approval` contains the unresolved approval request (tool name, arguments, risk tier). It is `null` otherwise.

### `GET /api/workflow-runs/{run_id}/events`

Return the durable event log for one run.

```bash
$ curl http://localhost:8080/api/workflow-runs/<run_id>/events
```

Example response:

```json
[
  {
    "id": 1,
    "run_id": "uuid",
    "step_id": null,
    "event_type": "run_started",
    "payload": {
      "workflow_id": "issue_delivery",
      "target": "issue:123"
    },
    "created_at": "2026-03-15T10:00:05+00:00"
  },
  {
    "id": 2,
    "run_id": "uuid",
    "step_id": "start_work",
    "event_type": "step_started",
    "payload": {
      "step_type": "runner"
    },
    "created_at": "2026-03-15T10:00:07+00:00"
  }
]
```

### Error Responses

Missing runs return:

```json
{"detail": "Run not found"}
```

with HTTP `404`.

## SSE Endpoint

Workflow event streaming uses the existing SSE endpoint:

```bash
$ curl -N "http://localhost:8080/api/events?workflow_run_id=<run_id>"
```

### Query Parameters

| Parameter | Description |
|---|---|
| `workflow_run_id` | Subscribe to `workflow:{run_id}` |
| `db` | Database name, default `personal` |
| `client_id` | Reserved client identifier |

`conversation_id` continues to serve chat/conversation SSE. Workflow SSE uses `workflow_run_id`, not a `conversation_id` hack.

### Example Stream

```text
event: connected
data: {}

event: workflow_run_started
data: {"run_id":"uuid","event_type":"run_started","workflow_id":"issue_delivery","target":"issue:123"}

event: workflow_step_started
data: {"run_id":"uuid","event_type":"step_started","step_type":"runner"}

event: workflow_step_finished
data: {"run_id":"uuid","event_type":"step_finished","result_status":"success","duration_ms":143}
```

## Current API Scope

What the workflow API does today:

- list definitions
- list runs
- show run detail
- show durable event history
- stream live events

What it does not do today:

- start workflows
- resume workflows
- cancel workflows
- approve or deny workflow actions

Those are CLI-owned operations in the current release.

## Related Monitoring Model

Workflow SSE is fed by the same event bus used elsewhere in Anteroom:

- CLI workflow runs publish events
- events are persisted to `change_log`
- the web app poller reads those events
- SSE subscribers receive the same transitions

That is what gives you cross-process visibility for CLI-started runs.

## See Also

- [Workflow Monitoring](monitoring.md)
- [Workflow Commands](commands.md)
- [API Index](../api/index.md)
