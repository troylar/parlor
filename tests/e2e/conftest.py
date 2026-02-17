"""Shared fixtures for end-to-end tests (Playwright + MCP)."""

from __future__ import annotations

import json
import shutil
import socket
import threading
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Generator
from unittest.mock import patch

import httpx
import pytest
import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware

from anteroom.config import (
    AIConfig,
    AppConfig,
    AppSettings,
    EmbeddingsConfig,
    McpServerConfig,
    SafetyConfig,
)

HAS_UVX = shutil.which("uvx") is not None
HAS_NPX = shutil.which("npx") is not None

requires_uvx = pytest.mark.skipif(not HAS_UVX, reason="uvx not available on PATH")
requires_npx = pytest.mark.skipif(not HAS_NPX, reason="npx not available on PATH")
requires_mcp = pytest.mark.skipif(not (HAS_UVX or HAS_NPX), reason="neither uvx nor npx available")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _NoopRateLimiter(BaseHTTPMiddleware):
    """Pass-through middleware that replaces the rate limiter during e2e tests."""

    def __init__(self, app, **kwargs):
        super().__init__(app)

    async def dispatch(self, request, call_next):
        return await call_next(request)


class _Server:
    """Manages a uvicorn server in a background thread."""

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


# ---------------------------------------------------------------------------
# Original e2e fixtures (no MCP)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app_server(tmp_path_factory: pytest.TempPathFactory) -> Generator[tuple[str, Path], None, None]:
    """Start a real Anteroom server on a random port with a temp SQLite DB.

    Yields (base_url, data_dir).
    """
    data_dir = tmp_path_factory.mktemp("anteroom_e2e")
    port = _free_port()

    config = AppConfig(
        ai=AIConfig(
            base_url="http://localhost:1/v1",  # dummy — e2e tests don't call AI
            api_key="test-key-not-real",
            model="gpt-4",
        ),
        app=AppSettings(
            host="127.0.0.1",
            port=port,
            data_dir=data_dir,
            tls=False,
        ),
        embeddings=EmbeddingsConfig(enabled=False),
    )

    from anteroom.app import create_app

    with patch("anteroom.app.RateLimitMiddleware", _NoopRateLimiter):
        app = create_app(config)

    server = _Server(app, "127.0.0.1", port)
    server.start()

    base_url = f"http://127.0.0.1:{port}"
    yield base_url, data_dir

    server.stop()


@pytest.fixture(scope="session")
def base_url(app_server: tuple[str, Path]) -> str:
    return app_server[0]


@pytest.fixture(scope="session")
def _session_cookies(base_url: str) -> dict[str, str]:
    """Fetch the index page once to get session + CSRF cookies."""
    resp = httpx.get(f"{base_url}/", follow_redirects=True)
    resp.raise_for_status()
    cookies: dict[str, str] = {}
    for cookie in resp.cookies.jar:
        cookies[cookie.name] = cookie.value
    return cookies


@pytest.fixture()
def api_client(base_url: str, _session_cookies: dict[str, str]) -> Generator[httpx.Client, None, None]:
    """Synchronous httpx client pre-configured with auth cookies + CSRF header."""
    session_token = _session_cookies.get("anteroom_session", "")
    csrf_token = _session_cookies.get("anteroom_csrf", "")
    with httpx.Client(
        base_url=base_url,
        cookies={"anteroom_session": session_token, "anteroom_csrf": csrf_token},
        headers={"X-CSRF-Token": csrf_token},
        timeout=10,
    ) as client:
        yield client


@pytest.fixture()
def conversation_id(api_client: httpx.Client) -> str:
    """Create a fresh conversation and return its id."""
    resp = api_client.post("/api/conversations", json={"title": "E2E Test"})
    resp.raise_for_status()
    return resp.json()["id"]


@pytest.fixture()
def canvas_data(api_client: httpx.Client, conversation_id: str) -> dict:
    """Create a conversation with a canvas attached. Returns canvas dict."""
    resp = api_client.post(
        f"/api/conversations/{conversation_id}/canvas",
        json={"title": "Test Canvas", "content": "# Hello\n\nWorld"},
    )
    resp.raise_for_status()
    data = resp.json()
    data["conversation_id"] = conversation_id
    return data


