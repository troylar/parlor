"""Tests for the workflow engine core.

These tests use a GENERIC test workflow — not issue_delivery — to prove the
engine is domain-neutral. The test workflow has runner, gate, and loop steps
that process arbitrary data, not GitHub-specific data.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from anteroom.config import WorkflowConfig
from anteroom.db import init_db
from anteroom.services.workflow_engine import (
    WorkflowEngine,
    load_definition,
    register_gate_condition,
    resolve_context_from,
    resolve_template,
    validate_approval_mode,
)
from anteroom.services.workflow_runners import create_default_registry
from anteroom.services.workflow_storage import list_workflow_events, list_workflow_steps

# ---------------------------------------------------------------------------
# Generic test workflow YAML (domain-neutral — no GitHub/PR/issue concepts)
# ---------------------------------------------------------------------------

GENERIC_WORKFLOW = """\
kind: workflow
id: test_pipeline
version: 0.1.0
inputs:
  target_name:
    type: string
    required: true
  threshold:
    type: integer
    required: false
policies:
  max_review_rounds: 2
steps:
  - id: validate_input
    type: runner
    runner: shell
    command: "echo Validating {target_name}"
    timeout: 10
  - id: gate_ready
    type: gate
    condition: always_pass
    if_false: not_ready
  - id: process_data
    type: runner
    runner: shell
    command: "echo Processing data for {target_name}"
    timeout: 30
"""

LOOP_WORKFLOW = """\
kind: workflow
id: test_loop
version: 0.1.0
inputs: {}
policies: {}
steps:
  - id: refine_loop
    type: loop
    max_rounds: 3
    steps:
      - id: check
        type: runner
        runner: shell
        command: "echo checking round"
      - id: fix
        type: runner
        runner: shell
        command: "echo fixing round"
"""

GATE_FAIL_WORKFLOW = """\
kind: workflow
id: test_gate_fail
version: 0.1.0
inputs: {}
steps:
  - id: pre_check
    type: runner
    runner: shell
    command: "echo pre-check"
  - id: gate_block
    type: gate
    condition: always_fail
    if_false: requirement_not_met
  - id: should_not_run
    type: runner
    runner: shell
    command: "echo should not reach here"
