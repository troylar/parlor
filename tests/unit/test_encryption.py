"""Tests for encryption at rest functionality."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from anteroom.services.encryption import (
    HKDF_INFO,
    HKDF_KEY_LENGTH,
    HKDF_SALT,
    derive_db_key,
    is_sqlcipher_available,
    verify_encryption,
)

# A stable test PEM key for deterministic key derivation tests
_TEST_PEM = """-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEIKFwEEeil3TLxMALbFxB7pVqpOSyIosFX5mP5+g6mFYy
-----END PRIVATE KEY-----"""


class TestDeriveDbKey:
    def test_derives_32_byte_key(self) -> None:
        key = derive_db_key(_TEST_PEM)
        assert isinstance(key, bytes)
        assert len(key) == HKDF_KEY_LENGTH

    def test_deterministic(self) -> None:
        key1 = derive_db_key(_TEST_PEM)
        key2 = derive_db_key(_TEST_PEM)
        assert key1 == key2

    def test_different_keys_for_different_pems(self) -> None:
        key1 = derive_db_key(_TEST_PEM)
        key2 = derive_db_key("-----BEGIN PRIVATE KEY-----\nDIFFERENT\n-----END PRIVATE KEY-----")
        assert key1 != key2

    def test_empty_pem_raises(self) -> None:
        with pytest.raises(ValueError, match="no identity private key"):
            derive_db_key("")

    def test_uses_correct_hkdf_params(self) -> None:
        """Verify the HKDF parameters are what we expect for key derivation."""
        assert HKDF_SALT == b"anteroom-db-encryption-salt-v1"
        assert HKDF_INFO == b"anteroom-db-encryption-v1"
        assert HKDF_KEY_LENGTH == 32


class TestIsSqlcipherAvailable:
    def test_returns_false_when_import_fails(self) -> None:
        import sys

        # Remove any cached module so the import is retried
        saved = sys.modules.pop("sqlcipher3", None)
        try:
            with patch.dict("sys.modules", {"sqlcipher3": None}):
                # Reload the encryption module to pick up the patched import
                # But is_sqlcipher_available does a deferred import, so just call it
                # with sqlcipher3 set to None (triggers ImportError on attribute access)
                assert is_sqlcipher_available() is False
        finally:
            if saved is not None:
                sys.modules["sqlcipher3"] = saved

    def test_returns_true_when_installed(self) -> None:
        import sys
        from unittest.mock import MagicMock

        mock_mod = MagicMock()
        saved = sys.modules.get("sqlcipher3")
        sys.modules["sqlcipher3"] = mock_mod
        try:
            assert is_sqlcipher_available() is True
        finally:
            if saved is not None:
                sys.modules["sqlcipher3"] = saved
            else:
                sys.modules.pop("sqlcipher3", None)


class TestVerifyEncryption:
    def test_plaintext_db_returns_false(self, tmp_path: Path) -> None:
        db_path = tmp_path / "plain.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.commit()
        conn.close()

        assert verify_encryption(db_path) is False

    def test_nonexistent_db_returns_false(self, tmp_path: Path) -> None:
        assert verify_encryption(tmp_path / "nope.db") is False

    def test_corrupted_file_returns_true(self, tmp_path: Path) -> None:
        db_path = tmp_path / "encrypted.db"
        db_path.write_bytes(b"\x00" * 4096 + b"random encrypted data")
        assert verify_encryption(db_path) is True


class TestOpenEncryptedDb:
    def test_open_calls_sqlcipher_with_pragmas(self) -> None:
        """open_encrypted_db calls sqlcipher3.connect and sets PRAGMA key."""
        import sys
        from unittest.mock import MagicMock

        from anteroom.services.encryption import open_encrypted_db

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (5,)
        mock_sqlcipher = MagicMock()
        mock_sqlcipher.connect.return_value = mock_conn

        saved = sys.modules.get("sqlcipher3")
        sys.modules["sqlcipher3"] = mock_sqlcipher
        try:
            key = derive_db_key(_TEST_PEM)
            conn = open_encrypted_db(Path("/tmp/test.db"), key)
            assert conn is mock_conn
            mock_sqlcipher.connect.assert_called_once_with("/tmp/test.db")
            # Verify PRAGMA key was set
            calls = [str(c) for c in mock_conn.execute.call_args_list]
            assert any("PRAGMA key" in c for c in calls)
            assert any("PRAGMA cipher_page_size" in c for c in calls)
        finally:
            if saved is not None:
                sys.modules["sqlcipher3"] = saved
            else:
                sys.modules.pop("sqlcipher3", None)

    def test_open_raises_on_wrong_key(self) -> None:
        """open_encrypted_db raises ValueError when DB can't be read with key."""
        import sys
        from unittest.mock import MagicMock

        from anteroom.services.encryption import open_encrypted_db

        mock_conn = MagicMock()
        # First two execute calls (PRAGMA key, cipher_page_size) succeed
        # Third call (SELECT count) raises
        call_count = 0

        def _side_effect(sql, *args):
            nonlocal call_count
            call_count += 1
            if "SELECT count" in sql:
                raise Exception("file is not a database")
            return MagicMock()

        mock_conn.execute.side_effect = _side_effect
        mock_sqlcipher = MagicMock()
        mock_sqlcipher.connect.return_value = mock_conn

        saved = sys.modules.get("sqlcipher3")
        sys.modules["sqlcipher3"] = mock_sqlcipher
        try:
            key = derive_db_key(_TEST_PEM)
            with pytest.raises(ValueError, match="Failed to open encrypted database"):
                open_encrypted_db(Path("/tmp/test.db"), key)
            mock_conn.close.assert_called_once()
        finally:
            if saved is not None:
                sys.modules["sqlcipher3"] = saved
            else:
                sys.modules.pop("sqlcipher3", None)


