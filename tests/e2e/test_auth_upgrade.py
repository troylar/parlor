"""E2e tests for stale auth cookie handling on upgrade from pre-identity versions.

Tests the full server-side behavior: middleware 401 + fresh cookie, index route
cookie refresh, and partial-identity repair on server startup.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import uvicorn
import yaml
from starlette.middleware.base import BaseHTTPMiddleware

from anteroom.config import (
    AIConfig,
    AppConfig,
    AppSettings,
    EmbeddingsConfig,
    UserIdentity,
)

pytestmark = [pytest.mark.e2e]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _NoopRateLimiter(BaseHTTPMiddleware):
    def __init__(self, app, **kwargs):
        super().__init__(app)

    async def dispatch(self, request, call_next):
        return await call_next(request)


class _Server:
    def __init__(self, app, host: str, port: int) -> None:
        self.config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        self.server = uvicorn.Server(self.config)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self.server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.config.port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("Server did not start in time")

    def stop(self) -> None:
        self.server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)


def _start_server(
    data_dir: Path,
    identity: UserIdentity | None = None,
    config_path: Path | None = None,
) -> tuple[str, _Server]:
    """Start a real Anteroom server with the given identity config."""
    port = _free_port()

    config = AppConfig(
        ai=AIConfig(
            base_url="http://localhost:1/v1",
            api_key="test-key-not-real",
            model="gpt-4",
        ),
        app=AppSettings(
            host="127.0.0.1",
            port=port,
            data_dir=data_dir,
            tls=False,
        ),
        identity=identity,
        embeddings=EmbeddingsConfig(enabled=False),
    )

    from anteroom.app import create_app

    with patch("anteroom.app.RateLimitMiddleware", _NoopRateLimiter):
        if config_path:
            with patch("anteroom.config._get_config_path", return_value=config_path):
                app = create_app(config)
        else:
            app = create_app(config)

    server = _Server(app, "127.0.0.1", port)
    server.start()
    base_url = f"http://127.0.0.1:{port}"
    return base_url, server


class TestStaleCookieRecovery:
    """Test that users with stale cookies from pre-identity versions recover automatically."""

    def test_stale_cookie_gets_401_with_fresh_cookie_in_response(self, tmp_path: Path) -> None:
        """A request with a stale cookie should get 401 + Set-Cookie with the correct token."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        base_url, server = _start_server(data_dir)
        try:
            # First, get the real cookie from index
            resp = httpx.get(f"{base_url}/", follow_redirects=True)
            assert resp.status_code == 200
            real_cookie = None
            for cookie in resp.cookies.jar:
                if cookie.name == "anteroom_session":
                    real_cookie = cookie.value
            assert real_cookie is not None

            # Now make a request with a stale cookie
            resp = httpx.get(
                f"{base_url}/api/config",
                cookies={"anteroom_session": "old-pre-identity-random-token"},
            )
            assert resp.status_code == 401

            # The 401 response should include a Set-Cookie with the correct token
            set_cookie = resp.headers.get("set-cookie", "")
            assert "anteroom_session" in set_cookie
            assert real_cookie in set_cookie
        finally:
            server.stop()

    def test_index_route_always_sets_fresh_cookie(self, tmp_path: Path) -> None:
        """Visiting / should always set a valid session cookie, overwriting stale ones."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        base_url, server = _start_server(data_dir)
        try:
            # Visit index with a stale cookie
            resp = httpx.get(
                f"{base_url}/",
                cookies={"anteroom_session": "stale-token-from-old-version"},
                follow_redirects=True,
            )
            assert resp.status_code == 200

            # Extract the fresh cookie
            fresh_cookie = None
            for cookie in resp.cookies.jar:
                if cookie.name == "anteroom_session":
                    fresh_cookie = cookie.value
            assert fresh_cookie is not None
            assert fresh_cookie != "stale-token-from-old-version"

            # Use the fresh cookie to make an API call
            resp = httpx.get(
                f"{base_url}/api/config",
                cookies={"anteroom_session": fresh_cookie},
            )
            assert resp.status_code == 200
        finally:
            server.stop()

    def test_full_recovery_flow(self, tmp_path: Path) -> None:
        """Simulate the complete upgrade path: stale cookie -> 401 -> visit / -> working session."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        base_url, server = _start_server(data_dir)
        try:
            # Step 1: API call with stale cookie fails
            resp = httpx.get(
                f"{base_url}/api/config",
                cookies={"anteroom_session": "pre-identity-random-token"},
            )
            assert resp.status_code == 401

            # Step 2: Visit index to get fresh cookie (simulating _handle401() redirect)
            resp = httpx.get(f"{base_url}/", follow_redirects=True)
            assert resp.status_code == 200
            cookies: dict[str, str] = {}
            for cookie in resp.cookies.jar:
                cookies[cookie.name] = cookie.value
            assert "anteroom_session" in cookies
            assert "anteroom_csrf" in cookies

            # Step 3: API call with fresh cookies succeeds
            resp = httpx.get(
                f"{base_url}/api/config",
                cookies=cookies,
            )
            assert resp.status_code == 200
            assert "ai" in resp.json()
        finally:
            server.stop()


