from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from .plan import parse_plan_command
from .skills import SkillRegistry

CommandKind = Literal[
    "exit",
    "new_conversation",
    "list_conversations",
    "resume_conversation",
    "delete_conversation",
    "compact_conversation",
    "rewind_conversation",
    "search_conversations",
    "show_help",
    "show_message",
    "show_spaces",
    "show_artifacts",
    "show_artifact",
    "show_packs",
    "show_pack",
    "show_pack_sources",
    "refresh_pack_sources",
    "add_pack_source",
    "install_pack",
    "update_pack",
    "attach_pack",
    "detach_pack",
    "show_space",
    "show_space_sources",
    "create_space",
    "update_space",
    "refresh_space",
    "export_space",
    "show_tools",
    "show_usage",
    "show_mcp_status",
    "show_mcp_server_detail",
    "run_mcp_action",
    "show_plan_status",
    "set_plan_mode",
    "show_skills",
    "show_model",
    "show_slug",
    "set_model",
    "set_space",
    "delete_space",
    "set_slug",
    "rename_conversation",
    "delete_artifact",
    "delete_pack",
    "forward_prompt",
]


@dataclass(frozen=True)
class ParsedSlashCommand:
    raw: str
    name: str
    arg: str = ""


@dataclass(frozen=True)
class SkillDescription:
    display_name: str
    description: str
    source: str
    accepts_args: bool = False


@dataclass(frozen=True)
class SearchedDirectory:
    path: str
    source: str
    skill_count: int
    exists: bool


@dataclass
class CommandContext:
    current_model: str
    working_dir: str
    available_tools: Sequence[str] = field(default_factory=tuple)
    tool_registry: Any | None = None
    skill_registry: SkillRegistry | None = None
    artifact_registry: Any | None = None
    plan_mode: bool = False


@dataclass(frozen=True)
class CommandResult:
    kind: CommandKind
    command: ParsedSlashCommand
    message: str | None = None
    conversation_type: str | None = None
    conversation_title: str | None = None
    resume_target: str | None = None
    delete_target: str | None = None
    model_name: str | None = None
    slug_value: str | None = None
    search_query: str | None = None
    list_limit: int | None = None
    rewind_arg: str | None = None
    space_target: str | None = None
    space_edit_field: str | None = None
    space_edit_value: str | None = None
    artifact_fqn: str | None = None
    pack_ref: str | None = None
    pack_path: str | None = None
    pack_project_scope: bool = False
    pack_attach_after_install: bool = False
    pack_priority: int | None = None
    pack_source_url: str | None = None
    mcp_server_name: str | None = None
    mcp_action: str | None = None
    plan_mode_enabled: bool | None = None
    tool_names: tuple[str, ...] = ()
    skill_entries: tuple[SkillDescription, ...] = ()
    skill_warnings: tuple[str, ...] = ()
    searched_dirs: tuple[SearchedDirectory, ...] = ()
    forward_prompt: str | None = None
    echo_user: bool = True


COMMON_COMMANDS: tuple[str, ...] = (
    "/quit",
    "/exit",
    "/new",
    "/help",
    "/tools",
    "/model",
    "/usage",
    "/skills",
    "/reload-skills",
    "/list",
    "/last",
    "/resume",
    "/search",
    "/rename",
    "/slug",
    "/spaces",
    "/space",
    "/artifacts",
    "/artifact",
    "/packs",
    "/pack",
    "/delete",
    "/rewind",
    "/compact",
    "/mcp",
    "/plan",
)

PARITY_TIER_1_COMMANDS: tuple[str, ...] = (
    "/help",
    "/new",
    "/tools",
    "/usage",
    "/model",
    "/skills",
    "/reload-skills",
)

PARITY_TIER_2_COMMANDS: tuple[str, ...] = (
    "/list",
    "/last",
    "/resume",
    "/search",
    "/rename",
    "/slug",
    "/delete",
    "/rewind",
    "/compact",
    "/spaces",
    "/space",
    "/artifacts",
    "/artifact",
    "/packs",
    "/pack",
    "/mcp",
    "/plan",
)


