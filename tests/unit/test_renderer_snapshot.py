"""Snapshot-style tests for theme-aware renderer output.

Verifies that:
- set_theme() updates the module-level _theme and refreshes aliases
- _refresh_aliases() correctly updates module aliases from the theme
- Theme colors flow through render_error(), render_warning(), and
  _build_thinking_text() instead of being hardcoded
- All built-in themes round-trip correctly through set_theme()
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

import anteroom.cli.renderer as renderer
from anteroom.cli.renderer import (
    _build_thinking_text,
    _refresh_aliases,
    render_error,
    render_warning,
    set_theme,
)
from anteroom.cli.themes import _BUILTIN_THEMES, CliTheme

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture_console() -> tuple[Console, io.StringIO]:
    """Return a (console, buffer) pair that captures Rich output as plain text."""
    buf = io.StringIO()
    con = Console(file=buf, force_terminal=True, color_system="truecolor", width=120, highlight=False)
    return con, buf


def _plain(text: str) -> str:
    """Strip ANSI escapes for assertion-friendly comparisons."""
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _hex_to_ansi_rgb(hex_color: str) -> str:
    """Convert #RRGGBB to the ANSI 24-bit foreground RGB sequence Rich emits."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"38;2;{r};{g};{b}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def restore_midnight_theme() -> None:  # type: ignore[return]
    """Reset to midnight after every test so state doesn't leak."""
    yield
    set_theme(CliTheme.load("midnight"))


# ---------------------------------------------------------------------------
# set_theme() and _refresh_aliases()
# ---------------------------------------------------------------------------


class TestSetTheme:
    def test_set_theme_updates_module_theme(self) -> None:
        dawn = CliTheme.load("dawn")
        set_theme(dawn)
        assert renderer._theme is dawn

    def test_set_theme_refreshes_gold_alias(self) -> None:
        dawn = CliTheme.load("dawn")
        set_theme(dawn)
        assert renderer.GOLD == dawn.accent

    def test_set_theme_refreshes_muted_alias(self) -> None:
        dawn = CliTheme.load("dawn")
        set_theme(dawn)
        assert renderer.MUTED == dawn.muted

    def test_set_theme_refreshes_chrome_alias(self) -> None:
        dawn = CliTheme.load("dawn")
        set_theme(dawn)
        assert renderer.CHROME == dawn.chrome

    def test_set_theme_refreshes_error_red_alias(self) -> None:
        dawn = CliTheme.load("dawn")
        set_theme(dawn)
        assert renderer.ERROR_RED == dawn.error

    def test_set_theme_refreshes_slate_alias(self) -> None:
        dawn = CliTheme.load("dawn")
        set_theme(dawn)
        assert renderer.SLATE == dawn.secondary

    def test_set_theme_refreshes_blue_alias(self) -> None:
        dawn = CliTheme.load("dawn")
        set_theme(dawn)
        assert renderer.BLUE == dawn.logo_blue

    def test_set_theme_midnight_accent(self) -> None:
        midnight = CliTheme.load("midnight")
        set_theme(midnight)
        assert renderer._theme.accent == midnight.accent

    def test_set_theme_dawn_accent(self) -> None:
        dawn = CliTheme.load("dawn")
        set_theme(dawn)
        assert renderer._theme.accent == dawn.accent

    def test_aliases_differ_between_themes(self) -> None:
        set_theme(CliTheme.load("midnight"))
        midnight_gold = renderer.GOLD

        set_theme(CliTheme.load("dawn"))
        dawn_gold = renderer.GOLD

        # Midnight accent is #C5A059; dawn accent is #B8860B — they must differ
        assert midnight_gold != dawn_gold


class TestRefreshAliases:
    def test_refresh_aliases_updates_gold(self) -> None:
        renderer._theme = CliTheme.load("dawn")
        _refresh_aliases()
        assert renderer.GOLD == CliTheme.load("dawn").accent

    def test_refresh_aliases_updates_muted(self) -> None:
        renderer._theme = CliTheme.load("high-contrast")
        _refresh_aliases()
        assert renderer.MUTED == CliTheme.load("high-contrast").muted

    def test_refresh_aliases_updates_chrome(self) -> None:
        renderer._theme = CliTheme.load("accessible")
        _refresh_aliases()
        assert renderer.CHROME == CliTheme.load("accessible").chrome

    def test_refresh_aliases_all_fields_in_sync(self) -> None:
        theme = CliTheme.load("dawn")
        renderer._theme = theme
        _refresh_aliases()
        assert renderer.GOLD == theme.accent
        assert renderer.SLATE == theme.secondary
        assert renderer.BLUE == theme.logo_blue
        assert renderer.MUTED == theme.muted
        assert renderer.CHROME == theme.chrome
        assert renderer.ERROR_RED == theme.error


# ---------------------------------------------------------------------------
# Dawn theme accent
# ---------------------------------------------------------------------------


