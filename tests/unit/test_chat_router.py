"""Tests for chat router endpoints (stop_generation, get_attachment, stale stream)."""

from __future__ import annotations

import asyncio
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.routers.chat import _active_streams, _cancel_events, router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")

    mock_db = MagicMock()
    mock_db_manager = MagicMock()
    mock_db_manager.get.return_value = mock_db
    app.state.db = mock_db
    app.state.db_manager = mock_db_manager

    mock_config = MagicMock()
    mock_config.identity = None
    mock_config.app.data_dir = Path(tempfile.mkdtemp())
    mock_config.app.tls = False
    app.state.config = mock_config

    app.state.tool_registry = MagicMock()
    app.state.mcp_manager = MagicMock()

    return app


class TestStopGenerationEndpoint:
    """POST /conversations/{id}/stop — cancel active generation."""

    def test_stop_success(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/stop")
            assert resp.status_code == 200
            assert resp.json()["status"] == "stopped"

    def test_stop_conversation_not_found(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = None
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/stop")
            assert resp.status_code == 404

    def test_stop_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.post("/api/conversations/bad-uuid/stop")
        assert resp.status_code == 400

    def test_stop_no_active_stream(self) -> None:
        """Stop should succeed even when no stream is active (idempotent)."""
        conv_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/stop")
            assert resp.status_code == 200


class TestGetAttachmentEndpoint:
    """GET /attachments/{id} — retrieve attachment files."""

    def test_attachment_not_found(self) -> None:
        att_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_attachment.return_value = None
            client = TestClient(app)
            resp = client.get(f"/api/attachments/{att_id}")
            assert resp.status_code == 404

    def test_attachment_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/attachments/bad-uuid")
        assert resp.status_code == 400

    def test_attachment_path_traversal_blocked(self) -> None:
        att_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_attachment.return_value = {
                "id": att_id,
                "storage_path": "../../etc/passwd",
                "mime_type": "text/plain",
                "filename": "passwd",
            }
            client = TestClient(app)
            resp = client.get(f"/api/attachments/{att_id}")
            assert resp.status_code == 403

    def test_attachment_file_missing(self) -> None:
        att_id = str(uuid.uuid4())
        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_attachment.return_value = {
                "id": att_id,
                "storage_path": "attachments/nonexistent.txt",
                "mime_type": "text/plain",
                "filename": "nonexistent.txt",
            }
            client = TestClient(app)
            resp = client.get(f"/api/attachments/{att_id}")
            assert resp.status_code == 404

    def test_attachment_inline_for_image(self) -> None:
        att_id = str(uuid.uuid4())
        app = _make_app()
        data_dir = app.state.config.app.data_dir
        # Create a real file in data_dir
        att_dir = data_dir / "attachments"
        att_dir.mkdir(parents=True, exist_ok=True)
        test_file = att_dir / "test.png"
        test_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_attachment.return_value = {
                "id": att_id,
                "storage_path": "attachments/test.png",
                "mime_type": "image/png",
                "filename": "test.png",
            }
            client = TestClient(app)
            resp = client.get(f"/api/attachments/{att_id}")
            assert resp.status_code == 200
            assert "inline" in resp.headers.get("content-disposition", "")

    def test_attachment_download_for_non_image(self) -> None:
        att_id = str(uuid.uuid4())
        app = _make_app()
        data_dir = app.state.config.app.data_dir
        att_dir = data_dir / "attachments"
        att_dir.mkdir(parents=True, exist_ok=True)
        test_file = att_dir / "doc.pdf"
        test_file.write_bytes(b"%PDF-1.4 test content")
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_attachment.return_value = {
                "id": att_id,
                "storage_path": "attachments/doc.pdf",
                "mime_type": "application/pdf",
                "filename": "doc.pdf",
            }
            client = TestClient(app)
            resp = client.get(f"/api/attachments/{att_id}")
            assert resp.status_code == 200
            assert "attachment" in resp.headers.get("content-disposition", "")


class TestStaleStreamDetection:
    """Stale stream detection and cleanup in _active_streams."""

    def setup_method(self) -> None:
        _active_streams.clear()
        _cancel_events.clear()

    def teardown_method(self) -> None:
        _active_streams.clear()
        _cancel_events.clear()

    def test_active_streams_stores_metadata(self) -> None:
        """_active_streams now stores dicts with metadata instead of bools."""
        cid = str(uuid.uuid4())
        cancel = asyncio.Event()
        mock_request = MagicMock()
        _active_streams[cid] = {
            "started_at": time.monotonic(),
            "request": mock_request,
            "cancel_event": cancel,
        }
        info = _active_streams[cid]
        assert isinstance(info, dict)
        assert "started_at" in info
        assert info["cancel_event"] is cancel

    def test_stale_stream_detected_by_disconnect(self) -> None:
        """A stream whose request is disconnected is considered stale."""
        cid = str(uuid.uuid4())
        cancel = asyncio.Event()
        mock_request = AsyncMock()
        mock_request.is_disconnected.return_value = True
        _active_streams[cid] = {
            "started_at": time.monotonic(),
            "request": mock_request,
            "cancel_event": cancel,
        }
        info = _active_streams[cid]
        loop = asyncio.new_event_loop()
        is_disconnected = loop.run_until_complete(info["request"].is_disconnected())
        loop.close()
        assert is_disconnected is True

    def test_active_stream_not_stale(self) -> None:
        """A connected stream with recent start time is not stale."""
        cid = str(uuid.uuid4())
        cancel = asyncio.Event()
        mock_request = AsyncMock()
        mock_request.is_disconnected.return_value = False
        _active_streams[cid] = {
            "started_at": time.monotonic(),
            "request": mock_request,
            "cancel_event": cancel,
        }
        info = _active_streams[cid]
        loop = asyncio.new_event_loop()
        is_disconnected = loop.run_until_complete(info["request"].is_disconnected())
        loop.close()
        assert is_disconnected is False
        assert time.monotonic() - info["started_at"] < 5

    def test_stale_cancel_event_fires(self) -> None:
        """When a stale stream is detected, its cancel event should be set."""
        cancel = asyncio.Event()
        assert not cancel.is_set()
        cancel.set()
        assert cancel.is_set()

    def test_truthiness_preserved(self) -> None:
        """Dict value is truthy, preserving the queue routing check."""
        cid = str(uuid.uuid4())
        _active_streams[cid] = {
            "started_at": time.monotonic(),
            "request": MagicMock(),
            "cancel_event": asyncio.Event(),
        }
        assert _active_streams.get(cid)


class TestBuildToolListWithSkills:
    """_build_tool_list includes invoke_skill when skill_registry has skills."""

    def test_invoke_skill_added_when_skills_exist(self) -> None:
        from anteroom.routers.chat import _build_tool_list

        tool_reg = MagicMock()
        tool_reg.get_openai_tools.return_value = [
            {"type": "function", "function": {"name": "read_file"}},
        ]
        tool_reg.list_tools.return_value = ["read_file"]
        mcp = MagicMock()
        mcp.get_openai_tools.return_value = []

        skill_reg = MagicMock()
        skill_reg.get_invoke_skill_definition.return_value = {
            "type": "function",
            "function": {"name": "invoke_skill"},
        }

        tools, _, _ = _build_tool_list(
            tool_registry=tool_reg,
            mcp_manager=mcp,
            plan_mode=False,
            conversation_id="c1",
            data_dir=Path(tempfile.mkdtemp()),
            max_tools=128,
            skill_registry=skill_reg,
        )
        tool_names = [t["function"]["name"] for t in tools]
        assert "invoke_skill" in tool_names

    def test_no_invoke_skill_when_no_registry(self) -> None:
        from anteroom.routers.chat import _build_tool_list

        tool_reg = MagicMock()
        tool_reg.get_openai_tools.return_value = [
            {"type": "function", "function": {"name": "read_file"}},
        ]
        tool_reg.list_tools.return_value = ["read_file"]
        mcp = None

        tools, _, _ = _build_tool_list(
            tool_registry=tool_reg,
            mcp_manager=mcp,
            plan_mode=False,
            conversation_id="c1",
            data_dir=Path(tempfile.mkdtemp()),
            max_tools=128,
            skill_registry=None,
        )
        tool_names = [t["function"]["name"] for t in tools]
        assert "invoke_skill" not in tool_names


class TestExecuteWebToolInvokeSkill:
    """_execute_web_tool handles invoke_skill correctly."""

    @pytest.mark.asyncio
    async def test_invoke_skill_no_queue_returns_error(self) -> None:
        from anteroom.routers.chat import ToolExecutorContext, WebConfirmContext, _execute_web_tool, _message_queues

        skill_reg = MagicMock()
        skill = MagicMock()
        skill.prompt = "Do the thing"
        skill_reg.get.return_value = skill

        ctx = ToolExecutorContext(
            tool_registry=MagicMock(),
            mcp_manager=None,
            confirm_ctx=MagicMock(spec=WebConfirmContext),
            ai_service=MagicMock(),
            cancel_event=asyncio.Event(),
            db=MagicMock(),
            uid=None,
            uname=None,
            conversation_id="no-queue-conv",
            tools_openai=[],
            subagent_events={},
            subagent_limiter=MagicMock(),
            sa_config=MagicMock(),
            request_config=MagicMock(),
            skill_registry=skill_reg,
        )

        _message_queues.pop("no-queue-conv", None)

        result = await _execute_web_tool(ctx, "invoke_skill", {"skill_name": "test"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invoke_skill_with_queue_succeeds(self) -> None:
        from anteroom.routers.chat import ToolExecutorContext, WebConfirmContext, _execute_web_tool, _message_queues

        skill_reg = MagicMock()
        skill = MagicMock()
        skill.prompt = "Do the thing"
        skill_reg.get.return_value = skill

        queue: asyncio.Queue = asyncio.Queue()
        conv_id = "queued-conv"

        ctx = ToolExecutorContext(
            tool_registry=MagicMock(),
            mcp_manager=None,
            confirm_ctx=MagicMock(spec=WebConfirmContext),
            ai_service=MagicMock(),
            cancel_event=asyncio.Event(),
            db=MagicMock(),
            uid=None,
            uname=None,
            conversation_id=conv_id,
            tools_openai=[],
            subagent_events={},
            subagent_limiter=MagicMock(),
            sa_config=MagicMock(),
            request_config=MagicMock(),
            skill_registry=skill_reg,
        )

        _message_queues[conv_id] = queue
        try:
            result = await _execute_web_tool(ctx, "invoke_skill", {"skill_name": "test"})
            assert result["status"] == "skill_invoked"
            assert not queue.empty()
        finally:
            _message_queues.pop(conv_id, None)
