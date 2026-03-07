"""E2E smoke tests for critical UI flows.

Tests the real server with no mocking of middleware or routing.
Catches regressions like the v1.3.0 Content-Type/CSP issue (#92).

Acceptance criteria from #93:
- Boot server, load page, verify no console errors (CSP violations)
- Click "New Chat", verify conversation is created
- Send a message, verify SSE stream starts
- Create a conversation with a space selected
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from .conftest import parse_sse_events

pytestmark = [pytest.mark.e2e]


# ---------------------------------------------------------------------------
# API-level smoke tests (httpx — no browser needed)
# ---------------------------------------------------------------------------


class TestServerBootstrap:
    """Verify the server starts and serves the index page correctly.

    These tests use raw httpx (no auth) to verify the unauthenticated
    index route works correctly — this is intentional since GET / must
    be accessible without a session to bootstrap cookies.
    """

    def test_index_returns_200_with_cookies(self, base_url: str) -> None:
        """GET / should return 200 and set both session and CSRF cookies."""
        import httpx

        resp = httpx.get(f"{base_url}/", follow_redirects=True)
        assert resp.status_code == 200

        cookie_names = {c.name for c in resp.cookies.jar}
        assert "anteroom_session" in cookie_names
        assert "anteroom_csrf" in cookie_names

    def test_index_returns_valid_html(self, base_url: str) -> None:
        """GET / should return HTML with essential UI elements."""
        import httpx

        resp = httpx.get(f"{base_url}/", follow_redirects=True)
        assert "text/html" in resp.headers.get("content-type", "")
        assert "btn-send" in resp.text
        assert "message-input" in resp.text
        assert "btn-new-chat" in resp.text

    def test_welcome_actions_present(self, base_url: str) -> None:
        """Welcome screen should have task-based onboarding action buttons."""
        import httpx

        resp = httpx.get(f"{base_url}/", follow_redirects=True)
        html = resp.text
        assert 'data-action="chat"' in html
        assert 'data-action="settings"' in html
        assert 'data-action="space"' in html
        assert "welcome-actions" in html
        assert "Start chatting" in html
        assert "Configure your model" in html
        assert "Create a space" in html

    def test_welcome_actions_no_inline_handlers(self, base_url: str) -> None:
        """Welcome action buttons must not use inline onclick (CSP blocks them)."""
        import httpx

        resp = httpx.get(f"{base_url}/", follow_redirects=True)
        # Extract just the welcome-actions section
        start = resp.text.find("welcome-actions")
        end = resp.text.find("welcome-tips", start)
        section = resp.text[start:end] if start >= 0 and end >= 0 else ""
        assert "onclick" not in section, "Inline onclick handlers violate CSP"

    def test_welcome_chat_js_has_action_handler(self, base_url: str) -> None:
        """chat.js must contain the delegated click handler for welcome actions."""
        import httpx

        resp = httpx.get(f"{base_url}/", follow_redirects=True)
        html = resp.text
        # Extract the chat.js URL from the HTML
        import re

        match = re.search(r'src="(/js/chat\.js[^"]*)"', html)
        assert match, "chat.js script tag not found"
        js_resp = httpx.get(f"{base_url}{match.group(1)}")
        js_resp.raise_for_status()
        js = js_resp.text
        # Verify the handler dispatches all 3 actions
        assert "data-action" in js, "click handler must use data-action selector"
        assert 'action === "chat"' in js or "action === 'chat'" in js, "handler must dispatch chat action"
        assert 'action === "settings"' in js or "action === 'settings'" in js, "handler must dispatch settings action"
        assert 'action === "space"' in js or "action === 'space'" in js, "handler must dispatch space action"
        assert "openSettings" in js, "handler must call openSettings for settings action"
        assert "btn-space-add" in js, "handler must target btn-space-add for space action"
        assert ".focus()" in js, "handler must focus input for chat action"

    def test_welcome_html_and_js_actions_match(self, base_url: str) -> None:
        """The data-action values in the HTML must match those handled in chat.js."""
        import re

        import httpx

        resp = httpx.get(f"{base_url}/", follow_redirects=True)
        html_actions = set(re.findall(r'data-action="(\w+)"', resp.text))
        assert html_actions == {"chat", "settings", "space"}
        # Also verify chat.js _getWelcomeHtml produces the same actions
        match = re.search(r'src="(/js/chat\.js[^"]*)"', resp.text)
        assert match
        js = httpx.get(f"{base_url}{match.group(1)}").text
        js_actions = set(re.findall(r'data-action="(\w+)"', js))
        # JS template should define the same 3 actions
        assert js_actions == html_actions, f"HTML actions {html_actions} != JS template actions {js_actions}"

    def test_security_headers_present(self, base_url: str) -> None:
        """Index response should include security headers."""
        import httpx

        resp = httpx.get(f"{base_url}/", follow_redirects=True)
        assert "content-security-policy" in resp.headers
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert "x-frame-options" in resp.headers


class TestAuthEnforcement:
    """Verify auth and CSRF middleware work end-to-end."""

    def test_unauthenticated_api_returns_401(self, base_url: str) -> None:
        """API requests without a valid session cookie should get 401."""
        import httpx

        resp = httpx.get(f"{base_url}/api/conversations")
        assert resp.status_code == 401

    def test_stale_cookie_returns_401(self, base_url: str) -> None:
        """API requests with a stale/invalid session cookie should get 401."""
        import httpx

        resp = httpx.get(
            f"{base_url}/api/conversations",
            cookies={"anteroom_session": "invalid-stale-token"},
        )
        assert resp.status_code == 401

    def test_missing_csrf_returns_403(self, base_url: str, _session_cookies: dict[str, str]) -> None:
        """State-changing requests without CSRF token should be rejected."""
        import httpx

        session_token = _session_cookies.get("anteroom_session", "")
        resp = httpx.post(
            f"{base_url}/api/conversations",
            cookies={"anteroom_session": session_token},
            json={"title": "No CSRF"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 403


class TestConversationCRUD:
    """Verify conversation creation and listing through the real server."""

    def test_create_conversation(self, api_client) -> None:
        """POST /api/conversations should create a new chat conversation."""
        resp = api_client.post("/api/conversations", json={"title": "Smoke Test Chat"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Smoke Test Chat"
        assert data["type"] == "chat"
        assert "id" in data

    def test_create_conversation_requires_content_type(self, api_client) -> None:
        """POST without Content-Type: application/json should be rejected.

        This catches the v1.3.0 regression (#92) where missing Content-Type
        headers caused 415 errors.
        """
        resp = api_client.post(
            "/api/conversations",
            content=b'{"title": "test"}',
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code in (415, 422)

    def test_list_conversations(self, api_client, conversation_id: str) -> None:
        """GET /api/conversations should return the created conversation."""
        resp = api_client.get("/api/conversations")
        assert resp.status_code == 200
        conversations = resp.json()
        assert any(c["id"] == conversation_id for c in conversations)

    def test_get_conversation_detail(self, api_client, conversation_id: str) -> None:
        """GET /api/conversations/{id} should return conversation with messages."""
        resp = api_client.get(f"/api/conversations/{conversation_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == conversation_id
        assert "messages" in data

    def test_get_nonexistent_conversation_returns_404(self, api_client) -> None:
        """GET /api/conversations/{bad-id} should return 404."""
        resp = api_client.get("/api/conversations/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


class TestChatSSEStream:
    """Verify that sending a message produces an SSE stream."""

    def test_send_message_returns_sse_stream(self, api_client, conversation_id: str) -> None:
        """POST /api/conversations/{id}/chat should return an SSE event stream.

        The AI endpoint is unreachable (dummy URL), so we expect an error event,
        but the key assertion is that the server starts an SSE stream at all
        (correct Content-Type, event format).
        """
        with api_client.stream(
            "POST",
            f"/api/conversations/{conversation_id}/chat",
            json={"message": "hello"},
            timeout=15,
        ) as resp:
            assert resp.status_code == 200
            content_type = resp.headers.get("content-type", "")
            assert "text/event-stream" in content_type

            body = resp.read().decode()

        events = parse_sse_events(SimpleNamespace(text=body))
        assert len(events) > 0
        event_types = {e["event"] for e in events}
        # Should end with either "done" or "error" (AI unreachable)
        assert event_types & {"done", "error"}

    def test_send_message_stores_user_message(self, api_client, conversation_id: str) -> None:
        """After sending a message, the user message should be persisted."""
        with api_client.stream(
            "POST",
            f"/api/conversations/{conversation_id}/chat",
            json={"message": "test persistence"},
            timeout=15,
        ) as resp:
            resp.read()

        detail = api_client.get(f"/api/conversations/{conversation_id}")
        assert detail.status_code == 200
        messages = detail.json()["messages"]
        user_messages = [m for m in messages if m["role"] == "user"]
        assert any("test persistence" in m["content"] for m in user_messages)


class TestSpaceConversation:
    """Verify creating a conversation linked to a space."""

    def test_create_space(self, api_client) -> None:
        """POST /api/spaces should create a new space."""
        resp = api_client.post(
            "/api/spaces",
            json={"name": "smoke-test-space", "instructions": "You are a test assistant."},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "smoke-test-space"
        assert "id" in data

    def test_create_conversation_with_space(self, api_client) -> None:
        """A conversation created with a space_id should be linked to that space."""
        space = api_client.post(
            "/api/spaces",
            json={"name": "space-link-test"},
        ).json()

        conv = api_client.post(
            "/api/conversations",
            json={"title": "Space Chat", "space_id": space["id"]},
        )
        assert conv.status_code == 201
        conv_data = conv.json()
        assert conv_data["space_id"] == space["id"]

    def test_list_conversations_by_space(self, api_client) -> None:
        """Conversations should be filterable by space_id."""
        space = api_client.post("/api/spaces", json={"name": "filter-test"}).json()
        api_client.post(
            "/api/conversations",
            json={"title": "In Space", "space_id": space["id"]},
        )
        api_client.post("/api/conversations", json={"title": "No Space"})

        resp = api_client.get(f"/api/conversations?space_id={space['id']}")
        assert resp.status_code == 200
        filtered = resp.json()
        assert any(c["title"] == "In Space" for c in filtered)
        assert not any(c["title"] == "No Space" for c in filtered)


# ---------------------------------------------------------------------------
# Playwright browser smoke tests (skip if Playwright unavailable)
# ---------------------------------------------------------------------------

try:
    import playwright  # noqa: F401

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

requires_playwright = pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed")


@requires_playwright
class TestBrowserPageLoad:
    """Verify the page loads in a real browser without errors."""

    def test_no_console_errors_on_load(self, authenticated_page) -> None:
        """Loading the page should produce no console errors."""
        console_errors: list[str] = []

        def on_console(msg):
            if msg.type == "error":
                console_errors.append(msg.text)

        page = authenticated_page
        page.on("console", on_console)

        page.reload()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_selector("#btn-send", timeout=10000)

        # EventSource to the dummy AI endpoint produces expected connection
        # errors — filter those out but catch everything else (CSP, JS errors)
        csp_violations = [e for e in console_errors if "Content-Security-Policy" in e or "Refused to" in e]
        assert csp_violations == [], f"CSP violations found: {csp_violations}"

        js_errors = [
            e
            for e in console_errors
            if "Content-Security-Policy" not in e
            and "Refused to" not in e
            and "ERR_CONNECTION_REFUSED" not in e  # expected: dummy AI endpoint
            and "/api/events" not in e  # expected: SSE reconnect to test server
        ]
        assert js_errors == [], f"Unexpected JS errors: {js_errors}"

    def test_essential_ui_elements_present(self, authenticated_page) -> None:
        """The page should have the sidebar, input, and send button."""
        page = authenticated_page
        assert page.locator("#sidebar").is_visible()
        assert page.locator("#message-input").is_visible()
        assert page.locator("#btn-send").is_visible()
        assert page.locator("#btn-new-chat").is_visible()


@requires_playwright
class TestBrowserNewChat:
    """Verify the New Chat button creates a conversation in the browser."""

    def test_click_new_chat_creates_conversation(self, authenticated_page, api_client) -> None:
        """Clicking 'New Chat' should create a conversation visible in the sidebar."""
        import time

        page = authenticated_page

        before = api_client.get("/api/conversations").json()
        before_count = len(before)

        page.locator("#btn-new-chat").click()

        # Poll the API for the new conversation (avoids wait_for_function
        # which requires unsafe-eval, blocked by CSP)
        deadline = time.monotonic() + 5
        after = before
        while time.monotonic() < deadline:
            after = api_client.get("/api/conversations").json()
            if len(after) > before_count:
                break
            time.sleep(0.2)

        assert len(after) > before_count

    def test_new_chat_activates_input(self, authenticated_page) -> None:
        """After clicking New Chat, the message input should be enabled."""
        page = authenticated_page
        page.locator("#btn-new-chat").click()
        page.wait_for_timeout(500)

        input_el = page.locator("#message-input")
        assert input_el.is_visible()
        assert input_el.is_enabled()


@requires_playwright
class TestConcurrent401BrowserRecovery:
    """Verify concurrent 401s don't produce the 'Could not recover' banner (#687)."""

    def test_concurrent_401s_recover_without_banner(self, page, base_url: str) -> None:
        """Multiple simultaneous 401 responses should trigger a single recovery redirect,
        not exhaust the retry counter and show the error banner.

        Without the _recovering guard: call 1 takes the "first 401" path (resets
        counter, queues redirect). Calls 2-5 see rapid 401s and increment the retry
        counter to 3+, showing the permanent banner. With the guard: only call 1
        proceeds, calls 2-5 are no-ops.
        """
        # Load page normally — auth cookies are set, init completes
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_selector("#btn-send", timeout=10000)

        # Call _handle401() 5 times synchronously. window.location.href = '/'
        # queues a navigation but JS continues executing synchronously.
        # The test checks that the banner does NOT appear despite 5 rapid calls.
        result = page.evaluate("""() => {
            // Verify _handle401 is accessible (prefixed with _ but exposed for testing)
            if (typeof App._handle401 !== 'function') {
                throw new Error('App._handle401 is not a function — export may have been removed');
            }

            // Clear any prior state
            sessionStorage.removeItem('_anteroom_401_ts');
            sessionStorage.removeItem('_anteroom_401_retries');

            // Fire 5 synchronous _handle401 calls (simulates concurrent 401s)
            for (let i = 0; i < 5; i++) {
                App._handle401();
            }

            return {
                bannerPresent: !!document.getElementById('auth-error-banner'),
                retries: sessionStorage.getItem('_anteroom_401_retries'),
            };
        }""")

        # The banner must NOT appear — the _recovering flag prevents counter exhaustion
        assert result["bannerPresent"] is False, "Banner should not appear from concurrent 401s"
        # Only the first call should have set retries to '0' (first-401 path);
        # remaining calls were no-ops
        assert result["retries"] == "0", f"Expected retries='0', got {result['retries']!r}"

    def test_banner_appears_after_genuine_repeated_failures(self, page, base_url: str) -> None:
        """The banner should still appear when recovery genuinely fails across
        multiple page loads (retry counter reaches 3 via separate redirect cycles).
        """
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_selector("#btn-send", timeout=10000)

        # Simulate the state of 3 prior failed recovery attempts by
        # pre-populating sessionStorage (as if 3 redirects already failed)
        page.evaluate("""() => {
            sessionStorage.setItem('_anteroom_401_ts', String(Date.now()));
            sessionStorage.setItem('_anteroom_401_retries', '3');
        }""")

        # A single 401 now should show the banner (retries >= 3, within 5s window)
        page.route(
            "**/api/config",
            lambda route: route.fulfill(
                status=401,
                body='{"detail":"Session expired"}',
                headers={"Content-Type": "application/json"},
            ),
        )

        page.evaluate("App.api('/api/config').catch(() => {})")

        # Wait for the banner to appear (replaces arbitrary timeout)
        page.wait_for_selector("#auth-error-banner", timeout=5000)

        # Banner SHOULD appear — genuine repeated failure
        assert page.locator("#auth-error-banner").count() == 1
        assert page.locator("#auth-error-banner button").text_content() == "Retry"

        # Clean up
        page.unroute("**/api/config")
        page.evaluate("""() => {
            sessionStorage.removeItem('_anteroom_401_ts');
            sessionStorage.removeItem('_anteroom_401_retries');
            const banner = document.getElementById('auth-error-banner');
            if (banner) banner.remove();
        }""")


@requires_playwright
class TestSpaceSessionPersistence:
    """Verify space selection persists across page reloads via sessionStorage (#745)."""

    def test_space_persists_after_reload(self, authenticated_page, api_client) -> None:
        """Select a space through the UI, reload, verify the dropdown and list reflect the selection."""
        page = authenticated_page

        # Create a space via API
        resp = api_client.post("/api/spaces", json={"name": "persist-test-space"})
        resp.raise_for_status()
        space_id = resp.json()["id"]

        try:
            # Reload to pick up the new space in the list
            page.reload()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_selector("#space-select", timeout=5000)

            # Click the space item in the list (real UI interaction)
            page.locator(".space-item-name:text('persist-test-space')").click()

            # Wait for the click handler to update state + re-render
            page.wait_for_timeout(500)

            # Verify the dropdown reflects the selection
            select_value = page.evaluate("() => document.getElementById('space-select').value")
            assert select_value == space_id, f"Expected select={space_id}, got {select_value}"

            # Reload the page — this is the actual test: does the app restore from sessionStorage?
            page.reload()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_selector("#space-select", timeout=10000)

            # Wait for loadSpaces() to fetch and restore
            page.wait_for_timeout(1000)

            # Assert the dropdown value was restored by the app (not just browser storage)
            restored_value = page.evaluate("() => document.getElementById('space-select').value")
            assert restored_value == space_id, f"Dropdown not restored: expected {space_id}, got {restored_value}"

            # Assert the space list item has the 'active' class
            active_item = page.locator(".space-item.active .space-item-name")
            assert active_item.count() == 1, "Expected exactly one active space item"
            assert active_item.text_content().startswith("persist-test-space")

        finally:
            page.evaluate("() => sessionStorage.removeItem('anteroom_space_id')")
            api_client.delete(f"/api/spaces/{space_id}")

    def test_deleted_space_falls_back_to_all(self, authenticated_page, api_client) -> None:
        """If the persisted space no longer exists, fall back to All Spaces in the UI."""
        page = authenticated_page

        # Create and immediately delete a space
        resp = api_client.post("/api/spaces", json={"name": "ephemeral-space"})
        resp.raise_for_status()
        space_id = resp.json()["id"]
        api_client.delete(f"/api/spaces/{space_id}")

        # Set sessionStorage to the now-deleted space (simulates prior session)
        page.evaluate(
            """(spaceId) => {
                sessionStorage.setItem('anteroom_space_id', spaceId);
            }""",
            space_id,
        )

        # Reload — the app should detect the space is gone and clear the selection
        page.reload()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_selector("#space-select", timeout=10000)

        # Wait for loadSpaces() to fetch list and run validation
        page.wait_for_timeout(1000)

        # sessionStorage should be cleared by the app
        stored = page.evaluate("() => sessionStorage.getItem('anteroom_space_id')")
        assert stored is None, f"Expected cleared storage, got {stored!r}"

        # Dropdown should show empty value (All Spaces)
        select_value = page.evaluate("() => document.getElementById('space-select').value")
        assert select_value == "", f"Dropdown should be empty, got {select_value!r}"

        # The "All Spaces" item should have the 'active' class
        all_spaces_active = page.locator(".space-item.space-all.active")
        assert all_spaces_active.count() == 1, "All Spaces should be the active item"
