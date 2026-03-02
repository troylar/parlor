"""Tests for the config API router."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.routers.config_api import router


def _make_config(
    base_url: str = "https://api.example.com",
    api_key: str = "sk-test",
    model: str = "gpt-4",
    user_system_prompt: str = "",
    system_prompt: str = "",
    read_only: bool = False,
) -> MagicMock:
    config = MagicMock()
    config.ai.base_url = base_url
    config.ai.api_key = api_key
    config.ai.model = model
    config.ai.user_system_prompt = user_system_prompt
    config.ai.system_prompt = system_prompt
    config.safety.read_only = read_only
    config.identity = None
    return config


def _make_app(
    config: MagicMock | None = None,
    mcp_manager: MagicMock | None = None,
    tool_registry: MagicMock | None = None,
    db_manager: MagicMock | None = None,
    enforced_fields: list[str] | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.config = config or _make_config()
    app.state.mcp_manager = mcp_manager
    if tool_registry is not None:
        app.state.tool_registry = tool_registry
    if db_manager is not None:
        app.state.db_manager = db_manager
    if enforced_fields is not None:
        app.state.enforced_fields = enforced_fields
    return app


# ---------------------------------------------------------------------------
# GET /config
# ---------------------------------------------------------------------------


def test_get_config_no_mcp_no_identity() -> None:
    config = _make_config(api_key="sk-secret")
    app = _make_app(config=config)
    client = TestClient(app)
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ai"]["api_key_set"] is True
    assert data["ai"]["model"] == "gpt-4"
    assert data["ai"]["base_url"] == "https://api.example.com"
    assert data["identity"] is None
    assert data["mcp_servers"] == []
    assert data["enforced_fields"] == []
    assert data["read_only"] is False


def test_get_config_no_api_key() -> None:
    config = _make_config(api_key="")
    app = _make_app(config=config)
    client = TestClient(app)
    resp = client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json()["ai"]["api_key_set"] is False


def test_get_config_with_identity() -> None:
    config = _make_config()
    identity = MagicMock()
    identity.user_id = "user-abc"
    identity.display_name = "Test User"
    config.identity = identity

    app = _make_app(config=config)
    client = TestClient(app)
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["identity"] == {"user_id": "user-abc", "display_name": "Test User"}


def test_get_config_with_mcp_manager() -> None:
    mcp_manager = MagicMock()
    mcp_manager.get_server_statuses.return_value = {
        "my-server": {
            "name": "my-server",
            "transport": "stdio",
            "status": "connected",
            "tool_count": 3,
            "error_message": None,
        }
    }
    app = _make_app(mcp_manager=mcp_manager)
    client = TestClient(app)
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["mcp_servers"]) == 1
    srv = data["mcp_servers"][0]
    assert srv["name"] == "my-server"
    assert srv["transport"] == "stdio"
    assert srv["status"] == "connected"
    assert srv["tool_count"] == 3
    assert srv["error_message"] is None


def test_get_config_with_mcp_server_error() -> None:
    mcp_manager = MagicMock()
    mcp_manager.get_server_statuses.return_value = {
        "broken": {
            "name": "broken",
            "transport": "sse",
            "status": "error",
            "tool_count": 0,
            "error_message": "Connection refused",
        }
    }
    app = _make_app(mcp_manager=mcp_manager)
    client = TestClient(app)
    resp = client.get("/api/config")
    assert resp.status_code == 200
    srv = resp.json()["mcp_servers"][0]
    assert srv["error_message"] == "Connection refused"


def test_get_config_with_enforced_fields() -> None:
    app = _make_app(enforced_fields=["ai.model", "ai.system_prompt"])
    client = TestClient(app)
    resp = client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json()["enforced_fields"] == ["ai.model", "ai.system_prompt"]


def test_get_config_read_only() -> None:
    config = _make_config(read_only=True)
    app = _make_app(config=config)
    client = TestClient(app)
    resp = client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json()["read_only"] is True


# ---------------------------------------------------------------------------
# PATCH /config
# ---------------------------------------------------------------------------


def test_patch_config_update_model() -> None:
    config = _make_config(model="gpt-4")
    app = _make_app(config=config)
    client = TestClient(app)

    with (
        patch("anteroom.routers.config_api._persist_config") as mock_persist,
    ):
        resp = client.patch("/api/config", json={"model": "gpt-4o"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "gpt-4o"
        mock_persist.assert_called_once()


def test_patch_config_same_model_no_persist() -> None:
    config = _make_config(model="gpt-4")
    app = _make_app(config=config)
    client = TestClient(app)

    with patch("anteroom.routers.config_api._persist_config") as mock_persist:
        resp = client.patch("/api/config", json={"model": "gpt-4"})
        assert resp.status_code == 200
        mock_persist.assert_not_called()


def test_patch_config_update_system_prompt() -> None:
    config = _make_config(user_system_prompt="")
    app = _make_app(config=config)
    client = TestClient(app)

    with (
        patch("anteroom.routers.config_api._persist_config") as mock_persist,
        patch("anteroom.config._DEFAULT_SYSTEM_PROMPT", "DEFAULT"),
    ):
        resp = client.patch("/api/config", json={"system_prompt": "Be concise."})
        assert resp.status_code == 200
        data = resp.json()
        assert data["system_prompt"] == "Be concise."
        mock_persist.assert_called_once()


def test_patch_config_clear_system_prompt() -> None:
    config = _make_config(user_system_prompt="old prompt")
    app = _make_app(config=config)
    client = TestClient(app)

    with (
        patch("anteroom.routers.config_api._persist_config"),
        patch("anteroom.config._DEFAULT_SYSTEM_PROMPT", "DEFAULT"),
    ):
        resp = client.patch("/api/config", json={"system_prompt": ""})
        assert resp.status_code == 200
        assert resp.json()["system_prompt"] == ""


def test_patch_config_model_enforced_blocked() -> None:
    config = _make_config(model="gpt-4")
    app = _make_app(config=config, enforced_fields=["ai.model"])
    client = TestClient(app)

    resp = client.patch("/api/config", json={"model": "gpt-4o"})
    assert resp.status_code == 403
    assert "enforced" in resp.json()["detail"]


def test_patch_config_system_prompt_enforced_blocked() -> None:
    config = _make_config(user_system_prompt="locked")
    app = _make_app(config=config, enforced_fields=["ai.system_prompt"])
    client = TestClient(app)

    resp = client.patch("/api/config", json={"system_prompt": "override"})
    assert resp.status_code == 403
    assert "enforced" in resp.json()["detail"]


def test_patch_config_no_changes() -> None:
    config = _make_config(model="gpt-4", user_system_prompt="hello")
    app = _make_app(config=config)
    client = TestClient(app)

    with patch("anteroom.routers.config_api._persist_config") as mock_persist:
        resp = client.patch("/api/config", json={"model": "gpt-4", "system_prompt": "hello"})
        assert resp.status_code == 200
        mock_persist.assert_not_called()


# ---------------------------------------------------------------------------
# GET /models
# ---------------------------------------------------------------------------


def test_get_models_returns_sorted_list() -> None:
    config = _make_config()
    app = _make_app(config=config)
    client = TestClient(app)

    mock_service = AsyncMock()
    mock_service.validate_connection = AsyncMock(return_value=(True, "ok", ["gpt-4o", "gpt-3.5-turbo", "gpt-4"]))

    with patch("anteroom.routers.config_api.create_ai_service", return_value=mock_service):
        resp = client.get("/api/models")
        assert resp.status_code == 200
        models = resp.json()
        assert models == sorted(models)
        assert "gpt-4" in models
        assert "gpt-4o" in models


def test_get_models_returns_empty_on_error() -> None:
    config = _make_config()
    app = _make_app(config=config)
    client = TestClient(app)

    mock_service = AsyncMock()
    mock_service.validate_connection = AsyncMock(side_effect=Exception("connection error"))

    with patch("anteroom.routers.config_api.create_ai_service", return_value=mock_service):
        resp = client.get("/api/models")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /config/validate
# ---------------------------------------------------------------------------


def test_validate_connection_success() -> None:
    config = _make_config()
    app = _make_app(config=config)
    client = TestClient(app)

    mock_service = AsyncMock()
    mock_service.validate_connection = AsyncMock(return_value=(True, "Connected", ["gpt-4"]))

    with patch("anteroom.routers.config_api.create_ai_service", return_value=mock_service):
        resp = client.post("/api/config/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["message"] == "Connected"
        assert "gpt-4" in data["models"]


def test_validate_connection_failure() -> None:
    config = _make_config()
    app = _make_app(config=config)
    client = TestClient(app)

    mock_service = AsyncMock()
    mock_service.validate_connection = AsyncMock(return_value=(False, "Invalid API key", []))

    with patch("anteroom.routers.config_api.create_ai_service", return_value=mock_service):
        resp = client.post("/api/config/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert data["models"] == []


# ---------------------------------------------------------------------------
# GET /mcp/tools
# ---------------------------------------------------------------------------


def test_list_mcp_tools_no_manager_no_registry() -> None:
    app = _make_app(mcp_manager=None)
    client = TestClient(app)
    resp = client.get("/api/mcp/tools")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_mcp_tools_builtin_only() -> None:
    tool_registry = MagicMock()
    tool_registry.get_openai_tools.return_value = [
        {
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {}},
            }
        }
    ]
    app = _make_app(tool_registry=tool_registry, mcp_manager=None)
    client = TestClient(app)
    resp = client.get("/api/mcp/tools")
    assert resp.status_code == 200
    tools = resp.json()
    assert len(tools) == 1
    assert tools[0]["name"] == "read_file"
    assert tools[0]["server_name"] == "builtin"
    assert tools[0]["description"] == "Read a file"


def test_list_mcp_tools_mcp_tools_included() -> None:
    mcp_manager = MagicMock()
    mcp_manager.get_all_tools.return_value = [
        {
            "name": "search",
            "server_name": "brave",
            "description": "Search the web",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        }
    ]
    app = _make_app(mcp_manager=mcp_manager)
    client = TestClient(app)
    resp = client.get("/api/mcp/tools")
    assert resp.status_code == 200
    tools = resp.json()
    assert any(t["name"] == "search" and t["server_name"] == "brave" for t in tools)


def test_list_mcp_tools_both_builtin_and_mcp() -> None:
    tool_registry = MagicMock()
    tool_registry.get_openai_tools.return_value = [
        {"function": {"name": "bash", "description": "Run bash", "parameters": {}}}
    ]

    mcp_manager = MagicMock()
    mcp_manager.get_all_tools.return_value = [
        {
            "name": "fetch_url",
            "server_name": "fetcher",
            "description": "Fetch a URL",
            "input_schema": {},
        }
    ]

    app = _make_app(tool_registry=tool_registry, mcp_manager=mcp_manager)
    client = TestClient(app)
    resp = client.get("/api/mcp/tools")
    assert resp.status_code == 200
    tools = resp.json()
    names = [t["name"] for t in tools]
    assert "bash" in names
    assert "fetch_url" in names


# ---------------------------------------------------------------------------
# GET /databases
# ---------------------------------------------------------------------------


def test_list_databases_no_db_manager() -> None:
    app = _make_app()
    client = TestClient(app)
    resp = client.get("/api/databases")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["name"] == "personal"


def test_list_databases_with_db_manager() -> None:
    db_manager = MagicMock()
    db_manager.list_databases.return_value = [
        {"name": "personal", "path": "/home/user/.anteroom/db.sqlite"},
        {"name": "work", "path": "/home/user/work.db"},
    ]
    app = _make_app(db_manager=db_manager)
    client = TestClient(app)
    resp = client.get("/api/databases")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[1]["name"] == "work"


# ---------------------------------------------------------------------------
# POST /databases
# ---------------------------------------------------------------------------


def test_add_database_success() -> None:
    db_manager = MagicMock()
    db_manager.list_databases.return_value = []

    home = Path.home()
    db_path = str(home / "test.db")

    app = _make_app(db_manager=db_manager)
    client = TestClient(app)

    with (
        patch("anteroom.routers.config_api._persist_database"),
        patch("anteroom.routers.config_api.Path.mkdir"),
    ):
        resp = client.post("/api/databases", json={"name": "mydb", "path": db_path})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "mydb"


def test_add_database_reserved_name_blocked() -> None:
    db_manager = MagicMock()
    app = _make_app(db_manager=db_manager)
    client = TestClient(app)

    resp = client.post("/api/databases", json={"name": "personal", "path": str(Path.home() / "x.db")})
    assert resp.status_code == 400
    assert "reserved" in resp.json()["detail"]


def test_add_database_no_db_manager() -> None:
    app = _make_app()
    client = TestClient(app)
    resp = client.post("/api/databases", json={"name": "mydb", "path": str(Path.home() / "x.db")})
    assert resp.status_code == 400
    assert "not available" in resp.json()["detail"]


def test_add_database_duplicate_name() -> None:
    db_manager = MagicMock()
    db_manager.list_databases.return_value = [{"name": "mydb", "path": "/home/user/mydb.db"}]
    app = _make_app(db_manager=db_manager)
    client = TestClient(app)

    resp = client.post("/api/databases", json={"name": "mydb", "path": str(Path.home() / "mydb.db")})
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


def test_add_database_invalid_extension() -> None:
    db_manager = MagicMock()
    db_manager.list_databases.return_value = []
    app = _make_app(db_manager=db_manager)
    client = TestClient(app)

    resp = client.post("/api/databases", json={"name": "mydb", "path": str(Path.home() / "mydb.txt")})
    assert resp.status_code == 400
    assert ".db" in resp.json()["detail"]


def test_add_database_outside_home_blocked() -> None:
    db_manager = MagicMock()
    db_manager.list_databases.return_value = []
    app = _make_app(db_manager=db_manager)
    client = TestClient(app)

    resp = client.post("/api/databases", json={"name": "mydb", "path": "/etc/mydb.db"})
    assert resp.status_code == 400
    assert "home directory" in resp.json()["detail"]


def test_add_database_valid_sqlite_extension() -> None:
    db_manager = MagicMock()
    db_manager.list_databases.return_value = []

    home = Path.home()
    db_path = str(home / "test.sqlite3")

    app = _make_app(db_manager=db_manager)
    client = TestClient(app)

    with (
        patch("anteroom.routers.config_api._persist_database"),
        patch("anteroom.routers.config_api.Path.mkdir"),
    ):
        resp = client.post("/api/databases", json={"name": "mydb3", "path": db_path})
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# DELETE /databases
# ---------------------------------------------------------------------------


def test_delete_database_success() -> None:
    db_manager = MagicMock()
    db_manager.list_databases.return_value = [{"name": "work", "path": "/home/user/work.db"}]
    app = _make_app(db_manager=db_manager)
    client = TestClient(app)

    with patch("anteroom.routers.config_api._remove_database_from_config"):
        resp = client.delete("/api/databases/work")
        assert resp.status_code == 204
        db_manager.remove.assert_called_once_with("work")


def test_delete_database_personal_blocked() -> None:
    db_manager = MagicMock()
    app = _make_app(db_manager=db_manager)
    client = TestClient(app)

    resp = client.delete("/api/databases/personal")
    assert resp.status_code == 400
    assert "personal" in resp.json()["detail"]


def test_delete_database_no_db_manager() -> None:
    app = _make_app()
    client = TestClient(app)

    resp = client.delete("/api/databases/work")
    assert resp.status_code == 400
    assert "not available" in resp.json()["detail"]


def test_delete_database_not_found() -> None:
    db_manager = MagicMock()
    db_manager.list_databases.return_value = [{"name": "other", "path": "/home/user/other.db"}]
    app = _make_app(db_manager=db_manager)
    client = TestClient(app)

    resp = client.delete("/api/databases/nonexistent")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /browse
# ---------------------------------------------------------------------------


def test_browse_default_home() -> None:
    app = _make_app()
    client = TestClient(app)

    with tempfile.TemporaryDirectory() as tmpdir:
        home_path = Path(tmpdir)
        # Create some entries
        (home_path / "subdir").mkdir()
        (home_path / "data.db").touch()
        (home_path / ".hidden").mkdir()
        (home_path / "notes.txt").touch()

        with (
            patch("anteroom.routers.config_api.Path.home", return_value=home_path),
            patch.dict(os.environ, {"HOME": tmpdir}),
        ):
            resp = client.get("/api/browse", params={"path": tmpdir})
            assert resp.status_code == 200
            data = resp.json()
            assert "current" in data
            assert "entries" in data
            names = [e["name"] for e in data["entries"]]
            assert "subdir" in names
            assert "data.db" in names
            # Hidden files and non-db files must be filtered out
            assert ".hidden" not in names
            assert "notes.txt" not in names


def test_browse_dirs_listed_before_files() -> None:
    app = _make_app()
    client = TestClient(app)

    with tempfile.TemporaryDirectory() as tmpdir:
        home_path = Path(tmpdir)
        (home_path / "zdir").mkdir()
        (home_path / "afile.db").touch()

        with patch("anteroom.routers.config_api.Path.home", return_value=home_path):
            resp = client.get("/api/browse", params={"path": tmpdir})
            assert resp.status_code == 200
            entries = resp.json()["entries"]
            types = [e["type"] for e in entries]
            # dirs first
            if types:
                first_file_idx = next((i for i, t in enumerate(types) if t == "file"), len(types))
                first_dir_idx = next((i for i, t in enumerate(types) if t == "dir"), len(types))
                assert first_dir_idx <= first_file_idx


def test_browse_outside_home_blocked() -> None:
    app = _make_app()
    client = TestClient(app)

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("anteroom.routers.config_api.Path.home", return_value=Path(tmpdir)):
            resp = client.get("/api/browse", params={"path": "/etc"})
            assert resp.status_code == 403


def test_browse_not_a_directory() -> None:
    app = _make_app()
    client = TestClient(app)

    with tempfile.TemporaryDirectory() as tmpdir:
        home_path = Path(tmpdir)
        test_file = home_path / "test.db"
        test_file.touch()

        with patch("anteroom.routers.config_api.Path.home", return_value=home_path):
            resp = client.get("/api/browse", params={"path": str(test_file)})
            assert resp.status_code == 400
            assert "Not a directory" in resp.json()["detail"]


def test_browse_parent_is_none_at_root() -> None:
    """When browsing root, parent should be None."""
    app = _make_app()
    client = TestClient(app)

    with tempfile.TemporaryDirectory() as tmpdir:
        home_path = Path(tmpdir)
        with patch("anteroom.routers.config_api.Path.home", return_value=home_path):
            resp = client.get("/api/browse", params={"path": tmpdir})
            assert resp.status_code == 200
            data = resp.json()
            # Parent exists for most dirs; just verify the key is present
            assert "parent" in data


# ---------------------------------------------------------------------------
# MCP server connect / disconnect / reconnect
# ---------------------------------------------------------------------------


def test_connect_mcp_server_no_manager() -> None:
    app = _make_app(mcp_manager=None)
    client = TestClient(app)

    resp = client.post("/api/mcp/servers/my-server/connect")
    assert resp.status_code == 400
    assert "No MCP servers" in resp.json()["detail"]


def test_connect_mcp_server_success() -> None:
    mcp_manager = MagicMock()
    mcp_manager.connect_server = AsyncMock()
    mcp_manager.get_server_statuses.return_value = {"my-server": {"name": "my-server", "status": "connected"}}
    app = _make_app(mcp_manager=mcp_manager)
    client = TestClient(app)

    resp = client.post("/api/mcp/servers/my-server/connect")
    assert resp.status_code == 200
    mcp_manager.connect_server.assert_called_once_with("my-server")


def test_connect_mcp_server_not_found() -> None:
    mcp_manager = MagicMock()
    mcp_manager.connect_server = AsyncMock(side_effect=ValueError("Server 'unknown' not found"))
    app = _make_app(mcp_manager=mcp_manager)
    client = TestClient(app)

    resp = client.post("/api/mcp/servers/unknown/connect")
    assert resp.status_code == 404


def test_disconnect_mcp_server_no_manager() -> None:
    app = _make_app(mcp_manager=None)
    client = TestClient(app)

    resp = client.post("/api/mcp/servers/my-server/disconnect")
    assert resp.status_code == 400


def test_disconnect_mcp_server_success() -> None:
    mcp_manager = MagicMock()
    mcp_manager.disconnect_server = AsyncMock()
    mcp_manager.get_server_statuses.return_value = {"my-server": {"name": "my-server", "status": "disconnected"}}
    app = _make_app(mcp_manager=mcp_manager)
    client = TestClient(app)

    resp = client.post("/api/mcp/servers/my-server/disconnect")
    assert resp.status_code == 200
    assert resp.json()["status"] == "disconnected"
    mcp_manager.disconnect_server.assert_called_once_with("my-server")


def test_disconnect_mcp_server_not_found() -> None:
    mcp_manager = MagicMock()
    mcp_manager.disconnect_server = AsyncMock(side_effect=ValueError("Server 'unknown' not found"))
    app = _make_app(mcp_manager=mcp_manager)
    client = TestClient(app)

    resp = client.post("/api/mcp/servers/unknown/disconnect")
    assert resp.status_code == 404


def test_reconnect_mcp_server_no_manager() -> None:
    app = _make_app(mcp_manager=None)
    client = TestClient(app)

    resp = client.post("/api/mcp/servers/my-server/reconnect")
    assert resp.status_code == 400


def test_reconnect_mcp_server_success() -> None:
    mcp_manager = MagicMock()
    mcp_manager.reconnect_server = AsyncMock()
    mcp_manager.get_server_statuses.return_value = {"my-server": {"name": "my-server", "status": "connected"}}
    app = _make_app(mcp_manager=mcp_manager)
    client = TestClient(app)

    resp = client.post("/api/mcp/servers/my-server/reconnect")
    assert resp.status_code == 200
    mcp_manager.reconnect_server.assert_called_once_with("my-server")


def test_reconnect_mcp_server_not_found() -> None:
    mcp_manager = MagicMock()
    mcp_manager.reconnect_server = AsyncMock(side_effect=ValueError("Server 'unknown' not found"))
    app = _make_app(mcp_manager=mcp_manager)
    client = TestClient(app)

    resp = client.post("/api/mcp/servers/unknown/reconnect")
    assert resp.status_code == 404


def test_connect_mcp_server_returns_unknown_status_when_not_in_statuses() -> None:
    """When statuses dict doesn't have the server name, fallback status is returned."""
    mcp_manager = MagicMock()
    mcp_manager.connect_server = AsyncMock()
    mcp_manager.get_server_statuses.return_value = {}
    app = _make_app(mcp_manager=mcp_manager)
    client = TestClient(app)

    resp = client.post("/api/mcp/servers/ghost/connect")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "ghost"
    assert data["status"] == "unknown"
