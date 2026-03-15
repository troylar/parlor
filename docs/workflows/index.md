# Workflows

Workflows let you run multi-step, resumable automation with durable state instead of hiding long-running work inside one chat transcript.

## Why Workflows?

Anteroom already gives you strong building blocks:

- [skills](../cli/skills.md) for reusable task prompts
- [tools](../cli/tools.md) for file edits, bash, search, and sub-agents
- [rules](../packs/artifact-types.md#rule) for always-on guidance and hard constraints
- [packs](../packs/index.md) and [spaces](../spaces/index.md) for sharing behavior and policy

Those pieces work well for a single turn. Workflows add the missing layer for work that spans:

- multiple steps
- retries and review loops
- process restarts
- durable state and history
- external monitoring

Common examples:

- issue delivery from approved plan to ready-for-review output
- overnight research collection and summarization
- gated document-generation pipelines
- policy-controlled operational runbooks

## What's What

| Concept | What it is |
|---|---|
| **Workflow definition** | A YAML file describing inputs, policies, steps, and optional notifications |
| **Workflow run** | One execution of a workflow against a specific target |
| **Runner step** | A unit of work executed by an agent runner, shell command, or Python script |
| **Gate step** | A named condition that can continue or block the run |
| **Loop step** | A bounded retry/review loop over nested steps |
| **Workflow event** | A durable state transition such as `step_started` or `run_completed` |
| **Workflow lock** | A DB-backed guard that prevents concurrent runs on the same target |
| **Hook** | A best-effort outbound notification via webhook or Unix socket |

## How It Fits

```text
Workflow Definition (YAML)
        │
        ▼
Workflow Engine
        │
        ├─→ agent runners / shell / python_script
        ├─→ workflow state in SQLite
        ├─→ workflow events
        ├─→ CLI progress output
        ├─→ read-only REST API
        └─→ SSE + notification hooks
```

## Current Scope

The current workflow feature set is intentionally narrow:

- CLI owns execution: `run`, `status`, `list`, `history`, `resume`, `cancel`
- the web side is read-only today: REST endpoints plus SSE streaming
- browser pages for workflow runs are a follow-up
- the engine is generic, but the shipped reference example is currently `issue_delivery` in `examples/workflows/`

!!! info
    Workflows are not a replacement for skills. A workflow coordinates durable execution over time. A skill shapes how a specific agent task is performed inside one step.

## Quick Links

### Start Here

- [Quickstart](quickstart.md) — run a workflow, inspect progress, and recover from interruption
- [Concepts](concepts.md) — mental model, lifecycle, state, and how workflows relate to skills and rules
- [Definitions](definitions.md) — YAML format, step types, hooks, and validation rules
- [Commands](commands.md) — CLI reference for running and recovering workflows
- [Monitoring](monitoring.md) — CLI progress, events, hooks, REST, and SSE
- [API Reference](api-reference.md) — exact endpoints and SSE parameters

### Related Docs

- [CLI Skills](../cli/skills.md) — reusable prompt templates for agent tasks
- [Packs & Artifacts](../packs/index.md) — distribute skills, rules, and config across teams
- [Spaces](../spaces/index.md) — activate packs and project context per workspace
- [Advanced Architecture](../advanced/architecture.md) — shared engine and runtime context

## Reference Example

The current reference workflow lives at `examples/workflows/issue_delivery.yaml`.

It is included as an example of how to express a real end-to-end process with gates,
runner steps, loops, monitoring hooks, and recovery. It is not the definition of the
workflow feature itself, and it should not be read as the only kind of workflow the
engine supports.
