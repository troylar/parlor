"""Tests for the SSE events router."""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.config import RateLimitConfig
from anteroom.routers.events import router


def _make_app(with_event_bus: bool = True, sse_retry_ms: int = 5000) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.rate_limit_config = RateLimitConfig(sse_retry_ms=sse_retry_ms)

    if with_event_bus:
        event_bus = MagicMock()
        queue: asyncio.Queue = asyncio.Queue()
        event_bus.subscribe.return_value = queue
        event_bus.unsubscribe = MagicMock()
        app.state.event_bus = event_bus

    return app


class TestEventStreamValidation:
    """GET /events — input validation."""

    def test_rejects_invalid_db_name(self) -> None:
        app = _make_app(with_event_bus=False)
        client = TestClient(app)
        resp = client.get("/api/events?db='; DROP TABLE x;--")
        assert resp.status_code == 400
        assert "Invalid database name" in resp.json()["detail"]

    def test_rejects_long_db_name(self) -> None:
        app = _make_app(with_event_bus=False)
        client = TestClient(app)
        resp = client.get(f"/api/events?db={'a' * 65}")
        assert resp.status_code == 400

    def test_rejects_invalid_conversation_id(self) -> None:
        app = _make_app(with_event_bus=False)
        client = TestClient(app)
        resp = client.get("/api/events?conversation_id=not-a-uuid")
        assert resp.status_code == 400
        assert "Invalid ID format" in resp.json()["detail"]

    def test_accepts_valid_db_name(self) -> None:
        app = _make_app(with_event_bus=False)
        client = TestClient(app)
        with client.stream("GET", "/api/events?db=personal") as resp:
            assert resp.status_code == 200

    def test_no_event_bus_returns_error_event(self) -> None:
        app = _make_app(with_event_bus=False)
        client = TestClient(app)
        with client.stream("GET", "/api/events") as resp:
            assert resp.status_code == 200
            first_line = None
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    first_line = line
                    break
            assert first_line is not None
            data = json.loads(first_line.removeprefix("data:").strip())
            assert "Event bus not available" in data["message"]

    def test_event_source_response_imported_at_function_level(self) -> None:
        """Regression: EventSourceResponse must be available regardless of event_bus presence."""
        # The import was previously inside the `if not hasattr(...)` block,
        # causing UnboundLocalError when event_bus existed. Verify the import
        # is at the top of the function by checking the function can be called
        # with an event_bus present without import errors.
        app = _make_app(with_event_bus=True)

        # Patch is_disconnected to return True immediately so the SSE loop exits
        async def mock_is_disconnected(self):
            return True

        client = TestClient(app)
        with patch("starlette.requests.Request.is_disconnected", mock_is_disconnected):
            with client.stream("GET", "/api/events") as resp:
                assert resp.status_code == 200

    def test_sql_injection_in_db_param(self) -> None:
        app = _make_app(with_event_bus=False)
        client = TestClient(app)
        resp = client.get("/api/events?db=test%20OR%201=1")
        assert resp.status_code == 400

    def test_accepts_valid_conversation_id(self) -> None:
        import uuid

        app = _make_app(with_event_bus=False)
        client = TestClient(app)
        conv_id = str(uuid.uuid4())
        with client.stream("GET", f"/api/events?conversation_id={conv_id}") as resp:
            assert resp.status_code == 200

    def test_event_stream_client_id_validation_empty(self) -> None:
        """Verify empty X-Client-Id header is accepted."""
        app = _make_app(with_event_bus=False)
        client = TestClient(app)
        with client.stream("GET", "/api/events", headers={"X-Client-Id": ""}) as resp:
            assert resp.status_code == 200

    def test_event_stream_client_id_validation_invalid(self) -> None:
        """Verify non-UUID X-Client-Id is accepted (not validated by backend)."""
        app = _make_app(with_event_bus=False)
        client = TestClient(app)
        with client.stream("GET", "/api/events", headers={"X-Client-Id": "not-a-uuid"}) as resp:
            assert resp.status_code == 200


