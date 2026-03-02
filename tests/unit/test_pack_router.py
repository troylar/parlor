"""Tests for the packs API router."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from anteroom.config import PackSourceConfig
from anteroom.routers.packs import (
    get_pack_by_id,
    remove_pack_by_id,
    router,
)
from anteroom.services.pack_refresh import SourceRefreshResult
from anteroom.services.pack_sources import CachedSource


def _make_app(pack_sources: list | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.db = MagicMock()
    config = MagicMock()
    config.pack_sources = pack_sources or []
    config.app.data_dir = Path("/tmp/test-data")
    app.state.config = config
    return app


class TestListPacksEndpoint:
    def test_list_empty(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.list_packs.return_value = []
            client = TestClient(app)
            resp = client.get("/api/packs")
            assert resp.status_code == 200
            assert resp.json() == []

    def test_list_with_packs(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.list_packs.return_value = [
                {
                    "namespace": "test-ns",
                    "name": "test-pack",
                    "version": "1.0.0",
                    "artifact_count": 3,
                    "source_path": "/secret/path/to/pack",
                }
            ]
            client = TestClient(app)
            resp = client.get("/api/packs")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["name"] == "test-pack"
            assert data[0]["artifact_count"] == 3
            assert "source_path" not in data[0]

    def test_list_calls_service_with_db(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.list_packs.return_value = []
            client = TestClient(app)
            client.get("/api/packs")
            mock_packs.list_packs.assert_called_once_with(app.state.db)


class TestGetPackEndpoint:
    def test_get_existing(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.resolve_pack.return_value = (
                {"id": "pack-1", "namespace": "test-ns", "name": "test-pack", "version": "1.0.0"},
                [],
            )
            mock_packs.get_pack_by_id.return_value = {
                "id": "pack-1",
                "namespace": "test-ns",
                "name": "test-pack",
                "version": "1.0.0",
                "source_path": "/secret/internal/path",
                "artifacts": [
                    {
                        "fqn": "@test-ns/skill/greet",
                        "type": "skill",
                        "content_hash": "abc123",
                        "content": "sensitive system instructions",
                    },
                ],
            }
            client = TestClient(app)
            resp = client.get("/api/packs/test-ns/test-pack")
            assert resp.status_code == 200
            data = resp.json()
            assert data["name"] == "test-pack"
            assert len(data["artifacts"]) == 1
            assert "source_path" not in data
            assert "content" not in data["artifacts"][0]

    def test_get_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.resolve_pack.return_value = (None, [])
            client = TestClient(app)
            resp = client.get("/api/packs/no/such-pack")
            assert resp.status_code == 404

    def test_get_calls_service_with_correct_args(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.resolve_pack.return_value = (
                {"id": "p1", "name": "p", "namespace": "n"},
                [],
            )
            mock_packs.get_pack_by_id.return_value = {"id": "p1", "name": "p", "namespace": "n", "artifacts": []}
            client = TestClient(app)
            client.get("/api/packs/my-ns/my-pack")
            mock_packs.resolve_pack.assert_called_once_with(app.state.db, "my-ns", "my-pack")
            mock_packs.get_pack_by_id.assert_called_once_with(app.state.db, "p1")

    def test_get_not_found_does_not_reflect_input(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.resolve_pack.return_value = (None, [])
            client = TestClient(app)
            resp = client.get("/api/packs/evil-ns/evil-name")
            assert resp.status_code == 404
            assert "evil-ns" not in resp.json()["detail"]
            assert "evil-name" not in resp.json()["detail"]

    def test_get_ambiguous_returns_409(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.resolve_pack.return_value = (
                None,
                [{"id": "p1", "version": "1.0"}, {"id": "p2", "version": "2.0"}],
            )
            client = TestClient(app)
            resp = client.get("/api/packs/test-ns/test-pack")
            assert resp.status_code == 409


class TestListSourcesEndpoint:
    def test_list_sources_empty(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/packs/sources")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_sources_with_cached(self) -> None:
        sources = [PackSourceConfig(url="https://example.com/packs.git", branch="main", refresh_interval=30)]
        app = _make_app(pack_sources=sources)
        cached = [
            CachedSource(
                url="https://example.com/packs.git",
                branch="main",
                path=Path("/tmp/cache"),
                ref="abc123def456",
            )
        ]
        with patch("anteroom.routers.packs.list_cached_sources", return_value=cached):
            client = TestClient(app)
            resp = client.get("/api/packs/sources")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["url"] == "https://example.com/packs.git"
        assert data[0]["cached"] is True
        assert data[0]["ref"] == "abc123def456"

    def test_list_sources_not_cached(self) -> None:
        sources = [PackSourceConfig(url="https://example.com/packs.git")]
        app = _make_app(pack_sources=sources)
        with patch("anteroom.routers.packs.list_cached_sources", return_value=[]):
            client = TestClient(app)
            resp = client.get("/api/packs/sources")

        data = resp.json()
        assert len(data) == 1
        assert data[0]["cached"] is False
        assert data[0]["ref"] is None


class TestAttachPackEndpoint:
    def test_attach_success(self) -> None:
        app = _make_app()
        with (
            patch("anteroom.routers.packs.packs") as mock_packs,
            patch("anteroom.services.pack_attachments.attach_pack", return_value={"id": "att-1", "scope": "global"}),
        ):
            mock_packs.resolve_pack.return_value = ({"id": "pack-1"}, [])
            client = TestClient(app)
            resp = client.post("/api/packs/test-ns/test-pack/attach", json={})
            assert resp.status_code == 200
            assert resp.json()["scope"] == "global"

    def test_attach_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.resolve_pack.return_value = (None, [])
            client = TestClient(app)
            resp = client.post("/api/packs/test-ns/test-pack/attach", json={})
            assert resp.status_code == 404

    def test_attach_duplicate_returns_409(self) -> None:
        app = _make_app()
        with (
            patch("anteroom.routers.packs.packs") as mock_packs,
            patch("anteroom.services.pack_attachments.attach_pack", side_effect=ValueError("already attached")),
        ):
            mock_packs.resolve_pack.return_value = ({"id": "pack-1"}, [])
            client = TestClient(app)
            resp = client.post("/api/packs/test-ns/test-pack/attach", json={})
            assert resp.status_code == 409

    def test_attach_with_project_path(self) -> None:
        app = _make_app()
        with (
            patch("anteroom.routers.packs.packs") as mock_packs,
            patch(
                "anteroom.services.pack_attachments.attach_pack",
                return_value={"id": "att-1", "scope": "project"},
            ) as mock_attach,
        ):
            mock_packs.resolve_pack.return_value = ({"id": "pack-1"}, [])
            client = TestClient(app)
            resp = client.post("/api/packs/test-ns/test-pack/attach", json={"project_path": "/my/proj"})
            assert resp.status_code == 200
            mock_attach.assert_called_once_with(app.state.db, "pack-1", project_path="/my/proj")


class TestDetachPackEndpoint:
    def test_detach_success(self) -> None:
        app = _make_app()
        with (
            patch("anteroom.routers.packs.packs") as mock_packs,
            patch("anteroom.services.pack_attachments.detach_pack", return_value=True),
        ):
            mock_packs.resolve_pack.return_value = ({"id": "pack-1"}, [])
            client = TestClient(app)
            resp = client.delete("/api/packs/test-ns/test-pack/attach")
            assert resp.status_code == 200
            assert resp.json()["status"] == "detached"

    def test_detach_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.resolve_pack.return_value = (None, [])
            client = TestClient(app)
            resp = client.delete("/api/packs/test-ns/test-pack/attach")
            assert resp.status_code == 404

    def test_detach_not_attached(self) -> None:
        app = _make_app()
        with (
            patch("anteroom.routers.packs.packs") as mock_packs,
            patch("anteroom.services.pack_attachments.detach_pack", return_value=False),
        ):
            mock_packs.resolve_pack.return_value = ({"id": "pack-1"}, [])
            client = TestClient(app)
            resp = client.delete("/api/packs/test-ns/test-pack/attach")
            assert resp.status_code == 404


class TestRemovePackEndpoint:
    def test_remove_success(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.resolve_pack.return_value = ({"id": "pack-1"}, [])
            mock_packs.remove_pack_by_id.return_value = True
            client = TestClient(app)
            resp = client.delete("/api/packs/test-ns/test-pack")
            assert resp.status_code == 200
            assert resp.json()["status"] == "removed"

    def test_remove_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.resolve_pack.return_value = (None, [])
            client = TestClient(app)
            resp = client.delete("/api/packs/test-ns/test-pack")
            assert resp.status_code == 404


class TestListPackAttachmentsEndpoint:
    def test_list_attachments(self) -> None:
        app = _make_app()
        with (
            patch("anteroom.routers.packs.packs") as mock_packs,
            patch("anteroom.services.pack_attachments.list_attachments_for_pack", return_value=[]),
        ):
            mock_packs.resolve_pack.return_value = ({"id": "pack-1"}, [])
            client = TestClient(app)
            resp = client.get("/api/packs/test-ns/test-pack/attachments")
            assert resp.status_code == 200
            assert resp.json() == []

    def test_list_attachments_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.resolve_pack.return_value = (None, [])
            client = TestClient(app)
            resp = client.get("/api/packs/test-ns/test-pack/attachments")
            assert resp.status_code == 404


class TestValidatePackPathParams:
    def test_invalid_namespace_returns_400(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.resolve_pack.return_value = (None, [])
            client = TestClient(app)
            resp = client.get("/api/packs/INVALID-NS/test-pack")
            assert resp.status_code == 400
            assert "Invalid namespace" in resp.json()["detail"]

    def test_invalid_name_returns_400(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.resolve_pack.return_value = (None, [])
            client = TestClient(app)
            resp = client.get("/api/packs/valid-ns/INVALID-NAME")
            assert resp.status_code == 400
            assert "Invalid pack name" in resp.json()["detail"]

    def test_namespace_with_special_chars_returns_400(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/packs/has$pecial/valid-name")
        assert resp.status_code == 400
        assert "Invalid namespace" in resp.json()["detail"]

    def test_valid_namespace_and_name_pass_validation(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.resolve_pack.return_value = (None, [])
            client = TestClient(app)
            resp = client.get("/api/packs/valid-ns/valid-name")
            assert resp.status_code == 404


class TestRemovePackEndpointExtended:
    def test_remove_when_service_returns_false(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.resolve_pack.return_value = ({"id": "pack-1"}, [])
            mock_packs.remove_pack_by_id.return_value = False
            client = TestClient(app)
            resp = client.delete("/api/packs/test-ns/test-pack")
            assert resp.status_code == 404
            assert resp.json()["detail"] == "Pack not found"


class TestGetPackEndpointExtended:
    def test_get_pack_by_id_after_resolve_returns_none(self) -> None:
        """get_pack_by_id returns None after resolve succeeds."""
        app = _make_app()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.resolve_pack.return_value = ({"id": "pack-1"}, [])
            mock_packs.get_pack_by_id.return_value = None
            client = TestClient(app)
            resp = client.get("/api/packs/test-ns/test-pack")
            assert resp.status_code == 404
            assert resp.json()["detail"] == "Pack not found"


class TestGetPackByIdEndpoint:
    """Tests for get_pack_by_id handler called directly (route is shadowed by {namespace}/{name})."""

    @pytest.mark.asyncio
    async def test_get_by_valid_id(self) -> None:
        request = MagicMock()
        request.app.state.db = MagicMock()
        pack_data = {
            "id": "abcdef01-1234-5678-9abc-def012345678",
            "namespace": "test-ns",
            "name": "test-pack",
            "version": "1.0.0",
            "source_path": "/secret/internal/path",
            "artifacts": [
                {
                    "fqn": "@test-ns/skill/greet",
                    "type": "skill",
                    "content": "secret instructions",
                }
            ],
        }
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.get_pack_by_id.return_value = pack_data
            result = await get_pack_by_id(request, "abcdef01-1234-5678-9abc-def012345678")
        assert result["name"] == "test-pack"
        assert "source_path" not in result
        assert "content" not in result["artifacts"][0]

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self) -> None:
        request = MagicMock()
        request.app.state.db = MagicMock()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.get_pack_by_id.return_value = None
            with pytest.raises(HTTPException) as exc_info:
                await get_pack_by_id(request, "abcdef01-1234-5678-9abc-def012345678")
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Pack not found"

    @pytest.mark.asyncio
    async def test_get_by_id_invalid_format_returns_400(self) -> None:
        request = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            await get_pack_by_id(request, "not-a-valid-uuid!!!")
        assert exc_info.value.status_code == 400
        assert "Invalid pack ID format" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_by_id_strips_source_path(self) -> None:
        request = MagicMock()
        request.app.state.db = MagicMock()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.get_pack_by_id.return_value = {
                "id": "abcdef01-1234-5678-9abc-def012345678",
                "namespace": "ns",
                "name": "p",
                "source_path": "/hidden/path",
                "artifacts": [],
            }
            result = await get_pack_by_id(request, "abcdef01-1234-5678-9abc-def012345678")
        assert "source_path" not in result

    @pytest.mark.asyncio
    async def test_get_by_id_calls_service_with_correct_id(self) -> None:
        request = MagicMock()
        request.app.state.db = MagicMock()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.get_pack_by_id.return_value = {"id": "abcd1234-abcd-abcd-abcd-abcd12345678", "artifacts": []}
            await get_pack_by_id(request, "abcd1234-abcd-abcd-abcd-abcd12345678")
        mock_packs.get_pack_by_id.assert_called_once_with(request.app.state.db, "abcd1234-abcd-abcd-abcd-abcd12345678")


class TestRemovePackByIdEndpoint:
    """Tests for remove_pack_by_id handler called directly (route is shadowed by {namespace}/{name})."""

    @pytest.mark.asyncio
    async def test_remove_by_valid_id(self) -> None:
        request = MagicMock()
        request.app.state.db = MagicMock()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.remove_pack_by_id.return_value = True
            result = await remove_pack_by_id(request, "abcdef01-1234-5678-9abc-def012345678")
        assert result["status"] == "removed"

    @pytest.mark.asyncio
    async def test_remove_by_id_not_found(self) -> None:
        request = MagicMock()
        request.app.state.db = MagicMock()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.remove_pack_by_id.return_value = False
            with pytest.raises(HTTPException) as exc_info:
                await remove_pack_by_id(request, "abcdef01-1234-5678-9abc-def012345678")
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Pack not found"

    @pytest.mark.asyncio
    async def test_remove_by_id_invalid_format_returns_400(self) -> None:
        request = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            await remove_pack_by_id(request, "not-a-valid-id!!!")
        assert exc_info.value.status_code == 400
        assert "Invalid pack ID format" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_remove_by_id_calls_service_with_correct_id(self) -> None:
        request = MagicMock()
        request.app.state.db = MagicMock()
        with patch("anteroom.routers.packs.packs") as mock_packs:
            mock_packs.remove_pack_by_id.return_value = True
            await remove_pack_by_id(request, "abcd1234-abcd-abcd-abcd-abcd12345678")
        mock_packs.remove_pack_by_id.assert_called_once_with(
            request.app.state.db, "abcd1234-abcd-abcd-abcd-abcd12345678"
        )


class TestRefreshSourcesEndpoint:
    def test_refresh_no_sources(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/packs/refresh")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_refresh_with_sources(self) -> None:
        sources = [PackSourceConfig(url="https://example.com/packs.git")]
        app = _make_app(pack_sources=sources)

        mock_worker = MagicMock()
        mock_worker.refresh_all.return_value = [
            SourceRefreshResult(url="https://example.com/packs.git", success=True, packs_installed=2, changed=True),
        ]

        with patch("anteroom.services.pack_refresh.PackRefreshWorker", return_value=mock_worker):
            client = TestClient(app)
            resp = client.post("/api/packs/refresh")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["success"] is True
        assert data[0]["packs_installed"] == 2
        assert data[0]["changed"] is True
