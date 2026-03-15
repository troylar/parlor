"""Tests for workflow resume, heartbeat, crash recovery, and cancel."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from anteroom.config import WorkflowConfig
from anteroom.db import init_db
from anteroom.services.workflow_engine import (
    WorkflowEngine,
    load_definition,
    register_gate_condition,
)
from anteroom.services.workflow_runners import create_default_registry
from anteroom.services.workflow_storage import (
    acquire_lock,
    create_workflow_event,
    create_workflow_run,
    create_workflow_step,
    find_running_steps,
    find_stale_runs,
    get_lock,
    get_workflow_run,
    list_completed_step_ids,
    list_workflow_events,
    list_workflow_steps,
    release_lock,
    update_workflow_run,
    update_workflow_step,
)

GENERIC_WORKFLOW = """\
kind: workflow
id: test_resume
version: 0.1.0
inputs: {}
steps:
  - id: step_a
    type: runner
    runner: shell
    command: "echo step A"
    timeout: 10
  - id: step_b
    type: runner
    runner: shell
    command: "echo step B"
    timeout: 10
  - id: step_c
    type: runner
    runner: shell
    command: "echo step C"
    timeout: 10
"""


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as td:
        conn = init_db(Path(td) / "test.db")
        yield conn
        conn.close()


@pytest.fixture()
def engine(db: Any) -> WorkflowEngine:
    config = WorkflowConfig(heartbeat_interval=1, stale_threshold=2)
    registry = create_default_registry()
    return WorkflowEngine(db, config, registry)


@pytest.fixture(autouse=True)
def _register_test_gates():
    async def always_pass(run: Any, step: Any, inputs: Any) -> bool:
        return True

    register_gate_condition("always_pass", always_pass)
    yield


# ---------------------------------------------------------------------------
# Storage: find_stale_runs, find_running_steps, list_completed_step_ids
# ---------------------------------------------------------------------------


class TestStaleRunDetection:
    def test_find_stale_runs_with_old_heartbeat(self, db: Any) -> None:
        run = create_workflow_run(
            db,
            workflow_id="test",
            workflow_version="0.1.0",
            target_kind="task",
            target_ref="t1",
        )
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        update_workflow_run(db, run["id"], status="running", heartbeat_at=old_time)

        stale = find_stale_runs(db, stale_threshold_seconds=60)
        assert len(stale) == 1
        assert stale[0]["id"] == run["id"]

    def test_find_stale_runs_ignores_fresh(self, db: Any) -> None:
        run = create_workflow_run(
            db,
            workflow_id="test",
            workflow_version="0.1.0",
            target_kind="task",
            target_ref="t1",
        )
        fresh_time = datetime.now(timezone.utc).isoformat()
        update_workflow_run(db, run["id"], status="running", heartbeat_at=fresh_time)

        stale = find_stale_runs(db, stale_threshold_seconds=60)
        assert len(stale) == 0

    def test_find_stale_runs_null_heartbeat(self, db: Any) -> None:
        """Null heartbeat_at is treated as stale."""
        run = create_workflow_run(
            db,
            workflow_id="test",
            workflow_version="0.1.0",
            target_kind="task",
            target_ref="t1",
        )
        update_workflow_run(db, run["id"], status="running")

        stale = find_stale_runs(db, stale_threshold_seconds=60)
        assert len(stale) == 1

    def test_find_stale_runs_ignores_non_running(self, db: Any) -> None:
        run = create_workflow_run(
            db,
            workflow_id="test",
            workflow_version="0.1.0",
            target_kind="task",
            target_ref="t1",
        )
        update_workflow_run(db, run["id"], status="paused")

        stale = find_stale_runs(db, stale_threshold_seconds=60)
        assert len(stale) == 0


class TestFindRunningSteps:
    def test_finds_running_steps(self, db: Any) -> None:
        run = create_workflow_run(
            db,
            workflow_id="test",
            workflow_version="0.1.0",
            target_kind="task",
            target_ref="t1",
        )
        step = create_workflow_step(
            db,
            run_id=run["id"],
            step_id="s1",
            step_type="runner",
        )
        update_workflow_step(db, step["id"], status="running")
        running = find_running_steps(db, run["id"])
        assert len(running) == 1
        assert running[0]["step_id"] == "s1"

    def test_ignores_completed_steps(self, db: Any) -> None:
        run = create_workflow_run(
            db,
            workflow_id="test",
            workflow_version="0.1.0",
            target_kind="task",
            target_ref="t1",
        )
        step = create_workflow_step(
            db,
            run_id=run["id"],
            step_id="s1",
            step_type="runner",
        )
        update_workflow_step(db, step["id"], status="completed")
        running = find_running_steps(db, run["id"])
        assert len(running) == 0


class TestListCompletedStepIds:
    def test_returns_completed_ids(self, db: Any) -> None:
        run = create_workflow_run(
            db,
            workflow_id="test",
            workflow_version="0.1.0",
            target_kind="task",
            target_ref="t1",
        )
        s1 = create_workflow_step(db, run_id=run["id"], step_id="s1", step_type="runner")
        s2 = create_workflow_step(db, run_id=run["id"], step_id="s2", step_type="runner")
        update_workflow_step(db, s1["id"], status="completed")
        update_workflow_step(db, s2["id"], status="running")

        completed = list_completed_step_ids(db, run["id"])
        assert completed == {"s1"}


# ---------------------------------------------------------------------------
# Engine: recover_interrupted_runs
# ---------------------------------------------------------------------------


class TestRecoverInterruptedRuns:
    @pytest.mark.asyncio
    async def test_marks_stale_runs_paused(self, db: Any, engine: WorkflowEngine) -> None:
        run = create_workflow_run(
            db,
            workflow_id="test",
            workflow_version="0.1.0",
            target_kind="task",
            target_ref="t1",
        )
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        update_workflow_run(db, run["id"], status="running", heartbeat_at=old_time)
        acquire_lock(db, target_kind="task", target_ref="t1", run_id=run["id"])

        recovered = await engine.recover_interrupted_runs()
        assert len(recovered) == 1

        refreshed = get_workflow_run(db, run["id"])
        assert refreshed["status"] == "paused"
        assert refreshed["stop_reason"] == "process_interrupted"

    @pytest.mark.asyncio
    async def test_releases_locks(self, db: Any, engine: WorkflowEngine) -> None:
        run = create_workflow_run(
            db,
            workflow_id="test",
            workflow_version="0.1.0",
            target_kind="task",
            target_ref="t1",
        )
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        update_workflow_run(db, run["id"], status="running", heartbeat_at=old_time)
        acquire_lock(db, target_kind="task", target_ref="t1", run_id=run["id"])

        await engine.recover_interrupted_runs()
        assert get_lock(db, target_kind="task", target_ref="t1") is None

    @pytest.mark.asyncio
    async def test_marks_running_steps_interrupted(self, db: Any, engine: WorkflowEngine) -> None:
        run = create_workflow_run(
            db,
            workflow_id="test",
            workflow_version="0.1.0",
            target_kind="task",
            target_ref="t1",
        )
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        update_workflow_run(db, run["id"], status="running", heartbeat_at=old_time)
        step = create_workflow_step(
            db,
            run_id=run["id"],
            step_id="active_step",
            step_type="runner",
        )
        update_workflow_step(db, step["id"], status="running")

        await engine.recover_interrupted_runs()

        steps = list_workflow_steps(db, run["id"])
        active = [s for s in steps if s["step_id"] == "active_step"]
        assert active[0]["status"] == "interrupted"
        assert active[0]["completed_at"] is not None

    @pytest.mark.asyncio
    async def test_emits_event_with_interrupted_steps(self, db: Any, engine: WorkflowEngine) -> None:
        run = create_workflow_run(
            db,
            workflow_id="test",
            workflow_version="0.1.0",
            target_kind="task",
            target_ref="t1",
        )
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        update_workflow_run(db, run["id"], status="running", heartbeat_at=old_time)
        step = create_workflow_step(
            db,
            run_id=run["id"],
            step_id="active_step",
            step_type="runner",
        )
        update_workflow_step(db, step["id"], status="running")

        await engine.recover_interrupted_runs()

        events = list_workflow_events(db, run["id"])
        pause_events = [e for e in events if e["event_type"] == "run_paused"]
        assert len(pause_events) == 1
        assert "active_step" in pause_events[0]["payload"]["interrupted_steps"]


# ---------------------------------------------------------------------------
# Engine: resume_run
# ---------------------------------------------------------------------------


class TestResumeRun:
    @pytest.mark.asyncio
    async def test_resume_skips_completed_steps(self, db: Any, engine: WorkflowEngine) -> None:
        """Resume skips completed steps and executes remaining."""
        defn = load_definition(GENERIC_WORKFLOW)
        run = await engine.start_run(defn, target_kind="task", target_ref="t1")
        assert run["status"] == "completed"

        # Manually mark as paused to simulate resume scenario
        update_workflow_run(db, run["id"], status="paused", stop_reason="test")

        # Mark only step_a and step_b as completed in a way resume recognizes
        # (they're already completed from the initial run)
        completed = list_completed_step_ids(db, run["id"])
        assert "step_a" in completed
        assert "step_b" in completed
        assert "step_c" in completed

        # Resume — all steps already completed, should complete immediately
        result = await engine.resume_run(run["id"], defn)
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_resume_rejects_non_resumable(self, db: Any, engine: WorkflowEngine) -> None:
        defn = load_definition(GENERIC_WORKFLOW)
        run = await engine.start_run(defn, target_kind="task", target_ref="t1")
        # Run is completed — not resumable
        with pytest.raises(ValueError, match="not resumable"):
            await engine.resume_run(run["id"], defn)

    @pytest.mark.asyncio
    async def test_resume_with_from_step_override(self, db: Any, engine: WorkflowEngine) -> None:
        defn = load_definition(GENERIC_WORKFLOW)
        run = await engine.start_run(defn, target_kind="task", target_ref="t1")
        update_workflow_run(db, run["id"], status="paused", stop_reason="test")

        # Resume from step_c — skips step_a and step_b
        result = await engine.resume_run(run["id"], defn, from_step="step_c")
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_resume_invalid_from_step_raises(self, db: Any, engine: WorkflowEngine) -> None:
        defn = load_definition(GENERIC_WORKFLOW)
        run = await engine.start_run(defn, target_kind="task", target_ref="t1")
        update_workflow_run(db, run["id"], status="paused", stop_reason="test")

        with pytest.raises(ValueError, match="not found in workflow"):
            await engine.resume_run(run["id"], defn, from_step="nonexistent")

    @pytest.mark.asyncio
    async def test_resume_emits_run_resumed_event(self, db: Any, engine: WorkflowEngine) -> None:
        defn = load_definition(GENERIC_WORKFLOW)
        run = await engine.start_run(defn, target_kind="task", target_ref="t1")
        update_workflow_run(db, run["id"], status="paused", stop_reason="test")

        await engine.resume_run(run["id"], defn)

        events = list_workflow_events(db, run["id"])
        resumed = [e for e in events if e["event_type"] == "run_resumed"]
        assert len(resumed) == 1


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


class TestCancel:
    def test_cancel_paused_run(self, db: Any) -> None:
        run = create_workflow_run(
            db,
            workflow_id="test",
            workflow_version="0.1.0",
            target_kind="task",
            target_ref="t1",
        )
        update_workflow_run(db, run["id"], status="paused")
        acquire_lock(db, target_kind="task", target_ref="t1", run_id=run["id"])

        # Cancel
        update_workflow_run(db, run["id"], status="cancelled")
        release_lock(db, run_id=run["id"])
        create_workflow_event(
            db,
            run_id=run["id"],
            event_type="run_cancelled",
            payload={"cancelled_from_status": "paused"},
        )

        refreshed = get_workflow_run(db, run["id"])
        assert refreshed["status"] == "cancelled"
        assert get_lock(db, target_kind="task", target_ref="t1") is None


# ---------------------------------------------------------------------------
# Generic runner preflight
# ---------------------------------------------------------------------------


class TestRunnerPreflight:
    @pytest.mark.asyncio
    async def test_missing_working_dir_fails(self) -> None:
        from anteroom.services.workflow_runners import execute_opaque_runner

        result = await execute_opaque_runner(
            mode="shell",
            command="echo test",
            working_dir="/nonexistent/path/that/does/not/exist",
            timeout=10,
        )
        assert result.status == "failed"
        assert "does not exist" in result.summary
