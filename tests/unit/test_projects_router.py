"""Tests for routers/projects.py (#689)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.routers.projects import router


def _make_app() -> tuple[FastAPI, MagicMock]:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    mock_db = MagicMock()
    app.state.db = mock_db
    app.state.config = MagicMock()
    app.state.config.identity = None
    return app, mock_db


class TestListProjects:
    def test_returns_projects(self) -> None:
        app, mock_db = _make_app()
        with patch("anteroom.routers.projects.storage") as mock_storage:
            mock_storage.list_projects.return_value = [{"id": "p1", "name": "My Project"}]
            client = TestClient(app)
            resp = client.get("/api/projects")
        assert resp.status_code == 200
        assert resp.json() == [{"id": "p1", "name": "My Project"}]


class TestCreateProject:
    def test_creates_project(self) -> None:
        app, mock_db = _make_app()
        with patch("anteroom.routers.projects.storage") as mock_storage:
            mock_storage.create_project.return_value = {"id": "p1", "name": "New"}
            client = TestClient(app)
            resp = client.post("/api/projects", json={"name": "New"})
        assert resp.status_code == 201

    def test_with_identity(self) -> None:
        app, mock_db = _make_app()
        identity = MagicMock()
        identity.user_id = "u1"
        identity.display_name = "Alice"
        app.state.config.identity = identity
        with patch("anteroom.routers.projects.storage") as mock_storage:
            mock_storage.create_project.return_value = {"id": "p1", "name": "New"}
            client = TestClient(app)
            resp = client.post("/api/projects", json={"name": "New"})
        assert resp.status_code == 201
        call_kwargs = mock_storage.create_project.call_args[1]
        assert call_kwargs["user_id"] == "u1"

    def test_empty_name_rejected(self) -> None:
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.post("/api/projects", json={"name": ""})
        assert resp.status_code == 422


class TestGetProject:
    def test_found(self) -> None:
        app, mock_db = _make_app()
        pid = str(uuid.uuid4())
        with patch("anteroom.routers.projects.storage") as mock_storage:
            mock_storage.get_project.return_value = {"id": pid, "name": "Proj"}
            client = TestClient(app)
            resp = client.get(f"/api/projects/{pid}")
        assert resp.status_code == 200

    def test_not_found(self) -> None:
        app, mock_db = _make_app()
        pid = str(uuid.uuid4())
        with patch("anteroom.routers.projects.storage") as mock_storage:
            mock_storage.get_project.return_value = None
            client = TestClient(app)
            resp = client.get(f"/api/projects/{pid}")
        assert resp.status_code == 404

    def test_invalid_uuid(self) -> None:
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.get("/api/projects/not-a-uuid")
        assert resp.status_code == 400
        assert "Invalid ID" in resp.json()["detail"]


class TestUpdateProject:
    def test_update_name(self) -> None:
        app, mock_db = _make_app()
        pid = str(uuid.uuid4())
        with patch("anteroom.routers.projects.storage") as mock_storage:
            mock_storage.update_project.return_value = {"id": pid, "name": "Renamed"}
            client = TestClient(app)
            resp = client.patch(f"/api/projects/{pid}", json={"name": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    def test_not_found(self) -> None:
        app, mock_db = _make_app()
        pid = str(uuid.uuid4())
        with patch("anteroom.routers.projects.storage") as mock_storage:
            mock_storage.update_project.return_value = None
            client = TestClient(app)
            resp = client.patch(f"/api/projects/{pid}", json={"name": "X"})
        assert resp.status_code == 404


class TestDeleteProject:
    def test_deletes(self) -> None:
        app, mock_db = _make_app()
        pid = str(uuid.uuid4())
        with patch("anteroom.routers.projects.storage") as mock_storage:
            mock_storage.delete_project.return_value = True
            client = TestClient(app)
            resp = client.delete(f"/api/projects/{pid}")
        assert resp.status_code == 204

    def test_not_found(self) -> None:
        app, mock_db = _make_app()
        pid = str(uuid.uuid4())
        with patch("anteroom.routers.projects.storage") as mock_storage:
            mock_storage.delete_project.return_value = False
            client = TestClient(app)
            resp = client.delete(f"/api/projects/{pid}")
        assert resp.status_code == 404

    def test_invalid_uuid(self) -> None:
        app, _ = _make_app()
        client = TestClient(app)
        resp = client.delete("/api/projects/bad")
        assert resp.status_code == 400
