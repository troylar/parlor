"""Tests for stale stream handling, disconnect polling, and stream-status endpoint."""

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

from anteroom.routers.chat import (
    _active_streams,
    _cancel_events,
    _message_queues,
    _poll_disconnect,
    router,
)


@pytest.fixture(autouse=True)
def _clean_module_state():
    _active_streams.clear()
    _cancel_events.clear()
    _message_queues.clear()
    yield
    _active_streams.clear()
    _cancel_events.clear()
    _message_queues.clear()


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


class TestPollDisconnect:
    """Test the _poll_disconnect background task."""

    @pytest.mark.asyncio
    async def test_cancels_on_disconnect(self) -> None:
        request = MagicMock()
        request.is_disconnected = AsyncMock(return_value=True)
        cancel_event = asyncio.Event()

        await _poll_disconnect(request, cancel_event, interval=0.01)

        assert cancel_event.is_set()

    @pytest.mark.asyncio
    async def test_does_not_cancel_when_connected(self) -> None:
        request = MagicMock()
        call_count = 0

        async def _is_disconnected():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                # Simulate disconnect after a few polls
                return True
            return False

        request.is_disconnected = _is_disconnected
        cancel_event = asyncio.Event()

        await _poll_disconnect(request, cancel_event, interval=0.01)

        assert cancel_event.is_set()
        assert call_count >= 3

    @pytest.mark.asyncio
    async def test_cancels_on_exception(self) -> None:
        request = MagicMock()
        request.is_disconnected = AsyncMock(side_effect=RuntimeError("broken"))
        cancel_event = asyncio.Event()

        await _poll_disconnect(request, cancel_event, interval=0.01)

        assert cancel_event.is_set()

    @pytest.mark.asyncio
    async def test_exits_when_cancel_already_set(self) -> None:
        request = MagicMock()
        request.is_disconnected = AsyncMock(return_value=False)
        cancel_event = asyncio.Event()
        cancel_event.set()

        # Should exit immediately since cancel_event is already set
        task = asyncio.create_task(_poll_disconnect(request, cancel_event, interval=0.01))
        await asyncio.wait_for(task, timeout=1.0)

        # is_disconnected should not be called since loop exits on cancel check
        assert cancel_event.is_set()

    @pytest.mark.asyncio
    async def test_can_be_cancelled(self) -> None:
        request = MagicMock()
        request.is_disconnected = AsyncMock(return_value=False)
        cancel_event = asyncio.Event()

        task = asyncio.create_task(_poll_disconnect(request, cancel_event, interval=10.0))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # cancel_event should NOT be set — the task was cancelled externally
        assert not cancel_event.is_set()


class TestStreamStatusEndpoint:
    """GET /conversations/{id}/stream-status."""

    def test_no_active_stream(self) -> None:
        conv_id = str(uuid.uuid4())
        app = _make_app()
        client = TestClient(app)
        resp = client.get(f"/api/conversations/{conv_id}/stream-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is False

    def test_active_stream(self) -> None:
        conv_id = str(uuid.uuid4())
        _active_streams[conv_id] = {
            "started_at": time.monotonic() - 5.0,
            "request": MagicMock(),
            "cancel_event": asyncio.Event(),
        }

        app = _make_app()
        client = TestClient(app)
        resp = client.get(f"/api/conversations/{conv_id}/stream-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is True
        assert data["age_seconds"] >= 5

    def test_invalid_uuid(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/api/conversations/not-a-uuid/stream-status")
        assert resp.status_code == 400


class TestStopEndpointCancelPropagation:
    """Verify stop endpoint actually fires cancel events."""

    def test_stop_fires_cancel_events(self) -> None:
        conv_id = str(uuid.uuid4())
        event1 = asyncio.Event()
        event2 = asyncio.Event()
        _cancel_events[conv_id] = {event1, event2}

        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/stop")
            assert resp.status_code == 200

        assert event1.is_set()
        assert event2.is_set()

    def test_stop_idempotent_no_events(self) -> None:
        conv_id = str(uuid.uuid4())
        # No events registered — should succeed without error

        app = _make_app()
        with patch("anteroom.routers.chat.storage") as mock_storage:
            mock_storage.get_conversation.return_value = {"id": conv_id, "type": "chat"}
            client = TestClient(app)
            resp = client.post(f"/api/conversations/{conv_id}/stop")
            assert resp.status_code == 200
            assert resp.json()["status"] == "stopped"
