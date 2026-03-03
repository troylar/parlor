"""Tests for the artifacts API router."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.routers.artifacts import router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.db = MagicMock()
    return app


class TestListArtifactsEndpoint:
    def test_list_empty(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.artifacts.artifact_storage") as mock_store:
            mock_store.list_artifacts.return_value = []
            client = TestClient(app)
            resp = client.get("/api/artifacts")
            assert resp.status_code == 200
            assert resp.json() == []

    def test_list_with_type_filter(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.artifacts.artifact_storage") as mock_store:
            mock_store.list_artifacts.return_value = [{"id": "a1", "fqn": "@a/skill/x", "type": "skill"}]
            mock_store.list_artifact_versions.return_value = [{"version": 1}]
            client = TestClient(app)
            resp = client.get("/api/artifacts?type=skill")
            assert resp.status_code == 200
            assert len(resp.json()) == 1
            mock_store.list_artifacts.assert_called_once()
            call_kwargs = mock_store.list_artifacts.call_args[1]
            assert call_kwargs["artifact_type"] == "skill"

    def test_list_with_namespace_filter(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.artifacts.artifact_storage") as mock_store:
            mock_store.list_artifacts.return_value = []
            client = TestClient(app)
            resp = client.get("/api/artifacts?namespace=core")
            assert resp.status_code == 200
            mock_store.list_artifacts.assert_called_once()
            call_kwargs = mock_store.list_artifacts.call_args[1]
            assert call_kwargs["namespace"] == "core"

    def test_invalid_type_rejected(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/artifacts?type=bogus")
        assert resp.status_code == 422


class TestGetArtifactEndpoint:
    def test_get_existing(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.artifacts.artifact_storage") as mock_store:
            mock_store.get_artifact_by_fqn.return_value = {
                "id": "a1",
                "fqn": "@core/skill/greet",
                "type": "skill",
                "content": "hello",
            }
            mock_store.list_artifact_versions.return_value = [
                {"id": "v1", "version": 1, "content": "hello"},
            ]
            client = TestClient(app)
            resp = client.get("/api/artifacts/@core/skill/greet")
            assert resp.status_code == 200
            data = resp.json()
            assert data["fqn"] == "@core/skill/greet"
            assert len(data["versions"]) == 1

    def test_get_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.artifacts.artifact_storage") as mock_store:
            mock_store.get_artifact_by_fqn.return_value = None
            client = TestClient(app)
            resp = client.get("/api/artifacts/@no/such/thing")
            assert resp.status_code == 404

    def test_get_invalid_fqn_returns_400(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/artifacts/not-a-valid-fqn")
        assert resp.status_code == 400

    def test_get_error_does_not_reflect_fqn(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.artifacts.artifact_storage") as mock_store:
            mock_store.get_artifact_by_fqn.return_value = None
            client = TestClient(app)
            resp = client.get("/api/artifacts/@no/skill/thing")
            assert resp.status_code == 404
            assert "@no/skill/thing" not in resp.json()["detail"]


class TestDeleteArtifactEndpoint:
    def test_delete_artifact_success(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.artifacts.artifact_storage") as mock_store:
            mock_store.get_artifact_by_fqn.return_value = {"id": "a1", "fqn": "@ns/skill/greet"}
            mock_store.delete_artifact.return_value = True
            client = TestClient(app)
            resp = client.delete("/api/artifacts/@ns/skill/greet")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "deleted"
            assert data["fqn"] == "@ns/skill/greet"
            mock_store.delete_artifact.assert_called_once_with(app.state.db, "a1")

    def test_delete_artifact_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.artifacts.artifact_storage") as mock_store:
            mock_store.get_artifact_by_fqn.return_value = None
            client = TestClient(app)
            resp = client.delete("/api/artifacts/@ns/skill/gone")
            assert resp.status_code == 404

    def test_delete_artifact_invalid_fqn(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.delete("/api/artifacts/bad-fqn")
        assert resp.status_code == 400

    def test_delete_built_in_artifact_blocked(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.artifacts.artifact_storage") as mock_store:
            mock_store.get_artifact_by_fqn.return_value = {
                "id": "a1",
                "fqn": "@core/skill/builtin",
                "source": "built_in",
            }
            client = TestClient(app)
            resp = client.delete("/api/artifacts/@core/skill/builtin")
            assert resp.status_code == 403
            assert "built-in" in resp.json()["detail"].lower()
            mock_store.delete_artifact.assert_not_called()


class TestListArtifactsVersion:
    def test_list_includes_version(self) -> None:
        app = _make_app()
        # The batch query calls db.execute(...).fetchall() directly
        app.state.db.execute.return_value.fetchall.return_value = [
            ("a1", 3),
        ]
        with patch("anteroom.routers.artifacts.artifact_storage") as mock_store:
            mock_store.list_artifacts.return_value = [
                {"id": "a1", "fqn": "@ns/skill/x", "type": "skill", "content": "hello"},
            ]
            client = TestClient(app)
            resp = client.get("/api/artifacts")
            assert resp.status_code == 200
            data = resp.json()
            assert data[0]["version"] == 3
            assert "content" not in data[0]

    def test_list_version_none_when_no_versions(self) -> None:
        app = _make_app()
        # Empty fetchall = no versions in DB
        app.state.db.execute.return_value.fetchall.return_value = []
        with patch("anteroom.routers.artifacts.artifact_storage") as mock_store:
            mock_store.list_artifacts.return_value = [
                {"id": "a1", "fqn": "@ns/skill/x", "type": "skill", "content": "hello"},
            ]
            client = TestClient(app)
            resp = client.get("/api/artifacts")
            assert resp.status_code == 200
            data = resp.json()
            assert data[0]["version"] is None


class TestGetArtifactVersion:
    def test_get_includes_version(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.artifacts.artifact_storage") as mock_store:
            mock_store.get_artifact_by_fqn.return_value = {
                "id": "a1",
                "fqn": "@core/skill/greet",
                "type": "skill",
                "content": "hello",
            }
            mock_store.list_artifact_versions.return_value = [
                {"id": "v2", "version": 2, "content": "hello"},
                {"id": "v1", "version": 1, "content": "old"},
            ]
            client = TestClient(app)
            resp = client.get("/api/artifacts/@core/skill/greet")
            assert resp.status_code == 200
            data = resp.json()
            assert data["version"] == 2
