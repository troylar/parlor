"""Encryption at rest for the SQLite database.

Uses SQLCipher (optional dependency) with a key derived from the Ed25519
identity key via HKDF-SHA256. Gracefully degrades to standard sqlite3 when
SQLCipher is not installed or encryption is not configured.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

HKDF_SALT = b"anteroom-db-encryption-salt-v1"
HKDF_INFO = b"anteroom-db-encryption-v1"
HKDF_KEY_LENGTH = 32  # 256-bit key


def derive_db_key(private_key_pem: str) -> bytes:
    """Derive a 256-bit encryption key from the Ed25519 identity private key.

    Uses HKDF-SHA256 with a fixed salt and context label.
    """
    from cryptography.hazmat.primitives.hashes import SHA256
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    if not private_key_pem:
        raise ValueError("Cannot derive encryption key: no identity private key")

    ikm = private_key_pem.encode("utf-8")
    hkdf = HKDF(
        algorithm=SHA256(),
        length=HKDF_KEY_LENGTH,
        salt=HKDF_SALT,
        info=HKDF_INFO,
    )
    return hkdf.derive(ikm)


def _key_hex(key: bytes) -> str:
    """Format key as hex string for SQLCipher PRAGMA."""
    return key.hex()


def is_sqlcipher_available() -> bool:
    """Check if sqlcipher3 is installed and importable."""
    try:
        import sqlcipher3  # noqa: F401

        return True
    except ImportError:
        return False


def open_encrypted_db(db_path: Path, key: bytes) -> sqlite3.Connection:
    """Open (or create) an encrypted SQLite database using SQLCipher.

    Raises ImportError if sqlcipher3 is not installed.
    """
    import sqlcipher3

    conn = sqlcipher3.connect(str(db_path))  # type: ignore[attr-defined]
    # SECURITY-REVIEW: PRAGMA key does not support parameterized binding in SQLCipher.
    # _key_hex() returns hex-only output (0-9a-f) from bytes.hex(), so SQL injection
    # is not possible. The f-string is the required interface for SQLCipher PRAGMAs.
    conn.execute(f"PRAGMA key = \"x'{_key_hex(key)}'\"")
    conn.execute("PRAGMA cipher_page_size = 4096")
    conn.execute("PRAGMA kdf_iter = 256000")
    # Verify the key works by reading the schema
    try:
        conn.execute("SELECT count(*) FROM sqlite_master")
    except Exception as e:
        conn.close()
        logger.error("Failed to open encrypted database: %s", e)
        raise ValueError("Failed to open encrypted database (wrong key?)") from e
    return conn


def verify_encryption(db_path: Path) -> bool:
    """Check if a database file is encrypted.

    Returns True if the file cannot be opened with standard sqlite3
    (indicating it's encrypted or corrupted).
    """
    if not db_path.exists():
        return False

    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("SELECT count(*) FROM sqlite_master")
        conn.close()
        return False  # Opened fine = not encrypted
    except sqlite3.DatabaseError:
        return True  # Can't read it = likely encrypted


def migrate_plaintext_to_encrypted(
    db_path: Path,
    key: bytes,
    *,
    backup_suffix: str = ".bak-plaintext",
) -> Path:
    """Migrate an existing plaintext SQLite database to encrypted.

    1. Creates a backup of the original
    2. Opens the plaintext DB, attaches a new encrypted DB
    3. Exports all data via sqlcipher's sqlcipher_export
    4. Verifies the new DB
    5. Replaces the original

    Returns the path to the backup file.

    Raises ImportError if sqlcipher3 is not installed.
    Raises ValueError if migration verification fails.
    """
    import sqlcipher3

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    if verify_encryption(db_path):
        raise ValueError(f"Database already appears to be encrypted: {db_path}")

    backup_path = db_path.with_suffix(db_path.suffix + backup_suffix)

    # Step 1: Backup the original
    shutil.copy2(db_path, backup_path)
    import os

    os.chmod(backup_path, 0o600)
    logger.info("Backed up plaintext database to %s", backup_path)

    # Step 2: Create encrypted copy in a temp file
    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix=".db", dir=db_path.parent)
    tmp_path = Path(tmp_path_str)
    import os

    os.close(tmp_fd)

    try:
        # Open the plaintext DB with sqlcipher (no key = plaintext mode)
        src_conn = sqlcipher3.connect(str(db_path))  # type: ignore[attr-defined]
        src_conn.execute("PRAGMA key = ''")  # Plaintext mode

        # Attach the encrypted target
        hex_key = _key_hex(key)
        # SECURITY-REVIEW: ATTACH DATABASE and KEY do not support parameterized binding
        # in SQLCipher. tmp_path comes from tempfile.mkstemp (OS-controlled), hex_key is
        # hex-only from bytes.hex(). Reject paths containing single quotes as a safeguard.
        safe_path = str(tmp_path)
        if "'" in safe_path:
            raise ValueError(f"Database path contains unsafe characters: {safe_path}")
        src_conn.execute(f"ATTACH DATABASE '{safe_path}' AS encrypted KEY \"x'{hex_key}'\"")
        src_conn.execute("PRAGMA encrypted.cipher_page_size = 4096")
        src_conn.execute("PRAGMA encrypted.kdf_iter = 256000")

        # Export all data
        src_conn.execute("SELECT sqlcipher_export('encrypted')")
        src_conn.execute("DETACH DATABASE encrypted")
        src_conn.close()

        # Step 3: Verify the encrypted DB
        verify_conn = open_encrypted_db(tmp_path, key)
        row = verify_conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        verify_conn.close()
        if row[0] == 0:
            raise ValueError("Migration produced an empty database")

        # Step 4: Replace original with encrypted copy
        # Remove WAL/SHM files from the original
        for suffix in (".db-wal", ".db-shm", "-wal", "-shm"):
            wal_path = db_path.parent / (db_path.stem + suffix)
            if wal_path.exists():
                wal_path.unlink()

        shutil.move(str(tmp_path), str(db_path))
        logger.info("Migration complete: %s is now encrypted", db_path)

    except Exception:
        # Clean up temp file on failure
        if tmp_path.exists():
            tmp_path.unlink()
        raise

    return backup_path