class TestPartialIdentityRepair:
    """Test that server startup repairs partial identity (user_id but no private_key)."""

    def test_server_with_partial_identity_produces_stable_token(self, tmp_path: Path) -> None:
        """When config has user_id but no private_key, server should repair and produce stable tokens."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        config_path = tmp_path / "config.yaml"

        # Write a partial identity config
        config_data = {
            "identity": {
                "user_id": "legacy-user-id",
                "display_name": "Legacy User",
                "public_key": "",
                "private_key": "",
            }
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        # Partial identity: user_id set, no private_key
        partial_identity = UserIdentity(
            user_id="legacy-user-id",
            display_name="Legacy User",
            public_key="",
            private_key="",
        )

        base_url, server = _start_server(data_dir, identity=partial_identity, config_path=config_path)
        try:
            # Get cookie from first visit
            resp1 = httpx.get(f"{base_url}/", follow_redirects=True)
            assert resp1.status_code == 200
            cookie1 = None
            for cookie in resp1.cookies.jar:
                if cookie.name == "anteroom_session":
                    cookie1 = cookie.value

            # Get cookie from second visit
            resp2 = httpx.get(f"{base_url}/", follow_redirects=True)
            cookie2 = None
            for cookie in resp2.cookies.jar:
                if cookie.name == "anteroom_session":
                    cookie2 = cookie.value

            # Both visits should get the same token (stable, not random)
            assert cookie1 is not None
            assert cookie2 is not None
            assert cookie1 == cookie2

            # The cookie should work for API calls
            resp = httpx.get(
                f"{base_url}/api/config",
                cookies={"anteroom_session": cookie1},
            )
            assert resp.status_code == 200
        finally:
            server.stop()

    def test_server_with_full_identity_works_normally(self, tmp_path: Path) -> None:
        """A server with a complete identity should work without any repair."""
        from anteroom.identity import generate_identity

        data_dir = tmp_path / "data"
        data_dir.mkdir()

        id_data = generate_identity("Test User")
        identity = UserIdentity(
            user_id=id_data["user_id"],
            display_name=id_data["display_name"],
            public_key=id_data["public_key"],
            private_key=id_data["private_key"],
        )

        base_url, server = _start_server(data_dir, identity=identity)
        try:
            resp = httpx.get(f"{base_url}/", follow_redirects=True)
            assert resp.status_code == 200

            cookies: dict[str, str] = {}
            for cookie in resp.cookies.jar:
                cookies[cookie.name] = cookie.value

            resp = httpx.get(f"{base_url}/api/config", cookies=cookies)
            assert resp.status_code == 200
        finally:
            server.stop()

    def test_server_with_no_identity_generates_fresh(self, tmp_path: Path) -> None:
        """A server with no identity at all should auto-generate one."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        base_url, server = _start_server(data_dir, identity=None)
        try:
            resp = httpx.get(f"{base_url}/", follow_redirects=True)
            assert resp.status_code == 200

            cookies: dict[str, str] = {}
            for cookie in resp.cookies.jar:
                cookies[cookie.name] = cookie.value
            assert "anteroom_session" in cookies

            resp = httpx.get(f"{base_url}/api/config", cookies=cookies)
            assert resp.status_code == 200
        finally:
            server.stop()


class TestConcurrent401Recovery:
    """Test that concurrent API calls hitting 401 don't cause issues."""

    def test_multiple_401s_all_get_fresh_cookie(self, tmp_path: Path) -> None:
        """Multiple simultaneous requests with stale cookies should all get fresh cookies."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        base_url, server = _start_server(data_dir)
        try:
            # Get the real token first
            resp = httpx.get(f"{base_url}/", follow_redirects=True)
            real_cookie = None
            for cookie in resp.cookies.jar:
                if cookie.name == "anteroom_session":
                    real_cookie = cookie.value

            # Fire multiple requests with stale cookies
            stale_cookies = {"anteroom_session": "stale-token"}
            responses = []
            for _ in range(5):
                r = httpx.get(f"{base_url}/api/config", cookies=stale_cookies)
                responses.append(r)

            # All should be 401
            for r in responses:
                assert r.status_code == 401

            # All should have the fresh cookie in Set-Cookie
            for r in responses:
                set_cookie = r.headers.get("set-cookie", "")
                assert "anteroom_session" in set_cookie
                assert real_cookie in set_cookie
        finally:
            server.stop()
