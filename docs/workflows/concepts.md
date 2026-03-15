# Workflow Concepts

Workflows are Anteroom's durable orchestration layer: they decide what runs, in what order, with what policies, and how recovery works when a process dies halfway through.

## The Mental Model

Think in layers:

| Layer | Purpose | Good for | Not good for |
|---|---|---|---|
| **Skill** | Reusable prompt template | `/commit`, `/review`, domain-specific expert behavior | durable execution state |
| **Rule** | Always-on guidance or policy | coding standards, security constraints, process expectations | step sequencing |
| **Runner** | One execution backend | agent step, shell command, Python script | deciding workflow progression |
| **Workflow** | Multi-step orchestration with persistence | retries, recovery, monitoring, target locking | free-form one-off chat |

If you want a one-command prompt shortcut, use a skill.  
If you want policy or guidance applied every time, use a rule.  
If you want durable step-by-step execution with resume and monitoring, use a workflow.

## Core Objects

### Workflow Definition

A workflow definition is a YAML file with:

- an `id` and `version`
- declared `inputs`
- optional `policies`
- ordered `steps`
- optional `notifications`

Definitions are loaded in this priority order (first match wins):

1. Exact filesystem path (if the argument is a `.yaml`/`.yml` file that exists)
2. Reference examples in `examples/workflows/` (shipped with Anteroom)
3. Built-in definitions in `src/anteroom/workflows/` (inside the Python package)

### Workflow Run

A workflow run is one execution of a definition against a target. Each run stores:

- workflow id and version
- target kind and target ref
- current status
- current step id
- stop reason
- timestamps
- input payload
- attempt count

That run record is the source of truth. The workflow engine does not rely on chat history to reconstruct execution state.

### Step Record

Each step execution is persisted separately. A step record tracks:

- `step_id`
- `step_type`
- optional `runner_type`
- status
- normalized result summary
- artifacts and findings
- raw output path if present
- timing data

This is what powers status, history, resume, and monitoring.

## Step Types

### Runner

A runner step does work.

Current runner categories:

- `cli_claude`
- `cli_codex`
- `shell`
- `python_script`

Agent runners execute through Anteroom's agent loop. Shell and Python-script runners execute as subprocesses and are normalized into the same result shape.

### Gate

A gate step evaluates a named condition. It either:

- passes and the workflow continues
- blocks and the run stops with a clear reason

Gates keep workflow progression declarative. The engine does not hardcode business logic into the step loop.

### Loop

A loop step retries a nested sequence up to a bounded round limit.

Use loops for:

- review / fix cycles
- bounded validation retries
- repeat-until-clean patterns

Loops are durable: nested step executions are recorded, not just kept in memory.

## Run Statuses

Workflow runs move through a small set of statuses:

| Status | Meaning |
|---|---|
| `pending` | Run record created, not started yet |
| `running` | Currently executing |
| `paused` | Stopped and recoverable |
| `waiting_for_approval` | Paused waiting for an external approval — resumable or cancellable |
| `blocked` | Stopped by a gate or blocked step result |
| `completed` | Finished successfully |
| `failed` | Failed due to step error or exception |
| `cancelled` | Explicitly cancelled |

Step records have a separate status set:

- `pending`
- `running`
- `completed`
- `failed`
- `interrupted`
- `skipped`

## Recovery Model

Workflows are designed to survive process interruption.

Current recovery behavior:

- runs record a `heartbeat_at` timestamp while active
- stale `running` runs can be recovered on demand
- interrupted steps are marked `interrupted`
- locks are released during recovery
- `resume` rebuilds prior step results from the DB
- completed steps are skipped unless you override the resume point

!!! warning
    Active cancellation is not a cross-process control channel yet. `aroom workflow cancel` is for paused runs. To stop an active foreground run, use `Ctrl-C` in the terminal that started it.

## Concurrency Locking

Workflows use a DB-backed lock on `(target_kind, target_ref)`.

That means:

- only one workflow run can own a target at a time
- duplicate starts fail clearly
- lock enforcement is atomic at the database level
- lock cleanup happens on completion, recovery, or cancellation

This is what prevents two runs from racing on the same issue, report, or dataset target.

## Result Normalization

Every runner is normalized into the same shape:

```json
{
  "status": "success",
  "summary": "Checks passed.",
  "raw_output_path": null,
  "artifacts": {"exit_code": 0},
  "findings": [],
  "duration_ms": 182
}
```

The engine consumes normalized results, not runner-specific output formats.

## Skills, Rules, and Workflows

This is where workflows marry to the rest of Anteroom.

### Skills

Skills fit inside workflows, not beside them.

You use a workflow when you need durable orchestration.  
You use a skill when an agent runner step needs reusable expert behavior.

Examples:

- a workflow step can use an agent runner whose prompt is shaped by a review-oriented skill
- a future workflow definition can standardize the prompts it uses by following a skill-backed team convention

### Rules

Rules constrain workflow execution indirectly by shaping agent behavior and tool use.

Examples:

- a coding standards rule influences how an implementation step edits code
- a security rule discourages unsafe shell patterns inside an agent step
- a policy rule pushes a workflow to stop and ask for human review earlier

Rules belong in packs, spaces, and artifact loading. They should not be hardcoded into the workflow engine.

### Packs

Packs distribute the behavior around workflows:

- rules
- skills
- instructions
- config overlays

Workflows orchestrate those behaviors over time.

This is the clean split:

- packs distribute reusable building blocks
- workflows coordinate execution

## Monitoring Model

Workflows expose progress through four channels:

1. durable state in SQLite
2. CLI progress output
3. read-only REST endpoints
4. SSE and notification hooks

That is what makes workflows auditable and recoverable. You can inspect what happened even after the original terminal exits.

## Current Limits

The current implementation is intentionally conservative:

- the web layer is read-only for workflows
- workflow execution happens through the CLI
- browser pages for workflow runs are not shipped yet
- notification hooks are best-effort with bounded drain
- the packaged example focuses on delivery, but the engine itself is domain-neutral

## See Also

- [Definitions](definitions.md)
- [Commands](commands.md)
- [Monitoring](monitoring.md)
- [CLI Skills](../cli/skills.md)
- [Packs & Artifacts](../packs/index.md)
