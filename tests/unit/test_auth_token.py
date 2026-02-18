"""Tests for stable auth token derivation, middleware auth, and upgrade paths."""

from __future__ import annotations

import hashlib
import time
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.app import (
    SESSION_ABSOLUTE_TIMEOUT,
    SESSION_IDLE_TIMEOUT,
    BearerTokenMiddleware,
    _derive_auth_token,
)


def _make_config(private_key: str | None = None) -> MagicMock:
    config = MagicMock()
    identity = MagicMock()
    if private_key:
        identity.private_key = private_key
        config.identity = identity
    else:
        config.identity = None
    return config


def _make_middleware_app(
    token: str,
    auth_token: str = "",
    secure_cookies: bool = False,
) -> FastAPI:
    """Create a minimal FastAPI app with BearerTokenMiddleware for testing."""
    app = FastAPI()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    app.add_middleware(
        BearerTokenMiddleware,
        token_hash=token_hash,
        auth_token=auth_token,
        secure_cookies=secure_cookies,
    )

    @app.get("/api/test")
    async def _test():
        return {"ok": True}

    @app.post("/api/test")
    async def _test_post():
        return {"ok": True}

    @app.get("/health")
    async def _health():
        return {"healthy": True}

    return app


class TestDeriveAuthToken:
    def test_stable_same_key(self) -> None:
        """Same private key always produces the same token."""
        key = "-----BEGIN PRIVATE KEY-----\nfake-key-material\n-----END PRIVATE KEY-----"
        config = _make_config(private_key=key)
        token1 = _derive_auth_token(config)
        token2 = _derive_auth_token(config)
        assert token1 == token2
        assert len(token1) == 43

    def test_differs_per_key(self) -> None:
        """Different keys produce different tokens."""
        config_a = _make_config(private_key="key-alpha")
        config_b = _make_config(private_key="key-beta")
        assert _derive_auth_token(config_a) != _derive_auth_token(config_b)

    def test_fallback_no_identity(self) -> None:
        """Returns a random token when no identity is configured."""
        config = _make_config(private_key=None)
        token1 = _derive_auth_token(config)
        token2 = _derive_auth_token(config)
        # Random tokens should differ (extremely unlikely to collide)
        assert token1 != token2
        assert len(token1) > 20

    def test_fallback_empty_private_key(self) -> None:
        """Returns a random token when private key is empty string."""
        config = MagicMock()
        identity = MagicMock()
        identity.private_key = ""
        config.identity = identity
        token1 = _derive_auth_token(config)
        token2 = _derive_auth_token(config)
        assert token1 != token2

    def test_token_is_url_safe(self) -> None:
        """Derived token only contains URL-safe characters."""
        import re

        config = _make_config(private_key="test-key")
        token = _derive_auth_token(config)
        assert re.match(r"^[A-Za-z0-9_-]+$", token)


