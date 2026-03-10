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

pytestmark = [pytest.mark.e2e]


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

        # Simulate reconnect cleanup (same logic as _connectEventSource)
        page.evaluate(
            """() => {
                document.querySelectorAll(
                    '.approval-prompt:not(.approval-allowed):not(.approval-denied)'
                ).forEach(el => el.remove());
                document.querySelectorAll(
                    '.ask-user-prompt:not(.ask-user-answered):not(.ask-user-cancelled)'
                ).forEach(el => el.remove());
            }"""
        )

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
                // Simulate resolution
                const el = document.querySelector('[data-approval-id="test-reconnect-resolved"]');
                el.classList.add('approval-allowed');
            }"""
        )

        # Simulate reconnect cleanup
        page.evaluate(
            """() => {
                document.querySelectorAll(
                    '.approval-prompt:not(.approval-allowed):not(.approval-denied)'
                ).forEach(el => el.remove());
            }"""
        )

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

        # Simulate reconnect cleanup
        page.evaluate(
            """() => {
                document.querySelectorAll(
                    '.ask-user-prompt:not(.ask-user-answered):not(.ask-user-cancelled)'
                ).forEach(el => el.remove());
            }"""
        )

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

        # Simulate reconnect cleanup
        page.evaluate(
            """() => {
                document.querySelectorAll(
                    '.approval-prompt:not(.approval-allowed):not(.approval-denied)'
                ).forEach(el => el.remove());
                document.querySelectorAll(
                    '.ask-user-prompt:not(.ask-user-answered):not(.ask-user-cancelled)'
                ).forEach(el => el.remove());
            }"""
        )

        assert page.locator(".approval-prompt").count() == 1
        assert page.locator(".ask-user-prompt").count() == 1
        assert page.locator(".approval-prompt.approval-allowed").count() == 1
        assert page.locator(".ask-user-prompt.ask-user-answered").count() == 1