@pytest.fixture()
def authenticated_page(page, base_url: str):
    """Navigate to the app root so auth cookies are set, then return the page.

    Uses 'domcontentloaded' instead of 'networkidle' because the app opens
    SSE streams that keep the network permanently active.
    """
    page.goto(f"{base_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector("#btn-send", timeout=10000)
    return page


@pytest.fixture()
def page_with_conversation(authenticated_page, api_client: httpx.Client):
    """Authenticated page with a fresh conversation loaded in the sidebar."""
    resp = api_client.post("/api/conversations", json={"title": "Page Test"})
    resp.raise_for_status()
    conv_id = resp.json()["id"]

    page = authenticated_page
    page.evaluate(
        """(convId) => {
            if (typeof App !== 'undefined' && App.loadConversation) {
                App.loadConversation(convId);
            }
        }""",
        conv_id,
    )
    page.wait_for_timeout(500)
    return page, conv_id


# ---------------------------------------------------------------------------
# MCP e2e fixtures
# ---------------------------------------------------------------------------

MCP_TIME_SERVER = McpServerConfig(
    name="time",
    transport="stdio",
    command="uvx",
    args=["mcp-server-time"],
    timeout=30.0,
)

MCP_EVERYTHING_SERVER = McpServerConfig(
    name="everything",
    transport="stdio",
    command="npx",
    args=["-y", "@modelcontextprotocol/server-everything"],
    timeout=30.0,
)


def _mcp_server_configs() -> list[McpServerConfig]:
    configs: list[McpServerConfig] = []
    if HAS_UVX:
        configs.append(MCP_TIME_SERVER)
    if HAS_NPX:
        configs.append(MCP_EVERYTHING_SERVER)
    return configs


def _create_mcp_server(
    tmp_path_factory: pytest.TempPathFactory,
    approval_mode: str,
    dir_suffix: str,
) -> Generator[tuple[str, Path], None, None]:
    """Shared helper to start an MCP-enabled server with the given approval mode."""
    configs = _mcp_server_configs()
    if not configs:
        pytest.skip("No MCP servers available (need uvx or npx)")

    data_dir = tmp_path_factory.mktemp(f"anteroom_mcp_{dir_suffix}")
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
        mcp_servers=configs,
        embeddings=EmbeddingsConfig(enabled=False),
        safety=SafetyConfig(approval_mode=approval_mode),
    )

    from anteroom.app import create_app

    with patch("anteroom.app.RateLimitMiddleware", _NoopRateLimiter):
        app = create_app(config)

    server = _Server(app, "127.0.0.1", port)
    server.start()

    # Wait extra for MCP servers to connect (they spawn subprocesses)
    time.sleep(3)

    base_url = f"http://127.0.0.1:{port}"
    yield base_url, data_dir

    server.stop()


@pytest.fixture(scope="session")
def mcp_app_server(tmp_path_factory: pytest.TempPathFactory) -> Generator[tuple[str, Path], None, None]:
    """Start an Anteroom server with real MCP servers and auto approval mode."""
    yield from _create_mcp_server(tmp_path_factory, approval_mode="auto", dir_suffix="auto")


@pytest.fixture(scope="session")
def mcp_approval_server(tmp_path_factory: pytest.TempPathFactory) -> Generator[tuple[str, Path, Any], None, None]:
    """Start an Anteroom server with real MCP servers and ask_for_writes approval mode.

    Yields (base_url, data_dir, app) — includes the FastAPI app for direct state access.
    """
    configs = _mcp_server_configs()
    if not configs:
        pytest.skip("No MCP servers available (need uvx or npx)")

    data_dir = tmp_path_factory.mktemp("anteroom_mcp_approval")
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
        mcp_servers=configs,
        embeddings=EmbeddingsConfig(enabled=False),
        safety=SafetyConfig(approval_mode="ask_for_writes"),
    )

    from anteroom.app import create_app

    with patch("anteroom.app.RateLimitMiddleware", _NoopRateLimiter):
        app = create_app(config)

    server = _Server(app, "127.0.0.1", port)
    server.start()
    time.sleep(3)

    base_url = f"http://127.0.0.1:{port}"
    yield base_url, data_dir, app

    server.stop()


@pytest.fixture(scope="session")
def mcp_base_url(mcp_app_server: tuple[str, Path]) -> str:
    return mcp_app_server[0]


