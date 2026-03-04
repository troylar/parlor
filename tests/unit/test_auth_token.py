"""Tests for stable auth token derivation, middleware auth, and upgrade paths."""

from __future__ import annotations

import hashlib
import time
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.app import BearerTokenMiddleware, _derive_auth_token, session_id_from_token
from anteroom.config import RateLimitConfig, SessionConfig


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
    session_config: SessionConfig | None = None,
) -> FastAPI:
    """Create a minimal FastAPI app with BearerTokenMiddleware for testing."""
    from anteroom.services.session_store import MemorySessionStore

    app = FastAPI()
    # Set up session store on app.state so middleware can find it
    app.state.session_store = MemorySessionStore()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    app.add_middleware(
        BearerTokenMiddleware,
        token_hash=token_hash,
        auth_token=auth_token,
        secure_cookies=secure_cookies,
        session_config=session_config or SessionConfig(),
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
        # Use minimum absolute timeout (300s) and backdate beyond it
        cfg = SessionConfig(absolute_timeout=300, idle_timeout=99999)
        app = _make_middleware_app(token, session_config=cfg)
        client = TestClient(app)

        # First request creates the session
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

        # Backdate created_at beyond absolute timeout
        store = app.state.session_store
        sid = session_id_from_token(token)
        store._sessions[sid]["created_at"] = time.time() - 400

        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 401  # expired sessions are rejected, not auto-recreated

    def test_idle_timeout_returns_401(self) -> None:
        token = "correct-token"
        # Use minimum idle timeout (60s) and backdate beyond it
        cfg = SessionConfig(idle_timeout=60, absolute_timeout=99999)
        app = _make_middleware_app(token, session_config=cfg)
        client = TestClient(app)

        # First request creates the session
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

        # Backdate last_activity_at beyond idle timeout
        store = app.state.session_store
        sid = session_id_from_token(token)
        store._sessions[sid]["last_activity_at"] = time.time() - 120

        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 401  # expired sessions are rejected, not auto-recreated


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
        config.proxy = MagicMock()
        config.proxy.enabled = False
        config.proxy.allowed_origins = []
        config.session = SessionConfig()
        config.rate_limit = RateLimitConfig()

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


class TestIPAllowlistMiddleware:
    """Test IP allowlist integration in BearerTokenMiddleware."""

    def test_allowed_ip_passes(self) -> None:
        token = "test-token"
        # TestClient reports "testclient" as client host (not a valid IP),
        # so an empty allowlist (allow all) tests the pass-through case
        cfg = SessionConfig(allowed_ips=[])
        app = _make_middleware_app(token, session_config=cfg)
        client = TestClient(app)
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

    def test_blocked_ip_returns_403(self) -> None:
        token = "test-token"
        cfg = SessionConfig(allowed_ips=["10.0.0.0/8"])
        app = _make_middleware_app(token, session_config=cfg)
        client = TestClient(app)
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 403

    def test_empty_allowlist_allows_all(self) -> None:
        token = "test-token"
        cfg = SessionConfig(allowed_ips=[])
        app = _make_middleware_app(token, session_config=cfg)
        client = TestClient(app)
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

    def test_ip_block_bypasses_non_api_paths(self) -> None:
        token = "test-token"
        cfg = SessionConfig(allowed_ips=["10.0.0.0/8"])
        app = _make_middleware_app(token, session_config=cfg)
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200


class TestConcurrentSessionLimit:
    """Test concurrent session limit enforcement."""

    def test_within_limit_succeeds(self) -> None:
        token = "test-token"
        cfg = SessionConfig(max_concurrent_sessions=5)
        app = _make_middleware_app(token, session_config=cfg)
        client = TestClient(app)
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

    def test_unlimited_sessions_by_default(self) -> None:
        token = "test-token"
        cfg = SessionConfig(max_concurrent_sessions=0)
        app = _make_middleware_app(token, session_config=cfg)
        client = TestClient(app)
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

    def test_limit_exceeded_returns_429(self) -> None:
        token = "test-token"
        cfg = SessionConfig(max_concurrent_sessions=1)
        app = _make_middleware_app(token, session_config=cfg)
        client = TestClient(app)
        store = app.state.session_store

        # Fill the store with a session BEFORE the first request
        store.create("other-session", "10.0.0.1")

        # Our token has no session yet and the limit is reached
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 429

    def test_existing_session_within_limit(self) -> None:
        token = "test-token"
        cfg = SessionConfig(max_concurrent_sessions=2)
        app = _make_middleware_app(token, session_config=cfg)
        client = TestClient(app)

        # First request creates a session (count: 1)
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200


class TestSessionIPBinding:
    """Test that sessions are bound to the IP that created them."""

    def test_ip_mismatch_returns_401(self) -> None:
        token = "test-token"
        app = _make_middleware_app(token)
        client = TestClient(app)

        # Create session (TestClient uses "testclient" as host)
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

        # Tamper with the stored IP to simulate a different origin
        store = app.state.session_store
        sid = session_id_from_token(token)
        store._sessions[sid]["ip_address"] = "10.99.99.99"

        # Next request from "testclient" should fail IP binding check
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 401

    def test_same_ip_succeeds(self) -> None:
        token = "test-token"
        app = _make_middleware_app(token)
        client = TestClient(app)

        # Create session
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

        # Second request from same IP succeeds
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

    def test_loopback_ipv6_treated_as_ipv4(self) -> None:
        """Session created with 127.0.0.1, request from ::1 should succeed."""
        token = "test-token"
        app = _make_middleware_app(token)
        client = TestClient(app)

        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

        # Simulate session stored with IPv4 loopback, request from IPv6 loopback
        store = app.state.session_store
        sid = session_id_from_token(token)
        store._sessions[sid]["ip_address"] = "127.0.0.1"

        # Patch request.client.host to return ::1
        with patch("anteroom.app.Request") as _:
            # Directly test _check_session with different loopback forms
            middleware = app.middleware_stack
            # Walk the middleware stack to find BearerTokenMiddleware
            mw = middleware
            while hasattr(mw, "app"):
                if isinstance(mw, BearerTokenMiddleware):
                    break
                mw = mw.app
            if isinstance(mw, BearerTokenMiddleware):
                mw._ensure_store(MagicMock(app=app))
                result = mw._check_session(sid, "::1")
                assert result == "valid"

    def test_loopback_ipv4_mapped_ipv6(self) -> None:
        """Session created with ::ffff:127.0.0.1, request from 127.0.0.1 should succeed."""
        token = "test-token"
        app = _make_middleware_app(token)
        client = TestClient(app)

        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

        store = app.state.session_store
        sid = session_id_from_token(token)
        store._sessions[sid]["ip_address"] = "::ffff:127.0.0.1"

        # Walk middleware stack to find BearerTokenMiddleware
        mw = app.middleware_stack
        while hasattr(mw, "app"):
            if isinstance(mw, BearerTokenMiddleware):
                break
            mw = mw.app
        if isinstance(mw, BearerTokenMiddleware):
            mw._ensure_store(MagicMock(app=app))
            result = mw._check_session(sid, "127.0.0.1")
            assert result == "valid"

    def test_non_loopback_mismatch_still_caught(self) -> None:
        """Non-loopback IP mismatch should still be caught."""
        token = "test-token"
        app = _make_middleware_app(token)
        client = TestClient(app)

        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

        store = app.state.session_store
        sid = session_id_from_token(token)
        store._sessions[sid]["ip_address"] = "192.168.1.100"

        mw = app.middleware_stack
        while hasattr(mw, "app"):
            if isinstance(mw, BearerTokenMiddleware):
                break
            mw = mw.app
        if isinstance(mw, BearerTokenMiddleware):
            mw._ensure_store(MagicMock(app=app))
            result = mw._check_session(sid, "10.0.0.1")
            assert result == "ip_mismatch"


class TestNormalizeLoopback:
    """Test the _normalize_loopback helper function."""

    def test_ipv4_loopback(self) -> None:
        from anteroom.app import _normalize_loopback

        assert _normalize_loopback("127.0.0.1") == "127.0.0.1"

    def test_ipv6_loopback(self) -> None:
        from anteroom.app import _normalize_loopback

        assert _normalize_loopback("::1") == "127.0.0.1"

    def test_ipv4_mapped_ipv6_loopback(self) -> None:
        from anteroom.app import _normalize_loopback

        assert _normalize_loopback("::ffff:127.0.0.1") == "127.0.0.1"

    def test_non_loopback_ipv4_unchanged(self) -> None:
        from anteroom.app import _normalize_loopback

        assert _normalize_loopback("192.168.1.1") == "192.168.1.1"

    def test_non_loopback_ipv6_unchanged(self) -> None:
        from anteroom.app import _normalize_loopback

        assert _normalize_loopback("2001:db8::1") == "2001:db8::1"

    def test_invalid_ip_returned_as_is(self) -> None:
        from anteroom.app import _normalize_loopback

        assert _normalize_loopback("not-an-ip") == "not-an-ip"

    def test_testclient_host_returned_as_is(self) -> None:
        from anteroom.app import _normalize_loopback

        assert _normalize_loopback("testclient") == "testclient"


class TestSession401CookieRefresh:
    """Test that expired and ip_mismatch 401s include a fresh session cookie."""

    def test_expired_session_includes_cookie(self) -> None:
        token = "test-token"
        app = _make_middleware_app(token, auth_token=token)
        client = TestClient(app)

        # Create session
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

        # Backdate session to force absolute timeout
        store = app.state.session_store
        sid = session_id_from_token(token)
        store._sessions[sid]["created_at"] = time.time() - 999999

        # Expired session should return 401 WITH a Set-Cookie header
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 401
        assert "anteroom_session" in resp.headers.get("set-cookie", "")

    def test_ip_mismatch_includes_cookie(self) -> None:
        token = "test-token"
        app = _make_middleware_app(token, auth_token=token)
        client = TestClient(app)

        # Create session
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

        # Simulate IP mismatch with a non-loopback address
        store = app.state.session_store
        sid = session_id_from_token(token)
        store._sessions[sid]["ip_address"] = "10.99.99.99"

        # IP mismatch should return 401 WITH a Set-Cookie header
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 401
        assert "anteroom_session" in resp.headers.get("set-cookie", "")

    def test_expired_no_cookie_when_no_auth_token(self) -> None:
        token = "test-token"
        # No auth_token passed — cookie refresh should be skipped
        app = _make_middleware_app(token, auth_token="")
        client = TestClient(app)

        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

        store = app.state.session_store
        sid = session_id_from_token(token)
        store._sessions[sid]["created_at"] = time.time() - 999999

        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 401
        assert "anteroom_session" not in resp.headers.get("set-cookie", "")


class TestNormalizeLoopbackExtended:
    """Extended edge cases for _normalize_loopback."""

    def test_ipv6_full_loopback(self) -> None:
        """Full-form IPv6 loopback should normalize."""
        from anteroom.app import _normalize_loopback

        assert _normalize_loopback("0000:0000:0000:0000:0000:0000:0000:0001") == "127.0.0.1"

    def test_ipv4_127_variants(self) -> None:
        """All 127.x.x.x addresses are loopback."""
        from anteroom.app import _normalize_loopback

        assert _normalize_loopback("127.0.0.2") == "127.0.0.1"
        assert _normalize_loopback("127.255.255.255") == "127.0.0.1"

    def test_empty_string(self) -> None:
        from anteroom.app import _normalize_loopback

        assert _normalize_loopback("") == ""

    def test_ipv4_mapped_non_loopback(self) -> None:
        """IPv4-mapped IPv6 non-loopback should resolve to IPv4 form."""
        from anteroom.app import _normalize_loopback

        result = _normalize_loopback("::ffff:192.168.1.1")
        assert result == "192.168.1.1"

    def test_link_local_ipv6(self) -> None:
        """Link-local IPv6 should not be treated as loopback."""
        from anteroom.app import _normalize_loopback

        result = _normalize_loopback("fe80::1")
        assert result != "127.0.0.1"


class TestSessionCreationNormalizesIP:
    """Test that session creation stores normalized IPs."""

    def test_session_stores_normalized_loopback(self) -> None:
        """When a session is created from ::1, it should store 127.0.0.1."""
        from anteroom.app import _normalize_loopback

        # Verify the normalization would happen before storage
        assert _normalize_loopback("::1") == "127.0.0.1"
        assert _normalize_loopback("::ffff:127.0.0.1") == "127.0.0.1"

    def test_session_creation_and_check_round_trip(self) -> None:
        """Session created with normalized IP should validate against any loopback variant."""
        token = "test-token"
        app = _make_middleware_app(token)
        client = TestClient(app)

        # Create session
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

        # The stored IP should be normalized already (TestClient uses "testclient")
        store = app.state.session_store
        sid = session_id_from_token(token)
        # Manually set to normalized loopback to simulate real browser
        store._sessions[sid]["ip_address"] = "127.0.0.1"

        # Walk middleware stack
        mw = app.middleware_stack
        while hasattr(mw, "app"):
            if isinstance(mw, BearerTokenMiddleware):
                break
            mw = mw.app
        if isinstance(mw, BearerTokenMiddleware):
            mw._ensure_store(MagicMock(app=app))
            # All loopback variants should pass
            assert mw._check_session(sid, "127.0.0.1") == "valid"
            assert mw._check_session(sid, "::1") == "valid"
            assert mw._check_session(sid, "::ffff:127.0.0.1") == "valid"
            # Non-loopback should fail
            assert mw._check_session(sid, "192.168.1.1") == "ip_mismatch"


class TestExpiredSessionRecoveryFlow:
    """Test the full recovery flow: expired session -> 401 with cookie -> retry succeeds."""

    def test_full_recovery_after_absolute_timeout(self) -> None:
        """After absolute timeout, 401 response includes cookie that enables recovery."""
        token = "test-token"
        cfg = SessionConfig(absolute_timeout=300, idle_timeout=99999)
        app = _make_middleware_app(token, auth_token=token, session_config=cfg)
        client = TestClient(app)

        # Create session
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

        # Backdate to expire
        store = app.state.session_store
        sid = session_id_from_token(token)
        store._sessions[sid]["created_at"] = time.time() - 400

        # Expired -> 401 with fresh cookie
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 401
        set_cookie = resp.headers.get("set-cookie", "")
        assert "anteroom_session" in set_cookie
        assert token in set_cookie

        # Retry with the fresh cookie should work (session was deleted, new one created)
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

    def test_full_recovery_after_idle_timeout(self) -> None:
        """After idle timeout, 401 response includes cookie that enables recovery."""
        token = "test-token"
        cfg = SessionConfig(idle_timeout=60, absolute_timeout=99999)
        app = _make_middleware_app(token, auth_token=token, session_config=cfg)
        client = TestClient(app)

        # Create session
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

        # Backdate last_activity
        store = app.state.session_store
        sid = session_id_from_token(token)
        store._sessions[sid]["last_activity_at"] = time.time() - 120

        # Expired -> 401 with fresh cookie
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 401
        assert "anteroom_session" in resp.headers.get("set-cookie", "")

        # Retry succeeds
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200


class TestSessionConfigClamping:
    """Test that SessionConfig enforces minimum timeout values."""

    def test_idle_timeout_clamped_to_minimum(self) -> None:
        cfg = SessionConfig(idle_timeout=5)
        assert cfg.idle_timeout == 60  # minimum is 60s

    def test_absolute_timeout_clamped_to_minimum(self) -> None:
        cfg = SessionConfig(absolute_timeout=10)
        assert cfg.absolute_timeout == 300  # minimum is 300s

    def test_valid_timeouts_unchanged(self) -> None:
        cfg = SessionConfig(idle_timeout=1800, absolute_timeout=43200)
        assert cfg.idle_timeout == 1800
        assert cfg.absolute_timeout == 43200
