"""Playwright browser tests for the welcome onboarding actions.

Clicks each of the 3 welcome action buttons in a real browser and verifies
the user-visible outcomes: input focus, settings modal, space creation flow.

Addresses PR #800 review: the dead-button regression class requires actual
browser interaction tests, not just HTML/JS string validation.
"""

from __future__ import annotations

import pytest

HAS_PLAYWRIGHT = True
try:
    from playwright.sync_api import Page, expect
except ImportError:
    HAS_PLAYWRIGHT = False

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed"),
]


class TestWelcomeActions:
    """Click each welcome action button and verify the outcome."""

    def _new_chat_page(self, page: Page, base_url: str) -> None:
        """Navigate to the app and click New Chat to get to the welcome screen."""
        page.goto(base_url)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_selector("#btn-send", timeout=10000)
        page.click("button:has-text('New Chat')")
        page.wait_for_selector("[data-action='chat']", timeout=5000)

    def test_start_chatting_focuses_input(self, page: Page, base_url: str) -> None:
        """Clicking 'Start chatting' should focus the message input."""
        self._new_chat_page(page, base_url)
        # Click the "Start chatting" button
        page.click("[data-action='chat']")
        # The message input should now be focused
        input_el = page.locator("#message-input")
        expect(input_el).to_be_focused()

    def test_configure_model_opens_settings(self, page: Page, base_url: str) -> None:
        """Clicking 'Configure your model' should open the settings modal."""
        self._new_chat_page(page, base_url)
        # Click the "Configure your model" button
        page.click("[data-action='settings']")
        # The settings modal should become visible
        settings_modal = page.locator("#settings-modal, .settings-modal, .modal")
        expect(settings_modal.first).to_be_visible(timeout=3000)

    def test_create_space_clicks_sidebar_button(self, page: Page, base_url: str) -> None:
        """Clicking 'Create a space' should click the sidebar space-add button."""
        self._new_chat_page(page, base_url)
        # Verify the btn-space-add exists and is wired up
        space_btn = page.locator("#btn-space-add")
        expect(space_btn).to_be_attached()
        # Set up a click listener on the real sidebar button
        page.evaluate("""() => {
            window._spaceClicked = false;
            const btn = document.getElementById('btn-space-add');
            btn.addEventListener('click', () => {
                window._spaceClicked = true;
            }, {once: true});
        }""")
        page.click("[data-action='space']")
        page.wait_for_timeout(500)
        was_clicked = page.evaluate("() => window._spaceClicked")
        assert was_clicked, "Create a space button should click #btn-space-add"

    def test_welcome_actions_not_blocked_by_csp(self, page: Page, base_url: str) -> None:
        """No CSP violations should occur when clicking welcome actions."""
        csp_errors: list[str] = []
        page.on("console", lambda msg: csp_errors.append(msg.text) if "violates" in msg.text.lower() else None)

        self._new_chat_page(page, base_url)

        # Click chat action — should not trigger CSP error
        page.click("[data-action='chat']")
        page.wait_for_timeout(200)

        assert len(csp_errors) == 0, f"CSP violations found: {csp_errors}"
