"""Interactive full-screen config editor TUI.

Built with prompt_toolkit Application (same pattern as the resume picker).
No new dependencies — uses prompt_toolkit + Rich, both already required.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import renderer

# ---------------------------------------------------------------------------
# Layer colors for prompt_toolkit style fragments
# ---------------------------------------------------------------------------

LAYER_STYLE_MAP: dict[str, str] = {
    "default": "class:layer.default",
    "team": "class:layer.team",
    "team (enforced)": "class:layer.enforced",
    "pack": "class:layer.pack",
    "personal": "class:layer.personal",
    "space": "class:layer.space",
    "project": "class:layer.project",
    "env var": "class:layer.env",
}

# Rich markup colors (reused for /config list, /config get fallback)
LAYER_COLORS: dict[str, str] = {
    "default": "dim",
    "team": "bright_red",
    "team (enforced)": "bold red",
    "pack": "magenta",
    "personal": "bright_blue",
    "space": "bright_green",
    "project": "bright_yellow",
    "env var": "bright_cyan",
}

# Layer order for display (lowest to highest precedence)
_DISPLAY_LAYERS = ("default", "team", "pack", "personal", "space", "project", "env var")


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class _VisibleItem:
    """A single row in the field list — either a section header or a field."""

    kind: str  # "section" or "field"
    label: str  # display text
    dot_path: str = ""  # only for fields
    source: str = ""  # source layer
    is_enforced: bool = False
    is_modified: bool = False
    field_type: str = "str"


@dataclass
class ConfigTuiState:
    """Mutable state for the config TUI."""

    # All fields from config_editor
    all_fields: list[Any] = field(default_factory=list)  # ConfigFieldValue list
    source_map: dict[str, str] = field(default_factory=dict)
    enforced_fields: list[str] = field(default_factory=list)
    layer_raws: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Navigation
    selected_idx: int = 0
    collapsed_sections: set[str] = field(default_factory=set)
    search_filter: str = ""

    # Editing
    pending_changes: dict[str, Any] = field(default_factory=dict)
    active_scope: str = "personal"
    available_scopes: list[str] = field(default_factory=lambda: ["personal"])

    # Flash message
    message: str = ""
    message_time: float = 0.0

    # Cached visible items
    _visible: list[_VisibleItem] = field(default_factory=list)

    def set_message(self, msg: str) -> None:
        self.message = msg
        self.message_time = time.monotonic()

    @property
    def flash(self) -> str:
        if self.message and (time.monotonic() - self.message_time) < 4.0:
            return self.message
        return ""


# ---------------------------------------------------------------------------
# Pure rendering functions (testable without Application)
# ---------------------------------------------------------------------------


def build_visible_items(
    state: ConfigTuiState,
    config: Any,
) -> list[_VisibleItem]:
    """Build the list of visible items from state, respecting filters and collapsed sections."""
    from ..services.config_editor import list_settable_fields

    fields_info = list_settable_fields()
    search = state.search_filter.lower()

    # Group by top-level section
    sections: dict[str, list[_VisibleItem]] = {}
    for fi in fields_info:
        section = fi.dot_path.split(".")[0]
        if search and search not in fi.dot_path.lower():
            continue

        src = state.source_map.get(fi.dot_path, "default")
        if fi.dot_path in state.enforced_fields:
            src = "team (enforced)"

        item = _VisibleItem(
            kind="field",
            label=fi.dot_path,
            dot_path=fi.dot_path,
            source=src,
            is_enforced=fi.dot_path in state.enforced_fields,
            is_modified=fi.dot_path in state.pending_changes,
            field_type=fi.field_type,
        )
        sections.setdefault(section, []).append(item)

    # Build flat list with section headers
    items: list[_VisibleItem] = []
    for section_name in sorted(sections.keys()):
        collapsed = section_name in state.collapsed_sections
        arrow = "\u25b8" if collapsed else "\u25be"  # ▸ or ▾
        items.append(_VisibleItem(kind="section", label=f"{arrow} {section_name.upper()}"))
        if not collapsed:
            items.extend(sections[section_name])

    state._visible = items
    return items


def render_list_fragments(
    state: ConfigTuiState,
    visible: list[_VisibleItem],
    height: int = 30,
) -> list[tuple[str, str]]:
    """Render the left panel as FormattedText fragments."""
    fragments: list[tuple[str, str]] = []

    # Compute scroll window
    start = 0
    if state.selected_idx >= height:
        start = state.selected_idx - height + 1

    for i, item in enumerate(visible):
        if i < start:
            continue
        if i >= start + height:
            break

        is_sel = i == state.selected_idx

        if item.kind == "section":
            if is_sel:
                fragments.append(("class:list.selected", f"  {item.label}"))
                fragments.append(("class:list.selected", "\n"))
            else:
                fragments.append(("class:section", f"  {item.label}"))
                fragments.append(("class:section", "\n"))
        else:
            # Field row
            modified = "*" if item.is_modified else " "
            lock = " \U0001f512" if item.is_enforced else ""  # 🔒
            # Truncate dot_path for display
            path_display = item.dot_path
            if len(path_display) > 28:
                path_display = path_display[:25] + "..."

            source_display = f"[{item.source}]"
            line = f"  {modified} {path_display:<28s} {source_display}{lock}"

            if is_sel:
                fragments.append(("class:list.selected", line))
                fragments.append(("class:list.selected", "\n"))
            else:
                style = LAYER_STYLE_MAP.get(item.source, "")
                fragments.append(("", f"  {modified} {path_display:<28s} "))
                fragments.append((style, source_display))
                if lock:
                    fragments.append(("class:enforced-icon", lock))
                fragments.append(("", "\n"))

    return fragments


def render_detail_fragments(
    state: ConfigTuiState,
    visible: list[_VisibleItem],
    config: Any,
) -> list[tuple[str, str]]:
    """Render the right panel (field detail) as FormattedText fragments."""
    fragments: list[tuple[str, str]] = []

    if not visible or state.selected_idx >= len(visible):
        return [("class:detail.empty", "  No field selected")]

    item = visible[state.selected_idx]

    if item.kind == "section":
        section_name = item.label.split()[-1] if item.label else ""
        fragments.append(("class:detail.title", f"  {section_name} Section\n"))
        fragments.append(("class:detail.hint", "\n  Press Enter to expand/collapse\n"))
        return fragments

    # Field detail
    from ..services.config_editor import get_field

    try:
        fv = get_field(
            config,
            item.dot_path,
            state.source_map,
            state.enforced_fields,
            layer_raws=state.layer_raws,
        )
    except (ValueError, KeyError):
        fragments.append(("class:detail.error", f"  Cannot read {item.dot_path}\n"))
        return fragments

    # Show pending value if modified
    display_value = state.pending_changes.get(item.dot_path, fv.effective_value)
    is_modified = item.dot_path in state.pending_changes

    fragments.append(("class:detail.title", f"  {item.dot_path}\n"))
    fragments.append(("class:detail.sep", "  " + "\u2500" * 36 + "\n"))  # ─

    # Value
    val_style = "class:detail.modified" if is_modified else "class:detail.value"
    val_text = repr(display_value)
    if is_modified:
        val_text += "  (modified)"
    fragments.append(("class:detail.label", "  Value:    "))
    fragments.append((val_style, f"{val_text}\n"))

    # Source
    src_style = LAYER_STYLE_MAP.get(fv.source_layer, "")
    fragments.append(("class:detail.label", "  Source:   "))
    fragments.append((src_style, f"{fv.source_layer}\n"))

    # Type
    if fv.field_info:
        fragments.append(("class:detail.label", "  Type:     "))
        fragments.append(("class:detail.value", f"{fv.field_info.field_type}\n"))

        if fv.field_info.allowed_values:
            fragments.append(("class:detail.label", "  Allowed:  "))
            fragments.append(("class:detail.value", f"{', '.join(fv.field_info.allowed_values)}\n"))

        if fv.field_info.min_val is not None or fv.field_info.max_val is not None:
            fragments.append(("class:detail.label", "  Range:    "))
            fragments.append(("class:detail.value", f"{fv.field_info.min_val} .. {fv.field_info.max_val}\n"))

    # Enforced
    if fv.is_enforced:
        fragments.append(("class:detail.label", "  Enforced: "))
        fragments.append(("class:detail.enforced", "yes \u2014 locked by team config\n"))
    else:
        fragments.append(("class:detail.label", "  Enforced: "))
        fragments.append(("class:detail.value", "no\n"))

    # Layer breakdown
    if fv.layer_values or fv.effective_value is not None:
        fragments.append(("", "\n"))
        fragments.append(("class:detail.label", "  Layer values:\n"))
        for layer in _DISPLAY_LAYERS:
            if layer in fv.layer_values:
                val = fv.layer_values[layer]
                is_active = layer == fv.source_layer or (fv.source_layer == "team (enforced)" and layer == "team")
                l_style = LAYER_STYLE_MAP.get(layer, "")
                fragments.append((l_style, f"    {layer:<12s}"))
                fragments.append(("class:detail.value", f" \u2192 {val!r}"))
                if is_active:
                    fragments.append(("class:detail.active", "  \u2190 active"))
                fragments.append(("", "\n"))

    return fragments


def render_status_fragments(state: ConfigTuiState) -> list[tuple[str, str]]:
    """Render the bottom status bar."""
    fragments: list[tuple[str, str]] = []

    # Keybinding hints
    fragments.append(("class:status.key", " \u2191\u2193 "))
    fragments.append(("class:status.hint", "navigate  "))
    fragments.append(("class:status.key", " Enter "))
    fragments.append(("class:status.hint", "edit  "))
    fragments.append(("class:status.key", " Tab "))
    fragments.append(("class:status.hint", "scope  "))
    fragments.append(("class:status.key", " / "))
    fragments.append(("class:status.hint", "search  "))
    fragments.append(("class:status.key", " s "))
    fragments.append(("class:status.hint", "save  "))
    fragments.append(("class:status.key", " r "))
    fragments.append(("class:status.hint", "reset  "))
    fragments.append(("class:status.key", " Esc "))
    fragments.append(("class:status.hint", "quit"))

    # Scope indicator
    scope_text = f"  [{state.active_scope}]"
    fragments.append(("class:status.scope", scope_text))

    # Pending changes count
    n = len(state.pending_changes)
    if n:
        fragments.append(("class:status.modified", f"  {n} unsaved"))

    # Flash message
    flash = state.flash
    if flash:
        fragments.append(("class:status.flash", f"  {flash}"))

    return fragments


# ---------------------------------------------------------------------------
# TUI Application
# ---------------------------------------------------------------------------


async def run_config_tui(
    *,
    config: Any,
    db: Any,
    active_space: dict[str, Any] | None,
    working_dir: str,
    ai_service: Any,
    toolbar_refresh: Any,
) -> None:
    """Launch the full-screen config editor TUI.

    Called from the ``/config`` handler in ``repl.py``.
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout.containers import HSplit, VSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.layout import Layout
    from prompt_toolkit.styles import Style

    from ..services.config_editor import list_settable_fields

    # ── Build initial state ──

    state = _build_state(config, db, active_space, working_dir)
    visible = build_visible_items(state, config)

    if not visible:
        renderer.console.print("[dim]No config fields found.[/dim]\n")
        return

    # ── Controls ──

    list_control = FormattedTextControl(lambda: FormattedText(render_list_fragments(state, state._visible)))
    detail_control = FormattedTextControl(lambda: FormattedText(render_detail_fragments(state, state._visible, config)))
    status_control = FormattedTextControl(lambda: FormattedText(render_status_fragments(state)))

    def _refresh() -> None:
        build_visible_items(state, config)
        app.invalidate()

    # ── Keybindings ──

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def _up(event: Any) -> None:
        if state.selected_idx > 0:
            state.selected_idx -= 1
            _refresh()

    @kb.add("down")
    @kb.add("j")
    def _down(event: Any) -> None:
        if state.selected_idx < len(state._visible) - 1:
            state.selected_idx += 1
            _refresh()

    @kb.add("enter")
    def _enter(event: Any) -> None:
        if not state._visible or state.selected_idx >= len(state._visible):
            return
        item = state._visible[state.selected_idx]

        if item.kind == "section":
            # Toggle collapse
            section_name = item.label.split()[-1].lower()
            if section_name in state.collapsed_sections:
                state.collapsed_sections.discard(section_name)
            else:
                state.collapsed_sections.add(section_name)
            _refresh()
            return

        # Edit field
        if item.is_enforced:
            state.set_message("Locked by team config \u2014 cannot edit")
            _refresh()
            return

        if item.field_type == "bool":
            # Toggle boolean
            from dataclasses import asdict

            from ..services.config_overlays import flatten_to_dot_paths

            current = state.pending_changes.get(item.dot_path)
            if current is None:
                flat = flatten_to_dot_paths(asdict(config))
                current = flat.get(item.dot_path, False)
            new_val = not bool(current)
            state.pending_changes[item.dot_path] = new_val
            state.set_message(f"{item.dot_path} = {new_val}")
            _refresh()
            return

        if item.field_type == "enum":
            # Cycle through allowed values
            fi = None
            for f in list_settable_fields():
                if f.dot_path == item.dot_path:
                    fi = f
                    break
            if fi and fi.allowed_values:
                from dataclasses import asdict

                from ..services.config_overlays import flatten_to_dot_paths

                current = state.pending_changes.get(item.dot_path)
                if current is None:
                    flat = flatten_to_dot_paths(asdict(config))
                    current = flat.get(item.dot_path, "")
                current_str = str(current).lower()
                vals = list(fi.allowed_values)
                try:
                    idx = vals.index(current_str)
                    next_val = vals[(idx + 1) % len(vals)]
                except ValueError:
                    next_val = vals[0]
                state.pending_changes[item.dot_path] = next_val
                state.set_message(f"{item.dot_path} = {next_val}")
                _refresh()
                return

        # String/numeric: exit TUI temporarily for inline edit
        event.app.exit(result=("edit", item.dot_path))

    @kb.add("tab")
    def _cycle_scope(event: Any) -> None:
        scopes = state.available_scopes
        if not scopes:
            return
        try:
            idx = scopes.index(state.active_scope)
            state.active_scope = scopes[(idx + 1) % len(scopes)]
        except ValueError:
            state.active_scope = scopes[0]
        state.set_message(f"Scope: {state.active_scope}")
        _refresh()

    @kb.add("/")
    def _search(event: Any) -> None:
        event.app.exit(result=("search",))

    @kb.add("s")
    def _save(event: Any) -> None:
        if not state.pending_changes:
            state.set_message("No changes to save")
            _refresh()
            return
        event.app.exit(result=("save",))

    @kb.add("r")
    def _reset(event: Any) -> None:
        if not state._visible or state.selected_idx >= len(state._visible):
            return
        item = state._visible[state.selected_idx]
        if item.kind != "field":
            return
        if item.is_enforced:
            state.set_message("Locked by team config")
            _refresh()
            return
        event.app.exit(result=("reset", item.dot_path))

    @kb.add("escape")
    @kb.add("q")
    def _quit(event: Any) -> None:
        if state.pending_changes:
            event.app.exit(result=("confirm_quit",))
        else:
            event.app.exit(result=None)

    # ── Layout ──

    separator = Window(width=1, char="\u2502", style="class:separator")  # │
    body = VSplit(
        [
            Window(content=list_control, width=48, wrap_lines=False),
            separator,
            Window(content=detail_control, wrap_lines=True),
        ]
    )
    title_bar = Window(
        content=FormattedTextControl(
            lambda: FormattedText(
                [
                    ("class:title", " Config Editor  "),
                    ("class:title.scope", f" [{state.active_scope}] "),
                ]
            )
        ),
        height=1,
    )
    status_bar = Window(content=status_control, height=1)
    layout = Layout(HSplit([title_bar, body, status_bar]))

    # ── Style ──

    theme = renderer._theme
    style = Style.from_dict(
        {
            "title": f"bg:{theme.accent} {theme.bg_dark} bold",
            "title.scope": f"bg:{theme.bg_subtle} {theme.secondary}",
            "separator": theme.bg_subtle,
            # List panel
            "list.selected": f"bg:{theme.bg_highlight} {theme.accent} bold",
            "section": f"{theme.secondary} bold",
            "enforced-icon": theme.error,
            # Layer colors
            "layer.default": "gray italic",
            "layer.team": theme.error,
            "layer.enforced": f"{theme.error} bold",
            "layer.pack": theme.mcp_indicator,
            "layer.personal": theme.logo_blue,
            "layer.space": theme.success,
            "layer.project": theme.warning,
            "layer.env": theme.chrome,
            # Detail panel
            "detail.title": f"{theme.accent} bold",
            "detail.sep": theme.chrome,
            "detail.label": f"{theme.secondary}",
            "detail.value": theme.text_light,
            "detail.modified": f"{theme.warning} bold",
            "detail.enforced": f"{theme.error} bold",
            "detail.active": f"{theme.accent} bold",
            "detail.hint": f"{theme.chrome} italic",
            "detail.error": theme.error,
            "detail.empty": f"{theme.chrome} italic",
            # Status bar
            "status.key": f"bg:{theme.bg_subtle} {theme.accent} bold",
            "status.hint": f"bg:{theme.bg_darker} {theme.chrome}",
            "status.scope": f"bg:{theme.bg_darker} {theme.secondary} bold",
            "status.modified": f"bg:{theme.bg_darker} {theme.warning} bold",
            "status.flash": f"bg:{theme.bg_darker} {theme.success}",
        }
    )

    # ── Run loop ──
    # The TUI may exit with an action tuple, in which case we handle
    # the action and re-enter the TUI.

    while True:
        app: Application[Any] = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=True,
        )

        result = await app.run_async()

        if result is None:
            # Clean exit
            return

        if isinstance(result, tuple):
            action = result[0]

            if action == "confirm_quit":
                n = len(state.pending_changes)
                renderer.console.print(
                    f"\n  [bold]Discard {n} unsaved change{'s' if n != 1 else ''}?[/bold] [dim](y/N)[/dim] ",
                    end="",
                )
                try:
                    from prompt_toolkit import PromptSession as _ConfirmSession

                    confirm_session: _ConfirmSession[str] = _ConfirmSession()
                    answer = await confirm_session.prompt_async("")
                    if answer.strip().lower() in ("y", "yes"):
                        return
                except (EOFError, KeyboardInterrupt):
                    return
                # User said no — re-enter TUI
                continue

            if action == "edit":
                dot_path = result[1]
                await _handle_inline_edit(state, config, dot_path)
                build_visible_items(state, config)
                continue

            if action == "search":
                await _handle_search(state, config)
                build_visible_items(state, config)
                continue

            if action == "save":
                await _handle_save(
                    state,
                    config,
                    db,
                    active_space,
                    working_dir,
                    ai_service,
                    toolbar_refresh,
                )
                state = _build_state(config, db, active_space, working_dir)
                build_visible_items(state, config)
                continue

            if action == "reset":
                dot_path = result[1]
                await _handle_reset(
                    state,
                    dot_path,
                    db,
                    active_space,
                    working_dir,
                )
                state = _build_state(config, db, active_space, working_dir)
                build_visible_items(state, config)
                continue


