"""Tests for the interactive config TUI state and rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

from anteroom.cli.config_tui import (
    LAYER_COLORS,
    LAYER_STYLE_MAP,
    ConfigTuiState,
    _VisibleItem,
    build_visible_items,
    render_detail_fragments,
    render_list_fragments,
    render_status_fragments,
)

# ---------------------------------------------------------------------------
# Minimal config stub
# ---------------------------------------------------------------------------


@dataclass
class _AIStub:
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1"
    temperature: float | None = None


@dataclass
class _SafetyStub:
    approval_mode: str = "ask_for_writes"
    read_only: bool = False


@dataclass
class _ConfigStub:
    ai: _AIStub = field(default_factory=_AIStub)
    safety: _SafetyStub = field(default_factory=_SafetyStub)


def _make_field_info(dot_path: str, field_type: str = "str", **kwargs: Any) -> Any:
    """Create a ConfigFieldInfo-like object."""
    from anteroom.services.config_editor import ConfigFieldInfo

    return ConfigFieldInfo(dot_path=dot_path, field_type=field_type, **kwargs)


# ---------------------------------------------------------------------------
# ConfigTuiState tests
# ---------------------------------------------------------------------------


class TestConfigTuiState:
    def test_initial_state(self) -> None:
        state = ConfigTuiState()
        assert state.selected_idx == 0
        assert state.active_scope == "personal"
        assert state.pending_changes == {}
        assert state.collapsed_sections == set()
        assert state.search_filter == ""

    def test_set_message(self) -> None:
        state = ConfigTuiState()
        state.set_message("hello")
        assert state.message == "hello"
        assert state.flash == "hello"

    def test_flash_expires(self) -> None:
        state = ConfigTuiState()
        state.set_message("old")
        state.message_time = 0.0  # force expiry
        assert state.flash == ""

    def test_available_scopes_default(self) -> None:
        state = ConfigTuiState()
        assert state.available_scopes == ["personal"]


# ---------------------------------------------------------------------------
# build_visible_items tests
# ---------------------------------------------------------------------------


class TestBuildVisibleItems:
    @patch("anteroom.services.config_editor.list_settable_fields")
    def test_basic_grouping(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [
            _make_field_info("ai.model"),
            _make_field_info("ai.base_url"),
            _make_field_info("safety.approval_mode", "enum"),
        ]
        config = _ConfigStub()
        state = ConfigTuiState(source_map={"ai.model": "personal"})

        items = build_visible_items(state, config)

        # Should have 2 section headers + 3 fields
        sections = [i for i in items if i.kind == "section"]
        fields = [i for i in items if i.kind == "field"]
        assert len(sections) == 2
        assert len(fields) == 3

    @patch("anteroom.services.config_editor.list_settable_fields")
    def test_search_filter(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [
            _make_field_info("ai.model"),
            _make_field_info("safety.approval_mode"),
        ]
        config = _ConfigStub()
        state = ConfigTuiState(search_filter="model")

        items = build_visible_items(state, config)

        fields = [i for i in items if i.kind == "field"]
        assert len(fields) == 1
        assert fields[0].dot_path == "ai.model"

    @patch("anteroom.services.config_editor.list_settable_fields")
    def test_collapsed_section(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [
            _make_field_info("ai.model"),
            _make_field_info("ai.base_url"),
        ]
        config = _ConfigStub()
        state = ConfigTuiState(collapsed_sections={"ai"})

        items = build_visible_items(state, config)

        # Section header only, no fields
        assert len(items) == 1
        assert items[0].kind == "section"
        assert "\u25b8" in items[0].label  # ▸ collapsed arrow

    @patch("anteroom.services.config_editor.list_settable_fields")
    def test_expanded_section_arrow(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [_make_field_info("ai.model")]
        config = _ConfigStub()
        state = ConfigTuiState()

        items = build_visible_items(state, config)

        section = [i for i in items if i.kind == "section"][0]
        assert "\u25be" in section.label  # ▾ expanded arrow

    @patch("anteroom.services.config_editor.list_settable_fields")
    def test_enforced_field(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [_make_field_info("safety.approval_mode")]
        config = _ConfigStub()
        state = ConfigTuiState(enforced_fields=["safety.approval_mode"])

        items = build_visible_items(state, config)

        field_item = [i for i in items if i.kind == "field"][0]
        assert field_item.is_enforced
        assert field_item.source == "team (enforced)"

    @patch("anteroom.services.config_editor.list_settable_fields")
    def test_modified_field(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [_make_field_info("ai.model")]
        config = _ConfigStub()
        state = ConfigTuiState(pending_changes={"ai.model": "gpt-4o"})

        items = build_visible_items(state, config)

        field_item = [i for i in items if i.kind == "field"][0]
        assert field_item.is_modified

    @patch("anteroom.services.config_editor.list_settable_fields")
    def test_empty_search_returns_nothing(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [_make_field_info("ai.model")]
        config = _ConfigStub()
        state = ConfigTuiState(search_filter="nonexistent")

        items = build_visible_items(state, config)
        assert len(items) == 0


# ---------------------------------------------------------------------------
# render_list_fragments tests
# ---------------------------------------------------------------------------


class TestRenderListFragments:
    def test_selected_item_highlighted(self) -> None:
        visible = [
            _VisibleItem(kind="section", label="\u25be AI"),
            _VisibleItem(kind="field", label="ai.model", dot_path="ai.model", source="personal"),
            _VisibleItem(kind="field", label="ai.base_url", dot_path="ai.base_url", source="default"),
        ]
        state = ConfigTuiState(selected_idx=1)

        fragments = render_list_fragments(state, visible)

        # The selected item should use the selected style
        selected = [f for f in fragments if f[0] == "class:list.selected"]
        assert len(selected) > 0

    def test_enforced_shows_lock(self) -> None:
        visible = [
            _VisibleItem(
                kind="field",
                label="safety.approval_mode",
                dot_path="safety.approval_mode",
                source="team (enforced)",
                is_enforced=True,
            ),
        ]
        state = ConfigTuiState(selected_idx=1)  # not selected

        fragments = render_list_fragments(state, visible)

        # Should contain lock icon
        text = "".join(f[1] for f in fragments)
        assert "\U0001f512" in text  # 🔒

    def test_modified_shows_asterisk(self) -> None:
        visible = [
            _VisibleItem(
                kind="field",
                label="ai.model",
                dot_path="ai.model",
                source="personal",
                is_modified=True,
            ),
        ]
        state = ConfigTuiState(selected_idx=1)  # not selected

        fragments = render_list_fragments(state, visible)

        text = "".join(f[1] for f in fragments)
        assert "\u2022" in text  # bullet dot for modified

    def test_section_header_rendered(self) -> None:
        visible = [_VisibleItem(kind="section", label="\u25be AI")]
        state = ConfigTuiState(selected_idx=0)

        fragments = render_list_fragments(state, visible)

        text = "".join(f[1] for f in fragments)
        assert "AI" in text


# ---------------------------------------------------------------------------
# render_detail_fragments tests
# ---------------------------------------------------------------------------


class TestRenderDetailFragments:
    def test_section_selected(self) -> None:
        visible = [_VisibleItem(kind="section", label="\u25be AI")]
        state = ConfigTuiState(selected_idx=0)
        config = _ConfigStub()

        fragments = render_detail_fragments(state, visible, config)

        text = "".join(f[1] for f in fragments)
        assert "Section" in text

    @patch("anteroom.services.config_editor.get_field")
    def test_field_detail(self, mock_get: MagicMock) -> None:
        from anteroom.services.config_editor import ConfigFieldInfo, ConfigFieldValue

        mock_get.return_value = ConfigFieldValue(
            dot_path="ai.model",
            effective_value="gpt-4o",
            source_layer="personal",
            is_enforced=False,
            field_info=ConfigFieldInfo(dot_path="ai.model", field_type="str"),
            layer_values={"personal": "gpt-4o"},
        )
        visible = [
            _VisibleItem(kind="field", label="ai.model", dot_path="ai.model", source="personal"),
        ]
        state = ConfigTuiState(selected_idx=0)
        config = _ConfigStub()

        fragments = render_detail_fragments(state, visible, config)

        text = "".join(f[1] for f in fragments)
        assert "ai.model" in text
        assert "gpt-4o" in text
        assert "personal" in text

    @patch("anteroom.services.config_editor.get_field")
    def test_modified_field_shows_pending(self, mock_get: MagicMock) -> None:
        from anteroom.services.config_editor import ConfigFieldInfo, ConfigFieldValue

        mock_get.return_value = ConfigFieldValue(
            dot_path="ai.model",
            effective_value="gpt-4o-mini",
            source_layer="default",
            is_enforced=False,
            field_info=ConfigFieldInfo(dot_path="ai.model", field_type="str"),
        )
        visible = [
            _VisibleItem(kind="field", label="ai.model", dot_path="ai.model", source="default"),
        ]
        state = ConfigTuiState(
            selected_idx=0,
            pending_changes={"ai.model": "gpt-4o"},
        )
        config = _ConfigStub()

        fragments = render_detail_fragments(state, visible, config)

        text = "".join(f[1] for f in fragments)
        assert "gpt-4o" in text
        assert "(modified)" in text

    @patch("anteroom.services.config_editor.get_field")
    def test_enforced_field_detail(self, mock_get: MagicMock) -> None:
        from anteroom.services.config_editor import ConfigFieldValue

        mock_get.return_value = ConfigFieldValue(
            dot_path="safety.approval_mode",
            effective_value="ask",
            source_layer="team (enforced)",
            is_enforced=True,
        )
        visible = [
            _VisibleItem(
                kind="field",
                label="safety.approval_mode",
                dot_path="safety.approval_mode",
                source="team (enforced)",
                is_enforced=True,
            ),
        ]
        state = ConfigTuiState(selected_idx=0)
        config = _ConfigStub()

        fragments = render_detail_fragments(state, visible, config)

        text = "".join(f[1] for f in fragments)
        assert "locked by team config" in text

    def test_empty_visible(self) -> None:
        state = ConfigTuiState(selected_idx=0)
        config = _ConfigStub()

        fragments = render_detail_fragments(state, [], config)

        text = "".join(f[1] for f in fragments)
        assert "No field selected" in text

    @patch("anteroom.services.config_editor.get_field")
    def test_layer_breakdown(self, mock_get: MagicMock) -> None:
        from anteroom.services.config_editor import ConfigFieldInfo, ConfigFieldValue

        mock_get.return_value = ConfigFieldValue(
            dot_path="ai.model",
            effective_value="gpt-4o",
            source_layer="personal",
            is_enforced=False,
            field_info=ConfigFieldInfo(dot_path="ai.model", field_type="str"),
            layer_values={"default": "gpt-4o-mini", "personal": "gpt-4o"},
        )
        visible = [
            _VisibleItem(kind="field", label="ai.model", dot_path="ai.model", source="personal"),
        ]
        state = ConfigTuiState(selected_idx=0)
        config = _ConfigStub()

        fragments = render_detail_fragments(state, visible, config)

        text = "".join(f[1] for f in fragments)
        assert "gpt-4o-mini" in text  # default layer value
        assert "\u2190 active" in text  # ← active marker


# ---------------------------------------------------------------------------
# render_status_fragments tests
# ---------------------------------------------------------------------------


class TestRenderStatusFragments:
    def test_basic_status(self) -> None:
        state = ConfigTuiState()

        fragments = render_status_fragments(state)

        text = "".join(f[1] for f in fragments)
        assert "navigate" in text
        assert "edit" in text
        assert "save" in text
        assert "[personal]" in text

    def test_pending_changes_shown(self) -> None:
        state = ConfigTuiState(pending_changes={"ai.model": "gpt-4o", "ai.base_url": "http://x"})

        fragments = render_status_fragments(state)

        text = "".join(f[1] for f in fragments)
        assert "2 unsaved" in text

    def test_flash_message_shown(self) -> None:
        state = ConfigTuiState()
        state.set_message("Saved 1 change")

        fragments = render_status_fragments(state)

        text = "".join(f[1] for f in fragments)
        assert "Saved 1 change" in text

    def test_scope_displayed(self) -> None:
        state = ConfigTuiState(active_scope="space")

        fragments = render_status_fragments(state)

        text = "".join(f[1] for f in fragments)
        assert "[space]" in text

    def test_edit_mode_shows_field_prompt(self) -> None:
        state = ConfigTuiState(input_mode="edit", input_field="ai.model")

        fragments = render_status_fragments(state)

        text = "".join(f[1] for f in fragments)
        assert "Set ai.model" in text
        # Normal hints should NOT be shown
        assert "navigate" not in text

    def test_search_mode_shows_filter_prompt(self) -> None:
        state = ConfigTuiState(input_mode="search")

        fragments = render_status_fragments(state)

        text = "".join(f[1] for f in fragments)
        assert "Filter" in text
        assert "navigate" not in text

    def test_confirm_quit_shows_discard_prompt(self) -> None:
        state = ConfigTuiState(
            input_mode="confirm_quit",
            pending_changes={"ai.model": "gpt-4o", "ai.base_url": "x"},
        )

        fragments = render_status_fragments(state)

        text = "".join(f[1] for f in fragments)
        assert "Discard 2 unsaved changes" in text


# ---------------------------------------------------------------------------
# Scope cycling
# ---------------------------------------------------------------------------


class TestScopeCycling:
    def test_cycle_personal_to_space(self) -> None:
        state = ConfigTuiState(
            active_scope="personal",
            available_scopes=["personal", "space", "project"],
        )
        scopes = state.available_scopes
        idx = scopes.index(state.active_scope)
        state.active_scope = scopes[(idx + 1) % len(scopes)]
        assert state.active_scope == "space"

    def test_cycle_project_wraps_to_personal(self) -> None:
        state = ConfigTuiState(
            active_scope="project",
            available_scopes=["personal", "space", "project"],
        )
        scopes = state.available_scopes
        idx = scopes.index(state.active_scope)
        state.active_scope = scopes[(idx + 1) % len(scopes)]
        assert state.active_scope == "personal"

    def test_cycle_skips_unavailable(self) -> None:
        # No space available
        state = ConfigTuiState(
            active_scope="personal",
            available_scopes=["personal", "project"],
        )
        scopes = state.available_scopes
        idx = scopes.index(state.active_scope)
        state.active_scope = scopes[(idx + 1) % len(scopes)]
        assert state.active_scope == "project"


# ---------------------------------------------------------------------------
# Pending changes
# ---------------------------------------------------------------------------


class TestPendingChanges:
    def test_add_change(self) -> None:
        state = ConfigTuiState()
        state.pending_changes["ai.model"] = "gpt-4o"
        assert len(state.pending_changes) == 1

    def test_overwrite_change(self) -> None:
        state = ConfigTuiState(pending_changes={"ai.model": "gpt-4o"})
        state.pending_changes["ai.model"] = "gpt-4o-mini"
        assert state.pending_changes["ai.model"] == "gpt-4o-mini"

    def test_discard_all(self) -> None:
        state = ConfigTuiState(pending_changes={"ai.model": "gpt-4o", "ai.base_url": "x"})
        state.pending_changes.clear()
        assert len(state.pending_changes) == 0


# ---------------------------------------------------------------------------
# Layer style constants
# ---------------------------------------------------------------------------


class TestLayerConstants:
    def test_all_layers_have_styles(self) -> None:
        for layer in ("default", "team", "team (enforced)", "pack", "personal", "space", "project", "env var"):
            assert layer in LAYER_STYLE_MAP
            assert layer in LAYER_COLORS