class TestDawnThemeAccent:
    def test_dawn_accent_value(self) -> None:
        dawn = CliTheme.load("dawn")
        set_theme(dawn)
        assert renderer._theme.accent == "#B8860B"

    def test_dawn_error_value(self) -> None:
        dawn = CliTheme.load("dawn")
        set_theme(dawn)
        assert renderer._theme.error == "#DC2626"

    def test_dawn_warning_value(self) -> None:
        dawn = CliTheme.load("dawn")
        set_theme(dawn)
        assert renderer._theme.warning == "#D97706"


# ---------------------------------------------------------------------------
# Midnight theme aliases
# ---------------------------------------------------------------------------


class TestMidnightAliases:
    def test_midnight_gold_alias(self) -> None:
        set_theme(CliTheme.load("midnight"))
        assert renderer.GOLD == "#C5A059"

    def test_midnight_muted_alias(self) -> None:
        set_theme(CliTheme.load("midnight"))
        assert renderer.MUTED == "#8b8b8b"

    def test_midnight_chrome_alias(self) -> None:
        set_theme(CliTheme.load("midnight"))
        assert renderer.CHROME == "#6b7280"

    def test_midnight_error_alias(self) -> None:
        set_theme(CliTheme.load("midnight"))
        assert renderer.ERROR_RED == "#CD6B6B"


# ---------------------------------------------------------------------------
# render_error() uses theme error color
# ---------------------------------------------------------------------------


class TestRenderErrorTheme:
    def test_render_error_uses_theme_error_color(self) -> None:
        """render_error() must embed the theme's error color, not 'red'."""
        dawn = CliTheme.load("dawn")
        set_theme(dawn)

        buf = io.StringIO()
        # Patch the module-level console so render_error() writes to our buffer
        original_console = renderer.console
        renderer.console = Console(file=buf, force_terminal=True, color_system="truecolor", width=120, highlight=False)
        try:
            render_error("something went wrong")
        finally:
            renderer.console = original_console

        output = buf.getvalue()
        # Rich converts #RRGGBB to ANSI 24-bit sequences; check for the RGB portion
        assert _hex_to_ansi_rgb(dawn.error) in output
        assert "something went wrong" in _plain(output)

    def test_render_error_output_contains_error_label(self) -> None:
        set_theme(CliTheme.load("midnight"))

        buf = io.StringIO()
        original_console = renderer.console
        renderer.console = Console(file=buf, force_terminal=True, color_system="truecolor", width=120, highlight=False)
        try:
            render_error("disk full")
        finally:
            renderer.console = original_console

        plain = _plain(buf.getvalue())
        assert "Error:" in plain
        assert "disk full" in plain

    def test_render_error_midnight_error_color(self) -> None:
        midnight = CliTheme.load("midnight")
        set_theme(midnight)

        buf = io.StringIO()
        original_console = renderer.console
        renderer.console = Console(file=buf, force_terminal=True, color_system="truecolor", width=120, highlight=False)
        try:
            render_error("connection refused")
        finally:
            renderer.console = original_console

        assert _hex_to_ansi_rgb(midnight.error) in buf.getvalue()

    def test_render_error_no_hardcoded_red(self) -> None:
        """'red' must not appear as a standalone markup tag in error output."""
        set_theme(CliTheme.load("midnight"))

        buf = io.StringIO()
        original_console = renderer.console
        renderer.console = Console(file=buf, force_terminal=True, color_system="truecolor", width=120, highlight=False)
        try:
            render_error("boom")
        finally:
            renderer.console = original_console

        # Rich markup uses [red] or [red bold]; neither should be present
        # when the theme drives the color via a hex value
        raw = buf.getvalue()
        assert "[red]" not in raw
        assert "[red bold]" not in raw


# ---------------------------------------------------------------------------
# render_warning() uses theme warning color
# ---------------------------------------------------------------------------


class TestRenderWarningTheme:
    def test_render_warning_uses_theme_warning_color(self) -> None:
        dawn = CliTheme.load("dawn")
        set_theme(dawn)

        buf = io.StringIO()
        original_console = renderer.console
        renderer.console = Console(file=buf, force_terminal=True, color_system="truecolor", width=120, highlight=False)
        try:
            render_warning("low memory")
        finally:
            renderer.console = original_console

        output = buf.getvalue()
        assert _hex_to_ansi_rgb(dawn.warning) in output
        assert "low memory" in _plain(output)

    def test_render_warning_output_contains_warning_label(self) -> None:
        set_theme(CliTheme.load("midnight"))

        buf = io.StringIO()
        original_console = renderer.console
        renderer.console = Console(file=buf, force_terminal=True, color_system="truecolor", width=120, highlight=False)
        try:
            render_warning("approaching token limit")
        finally:
            renderer.console = original_console

        plain = _plain(buf.getvalue())
        assert "Warning:" in plain
        assert "approaching token limit" in plain

    def test_render_warning_no_hardcoded_yellow(self) -> None:
        set_theme(CliTheme.load("midnight"))

        buf = io.StringIO()
        original_console = renderer.console
        renderer.console = Console(file=buf, force_terminal=True, color_system="truecolor", width=120, highlight=False)
        try:
            render_warning("something")
        finally:
            renderer.console = original_console

        raw = buf.getvalue()
        assert "[yellow]" not in raw
        assert "[yellow bold]" not in raw

    def test_render_warning_accessible_theme(self) -> None:
        accessible = CliTheme.load("accessible")
        set_theme(accessible)

        buf = io.StringIO()
        original_console = renderer.console
        renderer.console = Console(file=buf, force_terminal=True, color_system="truecolor", width=120, highlight=False)
        try:
            render_warning("cvd safe warning")
        finally:
            renderer.console = original_console

        assert _hex_to_ansi_rgb(accessible.warning) in buf.getvalue()


