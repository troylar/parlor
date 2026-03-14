"""Tests for the shared slash-command engine (cli/commands.py)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from anteroom.cli.commands import (
    ALL_COMMAND_NAMES,
    COMMAND_DESCRIPTIONS,
    SUBCOMMAND_COMPLETIONS,
    CommandContext,
    CommandResult,
    SkillDescription,
    build_help_markdown,
    build_skills_markdown,
    build_tools_markdown,
    execute_slash_command,
    get_builtin_names,
    parse_slash_command,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(**kwargs: object) -> CommandContext:
    """Create a minimal CommandContext for tests."""
    defaults: dict[str, object] = {
        "current_model": "gpt-4o",
        "working_dir": "/tmp/test",
    }
    defaults.update(kwargs)
    return CommandContext(**defaults)  # type: ignore[arg-type]


def _exec(prompt: str, **ctx_kwargs: object) -> CommandResult | None:
    """Execute a slash command with a default context."""
    return execute_slash_command(prompt, _ctx(**ctx_kwargs))


# ---------------------------------------------------------------------------
# parse_slash_command
# ---------------------------------------------------------------------------


class TestParseSlashCommand:
    def test_valid_command(self) -> None:
        result = parse_slash_command("/help")
        assert result is not None
        assert result.name == "/help"
        assert result.arg == ""

    def test_command_with_arg(self) -> None:
        result = parse_slash_command("/new note My Title")
        assert result is not None
        assert result.name == "/new"
        assert result.arg == "note My Title"

    def test_non_slash_returns_none(self) -> None:
        assert parse_slash_command("hello world") is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_slash_command("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert parse_slash_command("   ") is None

    def test_leading_whitespace(self) -> None:
        result = parse_slash_command("  /help")
        assert result is not None
        assert result.name == "/help"

    def test_case_insensitive(self) -> None:
        result = parse_slash_command("/HELP")
        assert result is not None
        assert result.name == "/help"

    def test_preserves_raw(self) -> None:
        result = parse_slash_command("  /New note Title  ")
        assert result is not None
        assert result.raw == "  /New note Title  "

    def test_arg_whitespace_stripped(self) -> None:
        result = parse_slash_command("/search   some query  ")
        assert result is not None
        assert result.arg == "some query"


# ---------------------------------------------------------------------------
# Simple commands
# ---------------------------------------------------------------------------


class TestExitCommands:
    @pytest.mark.parametrize("cmd", ["/quit", "/exit", "/QUIT", "/Exit"])
    def test_exit(self, cmd: str) -> None:
        result = _exec(cmd)
        assert result is not None
        assert result.kind == "exit"
        assert result.echo_user is False


class TestHelpCommand:
    def test_help(self) -> None:
        result = _exec("/help")
        assert result is not None
        assert result.kind == "show_help"


class TestCompactCommand:
    def test_compact(self) -> None:
        result = _exec("/compact")
        assert result is not None
        assert result.kind == "compact_conversation"
        assert result.echo_user is False


class TestUsageCommand:
    def test_usage(self) -> None:
        result = _exec("/usage")
        assert result is not None
        assert result.kind == "show_usage"


class TestConventionsCommand:
    @pytest.mark.parametrize("cmd", ["/conventions", "/instructions"])
    def test_conventions(self, cmd: str) -> None:
        result = _exec(cmd)
        assert result is not None
        assert result.kind == "show_conventions"


class TestVerboseCommand:
    def test_verbose(self) -> None:
        result = _exec("/verbose")
        assert result is not None
        assert result.kind == "toggle_verbose"
        assert result.echo_user is False


class TestDetailCommand:
    def test_detail(self) -> None:
        result = _exec("/detail")
        assert result is not None
        assert result.kind == "show_detail"
        assert result.echo_user is False


class TestArtifactCheckCommand:
    def test_artifact_check(self) -> None:
        result = _exec("/artifact-check")
        assert result is not None
        assert result.kind == "check_artifacts"


# ---------------------------------------------------------------------------
# New conversation
# ---------------------------------------------------------------------------


class TestNewConversation:
    def test_new_default(self) -> None:
        result = _exec("/new")
        assert result is not None
        assert result.kind == "new_conversation"
        assert result.conversation_type == "chat"
        assert result.conversation_title == "New Conversation"
        assert result.echo_user is False

    def test_new_note(self) -> None:
        result = _exec("/new note My Note")
        assert result is not None
        assert result.conversation_type == "note"
        assert result.conversation_title == "My Note"

    def test_new_doc(self) -> None:
        result = _exec("/new doc")
        assert result is not None
        assert result.conversation_type == "document"
        assert result.conversation_title == "New Document"

    def test_new_document(self) -> None:
        result = _exec("/new document Report")
        assert result is not None
        assert result.conversation_type == "document"
        assert result.conversation_title == "Report"

    def test_new_note_no_title(self) -> None:
        result = _exec("/new note")
        assert result is not None
        assert result.conversation_type == "note"
        assert result.conversation_title == "New Note"


# ---------------------------------------------------------------------------
# Append
# ---------------------------------------------------------------------------


class TestAppendCommand:
    def test_append_with_text(self) -> None:
        result = _exec("/append some text here")
        assert result is not None
        assert result.kind == "append_text"
        assert result.message == "some text here"
        assert result.echo_user is False

    def test_append_no_text(self) -> None:
        result = _exec("/append")
        assert result is not None
        assert result.kind == "show_message"
        assert "Usage" in (result.message or "")


# ---------------------------------------------------------------------------
# List / Last / Resume / Search / Delete / Rename / Slug / Rewind
# ---------------------------------------------------------------------------


class TestListCommand:
    def test_list_default(self) -> None:
        result = _exec("/list")
        assert result is not None
        assert result.kind == "list_conversations"
        assert result.list_limit == 20

    def test_list_with_limit(self) -> None:
        result = _exec("/list 50")
        assert result is not None
        assert result.list_limit == 50

    def test_list_non_digit(self) -> None:
        result = _exec("/list abc")
        assert result is not None
        assert result.list_limit == 20


class TestLastCommand:
    def test_last(self) -> None:
        result = _exec("/last")
        assert result is not None
        assert result.kind == "resume_conversation"
        assert result.resume_target is None
        assert result.echo_user is False


class TestResumeCommand:
    def test_resume_no_arg(self) -> None:
        result = _exec("/resume")
        assert result is not None
        assert result.kind == "list_conversations"

    def test_resume_with_target(self) -> None:
        result = _exec("/resume my-slug")
        assert result is not None
        assert result.kind == "resume_conversation"
        assert result.resume_target == "my-slug"


class TestSearchCommand:
    def test_search_no_arg(self) -> None:
        result = _exec("/search")
        assert result is not None
        assert result.kind == "show_message"
        assert "Usage" in (result.message or "")

    def test_search_with_query(self) -> None:
        result = _exec("/search hello world")
        assert result is not None
        assert result.kind == "search_conversations"
        assert result.search_query == "hello world"


class TestDeleteCommand:
    def test_delete_no_arg(self) -> None:
        result = _exec("/delete")
        assert result is not None
        assert result.kind == "show_message"

    def test_delete_with_target(self) -> None:
        result = _exec("/delete my-slug")
        assert result is not None
        assert result.kind == "delete_conversation"
        assert result.delete_target == "my-slug"

    def test_delete_with_confirm(self) -> None:
        result = _exec("/delete --confirm my-slug")
        assert result is not None
        assert result.delete_target == "my-slug"


class TestRenameCommand:
    def test_rename_no_arg(self) -> None:
        result = _exec("/rename")
        assert result is not None
        assert result.kind == "show_message"

    def test_rename_with_title(self) -> None:
        result = _exec("/rename New Title")
        assert result is not None
        assert result.kind == "rename_conversation"
        assert result.conversation_title == "New Title"


class TestSlugCommand:
    def test_slug_no_arg(self) -> None:
        result = _exec("/slug")
        assert result is not None
        assert result.kind == "show_slug"

    def test_slug_set(self) -> None:
        result = _exec("/slug my-slug")
        assert result is not None
        assert result.kind == "set_slug"
        assert result.slug_value == "my-slug"


class TestRewindCommand:
    def test_rewind(self) -> None:
        result = _exec("/rewind 5")
        assert result is not None
        assert result.kind == "rewind_conversation"
        assert result.rewind_arg == "5"
        assert result.echo_user is False

    def test_rewind_no_arg(self) -> None:
        result = _exec("/rewind")
        assert result is not None
        assert result.kind == "rewind_conversation"
        assert result.rewind_arg == ""


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class TestModelCommand:
    def test_model_show(self) -> None:
        result = _exec("/model", current_model="gpt-4o")
        assert result is not None
        assert result.kind == "show_model"
        assert result.model_name == "gpt-4o"

    def test_model_set(self) -> None:
        result = _exec("/model claude-3-opus")
        assert result is not None
        assert result.kind == "set_model"
        assert result.model_name == "claude-3-opus"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class TestToolsCommand:
    def test_tools_with_available(self) -> None:
        result = _exec("/tools", available_tools=["read_file", "bash"])
        assert result is not None
        assert result.kind == "show_tools"
        assert "bash" in result.tool_names
        assert "read_file" in result.tool_names

    def test_tools_with_registry(self) -> None:
        reg = MagicMock()
        reg.list_tools.return_value = ["custom_tool"]
        result = _exec("/tools", tool_registry=reg, available_tools=["bash"])
        assert result is not None
        assert "custom_tool" in result.tool_names
        assert "bash" in result.tool_names


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


class TestUploadCommand:
    def test_upload_with_path(self) -> None:
        result = _exec("/upload /path/to/file.txt")
        assert result is not None
        assert result.kind == "upload_file"
        assert result.upload_path == "/path/to/file.txt"

    def test_upload_no_path(self) -> None:
        result = _exec("/upload")
        assert result is not None
        assert result.kind == "show_message"
        assert "Usage" in (result.message or "")


# ---------------------------------------------------------------------------
# Reprocess
# ---------------------------------------------------------------------------


class TestReprocessCommand:
    def test_reprocess_all(self) -> None:
        result = _exec("/reprocess all")
        assert result is not None
        assert result.kind == "reprocess_source"
        assert result.reprocess_arg == "all"

    def test_reprocess_no_arg(self) -> None:
        result = _exec("/reprocess")
        assert result is not None
        assert result.kind == "reprocess_source"
        assert result.reprocess_arg is None


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


class TestSkillsCommand:
    def test_skills_no_registry(self) -> None:
        result = _exec("/skills")
        assert result is not None
        assert result.kind == "show_skills"
        assert result.skill_entries == ()

    def test_skills_with_registry(self) -> None:
        reg = MagicMock(spec=["get_skill_descriptions", "get", "searched_dirs", "load_warnings"])
        reg.get_skill_descriptions.return_value = [("deploy", "Deploy app")]
        skill_mock = MagicMock()
        skill_mock.source = "global"
        skill_mock.prompt = "deploy {args}"
        reg.get.return_value = skill_mock
        reg.searched_dirs = []
        reg.load_warnings = []

        result = _exec("/skills", skill_registry=reg)
        assert result is not None
        assert result.kind == "show_skills"
        assert len(result.skill_entries) == 1
        assert result.skill_entries[0].display_name == "deploy"
        assert result.skill_entries[0].accepts_args is True

    def test_skills_purity_no_reload(self) -> None:
        """Engine must NOT call reload() or load_from_artifacts() — that's the adapter's job."""
        reg = MagicMock(
            spec=["get_skill_descriptions", "get", "searched_dirs", "load_warnings", "reload", "load_from_artifacts"],
        )
        reg.get_skill_descriptions.return_value = []
        reg.searched_dirs = []
        reg.load_warnings = []

        _exec("/skills", skill_registry=reg)
        reg.reload.assert_not_called()
        reg.load_from_artifacts.assert_not_called()

    def test_reload_skills_purity(self) -> None:
        """Same purity check for /reload-skills."""
        reg = MagicMock(
            spec=["get_skill_descriptions", "get", "searched_dirs", "load_warnings", "reload", "load_from_artifacts"],
        )
        reg.get_skill_descriptions.return_value = []
        reg.searched_dirs = []
        reg.load_warnings = []

        _exec("/reload-skills", skill_registry=reg)
        reg.reload.assert_not_called()


# ---------------------------------------------------------------------------
# Space
# ---------------------------------------------------------------------------


class TestSpaceCommands:
    def test_spaces(self) -> None:
        result = _exec("/spaces")
        assert result is not None
        assert result.kind == "show_spaces"

    def test_space_list(self) -> None:
        result = _exec("/space list")
        assert result is not None
        assert result.kind == "show_spaces"

    def test_space_no_arg(self) -> None:
        result = _exec("/space")
        assert result is not None
        assert result.kind == "show_spaces"

    def test_space_show(self) -> None:
        result = _exec("/space show my-space")
        assert result is not None
        assert result.kind == "show_space"
        assert result.space_target == "my-space"

    @pytest.mark.parametrize("sub", ["switch", "select", "use"])
    def test_space_switch_aliases(self, sub: str) -> None:
        result = _exec(f"/space {sub} my-space")
        assert result is not None
        assert result.kind == "set_space"
        assert result.space_target == "my-space"

    def test_space_switch_no_target(self) -> None:
        result = _exec("/space switch")
        assert result is not None
        assert result.kind == "show_message"

    def test_space_create(self) -> None:
        result = _exec("/space create my-space")
        assert result is not None
        assert result.kind == "create_space"
        assert result.space_target == "my-space"

    def test_space_create_no_name(self) -> None:
        result = _exec("/space create")
        assert result is not None
        assert result.kind == "show_message"

    def test_space_init(self) -> None:
        result = _exec("/space init")
        assert result is not None
        assert result.kind == "init_space"

    def test_space_load(self) -> None:
        result = _exec("/space load /path/to/space.yaml")
        assert result is not None
        assert result.kind == "load_space"
        assert result.space_target == "/path/to/space.yaml"

    def test_space_load_no_path(self) -> None:
        result = _exec("/space load")
        assert result is not None
        assert result.kind == "show_message"

    def test_space_clone(self) -> None:
        result = _exec("/space clone my-space")
        assert result is not None
        assert result.kind == "clone_space"

    def test_space_clone_no_name(self) -> None:
        result = _exec("/space clone")
        assert result is not None
        assert result.kind == "show_message"

    def test_space_map(self) -> None:
        result = _exec("/space map /some/dir")
        assert result is not None
        assert result.kind == "map_space"

    def test_space_map_no_dir(self) -> None:
        result = _exec("/space map")
        assert result is not None
        assert result.kind == "show_message"

    def test_space_refresh(self) -> None:
        result = _exec("/space refresh")
        assert result is not None
        assert result.kind == "refresh_space"

    def test_space_clear(self) -> None:
        result = _exec("/space clear")
        assert result is not None
        assert result.kind == "set_space"
        assert result.space_target == ""

    def test_space_edit_instructions(self) -> None:
        result = _exec("/space edit instructions New instructions here")
        assert result is not None
        assert result.kind == "update_space"
        assert result.space_edit_field == "instructions"
        assert result.space_edit_value == "New instructions here"

    def test_space_edit_model(self) -> None:
        result = _exec("/space edit model gpt-4o")
        assert result is not None
        assert result.kind == "update_space"
        assert result.space_edit_field == "model"

    def test_space_edit_name(self) -> None:
        result = _exec("/space edit name new-name")
        assert result is not None
        assert result.kind == "update_space"
        assert result.space_edit_field == "name"
        assert result.space_edit_value == "new-name"

    def test_space_edit_name_empty(self) -> None:
        result = _exec("/space edit name")
        assert result is not None
        assert result.kind == "show_message"

    def test_space_edit_unknown_field(self) -> None:
        result = _exec("/space edit bogus value")
        assert result is not None
        assert result.kind == "show_message"

    def test_space_edit_no_field(self) -> None:
        result = _exec("/space edit")
        assert result is not None
        assert result.kind == "show_message"

    def test_space_export(self) -> None:
        result = _exec("/space export")
        assert result is not None
        assert result.kind == "export_space"

    def test_space_sources(self) -> None:
        result = _exec("/space sources")
        assert result is not None
        assert result.kind == "show_space_sources"

    def test_space_link_source(self) -> None:
        result = _exec("/space link-source abc123")
        assert result is not None
        assert result.kind == "link_source"
        assert result.space_target == "abc123"

    def test_space_link_source_no_id(self) -> None:
        result = _exec("/space link-source")
        assert result is not None
        assert result.kind == "show_message"

    def test_space_unlink_source(self) -> None:
        result = _exec("/space unlink-source abc123")
        assert result is not None
        assert result.kind == "unlink_source"

    def test_space_unlink_source_no_id(self) -> None:
        result = _exec("/space unlink-source")
        assert result is not None
        assert result.kind == "show_message"

    def test_space_delete(self) -> None:
        result = _exec("/space delete my-space")
        assert result is not None
        assert result.kind == "delete_space"
        assert result.space_target == "my-space"

    def test_space_delete_no_name(self) -> None:
        result = _exec("/space delete")
        assert result is not None
        assert result.kind == "show_message"

    def test_space_delete_with_confirm(self) -> None:
        result = _exec("/space delete --confirm my-space")
        assert result is not None
        assert result.kind == "delete_space"
        assert result.space_target == "my-space"

    def test_space_unknown_subcommand(self) -> None:
        result = _exec("/space bogus")
        assert result is not None
        assert result.kind == "show_message"
        assert "Usage" in (result.message or "")


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------


class TestArtifactCommands:
    def test_artifacts_list(self) -> None:
        result = _exec("/artifacts")
        assert result is not None
        assert result.kind == "show_artifacts"

    def test_artifact_list(self) -> None:
        result = _exec("/artifact list")
        assert result is not None
        assert result.kind == "show_artifacts"

    def test_artifact_no_arg(self) -> None:
        result = _exec("/artifact")
        assert result is not None
        assert result.kind == "show_artifacts"

    def test_artifact_show(self) -> None:
        result = execute_slash_command("/artifact show ns/skill/test", _ctx())
        assert result is not None
        assert result.kind == "show_artifact"
        assert result.artifact_fqn == "ns/skill/test"

    def test_artifact_delete(self) -> None:
        result = execute_slash_command("/artifact delete ns/skill/test", _ctx())
        assert result is not None
        assert result.kind == "delete_artifact"

    def test_artifact_import(self) -> None:
        result = execute_slash_command("/artifact import", _ctx())
        assert result is not None
        assert result.kind == "show_message"
        assert "CLI" in (result.message or "")

    def test_artifact_create(self) -> None:
        result = execute_slash_command("/artifact create", _ctx())
        assert result is not None
        assert result.kind == "show_message"

    def test_artifact_unknown(self) -> None:
        result = execute_slash_command("/artifact bogus", _ctx())
        assert result is not None
        assert result.kind == "show_message"

    def test_artifacts_with_subcommand_not_overridden(self) -> None:
        """Regression: /artifacts show X must NOT override subcommand to 'list'."""
        result = execute_slash_command("/artifacts show ns/skill/test", _ctx())
        assert result is not None
        assert result.kind == "show_artifact"
        assert result.artifact_fqn == "ns/skill/test"

    def test_artifacts_delete_not_overridden(self) -> None:
        """Regression: /artifacts delete X must NOT override subcommand to 'list'."""
        result = execute_slash_command("/artifacts delete ns/skill/test", _ctx())
        assert result is not None
        assert result.kind == "delete_artifact"


# ---------------------------------------------------------------------------
# Pack
# ---------------------------------------------------------------------------


class TestPackCommands:
    def test_packs_list(self) -> None:
        result = _exec("/packs")
        assert result is not None
        assert result.kind == "show_packs"

    def test_pack_list(self) -> None:
        result = _exec("/pack list")
        assert result is not None
        assert result.kind == "show_packs"

    def test_pack_no_arg(self) -> None:
        result = _exec("/pack")
        assert result is not None
        assert result.kind == "show_packs"

    def test_pack_show(self) -> None:
        result = execute_slash_command("/pack show ns/name", _ctx())
        assert result is not None
        assert result.kind == "show_pack"
        assert result.pack_ref == "ns/name"

    def test_pack_remove(self) -> None:
        result = execute_slash_command("/pack remove ns/name", _ctx())
        assert result is not None
        assert result.kind == "delete_pack"

    def test_pack_delete_alias(self) -> None:
        result = execute_slash_command("/pack delete ns/name", _ctx())
        assert result is not None
        assert result.kind == "delete_pack"

    def test_pack_sources(self) -> None:
        result = _exec("/pack sources")
        assert result is not None
        assert result.kind == "show_pack_sources"

    def test_pack_refresh(self) -> None:
        result = _exec("/pack refresh")
        assert result is not None
        assert result.kind == "refresh_pack_sources"

    def test_pack_add_source(self) -> None:
        result = execute_slash_command("/pack add-source https://example.com/repo.git", _ctx())
        assert result is not None
        assert result.kind == "add_pack_source"
        assert result.pack_source_url == "https://example.com/repo.git"

    def test_pack_attach(self) -> None:
        result = execute_slash_command("/pack attach ns/name", _ctx())
        assert result is not None
        assert result.kind == "attach_pack"
        assert result.pack_ref == "ns/name"

    def test_pack_attach_project(self) -> None:
        result = execute_slash_command("/pack attach ns/name --project", _ctx())
        assert result is not None
        assert result.kind == "attach_pack"
        assert result.pack_project_scope is True

    def test_pack_attach_no_target(self) -> None:
        result = execute_slash_command("/pack attach", _ctx())
        assert result is not None
        assert result.kind == "show_message"

    def test_pack_detach(self) -> None:
        result = execute_slash_command("/pack detach ns/name", _ctx())
        assert result is not None
        assert result.kind == "detach_pack"

    def test_pack_install(self) -> None:
        result = execute_slash_command("/pack install /path/to/pack", _ctx())
        assert result is not None
        assert result.kind == "install_pack"
        assert result.pack_path == "/path/to/pack"

    def test_pack_install_with_flags(self) -> None:
        result = execute_slash_command("/pack install /path --attach --priority 10", _ctx())
        assert result is not None
        assert result.kind == "install_pack"
        assert result.pack_attach_after_install is True
        assert result.pack_priority == 10

    def test_pack_install_no_path(self) -> None:
        result = execute_slash_command("/pack install", _ctx())
        assert result is not None
        assert result.kind == "show_message"

    def test_pack_update(self) -> None:
        result = execute_slash_command("/pack update /path/to/pack", _ctx())
        assert result is not None
        assert result.kind == "update_pack"

    def test_pack_unknown(self) -> None:
        result = execute_slash_command("/pack bogus", _ctx())
        assert result is not None
        assert result.kind == "show_message"

    def test_packs_with_subcommand_not_overridden(self) -> None:
        """Regression: /packs show X must NOT override subcommand to 'list'."""
        result = execute_slash_command("/packs show ns/name", _ctx())
        assert result is not None
        assert result.kind == "show_pack"
        assert result.pack_ref == "ns/name"

    def test_packs_remove_not_overridden(self) -> None:
        """Regression: /packs remove X must NOT override subcommand to 'list'."""
        result = execute_slash_command("/packs remove ns/name", _ctx())
        assert result is not None
        assert result.kind == "delete_pack"


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


class TestMcpCommands:
    def test_mcp_no_arg(self) -> None:
        result = _exec("/mcp")
        assert result is not None
        assert result.kind == "show_mcp_status"

    def test_mcp_status(self) -> None:
        result = _exec("/mcp status")
        assert result is not None
        assert result.kind == "show_mcp_status"

    def test_mcp_status_server(self) -> None:
        result = _exec("/mcp status my-server")
        assert result is not None
        assert result.kind == "show_mcp_server_detail"
        assert result.mcp_server_name == "my-server"

    @pytest.mark.parametrize("action", ["connect", "disconnect", "reconnect"])
    def test_mcp_actions(self, action: str) -> None:
        result = _exec(f"/mcp {action} my-server")
        assert result is not None
        assert result.kind == "run_mcp_action"
        assert result.mcp_action == action
        assert result.mcp_server_name == "my-server"

    @pytest.mark.parametrize("action", ["connect", "disconnect", "reconnect"])
    def test_mcp_actions_no_target(self, action: str) -> None:
        result = _exec(f"/mcp {action}")
        assert result is not None
        assert result.kind == "show_message"

    def test_mcp_unknown(self) -> None:
        result = _exec("/mcp bogus")
        assert result is not None
        assert result.kind == "show_message"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestConfigCommand:
    def test_config_no_arg(self) -> None:
        result = _exec("/config")
        assert result is not None
        assert result.kind == "show_config"
        assert result.config_subcommand is None

    @pytest.mark.parametrize("sub", ["list", "get", "set", "reset"])
    def test_config_subcommands(self, sub: str) -> None:
        result = _exec(f"/config {sub} some.field")
        assert result is not None
        assert result.kind == "show_config"
        assert result.config_subcommand == sub
        assert result.config_arg == "some.field"

    def test_config_unknown_opens_tui(self) -> None:
        result = _exec("/config bogus")
        assert result is not None
        assert result.kind == "show_config"
        assert result.config_subcommand is None


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


class TestPlanCommands:
    def test_plan_on(self) -> None:
        result = _exec("/plan on")
        assert result is not None
        assert result.kind == "set_plan_mode"
        assert result.plan_mode_enabled is True

    def test_plan_start(self) -> None:
        result = _exec("/plan start")
        assert result is not None
        assert result.kind == "set_plan_mode"
        assert result.plan_mode_enabled is True

    def test_plan_off(self) -> None:
        result = _exec("/plan off")
        assert result is not None
        assert result.kind == "set_plan_mode"
        assert result.plan_mode_enabled is False

    def test_plan_status(self) -> None:
        result = _exec("/plan status", plan_mode=True)
        assert result is not None
        assert result.kind == "show_plan_status"
        assert result.plan_mode_enabled is True

    def test_plan_approve(self) -> None:
        result = _exec("/plan approve")
        assert result is not None
        assert result.kind == "approve_plan"

    def test_plan_edit(self) -> None:
        result = _exec("/plan edit some edits")
        assert result is not None
        assert result.kind == "edit_plan"
        assert result.plan_edit_arg == "some edits"

    def test_plan_reject(self) -> None:
        """Regression: /plan reject must dispatch as reject_plan, not fall through to agent."""
        result = _exec("/plan reject")
        assert result is not None
        assert result.kind == "reject_plan"

    def test_plan_inline_prompt_returns_none(self) -> None:
        """Inline plan prompts fall through to the agent loop."""
        result = _exec("/plan build a REST API")
        assert result is None

    def test_plan_no_arg_defaults_to_on(self) -> None:
        """``/plan`` with no arguments defaults to ``on`` via parse_plan_command."""
        result = _exec("/plan")
        assert result is not None
        assert result.kind == "set_plan_mode"
        assert result.plan_mode_enabled is True


# ---------------------------------------------------------------------------
# Skill resolution (fallback)
# ---------------------------------------------------------------------------


class TestSkillResolution:
    def test_skill_match(self) -> None:
        reg = MagicMock()
        reg.resolve_input.return_value = (True, "expanded prompt")
        result = _exec("/deploy", skill_registry=reg)
        assert result is not None
        assert result.kind == "forward_prompt"
        assert result.forward_prompt == "expanded prompt"
        assert result.echo_user is False

    def test_skill_no_match(self) -> None:
        reg = MagicMock()
        reg.resolve_input.return_value = (False, "")
        result = _exec("/unknown-command", skill_registry=reg)
        assert result is None

    def test_no_registry_unknown_returns_none(self) -> None:
        result = _exec("/unknown-command")
        assert result is None


# ---------------------------------------------------------------------------
# Non-slash input
# ---------------------------------------------------------------------------


class TestNonSlashInput:
    def test_plain_text_returns_none(self) -> None:
        result = _exec("hello world")
        assert result is None

    def test_empty_returns_none(self) -> None:
        result = _exec("")
        assert result is None


# ---------------------------------------------------------------------------
# Metadata consistency
# ---------------------------------------------------------------------------


class TestMetadataConsistency:
    def test_all_commands_have_descriptions(self) -> None:
        for name in ALL_COMMAND_NAMES:
            assert name in COMMAND_DESCRIPTIONS, f"Command '{name}' missing from COMMAND_DESCRIPTIONS"

    def test_descriptions_match_commands(self) -> None:
        for name in COMMAND_DESCRIPTIONS:
            assert name in ALL_COMMAND_NAMES, f"Description for '{name}' has no matching command"

    def test_get_builtin_names_matches_all_commands(self) -> None:
        builtins = get_builtin_names()
        expected = frozenset(ALL_COMMAND_NAMES)
        assert builtins == expected

    def test_builtin_names_includes_previously_missing(self) -> None:
        """Commands that were missing from the old hardcoded _BUILTIN_COMMANDS."""
        builtins = get_builtin_names()
        for name in ["instructions", "artifact", "artifacts", "artifact-check", "config", "reprocess"]:
            assert name in builtins, f"'{name}' should be in builtin names"

    def test_builtin_names_excludes_stale(self) -> None:
        """Stale entries that were in the old hardcoded _BUILTIN_COMMANDS."""
        builtins = get_builtin_names()
        for name in ["project", "projects"]:
            assert name not in builtins, f"'{name}' should NOT be in builtin names"

    def test_space_subcommand_completions_complete(self) -> None:
        expected = {
            "list",
            "show",
            "switch",
            "select",
            "use",
            "create",
            "init",
            "load",
            "refresh",
            "clear",
            "clone",
            "map",
            "edit",
            "export",
            "sources",
            "link-source",
            "unlink-source",
        }
        actual = set(SUBCOMMAND_COMPLETIONS["space"])
        assert actual == expected, f"Missing: {expected - actual}, Extra: {actual - expected}"

    def test_pack_subcommand_completions_complete(self) -> None:
        expected = {
            "list",
            "show",
            "install",
            "remove",
            "sources",
            "attach",
            "detach",
            "update",
            "add-source",
            "refresh",
        }
        actual = set(SUBCOMMAND_COMPLETIONS["pack"])
        assert actual == expected

    def test_artifact_subcommand_completions_complete(self) -> None:
        expected = {"list", "show", "delete", "import", "create"}
        actual = set(SUBCOMMAND_COMPLETIONS["artifact"])
        assert actual == expected

    def test_mcp_subcommand_completions_complete(self) -> None:
        expected = {"status", "connect", "disconnect", "reconnect"}
        actual = set(SUBCOMMAND_COMPLETIONS["mcp"])
        assert actual == expected


# ---------------------------------------------------------------------------
# Display builders
# ---------------------------------------------------------------------------


class TestBuildHelpMarkdown:
    def test_contains_core_commands(self) -> None:
        text = build_help_markdown()
        assert "/new" in text
        assert "/help" not in text or "/quit" in text  # help mentions quit
        assert "/quit" in text
        assert "/skills" in text

    def test_mentions_custom_skills(self) -> None:
        assert "Custom skills" in build_help_markdown()


class TestBuildToolsMarkdown:
    def test_empty(self) -> None:
        assert "No tools" in build_tools_markdown([])

    def test_with_tools(self) -> None:
        text = build_tools_markdown(["bash", "read_file"])
        assert "`bash`" in text
        assert "`read_file`" in text


class TestBuildSkillsMarkdown:
    def test_no_registry(self) -> None:
        text = build_skills_markdown([], [], has_registry=False)
        assert "No skill registry" in text

    def test_no_skills(self) -> None:
        text = build_skills_markdown([], [])
        assert "No skills loaded" in text

    def test_with_skills(self) -> None:
        entries = [SkillDescription("deploy", "Deploy app", "global")]
        text = build_skills_markdown(entries, [])
        assert "/deploy" in text
        assert "Deploy app" in text

    def test_with_warnings(self) -> None:
        text = build_skills_markdown([], ["bad skill"])
        assert "bad skill" in text
        assert "Warnings" in text


# ---------------------------------------------------------------------------
# _parse_pack_path_flags edge cases
# ---------------------------------------------------------------------------


class TestParsePackPathFlags:
    def test_invalid_shlex(self) -> None:
        from anteroom.cli.commands import _parse_pack_path_flags

        result = _parse_pack_path_flags("install", "path 'unclosed")
        assert isinstance(result, str)

    def test_no_path(self) -> None:
        from anteroom.cli.commands import _parse_pack_path_flags

        result = _parse_pack_path_flags("install", "")
        assert isinstance(result, str)
        assert "Usage" in result

    def test_priority_no_value(self) -> None:
        from anteroom.cli.commands import _parse_pack_path_flags

        result = _parse_pack_path_flags("install", "/path --priority")
        assert isinstance(result, str)

    def test_priority_non_int(self) -> None:
        from anteroom.cli.commands import _parse_pack_path_flags

        result = _parse_pack_path_flags("install", "/path --priority abc")
        assert isinstance(result, str)
        assert "integer" in result

    def test_multiple_paths(self) -> None:
        from anteroom.cli.commands import _parse_pack_path_flags

        result = _parse_pack_path_flags("install", "/path1 /path2")
        assert isinstance(result, str)

    def test_update_ignores_attach(self) -> None:
        from anteroom.cli.commands import _parse_pack_path_flags

        result = _parse_pack_path_flags("update", "/path --attach")
        # --attach is only valid for install, so it becomes an extra path token
        assert isinstance(result, str)  # Usage error due to extra token


# ---------------------------------------------------------------------------
# _parse_new_conversation edge cases
# ---------------------------------------------------------------------------


class TestParseNewConversation:
    def test_empty(self) -> None:
        from anteroom.cli.commands import _parse_new_conversation

        assert _parse_new_conversation("") == ("chat", "New Conversation")

    def test_note_with_title(self) -> None:
        from anteroom.cli.commands import _parse_new_conversation

        assert _parse_new_conversation("note My Title") == ("note", "My Title")

    def test_doc_alias(self) -> None:
        from anteroom.cli.commands import _parse_new_conversation

        assert _parse_new_conversation("doc Report")[0] == "document"

    def test_document_alias(self) -> None:
        from anteroom.cli.commands import _parse_new_conversation

        assert _parse_new_conversation("document Report")[0] == "document"

    def test_unknown_type(self) -> None:
        from anteroom.cli.commands import _parse_new_conversation

        assert _parse_new_conversation("other stuff") == ("chat", "New Conversation")
