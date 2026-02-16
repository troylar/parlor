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