def parse_slash_command(prompt: str) -> ParsedSlashCommand | None:
    stripped = prompt.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped.split(maxsplit=1)
    return ParsedSlashCommand(raw=prompt, name=parts[0].lower(), arg=parts[1].strip() if len(parts) > 1 else "")


def execute_slash_command(prompt: str, context: CommandContext) -> CommandResult | None:
    parsed = parse_slash_command(prompt)
    if parsed is None:
        return None

    if parsed.name in {"/quit", "/exit"}:
        return CommandResult(kind="exit", command=parsed, echo_user=False)

    if parsed.name == "/new":
        conv_type, title = _parse_new_conversation(parsed.arg)
        return CommandResult(
            kind="new_conversation",
            command=parsed,
            conversation_type=conv_type,
            conversation_title=title,
            echo_user=False,
        )

    if parsed.name == "/help":
        return CommandResult(kind="show_help", command=parsed)

    if parsed.name == "/compact":
        return CommandResult(kind="compact_conversation", command=parsed, echo_user=False)

    if parsed.name == "/list":
        limit = 20
        if parsed.arg.isdigit():
            limit = max(1, int(parsed.arg))
        return CommandResult(kind="list_conversations", command=parsed, list_limit=limit)

    if parsed.name == "/last":
        return CommandResult(kind="resume_conversation", command=parsed, resume_target=None, echo_user=False)

    if parsed.name == "/resume":
        if not parsed.arg:
            return CommandResult(kind="list_conversations", command=parsed, list_limit=20)
        return CommandResult(
            kind="resume_conversation",
            command=parsed,
            resume_target=parsed.arg,
            echo_user=False,
        )

    if parsed.name == "/search":
        if not parsed.arg:
            return CommandResult(
                kind="show_message",
                command=parsed,
                message="Usage: `/search <query>`",
            )
        return CommandResult(kind="search_conversations", command=parsed, search_query=parsed.arg)

    if parsed.name == "/spaces":
        return CommandResult(kind="show_spaces", command=parsed)

    if parsed.name == "/space":
        parts = parsed.arg.split(maxsplit=1)
        sub = parts[0].lower() if parts else ""
        target = parts[1].strip() if len(parts) == 2 else ""
        if sub in {"", "list"}:
            return CommandResult(kind="show_spaces", command=parsed)
        if sub == "create":
            if not target:
                return CommandResult(
                    kind="show_message",
                    command=parsed,
                    message="Usage: `/space create <name>`",
                )
            return CommandResult(kind="create_space", command=parsed, space_target=target, echo_user=False)
        if sub == "edit":
            if not target:
                return CommandResult(
                    kind="show_message",
                    command=parsed,
                    message=(
                        "Usage: `/space edit instructions <text>`\n"
                        "`/space edit model <model-name>`\n"
                        "`/space edit name <new-name>`"
                    ),
                )
            edit_parts = target.split(maxsplit=1)
            edit_field = edit_parts[0].lower()
            edit_value = edit_parts[1] if len(edit_parts) == 2 else ""
            if edit_field not in {"instructions", "model", "name"}:
                return CommandResult(
                    kind="show_message",
                    command=parsed,
                    message="Unknown field. Use: `instructions`, `model`, or `name`.",
                )
            if edit_field == "name" and not edit_value.strip():
                return CommandResult(
                    kind="show_message",
                    command=parsed,
                    message="Usage: `/space edit name <new-name>`",
                )
            return CommandResult(
                kind="update_space",
                command=parsed,
                space_edit_field=edit_field,
                space_edit_value=edit_value,
                echo_user=False,
            )
        if sub == "refresh":
            return CommandResult(
                kind="refresh_space",
                command=parsed,
                space_target=target or None,
                echo_user=False,
            )
        if sub == "export":
            return CommandResult(
                kind="export_space",
                command=parsed,
                space_target=target or None,
                echo_user=False,
            )
        if sub == "show":
            return CommandResult(kind="show_space", command=parsed, space_target=target)
        if sub in {"switch", "select", "use"}:
            if not target:
                return CommandResult(
                    kind="show_message",
                    command=parsed,
                    message="Usage: `/space switch <name|id>`",
                )
            return CommandResult(kind="set_space", command=parsed, space_target=target, echo_user=False)
        if sub == "clear":
            return CommandResult(kind="set_space", command=parsed, space_target="", echo_user=False)
        if sub == "sources":
            return CommandResult(kind="show_space_sources", command=parsed, space_target=target or None)
        if sub == "delete":
            delete_target = target
            if delete_target.startswith("--confirm "):
                delete_target = delete_target[len("--confirm ") :].strip()
            if not delete_target:
                return CommandResult(
                    kind="show_message",
                    command=parsed,
                    message="Usage: `/space delete <name|id>`",
                )
            return CommandResult(kind="delete_space", command=parsed, space_target=delete_target, echo_user=False)
        return CommandResult(
            kind="show_message",
            command=parsed,
            message=("Usage: `/space [list|create|edit|refresh|export|show|switch|select|use|clear|sources|delete]`"),
        )

    if parsed.name == "/tools":
        return CommandResult(kind="show_tools", command=parsed, tool_names=_tool_names(context))

    if parsed.name == "/model":
        if not parsed.arg:
            return CommandResult(kind="show_model", command=parsed, model_name=context.current_model)
        return CommandResult(kind="set_model", command=parsed, model_name=parsed.arg)

    if parsed.name == "/usage":
        return CommandResult(kind="show_usage", command=parsed)

    if parsed.name == "/plan":
        subcommand, inline_prompt = parse_plan_command(parsed.raw)
        if subcommand in {"on", "start"}:
            return CommandResult(kind="set_plan_mode", command=parsed, plan_mode_enabled=True, echo_user=False)
        if subcommand == "status":
            return CommandResult(kind="show_plan_status", command=parsed, plan_mode_enabled=context.plan_mode)
        if subcommand == "off":
            return CommandResult(kind="set_plan_mode", command=parsed, plan_mode_enabled=False, echo_user=False)
        if inline_prompt:
            return None
        return CommandResult(
            kind="show_message",
            command=parsed,
            message="Supported plan commands here: `/plan on`, `/plan off`, `/plan status`, or `/plan <prompt>`.",
        )

    if parsed.name == "/mcp":
        parts = parsed.arg.split(maxsplit=1) if parsed.arg else []
        subcommand = parts[0].lower() if parts else ""
        target = parts[1].strip() if len(parts) == 2 else ""
        if not subcommand:
            return CommandResult(kind="show_mcp_status", command=parsed)
        if subcommand == "status":
            if not target:
                return CommandResult(kind="show_mcp_status", command=parsed)
            return CommandResult(kind="show_mcp_server_detail", command=parsed, mcp_server_name=target)
        if subcommand in {"connect", "disconnect", "reconnect"}:
            if not target:
                return CommandResult(
                    kind="show_message",
                    command=parsed,
                    message=f"Usage: `/mcp {subcommand} <name>`",
                )
            return CommandResult(
                kind="run_mcp_action",
                command=parsed,
                mcp_action=subcommand,
                mcp_server_name=target,
                echo_user=False,
            )
        return CommandResult(
            kind="show_message",
            command=parsed,
            message="Usage: `/mcp [status <name>|connect|disconnect|reconnect <name>]`",
        )

    if parsed.name in {"/skills", "/reload-skills"}:
        return _skills_result(parsed, context)

    if parsed.name == "/delete":
        target = parsed.arg.strip()
        if target.startswith("--confirm "):
            target = target[len("--confirm ") :].strip()
        if not target:
            return CommandResult(
                kind="show_message",
                command=parsed,
                message="Usage: `/delete <number|slug|id>`",
            )
        return CommandResult(
            kind="delete_conversation",
            command=parsed,
            delete_target=target,
            echo_user=False,
        )

    if parsed.name == "/rewind":
        return CommandResult(
            kind="rewind_conversation",
            command=parsed,
            rewind_arg=parsed.arg,
            echo_user=False,
        )

    if parsed.name == "/rename":
        if not parsed.arg:
            return CommandResult(
                kind="show_message",
                command=parsed,
                message="Usage: `/rename <title>` or `/rename <number|slug|id> <title>`",
            )
        return CommandResult(
            kind="rename_conversation",
            command=parsed,
            conversation_title=parsed.arg,
        )

    if parsed.name == "/slug":
        if not parsed.arg:
            return CommandResult(kind="show_slug", command=parsed)
        return CommandResult(kind="set_slug", command=parsed, slug_value=parsed.arg.strip().lower())

    if parsed.name == "/space":
        arg = parsed.arg.strip()
        if not arg or arg == "list":
            return CommandResult(kind="show_spaces", command=parsed)
        parts = arg.split(maxsplit=1)
        subcommand = parts[0].lower()
        target = parts[1].strip() if len(parts) > 1 else ""
        if subcommand == "create":
            return CommandResult(kind="create_space", command=parsed, space_target=target)
        if subcommand == "edit":
            edit_parts = target.split(maxsplit=1)
            edit_field = edit_parts[0].lower() if edit_parts else ""
            edit_value = edit_parts[1] if len(edit_parts) > 1 else ""
            return CommandResult(
                kind="update_space",
                command=parsed,
                space_edit_field=edit_field,
                space_edit_value=edit_value,
            )
        if subcommand == "refresh":
            return CommandResult(kind="refresh_space", command=parsed, space_target=target or None)
        if subcommand == "export":
            return CommandResult(kind="export_space", command=parsed, space_target=target or None)
        if subcommand == "show":
            return CommandResult(kind="show_space", command=parsed, space_target=target)
        if subcommand in {"switch", "select", "use"}:
            return CommandResult(kind="set_space", command=parsed, space_target=target)
        if subcommand == "clear":
            return CommandResult(kind="set_space", command=parsed, space_target="")
        return CommandResult(kind="show_space", command=parsed, space_target=arg)

    if parsed.name in {"/artifact", "/artifacts"}:
        parts = prompt.split(maxsplit=2)
        subcommand = parts[1].lower() if len(parts) >= 2 else ""
        if parsed.name == "/artifacts":
            subcommand = "list"
        if subcommand in {"", "list"}:
            return CommandResult(kind="show_artifacts", command=parsed, echo_user=True)
        if subcommand == "show":
            target = parts[2].strip() if len(parts) >= 3 else ""
            return CommandResult(kind="show_artifact", command=parsed, artifact_fqn=target)
        if subcommand == "delete":
            target = parts[2].strip() if len(parts) >= 3 else ""
            return CommandResult(kind="delete_artifact", command=parsed, artifact_fqn=target, echo_user=False)
        if subcommand == "import":
            return CommandResult(
                kind="show_message",
                command=parsed,
                message="Use the CLI: `aroom artifact import --skills|--instructions|--all`",
            )
        if subcommand == "create":
            return CommandResult(
                kind="show_message",
                command=parsed,
                message="Use the CLI: `aroom artifact create <type> <name>`",
            )
        return CommandResult(
            kind="show_message",
            command=parsed,
            message="Usage: `/artifact {list,show,delete,import,create}`",
        )

    if parsed.name in {"/pack", "/packs"}:
        parts = prompt.split(maxsplit=2)
        subcommand = parts[1].lower() if len(parts) >= 2 else ""
        if parsed.name == "/packs":
            subcommand = "list"
        if subcommand in {"", "list"}:
            return CommandResult(kind="show_packs", command=parsed)
        if subcommand == "show":
            target = parts[2].strip() if len(parts) >= 3 else ""
            return CommandResult(kind="show_pack", command=parsed, pack_ref=target)
        if subcommand in {"remove", "delete"}:
            target = parts[2].strip() if len(parts) >= 3 else ""
            return CommandResult(kind="delete_pack", command=parsed, pack_ref=target, echo_user=False)
        if subcommand == "sources":
            return CommandResult(kind="show_pack_sources", command=parsed)
        if subcommand in {"attach", "detach"}:
            rest = parts[2].strip() if len(parts) >= 3 else ""
            if "--project" in rest:
                return None
            project_scope = "--project" in rest
            target = rest.replace("--project", "").strip()
            if not target:
                return CommandResult(
                    kind="show_message",
                    command=parsed,
                    message=f"Usage: `/pack {subcommand} <namespace/name> [--project]`",
                )
            return CommandResult(
                kind="attach_pack" if subcommand == "attach" else "detach_pack",
                command=parsed,
                pack_ref=target,
                pack_project_scope=project_scope,
                echo_user=False,
            )
        if subcommand == "refresh":
            return CommandResult(kind="refresh_pack_sources", command=parsed, echo_user=False)
        if subcommand == "add-source":
            target = parts[2].strip() if len(parts) >= 3 else ""
            return CommandResult(kind="add_pack_source", command=parsed, pack_source_url=target, echo_user=False)
        if subcommand in {"install", "update"}:
            rest = parts[2].strip() if len(parts) >= 3 else ""
            parsed_pack_op = _parse_pack_path_flags(subcommand, rest)
            if isinstance(parsed_pack_op, str):
                return CommandResult(kind="show_message", command=parsed, message=parsed_pack_op)
            return CommandResult(
                kind="install_pack" if subcommand == "install" else "update_pack",
                command=parsed,
                pack_path=parsed_pack_op["path"],
                pack_project_scope=parsed_pack_op["project_scope"],
                pack_attach_after_install=parsed_pack_op["attach_after_install"],
                pack_priority=parsed_pack_op["priority"],
                echo_user=False,
            )
        return CommandResult(
            kind="show_message",
            command=parsed,
            message="Usage: `/pack {list,show,remove,sources,install,update,attach,detach,refresh,add-source}`",
        )

    if context.skill_registry is not None:
        is_skill, expanded = context.skill_registry.resolve_input(prompt)
        if is_skill:
            return CommandResult(
                kind="forward_prompt",
                command=parsed,
                forward_prompt=expanded,
                echo_user=False,
            )

    return None


