"""Unit tests for the event bus and db_auth modules."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from anteroom.db import DatabaseManager
from anteroom.services.event_bus import EventBus

# --- Pure in-memory pub/sub tests ---


@pytest.mark.asyncio
async def test_subscribe_and_publish():
    bus = EventBus()
    queue = bus.subscribe("test:channel")
    await bus.publish("test:channel", {"type": "hello", "data": {"msg": "world"}})
    event = queue.get_nowait()
    assert event["type"] == "hello"
    assert event["data"]["msg"] == "world"


@pytest.mark.asyncio
async def test_unsubscribe():
    bus = EventBus()
    queue = bus.subscribe("test:channel")
    bus.unsubscribe("test:channel", queue)
    await bus.publish("test:channel", {"type": "ignored"})
    assert queue.empty()


@pytest.mark.asyncio
async def test_multiple_subscribers():
    bus = EventBus()
    q1 = bus.subscribe("ch")
    q2 = bus.subscribe("ch")
    q3 = bus.subscribe("ch")
    await bus.publish("ch", {"type": "broadcast"})
    for q in [q1, q2, q3]:
        event = q.get_nowait()
        assert event["type"] == "broadcast"


@pytest.mark.asyncio
async def test_channel_isolation():
    bus = EventBus()
    q_a = bus.subscribe("channel:a")
    q_b = bus.subscribe("channel:b")
    await bus.publish("channel:a", {"type": "a_event"})
    assert q_b.qsize() == 0
    event = q_a.get_nowait()
    assert event["type"] == "a_event"
    assert q_b.empty()


@pytest.mark.asyncio
async def test_subscriber_count():
    bus = EventBus()
    assert bus.subscriber_count("ch") == 0
    q1 = bus.subscribe("ch")
    assert bus.subscriber_count("ch") == 1
    q2 = bus.subscribe("ch")
    assert bus.subscriber_count("ch") == 2
    bus.unsubscribe("ch", q1)
    assert bus.subscriber_count("ch") == 1
    bus.unsubscribe("ch", q2)
    assert bus.subscriber_count("ch") == 0


@pytest.mark.asyncio
async def test_publish_to_empty_channel():
    bus = EventBus()
    await bus.publish("empty:channel", {"type": "no_one_listening"})


@pytest.mark.asyncio
async def test_unsubscribe_nonexistent():
    bus = EventBus()
    q = asyncio.Queue()
    bus.unsubscribe("nonexistent", q)


@pytest.mark.asyncio
async def test_many_subscribers():
    """Verify the bus handles N concurrent subscribers (more than 2)."""
    bus = EventBus()
    queues = [bus.subscribe("multi") for _ in range(10)]
    assert bus.subscriber_count("multi") == 10

    await bus.publish("multi", {"type": "to_all"})
    for q in queues:
        event = q.get_nowait()
        assert event["type"] == "to_all"


# --- DB persistence tests ---


def _make_db_manager(tmp_path: Path) -> DatabaseManager:
    """Create a DatabaseManager with a single personal DB in a temp dir."""
    db_path = tmp_path / "test.db"
    mgr = DatabaseManager()
    mgr.add("personal", db_path)
    return mgr


@pytest.mark.asyncio
async def test_publish_persists_to_change_log(tmp_path):
    """Publishing an event should INSERT a row into change_log."""
    mgr = _make_db_manager(tmp_path)
    bus = EventBus()
    bus._db_manager = mgr

    await bus.publish("global:personal", {"type": "conversation_created", "data": {"id": "abc"}})

    db = mgr.get("personal")
    rows = db.execute_fetchall("SELECT * FROM change_log")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "conversation_created"
    assert rows[0]["process_id"] == bus.process_id
    assert json.loads(rows[0]["payload"]) == {"id": "abc"}


@pytest.mark.asyncio
async def test_publish_without_db_manager_does_not_error():
    """Without a DB manager, publish should still deliver locally and not crash."""
    bus = EventBus()
    q = bus.subscribe("global:personal")
    await bus.publish("global:personal", {"type": "test"})
    event = q.get_nowait()
    assert event["type"] == "test"


# --- Cross-process polling tests ---


@pytest.mark.asyncio
async def test_poll_picks_up_events_from_other_process(tmp_path):
    """Simulate two processes: bus_a writes, bus_b polls and receives."""
    db_path = tmp_path / "shared.db"

    # Process A: writes an event
    mgr_a = DatabaseManager()
    mgr_a.add("personal", db_path)
    bus_a = EventBus()
    bus_a._db_manager = mgr_a

    # Process B: subscribes and polls
    mgr_b = DatabaseManager()
    mgr_b.add("personal", db_path)
    bus_b = EventBus()
    bus_b._db_manager = mgr_b
    bus_b._last_seen_ids["personal"] = 0

    q = bus_b.subscribe("global:personal")

    # A publishes (writes to change_log)
    await bus_a.publish("global:personal", {"type": "title_changed", "data": {"title": "Hello"}})

    # B polls
    await bus_b._poll_all_databases()

    event = q.get_nowait()
    assert event["type"] == "title_changed"
    assert event["data"]["title"] == "Hello"

    mgr_a.close_all()
    mgr_b.close_all()


@pytest.mark.asyncio
async def test_poll_skips_own_process_events(tmp_path):
    """A process should not receive its own events from the poller."""
    mgr = _make_db_manager(tmp_path)
    bus = EventBus()
    bus._db_manager = mgr
    bus._last_seen_ids["personal"] = 0

    q = bus.subscribe("global:personal")

    # Publish — local delivery happens, plus persists to change_log
    await bus.publish("global:personal", {"type": "test"})

    # Drain the locally-delivered event
    local_event = q.get_nowait()
    assert local_event["type"] == "test"

    # Now poll — should NOT re-deliver because same process_id
    await bus._poll_all_databases()
    assert q.empty()

    mgr.close_all()


@pytest.mark.asyncio
async def test_poll_multiple_events_ordering(tmp_path):
    """Events from another process should arrive in order."""
    db_path = tmp_path / "shared.db"

    mgr_writer = DatabaseManager()
    mgr_writer.add("personal", db_path)
    writer = EventBus()
    writer._db_manager = mgr_writer

    mgr_reader = DatabaseManager()
    mgr_reader.add("personal", db_path)
    reader = EventBus()
    reader._db_manager = mgr_reader
    reader._last_seen_ids["personal"] = 0

    q = reader.subscribe("global:personal")

    for i in range(5):
        await writer.publish("global:personal", {"type": "msg", "data": {"seq": i}})

    await reader._poll_all_databases()

    for i in range(5):
        event = q.get_nowait()
        assert event["data"]["seq"] == i

    mgr_writer.close_all()
    mgr_reader.close_all()


@pytest.mark.asyncio
async def test_cleanup_removes_old_events(tmp_path):
    """Cleanup should delete old change_log rows."""
    mgr = _make_db_manager(tmp_path)
    bus = EventBus()
    bus._db_manager = mgr

    db = mgr.get("personal")
    # Insert an old row manually
    db.execute(
        "INSERT INTO change_log (process_id, channel, event_type, payload, created_at) VALUES (?, ?, ?, ?, ?)",
        ("old-proc", "global:personal", "test", "{}", "2020-01-01T00:00:00"),
    )
    db.commit()

    # Insert a recent row via publish
    await bus.publish("global:personal", {"type": "recent"})

    rows_before = db.execute_fetchall("SELECT * FROM change_log")
    assert len(rows_before) == 2

    bus._cleanup_old_events()

    rows_after = db.execute_fetchall("SELECT * FROM change_log")
    assert len(rows_after) == 1
    assert rows_after[0]["event_type"] == "recent"

    mgr.close_all()


@pytest.mark.asyncio
async def test_start_polling_seeds_last_seen_id(tmp_path):
    """start_polling should seed last_seen_ids to current max, not zero."""
    db_path = tmp_path / "test.db"
    mgr = DatabaseManager()
    mgr.add("personal", db_path)

    db = mgr.get("personal")
    db.execute(
        "INSERT INTO change_log (process_id, channel, event_type, payload) VALUES (?, ?, ?, ?)",
        ("other", "global:personal", "old_event", "{}"),
    )
    db.commit()

    bus = EventBus()
    bus.start_polling(mgr)

    # Should have seeded to max id (1), not 0
    assert bus._last_seen_ids["personal"] >= 1

    q = bus.subscribe("global:personal")
    # Give the poll loop one tick
    await asyncio.sleep(0.1)
    # Should NOT have picked up the old event (it was before our seed)
    assert q.empty()

    bus.stop_polling()
    mgr.close_all()


# --- Additional coverage tests (issue #689) ---


@pytest.mark.asyncio
async def test_publish_queue_full_drops_event():
    """When a subscriber's queue is full, QueueFull is caught and event is dropped (lines 78-79)."""
    bus = EventBus()
    # Create a queue with capacity 1 and fill it so put_nowait raises QueueFull
    full_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)
    full_queue.put_nowait({"type": "already_here"})

    if "test:ch" not in bus._subscribers:
        bus._subscribers["test:ch"] = set()
    bus._subscribers["test:ch"].add(full_queue)

    # Should not raise even though queue is full
    await bus.publish("test:ch", {"type": "overflow"})

    # Only the original item remains
    assert full_queue.qsize() == 1
    event = full_queue.get_nowait()
    assert event["type"] == "already_here"


