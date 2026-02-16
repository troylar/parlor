"""Tests for CLI input drain logic — command filtering during streaming."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from anteroom.cli.repl import _EXIT_COMMANDS, _drain_input_to_msg_queue


@pytest.fixture
def drain_env():
    """Provide common objects for drain tests."""
    input_queue: asyncio.Queue[str] = asyncio.Queue()
    msg_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    cancel_event = asyncio.Event()
    exit_flag = asyncio.Event()
    warnings: list[str] = []

    def warn_cb(cmd: str) -> None:
        warnings.append(cmd)

    return {
        "input_queue": input_queue,
        "msg_queue": msg_queue,
        "cancel_event": cancel_event,
        "exit_flag": exit_flag,
        "warn_cb": warn_cb,
        "warnings": warnings,
    }


# =============================================================================
# Command filtering
# =============================================================================


class TestCommandFiltering:
    @pytest.mark.asyncio
    async def test_normal_message_passes_through(self, drain_env, tmp_path):
        """Normal text is queued as a user message."""
        db = MagicMock()
        drain_env["input_queue"].put_nowait("Hello, help me with code")

        with patch("anteroom.services.storage.create_message"):
            await _drain_input_to_msg_queue(
                drain_env["input_queue"],
                drain_env["msg_queue"],
                str(tmp_path),
                db,
                "conv-123",
                drain_env["cancel_event"],
                drain_env["exit_flag"],
                warn_callback=drain_env["warn_cb"],
            )

        assert drain_env["msg_queue"].qsize() == 1
        msg = drain_env["msg_queue"].get_nowait()
        assert msg["role"] == "user"
        assert msg["content"] == "Hello, help me with code"
        assert not drain_env["cancel_event"].is_set()
        assert not drain_env["exit_flag"].is_set()
        assert drain_env["warnings"] == []

    @pytest.mark.asyncio
    async def test_quit_command_triggers_exit(self, drain_env, tmp_path):
        """/quit sets cancel_event and exit_flag, nothing queued."""
        db = MagicMock()
        drain_env["input_queue"].put_nowait("/quit")

        with patch("anteroom.services.storage.create_message"):
            await _drain_input_to_msg_queue(
                drain_env["input_queue"],
                drain_env["msg_queue"],
                str(tmp_path),
                db,
                "conv-123",
                drain_env["cancel_event"],
                drain_env["exit_flag"],
                warn_callback=drain_env["warn_cb"],
            )

        assert drain_env["cancel_event"].is_set()
        assert drain_env["exit_flag"].is_set()
        assert drain_env["msg_queue"].empty()

    @pytest.mark.asyncio
    async def test_exit_command_triggers_exit(self, drain_env, tmp_path):
        """/exit is equivalent to /quit."""
        db = MagicMock()
        drain_env["input_queue"].put_nowait("/exit")

        with patch("anteroom.services.storage.create_message"):
            await _drain_input_to_msg_queue(
                drain_env["input_queue"],
                drain_env["msg_queue"],
                str(tmp_path),
                db,
                "conv-123",
                drain_env["cancel_event"],
                drain_env["exit_flag"],
                warn_callback=drain_env["warn_cb"],
            )

        assert drain_env["cancel_event"].is_set()
        assert drain_env["exit_flag"].is_set()

    @pytest.mark.asyncio
    async def test_other_command_ignored_with_warning(self, drain_env, tmp_path):
        """/new, /help, etc. are ignored and produce a warning."""
        db = MagicMock()
        drain_env["input_queue"].put_nowait("/new")
        drain_env["input_queue"].put_nowait("/help")

        with patch("anteroom.services.storage.create_message"):
            await _drain_input_to_msg_queue(
                drain_env["input_queue"],
                drain_env["msg_queue"],
                str(tmp_path),
                db,
                "conv-123",
                drain_env["cancel_event"],
                drain_env["exit_flag"],
                warn_callback=drain_env["warn_cb"],
            )

        assert drain_env["msg_queue"].empty()
        assert drain_env["warnings"] == ["/new", "/help"]
        assert not drain_env["cancel_event"].is_set()

    @pytest.mark.asyncio
    async def test_command_case_insensitive(self, drain_env, tmp_path):
        """/QUIT and /Quit are treated the same as /quit."""
        db = MagicMock()
        drain_env["input_queue"].put_nowait("/QUIT")

        with patch("anteroom.services.storage.create_message"):
            await _drain_input_to_msg_queue(
                drain_env["input_queue"],
                drain_env["msg_queue"],
                str(tmp_path),
                db,
                "conv-123",
                drain_env["cancel_event"],
                drain_env["exit_flag"],
                warn_callback=drain_env["warn_cb"],
            )

        assert drain_env["cancel_event"].is_set()
        assert drain_env["exit_flag"].is_set()

    @pytest.mark.asyncio
    async def test_command_with_args_recognized(self, drain_env, tmp_path):
        """/model gpt-4 should be recognized as /model command."""
        db = MagicMock()
        drain_env["input_queue"].put_nowait("/model gpt-4")

        with patch("anteroom.services.storage.create_message"):
            await _drain_input_to_msg_queue(
                drain_env["input_queue"],
                drain_env["msg_queue"],
                str(tmp_path),
                db,
                "conv-123",
                drain_env["cancel_event"],
                drain_env["exit_flag"],
                warn_callback=drain_env["warn_cb"],
            )

        assert drain_env["msg_queue"].empty()
        assert drain_env["warnings"] == ["/model"]


# =============================================================================
# Mixed input ordering
# =============================================================================


class TestMixedInput:
    @pytest.mark.asyncio
    async def test_messages_before_quit_are_queued(self, drain_env, tmp_path):
        """Messages before /quit are queued, /quit stops drain, messages after are left."""
        db = MagicMock()
        drain_env["input_queue"].put_nowait("first message")
        drain_env["input_queue"].put_nowait("second message")
        drain_env["input_queue"].put_nowait("/quit")
        drain_env["input_queue"].put_nowait("should not be processed")

        with patch("anteroom.services.storage.create_message"):
            await _drain_input_to_msg_queue(
                drain_env["input_queue"],
                drain_env["msg_queue"],
                str(tmp_path),
                db,
                "conv-123",
                drain_env["cancel_event"],
                drain_env["exit_flag"],
                warn_callback=drain_env["warn_cb"],
            )

        assert drain_env["msg_queue"].qsize() == 2
        assert drain_env["cancel_event"].is_set()
        # The message after /quit is still in input_queue
        assert not drain_env["input_queue"].empty()
        remaining = drain_env["input_queue"].get_nowait()
        assert remaining == "should not be processed"

    @pytest.mark.asyncio
    async def test_ignored_commands_dont_block_messages(self, drain_env, tmp_path):
        """Commands between messages are skipped, messages still queued."""
        db = MagicMock()
        drain_env["input_queue"].put_nowait("msg 1")
        drain_env["input_queue"].put_nowait("/tools")
        drain_env["input_queue"].put_nowait("msg 2")
        drain_env["input_queue"].put_nowait("/help")
        drain_env["input_queue"].put_nowait("msg 3")

        with patch("anteroom.services.storage.create_message"):
            await _drain_input_to_msg_queue(
                drain_env["input_queue"],
                drain_env["msg_queue"],
                str(tmp_path),
                db,
                "conv-123",
                drain_env["cancel_event"],
                drain_env["exit_flag"],
                warn_callback=drain_env["warn_cb"],
            )

        assert drain_env["msg_queue"].qsize() == 3
        contents = []
        while not drain_env["msg_queue"].empty():
            contents.append(drain_env["msg_queue"].get_nowait()["content"])
        assert contents == ["msg 1", "msg 2", "msg 3"]
        assert drain_env["warnings"] == ["/tools", "/help"]

    @pytest.mark.asyncio
    async def test_empty_input_queue_is_noop(self, drain_env, tmp_path):
        """Empty input queue does nothing."""
        db = MagicMock()

        with patch("anteroom.services.storage.create_message"):
            await _drain_input_to_msg_queue(
                drain_env["input_queue"],
                drain_env["msg_queue"],
                str(tmp_path),
                db,
                "conv-123",
                drain_env["cancel_event"],
                drain_env["exit_flag"],
                warn_callback=drain_env["warn_cb"],
            )

        assert drain_env["msg_queue"].empty()
        assert not drain_env["cancel_event"].is_set()

    @pytest.mark.asyncio
    async def test_no_warn_callback_still_works(self, drain_env, tmp_path):
        """warn_callback=None doesn't crash when commands are filtered."""
        db = MagicMock()
        drain_env["input_queue"].put_nowait("/tools")
        drain_env["input_queue"].put_nowait("real message")

        with patch("anteroom.services.storage.create_message"):
            await _drain_input_to_msg_queue(
                drain_env["input_queue"],
                drain_env["msg_queue"],
                str(tmp_path),
                db,
                "conv-123",
                drain_env["cancel_event"],
                drain_env["exit_flag"],
                warn_callback=None,
            )

        assert drain_env["msg_queue"].qsize() == 1

    @pytest.mark.asyncio
    async def test_storage_called_for_each_message(self, drain_env, tmp_path):
        """storage.create_message is called once per queued message."""
        db = MagicMock()
        drain_env["input_queue"].put_nowait("msg A")
        drain_env["input_queue"].put_nowait("/new")  # ignored
        drain_env["input_queue"].put_nowait("msg B")

        with patch("anteroom.services.storage.create_message") as mock_create:
            await _drain_input_to_msg_queue(
                drain_env["input_queue"],
                drain_env["msg_queue"],
                str(tmp_path),
                db,
                "conv-123",
                drain_env["cancel_event"],
                drain_env["exit_flag"],
                warn_callback=drain_env["warn_cb"],
            )

        assert mock_create.call_count == 2
        calls = mock_create.call_args_list
        assert calls[0].args == (db, "conv-123", "user", "msg A")
        assert calls[1].args == (db, "conv-123", "user", "msg B")