def build_help_markdown() -> str:
    return (
        "## Slash Commands\n\n"
        "- `/new` start a fresh conversation\n"
        "- `/new note <title>` start a note\n"
        "- `/new doc <title>` start a document\n"
        "- `/last` resume the most recent conversation\n"
        "- `/list [N]` list recent conversations\n"
        "- `/resume <number|slug|id>` open a conversation\n"
        "- `/search <query>` search conversations\n"
        "- `/delete <number|slug|id>` delete a conversation\n"
        "- `/rewind <position> [--undo-files]` rewind the active conversation\n"
        "- `/compact` summarize the current thread to free context\n"
        "- `/space` lists spaces, `/space create <name>` adds one, `/space edit ...` updates the active one,\n"
        "  `/space refresh` reloads it from disk, `/space export` dumps YAML,\n"
        "  and `/space switch <name>` activates one\n"
        "- `/artifact` list artifacts and `/artifact show <fqn>` inspect one\n"
        "- `/pack` lists packs; `/pack show <namespace/name>` inspects one; `/pack install <path>` or\n"
        "  `/pack update <path>` manage local packs; `/pack refresh` updates configured sources;\n"
        "  `/pack add-source <url>` adds a source\n"
        "- `/mcp` shows MCP status; `/mcp status <name>` and\n"
        "  `/mcp connect|disconnect|reconnect <name>` manage a server\n"
        "- `/plan on|off|status` controls planning mode; inline `/plan <prompt>` remains CLI/Textual-local for now\n"
        "- `/upload <path>` uploads a local file (CLI/Textual only)\n"
        "- `/verbose` cycles tool detail level (CLI/Textual only)\n"
        "- `/detail` replays the last turn's tool activity (CLI/Textual only)\n"
        "- `/rename <title>` rename the current conversation\n"
        "- `/slug [name]` show or set the current slug\n"
        "- `/conventions` show loaded project conventions\n"
        "- `/tools` list the currently available tools\n"
        "- `/skills` list available slash skills\n"
        "- `/reload-skills` reload skill files from disk\n"
        "- `/model [NAME]` show or change the active model\n"
        "- `/usage` show token usage totals\n"
        "- `/quit` or `/exit` leave the app\n\n"
        "Custom skills also work here as `/skill-name ...`."
    )


