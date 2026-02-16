"""Shared database passphrase authentication using argon2id."""

from __future__ import annotations

import logging

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError

    _hasher = PasswordHasher()
    _HAS_ARGON2 = True
except ImportError:
    _HAS_ARGON2 = False

logger = logging.getLogger(__name__)


def hash_passphrase(passphrase: str) -> str:
    """Hash a passphrase using argon2id. Requires ``argon2-cffi``."""
    if not _HAS_ARGON2:
        raise RuntimeError("argon2-cffi is required for shared database auth. Install with: pip install argon2-cffi")
    return _hasher.hash(passphrase)


def verify_passphrase(passphrase: str, passphrase_hash: str) -> bool:
    """Verify a passphrase against an argon2id hash. Returns False on mismatch."""
    if not _HAS_ARGON2:
        raise RuntimeError("argon2-cffi is required for shared database auth. Install with: pip install argon2-cffi")
    try:
        return _hasher.verify(passphrase_hash, passphrase)
    except VerifyMismatchError:
        return False


def needs_rehash(passphrase_hash: str) -> bool:
    """Check if a hash needs to be re-hashed with updated parameters."""
    if not _HAS_ARGON2:
        return False
    return _hasher.check_needs_rehash(passphrase_hash)
