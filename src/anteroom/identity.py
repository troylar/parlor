"""User identity generation using Ed25519 keypairs."""

from __future__ import annotations

import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def generate_identity(display_name: str) -> dict[str, str]:
    """Generate a new user identity with UUID4 + Ed25519 keypair.

    Returns dict with user_id, display_name, public_key (PEM), private_key (PEM).
    """
    user_id = str(uuid.uuid4())
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    return {
        "user_id": user_id,
        "display_name": display_name,
        "public_key": public_pem,
        "private_key": private_pem,
    }


def load_private_key(pem: str) -> Ed25519PrivateKey:
    """Deserialize a PEM-encoded Ed25519 private key."""
    key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Expected Ed25519 private key")
    return key


def load_public_key(pem: str) -> Ed25519PublicKey:
    """Deserialize a PEM-encoded Ed25519 public key."""
    key = serialization.load_pem_public_key(pem.encode("utf-8"))
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Expected Ed25519 public key")
    return key