def build_tools_markdown(tool_names: Sequence[str]) -> str:
    if not tool_names:
        return "No tools are currently available."
    return "## Tools\n\n" + "\n".join(f"- `{name}`" for name in tool_names)


def build_skills_markdown(
    entries: Sequence[SkillDescription],
    warnings: Sequence[str],
    *,
    has_registry: bool = True,
) -> str:
    if not has_registry:
        return "No skill registry is attached to this session."
    lines = ["## Skills"]
    if entries:
        for entry in entries:
            description = entry.description or "No description."
            lines.append(f"- `/{entry.display_name}` — {description} ({entry.source})")
    else:
        lines.append("No skills loaded. Add YAML files under `~/.anteroom/skills/` or a project `.anteroom/skills/`.")
    if warnings:
        lines.append("")
        lines.append("### Warnings")
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines)


def _parse_new_conversation(arg: str) -> tuple[str, str]:
    if not arg:
        return "chat", "New Conversation"
    parts = arg.split(maxsplit=1)
    first = parts[0].lower()
    if first in {"note", "doc", "document"}:
        conv_type = "document" if first in {"doc", "document"} else "note"
        title = parts[1].strip() if len(parts) > 1 and parts[1].strip() else f"New {conv_type.title()}"
        return conv_type, title
    return "chat", "New Conversation"


