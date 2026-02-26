"""Tests for session store backends."""

import time

from anteroom.services.session_store import (
    MemorySessionStore,
    SQLiteSessionStore,
    create_session_store,
)


class TestMemorySessionStore:
    """Tests for MemorySessionStore."""

    def setup_method(self):
        self.store = MemorySessionStore()

    def test_create_returns_session_dict(self):
        session = self.store.create("sess-1", "10.0.0.1")
        assert session["id"] == "sess-1"
        assert session["ip_address"] == "10.0.0.1"
        assert session["user_id"] == ""
        assert "created_at" in session
        assert "last_activity_at" in session

    def test_create_with_user_id(self):
        session = self.store.create("sess-1", "10.0.0.1", user_id="user-42")
        assert session["user_id"] == "user-42"

    def test_get_existing_session(self):
        self.store.create("sess-1", "10.0.0.1")
        session = self.store.get("sess-1")
        assert session is not None
        assert session["id"] == "sess-1"

    def test_get_nonexistent_returns_none(self):
        assert self.store.get("does-not-exist") is None

    def test_get_returns_copy(self):
        self.store.create("sess-1", "10.0.0.1")
        s1 = self.store.get("sess-1")
        s2 = self.store.get("sess-1")
        assert s1 is not s2

    def test_touch_updates_last_activity(self):
        self.store.create("sess-1", "10.0.0.1")
        original = self.store.get("sess-1")["last_activity_at"]
        time.sleep(0.01)
        self.store.touch("sess-1")
        updated = self.store.get("sess-1")["last_activity_at"]
        assert updated > original

    def test_touch_nonexistent_is_noop(self):
        self.store.touch("does-not-exist")  # should not raise

    def test_delete_removes_session(self):
        self.store.create("sess-1", "10.0.0.1")
        self.store.delete("sess-1")
        assert self.store.get("sess-1") is None

    def test_delete_nonexistent_is_noop(self):
        self.store.delete("does-not-exist")  # should not raise

    def test_count_active(self):
        assert self.store.count_active() == 0
        self.store.create("sess-1", "10.0.0.1")
        self.store.create("sess-2", "10.0.0.2")
        assert self.store.count_active() == 2

    def test_cleanup_expired_idle(self):
        self.store.create("sess-1", "10.0.0.1")
        # Manually age the session
        self.store._sessions["sess-1"]["last_activity_at"] = time.time() - 100
        removed = self.store.cleanup_expired(idle_timeout=50, absolute_timeout=99999)
        assert removed == 1
        assert self.store.get("sess-1") is None

    def test_cleanup_expired_absolute(self):
        self.store.create("sess-1", "10.0.0.1")
        self.store._sessions["sess-1"]["created_at"] = time.time() - 100
        removed = self.store.cleanup_expired(idle_timeout=99999, absolute_timeout=50)
        assert removed == 1

    def test_cleanup_keeps_active_sessions(self):
        self.store.create("sess-1", "10.0.0.1")
        removed = self.store.cleanup_expired(idle_timeout=99999, absolute_timeout=99999)
        assert removed == 0
        assert self.store.get("sess-1") is not None

    def test_create_if_allowed_within_limit(self):
        assert self.store.create_if_allowed("sess-1", "10.0.0.1", max_sessions=2) is True
        assert self.store.get("sess-1") is not None

    def test_create_if_allowed_at_limit(self):
        self.store.create("sess-1", "10.0.0.1")
        assert self.store.create_if_allowed("sess-2", "10.0.0.2", max_sessions=1) is False
        assert self.store.get("sess-2") is None

    def test_create_if_allowed_unlimited(self):
        self.store.create("sess-1", "10.0.0.1")
        assert self.store.create_if_allowed("sess-2", "10.0.0.2", max_sessions=0) is True


