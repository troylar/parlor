"""Tests for the CLI status bar."""

from __future__ import annotations

from anteroom.cli.renderer import StatusBar, get_status_bar, init_status_bar


class TestStatusBarInit:
    def test_defaults(self) -> None:
        sb = StatusBar()
        assert sb.model == ""
        assert sb.conv_id == ""
        assert sb.version == ""
        assert sb.git_branch == ""
        assert sb.venv == ""
        assert sb.context_tokens == 0
        assert sb.context_max == 128_000
        assert sb.thinking is False
        assert sb.thinking_elapsed == 0.0
        assert sb.tool_calls == 0
        assert sb.subagent_count == 0
        assert sb.canvas_title is None
        assert sb.plan_active is False
        assert sb.plan_step == 0
        assert sb.plan_total == 0
        assert sb.plan_step_desc == ""

    def test_custom_init(self) -> None:
        sb = StatusBar(model="gpt-4o", conv_id="abc123", version="1.0.0")
        assert sb.model == "gpt-4o"
        assert sb.conv_id == "abc123"
        assert sb.version == "1.0.0"


class TestStatusBarIdleText:
    def test_shows_version_model_conv(self) -> None:
        sb = StatusBar(model="gpt-4o", conv_id="abcdef1234567890", version="1.22.0")
        text = sb.get_toolbar_text()
        assert "anteroom v1.22.0" in text
        assert "gpt-4o" in text
        assert "Conv: abcdef12" in text

    def test_empty_when_no_info(self) -> None:
        sb = StatusBar()
        assert sb.get_toolbar_text() == ""

    def test_partial_info(self) -> None:
        sb = StatusBar(model="gpt-4o")
        text = sb.get_toolbar_text()
        assert "gpt-4o" in text
        assert "anteroom" not in text

    def test_idle_shows_branch(self) -> None:
        sb = StatusBar(model="gpt-4o", version="1.0")
        sb.git_branch = "main"
        text = sb.get_toolbar_text()
        assert "\u2387 main" in text
        assert "gpt-4o" in text

    def test_idle_shows_venv(self) -> None:
        sb = StatusBar(model="gpt-4o")
        sb.venv = "myenv"
        text = sb.get_toolbar_text()
        assert "\u24e5 myenv" in text

    def test_idle_shows_context(self) -> None:
        sb = StatusBar()
        sb.context_tokens = 50_000
        sb.context_max = 128_000
        text = sb.get_toolbar_text()
        assert "ctx: 50k/128k (39%)" in text

    def test_idle_context_zero_hidden(self) -> None:
        sb = StatusBar(model="gpt-4o")
        # context_tokens defaults to 0, should not show ctx
        text = sb.get_toolbar_text()
        assert "ctx:" not in text


class TestStatusBarThinking:
    def test_thinking_shown(self) -> None:
        sb = StatusBar(model="gpt-4o")
        sb.set_thinking(True)
        text = sb.get_toolbar_text()
        assert "Thinking..." in text

    def test_thinking_with_elapsed(self) -> None:
        sb = StatusBar()
        sb.set_thinking(True)
        sb.thinking_elapsed = 5.0
        text = sb.get_toolbar_text()
        assert "Thinking... 5s" in text

    def test_clear_thinking(self) -> None:
        sb = StatusBar(model="gpt-4o")
        sb.set_thinking(True)
        sb.thinking_elapsed = 3.0
        sb.clear_thinking()
        assert sb.thinking is False
        assert sb.thinking_elapsed == 0.0

    def test_set_thinking_false_clears_elapsed(self) -> None:
        sb = StatusBar()
        sb.set_thinking(True)
        sb.thinking_elapsed = 10.0
        sb.set_thinking(False)
        assert sb.thinking_elapsed == 0.0


class TestStatusBarToolCalls:
    def test_increment(self) -> None:
        sb = StatusBar()
        sb.increment_tool_calls()
        assert sb.tool_calls == 1
        text = sb.get_toolbar_text()
        assert "1 tool call" in text
        assert "tool calls" not in text

    def test_plural(self) -> None:
        sb = StatusBar()
        sb.increment_tool_calls()
        sb.increment_tool_calls()
        text = sb.get_toolbar_text()
        assert "2 tool calls" in text

    def test_reset_turn(self) -> None:
        sb = StatusBar()
        sb.set_thinking(True)
        sb.thinking_elapsed = 5.0
        sb.increment_tool_calls()
        sb.increment_tool_calls()
        sb.reset_turn()
        assert sb.tool_calls == 0
        assert sb.thinking is False
        assert sb.thinking_elapsed == 0.0


class TestStatusBarSubagents:
    def test_subagent_count(self) -> None:
        sb = StatusBar()
        sb.set_subagent_count(1)
        text = sb.get_toolbar_text()
        assert "1 sub-agent" in text
        assert "sub-agents" not in text

    def test_subagent_plural(self) -> None:
        sb = StatusBar()
        sb.set_subagent_count(3)
        text = sb.get_toolbar_text()
        assert "3 sub-agents" in text

    def test_subagent_zero_hidden(self) -> None:
        sb = StatusBar(model="gpt-4o")
        sb.set_subagent_count(0)
        text = sb.get_toolbar_text()
        assert "sub-agent" not in text


