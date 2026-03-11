from __future__ import annotations

from anteroom.cli.commands import (
    COMMON_COMMANDS,
    PARITY_TIER_1_COMMANDS,
    PARITY_TIER_2_COMMANDS,
    CommandContext,
    execute_slash_command,
)


class _Skill:
    source = "project"
    prompt = "Run checks with {args}."


class _SkillRegistry:
    load_warnings = []
    searched_dirs = []

    def reload(self, working_dir: str | None = None):
        return None

    def load_from_artifacts(self, artifact_registry):
        return None

    def get_skill_descriptions(self):
        return [("deploy-check", "Run the deployment checklist")]

    def get(self, name: str):
        return _Skill()

    def resolve_input(self, prompt: str):
        if prompt.startswith("/deploy-check"):
            return True, "expanded"
        return False, prompt


def _context() -> CommandContext:
    return CommandContext(
        current_model="gpt-5.2",
        working_dir="/repo",
        available_tools=("read_file", "bash"),
        skill_registry=_SkillRegistry(),
    )


def test_parity_tiers_are_known_common_commands() -> None:
    common = set(COMMON_COMMANDS)
    assert set(PARITY_TIER_1_COMMANDS).issubset(common)
    assert set(PARITY_TIER_2_COMMANDS).issubset(common)
    assert set(PARITY_TIER_1_COMMANDS).isdisjoint(set(PARITY_TIER_2_COMMANDS))


def test_parity_tier_commands_have_representative_parses() -> None:
    examples = {
        "/help": "/help",
        "/new": "/new note Architecture Notes",
        "/tools": "/tools",
        "/usage": "/usage",
        "/model": "/model gpt-5.4-mini",
        "/skills": "/skills",
        "/reload-skills": "/reload-skills",
        "/list": "/list 5",
        "/last": "/last",
        "/resume": "/resume feature-slug",
        "/search": "/search renderer",
        "/rename": "/rename Updated title",
        "/slug": "/slug feature-slug",
        "/delete": "/delete feature-slug",
        "/rewind": "/rewind 3 --undo-files",
        "/compact": "/compact",
        "/spaces": "/spaces",
        "/space": "/space switch demo",
        "/artifacts": "/artifacts",
        "/artifact": "/artifact show @core/skill/demo",
        "/packs": "/packs",
        "/pack": "/pack show demo/focus-fold",
        "/mcp": "/mcp status docs",
        "/plan": "/plan status",
    }

    for command in PARITY_TIER_1_COMMANDS + PARITY_TIER_2_COMMANDS:
        result = execute_slash_command(examples[command], _context())
        assert result is not None, command


def test_space_aliases_remain_supported() -> None:
    create_result = execute_slash_command("/space create demo", _context())
    edit_result = execute_slash_command("/space edit model gpt-5.4-mini", _context())
    refresh_result = execute_slash_command("/space refresh demo", _context())
    export_result = execute_slash_command("/space export demo", _context())
    select_result = execute_slash_command("/space select demo", _context())
    use_result = execute_slash_command("/space use demo", _context())
    sources_result = execute_slash_command("/space sources demo", _context())
    delete_result = execute_slash_command("/space delete demo", _context())

    assert create_result is not None
    assert create_result.kind == "create_space"
    assert create_result.space_target == "demo"

    assert edit_result is not None
    assert edit_result.kind == "update_space"
    assert edit_result.space_edit_field == "model"
    assert edit_result.space_edit_value == "gpt-5.4-mini"

    assert refresh_result is not None
    assert refresh_result.kind == "refresh_space"
    assert refresh_result.space_target == "demo"

    assert export_result is not None
    assert export_result.kind == "export_space"
    assert export_result.space_target == "demo"

    assert select_result is not None
    assert select_result.kind == "set_space"
    assert select_result.space_target == "demo"

    assert use_result is not None
    assert use_result.kind == "set_space"
    assert use_result.space_target == "demo"

    assert sources_result is not None
    assert sources_result.kind == "show_space_sources"
    assert sources_result.space_target == "demo"

    assert delete_result is not None
    assert delete_result.kind == "delete_space"
    assert delete_result.space_target == "demo"


def test_custom_skills_remain_in_shared_parity_surface() -> None:
    result = execute_slash_command("/deploy-check staging", _context())

    assert result is not None
    assert result.kind == "forward_prompt"
    assert result.forward_prompt == "expanded"


def test_pack_scope_commands_remain_supported() -> None:
    attach_result = execute_slash_command("/pack attach default/demo", _context())
    detach_result = execute_slash_command("/pack detach default/demo", _context())
    refresh_result = execute_slash_command("/pack refresh", _context())
    add_source_result = execute_slash_command("/pack add-source https://example.com/packs.git", _context())
    install_result = execute_slash_command("/pack install ./demo-pack --project --attach --priority 10", _context())
    update_result = execute_slash_command("/pack update ./demo-pack --project", _context())

    assert attach_result is not None
    assert attach_result.kind == "attach_pack"
    assert attach_result.pack_ref == "default/demo"
    assert attach_result.pack_project_scope is False

    assert detach_result is not None
    assert detach_result.kind == "detach_pack"
    assert detach_result.pack_ref == "default/demo"
    assert detach_result.pack_project_scope is False

    assert refresh_result is not None
    assert refresh_result.kind == "refresh_pack_sources"

    assert add_source_result is not None
    assert add_source_result.kind == "add_pack_source"
    assert add_source_result.pack_source_url == "https://example.com/packs.git"

    assert install_result is not None
    assert install_result.kind == "install_pack"
    assert install_result.pack_path == "./demo-pack"
    assert install_result.pack_project_scope is True
    assert install_result.pack_attach_after_install is True
    assert install_result.pack_priority == 10

    assert update_result is not None
    assert update_result.kind == "update_pack"
    assert update_result.pack_path == "./demo-pack"
    assert update_result.pack_project_scope is True


def test_mcp_commands_remain_supported() -> None:
    status_result = execute_slash_command("/mcp", _context())
    detail_result = execute_slash_command("/mcp status docs", _context())
    action_result = execute_slash_command("/mcp reconnect docs", _context())

    assert status_result is not None
    assert status_result.kind == "show_mcp_status"

    assert detail_result is not None
    assert detail_result.kind == "show_mcp_server_detail"
    assert detail_result.mcp_server_name == "docs"

    assert action_result is not None
    assert action_result.kind == "run_mcp_action"
    assert action_result.mcp_action == "reconnect"
    assert action_result.mcp_server_name == "docs"


def test_plan_mode_commands_remain_supported() -> None:
    on_result = execute_slash_command("/plan on", _context())
    status_result = execute_slash_command("/plan status", CommandContext(
        current_model="gpt-5.2",
        working_dir="/repo",
        available_tools=("read_file", "bash"),
        skill_registry=_SkillRegistry(),
        plan_mode=True,
    ))
    off_result = execute_slash_command("/plan off", CommandContext(
        current_model="gpt-5.2",
        working_dir="/repo",
        available_tools=("read_file", "bash"),
        skill_registry=_SkillRegistry(),
        plan_mode=True,
    ))

    assert on_result is not None
    assert on_result.kind == "set_plan_mode"
    assert on_result.plan_mode_enabled is True

    assert status_result is not None
    assert status_result.kind == "show_plan_status"
    assert status_result.plan_mode_enabled is True

    assert off_result is not None
    assert off_result.kind == "set_plan_mode"
    assert off_result.plan_mode_enabled is False