@pytest.mark.asyncio
async def test_persist_event_db_exception_is_swallowed(tmp_path):
    """Exception during change_log INSERT is caught and logged without raising (lines 101-102)."""
    from unittest.mock import MagicMock

    bus = EventBus()
    mock_db = MagicMock()
    mock_db.execute.side_effect = Exception("disk full")

    mock_mgr = MagicMock()
    mock_mgr.get.return_value = mock_db
    bus._db_manager = mock_mgr

    # Must not raise
    await bus.publish("global:personal", {"type": "test", "data": {}})


def test_channel_to_db_name_non_global_returns_personal():
    """Non-global channels fall back to 'personal' (line 109)."""
    bus = EventBus()
    assert bus._channel_to_db_name("conversation:abc123") == "personal"
    assert bus._channel_to_db_name("anything:else") == "personal"
    assert bus._channel_to_db_name("no_colon") == "personal"


def test_channel_to_db_name_global_extracts_db_name():
    """Global channels extract the db name after the colon."""
    bus = EventBus()
    assert bus._channel_to_db_name("global:team-alpha") == "team-alpha"
    assert bus._channel_to_db_name("global:personal") == "personal"


@pytest.mark.asyncio
async def test_start_polling_seeds_zero_on_db_exception(tmp_path):
    """If querying MAX(id) fails during start_polling, last_seen_ids defaults to 0 (lines 123-124)."""
    from unittest.mock import MagicMock

    mock_db = MagicMock()
    mock_db.execute_fetchone.side_effect = Exception("table missing")

    mock_mgr = MagicMock()
    mock_mgr.list_databases.return_value = [{"name": "personal"}]
    mock_mgr.get.return_value = mock_db

    bus = EventBus()
    bus.start_polling(mock_mgr)

    assert bus._last_seen_ids.get("personal") == 0

    bus.stop_polling()