class TestStatusBarCanvas:
    def test_canvas_shown(self) -> None:
        sb = StatusBar()
        sb.set_canvas("My Document")
        text = sb.get_toolbar_text()
        assert "Canvas: My Document" in text

    def test_canvas_cleared(self) -> None:
        sb = StatusBar(model="gpt-4o")
        sb.set_canvas("My Document")
        sb.set_canvas(None)
        text = sb.get_toolbar_text()
        assert "Canvas" not in text


class TestStatusBarPlan:
    def test_plan_progress(self) -> None:
        sb = StatusBar()
        sb.set_plan_progress(2, 5, "Add tests")
        text = sb.get_toolbar_text()
        assert "Plan: 2/5 (40%)" in text
        assert "Add tests" in text

    def test_plan_zero_progress(self) -> None:
        sb = StatusBar()
        sb.set_plan_progress(0, 5, "Create module")
        text = sb.get_toolbar_text()
        assert "Plan: 0/5 (0%)" in text

    def test_plan_complete(self) -> None:
        sb = StatusBar()
        sb.set_plan_progress(5, 5)
        text = sb.get_toolbar_text()
        assert "Plan: 5/5 (100%)" in text

    def test_clear_plan(self) -> None:
        sb = StatusBar(model="gpt-4o")
        sb.set_plan_progress(2, 5, "Testing")
        sb.clear_plan()
        assert sb.plan_active is False
        text = sb.get_toolbar_text()
        assert "Plan" not in text


class TestStatusBarContext:
    def test_set_context(self) -> None:
        sb = StatusBar()
        sb.set_context(50_000, 200_000)
        assert sb.context_tokens == 50_000
        assert sb.context_max == 200_000

    def test_set_context_tokens_only(self) -> None:
        sb = StatusBar()
        sb.set_context(30_000)
        assert sb.context_tokens == 30_000
        assert sb.context_max == 128_000  # default preserved

    def test_set_context_zero_max_preserves(self) -> None:
        sb = StatusBar()
        sb.context_max = 64_000
        sb.set_context(10_000, 0)
        assert sb.context_max == 64_000

    def test_context_percentage_capped(self) -> None:
        sb = StatusBar()
        sb.set_context(150_000, 100_000)
        text = sb.get_toolbar_text()
        assert "(100%)" in text


class TestStatusBarCombined:
    def test_thinking_and_tool_calls(self) -> None:
        sb = StatusBar()
        sb.set_thinking(True)
        sb.thinking_elapsed = 3.0
        sb.increment_tool_calls()
        text = sb.get_toolbar_text()
        assert "Thinking... 3s" in text
        assert "1 tool call" in text
        assert " | " in text

    def test_plan_and_thinking(self) -> None:
        sb = StatusBar()
        sb.set_plan_progress(1, 3, "Step one")
        sb.set_thinking(True)
        text = sb.get_toolbar_text()
        assert "Plan: 1/3" in text
        assert "Thinking..." in text

    def test_active_with_persistent_context(self) -> None:
        sb = StatusBar(model="gpt-4o", version="1.0.0")
        sb.git_branch = "main"
        sb.set_thinking(True)
        text = sb.get_toolbar_text()
        # Activity side should have thinking
        assert "Thinking..." in text
        # Persistent context should still show model and branch
        assert "gpt-4o" in text
        assert "\u2387 main" in text
        # Version shows only in idle, not in active mode
        assert "anteroom" not in text
        # Separator between activity and context
        assert "\u2502" in text

    def test_active_without_context(self) -> None:
        sb = StatusBar()
        sb.set_thinking(True)
        text = sb.get_toolbar_text()
        assert "Thinking..." in text
        # No separator when no persistent context
        assert "\u2502" not in text


class TestStatusBarInvalidate:
    def test_invalidate_calls_callback(self) -> None:
        called = []
        sb = StatusBar()
        sb.set_invalidate_callback(lambda: called.append(True))
        sb.invalidate()
        assert len(called) == 1

    def test_invalidate_no_callback(self) -> None:
        sb = StatusBar()
        sb.invalidate()  # should not raise

    def test_invalidate_callback_exception_suppressed(self) -> None:
        def bad_cb() -> None:
            raise RuntimeError("boom")

        sb = StatusBar()
        sb.set_invalidate_callback(bad_cb)
        sb.invalidate()  # should not raise


class TestStatusBarSetIdleInfo:
    def test_updates_fields(self) -> None:
        sb = StatusBar()
        sb.set_idle_info(model="new-model", conv_id="new-id", version="2.0")
        assert sb.model == "new-model"
        assert sb.conv_id == "new-id"
        assert sb.version == "2.0"

    def test_empty_strings_preserve_existing(self) -> None:
        sb = StatusBar(model="original", conv_id="orig-id")
        sb.set_idle_info(model="", conv_id="")
        assert sb.model == "original"
        assert sb.conv_id == "orig-id"


class TestStatusBarSingleton:
    def test_init_creates_and_returns(self) -> None:
        sb = init_status_bar(model="test-model", version="1.0")
        assert sb is not None
        assert sb.model == "test-model"
        assert get_status_bar() is sb

    def test_get_returns_same_instance(self) -> None:
        sb = init_status_bar(model="m")
        assert get_status_bar() is sb
        assert get_status_bar() is sb

    def test_reinit_replaces(self) -> None:
        sb1 = init_status_bar(model="first")
        sb2 = init_status_bar(model="second")
        assert sb2 is not sb1
        assert get_status_bar() is sb2
        assert get_status_bar().model == "second"
