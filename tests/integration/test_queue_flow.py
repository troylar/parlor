"""Integration tests for web UI prompt queue flow.

Tests the queue routing logic and state management in chat.py.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from anteroom.routers.chat import (
    MAX_QUEUED_MESSAGES,
    _active_streams,
    _cancel_events,
    _message_queues,
)


def _stream_entry() -> dict[str, Any]:
    """Create a standard active stream metadata dict."""
    return {
        "started_at": time.monotonic(),
        "request": MagicMock(),
        "cancel_event": asyncio.Event(),
    }


@pytest.fixture(autouse=True)
def _clean_module_state():
    """Ensure module-level dicts are clean between tests."""
    _active_streams.clear()
    _message_queues.clear()
    _cancel_events.clear()
    yield
    _active_streams.clear()
    _message_queues.clear()
    _cancel_events.clear()


# =============================================================================
# Queue state management
# =============================================================================


class TestQueueStateManagement:
    def test_active_stream_flag_set_and_read(self):
        """Active stream flag can be set and read for a conversation."""
        cid = "00000000-0000-0000-0000-000000000001"
        _active_streams[cid] = _stream_entry()
        assert _active_streams.get(cid)

    def test_inactive_stream_returns_none(self):
        """Unset conversation returns None (falsy) — triggers normal flow."""
        cid = "00000000-0000-0000-0000-000000000002"
        assert _active_streams.get(cid) is None

    def test_queue_accepts_message(self):
        """Queue can accept and hold a message."""
        cid = "00000000-0000-0000-0000-000000000003"
        _active_streams[cid] = _stream_entry()
        _message_queues[cid] = asyncio.Queue()

        loop = asyncio.new_event_loop()
        loop.run_until_complete(_message_queues[cid].put({"role": "user", "content": "test"}))
        assert _message_queues[cid].qsize() == 1
        loop.close()

    def test_queue_fifo_order(self):
        """Messages come out of the queue in FIFO order."""
        cid = "00000000-0000-0000-0000-000000000004"
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        _message_queues[cid] = queue

        loop = asyncio.new_event_loop()
        for i in range(3):
            loop.run_until_complete(queue.put({"role": "user", "content": f"msg_{i}"}))

        results = []
        for _ in range(3):
            msg = loop.run_until_complete(queue.get())
            results.append(msg["content"])
        loop.close()

        assert results == ["msg_0", "msg_1", "msg_2"]

    def test_queue_at_max_capacity(self):
        """Queue can hold exactly MAX_QUEUED_MESSAGES items."""
        cid = "00000000-0000-0000-0000-000000000005"
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        _message_queues[cid] = queue

        loop = asyncio.new_event_loop()
        for i in range(MAX_QUEUED_MESSAGES):
            loop.run_until_complete(queue.put({"role": "user", "content": f"msg {i}"}))

        assert queue.qsize() == MAX_QUEUED_MESSAGES
        loop.close()

    def test_queue_size_check_at_boundary(self):
        """qsize() accurately reflects count for boundary checking."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        loop = asyncio.new_event_loop()

        # At MAX - 1: should be allowed
        for i in range(MAX_QUEUED_MESSAGES - 1):
            loop.run_until_complete(queue.put({"role": "user", "content": f"msg {i}"}))
        assert queue.qsize() < MAX_QUEUED_MESSAGES

        # At MAX: should be rejected
        loop.run_until_complete(queue.put({"role": "user", "content": "last"}))
        assert queue.qsize() >= MAX_QUEUED_MESSAGES
        loop.close()


# =============================================================================
# Cleanup behavior
# =============================================================================