@pytest.mark.asyncio
async def test_poll_loop_triggers_cleanup_after_threshold():
    """_poll_loop increments _cleanup_counter and calls _cleanup_old_events when threshold reached (lines 137-141)."""
    from unittest.mock import patch

    from anteroom.services.event_bus import CLEANUP_INTERVAL_SECONDS, POLL_INTERVAL_SECONDS

    bus = EventBus()
    threshold = int(CLEANUP_INTERVAL_SECONDS / POLL_INTERVAL_SECONDS)

    # Set counter just below threshold so the first iteration triggers cleanup
    bus._cleanup_counter = threshold - 1

    poll_calls = []
    cleanup_calls = []

    # Use a real asyncio.Event to stop the loop after one iteration
    stop_event = asyncio.Event()

    async def fake_sleep(_duration):
        # After first sleep, stop the loop on the next iteration by cancelling
        if len(poll_calls) > 0:
            stop_event.set()
            raise asyncio.CancelledError()

    async def fake_poll():
        poll_calls.append(True)

    with (
        patch.object(bus, "_poll_all_databases", side_effect=fake_poll),
        patch.object(bus, "_cleanup_old_events", side_effect=lambda: cleanup_calls.append(True)),
        patch("anteroom.services.event_bus.asyncio.sleep", side_effect=fake_sleep),
    ):
        await bus._poll_loop()

    assert len(poll_calls) == 1
    assert len(cleanup_calls) == 1
    assert bus._cleanup_counter == 0