@pytest.fixture(scope="session")
def _mcp_session_cookies(mcp_base_url: str) -> dict[str, str]:
    resp = httpx.get(f"{mcp_base_url}/", follow_redirects=True)
    resp.raise_for_status()
    cookies: dict[str, str] = {}
    for cookie in resp.cookies.jar:
        cookies[cookie.name] = cookie.value
    return cookies


@pytest.fixture()
def mcp_api_client(mcp_base_url: str, _mcp_session_cookies: dict[str, str]) -> Generator[httpx.Client, None, None]:
    """httpx client for the MCP-enabled server, with auth cookies + CSRF."""
    session_token = _mcp_session_cookies.get("anteroom_session", "")
    csrf_token = _mcp_session_cookies.get("anteroom_csrf", "")
    with httpx.Client(
        base_url=mcp_base_url,
        cookies={"anteroom_session": session_token, "anteroom_csrf": csrf_token},
        headers={"X-CSRF-Token": csrf_token},
        timeout=30,
    ) as client:
        yield client


@pytest.fixture()
def mcp_conversation_id(mcp_api_client: httpx.Client) -> str:
    resp = mcp_api_client.post("/api/conversations", json={"title": "MCP E2E Test"})
    resp.raise_for_status()
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# MCP approval-mode fixtures (ask_for_writes)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def mcp_approval_base_url(mcp_approval_server: tuple[str, Path, Any]) -> str:
    return mcp_approval_server[0]


@pytest.fixture(scope="session")
def mcp_approval_app(mcp_approval_server: tuple[str, Path, Any]) -> Any:
    """Direct reference to the FastAPI app for state inspection."""
    return mcp_approval_server[2]


@pytest.fixture(scope="session")
def _mcp_approval_cookies(mcp_approval_base_url: str) -> dict[str, str]:
    resp = httpx.get(f"{mcp_approval_base_url}/", follow_redirects=True)
    resp.raise_for_status()
    cookies: dict[str, str] = {}
    for cookie in resp.cookies.jar:
        cookies[cookie.name] = cookie.value
    return cookies


@pytest.fixture()
def mcp_approval_client(
    mcp_approval_base_url: str, _mcp_approval_cookies: dict[str, str]
) -> Generator[httpx.Client, None, None]:
    """httpx client for the approval-mode MCP server."""
    session_token = _mcp_approval_cookies.get("anteroom_session", "")
    csrf_token = _mcp_approval_cookies.get("anteroom_csrf", "")
    with httpx.Client(
        base_url=mcp_approval_base_url,
        cookies={"anteroom_session": session_token, "anteroom_csrf": csrf_token},
        headers={"X-CSRF-Token": csrf_token},
        timeout=30,
    ) as client:
        yield client


@pytest.fixture()
def mcp_approval_conversation_id(mcp_approval_client: httpx.Client) -> str:
    resp = mcp_approval_client.post("/api/conversations", json={"title": "MCP Approval Test"})
    resp.raise_for_status()
    return resp.json()["id"]


def parse_sse_events(response: httpx.Response) -> list[dict[str, Any]]:
    """Parse SSE text into a list of {event, data} dicts."""
    events: list[dict[str, Any]] = []
    current_event = "message"
    current_data_lines: list[str] = []

    for line in response.text.splitlines():
        if line.startswith("event:"):
            current_event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            current_data_lines.append(line[len("data:") :].strip())
        elif line == "":
            if current_data_lines:
                raw = "\n".join(current_data_lines)
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    data = raw
                events.append({"event": current_event, "data": data})
            current_event = "message"
            current_data_lines = []

    if current_data_lines:
        raw = "\n".join(current_data_lines)
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            data = raw
        events.append({"event": current_event, "data": data})

    return events


def mock_tool_call_stream(
    tool_name: str,
    arguments: dict[str, Any],
    tool_call_id: str = "call_test_001",
    follow_up_text: str = "Here is the result.",
) -> Any:
    """Create an async generator that mimics AIService.stream_chat.

    Yields a tool_call event, then on the next invocation yields a text response.
    """
    call_count = 0

    async def _stream(
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        cancel_event: Any = None,
        extra_system_prompt: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            yield {
                "event": "tool_call",
                "data": {
                    "id": tool_call_id,
                    "function_name": tool_name,
                    "arguments": arguments,
                },
            }
            yield {"event": "done", "data": {}}
        else:
            for token in follow_up_text.split():
                yield {"event": "token", "data": {"content": token + " "}}
            yield {"event": "done", "data": {}}

    return _stream