def _tool_names(context: CommandContext) -> tuple[str, ...]:
    names = set(context.available_tools)
    if context.tool_registry is not None:
        names.update(context.tool_registry.list_tools())
    return tuple(sorted(names))


def _parse_pack_path_flags(subcommand: str, rest: str) -> dict[str, Any] | str:
    try:
        argv = shlex.split(rest)
    except ValueError as exc:
        return str(exc)

    if not argv:
        return f"Usage: `/pack {subcommand} <path>`"

    project_scope = False
    attach_after_install = False
    priority = 50
    path_tokens: list[str] = []
    idx = 0
    while idx < len(argv):
        token = argv[idx]
        if token == "--project":
            project_scope = True
            idx += 1
            continue
        if token == "--attach" and subcommand == "install":
            attach_after_install = True
            idx += 1
            continue
        if token == "--priority" and subcommand == "install":
            if idx + 1 >= len(argv):
                return "Usage: `/pack install <path> [--project] [--attach] [--priority N]`"
            try:
                priority = int(argv[idx + 1])
            except ValueError:
                return "Priority must be an integer."
            idx += 2
            continue
        path_tokens.append(token)
        idx += 1

    if len(path_tokens) != 1:
        if subcommand == "install":
            return "Usage: `/pack install <path> [--project] [--attach] [--priority N]`"
        return "Usage: `/pack update <path> [--project]`"

    return {
        "path": path_tokens[0],
        "project_scope": project_scope,
        "attach_after_install": attach_after_install,
        "priority": priority,
    }


def _skills_result(parsed: ParsedSlashCommand, context: CommandContext) -> CommandResult:
    if context.skill_registry is None:
        return CommandResult(kind="show_skills", command=parsed)

    context.skill_registry.reload(context.working_dir)
    if context.artifact_registry is not None:
        context.skill_registry.load_from_artifacts(context.artifact_registry)

    entries: list[SkillDescription] = []
    for display_name, description in context.skill_registry.get_skill_descriptions():
        skill = context.skill_registry.get(display_name)
        entries.append(
            SkillDescription(
                display_name=display_name,
                description=description or "",
                source=skill.source if skill else "unknown",
                accepts_args=bool(skill and "{args}" in skill.prompt),
            )
        )

    searched_dirs = tuple(
        SearchedDirectory(
            path=item.path,
            source=item.source,
            skill_count=item.skill_count,
            exists=item.exists,
        )
        for item in context.skill_registry.searched_dirs
    )
    return CommandResult(
        kind="show_skills",
        command=parsed,
        skill_entries=tuple(entries),
        skill_warnings=tuple(context.skill_registry.load_warnings),
        searched_dirs=searched_dirs,
    )
