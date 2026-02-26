"""Tests for the structured audit log service."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anteroom.services.audit import (
    _GENESIS_HMAC,
    AuditEntry,
    AuditWriter,
    _compute_hmac,
    _derive_hmac_key,
    _redact_entry,
    create_audit_writer,
    verify_chain,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TEST_PRIVATE_KEY_PEM = """\
-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEIKm3jiGLj2ROYfJFzxVxV0iJq0J3XOqT5PnJPBG7vLZE
-----END PRIVATE KEY-----"""


@pytest.fixture()
def audit_dir(tmp_path: Path) -> Path:
    return tmp_path / "audit"


@pytest.fixture()
def writer(audit_dir: Path) -> AuditWriter:
    return AuditWriter(
        audit_dir,
        enabled=True,
        tamper_protection="hmac",
        private_key_pem=_TEST_PRIVATE_KEY_PEM,
        retention_days=90,
    )


@pytest.fixture()
def writer_no_hmac(audit_dir: Path) -> AuditWriter:
    return AuditWriter(
        audit_dir,
        enabled=True,
        tamper_protection="none",
    )


@pytest.fixture()
def writer_disabled(audit_dir: Path) -> AuditWriter:
    return AuditWriter(audit_dir, enabled=False)


# ---------------------------------------------------------------------------
# AuditEntry
# ---------------------------------------------------------------------------


class TestAuditEntry:
    def test_create_sets_timestamp(self) -> None:
        entry = AuditEntry.create("auth.login", "info")
        assert entry.timestamp
        assert "T" in entry.timestamp  # ISO format

    def test_create_sets_event_type(self) -> None:
        entry = AuditEntry.create("tool_calls.executed", "warning", tool_name="bash")
        assert entry.event_type == "tool_calls.executed"
        assert entry.severity == "warning"
        assert entry.tool_name == "bash"

    def test_create_with_details(self) -> None:
        entry = AuditEntry.create("auth.failure", "error", details={"reason": "bad token"})
        assert entry.details["reason"] == "bad token"

    def test_create_defaults(self) -> None:
        entry = AuditEntry.create("test.event", "info")
        assert entry.session_id == ""
        assert entry.user_id == ""
        assert entry.source_ip == ""
        assert entry.conversation_id == ""
        assert entry.tool_name == ""
        assert entry.details == {}


# ---------------------------------------------------------------------------
# HMAC key derivation and computation
# ---------------------------------------------------------------------------


class TestHmacDerivation:
    def test_derive_key_returns_32_bytes(self) -> None:
        key = _derive_hmac_key(_TEST_PRIVATE_KEY_PEM)
        assert isinstance(key, bytes)
        assert len(key) == 32

    def test_derive_key_is_deterministic(self) -> None:
        k1 = _derive_hmac_key(_TEST_PRIVATE_KEY_PEM)
        k2 = _derive_hmac_key(_TEST_PRIVATE_KEY_PEM)
        assert k1 == k2

    def test_different_keys_produce_different_hmac_keys(self) -> None:
        k1 = _derive_hmac_key("key-one")
        k2 = _derive_hmac_key("key-two")
        assert k1 != k2

    def test_compute_hmac_deterministic(self) -> None:
        key = _derive_hmac_key(_TEST_PRIVATE_KEY_PEM)
        h1 = _compute_hmac(key, b'{"event":"test"}', "genesis")
        h2 = _compute_hmac(key, b'{"event":"test"}', "genesis")
        assert h1 == h2

    def test_compute_hmac_changes_with_prev(self) -> None:
        key = _derive_hmac_key(_TEST_PRIVATE_KEY_PEM)
        h1 = _compute_hmac(key, b'{"event":"test"}', "genesis")
        h2 = _compute_hmac(key, b'{"event":"test"}', "other-prev")
        assert h1 != h2

    def test_compute_hmac_changes_with_content(self) -> None:
        key = _derive_hmac_key(_TEST_PRIVATE_KEY_PEM)
        h1 = _compute_hmac(key, b'{"event":"a"}', "genesis")
        h2 = _compute_hmac(key, b'{"event":"b"}', "genesis")
        assert h1 != h2


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_redacts_sensitive_fields(self) -> None:
        entry = {
            "event_type": "tool_calls.executed",
            "details": {
                "tool_input": "rm -rf /",
                "tool_output": "success",
                "message_content": "hello world",
                "status": "ok",
            },
        }
        redacted = _redact_entry(entry)
        assert redacted["details"]["tool_input"] == "[REDACTED]"
        assert redacted["details"]["tool_output"] == "[REDACTED]"
        assert redacted["details"]["message_content"] == "[REDACTED]"
        assert redacted["details"]["status"] == "ok"

    def test_preserves_non_sensitive_fields(self) -> None:
        entry = {"event_type": "auth.login", "details": {"source_ip": "127.0.0.1"}}
        redacted = _redact_entry(entry)
        assert redacted["details"]["source_ip"] == "127.0.0.1"

    def test_handles_empty_details(self) -> None:
        entry = {"event_type": "test", "details": {}}
        redacted = _redact_entry(entry)
        assert redacted["details"] == {}

    def test_does_not_mutate_original(self) -> None:
        entry = {"event_type": "test", "details": {"tool_input": "secret"}}
        _redact_entry(entry)
        assert entry["details"]["tool_input"] == "secret"


# ---------------------------------------------------------------------------
# AuditWriter — basic emit
# ---------------------------------------------------------------------------


class TestAuditWriterEmit:
    def test_emit_creates_log_file(self, writer: AuditWriter, audit_dir: Path) -> None:
        entry = AuditEntry.create("test.event", "info")
        writer.emit(entry)
        files = list(audit_dir.glob("audit-*.jsonl"))
        assert len(files) == 1

    def test_emit_writes_valid_json(self, writer: AuditWriter, audit_dir: Path) -> None:
        entry = AuditEntry.create("test.event", "info", details={"key": "value"})
        writer.emit(entry)
        log_file = list(audit_dir.glob("audit-*.jsonl"))[0]
        line = log_file.read_text().strip()
        parsed = json.loads(line)
        assert parsed["event_type"] == "test.event"

    def test_emit_appends_multiple_entries(self, writer: AuditWriter, audit_dir: Path) -> None:
        for i in range(5):
            writer.emit(AuditEntry.create(f"test.event_{i}", "info"))
        log_file = list(audit_dir.glob("audit-*.jsonl"))[0]
        lines = [ln for ln in log_file.read_text().strip().split("\n") if ln]
        assert len(lines) == 5

    def test_emit_includes_hmac_fields(self, writer: AuditWriter, audit_dir: Path) -> None:
        writer.emit(AuditEntry.create("test.event", "info"))
        log_file = list(audit_dir.glob("audit-*.jsonl"))[0]
        parsed = json.loads(log_file.read_text().strip())
        assert "_hmac" in parsed
        assert "_prev_hmac" in parsed

    def test_emit_first_entry_has_genesis_prev(self, writer: AuditWriter, audit_dir: Path) -> None:
        writer.emit(AuditEntry.create("test.event", "info"))
        log_file = list(audit_dir.glob("audit-*.jsonl"))[0]
        parsed = json.loads(log_file.read_text().strip())
        assert parsed["_prev_hmac"] == _GENESIS_HMAC

    def test_emit_chains_hmac(self, writer: AuditWriter, audit_dir: Path) -> None:
        writer.emit(AuditEntry.create("event.a", "info"))
        writer.emit(AuditEntry.create("event.b", "info"))
        log_file = list(audit_dir.glob("audit-*.jsonl"))[0]
        lines = log_file.read_text().strip().split("\n")
        first = json.loads(lines[0])
        second = json.loads(lines[1])
        assert second["_prev_hmac"] == first["_hmac"]

    def test_file_permissions_0600(self, writer: AuditWriter, audit_dir: Path) -> None:
        writer.emit(AuditEntry.create("test.event", "info"))
        log_file = list(audit_dir.glob("audit-*.jsonl"))[0]
        mode = log_file.stat().st_mode & 0o777
        assert mode == 0o600

    def test_dir_permissions_0700(self, writer: AuditWriter, audit_dir: Path) -> None:
        # Writer creates the dir on init
        mode = audit_dir.stat().st_mode & 0o777
        assert mode == 0o700


# ---------------------------------------------------------------------------
# AuditWriter — disabled
# ---------------------------------------------------------------------------


class TestAuditWriterDisabled:
    def test_emit_noop_when_disabled(self, writer_disabled: AuditWriter, audit_dir: Path) -> None:
        writer_disabled.emit(AuditEntry.create("test.event", "info"))
        assert not audit_dir.exists() or not list(audit_dir.glob("audit-*.jsonl"))

    def test_is_event_enabled_returns_false(self, writer_disabled: AuditWriter) -> None:
        assert not writer_disabled.is_event_enabled("auth.login")


# ---------------------------------------------------------------------------
# AuditWriter — no HMAC
# ---------------------------------------------------------------------------


class TestAuditWriterNoFcntl:
    def test_emit_works_without_fcntl(self, audit_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import anteroom.services.audit as audit_mod

        monkeypatch.setattr(audit_mod, "fcntl", None)
        w = AuditWriter(audit_dir, enabled=True, tamper_protection="none")
        w.emit(AuditEntry.create("test.nofcntl", "info"))
        log_file = list(audit_dir.glob("audit-*.jsonl"))[0]
        parsed = json.loads(log_file.read_text().strip())
        assert parsed["event_type"] == "test.nofcntl"


class TestAuditWriterNoHmac:
    def test_emit_without_hmac(self, writer_no_hmac: AuditWriter, audit_dir: Path) -> None:
        writer_no_hmac.emit(AuditEntry.create("test.event", "info"))
        log_file = list(audit_dir.glob("audit-*.jsonl"))[0]
        parsed = json.loads(log_file.read_text().strip())
        assert "_hmac" not in parsed
        assert "_prev_hmac" not in parsed


# ---------------------------------------------------------------------------
# AuditWriter — redaction
# ---------------------------------------------------------------------------


class TestAuditWriterRedaction:
    def test_emit_redacts_when_enabled(self, audit_dir: Path) -> None:
        w = AuditWriter(audit_dir, enabled=True, tamper_protection="none", redact_content=True)
        w.emit(AuditEntry.create("tool.call", "info", details={"tool_input": "secret data"}))
        log_file = list(audit_dir.glob("audit-*.jsonl"))[0]
        parsed = json.loads(log_file.read_text().strip())
        assert parsed["details"]["tool_input"] == "[REDACTED]"

    def test_emit_preserves_when_redaction_disabled(self, audit_dir: Path) -> None:
        w = AuditWriter(audit_dir, enabled=True, tamper_protection="none", redact_content=False)
        w.emit(AuditEntry.create("tool.call", "info", details={"tool_input": "secret data"}))
        log_file = list(audit_dir.glob("audit-*.jsonl"))[0]
        parsed = json.loads(log_file.read_text().strip())
        assert parsed["details"]["tool_input"] == "secret data"


# ---------------------------------------------------------------------------
# AuditWriter — event filtering
# ---------------------------------------------------------------------------


class TestAuditWriterEventFiltering:
    def test_event_enabled_by_default(self, writer: AuditWriter) -> None:
        assert writer.is_event_enabled("auth.login")

    def test_event_disabled_by_config(self, audit_dir: Path) -> None:
        w = AuditWriter(audit_dir, enabled=True, events={"auth": False})
        assert not w.is_event_enabled("auth.login")

    def test_event_enabled_by_config(self, audit_dir: Path) -> None:
        w = AuditWriter(audit_dir, enabled=True, events={"auth": True, "tool_calls": False})
        assert w.is_event_enabled("auth.login")
        assert not w.is_event_enabled("tool_calls.executed")

    def test_emit_skips_disabled_event(self, audit_dir: Path) -> None:
        w = AuditWriter(audit_dir, enabled=True, tamper_protection="none", events={"auth": False})
        w.emit(AuditEntry.create("auth.login", "info"))
        assert not list(audit_dir.glob("audit-*.jsonl"))


# ---------------------------------------------------------------------------
# AuditWriter — rotation
# ---------------------------------------------------------------------------


class TestAuditWriterRotation:
    def test_size_rotation_renames_file(self, audit_dir: Path) -> None:
        w = AuditWriter(
            audit_dir,
            enabled=True,
            tamper_protection="none",
            rotation="size",
            rotate_size_bytes=100,
        )
        # Write enough to trigger rotation
        for i in range(20):
            w.emit(AuditEntry.create(f"event.{i}", "info", details={"padding": "x" * 50}))
        files = list(audit_dir.glob("audit-*"))
        assert len(files) >= 2  # At least the current + one rotated


# ---------------------------------------------------------------------------
# AuditWriter — retention
# ---------------------------------------------------------------------------


class TestAuditWriterRetention:
    def test_purge_deletes_old_files(self, audit_dir: Path) -> None:
        audit_dir.mkdir(parents=True, exist_ok=True)
        old_file = audit_dir / "audit-2020-01-01.jsonl"
        old_file.write_text('{"event":"old"}\n')
        # Set mtime to the past
        import os

        old_mtime = time.time() - (365 * 86400)
        os.utime(old_file, (old_mtime, old_mtime))

        w = AuditWriter(audit_dir, enabled=True, retention_days=90)
        deleted = w.purge_old_logs()
        assert deleted == 1
        assert not old_file.exists()

    def test_purge_keeps_recent_files(self, audit_dir: Path) -> None:
        audit_dir.mkdir(parents=True, exist_ok=True)
        recent_file = audit_dir / "audit-2026-02-25.jsonl"
        recent_file.write_text('{"event":"recent"}\n')

        w = AuditWriter(audit_dir, enabled=True, retention_days=90)
        deleted = w.purge_old_logs()
        assert deleted == 0
        assert recent_file.exists()

    def test_purge_noop_when_disabled(self, writer_disabled: AuditWriter) -> None:
        assert writer_disabled.purge_old_logs() == 0


# ---------------------------------------------------------------------------
# Chain verification
# ---------------------------------------------------------------------------


class TestVerifyChain:
    def test_verify_valid_chain(self, writer: AuditWriter, audit_dir: Path) -> None:
        for i in range(5):
            writer.emit(AuditEntry.create(f"event.{i}", "info"))
        log_file = list(audit_dir.glob("audit-*.jsonl"))[0]
        results = verify_chain(log_file, _TEST_PRIVATE_KEY_PEM)
        assert len(results) == 5
        assert all(r["valid"] for r in results)

    def test_verify_detects_tampered_entry(self, writer: AuditWriter, audit_dir: Path) -> None:
        for i in range(3):
            writer.emit(AuditEntry.create(f"event.{i}", "info"))
        log_file = list(audit_dir.glob("audit-*.jsonl"))[0]

        # Tamper with the second line
        lines = log_file.read_text().strip().split("\n")
        entry = json.loads(lines[1])
        entry["event_type"] = "tampered"
        lines[1] = json.dumps(entry, separators=(",", ":"))
        log_file.write_text("\n".join(lines) + "\n")

        results = verify_chain(log_file, _TEST_PRIVATE_KEY_PEM)
        assert not results[1]["valid"]

    def test_verify_detects_deleted_entry(self, writer: AuditWriter, audit_dir: Path) -> None:
        for i in range(4):
            writer.emit(AuditEntry.create(f"event.{i}", "info"))
        log_file = list(audit_dir.glob("audit-*.jsonl"))[0]

        # Delete the second line
        lines = log_file.read_text().strip().split("\n")
        del lines[1]
        log_file.write_text("\n".join(lines) + "\n")

        results = verify_chain(log_file, _TEST_PRIVATE_KEY_PEM)
        # The third entry (now second) should fail because its _prev_hmac
        # points to a deleted entry
        assert not results[1]["valid"]

    def test_verify_empty_log(self, audit_dir: Path) -> None:
        log_file = audit_dir / "audit-empty.jsonl"
        audit_dir.mkdir(parents=True, exist_ok=True)
        log_file.write_text("")
        results = verify_chain(log_file, _TEST_PRIVATE_KEY_PEM)
        assert results == []

    def test_verify_single_entry(self, writer: AuditWriter, audit_dir: Path) -> None:
        writer.emit(AuditEntry.create("single.event", "info"))
        log_file = list(audit_dir.glob("audit-*.jsonl"))[0]
        results = verify_chain(log_file, _TEST_PRIVATE_KEY_PEM)
        assert len(results) == 1
        assert results[0]["valid"]

    def test_verify_with_wrong_key(self, writer: AuditWriter, audit_dir: Path) -> None:
        writer.emit(AuditEntry.create("event", "info"))
        log_file = list(audit_dir.glob("audit-*.jsonl"))[0]
        results = verify_chain(log_file, "wrong-key-pem")
        assert len(results) == 1
        assert not results[0]["valid"]

    def test_verify_invalid_json_line(self, audit_dir: Path) -> None:
        audit_dir.mkdir(parents=True, exist_ok=True)
        log_file = audit_dir / "audit-bad.jsonl"
        log_file.write_text("not json\n")
        results = verify_chain(log_file, _TEST_PRIVATE_KEY_PEM)
        assert len(results) == 1
        assert not results[0]["valid"]
        assert results[0]["error"] == "invalid JSON"


# ---------------------------------------------------------------------------
# Chain resumption
# ---------------------------------------------------------------------------


class TestChainResumption:
    def test_resume_chain_after_restart(self, audit_dir: Path) -> None:
        """An AuditWriter should resume the chain from the last HMAC in the file."""
        w1 = AuditWriter(audit_dir, enabled=True, tamper_protection="hmac", private_key_pem=_TEST_PRIVATE_KEY_PEM)
        w1.emit(AuditEntry.create("event.first", "info"))
        w1.emit(AuditEntry.create("event.second", "info"))

        # Create a new writer (simulates restart)
        w2 = AuditWriter(audit_dir, enabled=True, tamper_protection="hmac", private_key_pem=_TEST_PRIVATE_KEY_PEM)
        w2.emit(AuditEntry.create("event.third", "info"))

        log_file = list(audit_dir.glob("audit-*.jsonl"))[0]
        results = verify_chain(log_file, _TEST_PRIVATE_KEY_PEM)
        assert len(results) == 3
        assert all(r["valid"] for r in results)


# ---------------------------------------------------------------------------
# create_audit_writer factory
# ---------------------------------------------------------------------------


class TestCreateAuditWriter:
    def test_creates_disabled_writer_without_config(self) -> None:
        config = MagicMock()
        config.audit = None
        del config.audit  # hasattr returns False
        writer = create_audit_writer(config)
        assert not writer.enabled

    def test_creates_enabled_writer(self, tmp_path: Path) -> None:
        config = MagicMock()
        config.app.data_dir = tmp_path
        config.audit.enabled = True
        config.audit.log_path = ""
        config.audit.tamper_protection = "hmac"
        config.audit.rotation = "daily"
        config.audit.rotate_size_bytes = 10_485_760
        config.audit.retention_days = 90
        config.audit.redact_content = True
        config.audit.events = {"auth": True}
        writer = create_audit_writer(config, private_key_pem=_TEST_PRIVATE_KEY_PEM)
        assert writer.enabled
        assert writer.log_dir == tmp_path / "audit"

    def test_uses_custom_log_path(self, tmp_path: Path) -> None:
        config = MagicMock()
        config.app.data_dir = tmp_path
        custom_path = tmp_path / "custom_audit"
        config.audit.enabled = True
        config.audit.log_path = str(custom_path)
        config.audit.tamper_protection = "none"
        config.audit.rotation = "daily"
        config.audit.rotate_size_bytes = 10_485_760
        config.audit.retention_days = 30
        config.audit.redact_content = False
        config.audit.events = {}
        writer = create_audit_writer(config, private_key_pem="")
        assert writer.log_dir == custom_path


# ---------------------------------------------------------------------------
# AuditConfig parsing (integration with config.py)
# ---------------------------------------------------------------------------


class TestAuditConfigParsing:
    def test_default_audit_config(self) -> None:
        from anteroom.config import AuditConfig

        cfg = AuditConfig()
        assert not cfg.enabled
        assert cfg.tamper_protection == "hmac"
        assert cfg.rotation == "daily"
        assert cfg.retention_days == 90
        assert cfg.redact_content is True

    def test_audit_config_in_app_config(self) -> None:
        from anteroom.config import AIConfig, AppConfig, AuditConfig

        ai = AIConfig(base_url="http://localhost", api_key="test")
        app = AppConfig(ai=ai)
        assert isinstance(app.audit, AuditConfig)
        assert not app.audit.enabled