# =============================================================================
# Exit commands constant
# =============================================================================


class TestExitCommands:
    def test_exit_commands_contains_quit_and_exit(self):
        """_EXIT_COMMANDS contains both /quit and /exit."""
        assert "/quit" in _EXIT_COMMANDS
        assert "/exit" in _EXIT_COMMANDS

    def test_exit_commands_is_frozen(self):
        """_EXIT_COMMANDS is immutable."""
        assert isinstance(_EXIT_COMMANDS, frozenset)


# =============================================================================
# Escape key cancellation via prompt_toolkit key binding
# =============================================================================


class TestEscapeCancellation:
    """Test the Escape key cancellation mechanism for the concurrent REPL.

    The actual key binding is registered inside _run_repl and uses
    prompt_toolkit's key processor. These tests verify the underlying
    cancel event contract that the binding relies on.
    """

    def test_cancel_event_set_stops_agent(self):
        """Setting the cancel event signals the agent loop to stop."""
        cancel = asyncio.Event()
        assert not cancel.is_set()
        cancel.set()
        assert cancel.is_set()

    def test_cancel_event_reference_pattern(self):
        """The mutable list pattern correctly shares cancel events."""
        _current_cancel: list[asyncio.Event | None] = [None]

        # Before agent starts: no cancel event
        assert _current_cancel[0] is None

        # Agent starts: cancel event set
        cancel = asyncio.Event()
        _current_cancel[0] = cancel
        assert _current_cancel[0] is cancel

        # Simulating Escape press: set the referenced event
        _current_cancel[0].set()
        assert cancel.is_set()

        # Agent finishes: cancel event cleared
        _current_cancel[0] = None
        assert _current_cancel[0] is None
        # Original event remains set (already fired)
        assert cancel.is_set()

    def test_cancel_event_none_prevents_crash(self):
        """If cancel event is None (no agent running), Escape handler is safe."""
        _current_cancel: list[asyncio.Event | None] = [None]

        # Simulating the Escape handler check
        ce = _current_cancel[0]
        if ce is not None:
            ce.set()
        # Should not raise — ce is None, guard prevents .set() call

    def test_agent_busy_gate(self):
        """Cancel binding only activates when agent_busy is set."""
        agent_busy = asyncio.Event()
        _current_cancel: list[asyncio.Event | None] = [None]
        cancel = asyncio.Event()
        _current_cancel[0] = cancel

        # Agent not busy: Escape should not cancel
        assert not agent_busy.is_set()

        # Agent busy: Escape should cancel
        agent_busy.set()
        assert agent_busy.is_set()
        if agent_busy.is_set():
            ce = _current_cancel[0]
            if ce is not None:
                ce.set()
        assert cancel.is_set()

    def test_escape_does_not_affect_exit_flag(self):
        """Escape cancels the current agent run but does NOT set exit_flag."""
        cancel = asyncio.Event()
        exit_flag = asyncio.Event()

        # Escape handler only sets cancel_event, not exit_flag
        cancel.set()
        assert cancel.is_set()
        assert not exit_flag.is_set()

    def test_cleanup_clears_cancel_reference(self):
        """After agent finishes, cancel reference is cleared to prevent stale refs."""
        _current_cancel: list[asyncio.Event | None] = [None]
        cancel1 = asyncio.Event()
        cancel2 = asyncio.Event()

        # First agent run
        _current_cancel[0] = cancel1
        _current_cancel[0] = None  # cleanup

        # Second agent run — should use new event, not stale first one
        _current_cancel[0] = cancel2
        _current_cancel[0].set()
        assert cancel2.is_set()
        assert not cancel1.is_set()  # first event was never set