@pytest.mark.asyncio
async def test_poll_loop_exception_retries_with_backoff():
    """An unexpected exception in _poll_loop is caught, logged, and retried."""
    from unittest.mock import AsyncMock, patch

    bus = EventBus()

    call_count = 0

    async def exploding_poll():
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            # Stop after 3 retries by cancelling from inside
            raise asyncio.CancelledError
        raise RuntimeError("unexpected crash")

    with (
        patch.object(bus, "_poll_all_databases", side_effect=exploding_poll),
        patch("anteroom.services.event_bus.asyncio.sleep", new_callable=AsyncMock),
    ):
        task = asyncio.ensure_future(bus._poll_loop())
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        # Should have retried multiple times instead of crashing permanently
        assert call_count >= 2

    assert call_count >= 1


@pytest.mark.asyncio
async def test_poll_all_databases_no_db_manager_returns_early():
    """_poll_all_databases exits immediately when _db_manager is None (line 149)."""
    bus = EventBus()
    assert bus._db_manager is None
    # Must not raise; just returns
    await bus._poll_all_databases()


@pytest.mark.asyncio
async def test_poll_all_databases_queue_full_is_silenced(tmp_path):
    """QueueFull during cross-process polling is silently dropped (lines 174-175)."""
    db_path = tmp_path / "shared.db"

    mgr_writer = DatabaseManager()
    mgr_writer.add("personal", db_path)
    writer = EventBus()
    writer._db_manager = mgr_writer

    mgr_reader = DatabaseManager()
    mgr_reader.add("personal", db_path)
    reader = EventBus()
    reader._db_manager = mgr_reader
    reader._last_seen_ids["personal"] = 0

    # Create a full queue and inject it as a subscriber
    full_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)
    full_queue.put_nowait({"type": "pre_filled"})
    reader._subscribers["global:personal"] = {full_queue}

    # Writer publishes from a different process
    await writer.publish("global:personal", {"type": "overflow_event", "data": {}})

    # Poll should not raise despite queue being full
    await reader._poll_all_databases()

    # Queue still has original item, overflow was silently dropped
    assert full_queue.qsize() == 1

    mgr_writer.close_all()
    mgr_reader.close_all()


@pytest.mark.asyncio
async def test_poll_all_databases_db_exception_is_logged(tmp_path):
    """DB exception during polling is caught and logged (lines 176-177)."""
    from unittest.mock import MagicMock

    mock_db = MagicMock()
    mock_db.execute_fetchall.side_effect = Exception("connection lost")

    mock_mgr = MagicMock()
    mock_mgr.list_databases.return_value = [{"name": "personal"}]
    mock_mgr.get.return_value = mock_db

    bus = EventBus()
    bus._db_manager = mock_mgr
    bus._last_seen_ids["personal"] = 0

    # Must not raise
    await bus._poll_all_databases()


def test_cleanup_old_events_no_db_manager_returns_early():
    """_cleanup_old_events exits immediately when _db_manager is None (line 182)."""
    bus = EventBus()
    assert bus._db_manager is None
    # Must not raise
    bus._cleanup_old_events()


def test_cleanup_old_events_db_exception_is_swallowed():
    """DB exception during cleanup is caught and logged (lines 189-190)."""
    from unittest.mock import MagicMock

    mock_db = MagicMock()
    mock_db.execute.side_effect = Exception("disk error")

    mock_mgr = MagicMock()
    mock_mgr.list_databases.return_value = [{"name": "personal"}]
    mock_mgr.get.return_value = mock_db

    bus = EventBus()
    bus._db_manager = mock_mgr

    # Must not raise
    bus._cleanup_old_events()


# --- db_auth tests ---


class TestDbAuth:
    def test_hash_and_verify(self):
        from anteroom.services.db_auth import hash_passphrase, verify_passphrase

        hashed = hash_passphrase("my-secret-passphrase")
        assert hashed.startswith("$argon2id$")
        assert verify_passphrase("my-secret-passphrase", hashed)
        assert not verify_passphrase("wrong-passphrase", hashed)

    def test_needs_rehash(self):
        from anteroom.services.db_auth import hash_passphrase, needs_rehash

        hashed = hash_passphrase("test")
        assert not needs_rehash(hashed)
