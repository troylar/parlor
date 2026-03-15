"""Workflow engine storage — CRUD for runs, steps, events, approval requests, and locks."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..db import ThreadSafeConnection

logger = logging.getLogger(__name__)


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_VALID_RUN_STATUSES = frozenset(
    {
        "pending",
        "running",
        "paused",
        "waiting_for_approval",
        "blocked",
        "completed",
        "failed",
        "cancelled",
    }
)

_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled", "blocked"})

_VALID_STEP_STATUSES = frozenset(
    {"pending", "running", "completed", "failed", "interrupted", "skipped"}
)

_VALID_APPROVAL_STATUSES = frozenset({"pending", "approved", "denied", "expired"})

_ALLOWED_RUN_UPDATE_COLUMNS: set[str] = {
    "status",
    "current_step_id",
    "attempt_count",
    "stop_reason",
    "updated_at",
    "started_at",
    "completed_at",
}


# ---------------------------------------------------------------------------
# Workflow Runs
# ---------------------------------------------------------------------------


def create_workflow_run(
    db: ThreadSafeConnection,
    *,
    workflow_id: str,
    workflow_version: str,
    target_kind: str,
    target_ref: str,
    inputs: dict[str, Any] | None = None,
    space_id: str | None = None,
) -> dict[str, Any]:
    rid = _uuid()
    now = _now()
    inputs_json = json.dumps(inputs) if inputs else None
    db.execute(
        "INSERT INTO workflow_runs"
        " (id, workflow_id, workflow_version, status, target_kind, target_ref,"
        "  inputs_json, space_id, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rid, workflow_id, workflow_version, "pending", target_kind, target_ref, inputs_json, space_id, now, now),
    )
    db.commit()
    return {
        "id": rid,
        "workflow_id": workflow_id,
        "workflow_version": workflow_version,
        "status": "pending",
        "target_kind": target_kind,
        "target_ref": target_ref,
        "current_step_id": None,
        "attempt_count": 0,
        "stop_reason": None,
        "inputs": inputs,
        "space_id": space_id,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
    }


def get_workflow_run(db: ThreadSafeConnection, run_id: str) -> dict[str, Any] | None:
    row = db.execute_fetchone("SELECT * FROM workflow_runs WHERE id = ?", (run_id,))
    if not row:
        return None
    result = dict(row)
    raw = result.pop("inputs_json", None)
    result["inputs"] = json.loads(raw) if raw else None
    return result


def update_workflow_run(
    db: ThreadSafeConnection,
    run_id: str,
    **updates: Any,
) -> dict[str, Any] | None:
    if not updates:
        return get_workflow_run(db, run_id)
    updates["updated_at"] = _now()
    parts: list[str] = []
    params: list[Any] = []
    for col, val in updates.items():
        if col not in _ALLOWED_RUN_UPDATE_COLUMNS:
            raise ValueError(f"Column {col!r} not in allowed workflow_runs update columns")
        parts.append(f"{col} = ?")
        params.append(val)
    params.append(run_id)
    db.execute(f"UPDATE workflow_runs SET {', '.join(parts)} WHERE id = ?", tuple(params))
    db.commit()
    return get_workflow_run(db, run_id)


def list_workflow_runs(
    db: ThreadSafeConnection,
    *,
    status: str | None = None,
    workflow_id: str | None = None,
    target_kind: str | None = None,
    target_ref: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if status and status in _VALID_RUN_STATUSES:
        conditions.append("status = ?")
        params.append(status)
    if workflow_id:
        conditions.append("workflow_id = ?")
        params.append(workflow_id)
    if target_kind:
        conditions.append("target_kind = ?")
        params.append(target_kind)
    if target_ref:
        conditions.append("target_ref = ?")
        params.append(target_ref)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.extend([limit, offset])
    rows = db.execute_fetchall(
        f"SELECT * FROM workflow_runs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        tuple(params),
    )
    results = []
    for r in rows:
        d = dict(r)
        raw = d.pop("inputs_json", None)
        d["inputs"] = json.loads(raw) if raw else None
        results.append(d)
    return results


def delete_workflow_run(db: ThreadSafeConnection, run_id: str) -> bool:
    row = db.execute_fetchone("SELECT id FROM workflow_runs WHERE id = ?", (run_id,))
    if not row:
        return False
    db.execute("DELETE FROM workflow_runs WHERE id = ?", (run_id,))
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Workflow Steps
# ---------------------------------------------------------------------------


def create_workflow_step(
    db: ThreadSafeConnection,
    *,
    run_id: str,
    step_id: str,
    step_type: str,
    runner_type: str | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    sid = _uuid()
    now = _now()
    db.execute(
        "INSERT INTO workflow_steps"
        " (id, run_id, step_id, step_type, runner_type, status, attempt, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (sid, run_id, step_id, step_type, runner_type, "pending", attempt, now),
    )
    db.commit()
    return {
        "id": sid,
        "run_id": run_id,
        "step_id": step_id,
        "step_type": step_type,
        "runner_type": runner_type,
        "status": "pending",
        "attempt": attempt,
        "result_status": None,
        "result_summary": None,
        "result_artifacts": None,
        "result_findings": None,
        "raw_output_path": None,
        "duration_ms": None,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
    }


def get_workflow_step(db: ThreadSafeConnection, step_record_id: str) -> dict[str, Any] | None:
    row = db.execute_fetchone("SELECT * FROM workflow_steps WHERE id = ?", (step_record_id,))
    if not row:
        return None
    return _deserialize_step(dict(row))


def list_workflow_steps(db: ThreadSafeConnection, run_id: str) -> list[dict[str, Any]]:
    rows = db.execute_fetchall(
        "SELECT * FROM workflow_steps WHERE run_id = ? ORDER BY created_at",
        (run_id,),
    )
    return [_deserialize_step(dict(r)) for r in rows]


def update_workflow_step(
    db: ThreadSafeConnection,
    step_record_id: str,
    *,
    status: str | None = None,
    result_status: str | None = None,
    result_summary: str | None = None,
    result_artifacts: dict[str, Any] | None = None,
    result_findings: list[dict[str, Any]] | None = None,
    raw_output_path: str | None = None,
    duration_ms: int | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any] | None:
    parts: list[str] = []
    params: list[Any] = []
    if status is not None:
        parts.append("status = ?")
        params.append(status)
    if result_status is not None:
        parts.append("result_status = ?")
        params.append(result_status)
    if result_summary is not None:
        parts.append("result_summary = ?")
        params.append(result_summary)
    if result_artifacts is not None:
        parts.append("result_artifacts_json = ?")
        params.append(json.dumps(result_artifacts))
    if result_findings is not None:
        parts.append("result_findings_json = ?")
        params.append(json.dumps(result_findings))
    if raw_output_path is not None:
        parts.append("raw_output_path = ?")
        params.append(raw_output_path)
    if duration_ms is not None:
        parts.append("duration_ms = ?")
        params.append(duration_ms)
    if started_at is not None:
        parts.append("started_at = ?")
        params.append(started_at)
    if completed_at is not None:
        parts.append("completed_at = ?")
        params.append(completed_at)
    if not parts:
        return get_workflow_step(db, step_record_id)
    params.append(step_record_id)
    db.execute(f"UPDATE workflow_steps SET {', '.join(parts)} WHERE id = ?", tuple(params))
    db.commit()
    return get_workflow_step(db, step_record_id)


def _deserialize_step(d: dict[str, Any]) -> dict[str, Any]:
    raw_artifacts = d.pop("result_artifacts_json", None)
    d["result_artifacts"] = json.loads(raw_artifacts) if raw_artifacts else None
    raw_findings = d.pop("result_findings_json", None)
    d["result_findings"] = json.loads(raw_findings) if raw_findings else None
    return d


# ---------------------------------------------------------------------------
# Workflow Events
# ---------------------------------------------------------------------------


def create_workflow_event(
    db: ThreadSafeConnection,
    *,
    run_id: str,
    event_type: str,
    step_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _now()
    payload_json = json.dumps(payload) if payload else None
    cursor = db.execute(
        "INSERT INTO workflow_events (run_id, step_id, event_type, payload_json, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (run_id, step_id, event_type, payload_json, now),
    )
    db.commit()
    return {
        "id": cursor.lastrowid,
        "run_id": run_id,
        "step_id": step_id,
        "event_type": event_type,
        "payload": payload,
        "created_at": now,
    }


def list_workflow_events(db: ThreadSafeConnection, run_id: str) -> list[dict[str, Any]]:
    rows = db.execute_fetchall(
        "SELECT * FROM workflow_events WHERE run_id = ? ORDER BY id",
        (run_id,),
    )
    results = []
    for r in rows:
        d = dict(r)
        raw = d.pop("payload_json", None)
        d["payload"] = json.loads(raw) if raw else None
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Workflow Approval Requests
# ---------------------------------------------------------------------------


def create_approval_request(
    db: ThreadSafeConnection,
    *,
    run_id: str,
    step_id: str,
    tool_name: str,
    tool_args: dict[str, Any],
    risk_tier: str,
    timeout_at: str | None = None,
) -> dict[str, Any]:
    aid = _uuid()
    now = _now()
    db.execute(
        "INSERT INTO workflow_approval_requests"
        " (id, run_id, step_id, tool_name, tool_args_json, risk_tier, status, timeout_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (aid, run_id, step_id, tool_name, json.dumps(tool_args), risk_tier, "pending", timeout_at, now),
    )
    db.commit()
    return {
        "id": aid,
        "run_id": run_id,
        "step_id": step_id,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "risk_tier": risk_tier,
        "status": "pending",
        "resolved_by": None,
        "resolved_at": None,
        "timeout_at": timeout_at,
        "created_at": now,
    }


def get_approval_request(db: ThreadSafeConnection, request_id: str) -> dict[str, Any] | None:
    row = db.execute_fetchone("SELECT * FROM workflow_approval_requests WHERE id = ?", (request_id,))
    if not row:
        return None
    return _deserialize_approval(dict(row))


def get_pending_approval(db: ThreadSafeConnection, run_id: str) -> dict[str, Any] | None:
    row = db.execute_fetchone(
        "SELECT * FROM workflow_approval_requests WHERE run_id = ? AND status = 'pending' ORDER BY created_at DESC",
        (run_id,),
    )
    if not row:
        return None
    return _deserialize_approval(dict(row))


def resolve_approval_request(
    db: ThreadSafeConnection,
    request_id: str,
    *,
    status: str,
    resolved_by: str | None = None,
) -> dict[str, Any] | None:
    if status not in ("approved", "denied", "expired"):
        raise ValueError(f"Invalid approval resolution: {status!r}")
    now = _now()
    cursor = db.execute(
        "UPDATE workflow_approval_requests SET status = ?, resolved_by = ?, resolved_at = ?"
        " WHERE id = ? AND status = 'pending'",
        (status, resolved_by, now, request_id),
    )
    db.commit()
    if cursor.rowcount == 0:
        existing = get_approval_request(db, request_id)
        if existing and existing["status"] != "pending":
            raise ValueError(
                f"Approval request {request_id} already resolved as"
                f" {existing['status']!r}; cannot transition to {status!r}"
            )
        return None
    return get_approval_request(db, request_id)


def _deserialize_approval(d: dict[str, Any]) -> dict[str, Any]:
    raw = d.pop("tool_args_json", None)
    d["tool_args"] = json.loads(raw) if raw else None
    return d


# ---------------------------------------------------------------------------
# Workflow Locks
# ---------------------------------------------------------------------------


def acquire_lock(
    db: ThreadSafeConnection,
    *,
    target_kind: str,
    target_ref: str,
    run_id: str,
) -> bool:
    import sqlite3

    now = _now()
    try:
        db.execute(
            "INSERT INTO workflow_locks (target_kind, target_ref, run_id, acquired_at) VALUES (?, ?, ?, ?)",
            (target_kind, target_ref, run_id, now),
        )
        db.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def release_lock(db: ThreadSafeConnection, *, run_id: str) -> bool:
    cursor = db.execute("DELETE FROM workflow_locks WHERE run_id = ?", (run_id,))
    db.commit()
    return cursor.rowcount > 0


def release_lock_by_target(
    db: ThreadSafeConnection,
    *,
    target_kind: str,
    target_ref: str,
) -> bool:
    cursor = db.execute(
        "DELETE FROM workflow_locks WHERE target_kind = ? AND target_ref = ?",
        (target_kind, target_ref),
    )
    db.commit()
    return cursor.rowcount > 0


def get_lock(
    db: ThreadSafeConnection,
    *,
    target_kind: str,
    target_ref: str,
) -> dict[str, Any] | None:
    row = db.execute_fetchone(
        "SELECT * FROM workflow_locks WHERE target_kind = ? AND target_ref = ?",
        (target_kind, target_ref),
    )
    return dict(row) if row else None