class TestMigration:
    def test_migration_nonexistent_db_raises(self, tmp_path: Path) -> None:
        from anteroom.services.encryption import migrate_plaintext_to_encrypted

        key = derive_db_key(_TEST_PEM)
        with pytest.raises(FileNotFoundError):
            migrate_plaintext_to_encrypted(tmp_path / "nope.db", key)

    def test_migration_already_encrypted_raises(self, tmp_path: Path) -> None:
        from anteroom.services.encryption import migrate_plaintext_to_encrypted

        db_path = tmp_path / "encrypted.db"
        db_path.write_bytes(b"\x00" * 4096 + b"looks encrypted")

        key = derive_db_key(_TEST_PEM)
        with pytest.raises(ValueError, match="already appears to be encrypted"):
            migrate_plaintext_to_encrypted(db_path, key)

    def test_migration_happy_path_with_mock(self, tmp_path: Path) -> None:
        """Test migration flow: backup, export, verify, replace."""
        import os
        import sys
        from unittest.mock import MagicMock

        from anteroom.services.encryption import migrate_plaintext_to_encrypted

        # Create a real plaintext DB
        db_path = tmp_path / "chat.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER, name TEXT)")
        conn.execute("INSERT INTO test VALUES (1, 'hello')")
        conn.commit()
        conn.close()

        key = derive_db_key(_TEST_PEM)

        # Mock sqlcipher3 to simulate the migration
        mock_src_conn = MagicMock()
        mock_verify_conn = MagicMock()
        mock_verify_conn.execute.return_value.fetchone.return_value = (3,)  # non-zero table count

        connect_calls = []

        def _mock_connect(path):
            connect_calls.append(path)
            if len(connect_calls) == 1:
                return mock_src_conn
            return mock_verify_conn

        mock_sqlcipher = MagicMock()
        mock_sqlcipher.connect.side_effect = _mock_connect

        saved = sys.modules.get("sqlcipher3")
        sys.modules["sqlcipher3"] = mock_sqlcipher
        try:
            # Write a fake encrypted DB to the temp path that migration will create
            # We need to patch tempfile.mkstemp to control the temp file
            with patch("anteroom.services.encryption.tempfile.mkstemp") as mock_mkstemp:
                tmp_encrypted = tmp_path / "tmp_encrypted.db"
                tmp_encrypted.write_bytes(b"fake encrypted")
                fd = os.open(str(tmp_encrypted), os.O_RDWR)
                mock_mkstemp.return_value = (fd, str(tmp_encrypted))

                backup_path = migrate_plaintext_to_encrypted(db_path, key)

            # Verify backup was created
            assert backup_path.exists()
            assert backup_path.suffix.endswith("-plaintext")

            # Verify sqlcipher_export was called
            export_calls = [str(c) for c in mock_src_conn.execute.call_args_list]
            assert any("sqlcipher_export" in c for c in export_calls)
        finally:
            if saved is not None:
                sys.modules["sqlcipher3"] = saved
            else:
                sys.modules.pop("sqlcipher3", None)


class TestStorageConfig:
    def test_default_values(self) -> None:
        from anteroom.config import StorageConfig

        cfg = StorageConfig()
        assert cfg.retention_days == 0
        assert cfg.retention_check_interval == 3600
        assert cfg.purge_attachments is True
        assert cfg.purge_embeddings is True
        assert cfg.encrypt_at_rest is False
        assert cfg.encryption_kdf == "hkdf-sha256"

    def test_clamps_check_interval(self) -> None:
        from anteroom.config import StorageConfig

        cfg = StorageConfig(retention_check_interval=10)
        assert cfg.retention_check_interval == 60  # clamped to minimum

    def test_custom_values(self) -> None:
        from anteroom.config import StorageConfig

        cfg = StorageConfig(
            retention_days=90,
            retention_check_interval=7200,
            purge_attachments=False,
            encrypt_at_rest=True,
        )
        assert cfg.retention_days == 90
        assert cfg.retention_check_interval == 7200
        assert cfg.purge_attachments is False
        assert cfg.encrypt_at_rest is True


class TestConfigValidation:
    def test_storage_section_accepted(self) -> None:
        from anteroom.services.config_validator import validate_config

        raw = {
            "ai": {"base_url": "http://localhost:11434/v1"},
            "storage": {
                "retention_days": 90,
                "retention_check_interval": 3600,
                "encrypt_at_rest": False,
            },
        }
        result = validate_config(raw)
        assert result.is_valid

    def test_unknown_storage_key_warns(self) -> None:
        from anteroom.services.config_validator import validate_config

        raw = {
            "ai": {"base_url": "http://localhost:11434/v1"},
            "storage": {"unknown_field": True},
        }
        result = validate_config(raw)
        warnings = [e for e in result.errors if e.severity == "warning"]
        assert any("unknown_field" in str(w) for w in warnings)

    def test_retention_days_range_validation(self) -> None:
        from anteroom.services.config_validator import validate_config

        raw = {
            "ai": {"base_url": "http://localhost:11434/v1"},
            "storage": {"retention_days": -1},
        }
        result = validate_config(raw)
        warnings = [e for e in result.errors if "retention_days" in e.path]
        assert len(warnings) > 0


class TestDbInitWithEncryption:
    def test_init_db_without_encryption(self, tmp_path: Path) -> None:
        from anteroom.db import init_db

        db = init_db(tmp_path / "test.db")
        assert db is not None
        db.close()

    def test_init_db_encryption_key_none_uses_sqlite(self, tmp_path: Path) -> None:
        from anteroom.db import init_db

        db = init_db(tmp_path / "test.db", encryption_key=None)
        assert db is not None
        db.close()
