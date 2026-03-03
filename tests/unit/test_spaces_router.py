"""Tests for the spaces API router."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.routers.spaces import SpaceCreateRequest, SpaceSourceLinkRequest, SpaceUpdateRequest, router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.db = MagicMock()
    return app


class TestSpaceCreateRequestValidation:
    def test_valid_name_alphanumeric(self) -> None:
        req = SpaceCreateRequest(name="myspace", source_file="/tmp/space.yaml")
        assert req.name == "myspace"

    def test_valid_name_with_hyphens_and_underscores(self) -> None:
        req = SpaceCreateRequest(name="my-space_01", source_file="/tmp/space.yaml")
        assert req.name == "my-space_01"

    def test_valid_name_max_length(self) -> None:
        name = "a" + "b" * 63
        req = SpaceCreateRequest(name=name, source_file="/tmp/space.yaml")
        assert req.name == name

    def test_invalid_name_starts_with_hyphen(self) -> None:
        import pytest

        with pytest.raises(Exception):
            SpaceCreateRequest(name="-bad", source_file="/tmp/space.yaml")

    def test_invalid_name_starts_with_underscore(self) -> None:
        import pytest

        with pytest.raises(Exception):
            SpaceCreateRequest(name="_bad", source_file="/tmp/space.yaml")

    def test_invalid_name_too_long(self) -> None:
        import pytest

        with pytest.raises(Exception):
            SpaceCreateRequest(name="a" * 65, source_file="/tmp/space.yaml")

    def test_invalid_name_empty(self) -> None:
        import pytest

        with pytest.raises(Exception):
            SpaceCreateRequest(name="", source_file="/tmp/space.yaml")

    def test_invalid_name_with_space(self) -> None:
        import pytest

        with pytest.raises(Exception):
            SpaceCreateRequest(name="has space", source_file="/tmp/space.yaml")

    def test_invalid_name_with_slash(self) -> None:
        import pytest

        with pytest.raises(Exception):
            SpaceCreateRequest(name="has/slash", source_file="/tmp/space.yaml")

    def test_path_traversal_rejected(self) -> None:
        import pytest

        with pytest.raises(Exception):
            SpaceCreateRequest(name="myspace", source_file="/tmp/../etc/passwd")

    def test_valid_source_file_with_subdirs(self) -> None:
        req = SpaceCreateRequest(name="myspace", source_file="/home/user/spaces/work.yaml")
        assert req.source_file == "/home/user/spaces/work.yaml"

    def test_default_source_hash_empty(self) -> None:
        req = SpaceCreateRequest(name="myspace", source_file="/tmp/space.yaml")
        assert req.source_hash == ""

    def test_explicit_source_hash(self) -> None:
        req = SpaceCreateRequest(name="myspace", source_file="/tmp/space.yaml", source_hash="abc123")
        assert req.source_hash == "abc123"


class TestSpaceSourceLinkRequestValidation:
    def test_all_none_by_default(self) -> None:
        req = SpaceSourceLinkRequest()
        assert req.source_id is None
        assert req.group_id is None
        assert req.tag_filter is None

    def test_with_source_id(self) -> None:
        req = SpaceSourceLinkRequest(source_id="src-1")
        assert req.source_id == "src-1"

    def test_with_group_id(self) -> None:
        req = SpaceSourceLinkRequest(group_id="grp-1")
        assert req.group_id == "grp-1"

    def test_with_tag_filter(self) -> None:
        req = SpaceSourceLinkRequest(tag_filter="docs")
        assert req.tag_filter == "docs"


class TestListSpacesEndpoint:
    def test_list_empty(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.spaces.list_spaces", return_value=[]):
            client = TestClient(app)
            resp = client.get("/api/spaces")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_with_spaces(self) -> None:
        app = _make_app()
        spaces = [
            {"id": "sp-1", "name": "work", "source_file": "/tmp/work.yaml", "source_hash": "abc"},
            {"id": "sp-2", "name": "personal", "source_file": "/tmp/personal.yaml", "source_hash": "def"},
        ]
        with patch("anteroom.routers.spaces.list_spaces", return_value=spaces):
            client = TestClient(app)
            resp = client.get("/api/spaces")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "work"
        assert data[1]["name"] == "personal"

    def test_list_calls_service_with_db(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.spaces.list_spaces", return_value=[]) as mock_list:
            client = TestClient(app)
            client.get("/api/spaces")
        mock_list.assert_called_once_with(app.state.db)


class TestCreateSpaceEndpoint:
    def test_create_success(self) -> None:
        app = _make_app()
        created = {"id": "sp-1", "name": "myspace", "source_file": "/tmp/space.yaml", "source_hash": ""}
        with patch("anteroom.routers.spaces.db_create_space", return_value=created):
            client = TestClient(app)
            resp = client.post("/api/spaces", json={"name": "myspace", "source_file": "/tmp/space.yaml"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == "sp-1"
        assert data["name"] == "myspace"

    def test_create_with_source_hash(self) -> None:
        app = _make_app()
        created = {"id": "sp-1", "name": "myspace", "source_file": "/tmp/space.yaml", "source_hash": "sha256abc"}
        with patch("anteroom.routers.spaces.db_create_space", return_value=created) as mock_create:
            client = TestClient(app)
            resp = client.post(
                "/api/spaces",
                json={"name": "myspace", "source_file": "/tmp/space.yaml", "source_hash": "sha256abc"},
            )
        assert resp.status_code == 201
        mock_create.assert_called_once_with(
            app.state.db,
            name="myspace",
            instructions="",
            model=None,
            source_file="/tmp/space.yaml",
            source_hash="sha256abc",
        )

    def test_create_invalid_name_returns_422(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/spaces", json={"name": "-invalid", "source_file": "/tmp/space.yaml"})
        assert resp.status_code == 422

    def test_create_path_traversal_returns_422(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/spaces", json={"name": "myspace", "source_file": "/tmp/../etc/passwd"})
        assert resp.status_code == 422

    def test_create_missing_name_returns_422(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/spaces", json={"source_file": "/tmp/space.yaml"})
        assert resp.status_code == 422

    def test_create_empty_body_returns_422(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/spaces", json={})
        assert resp.status_code == 422


class TestGetSpaceEndpoint:
    def test_get_existing_space(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work", "source_file": "/tmp/work.yaml"}
        with patch("anteroom.routers.spaces.get_space", return_value=space):
            client = TestClient(app)
            resp = client.get("/api/spaces/sp-1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "sp-1"
        assert resp.json()["name"] == "work"

    def test_get_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.spaces.get_space", return_value=None):
            client = TestClient(app)
            resp = client.get("/api/spaces/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Space not found"

    def test_get_calls_service_with_correct_args(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.spaces.get_space", return_value={"id": "sp-1", "name": "work"}) as mock_get:
            client = TestClient(app)
            client.get("/api/spaces/sp-1")
        mock_get.assert_called_once_with(app.state.db, "sp-1")


class TestDeleteSpaceEndpoint:
    def test_delete_success(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.spaces.db_delete_space", return_value=True):
            client = TestClient(app)
            resp = client.delete("/api/spaces/sp-1")
        assert resp.status_code == 204

    def test_delete_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.spaces.db_delete_space", return_value=False):
            client = TestClient(app)
            resp = client.delete("/api/spaces/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Space not found"

    def test_delete_no_response_body_on_success(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.spaces.db_delete_space", return_value=True):
            client = TestClient(app)
            resp = client.delete("/api/spaces/sp-1")
        assert resp.status_code == 204
        assert resp.content == b""

    def test_delete_calls_service_with_correct_args(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.spaces.db_delete_space", return_value=True) as mock_delete:
            client = TestClient(app)
            client.delete("/api/spaces/sp-1")
        mock_delete.assert_called_once_with(app.state.db, "sp-1")


class TestGetSpacePathsEndpoint:
    def test_get_paths_success(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work"}
        paths = [{"id": "path-1", "space_id": "sp-1", "path": "/home/user/projects"}]
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.routers.spaces.get_space_paths", return_value=paths),
        ):
            client = TestClient(app)
            resp = client.get("/api/spaces/sp-1/paths")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["path"] == "/home/user/projects"

    def test_get_paths_empty(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work"}
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.routers.spaces.get_space_paths", return_value=[]),
        ):
            client = TestClient(app)
            resp = client.get("/api/spaces/sp-1/paths")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_paths_space_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.spaces.get_space", return_value=None):
            client = TestClient(app)
            resp = client.get("/api/spaces/nonexistent/paths")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Space not found"

    def test_get_paths_calls_service_with_correct_space_id(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work"}
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.routers.spaces.get_space_paths", return_value=[]) as mock_paths,
        ):
            client = TestClient(app)
            client.get("/api/spaces/sp-1/paths")
        mock_paths.assert_called_once_with(app.state.db, "sp-1")


class TestRefreshSpaceEndpoint:
    def test_refresh_space_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.spaces.get_space", return_value=None):
            client = TestClient(app)
            resp = client.post("/api/spaces/nonexistent/refresh")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Space not found"

    def test_refresh_no_source_file(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work", "source_file": ""}
        with patch("anteroom.routers.spaces.get_space", return_value=space):
            client = TestClient(app)
            resp = client.post("/api/spaces/sp-1/refresh")
        assert resp.status_code == 400
        assert "source file" in resp.json()["detail"]

    def test_refresh_file_not_on_disk(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work", "source_file": "/nonexistent/path/space.yaml"}
        with patch("anteroom.routers.spaces.get_space", return_value=space):
            client = TestClient(app)
            resp = client.post("/api/spaces/sp-1/refresh")
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"]

    def test_refresh_invalid_yaml(self, tmp_path) -> None:
        app = _make_app()
        bad_yaml = tmp_path / "space.yaml"
        bad_yaml.write_text("{{invalid yaml}}")
        space = {"id": "sp-1", "name": "work", "source_file": str(bad_yaml)}
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.services.spaces.parse_space_file", side_effect=Exception("parse error")),
        ):
            client = TestClient(app)
            resp = client.post("/api/spaces/sp-1/refresh")
        assert resp.status_code == 400
        assert "Invalid space file" in resp.json()["detail"]

    def test_refresh_success(self, tmp_path) -> None:
        app = _make_app()
        space_file = tmp_path / "space.yaml"
        space_file.write_text("name: work\n")
        space = {"id": "sp-1", "name": "work", "source_file": str(space_file)}
        updated_space = {"id": "sp-1", "name": "work", "source_file": str(space_file), "source_hash": "newhash123"}

        mock_cfg = MagicMock()
        mock_cfg.name = "work"
        mock_cfg.instructions = ""
        mock_cfg.config = {}

        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.services.spaces.parse_space_file", return_value=mock_cfg),
            patch("anteroom.services.spaces.compute_file_hash", return_value="newhash123"),
            patch("anteroom.routers.spaces.update_space", return_value=updated_space) as mock_update,
        ):
            client = TestClient(app)
            resp = client.post("/api/spaces/sp-1/refresh")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "sp-1"
        assert data["name"] == "work"
        assert data["source_hash"] == "newhash123"
        assert data["refreshed"] is True
        mock_update.assert_called_once_with(app.state.db, "sp-1", source_hash="newhash123", instructions="", model=None)

    def test_refresh_clears_model_when_removed_from_yaml(self, tmp_path) -> None:
        """Regression: removing model from YAML should clear it in the DB."""
        app = _make_app()
        space_file = tmp_path / "space.yaml"
        space_file.write_text("name: work\n")
        space = {"id": "sp-1", "name": "work", "source_file": str(space_file), "model": "gpt-4o"}
        updated_space = {
            "id": "sp-1",
            "name": "work",
            "source_file": str(space_file),
            "source_hash": "newhash",
            "model": None,
        }

        mock_cfg = MagicMock()
        mock_cfg.name = "work"
        mock_cfg.instructions = ""
        mock_cfg.config = {}  # No model in YAML

        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.services.spaces.parse_space_file", return_value=mock_cfg),
            patch("anteroom.services.spaces.compute_file_hash", return_value="newhash"),
            patch("anteroom.routers.spaces.update_space", return_value=updated_space) as mock_update,
        ):
            client = TestClient(app)
            resp = client.post("/api/spaces/sp-1/refresh")

        assert resp.status_code == 200
        # Model should be explicitly set to None (cleared), not omitted
        mock_update.assert_called_once_with(app.state.db, "sp-1", source_hash="newhash", instructions="", model=None)

    def test_refresh_sets_model_from_yaml(self, tmp_path) -> None:
        """Refresh should set model when present in YAML config."""
        app = _make_app()
        space_file = tmp_path / "space.yaml"
        space_file.write_text("name: work\nconfig:\n  model: gpt-4o\n")
        space = {"id": "sp-1", "name": "work", "source_file": str(space_file), "model": None}
        updated_space = {
            "id": "sp-1",
            "name": "work",
            "source_file": str(space_file),
            "source_hash": "newhash",
            "model": "gpt-4o",
        }

        mock_cfg = MagicMock()
        mock_cfg.name = "work"
        mock_cfg.instructions = ""
        mock_cfg.config = {"model": "gpt-4o"}

        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.services.spaces.parse_space_file", return_value=mock_cfg),
            patch("anteroom.services.spaces.compute_file_hash", return_value="newhash"),
            patch("anteroom.routers.spaces.update_space", return_value=updated_space) as mock_update,
        ):
            client = TestClient(app)
            resp = client.post("/api/spaces/sp-1/refresh")

        assert resp.status_code == 200
        mock_update.assert_called_once_with(
            app.state.db, "sp-1", source_hash="newhash", instructions="", model="gpt-4o"
        )


class TestGetSpaceSourcesEndpoint:
    def test_get_sources_success(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work"}
        sources = [{"id": "src-1", "space_id": "sp-1", "source_id": "s1"}]
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.routers.spaces.get_space_sources", return_value=sources),
        ):
            client = TestClient(app)
            resp = client.get("/api/spaces/sp-1/sources")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_get_sources_empty(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work"}
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.routers.spaces.get_space_sources", return_value=[]),
        ):
            client = TestClient(app)
            resp = client.get("/api/spaces/sp-1/sources")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_sources_space_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.spaces.get_space", return_value=None):
            client = TestClient(app)
            resp = client.get("/api/spaces/nonexistent/sources")
        assert resp.status_code == 404

    def test_get_sources_calls_with_correct_args(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work"}
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.routers.spaces.get_space_sources", return_value=[]) as mock_sources,
        ):
            client = TestClient(app)
            client.get("/api/spaces/sp-1/sources")
        mock_sources.assert_called_once_with(app.state.db, "sp-1")


class TestLinkSpaceSourceEndpoint:
    def test_link_source_success(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work"}
        link = {"id": "link-1", "space_id": "sp-1", "source_id": "src-1"}
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.routers.spaces.link_source_to_space", return_value=link),
        ):
            client = TestClient(app)
            resp = client.post("/api/spaces/sp-1/sources", json={"source_id": "src-1"})
        assert resp.status_code == 201
        assert resp.json()["source_id"] == "src-1"

    def test_link_group_success(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work"}
        link = {"id": "link-1", "space_id": "sp-1", "group_id": "grp-1"}
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.routers.spaces.link_source_to_space", return_value=link),
        ):
            client = TestClient(app)
            resp = client.post("/api/spaces/sp-1/sources", json={"group_id": "grp-1"})
        assert resp.status_code == 201

    def test_link_tag_filter_success(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work"}
        link = {"id": "link-1", "space_id": "sp-1", "tag_filter": "docs"}
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.routers.spaces.link_source_to_space", return_value=link),
        ):
            client = TestClient(app)
            resp = client.post("/api/spaces/sp-1/sources", json={"tag_filter": "docs"})
        assert resp.status_code == 201

    def test_link_space_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.spaces.get_space", return_value=None):
            client = TestClient(app)
            resp = client.post("/api/spaces/nonexistent/sources", json={"source_id": "src-1"})
        assert resp.status_code == 404

    def test_link_value_error_returns_400(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work"}
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.routers.spaces.link_source_to_space", side_effect=ValueError("source not found")),
        ):
            client = TestClient(app)
            resp = client.post("/api/spaces/sp-1/sources", json={"source_id": "bad-src"})
        assert resp.status_code == 400
        assert "source not found" in resp.json()["detail"]

    def test_link_calls_service_with_all_args(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work"}
        link = {"id": "link-1", "space_id": "sp-1", "source_id": "src-1"}
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.routers.spaces.link_source_to_space", return_value=link) as mock_link,
        ):
            client = TestClient(app)
            client.post("/api/spaces/sp-1/sources", json={"source_id": "src-1"})
        mock_link.assert_called_once_with(app.state.db, "sp-1", source_id="src-1", group_id=None, tag_filter=None)

    def test_link_empty_body_is_allowed(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work"}
        link = {"id": "link-1", "space_id": "sp-1"}
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.routers.spaces.link_source_to_space", return_value=link),
        ):
            client = TestClient(app)
            resp = client.post("/api/spaces/sp-1/sources", json={})
        assert resp.status_code == 201


class TestUnlinkSpaceSourceEndpoint:
    def test_unlink_success(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work"}
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.routers.spaces.unlink_source_from_space"),
        ):
            client = TestClient(app)
            resp = client.delete("/api/spaces/sp-1/sources/src-1")
        assert resp.status_code == 204
        assert resp.content == b""

    def test_unlink_space_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.spaces.get_space", return_value=None):
            client = TestClient(app)
            resp = client.delete("/api/spaces/nonexistent/sources/src-1")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Space not found"

    def test_unlink_calls_service_with_correct_args(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work"}
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.routers.spaces.unlink_source_from_space") as mock_unlink,
        ):
            client = TestClient(app)
            client.delete("/api/spaces/sp-1/sources/src-42")
        mock_unlink.assert_called_once_with(app.state.db, "sp-1", source_id="src-42")


class TestGetSpacePacksEndpoint:
    def test_get_packs_space_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.spaces.get_space", return_value=None):
            client = TestClient(app)
            resp = client.get("/api/spaces/nonexistent/packs")
        assert resp.status_code == 404

    def test_get_packs_empty(self) -> None:
        app = _make_app()
        space = {"id": "sp-1", "name": "work"}
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.services.pack_attachments.get_active_pack_ids_for_space", return_value=[]),
        ):
            client = TestClient(app)
            resp = client.get("/api/spaces/sp-1/packs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_packs_with_row_keys(self) -> None:
        """Test the hasattr(row, 'keys') branch using a sqlite3.Row-like MagicMock."""
        space = {"id": "sp-1", "name": "work"}
        pack_dict = {
            "id": "pack-1",
            "namespace": "test-ns",
            "name": "my-pack",
            "version": "1.0",
            "description": "A pack",
        }

        # Build a mock that has .keys() so dict(row) delegates through the keys branch
        mock_row = MagicMock()
        mock_row.keys.return_value = list(pack_dict.keys())
        # Make dict(mock_row) return the pack_dict via the mapping protocol
        mock_row.__iter__ = MagicMock(return_value=iter(pack_dict.items()))
        mock_row.keys.return_value = list(pack_dict.keys())
        mock_row.__getitem__ = MagicMock(side_effect=pack_dict.__getitem__)

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = mock_row
        mock_db.execute.return_value = mock_cursor

        app2 = FastAPI()
        app2.include_router(router, prefix="/api")
        app2.state.db = mock_db

        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.services.pack_attachments.get_active_pack_ids_for_space", return_value=["pack-1"]),
        ):
            client = TestClient(app2)
            resp = client.get("/api/spaces/sp-1/packs")
        assert resp.status_code == 200

    def test_get_packs_with_tuple_row(self) -> None:
        space = {"id": "sp-1", "name": "work"}

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        # A plain tuple-like row without .keys() attribute (sqlite3.Row behaves like this
        # when row_factory is not set)
        mock_row = ("pack-1", "test-ns", "my-pack", "1.0", "A pack")
        mock_cursor.fetchone.return_value = mock_row
        mock_db.execute.return_value = mock_cursor

        app2 = FastAPI()
        app2.include_router(router, prefix="/api")
        app2.state.db = mock_db

        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.services.pack_attachments.get_active_pack_ids_for_space", return_value=["pack-1"]),
        ):
            client = TestClient(app2)
            resp = client.get("/api/spaces/sp-1/packs")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "pack-1"
        assert data[0]["namespace"] == "test-ns"
        assert data[0]["name"] == "my-pack"
        assert data[0]["version"] == "1.0"
        assert data[0]["description"] == "A pack"

    def test_get_packs_row_not_found_skipped(self) -> None:
        space = {"id": "sp-1", "name": "work"}

        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_db.execute.return_value = mock_cursor

        app2 = FastAPI()
        app2.include_router(router, prefix="/api")
        app2.state.db = mock_db

        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.services.pack_attachments.get_active_pack_ids_for_space", return_value=["pack-ghost"]),
        ):
            client = TestClient(app2)
            resp = client.get("/api/spaces/sp-1/packs")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_packs_multiple_ids(self) -> None:
        space = {"id": "sp-1", "name": "work"}

        rows = {
            "pack-1": ("pack-1", "ns", "pack-a", "1.0", "Pack A"),
            "pack-2": ("pack-2", "ns", "pack-b", "2.0", "Pack B"),
        }

        mock_db = MagicMock()

        def execute_side_effect(query, params):
            pack_id = params[0]
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = rows.get(pack_id)
            return mock_cursor

        mock_db.execute.side_effect = execute_side_effect

        app2 = FastAPI()
        app2.include_router(router, prefix="/api")
        app2.state.db = mock_db

        pack_ids = ["pack-1", "pack-2"]
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.services.pack_attachments.get_active_pack_ids_for_space", return_value=pack_ids),
        ):
            client = TestClient(app2)
            resp = client.get("/api/spaces/sp-1/packs")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = {p["name"] for p in data}
        assert names == {"pack-a", "pack-b"}


class TestSpaceUpdateRequestValidation:
    def test_valid_name(self) -> None:
        req = SpaceUpdateRequest(name="new-name")
        assert req.name == "new-name"

    def test_none_name_allowed(self) -> None:
        req = SpaceUpdateRequest(name=None, instructions="hello")
        assert req.name is None
        assert req.instructions == "hello"

    def test_invalid_name_rejected(self) -> None:
        import pytest

        with pytest.raises(Exception):
            SpaceUpdateRequest(name="has space")

    def test_all_fields(self) -> None:
        req = SpaceUpdateRequest(name="updated", instructions="do things", model="gpt-4")
        assert req.name == "updated"
        assert req.instructions == "do things"
        assert req.model == "gpt-4"


_UUID1 = "a0000000-0000-4000-8000-000000000001"


class TestUpdateSpaceEndpoint:
    def test_update_name(self) -> None:
        app = _make_app()
        existing = {"id": _UUID1, "name": "old", "source_file": "", "source_hash": ""}
        updated = {"id": _UUID1, "name": "new-name", "source_file": "", "source_hash": ""}
        with (
            patch("anteroom.routers.spaces.get_space", return_value=existing),
            patch("anteroom.routers.spaces.update_space", return_value=updated),
        ):
            client = TestClient(app)
            resp = client.patch(f"/api/spaces/{_UUID1}", json={"name": "new-name"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "new-name"

    def test_update_instructions(self) -> None:
        app = _make_app()
        existing = {"id": _UUID1, "name": "work", "source_file": "", "source_hash": ""}
        updated = {**existing, "instructions": "Be helpful"}
        with (
            patch("anteroom.routers.spaces.get_space", return_value=existing),
            patch("anteroom.routers.spaces.update_space", return_value=updated),
        ):
            client = TestClient(app)
            resp = client.patch(f"/api/spaces/{_UUID1}", json={"instructions": "Be helpful"})
        assert resp.status_code == 200
        assert resp.json()["instructions"] == "Be helpful"

    def test_update_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.spaces.get_space", return_value=None):
            client = TestClient(app)
            resp = client.patch(f"/api/spaces/{_UUID1}", json={"name": "new"})
        assert resp.status_code == 404

    def test_update_empty_body_returns_existing(self) -> None:
        app = _make_app()
        existing = {"id": _UUID1, "name": "work", "source_file": "", "source_hash": ""}
        with patch("anteroom.routers.spaces.get_space", return_value=existing):
            client = TestClient(app)
            resp = client.patch(f"/api/spaces/{_UUID1}", json={})
        assert resp.status_code == 200
        assert resp.json()["name"] == "work"

    def test_update_invalid_uuid_returns_400(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.patch("/api/spaces/not-a-uuid", json={"name": "x"})
        assert resp.status_code == 400


class TestSyncSpaceEndpoint:
    def test_sync_success(self) -> None:
        app = _make_app()
        file_path = "/projects/myapp/.anteroom/space.yaml"
        synced = {"id": "sp-1", "name": "myspace", "source_file": file_path, "source_hash": "abc"}
        with patch("anteroom.services.spaces.sync_space_from_file", return_value=synced):
            with patch("pathlib.Path.is_file", return_value=True):
                client = TestClient(app)
                resp = client.post(
                    "/api/spaces/sync",
                    json={"file_path": file_path},
                    headers={"Content-Type": "application/json"},
                )
        assert resp.status_code == 200
        assert resp.json()["name"] == "myspace"

    def test_sync_missing_file_path(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/spaces/sync",
            json={},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert "file_path" in resp.json()["detail"]

    def test_sync_path_traversal_rejected(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/spaces/sync",
            json={"file_path": "/tmp/../etc/passwd"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()

    def test_sync_path_traversal_backslash(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/spaces/sync",
            json={"file_path": "C:\\Users\\..\\etc\\passwd"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    def test_sync_arbitrary_path_rejected(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/spaces/sync",
            json={"file_path": "/etc/passwd"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert ".anteroom" in resp.json()["detail"]

    def test_sync_wrong_content_type(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/spaces/sync",
            content='{"file_path": "/tmp/space.yaml"}',
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 415

    def test_sync_file_not_found(self) -> None:
        app = _make_app()
        with patch("pathlib.Path.is_file", return_value=False):
            client = TestClient(app)
            resp = client.post(
                "/api/spaces/sync",
                json={"file_path": "/nonexistent/.anteroom/space.yaml"},
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 400


class TestExportSpaceEndpoint:
    def test_export_success(self) -> None:
        from anteroom.services.spaces import SpaceConfig

        app = _make_app()
        space = {"id": _UUID1, "name": "work", "source_file": "", "source_hash": ""}
        cfg = SpaceConfig(name="work", instructions="Be helpful", config={"model": "gpt-4"})
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.services.spaces.export_space_to_yaml", return_value=cfg),
        ):
            client = TestClient(app)
            resp = client.get(f"/api/spaces/{_UUID1}/export")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "work"
        assert data["instructions"] == "Be helpful"
        assert data["config"]["model"] == "gpt-4"

    def test_export_not_found(self) -> None:
        app = _make_app()
        with patch("anteroom.routers.spaces.get_space", return_value=None):
            client = TestClient(app)
            resp = client.get(f"/api/spaces/{_UUID1}/export")
        assert resp.status_code == 404

    def test_export_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/spaces/not-a-uuid/export")
        assert resp.status_code == 400

    def test_export_minimal_space(self) -> None:
        from anteroom.services.spaces import SpaceConfig

        app = _make_app()
        space = {"id": _UUID1, "name": "minimal", "source_file": "", "source_hash": ""}
        cfg = SpaceConfig(name="minimal")
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.services.spaces.export_space_to_yaml", return_value=cfg),
        ):
            client = TestClient(app)
            resp = client.get(f"/api/spaces/{_UUID1}/export")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "minimal"
        assert "instructions" not in data
        assert "config" not in data


class TestEnrichOriginLocal:
    def test_local_space_origin(self) -> None:
        app = _make_app()
        space = {
            "id": "sp-1",
            "name": "local",
            "source_file": "/projects/myapp/.anteroom/space.yaml",
            "source_hash": "abc",
        }
        with (
            patch("anteroom.routers.spaces.get_space", return_value=space),
            patch("anteroom.routers.spaces.is_local_space", return_value=True),
        ):
            client = TestClient(app)
            resp = client.get("/api/spaces/sp-1")
        assert resp.status_code == 200
        assert resp.json()["origin"] == "local"
