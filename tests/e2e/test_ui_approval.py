"""E2E tests for approval and ask_user prompt rendering (#864).

Tests the three bugs fixed in #864:
1. Prompt cards insert after the last assistant message (correct ordering)
2. ask_user question text renders markdown (clickable file paths)
3. Resolved cards survive EventSource reconnect (selective cleanup)

Strategy: Use page.evaluate() to call Chat.showApprovalPrompt() and
Chat.showAskUserPrompt() directly, then assert on the rendered DOM.
This avoids needing a real AI + MCP flow while still testing the
actual browser rendering path.
"""

from __future__ import annotations

import pytest

try:
    import playwright  # noqa: F401

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

requires_playwright = pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed")

pytestmark = [pytest.mark.e2e, requires_playwright]


def _inject_messages(page, roles: list[str]) -> None:
    """Insert fake .message divs into #messages-container."""
    for role in roles:
        page.evaluate(
            """(role) => {
                const el = document.createElement('div');
                el.className = 'message ' + role;
                el.textContent = role + ' message';
                document.getElementById('messages-container').appendChild(el);
            }""",
            role,
        )


class TestApprovalPromptRendering:
    """Verify approval prompt card structure and positioning."""

    def test_approval_card_has_correct_structure(self, authenticated_page) -> None:
        page = authenticated_page
        page.evaluate(
            """() => {
                Chat.showApprovalPrompt({
                    approval_id: 'test-appr-1',
                    tool_name: 'bash',
                    reason: 'Running a shell command',
                    details: { command: 'rm -rf /tmp/test' },
                });
            }"""
        )

        card = page.locator('.approval-prompt[data-approval-id="test-appr-1"]')
        assert card.count() == 1

        assert card.locator(".approval-title").text_content() == "Approval Required"
        assert "Running a shell command" in card.locator(".approval-reason").text_content()
        assert card.locator(".approval-details code").text_content() == "rm -rf /tmp/test"

        assert card.locator(".approval-btn.approval-deny").count() == 1
        assert card.locator(".approval-btn.approval-allow").count() == 1
        assert card.locator(".approval-btn.approval-session").count() == 1
        assert card.locator(".approval-btn.approval-always").count() == 1

    def test_approval_card_inserts_after_last_assistant(self, authenticated_page) -> None:
        page = authenticated_page
        _inject_messages(page, ["assistant", "user"])

        page.evaluate(
            """() => {
                Chat.showApprovalPrompt({
                    approval_id: 'test-appr-order',
                    tool_name: 'write_file',
                    reason: 'Writing a file',
                });
            }"""
        )

        children = page.evaluate(
            """() => {
                const c = document.getElementById('messages-container');
                return [...c.children].map(el => {
                    if (el.classList.contains('approval-prompt')) return 'approval';
                    if (el.classList.contains('message')) {
                        return el.classList.contains('assistant') ? 'assistant' : 'user';
                    }
                    return 'other';
                });
            }"""
        )
        assert children == ["assistant", "approval", "user"]

    def test_approval_reason_renders_markdown(self, authenticated_page) -> None:
        page = authenticated_page
        page.evaluate(
            """() => {
                Chat.showApprovalPrompt({
                    approval_id: 'test-appr-md',
                    tool_name: 'write_file',
                    reason: 'Writing to `src/main.py`',
                });
            }"""
        )

        reason_el = page.locator(".approval-reason")
        code_el = reason_el.locator("code")
        assert code_el.count() >= 1
        assert "src/main.py" in code_el.first.text_content()


