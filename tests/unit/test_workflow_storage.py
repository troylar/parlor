"""Tests for workflow storage CRUD operations."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from anteroom.db import init_db
from anteroom.services.workflow_storage import (
    acquire_lock,
    create_approval_request,
    create_workflow_event,
    create_workflow_run,
    create_workflow_step,
    delete_workflow_run,
    get_approval_request,
    get_lock,
    get_pending_approval,
    get_workflow_run,
    get_workflow_step,
    list_workflow_events,
    list_workflow_runs,
    list_workflow_steps,
    release_lock,
    release_lock_by_target,
    resolve_approval_request,
    update_workflow_run,
    update_workflow_step,
)


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as td:
        conn = init_db(Path(td) / "test.db")
        yield conn
        conn.close()


def _make_run(db: Any, **overrides: Any) -> dict[str, Any]:
    defaults = {
        "workflow_id": "issue_delivery",
        "workflow_version": "0.1.0",
        "target_kind": "issue",
        "target_ref": "123",
    }
    defaults.update(overrides)
    return create_workflow_run(db, **defaults)


# ---------------------------------------------------------------------------
# Workflow Runs
# ---------------------------------------------------------------------------


class TestWorkflowRuns:
    def test_create_run(self, db: Any) -> None:
        run = _make_run(db)
        assert run["id"]
        assert run["status"] == "pending"
        assert run["workflow_id"] == "issue_delivery"
        assert run["target_kind"] == "issue"
        assert run["target_ref"] == "123"
        assert run["created_at"]
        assert run["inputs"] is None

    def test_create_run_with_inputs(self, db: Any) -> None:
        run = _make_run(db, inputs={"issue_number": 42})
        assert run["inputs"] == {"issue_number": 42}

    def test_get_run(self, db: Any) -> None:
        run = _make_run(db)
        fetched = get_workflow_run(db, run["id"])
        assert fetched is not None
        assert fetched["id"] == run["id"]
        assert fetched["workflow_id"] == "issue_delivery"

    def test_get_run_not_found(self, db: Any) -> None:
        assert get_workflow_run(db, "nonexistent") is None

    def test_get_run_deserializes_inputs(self, db: Any) -> None:
        run = _make_run(db, inputs={"key": "value"})
        fetched = get_workflow_run(db, run["id"])
        assert fetched is not None
        assert fetched["inputs"] == {"key": "value"}

    def test_update_run(self, db: Any) -> None:
        run = _make_run(db)
        updated = update_workflow_run(db, run["id"], status="running", started_at="2026-01-01T00:00:00Z")
        assert updated is not None
        assert updated["status"] == "running"
        assert updated["started_at"] == "2026-01-01T00:00:00Z"

    def test_update_run_rejects_invalid_column(self, db: Any) -> None:
        run = _make_run(db)
        with pytest.raises(ValueError, match="not in allowed"):
            update_workflow_run(db, run["id"], workflow_id="hacked")

    def test_list_runs(self, db: Any) -> None:
        _make_run(db, target_ref="1")
        _make_run(db, target_ref="2")
        runs = list_workflow_runs(db)
        assert len(runs) == 2

    def test_list_runs_filter_status(self, db: Any) -> None:
        run = _make_run(db)
        update_workflow_run(db, run["id"], status="running")
        _make_run(db, target_ref="2")
        runs = list_workflow_runs(db, status="running")
        assert len(runs) == 1
        assert runs[0]["status"] == "running"

    def test_list_runs_filter_workflow(self, db: Any) -> None:
        _make_run(db, workflow_id="issue_delivery")
        _make_run(db, workflow_id="other", target_ref="2")
        runs = list_workflow_runs(db, workflow_id="issue_delivery")
        assert len(runs) == 1

    def test_list_runs_limit_offset(self, db: Any) -> None:
        for i in range(5):
            _make_run(db, target_ref=str(i))
        runs = list_workflow_runs(db, limit=2, offset=1)
        assert len(runs) == 2

    def test_delete_run(self, db: Any) -> None:
        run = _make_run(db)
        assert delete_workflow_run(db, run["id"]) is True
        assert get_workflow_run(db, run["id"]) is None

    def test_delete_run_not_found(self, db: Any) -> None:
        assert delete_workflow_run(db, "nonexistent") is False

    def test_delete_cascades_steps(self, db: Any) -> None:
        run = _make_run(db)
        create_workflow_step(db, run_id=run["id"], step_id="s1", step_type="runner")
        delete_workflow_run(db, run["id"])
        assert list_workflow_steps(db, run["id"]) == []

    def test_delete_cascades_events(self, db: Any) -> None:
        run = _make_run(db)
        create_workflow_event(db, run_id=run["id"], event_type="step_started")
        delete_workflow_run(db, run["id"])
        assert list_workflow_events(db, run["id"]) == []

    def test_delete_cascades_approvals(self, db: Any) -> None:
        run = _make_run(db)
        create_approval_request(
            db, run_id=run["id"], step_id="s1", tool_name="bash", tool_args={}, risk_tier="EXECUTE"
        )
        delete_workflow_run(db, run["id"])
        assert get_pending_approval(db, run["id"]) is None

    def test_delete_cascades_locks(self, db: Any) -> None:
        run = _make_run(db)
        acquire_lock(db, target_kind="issue", target_ref="123", run_id=run["id"])
        delete_workflow_run(db, run["id"])
        assert get_lock(db, target_kind="issue", target_ref="123") is None


# ---------------------------------------------------------------------------
# Workflow Steps
# ---------------------------------------------------------------------------


class TestWorkflowSteps:
    def test_create_step(self, db: Any) -> None:
        run = _make_run(db)
        step = create_workflow_step(
            db, run_id=run["id"], step_id="verify_issue", step_type="runner", runner_type="shell"
        )
        assert step["step_id"] == "verify_issue"
        assert step["status"] == "pending"
        assert step["runner_type"] == "shell"

    def test_get_step(self, db: Any) -> None:
        run = _make_run(db)
        step = create_workflow_step(db, run_id=run["id"], step_id="s1", step_type="gate")
        fetched = get_workflow_step(db, step["id"])
        assert fetched is not None
        assert fetched["step_type"] == "gate"

    def test_list_steps(self, db: Any) -> None:
        run = _make_run(db)
        create_workflow_step(db, run_id=run["id"], step_id="s1", step_type="runner")
        create_workflow_step(db, run_id=run["id"], step_id="s2", step_type="gate")
        steps = list_workflow_steps(db, run["id"])
        assert len(steps) == 2

    def test_update_step_with_result(self, db: Any) -> None:
        run = _make_run(db)
        step = create_workflow_step(db, run_id=run["id"], step_id="s1", step_type="runner")
        updated = update_workflow_step(
            db,
            step["id"],
            status="completed",
            result_status="success",
            result_summary="All checks pass",
            result_artifacts={"pr_number": 42},
            result_findings=[{"type": "lint", "message": "ok"}],
            duration_ms=5000,
            completed_at="2026-01-01T00:05:00Z",
        )
        assert updated is not None
        assert updated["status"] == "completed"
        assert updated["result_artifacts"] == {"pr_number": 42}
        assert updated["result_findings"] == [{"type": "lint", "message": "ok"}]
        assert updated["duration_ms"] == 5000

    def test_update_step_no_changes(self, db: Any) -> None:
        run = _make_run(db)
        step = create_workflow_step(db, run_id=run["id"], step_id="s1", step_type="runner")
        result = update_workflow_step(db, step["id"])
        assert result is not None
        assert result["status"] == "pending"


# ---------------------------------------------------------------------------
# Workflow Events
# ---------------------------------------------------------------------------


class TestWorkflowEvents:
    def test_create_event(self, db: Any) -> None:
        run = _make_run(db)
        evt = create_workflow_event(db, run_id=run["id"], event_type="step_started", step_id="s1")
        assert evt["event_type"] == "step_started"
        assert evt["step_id"] == "s1"
        assert evt["id"] is not None

    def test_create_event_with_payload(self, db: Any) -> None:
        run = _make_run(db)
        evt = create_workflow_event(db, run_id=run["id"], event_type="step_finished", payload={"duration_ms": 100})
        assert evt["payload"] == {"duration_ms": 100}

    def test_list_events_ordered(self, db: Any) -> None:
        run = _make_run(db)
        create_workflow_event(db, run_id=run["id"], event_type="step_started")
        create_workflow_event(db, run_id=run["id"], event_type="step_finished")
        events = list_workflow_events(db, run["id"])
        assert len(events) == 2
        assert events[0]["id"] < events[1]["id"]
        assert events[0]["event_type"] == "step_started"
        assert events[1]["event_type"] == "step_finished"

    def test_list_events_deserializes_payload(self, db: Any) -> None:
        run = _make_run(db)
        create_workflow_event(db, run_id=run["id"], event_type="test", payload={"key": "val"})
        events = list_workflow_events(db, run["id"])
        assert events[0]["payload"] == {"key": "val"}


# ---------------------------------------------------------------------------
# Approval Requests
# ---------------------------------------------------------------------------


class TestApprovalRequests:
    def test_create_approval(self, db: Any) -> None:
        run = _make_run(db)
        req = create_approval_request(
            db, run_id=run["id"], step_id="s1", tool_name="write_file",
            tool_args={"path": "/src/foo.py"}, risk_tier="WRITE",
        )
        assert req["status"] == "pending"
        assert req["tool_name"] == "write_file"
        assert req["tool_args"] == {"path": "/src/foo.py"}

    def test_get_approval(self, db: Any) -> None:
        run = _make_run(db)
        req = create_approval_request(
            db, run_id=run["id"], step_id="s1", tool_name="bash",
            tool_args={"command": "rm -rf /"}, risk_tier="DESTRUCTIVE",
        )
        fetched = get_approval_request(db, req["id"])
        assert fetched is not None
        assert fetched["risk_tier"] == "DESTRUCTIVE"

    def test_get_pending_approval(self, db: Any) -> None:
        run = _make_run(db)
        create_approval_request(
            db, run_id=run["id"], step_id="s1", tool_name="bash",
            tool_args={}, risk_tier="EXECUTE",
        )
        pending = get_pending_approval(db, run["id"])
        assert pending is not None
        assert pending["status"] == "pending"

    def test_get_pending_approval_none(self, db: Any) -> None:
        run = _make_run(db)
        assert get_pending_approval(db, run["id"]) is None

    def test_resolve_approved(self, db: Any) -> None:
        run = _make_run(db)
        req = create_approval_request(
            db, run_id=run["id"], step_id="s1", tool_name="bash",
            tool_args={}, risk_tier="EXECUTE",
        )
        resolved = resolve_approval_request(db, req["id"], status="approved", resolved_by="operator")
        assert resolved is not None
        assert resolved["status"] == "approved"
        assert resolved["resolved_by"] == "operator"
        assert resolved["resolved_at"] is not None

    def test_resolve_denied(self, db: Any) -> None:
        run = _make_run(db)
        req = create_approval_request(
            db, run_id=run["id"], step_id="s1", tool_name="bash",
            tool_args={}, risk_tier="EXECUTE",
        )
        resolved = resolve_approval_request(db, req["id"], status="denied")
        assert resolved is not None
        assert resolved["status"] == "denied"

    def test_resolve_expired(self, db: Any) -> None:
        run = _make_run(db)
        req = create_approval_request(
            db, run_id=run["id"], step_id="s1", tool_name="bash",
            tool_args={}, risk_tier="EXECUTE", timeout_at="2026-01-01T00:05:00Z",
        )
        resolved = resolve_approval_request(db, req["id"], status="expired", resolved_by="timeout")
        assert resolved is not None
        assert resolved["status"] == "expired"

    def test_resolve_already_resolved_raises(self, db: Any) -> None:
        run = _make_run(db)
        req = create_approval_request(
            db, run_id=run["id"], step_id="s1", tool_name="bash",
            tool_args={}, risk_tier="EXECUTE",
        )
        resolve_approval_request(db, req["id"], status="approved", resolved_by="op1")
        with pytest.raises(ValueError, match="already resolved"):
            resolve_approval_request(db, req["id"], status="denied", resolved_by="op2")

    def test_resolve_nonexistent_returns_none(self, db: Any) -> None:
        result = resolve_approval_request(db, "nonexistent", status="approved")
        assert result is None

    def test_resolve_invalid_status_raises(self, db: Any) -> None:
        with pytest.raises(ValueError, match="Invalid approval resolution"):
            resolve_approval_request(db, "any-id", status="invalid")


# ---------------------------------------------------------------------------
# Workflow Locks
# ---------------------------------------------------------------------------


class TestWorkflowLocks:
    def test_acquire_lock(self, db: Any) -> None:
        run = _make_run(db)
        assert acquire_lock(db, target_kind="issue", target_ref="123", run_id=run["id"]) is True

    def test_acquire_duplicate_fails(self, db: Any) -> None:
        run = _make_run(db)
        acquire_lock(db, target_kind="issue", target_ref="123", run_id=run["id"])
        run2 = _make_run(db, target_ref="456")
        assert acquire_lock(db, target_kind="issue", target_ref="123", run_id=run2["id"]) is False

    def test_get_lock(self, db: Any) -> None:
        run = _make_run(db)
        acquire_lock(db, target_kind="issue", target_ref="123", run_id=run["id"])
        lock = get_lock(db, target_kind="issue", target_ref="123")
        assert lock is not None
        assert lock["run_id"] == run["id"]

    def test_get_lock_not_found(self, db: Any) -> None:
        assert get_lock(db, target_kind="issue", target_ref="999") is None

    def test_release_by_run_id(self, db: Any) -> None:
        run = _make_run(db)
        acquire_lock(db, target_kind="issue", target_ref="123", run_id=run["id"])
        assert release_lock(db, run_id=run["id"]) is True
        assert get_lock(db, target_kind="issue", target_ref="123") is None

    def test_release_by_target(self, db: Any) -> None:
        run = _make_run(db)
        acquire_lock(db, target_kind="issue", target_ref="123", run_id=run["id"])
        assert release_lock_by_target(db, target_kind="issue", target_ref="123") is True
        assert get_lock(db, target_kind="issue", target_ref="123") is None

    def test_release_nonexistent(self, db: Any) -> None:
        assert release_lock(db, run_id="nonexistent") is False

    def test_cascade_on_run_delete(self, db: Any) -> None:
        run = _make_run(db)
        acquire_lock(db, target_kind="issue", target_ref="123", run_id=run["id"])
        delete_workflow_run(db, run["id"])
        assert get_lock(db, target_kind="issue", target_ref="123") is None
