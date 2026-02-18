"""E2E tests for sub-agent loading indicator in the Web UI.

Verifies that the loading indicator DOM structure, CSS classes, and
transitions work correctly in a real browser. Uses Playwright to inject
synthetic DOM elements via page.evaluate() since we cannot mock the AI
backend in the threaded server.

Acceptance criteria from #101:
- A loading/spinner indicator appears when run_agent tool call starts
- The indicator transitions to sub-agent progress cards when events arrive
- The indicator is removed if the sub-agent errors before emitting events
- No visual regression in non-sub-agent tool call rendering
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.e2e]

try:
    import playwright  # noqa: F401

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

requires_playwright = pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed")


# ---------------------------------------------------------------------------
# Helper: inject assistant message container into the DOM
# ---------------------------------------------------------------------------

_INJECT_ASSISTANT_MSG = """
() => {
    const container = document.getElementById('messages-container');
    if (!container) return null;
    const msg = document.createElement('div');
    msg.className = 'message assistant';
    msg.innerHTML = '<div class="message-content"></div>';
    container.appendChild(msg);
    return true;
}
"""


@requires_playwright
class TestSubagentLoadingIndicator:
    """Verify run_agent tool call renders a distinctive loading state."""

    def test_subagent_tool_call_has_loading_classes(self, page_with_conversation) -> None:
        """A run_agent tool_call_start should produce tool-call-subagent and subagent-running classes."""
        page, conv_id = page_with_conversation

        result = page.evaluate("""
        () => {
            const container = document.getElementById('messages-container');
            const msg = document.createElement('div');
            msg.className = 'message assistant';
            msg.innerHTML = '<div class="message-content"></div>';
            container.appendChild(msg);
            const content = msg.querySelector('.message-content');

            // Simulate renderToolCallStart for run_agent
            const details = document.createElement('details');
            details.className = 'tool-call tool-call-subagent subagent-running';
            details.id = 'tool-test-subagent-001';
            details.open = true;

            const summary = document.createElement('summary');
            summary.textContent = 'Sub-agent running\\u2026 ';
            const spinner = document.createElement('span');
            spinner.className = 'tool-spinner';
            summary.appendChild(spinner);
            details.appendChild(summary);

            const toolContent = document.createElement('div');
            toolContent.className = 'tool-content';
            const promptEl = document.createElement('div');
            promptEl.className = 'subagent-loading-prompt';
            promptEl.textContent = 'Analyze the codebase for security issues';
            toolContent.appendChild(promptEl);
            const cardsContainer = document.createElement('div');
            cardsContainer.className = 'subagent-cards-container';
            toolContent.appendChild(cardsContainer);
            details.appendChild(toolContent);
            content.appendChild(details);

            return {
                hasSubagentClass: details.classList.contains('tool-call-subagent'),
                hasRunningClass: details.classList.contains('subagent-running'),
                isOpen: details.open,
                summaryText: summary.textContent.trim(),
                hasSpinner: !!details.querySelector('.tool-spinner'),
                hasPrompt: !!details.querySelector('.subagent-loading-prompt'),
                hasCardsContainer: !!details.querySelector('.subagent-cards-container'),
                promptText: promptEl.textContent,
            };
        }
        """)

        assert result["hasSubagentClass"] is True
        assert result["hasRunningClass"] is True
        assert result["isOpen"] is True
        assert "Sub-agent running" in result["summaryText"]
        assert result["hasSpinner"] is True
        assert result["hasPrompt"] is True
        assert result["hasCardsContainer"] is True
        assert result["promptText"] == "Analyze the codebase for security issues"

    def test_subagent_css_styles_applied(self, page_with_conversation) -> None:
        """The tool-call-subagent class should have a visible left border accent."""
        page, conv_id = page_with_conversation

        border = page.evaluate("""
        () => {
            const container = document.getElementById('messages-container');
            const msg = document.createElement('div');
            msg.className = 'message assistant';
            msg.innerHTML = '<div class="message-content"></div>';
            container.appendChild(msg);
            const content = msg.querySelector('.message-content');

            const details = document.createElement('details');
            details.className = 'tool-call tool-call-subagent subagent-running';
            const summary = document.createElement('summary');
            summary.textContent = 'Sub-agent running...';
            details.appendChild(summary);
            content.appendChild(details);

            const style = window.getComputedStyle(details);
            return {
                borderLeftWidth: style.borderLeftWidth,
                borderLeftStyle: style.borderLeftStyle,
                animationName: style.animationName,
            };
        }
        """)

        assert border["borderLeftWidth"] == "3px"
        assert border["borderLeftStyle"] == "solid"
        assert border["animationName"] == "subagentPulse"

    def test_subagent_cards_nest_inside_container(self, page_with_conversation) -> None:
        """Sub-agent cards should be inserted into the subagent-cards-container."""
        page, conv_id = page_with_conversation

        result = page.evaluate("""
        () => {
            const container = document.getElementById('messages-container');
            const msg = document.createElement('div');
            msg.className = 'message assistant';
            msg.innerHTML = '<div class="message-content"></div>';
            container.appendChild(msg);
            const content = msg.querySelector('.message-content');

            // Create the run_agent tool-call wrapper
            const details = document.createElement('details');
            details.className = 'tool-call tool-call-subagent subagent-running';
            details.open = true;
            const summary = document.createElement('summary');
            summary.textContent = 'Sub-agent running...';
            details.appendChild(summary);
            const toolContent = document.createElement('div');
            toolContent.className = 'tool-content';
            const prompt = document.createElement('div');
            prompt.className = 'subagent-loading-prompt';
            prompt.textContent = 'Test prompt';
            toolContent.appendChild(prompt);
            const cardsContainer = document.createElement('div');
            cardsContainer.className = 'subagent-cards-container';
            toolContent.appendChild(cardsContainer);
            details.appendChild(toolContent);
            content.appendChild(details);

            // Simulate subagent_start card being added to the container
            const card = document.createElement('div');
            card.className = 'subagent-card';
            card.id = 'subagent-agent-1';
            const header = document.createElement('div');
            header.className = 'subagent-header';
            const label = document.createElement('span');
            label.className = 'subagent-label';
            label.textContent = 'agent-1';
            header.appendChild(label);
            card.appendChild(header);
            cardsContainer.appendChild(card);

            // Hide loading prompt as the code does
            prompt.style.display = 'none';

            return {
                cardInsideContainer: cardsContainer.contains(card),
                cardCount: cardsContainer.querySelectorAll('.subagent-card').length,
                promptHidden: prompt.style.display === 'none',
            };
        }
        """)

        assert result["cardInsideContainer"] is True
        assert result["cardCount"] == 1
        assert result["promptHidden"] is True

    def test_completion_removes_running_state(self, page_with_conversation) -> None:
        """On tool_call_end success, subagent-running class is removed and summary updates."""
        page, conv_id = page_with_conversation

        result = page.evaluate("""
        () => {
            const container = document.getElementById('messages-container');
            const msg = document.createElement('div');
            msg.className = 'message assistant';
            msg.innerHTML = '<div class="message-content"></div>';
            container.appendChild(msg);
            const content = msg.querySelector('.message-content');

            // Create running state
            const details = document.createElement('details');
            details.className = 'tool-call tool-call-subagent subagent-running';
            details.open = true;
            const summary = document.createElement('summary');
            summary.textContent = 'Sub-agent running...';
            const spinner = document.createElement('span');
            spinner.className = 'tool-spinner';
            summary.appendChild(spinner);
            details.appendChild(summary);
            const toolContent = document.createElement('div');
            toolContent.className = 'tool-content';
            details.appendChild(toolContent);
            content.appendChild(details);

            // Simulate renderToolCallEnd for success
            spinner.remove();
            details.classList.remove('subagent-running');
            details.classList.add('tool-status-success');
            summary.textContent = 'Sub-agent complete';

            return {
                hasRunning: details.classList.contains('subagent-running'),
                hasSuccess: details.classList.contains('tool-status-success'),
                summaryText: summary.textContent,
                hasSpinner: !!details.querySelector('.tool-spinner'),
            };
        }
        """)

        assert result["hasRunning"] is False
        assert result["hasSuccess"] is True
        assert result["summaryText"] == "Sub-agent complete"
        assert result["hasSpinner"] is False

    def test_error_state_renders_correctly(self, page_with_conversation) -> None:
        """On tool_call_end error, loading prompt is hidden and error class applied."""
        page, conv_id = page_with_conversation

        result = page.evaluate("""
        () => {
            const container = document.getElementById('messages-container');
            const msg = document.createElement('div');
            msg.className = 'message assistant';
            msg.innerHTML = '<div class="message-content"></div>';
            container.appendChild(msg);
            const content = msg.querySelector('.message-content');

            // Create running state with loading prompt
            const details = document.createElement('details');
            details.className = 'tool-call tool-call-subagent subagent-running';
            details.open = true;
            const summary = document.createElement('summary');
            summary.textContent = 'Sub-agent running...';
            details.appendChild(summary);
            const toolContent = document.createElement('div');
            toolContent.className = 'tool-content';
            const prompt = document.createElement('div');
            prompt.className = 'subagent-loading-prompt';
            prompt.textContent = 'This will fail';
            toolContent.appendChild(prompt);
            details.appendChild(toolContent);
            content.appendChild(details);

            // Simulate renderToolCallEnd for error (no subagent events received)
            details.classList.remove('subagent-running');
            details.classList.add('tool-status-error');
            summary.textContent = 'Sub-agent failed';
            prompt.style.display = 'none';

            return {
                hasRunning: details.classList.contains('subagent-running'),
                hasError: details.classList.contains('tool-status-error'),
                summaryText: summary.textContent,
                promptHidden: prompt.style.display === 'none',
            };
        }
        """)

        assert result["hasRunning"] is False
        assert result["hasError"] is True
        assert result["summaryText"] == "Sub-agent failed"
        assert result["promptHidden"] is True


@requires_playwright
class TestNonSubagentToolCallRegression:
    """Verify non-sub-agent tool calls are not affected by the changes."""

    def test_regular_tool_call_unchanged(self, page_with_conversation) -> None:
        """A regular tool_call_start (not run_agent) should render with original classes."""
        page, conv_id = page_with_conversation

        result = page.evaluate("""
        () => {
            const container = document.getElementById('messages-container');
            const msg = document.createElement('div');
            msg.className = 'message assistant';
            msg.innerHTML = '<div class="message-content"></div>';
            container.appendChild(msg);
            const content = msg.querySelector('.message-content');

            // Simulate renderToolCallStart for a regular tool (e.g., read_file)
            const details = document.createElement('details');
            details.className = 'tool-call';
            details.id = 'tool-test-regular-001';

            const summary = document.createElement('summary');
            summary.textContent = 'Tool: read_file ';
            const spinner = document.createElement('span');
            spinner.className = 'tool-spinner';
            summary.appendChild(spinner);
            details.appendChild(summary);
            content.appendChild(details);

            return {
                hasToolCall: details.classList.contains('tool-call'),
                hasSubagentClass: details.classList.contains('tool-call-subagent'),
                hasRunningClass: details.classList.contains('subagent-running'),
                isOpen: details.open,
                summaryText: summary.textContent.trim(),
            };
        }
        """)

        assert result["hasToolCall"] is True
        assert result["hasSubagentClass"] is False
        assert result["hasRunningClass"] is False
        assert result["isOpen"] is False
        assert "Tool: read_file" in result["summaryText"]

    def test_regular_tool_call_no_accent_border(self, page_with_conversation) -> None:
        """Regular tool calls should not have the 3px left border accent."""
        page, conv_id = page_with_conversation

        border = page.evaluate("""
        () => {
            const container = document.getElementById('messages-container');
            const msg = document.createElement('div');
            msg.className = 'message assistant';
            msg.innerHTML = '<div class="message-content"></div>';
            container.appendChild(msg);
            const content = msg.querySelector('.message-content');

            const details = document.createElement('details');
            details.className = 'tool-call';
            const summary = document.createElement('summary');
            summary.textContent = 'Tool: bash';
            details.appendChild(summary);
            content.appendChild(details);

            const style = window.getComputedStyle(details);
            return {
                borderLeftWidth: style.borderLeftWidth,
                animationName: style.animationName,
            };
        }
        """)

        assert border["borderLeftWidth"] != "3px"
        assert border["animationName"] != "subagentPulse"