# ---------------------------------------------------------------------------
# _build_thinking_text() uses theme colors
# ---------------------------------------------------------------------------


class TestBuildThinkingTextTheme:
    def test_thinking_text_uses_accent_color(self) -> None:
        midnight = CliTheme.load("midnight")
        set_theme(midnight)
        text = _build_thinking_text(0.1)
        # The accent ANSI code should appear in the output
        assert midnight.ansi_fg("accent") in text

    def test_thinking_text_elapsed_uses_chrome_color(self) -> None:
        midnight = CliTheme.load("midnight")
        set_theme(midnight)
        # Use elapsed > 0.5 to get the timer line
        text = _build_thinking_text(2.0)
        assert midnight.ansi_fg("chrome") in text

    def test_thinking_text_error_uses_error_color(self) -> None:
        midnight = CliTheme.load("midnight")
        set_theme(midnight)
        text = _build_thinking_text(1.0, error_msg="timeout")
        assert midnight.ansi_fg("error") in text

    def test_thinking_text_dawn_accent(self) -> None:
        dawn = CliTheme.load("dawn")
        set_theme(dawn)
        text = _build_thinking_text(0.1)
        assert dawn.ansi_fg("accent") in text

    def test_thinking_text_high_contrast_accent(self) -> None:
        hc = CliTheme.load("high-contrast")
        set_theme(hc)
        text = _build_thinking_text(0.1)
        assert hc.ansi_fg("accent") in text

    def test_thinking_text_muted_in_cancel(self) -> None:
        midnight = CliTheme.load("midnight")
        set_theme(midnight)
        text = _build_thinking_text(1.0, cancel_msg="cancelled")
        assert midnight.ansi_fg("muted") in text


# ---------------------------------------------------------------------------
# All built-in themes round-trip through set_theme()
# ---------------------------------------------------------------------------


class TestAllBuiltInThemes:
    @pytest.mark.parametrize("theme_name", list(_BUILTIN_THEMES.keys()))
    def test_set_theme_returns_correct_theme_object(self, theme_name: str) -> None:
        expected = CliTheme.load(theme_name)
        set_theme(expected)
        assert renderer._theme is expected

    @pytest.mark.parametrize("theme_name", list(_BUILTIN_THEMES.keys()))
    def test_aliases_match_theme_after_set(self, theme_name: str) -> None:
        theme = CliTheme.load(theme_name)
        set_theme(theme)
        assert renderer.GOLD == theme.accent
        assert renderer.MUTED == theme.muted
        assert renderer.CHROME == theme.chrome
        assert renderer.ERROR_RED == theme.error

    @pytest.mark.parametrize("theme_name", list(_BUILTIN_THEMES.keys()))
    def test_render_error_uses_theme_error_for_each_builtin(self, theme_name: str) -> None:
        theme = CliTheme.load(theme_name)
        set_theme(theme)

        buf = io.StringIO()
        original_console = renderer.console
        renderer.console = Console(file=buf, force_terminal=True, color_system="truecolor", width=120, highlight=False)
        try:
            render_error("test error")
        finally:
            renderer.console = original_console

        assert _hex_to_ansi_rgb(theme.error) in buf.getvalue()

    @pytest.mark.parametrize("theme_name", list(_BUILTIN_THEMES.keys()))
    def test_render_warning_uses_theme_warning_for_each_builtin(self, theme_name: str) -> None:
        theme = CliTheme.load(theme_name)
        set_theme(theme)

        buf = io.StringIO()
        original_console = renderer.console
        renderer.console = Console(file=buf, force_terminal=True, color_system="truecolor", width=120, highlight=False)
        try:
            render_warning("test warning")
        finally:
            renderer.console = original_console

        assert _hex_to_ansi_rgb(theme.warning) in buf.getvalue()

    @pytest.mark.parametrize("theme_name", list(_BUILTIN_THEMES.keys()))
    def test_theme_accent_is_non_empty(self, theme_name: str) -> None:
        theme = CliTheme.load(theme_name)
        assert theme.accent != ""

    @pytest.mark.parametrize("theme_name", list(_BUILTIN_THEMES.keys()))
    def test_theme_error_is_non_empty(self, theme_name: str) -> None:
        theme = CliTheme.load(theme_name)
        assert theme.error != ""

    @pytest.mark.parametrize("theme_name", list(_BUILTIN_THEMES.keys()))
    def test_theme_warning_is_non_empty(self, theme_name: str) -> None:
        theme = CliTheme.load(theme_name)
        assert theme.warning != ""