class TestSQLiteSessionStore:
    """Tests for SQLiteSessionStore."""

    def setup_method(self):
        self.store = SQLiteSessionStore(":memory:")

    def teardown_method(self):
        self.store.close()

    def test_create_returns_session_dict(self):
        session = self.store.create("sess-1", "10.0.0.1")
        assert session["id"] == "sess-1"
        assert session["ip_address"] == "10.0.0.1"

    def test_create_with_user_id(self):
        session = self.store.create("sess-1", "10.0.0.1", user_id="user-42")
        assert session["user_id"] == "user-42"

    def test_get_existing_session(self):
        self.store.create("sess-1", "10.0.0.1")
        session = self.store.get("sess-1")
        assert session is not None
        assert session["id"] == "sess-1"

    def test_get_nonexistent_returns_none(self):
        assert self.store.get("does-not-exist") is None

    def test_touch_updates_last_activity(self):
        self.store.create("sess-1", "10.0.0.1")
        original = self.store.get("sess-1")["last_activity_at"]
        time.sleep(0.01)
        self.store.touch("sess-1")
        updated = self.store.get("sess-1")["last_activity_at"]
        assert updated > original

    def test_delete_removes_session(self):
        self.store.create("sess-1", "10.0.0.1")
        self.store.delete("sess-1")
        assert self.store.get("sess-1") is None

    def test_count_active(self):
        assert self.store.count_active() == 0
        self.store.create("sess-1", "10.0.0.1")
        self.store.create("sess-2", "10.0.0.2")
        assert self.store.count_active() == 2

    def test_cleanup_expired_idle(self):
        self.store.create("sess-1", "10.0.0.1")
        # Manually age the session
        conn = self.store._get_conn()
        conn.execute(
            "UPDATE sessions SET last_activity_at = ? WHERE id = ?",
            (time.time() - 100, "sess-1"),
        )
        conn.commit()
        removed = self.store.cleanup_expired(idle_timeout=50, absolute_timeout=99999)
        assert removed == 1

    def test_cleanup_expired_absolute(self):
        self.store.create("sess-1", "10.0.0.1")
        conn = self.store._get_conn()
        conn.execute(
            "UPDATE sessions SET created_at = ? WHERE id = ?",
            (time.time() - 100, "sess-1"),
        )
        conn.commit()
        removed = self.store.cleanup_expired(idle_timeout=99999, absolute_timeout=50)
        assert removed == 1

    def test_cleanup_keeps_active_sessions(self):
        self.store.create("sess-1", "10.0.0.1")
        removed = self.store.cleanup_expired(idle_timeout=99999, absolute_timeout=99999)
        assert removed == 0
        assert self.store.get("sess-1") is not None

    def test_create_replaces_existing(self):
        self.store.create("sess-1", "10.0.0.1", user_id="old")
        self.store.create("sess-1", "10.0.0.2", user_id="new")
        session = self.store.get("sess-1")
        assert session["user_id"] == "new"
        assert session["ip_address"] == "10.0.0.2"

    def test_close_and_reconnect(self):
        self.store.create("sess-1", "10.0.0.1")
        self.store.close()
        # Re-open triggers reconnect
        session = self.store.get("sess-1")
        # In-memory DB loses data on close, so session is None
        # This is expected for :memory: — real file DBs persist
        assert session is None


class TestCreateSessionStore:
    """Tests for the factory function."""

    def test_default_returns_memory_store(self):
        store = create_session_store("memory")
        assert isinstance(store, MemorySessionStore)

    def test_sqlite_without_data_dir_returns_memory(self):
        store = create_session_store("sqlite", "")
        assert isinstance(store, MemorySessionStore)

    def test_sqlite_with_data_dir(self, tmp_path):
        store = create_session_store("sqlite", str(tmp_path))
        assert isinstance(store, SQLiteSessionStore)
        store.close()

    def test_unknown_type_returns_memory(self):
        store = create_session_store("unknown")
        assert isinstance(store, MemorySessionStore)