"""


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as td:
        conn = init_db(Path(td) / "test.db")
        yield conn
        conn.close()


@pytest.fixture()
def engine(db: Any) -> WorkflowEngine:
    config = WorkflowConfig()
    registry = create_default_registry()
    return WorkflowEngine(db, config, registry)


@pytest.fixture(autouse=True)
def _register_test_gates():
    """Register generic test gate conditions."""

    async def always_pass(run: Any, step: Any, inputs: Any) -> bool:
        return True

    async def always_fail(run: Any, step: Any, inputs: Any) -> bool:
        return False

    register_gate_condition("always_pass", always_pass)
    register_gate_condition("always_fail", always_fail)
    yield


# ---------------------------------------------------------------------------
# Definition loading
# ---------------------------------------------------------------------------


class TestLoadDefinition:
    def test_load_valid_yaml(self) -> None:
        defn = load_definition(GENERIC_WORKFLOW)
        assert defn.id == "test_pipeline"
        assert defn.version == "0.1.0"
        assert len(defn.steps) == 3
        assert defn.inputs["target_name"]["required"] is True

    def test_load_with_loop(self) -> None:
        defn = load_definition(LOOP_WORKFLOW)
        assert defn.steps[0].type == "loop"
        assert defn.steps[0].max_rounds == 3
        assert len(defn.steps[0].steps) == 2

    def test_missing_kind_raises(self) -> None:
        yaml_str = (
            "id: test\nversion: 0.1.0\nsteps:\n  - id: s1\n    type: runner\n    runner: shell\n    command: echo"
        )
        with pytest.raises(ValueError, match="kind: workflow"):
            load_definition(yaml_str)

    def test_missing_id_raises(self) -> None:
        with pytest.raises(ValueError, match="'id'"):
            load_definition("kind: workflow\nversion: 0.1.0\nsteps:\n  - id: s1\n    type: runner")

    def test_empty_steps_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one step"):
            load_definition("kind: workflow\nid: test\nversion: 0.1.0\nsteps: []")

    def test_invalid_step_type_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid type"):
            load_definition("kind: workflow\nid: t\nversion: 0.1.0\nsteps:\n  - id: s1\n    type: invalid")

    def test_step_missing_id_raises(self) -> None:
        with pytest.raises(ValueError, match="'id'"):
            load_definition("kind: workflow\nid: t\nversion: 0.1.0\nsteps:\n  - type: runner")

    def test_load_from_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.yaml"
        f.write_text(GENERIC_WORKFLOW)
        defn = load_definition(f)
        assert defn.id == "test_pipeline"


# ---------------------------------------------------------------------------
# Template resolution
# ---------------------------------------------------------------------------


class TestTemplateResolution:
    def test_resolve_simple(self) -> None:
        result = resolve_template("hello {name}", {"name": "world"})
        assert result == "hello world"

    def test_resolve_shell_quoted(self) -> None:
        result = resolve_template("echo {val}", {"val": "foo bar; rm -rf /"}, shell_quote=True)
        assert "rm -rf" not in result or "'" in result

    def test_missing_variable_raises(self) -> None:
        with pytest.raises(KeyError):
            resolve_template("hello {missing}", {})


class TestContextFromResolution:
    def test_resolve_simple(self) -> None:
        refs = [{"step": "step1", "field": "result_summary"}]
        results = {"step1": {"result_summary": "All checks passed"}}
        ctx = resolve_context_from(refs, results)
        assert "All checks passed" in ctx

    def test_resolve_dotted_path(self) -> None:
        refs = [{"step": "step1", "field": "result_artifacts.count"}]
        results = {"step1": {"result_artifacts": {"count": 42}}}
        ctx = resolve_context_from(refs, results)
        assert "42" in ctx

    def test_missing_step_skipped(self) -> None:
        refs = [{"step": "nonexistent", "field": "summary"}]
        ctx = resolve_context_from(refs, {})
        assert ctx == ""


# ---------------------------------------------------------------------------
# Approval mode validation
# ---------------------------------------------------------------------------


class TestApprovalModeValidation:
    def test_equal_strictness_passes(self) -> None:
        defn = load_definition(GENERIC_WORKFLOW)
        validate_approval_mode(defn, "ask_for_writes")

    def test_stricter_passes(self) -> None:
        extra = (
            "steps:\n  - id: strict_step\n    type: runner\n"
            "    runner: shell\n    command: echo\n    approval_mode: ask"
        )
        yaml_str = GENERIC_WORKFLOW.replace("steps:", extra)
        defn = load_definition(yaml_str)
        validate_approval_mode(defn, "ask_for_writes")

    def test_more_permissive_raises(self) -> None:
        extra = (
            "steps:\n  - id: lax_step\n    type: runner\n    runner: shell\n    command: echo\n    approval_mode: auto"
        )
        yaml_str = GENERIC_WORKFLOW.replace("steps:", extra)
        defn = load_definition(yaml_str)
        with pytest.raises(ValueError, match="more permissive"):
            validate_approval_mode(defn, "ask_for_writes")

    def test_policy_level_too_permissive_raises(self) -> None:
        yaml_str = GENERIC_WORKFLOW.replace("policies:", "policies:\n  approval_mode: auto")
        defn = load_definition(yaml_str)
        with pytest.raises(ValueError, match="more permissive"):
            validate_approval_mode(defn, "ask_for_writes")


# ---------------------------------------------------------------------------
# Engine execution — generic workflows
# ---------------------------------------------------------------------------


class TestEngineExecution:
    @pytest.mark.asyncio
    async def test_run_generic_workflow(self, db: Any, engine: WorkflowEngine) -> None:
        """Engine executes a generic pipeline workflow with no domain-specific concepts."""
        defn = load_definition(GENERIC_WORKFLOW)
        run = await engine.start_run(
            defn,
            target_kind="dataset",
            target_ref="sales_q4",
            inputs={"target_name": "sales_q4"},
        )
        assert run["status"] == "completed"
        steps = list_workflow_steps(db, run["id"])
        assert len(steps) == 3
        assert all(s["status"] == "completed" for s in steps)

    @pytest.mark.asyncio
    async def test_gate_blocks_workflow(self, db: Any, engine: WorkflowEngine) -> None:
        """Gate step blocks the workflow when condition returns False."""
        defn = load_definition(GATE_FAIL_WORKFLOW)
        run = await engine.start_run(defn, target_kind="task", target_ref="t1")
        assert run["status"] == "blocked"
        assert "requirement_not_met" in (run.get("stop_reason") or "")

        steps = list_workflow_steps(db, run["id"])
        completed = [s for s in steps if s["status"] == "completed"]
        assert len(completed) == 2  # pre_check + gate_block

    @pytest.mark.asyncio
    async def test_loop_respects_max_rounds(self, db: Any, engine: WorkflowEngine) -> None:
        """Loop step exits after max_rounds."""
        defn = load_definition(LOOP_WORKFLOW)
        run = await engine.start_run(defn, target_kind="batch", target_ref="b1")
        assert run["status"] == "completed"

    @pytest.mark.asyncio
    async def test_concurrency_lock_rejects_duplicate(self, db: Any, engine: WorkflowEngine) -> None:
        """Second run on same target is rejected while first holds the lock."""
        from anteroom.services.workflow_storage import acquire_lock, create_workflow_run, release_lock

        # Create a real run record so the FK constraint is satisfied
        blocker = create_workflow_run(
            db,
            workflow_id="blocker",
            workflow_version="0.1.0",
            target_kind="doc",
            target_ref="d1",
        )
        acquire_lock(db, target_kind="doc", target_ref="d1", run_id=blocker["id"])

        defn = load_definition(GENERIC_WORKFLOW)
        with pytest.raises(RuntimeError, match="already locked"):
            await engine.start_run(defn, target_kind="doc", target_ref="d1", inputs={"target_name": "x"})

        release_lock(db, run_id=blocker["id"])

    @pytest.mark.asyncio
    async def test_missing_required_input_raises(self, db: Any, engine: WorkflowEngine) -> None:
        """Missing required input raises ValueError before execution starts."""
        defn = load_definition(GENERIC_WORKFLOW)
        with pytest.raises(ValueError, match="Missing required input"):
            await engine.start_run(defn, target_kind="task", target_ref="t1")

    @pytest.mark.asyncio
    async def test_events_emitted(self, db: Any, engine: WorkflowEngine) -> None:
        """Engine emits durable events for each state transition."""
        defn = load_definition(GENERIC_WORKFLOW)
        run = await engine.start_run(
            defn,
            target_kind="task",
            target_ref="t1",
            inputs={"target_name": "test"},
        )
        events = list_workflow_events(db, run["id"])
        event_types = [e["event_type"] for e in events]
        assert "run_started" in event_types
        assert "step_started" in event_types
        assert "step_finished" in event_types
        assert "run_completed" in event_types

    @pytest.mark.asyncio
    async def test_step_results_stored(self, db: Any, engine: WorkflowEngine) -> None:
        """Step results are persisted in storage."""
        defn = load_definition(GENERIC_WORKFLOW)
        run = await engine.start_run(
            defn,
            target_kind="task",
            target_ref="t1",
            inputs={"target_name": "test"},
        )
        steps = list_workflow_steps(db, run["id"])
        for step in steps:
            assert step["result_status"] is not None
            assert step["duration_ms"] is not None
            assert step["completed_at"] is not None

    @pytest.mark.asyncio
    async def test_lock_released_on_completion(self, db: Any, engine: WorkflowEngine) -> None:
        """Lock is released after successful completion."""
        from anteroom.services.workflow_storage import get_lock

        defn = load_definition(GENERIC_WORKFLOW)
        await engine.start_run(
            defn,
            target_kind="task",
            target_ref="t1",
            inputs={"target_name": "test"},
        )
        assert get_lock(db, target_kind="task", target_ref="t1") is None

    @pytest.mark.asyncio
    async def test_lock_released_on_failure(self, db: Any, engine: WorkflowEngine) -> None:
        """Lock is released even when the run fails."""
        from anteroom.services.workflow_storage import get_lock

        defn = load_definition(GATE_FAIL_WORKFLOW)
        await engine.start_run(defn, target_kind="task", target_ref="t1")
        assert get_lock(db, target_kind="task", target_ref="t1") is None


FAILING_WORKFLOW = """\
kind: workflow
id: test_fail
version: 0.1.0
inputs: {}
steps:
  - id: will_fail
    type: runner
    runner: shell
    command: "exit 7"
    timeout: 10
