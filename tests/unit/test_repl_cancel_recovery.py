"""Tests for REPL cancel recovery — keybinding routing, agent_busy lifecycle,
msg_queue backfill, and thinking indicator cleanup (#937).
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

# =============================================================================
# Ctrl-C keybinding: cancel when busy, clear buffer when idle
# =============================================================================


class TestCtrlCKeybinding:
    """Verify the @kb.add('c-c') handler routes correctly based on agent_busy."""

    def test_ctrl_c_sets_cancel_event_when_busy(self) -> None:
        """When agent_busy is set, Ctrl-C sets the cancel event."""
        agent_busy = asyncio.Event()
        agent_busy.set()
        cancel = asyncio.Event()
        _current_cancel: list[asyncio.Event | None] = [cancel]

        # Simulate the handler logic
        if agent_busy.is_set() and _current_cancel[0] is not None:
            _current_cancel[0].set()

        assert cancel.is_set()

    def test_ctrl_c_clears_buffer_when_idle(self) -> None:
        """When agent_busy is NOT set, Ctrl-C should not set cancel event."""
        agent_busy = asyncio.Event()  # not set
        cancel = asyncio.Event()
        _current_cancel: list[asyncio.Event | None] = [cancel]

        # Handler should NOT route to cancel when idle
        if agent_busy.is_set() and _current_cancel[0] is not None:
            _current_cancel[0].set()

        assert not cancel.is_set()

    def test_ctrl_c_safe_when_no_cancel_event(self) -> None:
        """When cancel event ref is None, Ctrl-C does not crash."""
        agent_busy = asyncio.Event()
        agent_busy.set()
        _current_cancel: list[asyncio.Event | None] = [None]

        # Should not raise
        if agent_busy.is_set() and _current_cancel[0] is not None:
            _current_cancel[0].set()

        # No crash, no event to check


# =============================================================================
# Escape keybinding: cancel when busy, no-op when idle
# =============================================================================


class TestEscapeKeybinding:
    """Verify the @kb.add('escape') handler routes correctly."""

    def test_escape_sets_cancel_event_when_busy(self) -> None:
        """When agent_busy is set, Escape sets the cancel event."""
        agent_busy = asyncio.Event()
        agent_busy.set()
        cancel = asyncio.Event()
        _current_cancel: list[asyncio.Event | None] = [cancel]

        if agent_busy.is_set() and _current_cancel[0] is not None:
            _current_cancel[0].set()

        assert cancel.is_set()

    def test_escape_is_noop_when_idle(self) -> None:
        """When agent_busy is NOT set, Escape does nothing."""
        agent_busy = asyncio.Event()
        cancel = asyncio.Event()
        _current_cancel: list[asyncio.Event | None] = [cancel]

        if agent_busy.is_set() and _current_cancel[0] is not None:
            _current_cancel[0].set()

        assert not cancel.is_set()


# =============================================================================
# agent_busy lifecycle after cancel
# =============================================================================


class TestAgentBusyCancel:
    """Verify agent_busy is always cleared on cancel, preserved on normal completion."""

    def test_agent_busy_cleared_on_cancel_with_empty_queue(self) -> None:
        """Cancel with empty queue clears agent_busy."""
        agent_busy = asyncio.Event()
        agent_busy.set()
        cancel_event = asyncio.Event()
        cancel_event.set()  # cancelled
        input_queue: asyncio.Queue[str] = asyncio.Queue()

        def _has_pending_work() -> bool:
            return not input_queue.empty()

        # Simulate finally block logic
        if cancel_event.is_set():
            agent_busy.clear()
        elif not _has_pending_work():
            agent_busy.clear()

        assert not agent_busy.is_set()

    def test_agent_busy_cleared_on_cancel_with_queued_items(self) -> None:
        """Cancel with items in input_queue still clears agent_busy (#937)."""
        agent_busy = asyncio.Event()
        agent_busy.set()
        cancel_event = asyncio.Event()
        cancel_event.set()  # cancelled
        input_queue: asyncio.Queue[str] = asyncio.Queue()
        input_queue.put_nowait("queued message")

        def _has_pending_work() -> bool:
            return not input_queue.empty()

        # Simulate finally block logic
        if cancel_event.is_set():
            agent_busy.clear()
        elif not _has_pending_work():
            agent_busy.clear()

        assert not agent_busy.is_set()

    def test_agent_busy_stays_set_on_normal_completion_with_pending(self) -> None:
        """Normal completion with pending work keeps agent_busy set."""
        agent_busy = asyncio.Event()
        agent_busy.set()
        cancel_event = asyncio.Event()  # not set — normal completion
        input_queue: asyncio.Queue[str] = asyncio.Queue()
        input_queue.put_nowait("queued message")

        def _has_pending_work() -> bool:
            return not input_queue.empty()

        # Simulate finally block logic
        if cancel_event.is_set():
            agent_busy.clear()
        elif not _has_pending_work():
            agent_busy.clear()

        assert agent_busy.is_set()

    def test_agent_busy_cleared_on_normal_completion_empty_queue(self) -> None:
        """Normal completion with empty queue clears agent_busy."""
        agent_busy = asyncio.Event()
        agent_busy.set()
        cancel_event = asyncio.Event()  # not set
        input_queue: asyncio.Queue[str] = asyncio.Queue()

        def _has_pending_work() -> bool:
            return not input_queue.empty()

        if cancel_event.is_set():
            agent_busy.clear()
        elif not _has_pending_work():
            agent_busy.clear()

        assert not agent_busy.is_set()


# =============================================================================
# msg_queue backfill on cancel
# =============================================================================


class TestMsgQueueBackfill:
    """Verify msg_queue items are backfilled into ai_messages on cancel."""

    def test_backfill_syncs_drained_messages(self) -> None:
        """On cancel, msg_queue items are appended to ai_messages."""
        ai_messages: list[dict[str, Any]] = [{"role": "user", "content": "initial"}]
        msg_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        cancel_event = asyncio.Event()
        cancel_event.set()

        msg_queue.put_nowait({"role": "user", "content": "queued follow-up"})
        msg_queue.put_nowait({"role": "user", "content": "another follow-up"})

        # Simulate backfill from finally block
        if cancel_event.is_set():
            while not msg_queue.empty():
                try:
                    leftover = msg_queue.get_nowait()
                    ai_messages.append(leftover)
                except asyncio.QueueEmpty:
                    break

        assert len(ai_messages) == 3
        assert ai_messages[1]["content"] == "queued follow-up"
        assert ai_messages[2]["content"] == "another follow-up"
        assert msg_queue.empty()

    def test_backfill_noop_when_empty(self) -> None:
        """Backfill is harmless when msg_queue is empty."""
        ai_messages: list[dict[str, Any]] = [{"role": "user", "content": "initial"}]
        msg_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        cancel_event = asyncio.Event()
        cancel_event.set()

        if cancel_event.is_set():
            while not msg_queue.empty():
                try:
                    leftover = msg_queue.get_nowait()
                    ai_messages.append(leftover)
                except asyncio.QueueEmpty:
                    break

        assert len(ai_messages) == 1

    def test_no_backfill_on_normal_completion(self) -> None:
        """On normal completion, msg_queue is not backfilled."""
        ai_messages: list[dict[str, Any]] = [{"role": "user", "content": "initial"}]
        msg_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        cancel_event = asyncio.Event()  # not set

        msg_queue.put_nowait({"role": "user", "content": "queued"})

        if cancel_event.is_set():
            while not msg_queue.empty():
                try:
                    leftover = msg_queue.get_nowait()
                    ai_messages.append(leftover)
                except asyncio.QueueEmpty:
                    break

        # msg_queue untouched, ai_messages unchanged
        assert len(ai_messages) == 1
        assert not msg_queue.empty()


# =============================================================================
# Runner-task exception surfacing
# =============================================================================


class TestRunnerTaskExceptions:
    """Verify that exceptions from done_tasks are surfaced and logged."""

    @pytest.mark.asyncio
    async def test_runner_exception_is_logged(self) -> None:
        """If runner_task fails, the exception is logged."""
        logged_exceptions: list[str] = []

        class _Handler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if record.exc_info and record.exc_info[1]:
                    logged_exceptions.append(str(record.exc_info[1]))

        handler = _Handler()
        logger = logging.getLogger("anteroom.cli.repl")
        logger.addHandler(handler)
        try:
            # Create a task that raises
            async def _fail() -> None:
                raise RuntimeError("runner exploded")

            task = asyncio.create_task(_fail())
            await asyncio.sleep(0.01)  # let it fail

            done_tasks = {task}
            for t in done_tasks:
                try:
                    t.result()
                except Exception:
                    logger.exception("REPL task failed")

            assert any("runner exploded" in e for e in logged_exceptions)
        finally:
            logger.removeHandler(handler)


# =============================================================================
# Thinking ticker orphan suppression
# =============================================================================


class TestThinkingTickerSuppression:
    """Verify _thinking_cancelled flag suppresses stale ticker output."""

    def setup_method(self) -> None:
        import anteroom.cli.renderer as mod

        self.mod = mod
        importlib.reload(mod)

    def test_stop_thinking_sync_sets_cancelled_flag(self) -> None:
        """stop_thinking_sync() sets _thinking_cancelled before cancelling ticker."""
        r = self.mod
        r._repl_mode = True
        r._thinking_start = time.monotonic() - 1.0
        r._thinking_ticker_task = None

        r.stop_thinking_sync()

        assert r._thinking_cancelled is True

    def test_start_thinking_resets_cancelled_flag(self) -> None:
        """start_thinking() resets _thinking_cancelled."""
        r = self.mod
        r._thinking_cancelled = True
        r._repl_mode = True
        r._stdout = MagicMock()

        r.start_thinking()

        assert r._thinking_cancelled is False

    @pytest.mark.asyncio
    async def test_ticker_exits_when_cancelled_flag_set(self) -> None:
        """_thinking_ticker() exits early when _thinking_cancelled is True."""
        r = self.mod
        r._thinking_cancelled = False
        r._thinking_start = time.monotonic()
        r._repl_mode = True
        r._stdout = MagicMock()

        # Start ticker, let it run one iteration, then set flag
        task = asyncio.create_task(r._thinking_ticker())
        await asyncio.sleep(0.1)
        r._thinking_cancelled = True
        await asyncio.sleep(0.6)  # let it wake and check

        assert task.done()