class TestCleanup:
    def test_cleanup_removes_active_stream(self):
        """Active stream flag is removed during cleanup."""
        cid = "00000000-0000-0000-0000-000000000010"
        _active_streams[cid] = _stream_entry()

        _active_streams.pop(cid, None)
        assert cid not in _active_streams

    def test_cleanup_removes_empty_queue(self):
        """Empty queue is removed during cleanup."""
        cid = "00000000-0000-0000-0000-000000000011"
        _message_queues[cid] = asyncio.Queue()

        queue = _message_queues.get(cid)
        if queue and queue.empty():
            _message_queues.pop(cid, None)

        assert cid not in _message_queues

    def test_cleanup_preserves_nonempty_queue(self):
        """Non-empty queue is NOT removed during cleanup."""
        cid = "00000000-0000-0000-0000-000000000012"
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        _message_queues[cid] = queue

        loop = asyncio.new_event_loop()
        loop.run_until_complete(queue.put({"role": "user", "content": "pending"}))
        loop.close()

        # Simulate cleanup logic from event_generator finally block
        q = _message_queues.get(cid)
        if q and q.empty():
            _message_queues.pop(cid, None)

        # Queue should still be there because it's not empty
        assert cid in _message_queues
        assert _message_queues[cid].qsize() == 1

    def test_cleanup_removes_cancel_event(self):
        """Cancel event is discarded from the set during cleanup."""
        cid = "00000000-0000-0000-0000-000000000013"
        event1 = asyncio.Event()
        event2 = asyncio.Event()
        _cancel_events[cid].add(event1)
        _cancel_events[cid].add(event2)

        # Simulate cleanup for event1
        _cancel_events.get(cid, set()).discard(event1)
        assert event1 not in _cancel_events[cid]
        assert event2 in _cancel_events[cid]

    def test_cleanup_removes_empty_cancel_set(self):
        """Empty cancel events set is cleaned up."""
        cid = "00000000-0000-0000-0000-000000000014"
        event = asyncio.Event()
        _cancel_events[cid].add(event)

        _cancel_events[cid].discard(event)
        if not _cancel_events.get(cid):
            _cancel_events.pop(cid, None)

        assert cid not in _cancel_events

    def test_cleanup_idempotent(self):
        """Cleanup of non-existent keys doesn't error."""
        cid = "00000000-0000-0000-0000-000000000015"

        # All should be no-ops
        _active_streams.pop(cid, None)
        queue = _message_queues.get(cid)
        if queue and queue.empty():
            _message_queues.pop(cid, None)
        _cancel_events.get(cid, set()).discard(asyncio.Event())
        if not _cancel_events.get(cid):
            _cancel_events.pop(cid, None)

        # No errors, no entries
        assert cid not in _active_streams
        assert cid not in _message_queues

    def test_full_lifecycle_cleanup(self):
        """Full lifecycle: create state -> cleanup -> verify clean."""
        cid = "00000000-0000-0000-0000-000000000016"
        cancel = asyncio.Event()

        # Setup (what chat() does)
        _active_streams[cid] = _stream_entry()
        _message_queues[cid] = asyncio.Queue()
        _cancel_events[cid].add(cancel)

        # Verify setup
        assert _active_streams[cid]
        assert _message_queues[cid] is not None
        assert cancel in _cancel_events[cid]

        # Cleanup (what event_generator finally block does)
        _active_streams.pop(cid, None)
        queue = _message_queues.get(cid)
        if queue and queue.empty():
            _message_queues.pop(cid, None)
        _cancel_events.get(cid, set()).discard(cancel)
        if not _cancel_events.get(cid):
            _cancel_events.pop(cid, None)

        # Verify clean
        assert cid not in _active_streams
        assert cid not in _message_queues
        assert cid not in _cancel_events


# =============================================================================
# Concurrent conversation isolation
# =============================================================================