"""


class TestRunnerFailurePropagation:
    """Failed runner results must fail the workflow, not silently continue."""

    @pytest.mark.asyncio
    async def test_failed_runner_fails_workflow(self, db: Any, engine: WorkflowEngine) -> None:
        """A shell step that exits non-zero must fail the entire run."""
        defn = load_definition(FAILING_WORKFLOW)
        run = await engine.start_run(defn, target_kind="task", target_ref="t1")
        assert run["status"] == "failed"
        assert "step_failed:will_fail" in (run.get("stop_reason") or "")

    @pytest.mark.asyncio
    async def test_failed_runner_emits_run_failed_event(self, db: Any, engine: WorkflowEngine) -> None:
        """A failed run must emit a run_failed durable event for SSE/webhook consumers."""
        defn = load_definition(FAILING_WORKFLOW)
        run = await engine.start_run(defn, target_kind="task", target_ref="t2")
        events = list_workflow_events(db, run["id"])
        event_types = [e["event_type"] for e in events]
        assert "run_failed" in event_types

    @pytest.mark.asyncio
    async def test_failed_step_stops_subsequent_steps(self, db: Any, engine: WorkflowEngine) -> None:
        """Steps after a failed step should not execute."""
        yaml_str = """\
kind: workflow
id: test_fail_stops
version: 0.1.0
inputs: {}
steps:
  - id: fail_step
    type: runner
    runner: shell
    command: "exit 1"
    timeout: 10
  - id: should_not_run
    type: runner
    runner: shell
    command: "echo should not reach here"
    timeout: 10
