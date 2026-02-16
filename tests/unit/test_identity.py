"""Tests for user identity generation."""

from __future__ import annotations

import uuid

from anteroom.identity import generate_identity, load_private_key, load_public_key


class TestGenerateIdentity:
    def test_returns_all_required_fields(self) -> None:
        result = generate_identity("Alice")
        assert "user_id" in result
        assert "display_name" in result
        assert "public_key" in result
        assert "private_key" in result

    def test_display_name_preserved(self) -> None:
        result = generate_identity("Bob")
        assert result["display_name"] == "Bob"

    def test_user_id_is_valid_uuid(self) -> None:
        result = generate_identity("test")
        uuid.UUID(result["user_id"])

    def test_keys_are_pem_format(self) -> None:
        result = generate_identity("test")
        assert result["public_key"].startswith("-----BEGIN PUBLIC KEY-----")
        assert result["private_key"].startswith("-----BEGIN PRIVATE KEY-----")

    def test_different_calls_produce_different_uuids(self) -> None:
        r1 = generate_identity("a")
        r2 = generate_identity("b")
        assert r1["user_id"] != r2["user_id"]

    def test_different_calls_produce_different_keys(self) -> None:
        r1 = generate_identity("a")
        r2 = generate_identity("a")
        assert r1["private_key"] != r2["private_key"]
        assert r1["public_key"] != r2["public_key"]


class TestKeyLoading:
    def test_load_private_key_roundtrip(self) -> None:
        result = generate_identity("test")
        key = load_private_key(result["private_key"])
        assert key is not None

    def test_load_public_key_roundtrip(self) -> None:
        result = generate_identity("test")
        key = load_public_key(result["public_key"])
        assert key is not None

    def test_sign_and_verify(self) -> None:
        result = generate_identity("test")
        private_key = load_private_key(result["private_key"])
        public_key = load_public_key(result["public_key"])
        message = b"test message"
        signature = private_key.sign(message)
        public_key.verify(signature, message)

    def test_invalid_pem_raises(self) -> None:
        import pytest

        with pytest.raises(Exception):
            load_private_key("not a pem key")

        with pytest.raises(Exception):
            load_public_key("not a pem key")