class TestConversationIsolation:
    def test_separate_queues_per_conversation(self):
        """Each conversation has its own independent queue."""
        cid_a = "00000000-0000-0000-0000-000000000020"
        cid_b = "00000000-0000-0000-0000-000000000021"

        _message_queues[cid_a] = asyncio.Queue()
        _message_queues[cid_b] = asyncio.Queue()

        loop = asyncio.new_event_loop()
        loop.run_until_complete(_message_queues[cid_a].put({"role": "user", "content": "for A"}))
        loop.close()

        assert _message_queues[cid_a].qsize() == 1
        assert _message_queues[cid_b].qsize() == 0

    def test_separate_active_stream_flags(self):
        """Active stream for one conversation doesn't affect another."""
        cid_a = "00000000-0000-0000-0000-000000000022"
        cid_b = "00000000-0000-0000-0000-000000000023"

        _active_streams[cid_a] = _stream_entry()
        assert _active_streams.get(cid_a)
        assert _active_streams.get(cid_b) is None

    def test_cleanup_one_doesnt_affect_other(self):
        """Cleaning up one conversation's state doesn't touch another's."""
        cid_a = "00000000-0000-0000-0000-000000000024"
        cid_b = "00000000-0000-0000-0000-000000000025"

        _active_streams[cid_a] = _stream_entry()
        _active_streams[cid_b] = _stream_entry()
        _message_queues[cid_a] = asyncio.Queue()
        _message_queues[cid_b] = asyncio.Queue()

        # Cleanup A
        _active_streams.pop(cid_a, None)
        q = _message_queues.get(cid_a)
        if q and q.empty():
            _message_queues.pop(cid_a, None)

        # B should be unaffected
        assert cid_a not in _active_streams
        assert cid_b in _active_streams
        assert cid_a not in _message_queues
        assert cid_b in _message_queues


# =============================================================================
# Queue routing logic (simulated endpoint behavior)
# =============================================================================


class TestQueueRouting:
    def test_routes_to_queue_when_active(self):
        """When stream is active, new message should be routed to queue."""
        cid = "00000000-0000-0000-0000-000000000030"
        _active_streams[cid] = _stream_entry()
        _message_queues[cid] = asyncio.Queue()

        # Simulate the routing check from chat()
        should_queue = not False and _active_streams.get(cid)  # not regenerate and active
        assert should_queue

    def test_routes_to_stream_when_inactive(self):
        """When no stream is active, should proceed with normal SSE flow."""
        cid = "00000000-0000-0000-0000-000000000031"
        should_queue = not False and _active_streams.get(cid)
        assert not should_queue

    def test_regenerate_bypasses_queue(self):
        """Regenerate requests bypass queue even when stream is active."""
        cid = "00000000-0000-0000-0000-000000000032"
        _active_streams[cid] = _stream_entry()
        _message_queues[cid] = asyncio.Queue()

        regenerate = True
        should_queue = not regenerate and _active_streams.get(cid)
        assert not should_queue

    def test_queue_full_check(self):
        """Queue at max capacity should trigger rejection."""
        cid = "00000000-0000-0000-0000-000000000033"
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        _message_queues[cid] = queue

        loop = asyncio.new_event_loop()
        for i in range(MAX_QUEUED_MESSAGES):
            loop.run_until_complete(queue.put({"role": "user", "content": f"msg {i}"}))
        loop.close()

        # This is the check from chat()
        is_full = queue.qsize() >= MAX_QUEUED_MESSAGES
        assert is_full

    def test_queue_created_if_missing(self):
        """Queue is created on-demand if it doesn't exist yet."""
        cid = "00000000-0000-0000-0000-000000000034"
        _active_streams[cid] = _stream_entry()

        # Simulate the queue creation logic from chat()
        queue = _message_queues.get(cid)
        if queue is None:
            queue = asyncio.Queue()
            _message_queues[cid] = queue

        assert cid in _message_queues
        assert isinstance(_message_queues[cid], asyncio.Queue)

    def test_position_reflects_queue_size(self):
        """Response position should reflect queue size after put."""
        cid = "00000000-0000-0000-0000-000000000035"
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        _message_queues[cid] = queue

        loop = asyncio.new_event_loop()
        loop.run_until_complete(queue.put({"role": "user", "content": "first"}))
        pos1 = queue.qsize()
        loop.run_until_complete(queue.put({"role": "user", "content": "second"}))
        pos2 = queue.qsize()
        loop.close()

        assert pos1 == 1
        assert pos2 == 2