"""
        defn = load_definition(yaml_str)
        run = await engine.start_run(defn, target_kind="task", target_ref="t1")
        assert run["status"] == "failed"
        steps = list_workflow_steps(db, run["id"])
        step_ids = [s["step_id"] for s in steps]
        assert "fail_step" in step_ids
        assert "should_not_run" not in step_ids


# ---------------------------------------------------------------------------
# Anti-overfitting check: engine has no GitHub-specific types
# ---------------------------------------------------------------------------


class TestDomainNeutrality:
    """These tests explicitly verify the engine is domain-neutral."""

    @pytest.mark.asyncio
    async def test_arbitrary_target_kind(self, db: Any, engine: WorkflowEngine) -> None:
        """target_kind can be anything — not just 'issue'."""
        defn = load_definition(GENERIC_WORKFLOW)
        run = await engine.start_run(
            defn,
            target_kind="document",
            target_ref="quarterly_report",
            inputs={"target_name": "quarterly_report"},
        )
        assert run["status"] == "completed"
        assert run["target_kind"] == "document"

    @pytest.mark.asyncio
    async def test_non_coding_workflow(self, db: Any, engine: WorkflowEngine) -> None:
        """A workflow with no coding concepts runs successfully."""
        yaml_str = """\
kind: workflow
id: data_pipeline
version: 0.1.0
inputs:
  dataset:
    type: string
    required: true
steps:
  - id: validate
    type: runner
    runner: shell
    command: "echo Validating {dataset}"
  - id: quality_gate
    type: gate
    condition: always_pass
    if_false: quality_check_failed
  - id: transform
    type: runner
    runner: shell
    command: "echo Transforming {dataset}"