# ---------------------------------------------------------------------------
# State builder
# ---------------------------------------------------------------------------


def _build_state(
    config: Any,
    db: Any,
    active_space: dict[str, Any] | None,
    working_dir: str,
) -> ConfigTuiState:
    """Build initial TUI state from current config context."""
    from ..config import _get_config_path
    from ..services.config_editor import (
        _read_yaml,
        build_full_source_map,
        collect_env_overrides,
    )
    from ..services.project_config import discover_project_config
    from ..services.team_config import discover_team_config

    team_raw: dict[str, Any] = {}
    enforced: list[str] = []
    team_path = discover_team_config(cwd=working_dir)
    if team_path:
        from ..services.team_config import load_team_config

        team_raw, enforced = load_team_config(team_path, interactive=False)

    personal_raw = _read_yaml(_get_config_path())

    space_raw: dict[str, Any] = {}
    if active_space and active_space.get("source_file"):
        sp_path = Path(active_space["source_file"])
        if sp_path.exists():
            from ..services.spaces import parse_space_file

            sc = parse_space_file(sp_path)
            space_raw = sc.config or {}

    project_raw: dict[str, Any] = {}
    proj_path = discover_project_config(working_dir)
    if proj_path:
        project_raw = _read_yaml(proj_path)
        project_raw.pop("required", None)

    env_overrides = collect_env_overrides()

    source_map = build_full_source_map(
        team_raw=team_raw,
        personal_raw=personal_raw,
        space_raw=space_raw,
        project_raw=project_raw,
        env_overrides=env_overrides,
    )

    # Available scopes
    scopes = ["personal"]
    if active_space and active_space.get("source_file"):
        scopes.append("space")
    scopes.append("project")

    # Layer raws for detail panel breakdown
    layer_raws: dict[str, dict[str, Any]] = {}
    if team_raw:
        layer_raws["team"] = team_raw
    if personal_raw:
        layer_raws["personal"] = personal_raw
    if space_raw:
        layer_raws["space"] = space_raw
    if project_raw:
        layer_raws["project"] = project_raw
    if env_overrides:
        layer_raws["env var"] = env_overrides

    return ConfigTuiState(
        source_map=source_map,
        enforced_fields=enforced,
        layer_raws=layer_raws,
        available_scopes=scopes,
    )


