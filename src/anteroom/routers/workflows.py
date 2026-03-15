"""Read-only REST API for workflow monitoring.

V1: no start/resume/cancel/approve via web API. CLI owns execution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(tags=["workflows"])


def _get_db(request: Request) -> Any:
    if hasattr(request.app.state, "db_manager"):
        db_name = request.query_params.get("db", "personal")
        return request.app.state.db_manager.get(db_name)
    return request.app.state.db


@router.get("/workflows")
async def list_workflows(request: Request) -> list[dict[str, Any]]:
    """List available workflow definitions."""
    definitions = []
    seen_ids: set[str] = set()
    # Built-in workflows
    builtin_dir = Path(__file__).parent.parent / "workflows"
    if builtin_dir.exists():
        for f in sorted(builtin_dir.glob("*.yaml")):
            definitions.append({"id": f.stem, "source": "built_in"})
            seen_ids.add(f.stem)
    # Package-shipped examples
    pkg_examples = Path(__file__).parent.parent / "workflows" / "examples"
    if pkg_examples.exists():
        for f in sorted(pkg_examples.glob("*.yaml")):
            if f.stem not in seen_ids:
                definitions.append({"id": f.stem, "source": "example"})
                seen_ids.add(f.stem)
    # Source-tree examples (development)
    src_examples = Path(__file__).parent.parent.parent.parent / "examples" / "workflows"
    if src_examples.exists():
        for f in sorted(src_examples.glob("*.yaml")):
            if f.stem not in seen_ids:
                definitions.append({"id": f.stem, "source": "example"})
                seen_ids.add(f.stem)
    return definitions


@router.get("/workflow-runs")
async def list_workflow_runs(
    request: Request,
    status: str | None = Query(default=None),
    workflow_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """List workflow runs with optional filters."""
    from ..services.workflow_storage import list_workflow_runs as ws_list

    db = _get_db(request)
    runs = ws_list(db, status=status, workflow_id=workflow_id, limit=limit, offset=offset)
    return runs


@router.get("/workflow-runs/{run_id}")
async def get_workflow_run(request: Request, run_id: str) -> dict[str, Any]:
    """Get detailed status of a workflow run including step history."""
    from ..services.workflow_storage import (
        get_pending_approval,
        list_workflow_steps,
    )
    from ..services.workflow_storage import (
        get_workflow_run as ws_get,
    )

    db = _get_db(request)
    run = ws_get(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    steps = list_workflow_steps(db, run_id)
    pending = get_pending_approval(db, run_id)

    return {
        **run,
        "steps": steps,
        "pending_approval": pending,
    }


@router.get("/workflow-runs/{run_id}/events")
async def get_workflow_events(request: Request, run_id: str) -> list[dict[str, Any]]:
    """Get durable event history for a workflow run."""
    from ..services.workflow_storage import get_workflow_run as ws_get
    from ..services.workflow_storage import list_workflow_events

    db = _get_db(request)
    run = ws_get(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    return list_workflow_events(db, run_id)