"""
        defn = load_definition(yaml_str)
        run = await engine.start_run(
            defn,
            target_kind="dataset",
            target_ref="sales_2026",
            inputs={"dataset": "sales_2026"},
        )
        assert run["status"] == "completed"
        steps = list_workflow_steps(db, run["id"])
        assert len(steps) == 3


# ---------------------------------------------------------------------------
# Blocker fixes — approval validation, loop persistence, agent runner
# ---------------------------------------------------------------------------


PERMISSIVE_WORKFLOW = """\
kind: workflow
id: test_permissive
version: 0.1.0
inputs: {}
policies:
  approval_mode: auto
steps:
  - id: do_thing
    type: runner
    runner: shell
    command: "echo hello"
"""


class TestApprovalModeEnforcement:
    """validate_approval_mode() must be called and block permissive workflows."""

    @pytest.mark.asyncio
    async def test_permissive_policy_blocked_at_start(self, db: Any) -> None:
        """Workflow with policies.approval_mode: auto rejected under ask_for_writes."""
        config = WorkflowConfig()
        registry = create_default_registry()
        engine = WorkflowEngine(
            db,
            config,
            registry,
            effective_approval_mode="ask_for_writes",
        )
        defn = load_definition(PERMISSIVE_WORKFLOW)
        with pytest.raises(ValueError, match="more permissive"):
            await engine.start_run(defn, target_kind="task", target_ref="t1")

    @pytest.mark.asyncio
    async def test_permissive_step_blocked_at_start(self, db: Any) -> None:
        """Step with approval_mode: auto rejected under ask_for_writes."""
        yaml_str = """\
kind: workflow
id: test_step_permissive
version: 0.1.0
inputs: {}
steps:
  - id: lax
    type: runner
    runner: shell
    command: "echo"
    approval_mode: auto
"""
        config = WorkflowConfig()
        registry = create_default_registry()
        engine = WorkflowEngine(
            db,
            config,
            registry,
            effective_approval_mode="ask_for_writes",
        )
        defn = load_definition(yaml_str)
        with pytest.raises(ValueError, match="more permissive"):
            await engine.start_run(defn, target_kind="task", target_ref="t1")


class TestLoopStepPersistence:
    """Loop nested steps must create workflow_steps rows and events."""

    @pytest.mark.asyncio
    async def test_loop_nested_steps_persisted(self, db: Any, engine: WorkflowEngine) -> None:
        """Each nested step in each round creates a step record."""
        defn = load_definition(LOOP_WORKFLOW)
        run = await engine.start_run(defn, target_kind="batch", target_ref="b1")
        assert run["status"] == "completed"

        steps = list_workflow_steps(db, run["id"])
        nested_steps = [s for s in steps if "_r" in s["step_id"]]
        assert len(nested_steps) >= 2  # at least 1 round with 2 steps

        for ns in nested_steps:
            assert ns["status"] == "completed"
            assert ns["result_status"] is not None
            assert ns["duration_ms"] is not None

    @pytest.mark.asyncio
    async def test_loop_nested_events_emitted(self, db: Any, engine: WorkflowEngine) -> None:
        """Events are emitted for each nested step start/finish."""
        defn = load_definition(LOOP_WORKFLOW)
        run = await engine.start_run(defn, target_kind="batch", target_ref="b1")

        events = list_workflow_events(db, run["id"])
        nested_events = [e for e in events if e.get("step_id") and "_r" in e["step_id"]]
        # at least 2 starts + 2 finishes for round 1
        assert len(nested_events) >= 4


# ---------------------------------------------------------------------------
# Load-time validation and fail-closed behavior
# ---------------------------------------------------------------------------


class TestLoadTimeValidation:
    """Bad step payloads and context_from refs must be rejected at load time."""

    def test_shell_runner_missing_command_rejected(self) -> None:
        yaml_str = """\
