"""Tests for workflow router endpoints."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from anteroom.db import init_db
from anteroom.services.workflow_storage import (
    create_workflow_event,
    create_workflow_run,
    create_workflow_step,
)


@pytest.fixture()
def test_app():
    """Create a minimal FastAPI app with the workflow router for testing."""
    from fastapi import FastAPI

    from anteroom.routers.workflows import router

    with tempfile.TemporaryDirectory() as td:
        db = init_db(Path(td) / "test.db")

        app = FastAPI()
        app.state.db = db
        app.include_router(router, prefix="/api")

        yield app, db
        db.close()


@pytest.fixture()
def client(test_app: tuple) -> TestClient:
    app, _ = test_app
    return TestClient(app)


@pytest.fixture()
def db(test_app: tuple) -> Any:
    _, db = test_app
    return db


class TestListWorkflows:
    def test_returns_list(self, client: TestClient) -> None:
        resp = client.get("/api/workflows")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestListWorkflowRuns:
    def test_empty_list(self, client: TestClient) -> None:
        resp = client.get("/api/workflow-runs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_with_runs(self, client: TestClient, db: Any) -> None:
        create_workflow_run(
            db,
            workflow_id="test",
            workflow_version="0.1.0",
            target_kind="task",
            target_ref="t1",
        )
        resp = client.get("/api/workflow-runs")
        assert resp.status_code == 200
        runs = resp.json()
        assert len(runs) == 1
        assert runs[0]["workflow_id"] == "test"

    def test_filter_by_status(self, client: TestClient, db: Any) -> None:
        run = create_workflow_run(
            db,
            workflow_id="test",
            workflow_version="0.1.0",
            target_kind="task",
            target_ref="t1",
        )
        from anteroom.services.workflow_storage import update_workflow_run

        update_workflow_run(db, run["id"], status="completed")

        resp = client.get("/api/workflow-runs?status=completed")
        assert len(resp.json()) == 1
        resp2 = client.get("/api/workflow-runs?status=running")
        assert len(resp2.json()) == 0


class TestGetWorkflowRun:
    def test_returns_run_with_steps(self, client: TestClient, db: Any) -> None:
        run = create_workflow_run(
            db,
            workflow_id="test",
            workflow_version="0.1.0",
            target_kind="task",
            target_ref="t1",
        )
        create_workflow_step(
            db,
            run_id=run["id"],
            step_id="s1",
            step_type="runner",
        )
        resp = client.get(f"/api/workflow-runs/{run['id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["workflow_id"] == "test"
        assert len(data["steps"]) == 1
        assert data["pending_approval"] is None

    def test_404_on_missing(self, client: TestClient) -> None:
        resp = client.get("/api/workflow-runs/nonexistent")
        assert resp.status_code == 404


class TestGetWorkflowEvents:
    def test_returns_events(self, client: TestClient, db: Any) -> None:
        run = create_workflow_run(
            db,
            workflow_id="test",
            workflow_version="0.1.0",
            target_kind="task",
            target_ref="t1",
        )
        create_workflow_event(
            db,
            run_id=run["id"],
            event_type="run_started",
            payload={"workflow_id": "test"},
        )
        resp = client.get(f"/api/workflow-runs/{run['id']}/events")
        assert resp.status_code == 200
        events = resp.json()
        assert len(events) == 1
        assert events[0]["event_type"] == "run_started"

    def test_404_on_missing_run(self, client: TestClient) -> None:
        resp = client.get("/api/workflow-runs/nonexistent/events")
        assert resp.status_code == 404
