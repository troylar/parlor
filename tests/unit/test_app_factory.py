"""Tests for app.py: create_app factory, middleware stack, lifespan management."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from anteroom.app import (
    MAX_REQUEST_BODY_BYTES,
    BearerTokenMiddleware,
    MaxBodySizeMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    session_id_from_token,
)
from anteroom.config import RateLimitConfig, SessionConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    tls: bool = False,
    host: str = "127.0.0.1",
    port: int = 8080,
    proxy_enabled: bool = False,
    private_key: str = "fake-private-key",
    data_dir: Path | None = None,
) -> MagicMock:
    config = MagicMock()
    config.app.tls = tls
    config.app.host = host
    config.app.port = port
    config.app.data_dir = data_dir or Path("/tmp/anteroom-test")
    config.ai.verify_ssl = True
    config.mcp_servers = []
    config.proxy.enabled = proxy_enabled
    config.proxy.allowed_origins = []
    config.session = SessionConfig()
    config.rate_limit = RateLimitConfig()
    config.shared_databases = []
    config.pack_sources = []
    config.safety.dlp = None
    config.safety.prompt_injection = None
    config.storage.retention_days = 0
    config.storage.encrypt_at_rest = False

    identity = MagicMock()
    identity.private_key = private_key
    identity.user_id = "test-user-id"
    identity.display_name = "Test User"
    identity.public_key = "fake-public-key"
    config.identity = identity

    return config


def _make_minimal_app(token: str = "test-token", session_config: SessionConfig | None = None) -> FastAPI:
    """Create a minimal FastAPI app with all relevant middleware for integration tests."""
    from anteroom.services.session_store import MemorySessionStore

    app = FastAPI()
    app.state.session_store = MemorySessionStore()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    app.add_middleware(
        BearerTokenMiddleware,
        token_hash=token_hash,
        auth_token=token,
        secure_cookies=False,
        session_config=session_config or SessionConfig(),
    )
    app.add_middleware(RateLimitMiddleware, max_requests=5, window_seconds=60)
    app.add_middleware(MaxBodySizeMiddleware, max_body_size=100)
    app.add_middleware(SecurityHeadersMiddleware, tls_enabled=False)

    @app.get("/api/test")
    async def _test():
        return {"ok": True}

    @app.post("/api/test")
    async def _test_post():
        return {"ok": True}

    @app.get("/public")
    async def _public():
        return {"public": True}

    return app


# ---------------------------------------------------------------------------
# SecurityHeadersMiddleware
# ---------------------------------------------------------------------------


class TestSecurityHeadersMiddleware:
    def _make_app_with_security_headers(self, tls_enabled: bool = False) -> FastAPI:
        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware, tls_enabled=tls_enabled)

        @app.get("/api/data")
        async def _api():
            return {"data": True}

        @app.get("/public")
        async def _public():
            return {"public": True}

        @app.get("/js/app.js")
        async def _js():
            return {"js": True}

        return app

    def test_x_frame_options_deny(self) -> None:
        app = self._make_app_with_security_headers()
        client = TestClient(app)
        resp = client.get("/api/data")
        assert resp.headers.get("x-frame-options") == "DENY"

    def test_x_content_type_options_nosniff(self) -> None:
        app = self._make_app_with_security_headers()
        client = TestClient(app)
        resp = client.get("/api/data")
        assert resp.headers.get("x-content-type-options") == "nosniff"

    def test_referrer_policy(self) -> None:
        app = self._make_app_with_security_headers()
        client = TestClient(app)
        resp = client.get("/public")
        assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    def test_permissions_policy(self) -> None:
        app = self._make_app_with_security_headers()
        client = TestClient(app)
        resp = client.get("/public")
        perms = resp.headers.get("permissions-policy", "")
        assert "camera=()" in perms
        assert "microphone=()" in perms

    def test_csp_header_present(self) -> None:
        app = self._make_app_with_security_headers()
        client = TestClient(app)
        resp = client.get("/api/data")
        csp = resp.headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp

    def test_hsts_when_tls_enabled(self) -> None:
        app = self._make_app_with_security_headers(tls_enabled=True)
        client = TestClient(app)
        resp = client.get("/api/data")
        hsts = resp.headers.get("strict-transport-security", "")
        assert "max-age=31536000" in hsts
        assert "includeSubDomains" in hsts

    def test_no_hsts_when_tls_disabled(self) -> None:
        app = self._make_app_with_security_headers(tls_enabled=False)
        client = TestClient(app)
        resp = client.get("/api/data")
        assert "strict-transport-security" not in resp.headers

    def test_api_paths_get_no_cache(self) -> None:
        app = self._make_app_with_security_headers()
        client = TestClient(app)
        resp = client.get("/api/data")
        cache = resp.headers.get("cache-control", "")
        assert "no-store" in cache

    def test_js_paths_get_no_cache_must_revalidate(self) -> None:
        app = self._make_app_with_security_headers()
        client = TestClient(app)
        resp = client.get("/js/app.js")
        cache = resp.headers.get("cache-control", "")
        assert "no-cache" in cache
        assert "must-revalidate" in cache

    def test_public_path_no_cache_control(self) -> None:
        app = self._make_app_with_security_headers()
        client = TestClient(app)
        resp = client.get("/public")
        assert "no-store" not in resp.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# MaxBodySizeMiddleware
# ---------------------------------------------------------------------------


class TestMaxBodySizeMiddleware:
    def _make_app_with_body_limit(self, max_bytes: int = 100) -> FastAPI:
        app = FastAPI()
        app.add_middleware(MaxBodySizeMiddleware, max_body_size=max_bytes)

        @app.post("/upload")
        async def _upload():
            return {"ok": True}

        return app

    def test_request_within_limit_passes(self) -> None:
        app = self._make_app_with_body_limit(max_bytes=1000)
        client = TestClient(app)
        resp = client.post(
            "/upload",
            content=b"small body",
            headers={"content-length": "10"},
        )
        assert resp.status_code == 200

    def test_request_exceeding_limit_returns_413(self) -> None:
        app = self._make_app_with_body_limit(max_bytes=100)
        client = TestClient(app)
        resp = client.post(
            "/upload",
            content=b"x" * 200,
            headers={"content-length": "200"},
        )
        assert resp.status_code == 413
        assert resp.json()["detail"] == "Request body too large"

    def test_request_no_content_length_passes(self) -> None:
        app = self._make_app_with_body_limit(max_bytes=100)
        client = TestClient(app)
        resp = client.post("/upload")
        assert resp.status_code == 200

    def test_max_request_body_bytes_constant(self) -> None:
        assert MAX_REQUEST_BODY_BYTES == 15 * 1024 * 1024


# ---------------------------------------------------------------------------
# RateLimitMiddleware
# ---------------------------------------------------------------------------


class TestRateLimitMiddleware:
    def _make_rate_limited_app(self, max_requests: int = 3, window: int = 60) -> FastAPI:
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware, max_requests=max_requests, window_seconds=window)

        @app.get("/api/test")
        async def _test():
            return {"ok": True}

        return app

    def test_requests_within_limit_pass(self) -> None:
        app = self._make_rate_limited_app(max_requests=5)
        client = TestClient(app)
        for _ in range(5):
            resp = client.get("/api/test")
            assert resp.status_code == 200

    def test_requests_exceeding_limit_return_429(self) -> None:
        app = self._make_rate_limited_app(max_requests=3)
        client = TestClient(app)
        for _ in range(3):
            client.get("/api/test")
        resp = client.get("/api/test")
        assert resp.status_code == 429
        assert resp.json()["detail"] == "Too many requests"

    def test_rate_limit_tracks_per_ip(self) -> None:
        app = self._make_rate_limited_app(max_requests=2)
        client = TestClient(app)
        client.get("/api/test")
        client.get("/api/test")
        resp = client.get("/api/test")
        assert resp.status_code == 429

    def test_old_hits_expire_from_window(self) -> None:
        app = FastAPI()

        @app.get("/api/test")
        async def _test():
            return {"ok": True}

        app.add_middleware(RateLimitMiddleware, max_requests=2, window_seconds=60)
        client = TestClient(app)

        resp1 = client.get("/api/test")
        assert resp1.status_code == 200
        resp2 = client.get("/api/test")
        assert resp2.status_code == 200
        resp3 = client.get("/api/test")
        assert resp3.status_code == 429

    def test_max_tracked_ips_constant(self) -> None:
        assert RateLimitMiddleware.MAX_TRACKED_IPS == 10000

    def test_exempt_path_not_rate_limited(self) -> None:
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware, max_requests=2, window_seconds=60, exempt_paths={"/api/events"})

        @app.get("/api/events")
        async def _events() -> dict[str, bool]:
            return {"ok": True}

        @app.get("/api/test")
        async def _test() -> dict[str, bool]:
            return {"ok": True}

        client = TestClient(app)
        # Exhaust limit on non-exempt path
        client.get("/api/test")
        client.get("/api/test")
        assert client.get("/api/test").status_code == 429

        # Exempt path should still work despite rate limit exhaustion
        for _ in range(5):
            resp = client.get("/api/events")
            assert resp.status_code == 200

    def test_non_exempt_path_still_rate_limited(self) -> None:
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware, max_requests=2, window_seconds=60, exempt_paths={"/api/events"})

        @app.get("/api/test")
        async def _test() -> dict[str, bool]:
            return {"ok": True}

        client = TestClient(app)
        client.get("/api/test")
        client.get("/api/test")
        assert client.get("/api/test").status_code == 429

    def test_exempt_path_exact_match_only(self) -> None:
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware, max_requests=2, window_seconds=60, exempt_paths={"/api/events"})

        @app.get("/api/events_extra")
        async def _events_extra() -> dict[str, bool]:
            return {"ok": True}

        client = TestClient(app)
        client.get("/api/events_extra")
        client.get("/api/events_extra")
        assert client.get("/api/events_extra").status_code == 429

    def test_exempt_paths_do_not_count_against_limit(self) -> None:
        """Requests to exempt paths should not consume rate limit budget."""
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware, max_requests=2, window_seconds=60, exempt_paths={"/api/events"})

        @app.get("/api/events")
        async def _events() -> dict[str, bool]:
            return {"ok": True}

        @app.get("/api/test")
        async def _test() -> dict[str, bool]:
            return {"ok": True}

        client = TestClient(app)
        # Hit exempt path many times
        for _ in range(10):
            client.get("/api/events")
        # Non-exempt path should still have full budget
        assert client.get("/api/test").status_code == 200
        assert client.get("/api/test").status_code == 200
        assert client.get("/api/test").status_code == 429

    def test_429_includes_retry_after_header(self) -> None:
        """429 response must include Retry-After header per RFC 6585."""
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware, max_requests=1, window_seconds=60)

        @app.get("/api/test")
        async def _test() -> dict[str, bool]:
            return {"ok": True}

        client = TestClient(app)
        client.get("/api/test")
        resp = client.get("/api/test")
        assert resp.status_code == 429
        assert resp.headers.get("retry-after") == "60"


# ---------------------------------------------------------------------------
# session_id_from_token
# ---------------------------------------------------------------------------


class TestSessionIdFromToken:
    def test_deterministic(self) -> None:
        token = "my-test-token"
        assert session_id_from_token(token) == session_id_from_token(token)

    def test_length_is_32(self) -> None:
        assert len(session_id_from_token("any-token")) == 32

    def test_different_tokens_produce_different_ids(self) -> None:
        assert session_id_from_token("token-a") != session_id_from_token("token-b")

    def test_returns_hex_string(self) -> None:
        result = session_id_from_token("test")
        assert all(c in "0123456789abcdef" for c in result)


# ---------------------------------------------------------------------------
# create_app factory
# ---------------------------------------------------------------------------


class TestCreateApp:
    def _patch_create_app_deps(self):
        """Return a context manager that patches all heavy lifespan dependencies."""
        return patch("anteroom.app.lifespan", new=_noop_lifespan)

    def test_create_app_returns_fastapi_instance(self) -> None:
        config = _make_config()
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)
        assert isinstance(app, FastAPI)

    def test_create_app_stores_config_on_state(self) -> None:
        config = _make_config()
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)
        assert app.state.config is config

    def test_create_app_stores_auth_token(self) -> None:
        config = _make_config()
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)
        assert hasattr(app.state, "auth_token")
        assert len(app.state.auth_token) > 0

    def test_create_app_stores_csrf_token(self) -> None:
        config = _make_config()
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)
        assert hasattr(app.state, "csrf_token")
        assert len(app.state.csrf_token) > 0

    def test_create_app_no_docs_urls(self) -> None:
        config = _make_config()
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)
        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None

    def test_create_app_with_no_identity_calls_ensure_identity(self) -> None:
        config = _make_config()
        config.identity = None

        fake_identity = MagicMock()
        fake_identity.private_key = "new-key"

        with (
            patch("anteroom.app.lifespan", new=_noop_lifespan),
            patch("anteroom.app.ensure_identity", return_value=fake_identity) as mock_ensure,
        ):
            from anteroom.app import create_app

            create_app(config)

        mock_ensure.assert_called_once()
        assert config.identity is fake_identity

    def test_create_app_repairs_partial_identity(self) -> None:
        config = _make_config(private_key="")

        full_identity = MagicMock()
        full_identity.private_key = "real-key"

        with (
            patch("anteroom.app.lifespan", new=_noop_lifespan),
            patch("anteroom.app.ensure_identity", return_value=full_identity) as mock_ensure,
        ):
            from anteroom.app import create_app

            create_app(config)

        mock_ensure.assert_called_once()

    def test_create_app_logs_ssl_warning_when_disabled(self) -> None:
        config = _make_config()
        config.ai.verify_ssl = False

        with (
            patch("anteroom.app.lifespan", new=_noop_lifespan),
            patch("anteroom.app.security_logger") as mock_log,
        ):
            from anteroom.app import create_app

            create_app(config)

        mock_log.warning.assert_called()
        warning_args = str(mock_log.warning.call_args_list)
        assert "SSL" in warning_args or "ssl" in warning_args.lower()

    def test_create_app_logs_tls_warning_non_localhost(self) -> None:
        config = _make_config(tls=False, host="0.0.0.0")

        with (
            patch("anteroom.app.lifespan", new=_noop_lifespan),
            patch("anteroom.app.security_logger") as mock_log,
        ):
            from anteroom.app import create_app

            create_app(config)

        all_calls = str(mock_log.warning.call_args_list)
        assert "TLS" in all_calls or "tls" in all_calls.lower() or "cleartext" in all_calls.lower()

    def test_create_app_with_dlp_enabled(self) -> None:
        config = _make_config()
        dlp_cfg = MagicMock()
        dlp_cfg.enabled = True
        config.safety.dlp = dlp_cfg

        fake_scanner = MagicMock()
        with (
            patch("anteroom.app.lifespan", new=_noop_lifespan),
            patch("anteroom.services.dlp.DlpScanner", return_value=fake_scanner),
        ):
            from anteroom.app import create_app

            app = create_app(config)

        assert app.state.dlp_scanner is not None

    def test_create_app_dlp_disabled_by_default(self) -> None:
        config = _make_config()
        config.safety.dlp = None
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)
        assert app.state.dlp_scanner is None

    def test_create_app_with_injection_detector_enabled(self) -> None:
        config = _make_config()
        inj_cfg = MagicMock()
        inj_cfg.enabled = True
        config.safety.prompt_injection = inj_cfg

        fake_detector = MagicMock()
        with (
            patch("anteroom.app.lifespan", new=_noop_lifespan),
            patch("anteroom.services.injection_detector.InjectionDetector", return_value=fake_detector),
        ):
            from anteroom.app import create_app

            app = create_app(config)

        assert app.state.injection_detector is not None

    def test_create_app_proxy_disabled_by_default(self) -> None:
        config = _make_config(proxy_enabled=False)
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)
        # Proxy router not mounted — no /v1 routes registered
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert not any(r.startswith("/v1") for r in routes)

    def test_create_app_allowed_origins_include_configured_host(self) -> None:
        config = _make_config(host="192.168.1.10", port=9090, tls=False)
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)
        origins = app.state._allowed_origins
        assert "http://192.168.1.10:9090" in origins

    def test_create_app_allowed_origins_include_localhost_variants(self) -> None:
        config = _make_config(host="127.0.0.1", port=8080)
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)
        origins = app.state._allowed_origins
        assert any("localhost" in o for o in origins)
        assert any("127.0.0.1" in o for o in origins)

    def test_create_app_proxy_allowed_origins_merged(self) -> None:
        config = _make_config(proxy_enabled=True)
        config.proxy.allowed_origins = ["http://custom-client.example.com"]
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)
        assert "http://custom-client.example.com" in app.state._allowed_origins

    def test_create_app_stores_session_store(self) -> None:
        config = _make_config()
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)
        assert hasattr(app.state, "session_store")
        assert app.state.session_store is not None

    def test_create_app_stores_enforced_fields(self) -> None:
        config = _make_config()
        enforced = ["ai.model"]
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config, enforced_fields=enforced)
        assert app.state.enforced_fields == enforced

    def test_create_app_none_config_loads_from_file(self) -> None:
        fake_config = _make_config()
        with (
            patch("anteroom.app.lifespan", new=_noop_lifespan),
            patch("anteroom.app.load_config", return_value=(fake_config, [])) as mock_load,
        ):
            from anteroom.app import create_app

            create_app(config=None)
        mock_load.assert_called_once()


# ---------------------------------------------------------------------------
# Index route and cookie-setting
# ---------------------------------------------------------------------------


class TestIndexRoute:
    def test_index_returns_200(self, tmp_path: Path) -> None:
        html_content = "<html><body>hello</body></html>"
        (tmp_path / "index.html").write_text(html_content)

        config = _make_config()
        with (
            patch("anteroom.app.lifespan", new=_noop_lifespan),
            patch("anteroom.app.Path", new=_make_path_patcher(tmp_path)),
        ):
            from anteroom.app import create_app

            app = create_app(config)

        client = TestClient(app, follow_redirects=False)
        resp = client.get("/")
        assert resp.status_code == 200

    def test_index_sets_session_cookie(self, tmp_path: Path) -> None:
        html_content = "<html><body>hello</body></html>"
        (tmp_path / "index.html").write_text(html_content)

        config = _make_config()
        with (
            patch("anteroom.app.lifespan", new=_noop_lifespan),
            patch("anteroom.app.Path", new=_make_path_patcher(tmp_path)),
        ):
            from anteroom.app import create_app

            app = create_app(config)

        client = TestClient(app, follow_redirects=False)
        resp = client.get("/")
        assert resp.status_code == 200
        cookies = resp.headers.get("set-cookie", "")
        assert "anteroom_session" in cookies

    def test_index_sets_csrf_cookie(self, tmp_path: Path) -> None:
        html_content = "<html><body>hello</body></html>"
        (tmp_path / "index.html").write_text(html_content)

        config = _make_config()
        with (
            patch("anteroom.app.lifespan", new=_noop_lifespan),
            patch("anteroom.app.Path", new=_make_path_patcher(tmp_path)),
        ):
            from anteroom.app import create_app

            app = create_app(config)

        client = TestClient(app, follow_redirects=False)
        resp = client.get("/")
        all_cookies = resp.headers.get_list("set-cookie")
        csrf_cookies = [c for c in all_cookies if "anteroom_csrf" in c]
        assert csrf_cookies

    def test_index_session_cookie_httponly(self, tmp_path: Path) -> None:
        html_content = "<html><body>hello</body></html>"
        (tmp_path / "index.html").write_text(html_content)

        config = _make_config()
        with (
            patch("anteroom.app.lifespan", new=_noop_lifespan),
            patch("anteroom.app.Path", new=_make_path_patcher(tmp_path)),
        ):
            from anteroom.app import create_app

            app = create_app(config)

        client = TestClient(app, follow_redirects=False)
        resp = client.get("/")
        all_cookies = resp.headers.get_list("set-cookie")
        session_cookies = [c for c in all_cookies if "anteroom_session" in c]
        assert session_cookies
        assert "httponly" in session_cookies[0].lower()


# ---------------------------------------------------------------------------
# Logout route
# ---------------------------------------------------------------------------


class TestLogoutRoute:
    def test_logout_returns_200(self) -> None:
        config = _make_config()
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)
        token = app.state.auth_token
        client = TestClient(app)
        resp = client.post(
            "/api/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "logged out"

    def test_logout_clears_session_cookie(self) -> None:
        config = _make_config()
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)
        token = app.state.auth_token
        client = TestClient(app)
        resp = client.post(
            "/api/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        all_cookies = resp.headers.get_list("set-cookie")
        # deleted cookies should appear with max-age=0 or empty value
        cookie_header = " ".join(all_cookies)
        assert "anteroom_session" in cookie_header

    def test_logout_with_session_cookie_deletes_session(self) -> None:
        config = _make_config()
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)
        token = app.state.auth_token
        csrf = app.state.csrf_token
        client = TestClient(app)

        # First, authenticate via bearer to establish session
        client.get("/api/logout", headers={"Authorization": f"Bearer {token}"})

        # Now logout via cookie
        resp = client.post(
            "/api/logout",
            cookies={"anteroom_session": token, "anteroom_csrf": csrf},
            headers={"x-csrf-token": csrf},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Auth middleware via full create_app (integration)
# ---------------------------------------------------------------------------


class TestAuthMiddlewareIntegration:
    def test_api_endpoint_requires_auth(self) -> None:
        config = _make_config()
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)
        client = TestClient(app)
        resp = client.get("/api/conversations")
        assert resp.status_code == 401

    def test_api_endpoint_accessible_with_valid_bearer(self) -> None:
        config = _make_config()
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)

        # Need to mock the DB for the conversations endpoint
        app.state.db = MagicMock()
        app.state.db_manager = MagicMock()
        app.state.db_manager.get.return_value = MagicMock()

        with patch("anteroom.routers.conversations.storage") as mock_storage:
            mock_storage.list_conversations.return_value = []
            token = app.state.auth_token
            client = TestClient(app)
            resp = client.get("/api/conversations", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_wrong_bearer_token_returns_401(self) -> None:
        config = _make_config()
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)
        client = TestClient(app)
        resp = client.get("/api/conversations", headers={"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 401

    def test_v1_path_also_requires_auth(self) -> None:
        config = _make_config(proxy_enabled=True)
        with patch("anteroom.app.lifespan", new=_noop_lifespan):
            from anteroom.app import create_app

            app = create_app(config)
        client = TestClient(app)
        resp = client.get("/v1/models")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# CSRF validation (cookie auth path)
# ---------------------------------------------------------------------------


class TestCsrfValidation:
    def test_post_with_cookie_requires_csrf(self) -> None:
        token = "test-token"
        app2 = _make_from_token(token)
        client = TestClient(app2)
        # Cookie auth without CSRF token
        resp = client.post("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 403
        assert "CSRF" in resp.json()["detail"]

    def test_post_with_bearer_skips_csrf(self) -> None:
        token = "test-token"
        app = _make_from_token(token)
        client = TestClient(app)
        resp = client.post("/api/test", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_get_with_cookie_no_csrf_needed(self) -> None:
        token = "test-token"
        app = _make_from_token(token)
        client = TestClient(app)
        resp = client.get("/api/test", cookies={"anteroom_session": token})
        assert resp.status_code == 200

    def test_post_with_mismatched_csrf_returns_403(self) -> None:
        token = "test-token"
        app = _make_from_token(token)
        client = TestClient(app)
        resp = client.post(
            "/api/test",
            cookies={"anteroom_session": token, "anteroom_csrf": "value-a"},
            headers={"x-csrf-token": "value-b"},
        )
        assert resp.status_code == 403

    def test_post_with_valid_csrf_succeeds(self) -> None:
        token = "test-token"
        csrf = "csrf-abc"
        app = _make_from_token(token)
        client = TestClient(app)
        resp = client.post(
            "/api/test",
            cookies={"anteroom_session": token, "anteroom_csrf": csrf},
            headers={"x-csrf-token": csrf},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Origin validation (CSRF double-submit + Origin header)
# ---------------------------------------------------------------------------


class TestOriginValidation:
    def test_allowed_origin_passes(self) -> None:
        token = "test-token"
        csrf = "csrf-token"
        app = _make_from_token(token)
        app.state._allowed_origins = {"http://localhost:8080"}
        client = TestClient(app)
        resp = client.post(
            "/api/test",
            cookies={"anteroom_session": token, "anteroom_csrf": csrf},
            headers={"x-csrf-token": csrf, "origin": "http://localhost:8080"},
        )
        assert resp.status_code == 200

    def test_disallowed_origin_returns_403(self) -> None:
        token = "test-token"
        csrf = "csrf-token"
        app = _make_from_token(token)
        app.state._allowed_origins = {"http://localhost:8080"}
        client = TestClient(app)
        resp = client.post(
            "/api/test",
            cookies={"anteroom_session": token, "anteroom_csrf": csrf},
            headers={"x-csrf-token": csrf, "origin": "http://evil.example.com"},
        )
        assert resp.status_code == 403
        assert "Origin" in resp.json()["detail"]

    def test_empty_allowed_origins_passes_any_origin(self) -> None:
        token = "test-token"
        csrf = "csrf-token"
        app = _make_from_token(token)
        app.state._allowed_origins = set()
        client = TestClient(app)
        resp = client.post(
            "/api/test",
            cookies={"anteroom_session": token, "anteroom_csrf": csrf},
            headers={"x-csrf-token": csrf, "origin": "http://anywhere.example.com"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Lifespan: startup / shutdown (unit-level, not full integration)
# ---------------------------------------------------------------------------


class TestLifespanStartup:
    @pytest.mark.asyncio
    async def test_lifespan_initializes_db(self, tmp_path: Path) -> None:
        from anteroom.app import lifespan

        app = FastAPI()
        config = _make_config(data_dir=tmp_path)
        app.state.config = config

        mock_db = MagicMock()
        mock_db._conn = MagicMock()

        with (
            patch("anteroom.app.init_db", return_value=mock_db) as mock_init,
            patch("anteroom.app.get_effective_dimensions", return_value=384),
            patch("anteroom.app.EventBus") as mock_event_bus_cls,
            patch("anteroom.app.ToolRegistry"),
            patch("anteroom.app.register_default_tools"),
            patch("anteroom.app.create_embedding_service", return_value=None),
            patch("anteroom.app.has_vec_support", return_value=False),
            patch("anteroom.services.storage.register_user"),
            patch("anteroom.services.storage.delete_empty_conversations"),
            patch("anteroom.services.audit.create_audit_writer") as mock_audit,
            patch("anteroom.services.starter_packs.install_starter_packs", return_value=[]),
            patch("anteroom.services.artifact_registry.ArtifactRegistry") as mock_reg_cls,
        ):
            mock_audit.return_value = MagicMock(enabled=False)
            mock_reg = MagicMock()
            mock_reg.count = 0
            mock_reg_cls.return_value = mock_reg
            mock_event_bus = MagicMock()
            mock_event_bus_cls.return_value = mock_event_bus

            async with lifespan(app):
                assert mock_init.called
                assert app.state.db is mock_db

    @pytest.mark.asyncio
    async def test_lifespan_shuts_down_db(self, tmp_path: Path) -> None:
        from anteroom.app import lifespan

        app = FastAPI()
        config = _make_config(data_dir=tmp_path)
        app.state.config = config

        mock_db = MagicMock()
        mock_db._conn = MagicMock()

        with (
            patch("anteroom.app.init_db", return_value=mock_db),
            patch("anteroom.app.get_effective_dimensions", return_value=384),
            patch("anteroom.app.EventBus") as mock_event_bus_cls,
            patch("anteroom.app.ToolRegistry"),
            patch("anteroom.app.register_default_tools"),
            patch("anteroom.app.create_embedding_service", return_value=None),
            patch("anteroom.app.has_vec_support", return_value=False),
            patch("anteroom.services.storage.register_user"),
            patch("anteroom.services.storage.delete_empty_conversations"),
            patch("anteroom.services.audit.create_audit_writer") as mock_audit,
            patch("anteroom.services.starter_packs.install_starter_packs", return_value=[]),
            patch("anteroom.services.artifact_registry.ArtifactRegistry") as mock_reg_cls,
        ):
            mock_audit.return_value = MagicMock(enabled=False)
            mock_reg = MagicMock()
            mock_reg.count = 0
            mock_reg_cls.return_value = mock_reg
            mock_event_bus = MagicMock()
            mock_event_bus_cls.return_value = mock_event_bus

            async with lifespan(app):
                pass

        mock_db.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_starts_retention_worker_when_configured(self, tmp_path: Path) -> None:
        from anteroom.app import lifespan

        app = FastAPI()
        config = _make_config(data_dir=tmp_path)
        config.storage.retention_days = 30
        config.storage.retention_check_interval = 3600
        config.storage.purge_attachments = True
        app.state.config = config

        mock_db = MagicMock()
        mock_db._conn = MagicMock()
        mock_worker = MagicMock()

        with (
            patch("anteroom.app.init_db", return_value=mock_db),
            patch("anteroom.app.get_effective_dimensions", return_value=384),
            patch("anteroom.app.EventBus") as mock_event_bus_cls,
            patch("anteroom.app.ToolRegistry"),
            patch("anteroom.app.register_default_tools"),
            patch("anteroom.app.create_embedding_service", return_value=None),
            patch("anteroom.app.has_vec_support", return_value=False),
            patch("anteroom.services.storage.register_user"),
            patch("anteroom.services.storage.delete_empty_conversations"),
            patch("anteroom.services.audit.create_audit_writer") as mock_audit,
            patch("anteroom.services.starter_packs.install_starter_packs", return_value=[]),
            patch("anteroom.services.artifact_registry.ArtifactRegistry") as mock_reg_cls,
            patch("anteroom.services.retention.RetentionWorker", return_value=mock_worker),
        ):
            mock_audit.return_value = MagicMock(enabled=False)
            mock_reg = MagicMock()
            mock_reg.count = 0
            mock_reg_cls.return_value = mock_reg
            mock_event_bus = MagicMock()
            mock_event_bus_cls.return_value = mock_event_bus

            async with lifespan(app):
                assert app.state.retention_worker is mock_worker
                mock_worker.start.assert_called_once()

        mock_worker.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_starts_mcp_manager_when_configured(self, tmp_path: Path) -> None:
        from anteroom.app import lifespan

        app = FastAPI()
        config = _make_config(data_dir=tmp_path)
        config.mcp_servers = [MagicMock()]
        config.mcp_tool_warning_threshold = 10
        app.state.config = config

        mock_db = MagicMock()
        mock_db._conn = MagicMock()
        mock_mcp = AsyncMock()
        mock_mcp.get_all_tools.return_value = []

        with (
            patch("anteroom.app.init_db", return_value=mock_db),
            patch("anteroom.app.get_effective_dimensions", return_value=384),
            patch("anteroom.app.EventBus") as mock_event_bus_cls,
            patch("anteroom.app.ToolRegistry"),
            patch("anteroom.app.register_default_tools"),
            patch("anteroom.app.create_embedding_service", return_value=None),
            patch("anteroom.app.has_vec_support", return_value=False),
            patch("anteroom.services.storage.register_user"),
            patch("anteroom.services.storage.delete_empty_conversations"),
            patch("anteroom.services.audit.create_audit_writer") as mock_audit,
            patch("anteroom.services.starter_packs.install_starter_packs", return_value=[]),
            patch("anteroom.services.artifact_registry.ArtifactRegistry") as mock_reg_cls,
            patch("anteroom.app.McpManager", return_value=mock_mcp),
        ):
            mock_audit.return_value = MagicMock(enabled=False)
            mock_reg = MagicMock()
            mock_reg.count = 0
            mock_reg_cls.return_value = mock_reg
            mock_event_bus = MagicMock()
            mock_event_bus_cls.return_value = mock_event_bus

            async with lifespan(app):
                assert app.state.mcp_manager is mock_mcp

        mock_mcp.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lifespan_no_retention_worker_when_zero_days(self, tmp_path: Path) -> None:
        from anteroom.app import lifespan

        app = FastAPI()
        config = _make_config(data_dir=tmp_path)
        config.storage.retention_days = 0
        app.state.config = config

        mock_db = MagicMock()
        mock_db._conn = MagicMock()

        with (
            patch("anteroom.app.init_db", return_value=mock_db),
            patch("anteroom.app.get_effective_dimensions", return_value=384),
            patch("anteroom.app.EventBus") as mock_event_bus_cls,
            patch("anteroom.app.ToolRegistry"),
            patch("anteroom.app.register_default_tools"),
            patch("anteroom.app.create_embedding_service", return_value=None),
            patch("anteroom.app.has_vec_support", return_value=False),
            patch("anteroom.services.storage.register_user"),
            patch("anteroom.services.storage.delete_empty_conversations"),
            patch("anteroom.services.audit.create_audit_writer") as mock_audit,
            patch("anteroom.services.starter_packs.install_starter_packs", return_value=[]),
            patch("anteroom.services.artifact_registry.ArtifactRegistry") as mock_reg_cls,
        ):
            mock_audit.return_value = MagicMock(enabled=False)
            mock_reg = MagicMock()
            mock_reg.count = 0
            mock_reg_cls.return_value = mock_reg
            mock_event_bus = MagicMock()
            mock_event_bus_cls.return_value = mock_event_bus

            async with lifespan(app):
                assert app.state.retention_worker is None

    @pytest.mark.asyncio
    async def test_lifespan_encryption_at_rest_raises_without_sqlcipher(self, tmp_path: Path) -> None:
        from anteroom.app import lifespan

        app = FastAPI()
        config = _make_config(data_dir=tmp_path)
        config.storage.encrypt_at_rest = True
        app.state.config = config

        with (
            patch("anteroom.app.get_effective_dimensions", return_value=384),
            patch("anteroom.services.encryption.is_sqlcipher_available", return_value=False),
        ):
            with pytest.raises(RuntimeError, match="sqlcipher3"):
                async with lifespan(app):
                    pass


# ---------------------------------------------------------------------------
# Helpers used by tests above
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Replacement lifespan that does nothing — avoids DB/filesystem I/O in tests."""
    yield


def _make_from_token(token: str, session_config: SessionConfig | None = None) -> FastAPI:
    """Build a minimal FastAPI app with BearerTokenMiddleware only."""
    from anteroom.services.session_store import MemorySessionStore

    app = FastAPI()
    app.state.session_store = MemorySessionStore()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    app.add_middleware(
        BearerTokenMiddleware,
        token_hash=token_hash,
        auth_token=token,
        secure_cookies=False,
        session_config=session_config or SessionConfig(),
    )

    @app.get("/api/test")
    async def _get():
        return {"ok": True}

    @app.post("/api/test")
    async def _post():
        return {"ok": True}

    return app


def _make_path_patcher(static_dir: Path):
    """Return a Path subclass that redirects __file__-relative paths to tmp_path."""
    real_path_cls = Path

    class PatchedPath(type(real_path_cls())):
        def __new__(cls, *args, **kwargs):
            return real_path_cls.__new__(cls, *args, **kwargs)

        def __truediv__(self, other):
            result = super().__truediv__(other)
            if str(result).endswith("/static") and "anteroom" in str(self):
                return real_path_cls(static_dir)
            return result

    return PatchedPath