kind: workflow
id: bad_shell
version: 0.1.0
inputs: {}
steps:
  - id: no_cmd
    type: runner
    runner: shell
"""
        with pytest.raises(ValueError, match="requires a 'command' field"):
            load_definition(yaml_str)

    def test_python_script_missing_command_rejected(self) -> None:
        yaml_str = """\
kind: workflow
id: bad_pyscript
version: 0.1.0
inputs: {}
steps:
  - id: no_cmd
    type: runner
    runner: python_script
"""
        with pytest.raises(ValueError, match="requires a 'command' field"):
            load_definition(yaml_str)

    def test_agent_runner_missing_prompt_rejected(self) -> None:
        yaml_str = """\
kind: workflow
id: bad_agent
version: 0.1.0
inputs: {}
steps:
  - id: no_prompt
    type: runner
    runner: cli_claude
"""
        with pytest.raises(ValueError, match="requires a 'prompt' field"):
            load_definition(yaml_str)

    def test_gate_missing_condition_rejected(self) -> None:
        yaml_str = """\
kind: workflow
id: bad_gate
version: 0.1.0
inputs: {}
steps:
  - id: no_cond
    type: gate
"""
        with pytest.raises(ValueError, match="requires a 'condition' field"):
            load_definition(yaml_str)

    def test_context_from_nonexistent_step_rejected(self) -> None:
        yaml_str = """\
kind: workflow
id: bad_ctx
version: 0.1.0
inputs: {}
steps:
  - id: step1
    type: runner
    runner: shell
    command: "echo hi"
    context_from:
      - step: nonexistent
        field: summary
"""
        with pytest.raises(ValueError, match="has not appeared before"):
            load_definition(yaml_str)

    def test_context_from_forward_reference_rejected(self) -> None:
        """context_from can't reference a step that comes later."""
        yaml_str = """\
kind: workflow
id: forward_ref
version: 0.1.0
inputs: {}
steps:
  - id: first
    type: runner
    runner: shell
    command: "echo"
    context_from:
      - step: second
        field: summary
  - id: second
    type: runner
    runner: shell
    command: "echo"
"""
        with pytest.raises(ValueError, match="has not appeared before"):
            load_definition(yaml_str)

    def test_context_from_valid_back_reference_passes(self) -> None:
        """context_from referencing an earlier step is fine."""
        yaml_str = """\
kind: workflow
id: valid_ctx
version: 0.1.0
inputs: {}
steps:
  - id: first
    type: runner
    runner: shell
    command: "echo first"
  - id: second
    type: runner
    runner: shell
    command: "echo second"
    context_from:
      - step: first
        field: result_summary
"""
        defn = load_definition(yaml_str)
        assert len(defn.steps) == 2

    def test_context_from_missing_step_field_rejected(self) -> None:
        yaml_str = """\
kind: workflow
id: bad_ref
version: 0.1.0
inputs: {}
steps:
  - id: first
    type: runner
    runner: shell
    command: "echo"
  - id: second
    type: runner
    runner: shell
    command: "echo"
    context_from:
      - field: summary
"""
        with pytest.raises(ValueError, match="missing 'step' field"):
            load_definition(yaml_str)


class TestAgentRunnerFailClosed:
    """Agent runner must fail when AI service is not configured."""

    @pytest.mark.asyncio
    async def test_agent_runner_no_ai_service_fails(self, db: Any) -> None:
        """Engine with no ai_service fails on agent runner steps, not succeeds."""
        yaml_str = """\
kind: workflow
id: agent_no_ai
version: 0.1.0
inputs: {}
steps:
  - id: do_ai
    type: runner
    runner: cli_claude
    prompt: "Do something"
"""
        config = WorkflowConfig()
        registry = create_default_registry()
        engine = WorkflowEngine(db, config, registry)  # no ai_service
        defn = load_definition(yaml_str)
        run = await engine.start_run(defn, target_kind="task", target_ref="t1")
        # The run must fail, not succeed with synthetic data
        assert run["status"] == "failed"
        assert run.get("stop_reason") is not None
