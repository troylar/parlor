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
            mock_store.list_artifacts.return_value = [{"fqn": "@a/skill/x", "type": "skill"}]
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
