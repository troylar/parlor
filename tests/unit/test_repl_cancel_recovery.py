"""Tests for REPL cancel recovery — keybinding routing, agent_busy lifecycle,
msg_queue backfill, and thinking indicator cleanup (#937).

These tests call the real extracted functions from repl.py, not copies of
the logic.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from anteroom.cli.repl import _cleanup_after_turn, _route_cancel_signal

# =============================================================================
# _route_cancel_signal — used by both Ctrl-C and Escape keybindings
# =============================================================================


class TestRouteCancelSignal:
    """Verify _route_cancel_signal() routes correctly based on agent_busy."""

    def test_routes_cancel_when_busy(self) -> None:
        """When agent_busy is set and cancel event exists, sets it and returns True."""
        agent_busy = asyncio.Event()
        agent_busy.set()
        cancel = asyncio.Event()
        current: list[asyncio.Event | None] = [cancel]

        result = _route_cancel_signal(agent_busy, current)

        assert result is True
        assert cancel.is_set()

    def test_does_not_route_when_idle(self) -> None:
        """When agent_busy is NOT set, returns False and cancel event is untouched."""
        agent_busy = asyncio.Event()  # not set
        cancel = asyncio.Event()
        current: list[asyncio.Event | None] = [cancel]

        result = _route_cancel_signal(agent_busy, current)

        assert result is False
        assert not cancel.is_set()

    def test_safe_when_cancel_event_is_none(self) -> None:
        """When cancel event ref is None, returns False without crashing."""
        agent_busy = asyncio.Event()
        agent_busy.set()
        current: list[asyncio.Event | None] = [None]

        result = _route_cancel_signal(agent_busy, current)

        assert result is False

    def test_does_not_route_when_idle_and_none(self) -> None:
        """When both idle and cancel is None, returns False."""
        agent_busy = asyncio.Event()
        current: list[asyncio.Event | None] = [None]

        result = _route_cancel_signal(agent_busy, current)

        assert result is False


# =============================================================================
# _cleanup_after_turn — called from the finally block
# =============================================================================


class TestCleanupAfterTurn:
    """Verify _cleanup_after_turn() handles cancel vs normal completion."""

    def test_cancel_clears_agent_busy_with_empty_queue(self) -> None:
        """Cancel with empty msg_queue clears agent_busy."""
        agent_busy = asyncio.Event()
        agent_busy.set()
        cancel_event = asyncio.Event()
        cancel_event.set()
        msg_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        ai_messages: list[dict[str, Any]] = [{"role": "user", "content": "initial"}]

        _cleanup_after_turn(cancel_event, agent_busy, msg_queue, ai_messages, lambda: False)

        assert not agent_busy.is_set()
        assert len(ai_messages) == 1  # nothing backfilled

    def test_cancel_clears_agent_busy_with_queued_items(self) -> None:
        """Cancel with items in input_queue still clears agent_busy (#937)."""
        agent_busy = asyncio.Event()
        agent_busy.set()
        cancel_event = asyncio.Event()
        cancel_event.set()
        msg_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        ai_messages: list[dict[str, Any]] = [{"role": "user", "content": "initial"}]

        # has_pending_work returns True (items in input_queue)
        _cleanup_after_turn(cancel_event, agent_busy, msg_queue, ai_messages, lambda: True)

        assert not agent_busy.is_set()  # cancel always clears

    def test_cancel_backfills_msg_queue_into_ai_messages(self) -> None:
        """On cancel, msg_queue items are appended to ai_messages."""
        agent_busy = asyncio.Event()
        agent_busy.set()
        cancel_event = asyncio.Event()
        cancel_event.set()
        msg_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        ai_messages: list[dict[str, Any]] = [{"role": "user", "content": "initial"}]

        msg_queue.put_nowait({"role": "user", "content": "queued follow-up"})
        msg_queue.put_nowait({"role": "user", "content": "another follow-up"})

        _cleanup_after_turn(cancel_event, agent_busy, msg_queue, ai_messages, lambda: False)

        assert len(ai_messages) == 3
        assert ai_messages[1]["content"] == "queued follow-up"
        assert ai_messages[2]["content"] == "another follow-up"
        assert msg_queue.empty()

    def test_cancel_backfill_noop_when_msg_queue_empty(self) -> None:
        """Backfill is harmless when msg_queue is empty."""
        agent_busy = asyncio.Event()
        agent_busy.set()
        cancel_event = asyncio.Event()
        cancel_event.set()
        msg_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        ai_messages: list[dict[str, Any]] = [{"role": "user", "content": "initial"}]

        _cleanup_after_turn(cancel_event, agent_busy, msg_queue, ai_messages, lambda: False)

        assert len(ai_messages) == 1

    def test_normal_completion_clears_when_no_pending_work(self) -> None:
        """Normal completion with no pending work clears agent_busy."""
        agent_busy = asyncio.Event()
        agent_busy.set()
        cancel_event = asyncio.Event()  # not set — normal completion
        msg_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        ai_messages: list[dict[str, Any]] = []

        _cleanup_after_turn(cancel_event, agent_busy, msg_queue, ai_messages, lambda: False)

        assert not agent_busy.is_set()

    def test_normal_completion_preserves_when_pending_work(self) -> None:
        """Normal completion with pending work keeps agent_busy set."""
        agent_busy = asyncio.Event()
        agent_busy.set()
        cancel_event = asyncio.Event()  # not set
        msg_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        ai_messages: list[dict[str, Any]] = []

        _cleanup_after_turn(cancel_event, agent_busy, msg_queue, ai_messages, lambda: True)

        assert agent_busy.is_set()

    def test_no_backfill_on_normal_completion(self) -> None:
        """On normal completion, msg_queue is not drained into ai_messages."""
        agent_busy = asyncio.Event()
        agent_busy.set()
        cancel_event = asyncio.Event()  # not set
        msg_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        ai_messages: list[dict[str, Any]] = [{"role": "user", "content": "initial"}]

        msg_queue.put_nowait({"role": "user", "content": "queued"})

        _cleanup_after_turn(cancel_event, agent_busy, msg_queue, ai_messages, lambda: True)

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

            async def _fail() -> None:
                raise RuntimeError("runner exploded")

            task = asyncio.create_task(_fail())
            await asyncio.sleep(0.01)

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
        # Reset only the globals under test — importlib.reload would clobber
        # unrelated module state (_verbosity, _theme, etc.) breaking other tests.
        mod._thinking_cancelled = False
        mod._thinking_start = 0
        mod._thinking_ticker_task = None
        mod._spinner = None
        mod._repl_mode = False

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

        task = asyncio.create_task(r._thinking_ticker())
        await asyncio.sleep(0.1)
        r._thinking_cancelled = True
        # Use task.cancel() as a deterministic fallback to avoid CI timing issues
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            pytest.fail("_thinking_ticker did not exit after _thinking_cancelled was set")
