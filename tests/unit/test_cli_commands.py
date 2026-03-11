from __future__ import annotations

from anteroom.cli.commands import (
    CommandContext,
    build_skills_markdown,
    build_tools_markdown,
    execute_slash_command,
    parse_slash_command,
)


class FakeSkill:
    def __init__(self, source: str, prompt: str = "Default prompt") -> None:
        self.source = source
        self.prompt = prompt


class FakeSkillRegistry:
    def __init__(self) -> None:
        self.reload_calls: list[str] = []
        self.load_warnings = ["warning one"]
        self.searched_dirs = []
        self._skills = {"deploy-check": FakeSkill("project", "Run checks with {args}.")}
        self.resolve_calls = 0

    def reload(self, working_dir: str | None = None):
        self.reload_calls.append(working_dir or "")

    def load_from_artifacts(self, artifact_registry):
        self.artifact_registry = artifact_registry

    def get_skill_descriptions(self):
        return [("deploy-check", "Run the deployment checklist")]

    def get(self, name: str):
        return self._skills.get(name)

    def resolve_input(self, prompt: str):
        self.resolve_calls += 1
        if prompt == "/deploy-check staging":
            return True, "Run the deployment checklist against staging."
        return False, prompt


def test_parse_slash_command() -> None:
    parsed = parse_slash_command("/model gpt-5.2")
    assert parsed is not None
    assert parsed.name == "/model"
    assert parsed.arg == "gpt-5.2"