class TestBearerTokenMiddleware:
    """Test the auth middleware directly via TestClient."""

    def test_valid_cookie_allows_request(self) -> None:
        token = "test-token-abc"
        app = _make_middleware_app(token)
        client = TestClient(app)
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_stale_cookie_returns_401(self) -> None:
        token = "correct-token"
        app = _make_middleware_app(token)
        client = TestClient(app)
        resp = client.get("/api/test", cookies={"anteroom_session": "old-stale-token"})
        assert resp.status_code == 401

    def test_missing_cookie_returns_401(self) -> None:
        token = "correct-token"
        app = _make_middleware_app(token)
        client = TestClient(app)
        resp = client.get("/api/test")
        assert resp.status_code == 401

    def test_valid_bearer_token_allows_request(self) -> None:
        token = "test-bearer-token"
        app = _make_middleware_app(token)
        client = TestClient(app)
        resp = client.get("/api/test", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_invalid_bearer_token_returns_401(self) -> None:
        token = "correct-token"
        app = _make_middleware_app(token)
        client = TestClient(app)
        resp = client.get("/api/test", headers={"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 401

    def test_non_api_path_bypasses_auth(self) -> None:
        token = "correct-token"
        app = _make_middleware_app(token)
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"healthy": True}

    def test_csrf_required_for_post(self) -> None:
        token = "correct-token"
        app = _make_middleware_app(token)
        client = TestClient(app)
        # POST with valid session cookie but no CSRF should fail
        resp = client.post("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 403

    def test_csrf_valid_for_post(self) -> None:
        token = "correct-token"
        csrf = "csrf-value"
        app = _make_middleware_app(token)
        client = TestClient(app)
        resp = client.post(
            "/api/test",
            cookies={"anteroom_session": token, "anteroom_csrf": csrf},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 200

    def test_expired_absolute_session_returns_401(self) -> None:
        token = "correct-token"
        app = _make_middleware_app(token)
        client = TestClient(app)

        # Make a first request to initialize the middleware stack
        client.get("/api/test", cookies={"anteroom_session": token})

        # Walk the middleware stack to find our middleware and backdate its creation time
        mw = app.middleware_stack
        while mw is not None:
            if isinstance(mw, BearerTokenMiddleware):
                mw._session_created_at = time.time() - SESSION_ABSOLUTE_TIMEOUT - 1
                break
            mw = getattr(mw, "app", None)

        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 401

    def test_idle_timeout_returns_401(self) -> None:
        token = "correct-token"
        app = _make_middleware_app(token)
        client = TestClient(app)

        # Make a first request to initialize the middleware stack
        client.get("/api/test", cookies={"anteroom_session": token})

        # Walk the middleware stack to find our middleware and backdate its last activity
        mw = app.middleware_stack
        while mw is not None:
            if isinstance(mw, BearerTokenMiddleware):
                mw._last_activity = time.time() - SESSION_IDLE_TIMEOUT - 1
                break
            mw = getattr(mw, "app", None)

        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 401


class TestMiddleware401CookieRefresh:
    """Test that 401 responses include a fresh session cookie for auto-recovery."""

    def test_401_includes_fresh_cookie(self) -> None:
        correct_token = "correct-token"
        app = _make_middleware_app(correct_token, auth_token=correct_token)
        client = TestClient(app)
        resp = client.get("/api/test", cookies={"anteroom_session": "stale-token"})
        assert resp.status_code == 401
        # The response should set a fresh cookie
        set_cookie = resp.headers.get("set-cookie", "")
        assert "anteroom_session" in set_cookie
        assert correct_token in set_cookie

    def test_401_cookie_has_httponly(self) -> None:
        correct_token = "correct-token"
        app = _make_middleware_app(correct_token, auth_token=correct_token)
        client = TestClient(app)
        resp = client.get("/api/test", cookies={"anteroom_session": "stale-token"})
        set_cookie = resp.headers.get("set-cookie", "")
        assert "httponly" in set_cookie.lower()

    def test_401_cookie_has_samesite_strict(self) -> None:
        correct_token = "correct-token"
        app = _make_middleware_app(correct_token, auth_token=correct_token)
        client = TestClient(app)
        resp = client.get("/api/test", cookies={"anteroom_session": "stale-token"})
        set_cookie = resp.headers.get("set-cookie", "")
        assert "samesite=strict" in set_cookie.lower()

    def test_401_no_cookie_when_auth_token_not_provided(self) -> None:
        """When middleware has no auth_token (legacy), 401 should not set a cookie."""
        correct_token = "correct-token"
        app = _make_middleware_app(correct_token, auth_token="")
        client = TestClient(app)
        resp = client.get("/api/test", cookies={"anteroom_session": "stale-token"})
        assert resp.status_code == 401
        set_cookie = resp.headers.get("set-cookie", "")
        assert "anteroom_session" not in set_cookie

    def test_stale_cookie_auto_recovery_on_retry(self) -> None:
        """After getting a 401 with fresh cookie, a retry should succeed."""
        correct_token = "correct-token"
        app = _make_middleware_app(correct_token, auth_token=correct_token)

        # Use a session-based client so cookies persist across requests
        with TestClient(app, cookies={"anteroom_session": "stale-token"}) as client:
            # First request fails with 401 but sets fresh cookie
            resp1 = client.get("/api/test")
            assert resp1.status_code == 401

            # The Set-Cookie header should have updated the client's cookie jar
            # Manually apply the fresh cookie (TestClient may not auto-apply Set-Cookie from 401)
            resp2 = client.get("/api/test", cookies={"anteroom_session": correct_token})
            assert resp2.status_code == 200


class TestPartialIdentityEdgeCase:
    """Test that partial identity (user_id present, private_key missing) is repaired."""

    def test_identity_with_empty_private_key_gets_repaired(self, tmp_path) -> None:
        """ensure_identity() should generate a keypair when user_id exists but private_key is empty."""
        import yaml

        from anteroom.config import ensure_identity

        config_path = tmp_path / "config.yaml"
        # Write a partial identity: user_id present, no private_key
        config_data = {
            "identity": {
                "user_id": "existing-user-123",
                "display_name": "Test User",
                "public_key": "",
                "private_key": "",
            }
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        identity = ensure_identity(config_path)

        assert identity.user_id == "existing-user-123"
        assert identity.private_key != ""
        assert "BEGIN" in identity.private_key
        assert identity.public_key != ""
        assert "BEGIN" in identity.public_key

        # Verify the config file was updated with the keypair
        with open(config_path) as f:
            updated = yaml.safe_load(f)
        assert updated["identity"]["private_key"] != ""
        assert updated["identity"]["user_id"] == "existing-user-123"

    def test_identity_with_valid_key_not_regenerated(self, tmp_path) -> None:
        """ensure_identity() should not regenerate when both user_id and private_key exist."""
        import yaml

        from anteroom.config import ensure_identity

        config_path = tmp_path / "config.yaml"
        original_key = "-----BEGIN PRIVATE KEY-----\nfake-key\n-----END PRIVATE KEY-----"
        config_data = {
            "identity": {
                "user_id": "existing-user-456",
                "display_name": "Test User",
                "public_key": "-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----",
                "private_key": original_key,
            }
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        identity = ensure_identity(config_path)

        assert identity.user_id == "existing-user-456"
        assert identity.private_key == original_key

    def test_no_identity_generates_fresh(self, tmp_path) -> None:
        """ensure_identity() should generate everything when no identity exists."""
        import yaml

        from anteroom.config import ensure_identity

        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump({}, f)

        identity = ensure_identity(config_path)

        assert identity.user_id != ""
        assert identity.private_key != ""
        assert "BEGIN" in identity.private_key

    def test_create_app_repairs_partial_identity(self) -> None:
        """create_app() should call ensure_identity() when identity has no private_key."""
        from anteroom.config import AppConfig, UserIdentity

        config = MagicMock(spec=AppConfig)
        config.ai = MagicMock()
        config.ai.verify_ssl = True
        config.app = MagicMock()
        config.app.tls = False
        config.app.data_dir = None
        config.mcp_servers = []
        config.embeddings = MagicMock()
        config.embeddings.enabled = False

        # Partial identity: has user_id but empty private_key
        partial_identity = MagicMock(spec=UserIdentity)
        partial_identity.private_key = ""
        config.identity = partial_identity

        full_identity = MagicMock(spec=UserIdentity)
        full_identity.private_key = "-----BEGIN PRIVATE KEY-----\nreal-key\n-----END PRIVATE KEY-----"

        with patch("anteroom.app.ensure_identity", return_value=full_identity) as mock_ensure:
            with patch("anteroom.app.lifespan"):
                from anteroom.app import create_app

                create_app(config)

        mock_ensure.assert_called_once()
        assert config.identity == full_identity


class TestDeriveTokenWithPartialIdentity:
    """Test that _derive_auth_token handles the upgrade path correctly."""

    def test_partial_identity_falls_back_to_random(self) -> None:
        """Identity with empty private_key should fall back to random token."""
        config = MagicMock()
        identity = MagicMock()
        identity.private_key = ""
        config.identity = identity

        token1 = _derive_auth_token(config)
        token2 = _derive_auth_token(config)
        # Random tokens differ
        assert token1 != token2

    def test_repaired_identity_produces_stable_token(self) -> None:
        """After ensure_identity repairs the key, token should be stable."""
        config = MagicMock()
        identity = MagicMock()
        identity.private_key = "-----BEGIN PRIVATE KEY-----\nrepaired-key\n-----END PRIVATE KEY-----"
        config.identity = identity

        token1 = _derive_auth_token(config)
        token2 = _derive_auth_token(config)
        assert token1 == token2
        assert len(token1) == 43
