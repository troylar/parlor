"""Tests for sub-agent tool (tools/subagent.py)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from anteroom.services.agent_loop import AgentEvent
from anteroom.tools.subagent import (
    _SUBAGENT_SYSTEM_PROMPT,
    DEFINITION,
    MAX_OUTPUT_CHARS,
    MAX_PROMPT_CHARS,
    MAX_SUBAGENT_DEPTH,
    SubagentLimiter,
    handle,
)


def _make_limiter() -> SubagentLimiter:
    """Create a fresh limiter for tests."""
    return SubagentLimiter()


def _mock_ai() -> MagicMock:
    """Create a mock AI service with standard config."""
    mock = MagicMock()
    mock.config = MagicMock()
    mock.config.model = "gpt-4"
    mock._token_provider = None
    return mock


class TestSubagentDefinition:
    def test_definition_name(self) -> None:
        assert DEFINITION["name"] == "run_agent"

    def test_definition_has_required_prompt(self) -> None:
        assert "prompt" in DEFINITION["parameters"]["properties"]
        assert "prompt" in DEFINITION["parameters"]["required"]

    def test_definition_has_optional_model(self) -> None:
        assert "model" in DEFINITION["parameters"]["properties"]
        assert "model" not in DEFINITION["parameters"]["required"]

    def test_definition_no_additional_properties(self) -> None:
        assert DEFINITION["parameters"]["additionalProperties"] is False


class TestSubagentHandler:
    @pytest.mark.asyncio
    async def test_missing_ai_service(self) -> None:
        result = await handle(prompt="test")
        assert "error" in result
        assert "AI service" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_tool_registry(self) -> None:
        mock_ai = MagicMock()
        result = await handle(prompt="test", _ai_service=mock_ai)
        assert "error" in result
        assert "tool registry" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_limiter(self) -> None:
        result = await handle(
            prompt="test",
            _ai_service=_mock_ai(),
            _tool_registry=MagicMock(),
            _depth=0,
        )
        assert "error" in result
        assert "limiter" in result["error"]

    @pytest.mark.asyncio
    async def test_prompt_too_long(self) -> None:
        result = await handle(
            prompt="x" * (MAX_PROMPT_CHARS + 1),
            _ai_service=_mock_ai(),
            _tool_registry=MagicMock(),
            _depth=0,
            _limiter=_make_limiter(),
        )
        assert "error" in result
        assert "maximum length" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_prompt_at_limit_accepted(self) -> None:
        """Prompt exactly at MAX_PROMPT_CHARS should not be rejected."""
        mock_registry = MagicMock()
        mock_registry.get_openai_tools.return_value = []

        async def mock_agent_loop(**kwargs):
            yield AgentEvent(kind="done", data={})

        with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
            with patch("anteroom.tools.subagent.AIService"):
                result = await handle(
                    prompt="x" * MAX_PROMPT_CHARS,
                    _ai_service=_mock_ai(),
                    _tool_registry=mock_registry,
                    _depth=0,
                    _limiter=_make_limiter(),
                )
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_invalid_model_rejected(self) -> None:
        result = await handle(
            prompt="test",
            model="'; DROP TABLE models; --",
            _ai_service=_mock_ai(),
            _tool_registry=MagicMock(),
            _depth=0,
            _limiter=_make_limiter(),
        )
        assert "error" in result
        assert "Invalid model" in result["error"]

    @pytest.mark.asyncio
    async def test_valid_model_accepted(self) -> None:
        mock_registry = MagicMock()
        mock_registry.get_openai_tools.return_value = []

        async def mock_agent_loop(**kwargs):
            yield AgentEvent(kind="done", data={})

        with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
            with patch("anteroom.tools.subagent.AIService"):
                result = await handle(
                    prompt="test",
                    model="gpt-4o-mini",
                    _ai_service=_mock_ai(),
                    _tool_registry=mock_registry,
                    _depth=0,
                    _limiter=_make_limiter(),
                )
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_max_depth_exceeded(self) -> None:
        result = await handle(
            prompt="test",
            _ai_service=_mock_ai(),
            _tool_registry=MagicMock(),
            _depth=MAX_SUBAGENT_DEPTH,
        )
        assert "error" in result
        assert "depth" in result["error"]

    @pytest.mark.asyncio
    async def test_depth_at_limit_minus_one_succeeds(self) -> None:
        """Depth one below max should still work."""
        mock_ai = _mock_ai()
        mock_ai.config.model = "test-model"

        mock_registry = MagicMock()
        mock_registry.get_openai_tools.return_value = [
            {"function": {"name": "read_file"}, "type": "function"},
            {"function": {"name": "run_agent"}, "type": "function"},
        ]

        async def mock_agent_loop(**kwargs):
            yield AgentEvent(kind="token", data={"content": "Hello"})
            yield AgentEvent(kind="done", data={})

        with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
            with patch("anteroom.tools.subagent.AIService"):
                result = await handle(
                    prompt="test",
                    _ai_service=mock_ai,
                    _tool_registry=mock_registry,
                    _depth=MAX_SUBAGENT_DEPTH - 1,
                    _limiter=_make_limiter(),
                )

        assert result["output"] == "Hello"
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_run_agent_excluded_at_max_depth(self) -> None:
        """At max_depth - 1, child tools should exclude run_agent since child will be at max_depth."""
        mock_ai = _mock_ai()
        mock_ai.config.model = "test-model"

        mock_registry = MagicMock()
        mock_registry.get_openai_tools.return_value = [
            {"function": {"name": "read_file"}, "type": "function"},
            {"function": {"name": "run_agent"}, "type": "function"},
        ]

        captured_tools: list[dict] = []

        async def mock_agent_loop(**kwargs):
            captured_tools.extend(kwargs.get("tools_openai", []))
            yield AgentEvent(kind="done", data={})

        with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
            with patch("anteroom.tools.subagent.AIService"):
                await handle(
                    prompt="test",
                    _ai_service=mock_ai,
                    _tool_registry=mock_registry,
                    _depth=MAX_SUBAGENT_DEPTH - 1,
                    _limiter=_make_limiter(),
                )

        tool_names = [t["function"]["name"] for t in captured_tools]
        assert "run_agent" not in tool_names
        assert "read_file" in tool_names

    @pytest.mark.asyncio
    async def test_simple_response(self) -> None:
        mock_registry = MagicMock()
        mock_registry.get_openai_tools.return_value = []

        async def mock_agent_loop(**kwargs):
            yield AgentEvent(kind="token", data={"content": "Result: "})
            yield AgentEvent(kind="token", data={"content": "42"})
            yield AgentEvent(kind="done", data={})

        with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
            with patch("anteroom.tools.subagent.AIService"):
                result = await handle(
                    prompt="What is 6*7?",
                    _ai_service=_mock_ai(),
                    _tool_registry=mock_registry,
                    _depth=0,
                    _limiter=_make_limiter(),
                )

        assert result["output"] == "Result: 42"
        assert result["model_used"] == "gpt-4"
        assert result["elapsed_seconds"] >= 0
        assert result["tool_calls_made"] == []

    @pytest.mark.asyncio
    async def test_model_override(self) -> None:
        mock_registry = MagicMock()
        mock_registry.get_openai_tools.return_value = []

        async def mock_agent_loop(**kwargs):
            yield AgentEvent(kind="done", data={})

        with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
            with patch("anteroom.tools.subagent.AIService"):
                result = await handle(
                    prompt="test",
                    model="gpt-4o-mini",
                    _ai_service=_mock_ai(),
                    _tool_registry=mock_registry,
                    _depth=0,
                    _limiter=_make_limiter(),
                )

        assert result["model_used"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_output_truncation(self) -> None:
        mock_registry = MagicMock()
        mock_registry.get_openai_tools.return_value = []

        large_content = "x" * (MAX_OUTPUT_CHARS + 1000)

        async def mock_agent_loop(**kwargs):
            yield AgentEvent(kind="token", data={"content": large_content})
            yield AgentEvent(kind="done", data={})

        with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
            with patch("anteroom.tools.subagent.AIService"):
                result = await handle(
                    prompt="test",
                    _ai_service=_mock_ai(),
                    _tool_registry=mock_registry,
                    _depth=0,
                    _limiter=_make_limiter(),
                )

        assert result["truncated"] is True
        assert len(result["output"]) < len(large_content)
        assert "truncated" in result["output"]

    @pytest.mark.asyncio
    async def test_tool_calls_tracked(self) -> None:
        mock_registry = MagicMock()
        mock_registry.get_openai_tools.return_value = []

        async def mock_agent_loop(**kwargs):
            yield AgentEvent(kind="tool_call_start", data={"tool_name": "read_file", "id": "1", "arguments": {}})
            yield AgentEvent(
                kind="tool_call_end", data={"tool_name": "read_file", "id": "1", "output": {}, "status": "success"}
            )
            yield AgentEvent(kind="tool_call_start", data={"tool_name": "bash", "id": "2", "arguments": {}})
            yield AgentEvent(
                kind="tool_call_end", data={"tool_name": "bash", "id": "2", "output": {}, "status": "success"}
            )
            yield AgentEvent(kind="done", data={})

        with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
            with patch("anteroom.tools.subagent.AIService"):
                result = await handle(
                    prompt="test",
                    _ai_service=_mock_ai(),
                    _tool_registry=mock_registry,
                    _depth=0,
                    _limiter=_make_limiter(),
                )

        assert result["tool_calls_made"] == ["read_file", "bash"]

    @pytest.mark.asyncio
    async def test_error_event(self) -> None:
        mock_registry = MagicMock()
        mock_registry.get_openai_tools.return_value = []

        async def mock_agent_loop(**kwargs):
            yield AgentEvent(kind="error", data={"message": "API rate limited"})

        with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
            with patch("anteroom.tools.subagent.AIService"):
                result = await handle(
                    prompt="test",
                    _ai_service=_mock_ai(),
                    _tool_registry=mock_registry,
                    _depth=0,
                    _limiter=_make_limiter(),
                )

        assert result["error"] == "API rate limited"

    @pytest.mark.asyncio
    async def test_exception_returns_generic_error(self) -> None:
        """Exceptions should return a generic message, not raw exception text."""
        mock_registry = MagicMock()
        mock_registry.get_openai_tools.return_value = []

        async def mock_agent_loop(**kwargs):
            raise RuntimeError("Connection lost: secret-api-key-12345")
            yield  # make it a generator  # noqa: E501

        with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
            with patch("anteroom.tools.subagent.AIService"):
                result = await handle(
                    prompt="test",
                    _ai_service=_mock_ai(),
                    _tool_registry=mock_registry,
                    _depth=0,
                    _limiter=_make_limiter(),
                )

        assert result["error"] == "Sub-agent execution failed"
        assert "secret" not in result["error"]

    @pytest.mark.asyncio
    async def test_event_sink_called(self) -> None:
        mock_registry = MagicMock()
        mock_registry.get_openai_tools.return_value = []

        sink_calls: list[tuple[str, AgentEvent]] = []

        async def mock_sink(agent_id: str, event: AgentEvent) -> None:
            sink_calls.append((agent_id, event))

        async def mock_agent_loop(**kwargs):
            yield AgentEvent(kind="token", data={"content": "hi"})
            yield AgentEvent(kind="done", data={})

        with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
            with patch("anteroom.tools.subagent.AIService"):
                await handle(
                    prompt="test",
                    _ai_service=_mock_ai(),
                    _tool_registry=mock_registry,
                    _depth=0,
                    _event_sink=mock_sink,
                    _agent_id="test-1",
                    _limiter=_make_limiter(),
                )

        event_kinds = [e.kind for _, e in sink_calls]
        assert "subagent_start" in event_kinds
        assert "subagent_end" in event_kinds

    @pytest.mark.asyncio
    async def test_cancel_event_propagated(self) -> None:
        """Cancel event should be forwarded to the child agent loop."""
        mock_registry = MagicMock()
        mock_registry.get_openai_tools.return_value = []

        cancel = asyncio.Event()
        captured_cancel: list = []

        async def mock_agent_loop(**kwargs):
            captured_cancel.append(kwargs.get("cancel_event"))
            yield AgentEvent(kind="done", data={})

        with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
            with patch("anteroom.tools.subagent.AIService"):
                await handle(
                    prompt="test",
                    _ai_service=_mock_ai(),
                    _tool_registry=mock_registry,
                    _cancel_event=cancel,
                    _depth=0,
                    _limiter=_make_limiter(),
                )

        assert captured_cancel[0] is cancel


class TestSubagentLimiter:
    @pytest.mark.asyncio
    async def test_acquire_and_release(self) -> None:
        limiter = SubagentLimiter(max_concurrent=2, max_total=5)
        assert await limiter.acquire() is True
        assert limiter.total_spawned == 1
        limiter.release()

    @pytest.mark.asyncio
    async def test_total_cap_exceeded(self) -> None:
        limiter = SubagentLimiter(max_concurrent=10, max_total=2)
        assert await limiter.acquire() is True
        limiter.release()
        assert await limiter.acquire() is True
        limiter.release()
        # Third acquire should fail — total cap of 2 reached
        assert await limiter.acquire() is False
        assert limiter.total_spawned == 2

    @pytest.mark.asyncio
    async def test_total_cap_rejects_via_handle(self) -> None:
        """handle() returns error when limiter total cap is exceeded."""
        limiter = SubagentLimiter(max_concurrent=10, max_total=0)
        result = await handle(
            prompt="test",
            _ai_service=_mock_ai(),
            _tool_registry=MagicMock(),
            _depth=0,
            _limiter=limiter,
        )
        assert "error" in result
        assert "Sub-agent limit reached" in result["error"]

    @pytest.mark.asyncio
    async def test_limiter_released_on_success(self) -> None:
        """Limiter slot is released after successful execution."""
        limiter = SubagentLimiter(max_concurrent=1, max_total=5)
        mock_registry = MagicMock()
        mock_registry.get_openai_tools.return_value = []

        async def mock_agent_loop(**kwargs):
            yield AgentEvent(kind="done", data={})

        with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
            with patch("anteroom.tools.subagent.AIService"):
                await handle(
                    prompt="test",
                    _ai_service=_mock_ai(),
                    _tool_registry=mock_registry,
                    _depth=0,
                    _limiter=limiter,
                )

        # Semaphore should be released — can acquire again
        assert await limiter.acquire() is True

    @pytest.mark.asyncio
    async def test_limiter_released_on_exception(self) -> None:
        """Limiter slot is released even when sub-agent raises."""
        limiter = SubagentLimiter(max_concurrent=1, max_total=5)
        mock_registry = MagicMock()
        mock_registry.get_openai_tools.return_value = []

        async def mock_agent_loop(**kwargs):
            raise RuntimeError("boom")
            yield  # noqa: E501

        with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
            with patch("anteroom.tools.subagent.AIService"):
                await handle(
                    prompt="test",
                    _ai_service=_mock_ai(),
                    _tool_registry=mock_registry,
                    _depth=0,
                    _limiter=limiter,
                )

        # Semaphore should still be released
        assert await limiter.acquire() is True


class TestSubagentSystemPrompt:
    @pytest.mark.asyncio
    async def test_defensive_system_prompt_injected(self) -> None:
        """The defensive system prompt must be passed to the agent loop."""
        mock_registry = MagicMock()
        mock_registry.get_openai_tools.return_value = []

        captured_kwargs: list[dict] = []

        async def mock_agent_loop(**kwargs):
            captured_kwargs.append(kwargs)
            yield AgentEvent(kind="done", data={})

        with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
            with patch("anteroom.tools.subagent.AIService"):
                await handle(
                    prompt="test",
                    _ai_service=_mock_ai(),
                    _tool_registry=mock_registry,
                    _depth=0,
                    _limiter=_make_limiter(),
                )

        assert len(captured_kwargs) == 1
        assert captured_kwargs[0]["extra_system_prompt"] == _SUBAGENT_SYSTEM_PROMPT

    def test_system_prompt_contains_safety_rules(self) -> None:
        assert "safety" in _SUBAGENT_SYSTEM_PROMPT.lower()
        assert "destructive" in _SUBAGENT_SYSTEM_PROMPT.lower()
        assert "security" in _SUBAGENT_SYSTEM_PROMPT.lower()


class TestSubagentConfigIsolation:
    @pytest.mark.asyncio
    async def test_deepcopy_config(self) -> None:
        """Config should be deep-copied so child doesn't mutate parent."""
        mock_ai = _mock_ai()
        original_model = mock_ai.config.model

        mock_registry = MagicMock()
        mock_registry.get_openai_tools.return_value = []

        captured_configs: list = []

        def capture_ai_service(config, **kwargs):
            captured_configs.append(config)
            return MagicMock()

        async def mock_agent_loop(**kwargs):
            yield AgentEvent(kind="done", data={})

        with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
            with patch("anteroom.tools.subagent.AIService", side_effect=capture_ai_service):
                await handle(
                    prompt="test",
                    model="different-model",
                    _ai_service=mock_ai,
                    _tool_registry=mock_registry,
                    _depth=0,
                    _limiter=_make_limiter(),
                )

        # Parent config should be unchanged
        assert mock_ai.config.model == original_model
        # Child config should have the override
        assert captured_configs[0].model == "different-model"


