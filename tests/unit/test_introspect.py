"""Tests for the introspect built-in tool."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from anteroom.tools.introspect import (
    DEFINITION,
    _gather_budget,
    _gather_config,
    _gather_instructions,
    _gather_safety,
    _gather_skills,
    _gather_tools,
    _redact,
    handle,
)

# --- Fixtures / helpers ---


@dataclass
class FakeAIConfig:
    model: str = "gpt-4o"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = "sk-secret-key-12345"
    system_prompt: str = "You are a helpful assistant."
    verify_ssl: bool = True
    request_timeout: int = 120


@dataclass
class FakeAppSettings:
    host: str = "127.0.0.1"
    port: int = 8080
    data_dir: str = "/tmp/test-anteroom"
    tls: bool = False


@dataclass
class FakeSubagentConfig:
    max_concurrent: int = 5
    max_total: int = 10
    max_depth: int = 3
    max_iterations: int = 50
    timeout: int = 300


@dataclass
class FakeSafetyConfig:
    enabled: bool = True
    approval_mode: str = "ask_for_writes"
    allowed_tools: list[str] = field(default_factory=lambda: ["read_file"])
    denied_tools: list[str] = field(default_factory=lambda: ["dangerous_tool"])
    custom_patterns: list[str] = field(default_factory=list)
    sensitive_paths: list[str] = field(default_factory=list)
    tool_tiers: dict[str, str] = field(default_factory=dict)
    subagent: FakeSubagentConfig = field(default_factory=FakeSubagentConfig)


@dataclass
class FakeCliConfig:
    model_context_window: int = 128000


@dataclass
class FakeConfig:
    ai: FakeAIConfig = field(default_factory=FakeAIConfig)
    app: FakeAppSettings = field(default_factory=FakeAppSettings)
    safety: FakeSafetyConfig = field(default_factory=FakeSafetyConfig)
    cli: FakeCliConfig = field(default_factory=FakeCliConfig)


def _make_tool_openai(name: str, desc: str = "A tool") -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


# --- Definition tests ---


class TestIntrospectDefinition:
    def test_name(self) -> None:
        assert DEFINITION["name"] == "introspect"

    def test_has_section_parameter(self) -> None:
        props = DEFINITION["parameters"]["properties"]
        assert "section" in props
        assert "enum" in props["section"]

    def test_section_not_required(self) -> None:
        assert DEFINITION["parameters"]["required"] == []

    def test_valid_sections(self) -> None:
        sections = DEFINITION["parameters"]["properties"]["section"]["enum"]
        assert set(sections) == {"config", "instructions", "tools", "safety", "skills", "budget", "spaces"}


# --- Registration tests ---


class TestIntrospectRegistration:
    def test_registered_in_default_tools(self) -> None:
        from anteroom.tools import ToolRegistry, register_default_tools

        registry = ToolRegistry()
        register_default_tools(registry)
        assert registry.has_tool("introspect")

    def test_read_tier(self) -> None:
        from anteroom.tools.tiers import DEFAULT_TOOL_TIERS, ToolTier

        assert DEFAULT_TOOL_TIERS["introspect"] == ToolTier.READ


# --- Redaction tests ---


class TestRedact:
    def test_redacts_api_key(self) -> None:
        assert _redact("api_key", "sk-12345") == "****"

    def test_redacts_password(self) -> None:
        assert _redact("db_password", "hunter2") == "****"

    def test_redacts_secret(self) -> None:
        assert _redact("client_secret", "abc") == "****"

    def test_redacts_token(self) -> None:
        assert _redact("auth_token", "tok-xyz") == "****"

    def test_does_not_redact_normal(self) -> None:
        assert _redact("model", "gpt-4o") == "gpt-4o"

    def test_empty_value_not_redacted(self) -> None:
        assert _redact("api_key", "") == ""

    def test_non_string_not_redacted(self) -> None:
        assert _redact("api_key", 12345) == 12345


# --- Section gatherer tests ---


class TestGatherConfig:
    def test_returns_ai_fields(self) -> None:
        config = FakeConfig()
        result = _gather_config(config)
        assert result["ai"]["model"] == "gpt-4o"
        assert result["ai"]["base_url"] == "https://api.openai.com/v1"

    def test_api_key_always_redacted(self) -> None:
        config = FakeConfig()
        result = _gather_config(config)
        assert result["ai"]["api_key"] == "****"

    def test_api_key_not_set(self) -> None:
        config = FakeConfig(ai=FakeAIConfig(api_key=""))
        result = _gather_config(config)
        assert result["ai"]["api_key"] == "(not set)"

    def test_app_settings(self) -> None:
        config = FakeConfig()
        result = _gather_config(config)
        assert result["app"]["port"] == 8080

    def test_long_system_prompt_truncated(self) -> None:
        config = FakeConfig(ai=FakeAIConfig(system_prompt="x" * 500))
        result = _gather_config(config)
        assert "truncated" in result["ai"]["system_prompt"]
        assert len(result["ai"]["system_prompt"]) < 500


class TestGatherInstructions:
    def test_no_info(self) -> None:
        result = _gather_instructions(None)
        assert result["loaded"] is False

    def test_with_sources(self) -> None:
        info = {
            "sources": [
                {"path": "/home/user/.anteroom/ANTEROOM.md", "source": "global", "estimated_tokens": 500},
                {"path": "/project/ANTEROOM.md", "source": "project", "estimated_tokens": 300},
            ],
            "total_tokens": 800,
        }
        result = _gather_instructions(info)
        assert result["loaded"] is True
        assert len(result["sources"]) == 2
        assert result["total_tokens"] == 800


class TestGatherTools:
    def test_builtin_tools(self) -> None:
        registry = MagicMock()
        registry.list_tools.return_value = ["read_file", "write_file", "bash"]
        result = _gather_tools(registry, None, None)
        assert result["builtin"]["count"] == 3
        assert "read_file" in result["builtin"]["names"]

    def test_mcp_servers(self) -> None:
        registry = MagicMock()
        registry.list_tools.return_value = []
        mcp = MagicMock()
        mcp.get_server_statuses.return_value = {
            "github": {"transport": "stdio", "status": "connected", "tool_count": 5},
        }
        mcp.get_all_tools.return_value = [
            {"name": "create_issue", "server_name": "github"},
            {"name": "list_repos", "server_name": "github"},
        ]
        result = _gather_tools(registry, mcp, None)
        assert len(result["mcp_servers"]) == 1
        assert result["mcp_servers"][0]["name"] == "github"
        assert result["mcp_servers"][0]["tool_count"] == 5
        assert "create_issue" in result["mcp_servers"][0]["tools"]

    def test_denied_tools_from_config(self) -> None:
        config = FakeConfig()
        result = _gather_tools(None, None, config)
        assert "dangerous_tool" in result["denied_tools"]

    def test_no_registry_or_mcp(self) -> None:
        result = _gather_tools(None, None, None)
        assert result["builtin"]["count"] == 0
        assert result["mcp_servers"] == []


class TestGatherSafety:
    def test_returns_approval_mode(self) -> None:
        config = FakeConfig()
        result = _gather_safety(config)
        assert result["approval_mode"] == "ask_for_writes"

    def test_includes_subagent_limits(self) -> None:
        config = FakeConfig()
        result = _gather_safety(config)
        assert result["subagent"]["max_concurrent"] == 5
        assert result["subagent"]["max_depth"] == 3

    def test_no_config(self) -> None:
        result = _gather_safety(None)
        assert result["available"] is False


class TestGatherSkills:
    def test_no_registry(self) -> None:
        result = _gather_skills(None)
        assert result["available"] is False

    def test_with_skills(self) -> None:
        skill1 = MagicMock()
        skill1.name = "commit"
        skill1.source = "default"
        skill2 = MagicMock()
        skill2.name = "deploy"
        skill2.source = "project"
        registry = MagicMock()
        registry.list_skills.return_value = [skill1, skill2]
        result = _gather_skills(registry)
        assert result["total"] == 2
        assert "default" in result["by_source"]
        assert "project" in result["by_source"]


class TestGatherBudget:
    def test_tool_token_estimation(self) -> None:
        tools = [_make_tool_openai("read_file"), _make_tool_openai("bash")]
        result = _gather_budget(tools, None, None)
        assert result["tool_definitions"]["count"] == 2
        assert result["tool_definitions"]["estimated_tokens"] > 0

    def test_instruction_tokens(self) -> None:
        info = {"total_tokens": 1200}
        result = _gather_budget(None, info, None)
        assert result["instructions"]["estimated_tokens"] == 1200

    def test_total_fixed_tokens(self) -> None:
        tools = [_make_tool_openai("read_file")]
        info = {"total_tokens": 500}
        config = FakeConfig()
        result = _gather_budget(tools, info, config)
        expected_tool = result["tool_definitions"]["estimated_tokens"]
        expected_sys = result["system_prompt"]["estimated_tokens"]
        assert result["total_fixed_tokens"] == expected_tool + 500 + expected_sys

    def test_percentage_used(self) -> None:
        config = FakeConfig(cli=FakeCliConfig(model_context_window=100000))
        info = {"total_tokens": 10000}
        result = _gather_budget(None, info, config)
        assert result["percentage_used"] == 10.0

    def test_no_inputs(self) -> None:
        result = _gather_budget(None, None, None)
        assert result["total_fixed_tokens"] == 0


# --- Handler tests ---


class TestHandle:
    @pytest.mark.asyncio
    async def test_returns_all_sections(self) -> None:
        config = FakeConfig()
        result = await handle(
            _config=config,
            _working_dir="/tmp/project",
        )
        assert "config" in result
        assert "instructions" in result
        assert "tools" in result
        assert "safety" in result
        assert "skills" in result
        assert "budget" in result
        assert result["working_dir"] == "/tmp/project"

    @pytest.mark.asyncio
    async def test_single_section(self) -> None:
        config = FakeConfig()
        result = await handle(
            section="safety",
            _config=config,
        )
        assert result["section"] == "safety"
        assert "data" in result
        assert result["data"]["approval_mode"] == "ask_for_writes"

    @pytest.mark.asyncio
    async def test_unknown_section(self) -> None:
        result = await handle(section="nonexistent")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_context_still_works(self) -> None:
        result = await handle()
        assert "config" in result
        assert result["config"] == {"available": False}
        assert result["instructions"]["loaded"] is False

    @pytest.mark.asyncio
    async def test_with_mcp_manager(self) -> None:
        mcp = MagicMock()
        mcp.get_server_statuses.return_value = {
            "fs": {"transport": "stdio", "status": "connected", "tool_count": 3},
        }
        mcp.get_all_tools.return_value = [
            {"name": "read_dir", "server_name": "fs"},
        ]
        result = await handle(
            section="tools",
            _mcp_manager=mcp,
        )
        assert len(result["data"]["mcp_servers"]) == 1
        assert result["data"]["mcp_servers"][0]["name"] == "fs"

    @pytest.mark.asyncio
    async def test_with_tools_openai_for_budget(self) -> None:
        tools = [_make_tool_openai("read_file"), _make_tool_openai("bash")]
        result = await handle(
            section="budget",
            _tools_openai=tools,
        )
        assert result["data"]["tool_definitions"]["count"] == 2
        assert result["data"]["total_fixed_tokens"] > 0