# ---------------------------------------------------------------------------
# Action handlers (called between TUI re-entries)
# ---------------------------------------------------------------------------


async def _handle_inline_edit(
    state: ConfigTuiState,
    config: Any,
    dot_path: str,
) -> None:
    """Prompt for a new value for a string/numeric field."""
    from dataclasses import asdict

    from ..services.config_editor import validate_field_value
    from ..services.config_overlays import flatten_to_dot_paths

    flat = flatten_to_dot_paths(asdict(config))
    current = state.pending_changes.get(dot_path, flat.get(dot_path, ""))

    try:
        from prompt_toolkit import PromptSession as _EditSession

        edit_session: _EditSession[str] = _EditSession()
        renderer.console.print(f"\n  [bold]{dot_path}[/bold] (current: {current!r})")
        raw = await edit_session.prompt_async("  New value: ")
        raw = raw.strip()
        if not raw:
            return
    except (EOFError, KeyboardInterrupt):
        return

    parsed, errors = validate_field_value(dot_path, raw)
    if errors:
        for err in errors:
            renderer.console.print(f"  [red]{err}[/red]")
        import asyncio

        await asyncio.sleep(1.5)
        return

    state.pending_changes[dot_path] = parsed
    state.set_message(f"{dot_path} = {parsed!r}")


