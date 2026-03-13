"""Tests for the introspect built-in tool."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from anteroom.tools.introspect import (
    DEFINITION,
    _gather_budget,
    _gather_config,
    _gather_instructions,
    _gather_runtime,
    _gather_safety,
    _gather_skills,
    _gather_spaces,
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
        expected = {
            "config",
            "instructions",
            "tools",
            "safety",
            "skills",
            "budget",
            "spaces",
            "package",
            "runtime",
        }
        assert set(sections) == expected


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


# --- GatherConfig: config_files branch (line 91) ---


class TestGatherConfigFiles:
    """Tests for the config_files branch in _gather_config (issue #689)."""

    def test_config_file_appended_when_exists(self, tmp_path: Any) -> None:
        """Line 91: config_files.append() is hit when personal config exists."""
        # Create a real config.yaml in a tmp dir so personal.exists() is True
        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text("model: gpt-4o\n")

        config = FakeConfig(app=FakeAppSettings(data_dir=str(tmp_path)))
        result = _gather_config(config)
        assert any(f["layer"] == "personal" for f in result["config_files"])

    def test_config_files_empty_when_not_exists(self, tmp_path: Any) -> None:
        """config_files stays empty when the personal config file doesn't exist."""
        # tmp_path exists but no config.yaml inside it
        config = FakeConfig(app=FakeAppSettings(data_dir=str(tmp_path)))
        result = _gather_config(config)
        assert result["config_files"] == []

    def test_config_files_empty_when_no_data_dir(self) -> None:
        """config_files stays empty when data_dir is falsy."""
        config = FakeConfig(app=FakeAppSettings(data_dir=""))
        result = _gather_config(config)
        assert result["config_files"] == []


# --- GatherSafety: tool_tier_overrides (line 179) ---


class TestGatherSafetyToolTierOverrides:
    """Tests for the tool_tier_overrides branch in _gather_safety (issue #689)."""

    def test_tool_tier_overrides_included_when_set(self) -> None:
        """Line 179: result['tool_tier_overrides'] is set when tool_tiers is non-empty."""
        safety = FakeSafetyConfig(tool_tiers={"bash": "execute", "read_file": "read"})
        config = FakeConfig(safety=safety)
        result = _gather_safety(config)
        assert "tool_tier_overrides" in result
        assert result["tool_tier_overrides"]["bash"] == "execute"
        assert result["tool_tier_overrides"]["read_file"] == "read"

    def test_tool_tier_overrides_absent_when_empty(self) -> None:
        """tool_tier_overrides key is NOT present when tool_tiers is empty."""
        safety = FakeSafetyConfig(tool_tiers={})
        config = FakeConfig(safety=safety)
        result = _gather_safety(config)
        assert "tool_tier_overrides" not in result

    def test_tool_tier_overrides_absent_when_none(self) -> None:
        """tool_tier_overrides key is NOT present when tool_tiers is None."""
        safety = FakeSafetyConfig(tool_tiers=None)  # type: ignore[arg-type]
        config = FakeConfig(safety=safety)
        result = _gather_safety(config)
        assert "tool_tier_overrides" not in result


# --- GatherSpaces (lines 271-326) ---


class TestGatherSpaces:
    """Tests for _gather_spaces covering all enrichment branches (issue #689)."""

    def test_no_active_space_no_db(self) -> None:
        """Base case: no active space and no db."""
        result = _gather_spaces(active_space=None, db=None)
        assert result["available"] is False
        assert result["active"] is None
        assert result["total"] == 0
        assert result["names"] == []

    def test_active_space_without_db(self) -> None:
        """Lines 271-275: active_space info is set even when db is None."""
        space = {"name": "my-space", "id": "abc-123", "source_file": "/path/space.yaml"}
        result = _gather_spaces(active_space=space, db=None)
        assert result["active"]["name"] == "my-space"
        assert result["active"]["id"] == "abc-123"
        assert result["active"]["source_file"] == "/path/space.yaml"

    def test_active_space_with_db_enriches_repo_paths(self) -> None:
        """Lines 277-284: repo_paths populated via get_space_paths."""
        space = {"name": "ws", "id": "id-1", "source_file": "/f.yaml"}
        db = MagicMock()

        with (
            pytest.MonkeyPatch().context() as mp,
        ):
            fake_paths = [{"local_path": "/repo/a"}, {"local_path": "/repo/b"}]
            mp.setattr(
                "anteroom.tools.introspect._gather_spaces",
                _gather_spaces,  # keep original; patch the import inside
            )
            import anteroom.services.space_storage as ss_mod

            orig_gsp = getattr(ss_mod, "get_space_paths", None)
            ss_mod.get_space_paths = lambda _db, _id: fake_paths  # type: ignore[attr-defined]
            try:
                result = _gather_spaces(active_space=space, db=db)
            finally:
                if orig_gsp is None:
                    del ss_mod.get_space_paths  # type: ignore[attr-defined]
                else:
                    ss_mod.get_space_paths = orig_gsp  # type: ignore[attr-defined]

        assert result["active"]["repo_paths"] == ["/repo/a", "/repo/b"]

    def test_active_space_with_db_repo_paths_exception_fallback(self) -> None:
        """Lines 283-284: repo_paths falls back to [] when get_space_paths raises."""
        space = {"name": "ws", "id": "id-1", "source_file": "/f.yaml"}
        db = MagicMock()

        import anteroom.services.space_storage as ss_mod

        orig_gsp = getattr(ss_mod, "get_space_paths", None)
        ss_mod.get_space_paths = lambda _db, _id: (_ for _ in ()).throw(RuntimeError("db error"))  # type: ignore[attr-defined]
        try:
            result = _gather_spaces(active_space=space, db=db)
        finally:
            if orig_gsp is None:
                del ss_mod.get_space_paths  # type: ignore[attr-defined]
            else:
                ss_mod.get_space_paths = orig_gsp  # type: ignore[attr-defined]

        assert result["active"]["repo_paths"] == []

    def test_active_space_with_db_enriches_pack_count(self) -> None:
        """Lines 285-291: pack_count populated via get_active_pack_ids_for_space."""
        space = {"name": "ws", "id": "id-1", "source_file": "/f.yaml"}
        db = MagicMock()

        import anteroom.services.pack_attachments as pa_mod

        orig = getattr(pa_mod, "get_active_pack_ids_for_space", None)
        pa_mod.get_active_pack_ids_for_space = lambda _db, _id: ["p1", "p2", "p3"]  # type: ignore[attr-defined]
        try:
            result = _gather_spaces(active_space=space, db=db)
        finally:
            if orig is None:
                del pa_mod.get_active_pack_ids_for_space  # type: ignore[attr-defined]
            else:
                pa_mod.get_active_pack_ids_for_space = orig  # type: ignore[attr-defined]

        assert result["active"]["pack_count"] == 3

    def test_active_space_with_db_pack_count_exception_fallback(self) -> None:
        """Lines 290-291: pack_count falls back to 0 when the call raises."""
        space = {"name": "ws", "id": "id-1", "source_file": "/f.yaml"}
        db = MagicMock()

        import anteroom.services.pack_attachments as pa_mod

        orig = getattr(pa_mod, "get_active_pack_ids_for_space", None)
        pa_mod.get_active_pack_ids_for_space = lambda _db, _id: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[attr-defined]
        try:
            result = _gather_spaces(active_space=space, db=db)
        finally:
            if orig is None:
                del pa_mod.get_active_pack_ids_for_space  # type: ignore[attr-defined]
            else:
                pa_mod.get_active_pack_ids_for_space = orig  # type: ignore[attr-defined]

        assert result["active"]["pack_count"] == 0

    def test_active_space_with_db_enriches_source_count(self) -> None:
        """Lines 292-298: source_count populated via get_space_sources."""
        space = {"name": "ws", "id": "id-1", "source_file": "/f.yaml"}
        db = MagicMock()

        import anteroom.services.storage as storage_mod

        orig = getattr(storage_mod, "get_space_sources", None)
        storage_mod.get_space_sources = lambda _db, _id: [{"id": "s1"}, {"id": "s2"}]  # type: ignore[attr-defined]
        try:
            result = _gather_spaces(active_space=space, db=db)
        finally:
            if orig is None:
                del storage_mod.get_space_sources  # type: ignore[attr-defined]
            else:
                storage_mod.get_space_sources = orig  # type: ignore[attr-defined]

        assert result["active"]["source_count"] == 2

    def test_active_space_with_db_source_count_exception_fallback(self) -> None:
        """Lines 296-298: source_count falls back to 0 when the call raises."""
        space = {"name": "ws", "id": "id-1", "source_file": "/f.yaml"}
        db = MagicMock()

        import anteroom.services.storage as storage_mod

        orig = getattr(storage_mod, "get_space_sources", None)
        storage_mod.get_space_sources = lambda _db, _id: (_ for _ in ()).throw(RuntimeError("fail"))  # type: ignore[attr-defined]
        try:
            result = _gather_spaces(active_space=space, db=db)
        finally:
            if orig is None:
                del storage_mod.get_space_sources  # type: ignore[attr-defined]
            else:
                storage_mod.get_space_sources = orig  # type: ignore[attr-defined]

        assert result["active"]["source_count"] == 0

    def test_active_space_with_db_instructions_preview(self) -> None:
        """instructions_preview set when space dict has instructions."""
        space = {
            "name": "ws",
            "id": "id-1",
            "source_file": "/f.yaml",
            "instructions": "Follow these project conventions carefully.",
        }
        db = MagicMock()
        result = _gather_spaces(active_space=space, db=db)
        assert result["active"]["instructions_preview"] == "Follow these project conventions carefully."

    def test_active_space_instructions_preview_truncated(self) -> None:
        """Long instructions are truncated to 200 chars + suffix."""
        space = {
            "name": "ws",
            "id": "id-1",
            "source_file": "/f.yaml",
            "instructions": "A" * 300,
        }
        db = MagicMock()
        result = _gather_spaces(active_space=space, db=db)
        assert result["active"]["instructions_preview"].endswith("... (truncated)")
        assert len(result["active"]["instructions_preview"]) < 300

    def test_active_space_no_instructions_when_empty(self) -> None:
        """instructions_preview is absent when instructions is falsy."""
        space = {"name": "ws", "id": "id-1", "source_file": "/f.yaml", "instructions": ""}
        db = MagicMock()
        result = _gather_spaces(active_space=space, db=db)
        assert "instructions_preview" not in result["active"]

    def test_active_space_no_instructions_when_missing(self) -> None:
        """instructions_preview is absent when instructions key is missing."""
        space = {"name": "ws", "id": "id-1", "source_file": "/f.yaml"}
        db = MagicMock()
        result = _gather_spaces(active_space=space, db=db)
        assert "instructions_preview" not in result["active"]

    def test_list_spaces_with_db(self) -> None:
        """Lines 318-323: list_spaces is called and total/names are populated."""
        db = MagicMock()

        import anteroom.services.space_storage as ss_mod

        orig = getattr(ss_mod, "list_spaces", None)
        ss_mod.list_spaces = lambda _db: [{"name": "alpha"}, {"name": "beta"}]  # type: ignore[attr-defined]
        try:
            result = _gather_spaces(active_space=None, db=db)
        finally:
            if orig is None:
                del ss_mod.list_spaces  # type: ignore[attr-defined]
            else:
                ss_mod.list_spaces = orig  # type: ignore[attr-defined]

        assert result["total"] == 2
        assert result["names"] == ["alpha", "beta"]

    def test_list_spaces_exception_fallback(self) -> None:
        """Lines 324-326: list_spaces exception falls back to total=0, names=[]."""
        db = MagicMock()

        import anteroom.services.space_storage as ss_mod

        orig = getattr(ss_mod, "list_spaces", None)
        ss_mod.list_spaces = lambda _db: (_ for _ in ()).throw(RuntimeError("fail"))  # type: ignore[attr-defined]
        try:
            result = _gather_spaces(active_space=None, db=db)
        finally:
            if orig is None:
                del ss_mod.list_spaces  # type: ignore[attr-defined]
            else:
                ss_mod.list_spaces = orig  # type: ignore[attr-defined]

        assert result["total"] == 0
        assert result["names"] == []

    @pytest.mark.asyncio
    async def test_handle_spaces_section(self) -> None:
        """handle() dispatches to _gather_spaces when section='spaces'."""
        result = await handle(section="spaces")
        assert result["section"] == "spaces"
        assert "data" in result
        assert result["data"]["available"] is False

    @pytest.mark.asyncio
    async def test_handle_spaces_section_with_db(self) -> None:
        """handle() passes _active_space and _db through to _gather_spaces."""
        space = {"name": "test-space", "id": "sp-1", "source_file": "/f.yaml"}
        db = MagicMock()

        import anteroom.services.space_storage as ss_mod

        orig = getattr(ss_mod, "list_spaces", None)
        ss_mod.list_spaces = lambda _db: [{"name": "test-space"}]  # type: ignore[attr-defined]
        try:
            result = await handle(section="spaces", _active_space=space, _db=db)
        finally:
            if orig is None:
                del ss_mod.list_spaces  # type: ignore[attr-defined]
            else:
                ss_mod.list_spaces = orig  # type: ignore[attr-defined]

        assert result["data"]["available"] is True
        assert result["data"]["active"]["name"] == "test-space"
        assert result["data"]["total"] == 1


class TestGatherPackage:
    """Tests for _gather_package returning installed Anteroom source root and version."""

    def test_returns_source_root_and_version(self) -> None:
        from anteroom.tools.introspect import _gather_package

        result = _gather_package()
        assert "source_root" in result
        assert "version" in result
        assert Path(result["source_root"]).is_dir()
        assert isinstance(result["version"], str)
        assert result["version"] != "unknown"

    @pytest.mark.asyncio
    async def test_handle_package_section(self) -> None:
        result = await handle(section="package")
        assert result["section"] == "package"
        data = result["data"]
        assert "source_root" in data
        assert "version" in data
        assert Path(data["source_root"]).is_dir()

    @pytest.mark.asyncio
    async def test_package_in_summary(self) -> None:
        result = await handle()
        assert "package" in result
        assert "source_root" in result["package"]
        assert "version" in result["package"]


class TestGatherRuntime:
    """Tests for _gather_runtime returning bounded session metadata."""

    def test_returns_available_false_when_none(self) -> None:
        result = _gather_runtime(None)
        assert result == {"available": False}

    def test_returns_full_info(self) -> None:
        info = {
            "interface": "cli",
            "conversation_id": "conv-1",
            "conversation_title": "Test Chat",
            "slug": "bright-fox",
            "message_count": 5,
            "active_space": {"name": "my-space", "id": "sp-1"},
        }
        result = _gather_runtime(info)
        assert result["interface"] == "cli"
        assert result["conversation_id"] == "conv-1"
        assert result["slug"] == "bright-fox"
        assert result["message_count"] == 5
        assert result["active_space"]["name"] == "my-space"

    def test_strips_none_values(self) -> None:
        info = {
            "interface": "web",
            "conversation_id": "conv-2",
            "slug": None,
            "message_count": 0,
        }
        result = _gather_runtime(info)
        assert "slug" not in result
        assert result["interface"] == "web"
        assert result["message_count"] == 0

    def test_empty_dict_returns_empty(self) -> None:
        result = _gather_runtime({})
        assert result == {}

    @pytest.mark.asyncio
    async def test_handle_runtime_section(self) -> None:
        info = {"interface": "cli", "conversation_id": "c1"}
        result = await handle(section="runtime", _runtime_info=info)
        assert result["section"] == "runtime"
        assert result["data"]["interface"] == "cli"

    @pytest.mark.asyncio
    async def test_handle_runtime_section_no_info(self) -> None:
        result = await handle(section="runtime")
        assert result["data"] == {"available": False}

    @pytest.mark.asyncio
    async def test_runtime_in_summary(self) -> None:
        info = {"interface": "web", "conversation_id": "c1"}
        result = await handle(_runtime_info=info)
        assert "runtime" in result
        assert result["runtime"]["interface"] == "web"

    @pytest.mark.asyncio
    async def test_handle_runtime_with_full_payload(self) -> None:
        """Verify runtime passes through all expected fields including token_totals."""
        info = {
            "interface": "cli",
            "conversation_id": "c1",
            "conversation_title": "Test Chat",
            "slug": "happy-penguin",
            "message_count": 5,
            "token_totals": 12345,
            "active_space": {"name": "dev", "id": "s1"},
        }
        result = await handle(section="runtime", _runtime_info=info)
        data = result["data"]
        assert data["interface"] == "cli"
        assert data["conversation_id"] == "c1"
        assert data["conversation_title"] == "Test Chat"
        assert data["slug"] == "happy-penguin"
        assert data["message_count"] == 5
        assert data["token_totals"] == 12345
        assert data["active_space"] == {"name": "dev", "id": "s1"}

    @pytest.mark.asyncio
    async def test_handle_runtime_zero_values_preserved(self) -> None:
        """Verify 0-value fields are not stripped (only None is filtered)."""
        info = {"interface": "web", "message_count": 0, "token_totals": 0}
        result = await handle(section="runtime", _runtime_info=info)
        data = result["data"]
        assert data["message_count"] == 0
        assert data["token_totals"] == 0