class TestAskUserPromptRendering:
    """Verify ask_user prompt card structure, positioning, and markdown."""

    def test_ask_user_card_has_correct_structure(self, authenticated_page) -> None:
        page = authenticated_page
        page.evaluate(
            """() => {
                Chat.showAskUserPrompt({
                    ask_id: 'test-ask-1',
                    question: 'What is the target directory?',
                });
            }"""
        )

        card = page.locator('.ask-user-prompt[data-ask-id="test-ask-1"]')
        assert card.count() == 1

        assert card.locator(".ask-user-title").count() == 1
        assert "target directory" in card.locator(".ask-user-question").text_content()
        has_input = (
            card.locator(".ask-user-input").count() == 1
            or card.locator("input").count() >= 1
            or card.locator("textarea").count() >= 1
        )
        assert has_input

    def test_ask_user_card_inserts_after_last_assistant(self, authenticated_page) -> None:
        page = authenticated_page
        _inject_messages(page, ["assistant", "user"])

        page.evaluate(
            """() => {
                Chat.showAskUserPrompt({
                    ask_id: 'test-ask-order',
                    question: 'Confirm?',
                });
            }"""
        )

        children = page.evaluate(
            """() => {
                const c = document.getElementById('messages-container');
                return [...c.children].map(el => {
                    if (el.classList.contains('ask-user-prompt')) return 'ask-user';
                    if (el.classList.contains('message')) {
                        return el.classList.contains('assistant') ? 'assistant' : 'user';
                    }
                    return 'other';
                });
            }"""
        )
        assert children == ["assistant", "ask-user", "user"]

    def test_ask_user_question_renders_markdown(self, authenticated_page) -> None:
        """Bug 2: question text should render markdown so file paths are clickable."""
        page = authenticated_page
        page.evaluate(
            """() => {
                Chat.showAskUserPrompt({
                    ask_id: 'test-ask-md',
                    question: 'Should I modify `src/anteroom/config.py`?',
                });
            }"""
        )

        question_el = page.locator(".ask-user-question")
        code_el = question_el.locator("code")
        assert code_el.count() >= 1
        assert "src/anteroom/config.py" in code_el.first.text_content()

    def test_ask_user_multiple_cards_ordered_correctly(self, authenticated_page) -> None:
        """Multiple prompt cards with interleaved assistant messages."""
        page = authenticated_page
        _inject_messages(page, ["assistant", "user", "assistant", "user"])

        page.evaluate(
            """() => {
                Chat.showAskUserPrompt({
                    ask_id: 'test-ask-multi',
                    question: 'Proceed?',
                });
            }"""
        )

        children = page.evaluate(
            """() => {
                const c = document.getElementById('messages-container');
                return [...c.children].map(el => {
                    if (el.classList.contains('ask-user-prompt')) return 'ask-user';
                    if (el.classList.contains('message')) {
                        return el.classList.contains('assistant') ? 'assistant' : 'user';
                    }
                    return 'other';
                });
            }"""
        )
        # Card should be after the LAST assistant, before the trailing user
        assert children == ["assistant", "user", "assistant", "ask-user", "user"]


class TestReconnectCleanup:
    """Bug 3: resolved cards survive reconnect, pending are removed."""

    def test_pending_approval_removed_on_reconnect(self, authenticated_page) -> None:
        page = authenticated_page
        page.evaluate(
            """() => {
                Chat.showApprovalPrompt({
                    approval_id: 'test-reconnect-pending',
                    tool_name: 'bash',
                    reason: 'Pending approval',
                });
            }"""
        )
        assert page.locator(".approval-prompt").count() == 1

        # Call the actual shared cleanup function used by app.js (#864)
        page.evaluate("() => Chat.cleanupPendingPrompts()")

        assert page.locator(".approval-prompt").count() == 0

    def test_resolved_approval_survives_reconnect(self, authenticated_page) -> None:
        page = authenticated_page
        page.evaluate(
            """() => {
                Chat.showApprovalPrompt({
                    approval_id: 'test-reconnect-resolved',
                    tool_name: 'bash',
                    reason: 'Will be resolved',
                });
                const el = document.querySelector('[data-approval-id="test-reconnect-resolved"]');
                el.classList.add('approval-allowed');
            }"""
        )

        page.evaluate("() => Chat.cleanupPendingPrompts()")

        assert page.locator(".approval-prompt.approval-allowed").count() == 1

    def test_resolved_ask_user_survives_reconnect(self, authenticated_page) -> None:
        page = authenticated_page
        page.evaluate(
            """() => {
                Chat.showAskUserPrompt({
                    ask_id: 'test-reconnect-ask-resolved',
                    question: 'Answered prompt',
                });
                const el = document.querySelector('[data-ask-id="test-reconnect-ask-resolved"]');
                el.classList.add('ask-user-answered');
            }"""
        )

        page.evaluate("() => Chat.cleanupPendingPrompts()")

        assert page.locator(".ask-user-prompt.ask-user-answered").count() == 1

    def test_mixed_pending_and_resolved_cleanup(self, authenticated_page) -> None:
        page = authenticated_page
        page.evaluate(
            """() => {
                // Pending approval
                Chat.showApprovalPrompt({
                    approval_id: 'mix-pending-appr',
                    tool_name: 'bash',
                    reason: 'Pending',
                });
                // Resolved approval
                Chat.showApprovalPrompt({
                    approval_id: 'mix-resolved-appr',
                    tool_name: 'bash',
                    reason: 'Resolved',
                });
                document.querySelector('[data-approval-id="mix-resolved-appr"]')
                    .classList.add('approval-allowed');

                // Pending ask_user
                Chat.showAskUserPrompt({
                    ask_id: 'mix-pending-ask',
                    question: 'Pending ask',
                });
                // Resolved ask_user
                Chat.showAskUserPrompt({
                    ask_id: 'mix-resolved-ask',
                    question: 'Resolved ask',
                });
                document.querySelector('[data-ask-id="mix-resolved-ask"]')
                    .classList.add('ask-user-answered');
            }"""
        )

        assert page.locator(".approval-prompt").count() == 2
        assert page.locator(".ask-user-prompt").count() == 2

        # Call the actual shared cleanup function used by app.js (#864)
        page.evaluate("() => Chat.cleanupPendingPrompts()")

        assert page.locator(".approval-prompt").count() == 1
        assert page.locator(".ask-user-prompt").count() == 1
        assert page.locator(".approval-prompt.approval-allowed").count() == 1
        assert page.locator(".ask-user-prompt.ask-user-answered").count() == 1


