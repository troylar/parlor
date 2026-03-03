"""Regression tests for bugs found during coverage analysis (#689).

Each test proves a specific bug that was found in untested code and fixed.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Bug 1: _pending_usage was declared inside the async-for loop, resetting
#         every iteration. Token usage was never written to the DB.
#         Fix: moved declaration above the loop.
# Bug 2: tool_call_end silently dropped when current_assistant_msg was None.
#         Fix: added warning log.
# Bug 3: .clear() on tool_call_start wiped ALL canvas accumulators.
#         Fix: per-index reset via .pop()/.discard().
# ---------------------------------------------------------------------------


def _make_stream_ctx(**overrides: object) -> MagicMock:
    """Build a minimal mock StreamContext for _stream_chat_events."""
    ctx = MagicMock()
    ctx.conversation_id = "conv-1"
    ctx.uid = "user-1"
    ctx.uname = "Test"
    ctx.event_bus = None
    ctx.embedding_worker = None
    ctx.canvas_needs_approval = False
    ctx.plan_mode = False
    ctx.plan_path = None
    ctx.request = MagicMock()
    ctx.request.app.state.config.cli.max_consecutive_text_only = 3
    ctx.request.app.state.audit_writer = None
    ctx.request.app.state.dlp_scanner = None
    ctx.request.app.state.injection_detector = None
    ctx.request.app.state.config.safety.output_filter = None
    ctx.last_token_broadcast = 0
    ctx.token_throttle_interval = 999
    ctx.client_id = "client-1"
    ctx.budget_config = None
    ctx.planning_config = MagicMock()
    ctx.planning_config.auto_mode = "off"
    ctx.planning_config.auto_threshold_tools = 0
    ctx.extra_system_prompt = ""
    ctx.ai_service = MagicMock()
    ctx.ai_service.config.narration_cadence = 0
    ctx.tool_registry = MagicMock()
    ctx.tool_registry.has_tool.return_value = True
    ctx.mcp_manager = None
    ctx.prompt_meta = {}
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


def _make_event(kind: str, data: dict) -> SimpleNamespace:
    return SimpleNamespace(kind=kind, data=data)


class TestStreamChatEventsBugs:
    """Tests for chat.py streaming event handler bugs."""

    @pytest.mark.asyncio
    async def test_pending_usage_persists_across_iterations(self) -> None:
        """Bug 1: usage event followed by assistant_message must trigger
        update_message_usage — proves _pending_usage is not reset per iteration."""
        import asyncio

        from anteroom.routers.chat import _stream_chat_events

        mock_storage = MagicMock()
        mock_storage.create_message.return_value = {"id": "msg-1", "position": 1}
        ctx = _make_stream_ctx()
        ctx.cancel_event = asyncio.Event()
        ctx.db = MagicMock()

        events = [
            _make_event("usage", {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "model": "test"}),
            _make_event("assistant_message", {"content": "Hello"}),
        ]

        async def fake_agent_loop(**kwargs):
            for e in events:
                yield e

        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_loop),
            patch("anteroom.routers.chat.storage", mock_storage),
        ):
            async for _ in _stream_chat_events(ctx):
                pass

        mock_storage.update_message_usage.assert_called_once()
        args = mock_storage.update_message_usage.call_args[0]
        assert args[1:] == ("msg-1", 10, 20, 30, "test")

    @pytest.mark.asyncio
    async def test_tool_call_end_without_assistant_msg_logs_warning(self) -> None:
        """Bug 2: tool_call_end before assistant_message should log a warning."""
        from anteroom.routers.chat import _stream_chat_events

        ctx = _make_stream_ctx()
        events = [
            _make_event("tool_call_end", {"id": "tc-1", "tool_name": "bash", "output": "ok", "status": "success"}),
        ]

        async def fake_agent_loop(**kwargs):
            for e in events:
                yield e

        mock_storage = MagicMock()
        with (
            patch("anteroom.services.agent_loop.run_agent_loop", side_effect=fake_agent_loop),
            patch("anteroom.routers.chat.storage", mock_storage),
            patch("anteroom.routers.chat.logger") as mock_logger,
        ):
            async for _ in _stream_chat_events(ctx):
                pass

        mock_logger.warning.assert_called_once()
        assert "tool_call_end received before assistant_message" in mock_logger.warning.call_args[0][0]
        mock_storage.create_tool_call.assert_not_called()


# ---------------------------------------------------------------------------
# Bug 4: except Exception: pass swallowed HTTPException(409) in add_database.
#         Fix: re-raise HTTPException before the bare except.
# ---------------------------------------------------------------------------


class TestConfigApiDuplicateDatabase:
    """Bug 4: duplicate database name check was broken."""

    def _make_app(self) -> Any:
        from fastapi import FastAPI

        from anteroom.routers.config_api import router

        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.state.config = MagicMock()
        app.state.config.identity = None
        db_manager = MagicMock()
        db_manager.list_databases.return_value = [
            {"name": "personal", "path": "/tmp/p.db"},
            {"name": "existing-db", "path": "/tmp/e.db"},
        ]
        app.state.db_manager = db_manager
        return app

    def test_duplicate_database_returns_409(self) -> None:
        from fastapi.testclient import TestClient

        app = self._make_app()
        client = TestClient(app)
        resp = client.post(
            "/api/databases",
            json={"name": "existing-db", "path": "/tmp/new.db"},
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Bug 5: _run_usage passed directory path to init_db instead of chat.db.
#         Fix: changed to config.app.data_dir / "chat.db".
# ---------------------------------------------------------------------------


class TestRunUsageDbPath:
    """Bug 5: aroom usage must open chat.db, not the directory."""

    def test_run_usage_calls_init_db_with_chat_db(self) -> None:
        """Source-level check: every init_db call in __main__.py uses / 'chat.db'."""
        import anteroom.__main__ as main_mod

        source = inspect.getsource(main_mod)
        lines = source.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if "init_db(" in stripped and "config.app.data_dir" in stripped:
                assert "chat.db" in stripped, (
                    f"Bug 5 regression at line {i}: init_db called with directory path instead of chat.db: {stripped}"
                )


# ---------------------------------------------------------------------------
# Bug 6: rm with separated flags (rm -v -r -f /) bypassed hard-block.
#         Fix: expanded regex to handle arbitrary flag sequences.
# ---------------------------------------------------------------------------


class TestRmSeparatedFlagsBlocked:
    """Bug 6: rm with separated flags must be hard-blocked."""

    @pytest.fixture(autouse=True)
    def _import_security(self) -> None:
        from anteroom.tools.security import check_hard_block

        self.check_hard_block = check_hard_block

    @pytest.mark.parametrize(
        "cmd",
        [
            "rm -rf /",
            "rm -fr /tmp",
            "rm -r -f /",
            "rm -f -r /",
            "rm -v -r -f /",
            "rm -v -f -r /home",
            "rm --verbose -rf /",
        ],
    )
    def test_rm_rf_variants_blocked(self, cmd: str) -> None:
        result = self.check_hard_block(cmd)
        assert result is not None, f"Expected '{cmd}' to be hard-blocked but it was allowed"

    @pytest.mark.parametrize(
        "cmd",
        [
            "rm file.txt",
            "rm -f file.txt",
            "rm -r empty_dir",
            "rm -i file.txt",
        ],
    )
    def test_safe_rm_not_blocked(self, cmd: str) -> None:
        result = self.check_hard_block(cmd)
        assert result is None, f"Expected '{cmd}' to be allowed but it was blocked: {result}"