class TestSubagentNestedAgentId:
    @pytest.mark.asyncio
    async def test_nested_subagent_gets_unique_agent_id(self) -> None:
        """Child tool executor should inject unique _agent_id for nested run_agent calls."""
        mock_registry = MagicMock()
        mock_registry.get_openai_tools.return_value = [{"function": {"name": "run_agent"}, "type": "function"}]

        captured_agent_ids: list[str] = []

        # The child_tool_executor is called when the child agent loop invokes tools.
        # We mock call_tool to capture the _agent_id injected for nested run_agent calls.
        async def mock_call_tool(name, args, confirm_callback=None):
            if name == "run_agent":
                captured_agent_ids.append(args.get("_agent_id", ""))
            return {"output": "ok"}

        mock_registry.call_tool = mock_call_tool

        async def mock_agent_loop(**kwargs):
            # Simulate the child calling run_agent twice (nested sub-agents)
            executor = kwargs["tool_executor"]
            await executor("run_agent", {"prompt": "nested-1"})
            await executor("run_agent", {"prompt": "nested-2"})
            yield AgentEvent(kind="done", data={})

        with patch("anteroom.tools.subagent.run_agent_loop", side_effect=mock_agent_loop):
            with patch("anteroom.tools.subagent.AIService"):
                await handle(
                    prompt="parent task",
                    _ai_service=_mock_ai(),
                    _tool_registry=mock_registry,
                    _depth=0,
                    _agent_id="agent-1",
                    _limiter=_make_limiter(),
                )

        # Each nested call should get a unique agent_id derived from the parent
        assert captured_agent_ids == ["agent-1.1", "agent-1.2"]


class TestRendererSubagentState:
    def test_clear_subagent_state(self) -> None:
        """clear_subagent_state should empty the tracking dict."""
        from anteroom.cli.renderer import _active_subagents, clear_subagent_state, render_subagent_start

        render_subagent_start("test-agent", "do something", "gpt-4", 1)
        assert "test-agent" in _active_subagents
        clear_subagent_state()
        assert len(_active_subagents) == 0


class TestSubagentRegistration:
    def test_registered_in_default_tools(self) -> None:
        from anteroom.tools import ToolRegistry, register_default_tools

        registry = ToolRegistry()
        register_default_tools(registry)
        assert registry.has_tool("run_agent")

    def test_in_openai_tools_list(self) -> None:
        from anteroom.tools import ToolRegistry, register_default_tools

        registry = ToolRegistry()
        register_default_tools(registry)
        tools = registry.get_openai_tools()
        names = [t["function"]["name"] for t in tools]
        assert "run_agent" in names