class TestTimeoutExpiredUX:
    """Verify timeout/expiry UX for approval and ask_user cards (#870).

    Tests the visual state changes applied by resolveApprovalCard and
    resolveAskUserCard when reason='timed_out': expired CSS class, grey
    border styling, and status text change.
    """

    def test_approval_expired_css_class_applied(self, authenticated_page) -> None:
        """resolveApprovalCard with timed_out adds .approval-expired class."""
        page = authenticated_page
        page.evaluate(
            """() => {
                Chat.showApprovalPrompt({
                    approval_id: 'test-expired-appr-1',
                    tool_name: 'bash',
                    reason: 'Running a shell command',
                });
                Chat.resolveApprovalCard('test-expired-appr-1', false, 'timed_out');
            }"""
        )

        card = page.locator('.approval-prompt[data-approval-id="test-expired-appr-1"]')
        assert card.count() == 1
        assert "approval-expired" in card.get_attribute("class")

    def test_approval_expired_status_text(self, authenticated_page) -> None:
        """resolveApprovalCard with timed_out shows 'Expired' status text."""
        page = authenticated_page
        page.evaluate(
            """() => {
                Chat.showApprovalPrompt({
                    approval_id: 'test-expired-appr-2',
                    tool_name: 'write_file',
                    reason: 'Writing a file',
                });
                Chat.resolveApprovalCard('test-expired-appr-2', false, 'timed_out');
            }"""
        )

        card = page.locator('.approval-prompt[data-approval-id="test-expired-appr-2"]')
        status = card.locator(".approval-status")
        assert status.count() == 1
        assert "Expired" in status.text_content()

    def test_approval_expired_does_not_add_allowed_or_denied(self, authenticated_page) -> None:
        """resolveApprovalCard with timed_out must not add allowed/denied classes."""
        page = authenticated_page
        page.evaluate(
            """() => {
                Chat.showApprovalPrompt({
                    approval_id: 'test-expired-appr-3',
                    tool_name: 'bash',
                    reason: 'Some command',
                });
                Chat.resolveApprovalCard('test-expired-appr-3', true, 'timed_out');
            }"""
        )

        card = page.locator('.approval-prompt[data-approval-id="test-expired-appr-3"]')
        classes = card.get_attribute("class")
        assert "approval-expired" in classes
        assert "approval-allowed" not in classes
        assert "approval-denied" not in classes

    def test_approval_expired_is_idempotent(self, authenticated_page) -> None:
        """Calling resolveApprovalCard twice on an expired card is a no-op."""
        page = authenticated_page
        page.evaluate(
            """() => {
                Chat.showApprovalPrompt({
                    approval_id: 'test-expired-appr-4',
                    tool_name: 'bash',
                    reason: 'Idempotent check',
                });
                Chat.resolveApprovalCard('test-expired-appr-4', false, 'timed_out');
                Chat.resolveApprovalCard('test-expired-appr-4', false, 'timed_out');
            }"""
        )

        status_count = page.evaluate(
            """() => document.querySelectorAll('[data-approval-id="test-expired-appr-4"] .approval-status').length"""
        )
        assert status_count == 1

    def test_approval_expired_removed_by_reconnect_cleanup(self, authenticated_page) -> None:
        """Expired approval cards are removed by cleanupPendingPrompts.

        The cleanup selector is :not(.approval-allowed):not(.approval-denied),
        so .approval-expired cards (which lack those classes) are removed on
        reconnect — they are transient, not pinned like allowed/denied cards.
        """
        page = authenticated_page
        page.evaluate(
            """() => {
                Chat.showApprovalPrompt({
                    approval_id: 'test-expired-appr-5',
                    tool_name: 'bash',
                    reason: 'Reconnect check',
                });
                Chat.resolveApprovalCard('test-expired-appr-5', false, 'timed_out');
            }"""
        )

        page.evaluate("() => Chat.cleanupPendingPrompts()")

        assert page.locator(".approval-prompt.approval-expired").count() == 0

    def test_ask_user_expired_css_class_applied(self, authenticated_page) -> None:
        """resolveAskUserCard with timed_out adds .ask-user-expired class."""
        page = authenticated_page
        page.evaluate(
            """() => {
                Chat.showAskUserPrompt({
                    ask_id: 'test-expired-ask-1',
                    question: 'Should I proceed?',
                });
                Chat.resolveAskUserCard('test-expired-ask-1', 'timed_out');
            }"""
        )

        card = page.locator('.ask-user-prompt[data-ask-id="test-expired-ask-1"]')
        assert card.count() == 1
        assert "ask-user-expired" in card.get_attribute("class")

    def test_ask_user_expired_status_text(self, authenticated_page) -> None:
        """resolveAskUserCard with timed_out shows 'Expired' status text."""
        page = authenticated_page
        page.evaluate(
            """() => {
                Chat.showAskUserPrompt({
                    ask_id: 'test-expired-ask-2',
                    question: 'What is the target?',
                });
                Chat.resolveAskUserCard('test-expired-ask-2', 'timed_out');
            }"""
        )

        card = page.locator('.ask-user-prompt[data-ask-id="test-expired-ask-2"]')
        status = card.locator(".ask-user-status")
        assert status.count() == 1
        assert "Expired" in status.text_content()

    def test_ask_user_expired_does_not_add_answered_or_cancelled(self, authenticated_page) -> None:
        """resolveAskUserCard with timed_out must not add answered/cancelled classes."""
        page = authenticated_page
        page.evaluate(
            """() => {
                Chat.showAskUserPrompt({
                    ask_id: 'test-expired-ask-3',
                    question: 'Confirm deletion?',
                });
                Chat.resolveAskUserCard('test-expired-ask-3', 'timed_out');
            }"""
        )

        card = page.locator('.ask-user-prompt[data-ask-id="test-expired-ask-3"]')
        classes = card.get_attribute("class")
        assert "ask-user-expired" in classes
        assert "ask-user-answered" not in classes
        assert "ask-user-cancelled" not in classes

    def test_ask_user_expired_is_idempotent(self, authenticated_page) -> None:
        """Calling resolveAskUserCard twice on an expired card is a no-op."""
        page = authenticated_page
        page.evaluate(
            """() => {
                Chat.showAskUserPrompt({
                    ask_id: 'test-expired-ask-4',
                    question: 'Idempotent?',
                });
                Chat.resolveAskUserCard('test-expired-ask-4', 'timed_out');
                Chat.resolveAskUserCard('test-expired-ask-4', 'timed_out');
            }"""
        )

        status_count = page.evaluate(
            """() => document.querySelectorAll('[data-ask-id="test-expired-ask-4"] .ask-user-status').length"""
        )
        assert status_count == 1

    def test_ask_user_expired_removed_by_reconnect_cleanup(self, authenticated_page) -> None:
        """Expired ask_user cards are removed by cleanupPendingPrompts.

        The cleanup selector is :not(.ask-user-answered):not(.ask-user-cancelled),
        so .ask-user-expired cards (which lack those classes) are removed on
        reconnect — they are transient, not pinned like answered/cancelled cards.
        """
        page = authenticated_page
        page.evaluate(
            """() => {
                Chat.showAskUserPrompt({
                    ask_id: 'test-expired-ask-5',
                    question: 'Reconnect check?',
                });
                Chat.resolveAskUserCard('test-expired-ask-5', 'timed_out');
            }"""
        )

        page.evaluate("() => Chat.cleanupPendingPrompts()")

        assert page.locator(".ask-user-prompt.ask-user-expired").count() == 0

    def test_expired_css_class_has_grey_border(self, authenticated_page) -> None:
        """Verify .approval-expired and .ask-user-expired use grey border styling."""
        page = authenticated_page

        page.evaluate(
            """() => {
                Chat.showApprovalPrompt({
                    approval_id: 'test-style-appr',
                    tool_name: 'bash',
                    reason: 'Style check',
                });
                Chat.resolveApprovalCard('test-style-appr', false, 'timed_out');

                Chat.showAskUserPrompt({
                    ask_id: 'test-style-ask',
                    question: 'Style check?',
                });
                Chat.resolveAskUserCard('test-style-ask', 'timed_out');
            }"""
        )

        appr_border = page.evaluate(
            """() => {
                const el = document.querySelector('.approval-expired');
                return getComputedStyle(el).borderColor;
            }"""
        )
        ask_border = page.evaluate(
            """() => {
                const el = document.querySelector('.ask-user-expired');
                return getComputedStyle(el).borderColor;
            }"""
        )
        assert appr_border is not None
        assert ask_border is not None