async def _handle_search(state: ConfigTuiState, config: Any) -> None:
    """Prompt for a search filter."""
    try:
        from prompt_toolkit import PromptSession as _SearchSession

        search_session: _SearchSession[str] = _SearchSession()
        renderer.console.print()
        raw = await search_session.prompt_async("  Filter: ")
        state.search_filter = raw.strip()
        state.selected_idx = 0
    except (EOFError, KeyboardInterrupt):
        pass


async def _handle_save(
    state: ConfigTuiState,
    config: Any,
    db: Any,
    active_space: dict[str, Any] | None,
    working_dir: str,
    ai_service: Any,
    toolbar_refresh: Any,
) -> None:
    """Commit all pending changes to the active scope."""
    from ..services.config_editor import (
        apply_field_to_config,
        check_write_allowed,
        write_personal_field,
        write_project_field,
        write_space_field,
    )

    scope = state.active_scope
    saved = 0
    errors: list[str] = []

    for dot_path, value in state.pending_changes.items():
        allowed, reason = check_write_allowed(dot_path, state.enforced_fields, allow_sensitive=True)
        if not allowed:
            errors.append(f"{dot_path}: {reason}")
            continue

        try:
            if scope == "personal":
                write_personal_field(dot_path, value)
            elif scope == "space":
                if not active_space or not active_space.get("source_file"):
                    errors.append(f"{dot_path}: no active space")
                    continue
                sp_path = Path(active_space["source_file"])
                sp_id = active_space.get("id")
                write_space_field(dot_path, value, sp_path, db=db, space_id=sp_id)
            elif scope == "project":
                write_project_field(dot_path, value, working_dir=working_dir)

            # Apply to live config (best-effort — file is already saved)
            try:
                apply_field_to_config(config, dot_path, value)
                if dot_path in ("ai.model", "model"):
                    if ai_service and hasattr(ai_service, "config"):
                        ai_service.config.model = value
                    toolbar_refresh()
            except (AttributeError, TypeError):
                pass

            saved += 1
        except Exception as exc:
            errors.append(f"{dot_path}: {exc}")

    if errors:
        for err in errors:
            renderer.console.print(f"  [red]{err}[/red]")

    # Only clear changes that were successfully saved; retain failed ones for retry
    failed_paths = {e.split(":")[0] for e in errors}
    for dot_path in list(state.pending_changes):
        if dot_path not in failed_paths:
            del state.pending_changes[dot_path]

    msg = f"Saved {saved} change{'s' if saved != 1 else ''} to {scope}"
    if errors:
        msg += f" ({len(errors)} failed)"
    state.set_message(msg)


async def _handle_reset(
    state: ConfigTuiState,
    dot_path: str,
    db: Any,
    active_space: dict[str, Any] | None,
    working_dir: str,
) -> None:
    """Reset a field at the active scope."""
    from ..services.config_editor import (
        reset_personal_field,
        reset_project_field,
        reset_space_field,
    )

    scope = state.active_scope

    try:
        deleted = False
        if scope == "personal":
            deleted = reset_personal_field(dot_path)
        elif scope == "space":
            if active_space and active_space.get("source_file"):
                sp_path = Path(active_space["source_file"])
                sp_id = active_space.get("id")
                deleted = reset_space_field(dot_path, sp_path, db=db, space_id=sp_id)
        elif scope == "project":
            deleted = reset_project_field(dot_path, working_dir=working_dir)

        if deleted:
            state.set_message(f"Removed {dot_path} from {scope}")
        else:
            state.set_message(f"{dot_path} not set in {scope}")
    except Exception as exc:
        renderer.console.print(f"  [red]Reset failed: {exc}[/red]")