def test_execute_new_note_command() -> None:
    result = execute_slash_command(
        "/new note Architecture Notes",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    assert result is not None
    assert result.kind == "new_conversation"
    assert result.conversation_type == "note"
    assert result.conversation_title == "Architecture Notes"


def test_execute_model_commands() -> None:
    show_result = execute_slash_command("/model", CommandContext(current_model="gpt-5.2", working_dir="/repo"))
    set_result = execute_slash_command(
        "/model gpt-5.4-mini",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )

    assert show_result is not None
    assert show_result.kind == "show_model"
    assert show_result.model_name == "gpt-5.2"

    assert set_result is not None
    assert set_result.kind == "set_model"
    assert set_result.model_name == "gpt-5.4-mini"


def test_execute_conversation_management_commands() -> None:
    list_result = execute_slash_command("/list 7", CommandContext(current_model="gpt-5.2", working_dir="/repo"))
    resume_result = execute_slash_command(
        "/resume architecture-notes",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    search_result = execute_slash_command(
        "/search renderer",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    rename_result = execute_slash_command(
        "/rename New title",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    slug_result = execute_slash_command(
        "/slug architecture-notes",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    rewind_result = execute_slash_command(
        "/rewind 4 --undo-files",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    spaces_result = execute_slash_command("/spaces", CommandContext(current_model="gpt-5.2", working_dir="/repo"))
    space_show_result = execute_slash_command(
        "/space show demo",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    space_create_result = execute_slash_command(
        "/space create demo",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    space_edit_result = execute_slash_command(
        "/space edit model gpt-5.4-mini",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    space_refresh_result = execute_slash_command(
        "/space refresh demo",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    space_export_result = execute_slash_command(
        "/space export demo",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    space_switch_result = execute_slash_command(
        "/space switch demo",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    space_select_result = execute_slash_command(
        "/space select demo",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    space_use_result = execute_slash_command(
        "/space use demo",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    space_sources_result = execute_slash_command(
        "/space sources demo",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    space_delete_result = execute_slash_command(
        "/space delete demo",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    artifacts_result = execute_slash_command("/artifacts", CommandContext(current_model="gpt-5.2", working_dir="/repo"))
    artifact_show_result = execute_slash_command(
        "/artifact show @core/skill/demo",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    artifact_delete_result = execute_slash_command(
        "/artifact delete @core/skill/demo",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    packs_result = execute_slash_command("/packs", CommandContext(current_model="gpt-5.2", working_dir="/repo"))
    pack_show_result = execute_slash_command(
        "/pack show default/demo",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    pack_delete_result = execute_slash_command(
        "/pack remove default/demo",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    pack_attach_result = execute_slash_command(
        "/pack attach default/demo",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    pack_detach_result = execute_slash_command(
        "/pack detach default/demo",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    pack_sources_result = execute_slash_command(
        "/pack sources",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    pack_refresh_result = execute_slash_command(
        "/pack refresh",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    pack_install_result = execute_slash_command(
        '/pack install "./demo pack" --project --attach --priority 10',
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    pack_update_result = execute_slash_command(
        "/pack update ./demo-pack --project",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    pack_add_source_result = execute_slash_command(
        "/pack add-source https://example.com/packs.git",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    mcp_status_result = execute_slash_command(
        "/mcp",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    mcp_detail_result = execute_slash_command(
        "/mcp status docs",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    mcp_action_result = execute_slash_command(
        "/mcp reconnect docs",
        CommandContext(current_model="gpt-5.2", working_dir="/repo"),
    )
    plan_on_result = execute_slash_command(
        "/plan on",
        CommandContext(current_model="gpt-5.2", working_dir="/repo", plan_mode=False),
    )
    plan_status_result = execute_slash_command(
        "/plan status",
        CommandContext(current_model="gpt-5.2", working_dir="/repo", plan_mode=True),
    )
    plan_off_result = execute_slash_command(
        "/plan off",
        CommandContext(current_model="gpt-5.2", working_dir="/repo", plan_mode=True),
    )

    assert list_result is not None
    assert list_result.kind == "list_conversations"
    assert list_result.list_limit == 7

    assert resume_result is not None
    assert resume_result.kind == "resume_conversation"
    assert resume_result.resume_target == "architecture-notes"

    assert search_result is not None
    assert search_result.kind == "search_conversations"
    assert search_result.search_query == "renderer"

    assert rename_result is not None
    assert rename_result.kind == "rename_conversation"
    assert rename_result.conversation_title == "New title"

    assert slug_result is not None
    assert slug_result.kind == "set_slug"
    assert slug_result.slug_value == "architecture-notes"

    assert rewind_result is not None
    assert rewind_result.kind == "rewind_conversation"
    assert rewind_result.rewind_arg == "4 --undo-files"

    assert spaces_result is not None
    assert spaces_result.kind == "show_spaces"

    assert space_show_result is not None
    assert space_show_result.kind == "show_space"
    assert space_show_result.space_target == "demo"

    assert space_create_result is not None
    assert space_create_result.kind == "create_space"
    assert space_create_result.space_target == "demo"

    assert space_edit_result is not None
    assert space_edit_result.kind == "update_space"
    assert space_edit_result.space_edit_field == "model"
    assert space_edit_result.space_edit_value == "gpt-5.4-mini"

    assert space_refresh_result is not None
    assert space_refresh_result.kind == "refresh_space"
    assert space_refresh_result.space_target == "demo"

    assert space_export_result is not None
    assert space_export_result.kind == "export_space"
    assert space_export_result.space_target == "demo"

    assert space_switch_result is not None
    assert space_switch_result.kind == "set_space"
    assert space_switch_result.space_target == "demo"

    assert space_select_result is not None
    assert space_select_result.kind == "set_space"
    assert space_select_result.space_target == "demo"

    assert space_use_result is not None
    assert space_use_result.kind == "set_space"
    assert space_use_result.space_target == "demo"

    assert space_sources_result is not None
    assert space_sources_result.kind == "show_space_sources"
    assert space_sources_result.space_target == "demo"

    assert space_delete_result is not None
    assert space_delete_result.kind == "delete_space"
    assert space_delete_result.space_target == "demo"

    assert artifacts_result is not None
    assert artifacts_result.kind == "show_artifacts"

    assert artifact_show_result is not None
    assert artifact_show_result.kind == "show_artifact"
    assert artifact_show_result.artifact_fqn == "@core/skill/demo"

    assert artifact_delete_result is not None
    assert artifact_delete_result.kind == "delete_artifact"
    assert artifact_delete_result.artifact_fqn == "@core/skill/demo"

    assert packs_result is not None
    assert packs_result.kind == "show_packs"

    assert pack_show_result is not None
    assert pack_show_result.kind == "show_pack"
    assert pack_show_result.pack_ref == "default/demo"

    assert pack_delete_result is not None
    assert pack_delete_result.kind == "delete_pack"
    assert pack_delete_result.pack_ref == "default/demo"

    assert pack_attach_result is not None
    assert pack_attach_result.kind == "attach_pack"
    assert pack_attach_result.pack_ref == "default/demo"
    assert pack_attach_result.pack_project_scope is False

    assert pack_detach_result is not None
    assert pack_detach_result.kind == "detach_pack"
    assert pack_detach_result.pack_ref == "default/demo"
    assert pack_detach_result.pack_project_scope is False

    assert pack_sources_result is not None
    assert pack_sources_result.kind == "show_pack_sources"

    assert pack_refresh_result is not None
    assert pack_refresh_result.kind == "refresh_pack_sources"

    assert pack_install_result is not None
    assert pack_install_result.kind == "install_pack"
    assert pack_install_result.pack_path == "./demo pack"
    assert pack_install_result.pack_project_scope is True
    assert pack_install_result.pack_attach_after_install is True
    assert pack_install_result.pack_priority == 10

    assert pack_update_result is not None
    assert pack_update_result.kind == "update_pack"
    assert pack_update_result.pack_path == "./demo-pack"
    assert pack_update_result.pack_project_scope is True

    assert pack_add_source_result is not None
    assert pack_add_source_result.kind == "add_pack_source"
    assert pack_add_source_result.pack_source_url == "https://example.com/packs.git"

    assert mcp_status_result is not None
    assert mcp_status_result.kind == "show_mcp_status"

    assert mcp_detail_result is not None
    assert mcp_detail_result.kind == "show_mcp_server_detail"
    assert mcp_detail_result.mcp_server_name == "docs"

    assert mcp_action_result is not None
    assert mcp_action_result.kind == "run_mcp_action"
    assert mcp_action_result.mcp_action == "reconnect"
    assert mcp_action_result.mcp_server_name == "docs"

    assert plan_on_result is not None
    assert plan_on_result.kind == "set_plan_mode"
    assert plan_on_result.plan_mode_enabled is True

    assert plan_status_result is not None
    assert plan_status_result.kind == "show_plan_status"
    assert plan_status_result.plan_mode_enabled is True

    assert plan_off_result is not None
    assert plan_off_result.kind == "set_plan_mode"
    assert plan_off_result.plan_mode_enabled is False


def test_builtin_commands_take_precedence_over_skills() -> None:
    registry = FakeSkillRegistry()
    result = execute_slash_command(
        "/help",
        CommandContext(current_model="gpt-5.2", working_dir="/repo", skill_registry=registry),
    )

    assert result is not None
    assert result.kind == "show_help"
    assert registry.resolve_calls == 0


def test_execute_skills_command_reloads_registry_and_returns_entries() -> None:
    registry = FakeSkillRegistry()
    result = execute_slash_command(
        "/skills",
        CommandContext(
            current_model="gpt-5.2", working_dir="/repo", skill_registry=registry, artifact_registry=object()
        ),
    )

    assert result is not None
    assert result.kind == "show_skills"
    assert registry.reload_calls == ["/repo"]
    assert [entry.display_name for entry in result.skill_entries] == ["deploy-check"]
    assert result.skill_entries[0].accepts_args is True
    assert result.skill_warnings == ("warning one",)


def test_execute_custom_skill_returns_forward_prompt() -> None:
    registry = FakeSkillRegistry()
    result = execute_slash_command(
        "/deploy-check staging",
        CommandContext(current_model="gpt-5.2", working_dir="/repo", skill_registry=registry),
    )

    assert result is not None
    assert result.kind == "forward_prompt"
    assert result.forward_prompt == "Run the deployment checklist against staging."


def test_tools_and_skills_markdown_helpers() -> None:
    tools_markdown = build_tools_markdown(["read", "grep"])
    skills_result = execute_slash_command(
        "/skills",
        CommandContext(current_model="gpt-5.2", working_dir="/repo", skill_registry=FakeSkillRegistry()),
    )

    assert tools_markdown == "## Tools\n\n- `read`\n- `grep`"
    assert skills_result is not None
    skills_markdown = build_skills_markdown(skills_result.skill_entries, skills_result.skill_warnings)
    assert "`/deploy-check`" in skills_markdown
    assert "Warnings" in skills_markdown
