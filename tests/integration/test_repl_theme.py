"""Integration tests: CLI theme system wiring between themes.py and renderer.py.

Tests the integration between ``CliTheme.load()``, ``renderer.set_theme()``,
and Rich Console output without requiring a full prompt_toolkit REPL session.

Covers:
- Built-in theme loading for all four theme names
- NO_COLOR env var behavior (returns midnight colors, not empty)
- set_theme() correctly updates renderer._theme
- Rich Console markup using theme colors does not raise MarkupError
- ANSI escape sequences from ansi_fg() match expected RGB values
- ansi_fg() returns empty string under NO_COLOR
- Config round-trip: CliTheme.load(config.cli.theme) → renderer._theme
"""

from __future__ import annotations

import io
import re

import pytest
from rich.console import Console
from rich.markup import MarkupError

from anteroom.cli import renderer
from anteroom.cli.themes import CliTheme
from anteroom.config import CliConfig

# ANSI 24-bit foreground: \033[38;2;R;G;Bm
_ANSI_24BIT_RE = re.compile(r"\033\[38;2;(\d+);(\d+);(\d+)m")

_BUILTIN_THEME_NAMES = ["midnight", "dawn", "high-contrast", "accessible"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert #RRGGBB to (r, g, b) tuple."""
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _render_markup(markup: str, theme: CliTheme) -> str:
    """Render Rich markup to a string buffer using the given theme's accent color."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor", width=80)
    console.print(markup)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Theme loading
# ---------------------------------------------------------------------------


class TestCliThemeLoad:
    """CliTheme.load() returns correct built-in themes."""

    @pytest.mark.parametrize("name", _BUILTIN_THEME_NAMES)
    def test_load_builtin_theme_returns_nonepty_colors(self, name: str) -> None:
        """Each built-in theme name loads a fully-populated CliTheme."""
        theme = CliTheme.load(name)
        assert isinstance(theme, CliTheme)
        # All color slots must be non-empty strings
        from dataclasses import fields

        for field in fields(theme):
            value = getattr(theme, field.name)
            assert value, f"Theme '{name}' field '{field.name}' is empty"

    def test_load_unknown_name_falls_back_to_midnight(self) -> None:
        """Unknown theme name falls back to midnight without raising."""
        midnight = CliTheme.load("midnight")
        fallback = CliTheme.load("nonexistent-theme")
        assert fallback == midnight

    def test_available_returns_all_four_themes(self) -> None:
        """CliTheme.available() lists all four built-in theme names."""
        names = CliTheme.available()
        assert set(names) == {"midnight", "dawn", "high-contrast", "accessible"}

    @pytest.mark.parametrize("name", _BUILTIN_THEME_NAMES)
    def test_load_returns_distinct_instances_per_theme(self, name: str) -> None:
        """Each theme name returns a distinct theme (not all the same object)."""
        themes = {n: CliTheme.load(n) for n in _BUILTIN_THEME_NAMES}
        # All four themes must be distinct objects
        theme_ids = {id(t) for t in themes.values()}
        assert len(theme_ids) == len(_BUILTIN_THEME_NAMES)


# ---------------------------------------------------------------------------
# NO_COLOR behavior
# ---------------------------------------------------------------------------


class TestNoColorEnvVar:
    """NO_COLOR env var leaves colors intact in CliTheme but suppresses ANSI helpers."""

    def test_load_with_no_color_returns_midnight_colors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """NO_COLOR returns the midnight theme (non-empty colors), not a blank theme."""
        monkeypatch.setenv("NO_COLOR", "1")
        theme = CliTheme.load("dawn")
        midnight = CliTheme.load.__func__(CliTheme, "midnight")  # type: ignore[attr-defined]
        # Under NO_COLOR, load() always returns _NO_COLOR_THEME which equals _MIDNIGHT
        from dataclasses import fields

        for field in fields(theme):
            assert getattr(theme, field.name) == getattr(midnight, field.name), (
                f"Field '{field.name}' differs: expected midnight value"
            )

    def test_load_with_no_color_colors_are_not_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Even under NO_COLOR, the returned theme has non-empty color slots."""
        monkeypatch.setenv("NO_COLOR", "1")
        theme = CliTheme.load("midnight")
        from dataclasses import fields

        for field in fields(theme):
            value = getattr(theme, field.name)
            assert value, f"Field '{field.name}' is empty under NO_COLOR — should retain midnight colors"

    def test_ansi_fg_returns_empty_string_under_no_color(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ansi_fg() returns empty string when NO_COLOR is set."""
        monkeypatch.setenv("NO_COLOR", "1")
        theme = CliTheme.load("midnight")
        # ansi_fg checks env var directly, so should be empty even though theme has colors
        result = theme.ansi_fg("accent")
        assert result == "", f"Expected empty string under NO_COLOR, got {result!r}"

    def test_ansi_reset_returns_empty_string_under_no_color(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ansi_reset property returns empty string when NO_COLOR is set."""
        monkeypatch.setenv("NO_COLOR", "1")
        theme = CliTheme.load("midnight")
        assert theme.ansi_reset == ""

    def test_ansi_fg_returns_sequence_without_no_color(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ansi_fg() returns a non-empty ANSI sequence when NO_COLOR is not set."""
        monkeypatch.delenv("NO_COLOR", raising=False)
        theme = CliTheme.load("midnight")
        result = theme.ansi_fg("accent")
        assert result != "", "Expected non-empty ANSI sequence without NO_COLOR"
        assert "\033[" in result, f"Expected ANSI escape in result, got {result!r}"


# ---------------------------------------------------------------------------
# set_theme() ↔ renderer._theme integration
# ---------------------------------------------------------------------------


class TestSetThemeRendererIntegration:
    """set_theme() updates renderer._theme to the correct CliTheme instance."""

    @pytest.fixture(autouse=True)
    def restore_renderer_theme(self) -> None:
        """Restore renderer._theme to midnight after each test."""
        original = renderer._theme
        yield
        renderer.set_theme(original)

    @pytest.mark.parametrize("name", _BUILTIN_THEME_NAMES)
    def test_set_theme_updates_renderer_theme(self, name: str) -> None:
        """After set_theme(), renderer._theme matches the loaded CliTheme."""
        theme = CliTheme.load(name)
        renderer.set_theme(theme)
        assert renderer._theme is theme

    @pytest.mark.parametrize("name", _BUILTIN_THEME_NAMES)
    def test_set_theme_updates_module_aliases(self, name: str) -> None:
        """set_theme() updates GOLD, SLATE, BLUE, etc. module-level aliases."""
        theme = CliTheme.load(name)
        renderer.set_theme(theme)
        assert renderer.GOLD == theme.accent
        assert renderer.SLATE == theme.secondary
        assert renderer.BLUE == theme.logo_blue
        assert renderer.MUTED == theme.muted
        assert renderer.CHROME == theme.chrome
        assert renderer.ERROR_RED == theme.error

    def test_set_theme_midnight_then_dawn_then_back(self) -> None:
        """Multiple sequential set_theme() calls each update renderer correctly."""
        midnight = CliTheme.load("midnight")
        dawn = CliTheme.load("dawn")

        renderer.set_theme(dawn)
        assert renderer._theme.accent == dawn.accent

        renderer.set_theme(midnight)
        assert renderer._theme.accent == midnight.accent


# ---------------------------------------------------------------------------
# Rich Console markup validity
# ---------------------------------------------------------------------------


class TestRichConsoleMarkupValidity:
    """Rich Console output using theme colors does not raise MarkupError."""

    @pytest.fixture(autouse=True)
    def restore_renderer_theme(self) -> None:
        original = renderer._theme
        yield
        renderer.set_theme(original)

    @pytest.mark.parametrize("name", _BUILTIN_THEME_NAMES)
    def test_theme_accent_color_is_valid_rich_markup(self, name: str) -> None:
        """Accent color from each theme can be used in Rich markup without error."""
        theme = CliTheme.load(name)
        renderer.set_theme(theme)

        accent = theme.accent
        markup = f"[bold {accent}]Hello, theme[/bold {accent}]"
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=True, color_system="truecolor", width=80)
        # Should not raise MarkupError
        try:
            console.print(markup)
        except MarkupError as exc:
            pytest.fail(f"Theme '{name}' accent color '{accent}' produced MarkupError: {exc}")

    @pytest.mark.parametrize("name", _BUILTIN_THEME_NAMES)
    def test_all_theme_hex_colors_are_valid_rich_markup(self, name: str) -> None:
        """All hex color values in each theme can be used in Rich markup."""
        from dataclasses import fields

        theme = CliTheme.load(name)
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=True, color_system="truecolor", width=80)

        for field in fields(theme):
            color = getattr(theme, field.name)
            markup = f"[{color}]text[/{color}]"
            try:
                console.print(markup)
            except MarkupError as exc:
                pytest.fail(f"Theme '{name}' field '{field.name}' color '{color}' produced MarkupError: {exc}")


# ---------------------------------------------------------------------------
# ANSI escape sequence correctness
# ---------------------------------------------------------------------------


class TestAnsiEscapeSequences:
    """ansi_fg() produces correct 24-bit ANSI escape sequences."""

    @pytest.fixture(autouse=True)
    def clear_no_color(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)

    @pytest.mark.parametrize("name", _BUILTIN_THEME_NAMES)
    def test_ansi_fg_accent_matches_hex_rgb(self, name: str) -> None:
        """ansi_fg('accent') encodes the correct RGB values from the hex color."""
        theme = CliTheme.load(name)
        escape_seq = theme.ansi_fg("accent")

        assert escape_seq, f"Expected non-empty ANSI escape for theme '{name}'"
        match = _ANSI_24BIT_RE.search(escape_seq)
        assert match, f"Expected 24-bit ANSI format in {escape_seq!r}"

        r, g, b = int(match.group(1)), int(match.group(2)), int(match.group(3))
        expected_r, expected_g, expected_b = _hex_to_rgb(theme.accent)
        assert (r, g, b) == (expected_r, expected_g, expected_b), (
            f"Theme '{name}' accent ANSI RGB ({r},{g},{b}) != hex RGB "
            f"({expected_r},{expected_g},{expected_b}) for {theme.accent}"
        )

    @pytest.mark.parametrize(
        "slot",
        ["accent", "secondary", "success", "error", "warning", "muted", "chrome"],
    )
    def test_midnight_ansi_fg_slots_match_hex(self, slot: str) -> None:
        """midnight theme ansi_fg() for key slots encodes correct RGB from hex."""
        theme = CliTheme.load("midnight")
        escape_seq = theme.ansi_fg(slot)
        hex_color = getattr(theme, slot)

        assert escape_seq, f"Expected non-empty ANSI escape for slot '{slot}'"
        match = _ANSI_24BIT_RE.search(escape_seq)
        assert match, f"Expected 24-bit ANSI format in {escape_seq!r}"

        r, g, b = int(match.group(1)), int(match.group(2)), int(match.group(3))
        expected_r, expected_g, expected_b = _hex_to_rgb(hex_color)
        assert (r, g, b) == (expected_r, expected_g, expected_b)

    def test_ansi_fg_unknown_slot_returns_empty(self) -> None:
        """ansi_fg() with a nonexistent slot name returns empty string."""
        theme = CliTheme.load("midnight")
        result = theme.ansi_fg("nonexistent_slot")
        assert result == ""

    def test_ansi_fg_returns_correct_format(self) -> None:
        """ansi_fg() output starts with ESC[ and ends with m."""
        theme = CliTheme.load("midnight")
        result = theme.ansi_fg("accent")
        assert result.startswith("\033["), f"Expected ESC[ prefix, got {result!r}"
        assert result.endswith("m"), f"Expected 'm' suffix, got {result!r}"


# ---------------------------------------------------------------------------
# Config round-trip
# ---------------------------------------------------------------------------


class TestConfigThemeRoundTrip:
    """Config round-trip: CliConfig.theme → CliTheme.load() → renderer._theme."""

    @pytest.fixture(autouse=True)
    def restore_renderer_theme(self) -> None:
        original = renderer._theme
        yield
        renderer.set_theme(original)

    @pytest.mark.parametrize("theme_name", _BUILTIN_THEME_NAMES)
    def test_config_theme_name_loads_correct_theme(self, theme_name: str) -> None:
        """Setting CliConfig.theme and calling CliTheme.load() mirrors repl.py startup."""
        config_cli = CliConfig(theme=theme_name)

        # Simulate what repl.py run_cli() does
        theme = CliTheme.load(config_cli.theme)
        renderer.set_theme(theme)

        expected = CliTheme.load(theme_name)
        assert renderer._theme == expected, f"renderer._theme does not match expected theme '{theme_name}'"

    def test_config_default_theme_is_midnight(self) -> None:
        """CliConfig default theme field is 'midnight'."""
        config_cli = CliConfig()
        assert config_cli.theme == "midnight"

    def test_config_unknown_theme_falls_back_to_midnight(self) -> None:
        """Unknown theme name in config causes renderer to use midnight."""
        config_cli = CliConfig(theme="does-not-exist")
        theme = CliTheme.load(config_cli.theme)
        renderer.set_theme(theme)

        midnight = CliTheme.load("midnight")
        assert renderer._theme == midnight

    @pytest.mark.parametrize("theme_name", _BUILTIN_THEME_NAMES)
    def test_renderer_theme_accent_matches_config_theme(self, theme_name: str) -> None:
        """renderer._theme.accent reflects the theme specified in config after set_theme."""
        config_cli = CliConfig(theme=theme_name)
        theme = CliTheme.load(config_cli.theme)
        renderer.set_theme(theme)

        expected_accent = CliTheme.load(theme_name).accent
        assert renderer._theme.accent == expected_accent