class TestEventStreamDelivery:
    """SSE event delivery from queues."""

    def test_unsubscribe_called_on_disconnect(self) -> None:
        """Verify unsubscribe is called for global channel on cleanup."""
        app = _make_app(with_event_bus=True)
        event_bus = app.state.event_bus
        mock_queue = MagicMock()
        mock_queue.get_nowait.side_effect = asyncio.QueueEmpty()
        event_bus.subscribe.return_value = mock_queue

        async def mock_disconnect(self):
            return True

        client = TestClient(app)
        with patch("starlette.requests.Request.is_disconnected", mock_disconnect):
            with client.stream("GET", "/api/events") as resp:
                assert resp.status_code == 200
                list(resp.iter_lines())

        event_bus.unsubscribe.assert_called()

    def test_conv_channel_subscribed_when_id_provided(self) -> None:
        """Verify dual-channel subscription when conversation_id is given."""
        conv_id = str(uuid.uuid4())
        app = _make_app(with_event_bus=True)
        event_bus = app.state.event_bus
        mock_queue = MagicMock()
        mock_queue.get_nowait.side_effect = asyncio.QueueEmpty()
        event_bus.subscribe.return_value = mock_queue

        async def mock_disconnect(self):
            return True

        client = TestClient(app)
        with patch("starlette.requests.Request.is_disconnected", mock_disconnect):
            with client.stream("GET", f"/api/events?conversation_id={conv_id}") as resp:
                assert resp.status_code == 200
                list(resp.iter_lines())

        # Should have subscribed to both global and conversation channels
        assert event_bus.subscribe.call_count == 2
        channels = [call.args[0] for call in event_bus.subscribe.call_args_list]
        assert "global:personal" in channels
        assert f"conversation:{conv_id}" in channels


class TestSSERetryField:
    """Verify the retry: field is sent to control browser reconnection interval."""

    def test_error_event_includes_retry_field(self) -> None:
        """When event_bus is absent, the error SSE event should include retry:."""
        app = _make_app(with_event_bus=False, sse_retry_ms=5000)
        client = TestClient(app)
        with client.stream("GET", "/api/events") as resp:
            assert resp.status_code == 200
            lines = list(resp.iter_lines())
        retry_lines = [ln for ln in lines if ln.startswith("retry:")]
        assert len(retry_lines) >= 1
        assert "retry: 5000" in retry_lines[0] or "retry:5000" in retry_lines[0]

    def test_connected_event_includes_retry_field(self) -> None:
        """When event_bus is present, the initial connected event should
        carry the retry: field unconditionally."""
        app = _make_app(with_event_bus=True, sse_retry_ms=3000)
        event_bus = app.state.event_bus
        mock_queue = MagicMock()
        mock_queue.get_nowait.side_effect = asyncio.QueueEmpty()
        event_bus.subscribe.return_value = mock_queue

        async def mock_disconnect(self: object) -> bool:
            return True

        client = TestClient(app)
        with patch("starlette.requests.Request.is_disconnected", mock_disconnect):
            with client.stream("GET", "/api/events") as resp:
                assert resp.status_code == 200
                lines = list(resp.iter_lines())

        retry_lines = [ln for ln in lines if ln.startswith("retry:")]
        assert len(retry_lines) >= 1
        assert "3000" in retry_lines[0]

    def test_custom_retry_ms_from_config(self) -> None:
        """Verify the retry field uses the configured value."""
        app = _make_app(with_event_bus=False, sse_retry_ms=10000)
        client = TestClient(app)
        with client.stream("GET", "/api/events") as resp:
            lines = list(resp.iter_lines())
        retry_lines = [ln for ln in lines if ln.startswith("retry:")]
        assert any("10000" in ln for ln in retry_lines)
