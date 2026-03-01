"""Rich-based terminal output for the CLI chat."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from enum import Enum
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.status import Status
from rich.text import Text

console = Console(stderr=True)
# Separate console for stdout markdown rendering (not stderr)
_stdout_console = Console()
_stdout = sys.stdout

# ---------------------------------------------------------------------------
# Color palette — explicit values for readability on dark terminals.
# Avoids Rich's [dim] (SGR 2 faint) which is nearly invisible on dark bg.
# ---------------------------------------------------------------------------

GOLD = "#C5A059"  # accents, "Thinking..." text
SLATE = "#94A3B8"  # labels ("You:", "AI:"), directory display
MUTED = "#8b8b8b"  # secondary text (tool results, approval feedback, version info)
CHROME = "#6b7280"  # UI chrome (status messages, hints, MCP info)
ERROR_RED = "#CD6B6B"  # pale red for inline errors (operational, not alarming)

_ESC_HINT_DELAY: float = 3.0  # seconds before showing "esc to cancel" hint
_STALL_THRESHOLD: float = 15.0  # seconds before showing API stall warning


def use_stdout_console() -> None:
    """Switch renderer to REPL-compatible mode.

    - Opens a duplicate of the real stderr file descriptor so Rich output
      bypasses prompt_toolkit's ``patch_stdout`` proxy entirely.  The proxy
      corrupts ANSI escape bytes; a raw fd duplicate does not.
    - Disables the animated spinner (Rich Live/Status) whose cursor
      manipulation conflicts with prompt_toolkit's terminal management.
      A static "Thinking..." line is printed instead.

    Call from inside ``patch_stdout()`` context.
    """
    global console, _stdout_console, _stdout, _repl_mode
    _real_stderr = os.fdopen(os.dup(sys.stderr.fileno()), "w", newline="")
    console = Console(file=_real_stderr, force_terminal=True)
    _stdout_console = Console(file=_real_stderr, force_terminal=True)
    _stdout = _real_stderr
    _repl_mode = True


_fullscreen_mode: bool = False
_fullscreen_layout: Any = None  # AnteroomLayout instance (avoid circular import)
_fullscreen_invalidate: Any = None  # Callable[[], None]


def get_fullscreen_layout() -> Any:
    """Return the current fullscreen layout, or ``None`` if not in fullscreen mode."""
    return _fullscreen_layout


def use_fullscreen_output(layout: Any, invalidate_fn: Any) -> None:
    """Switch renderer to full-screen mode.

    All Rich output is captured as ANSI and redirected to the layout's
    output pane.  The thinking spinner becomes a status-line update
    instead of raw ANSI cursor manipulation.

    Parameters
    ----------
    layout:
        An ``AnteroomLayout`` instance whose ``output`` control receives
        rendered fragments.
    invalidate_fn:
        Callable (typically ``app.invalidate``) that triggers a repaint
        after new content is appended.
    """
    global console, _stdout_console, _stdout, _repl_mode
    global _fullscreen_mode, _fullscreen_layout, _fullscreen_invalidate

    from .layout import OutputPaneWriter

    _fullscreen_mode = True
    _fullscreen_layout = layout
    _fullscreen_invalidate = invalidate_fn
    _repl_mode = True

    writer = OutputPaneWriter(layout.output, invalidate_fn)
    console = Console(file=writer, force_terminal=True)
    _stdout_console = Console(file=writer, force_terminal=True)
    _stdout = writer  # type: ignore[assignment]


def is_fullscreen() -> bool:
    """Return whether the renderer is in full-screen layout mode."""
    return _fullscreen_mode


def configure_thresholds(
    esc_hint_delay: float | None = None,
    stall_display: float | None = None,
    stall_warning: float | None = None,
) -> None:
    """Override default visual thresholds from config."""
    global _ESC_HINT_DELAY, _MID_STREAM_STALL, _STALL_THRESHOLD
    if esc_hint_delay is not None:
        _ESC_HINT_DELAY = esc_hint_delay
    if stall_display is not None:
        _MID_STREAM_STALL = stall_display
    if stall_warning is not None:
        _STALL_THRESHOLD = stall_warning


# Response buffer (tokens collected silently, rendered on completion)
_streaming_buffer: list[str] = []

# Spinner state
_thinking_start: float = 0
_spinner: Status | None = None
_last_spinner_update: float = 0
_thinking_ticker_task: asyncio.Task[None] | None = None

# Lifecycle phase tracking
_thinking_phase: str = ""  # current phase: connecting, waiting, streaming
_thinking_tokens: int = 0  # token counter during streaming
_streaming_chars: int = 0  # character counter during streaming
_last_chunk_time: float = 0  # monotonic time of last token (for stall detection)
_phase_start_time: float = 0  # monotonic time when current phase began
_MID_STREAM_STALL: float = 5.0  # seconds of silence before marking "stalled"

# Tool call timing
_tool_start: float = 0
_tool_ticker_task: asyncio.Task[None] | None = None
_tool_ticker_summary: str = ""
_tool_spinner: Status | None = None

# Dedup tracking for repeated similar tool calls
_dedup_key: str = ""  # tool action type (e.g. "Editing", "Reading", "bash")
_dedup_count: int = 0
_dedup_first_summary: str = ""  # first summary in the group (printed immediately)

# Legacy alias used by tests — kept in sync with _dedup_key
_dedup_summary: str = ""

# Whether dedup is enabled (set from config)
_tool_dedup_enabled: bool = True

# Track whether we've started a tool call batch (for spacing)
_tool_batch_active: bool = False

# ---------------------------------------------------------------------------
# Plan checklist state
# ---------------------------------------------------------------------------

_plan_steps: list[dict[str, str]] = []  # [{"text": "...", "status": "pending|in_progress|complete"}]
_plan_visible: bool = False
_plan_written_lines: int = 0  # lines currently on screen (for cursor-up on redraw)
_plan_checkpoint: int = 0  # OutputControl fragment index for plan block (fullscreen)


def _render_plan_to_pane() -> None:
    """Render the plan checklist into the fullscreen output pane.

    Truncates back to ``_plan_checkpoint`` and re-appends all steps,
    allowing in-place updates without ANSI cursor codes.

    Safe to call while streaming is active — saves and restores the
    streaming cursor state so both plan updates and token streaming
    remain consistent.
    """
    global _streaming_checkpoint, _streaming_cursor_active
    if not _fullscreen_layout or not _plan_steps:
        return

    output = _fullscreen_layout.output

    # If the streaming cursor is active, stop it first so its fragments
    # are removed cleanly before we truncate back to the plan checkpoint.
    was_streaming = _streaming_cursor_active
    if was_streaming:
        _stop_streaming_cursor()

    output.truncate_to(_plan_checkpoint)

    # Plan header
    output.append([("class:plan.header", "  Plan\n")])

    # Steps
    _step_icons = {
        "pending": ("\u25cb", "class:plan.pending"),  # ○
        "in_progress": ("\u25b8", "class:plan.active"),  # ▸
        "complete": ("\u2713", "class:plan.complete"),  # ✓
        "failed": ("\u2717", "class:plan.failed"),  # ✗
    }
    for step in _plan_steps:
        status = step["status"]
        icon, style = _step_icons.get(status, ("\u25cb", "class:plan.pending"))
        output.append([(style, f"    {icon} {step['text']}\n")])

    output.append_newline()

    # Update streaming checkpoint to be after the plan block
    _streaming_checkpoint = output.checkpoint()

    # Restore streaming cursor if it was active — re-render buffer + cursor
    if was_streaming:
        _streaming_cursor_active = True
        _update_streaming_cursor()

    if _fullscreen_invalidate:
        _fullscreen_invalidate()


# ---------------------------------------------------------------------------
# Plan checklist API
# ---------------------------------------------------------------------------


def start_plan(steps: list[str]) -> None:
    """Initialize the plan checklist with step descriptions.

    Call this when a plan is approved and execution begins.
    The checklist is rendered above the thinking line during agentic runs.
    """
    global _plan_steps, _plan_visible, _plan_written_lines, _plan_checkpoint, _streaming_checkpoint
    _plan_steps = [{"text": s, "status": "pending"} for s in steps]
    _plan_visible = True
    _plan_written_lines = 0

    if _fullscreen_mode and _fullscreen_layout:
        _plan_checkpoint = _fullscreen_layout.output.checkpoint()
        _render_plan_to_pane()


def update_plan_step(index: int, status: str) -> None:
    """Update a plan step status: 'pending', 'in_progress', or 'complete'.

    Triggers a redraw if the thinking block is currently displayed.
    """
    if not _plan_steps or index < 0 or index >= len(_plan_steps):
        return
    _plan_steps[index]["status"] = status

    if _fullscreen_mode and _fullscreen_layout:
        _render_plan_to_pane()
        return

    # Redraw if thinking block is on screen
    if _repl_mode and _thinking_start and _stdout and _plan_written_lines > 0:
        elapsed = time.monotonic() - _thinking_start
        _write_thinking_block(elapsed)


def clear_plan() -> None:
    """Clear plan state entirely (e.g. on /plan off or new conversation)."""
    global _plan_steps, _plan_visible, _plan_written_lines, _plan_checkpoint, _streaming_checkpoint
    # Remove plan fragments from the fullscreen output pane before resetting checkpoint
    if _fullscreen_mode and _fullscreen_layout and _plan_checkpoint > 0:
        _fullscreen_layout.output.truncate_to(_plan_checkpoint)
        _streaming_checkpoint = _fullscreen_layout.output.checkpoint()
    _plan_steps = []
    _plan_visible = False
    _plan_written_lines = 0
    _plan_checkpoint = 0


def _plan_block_height() -> int:
    """Number of terminal lines the plan block occupies (0 if no plan)."""
    if not _plan_visible or not _plan_steps:
        return 0
    return len(_plan_steps) + 1  # header line + one line per step


def _collapse_plan() -> None:
    """Replace the plan checklist with a one-line summary.

    Called when the agentic run completes (done event).
    """
    global _plan_visible, _plan_written_lines
    if not _plan_steps:
        _plan_visible = False
        _plan_written_lines = 0
        return

    completed = sum(1 for s in _plan_steps if s["status"] == "complete")
    total = len(_plan_steps)

    if _fullscreen_mode and _fullscreen_layout:
        # In fullscreen, render as styled fragments to the output pane
        summary = f"  \u2713 Plan: {completed}/{total} steps complete\n"
        if completed == total:
            _fullscreen_layout.append_output_fragments([("class:status", summary)])
        else:
            summary = f"  \u25cb Plan: {completed}/{total} steps complete\n"
            _fullscreen_layout.append_output_fragments([("class:status.hint", summary)])
        if _fullscreen_invalidate:
            _fullscreen_invalidate()
    elif _repl_mode and _stdout:
        green = "\033[32m"
        muted = "\033[38;2;139;139;139m"
        rst = "\033[0m"
        if completed == total:
            line = f"  {green}\u2713 Plan: {completed}/{total} steps complete{rst}"
        else:
            line = f"  {muted}\u25cb Plan: {completed}/{total} steps complete{rst}"
        _stdout.write(f"{line}\n")
        _stdout.flush()

    _plan_visible = False
    _plan_written_lines = 0


def get_plan_steps() -> list[dict[str, str]]:
    """Return the current plan steps (for testing/inspection)."""
    return list(_plan_steps)


def is_plan_visible() -> bool:
    """Return whether a plan checklist is currently active."""
    return _plan_visible


# ---------------------------------------------------------------------------
# Verbosity
# ---------------------------------------------------------------------------


class Verbosity(Enum):
    COMPACT = "compact"
    DETAILED = "detailed"
    VERBOSE = "verbose"


_verbosity: Verbosity = Verbosity.COMPACT

# Tool call history for /detail replay
_tool_history: list[dict[str, Any]] = []
_current_turn_tools: list[dict[str, Any]] = []


def get_verbosity() -> Verbosity:
    return _verbosity


def set_verbosity(v: Verbosity) -> None:
    global _verbosity
    _verbosity = v


def set_tool_dedup(enabled: bool) -> None:
    global _tool_dedup_enabled
    _tool_dedup_enabled = enabled


def cycle_verbosity() -> Verbosity:
    global _verbosity
    order = [Verbosity.COMPACT, Verbosity.DETAILED, Verbosity.VERBOSE]
    idx = order.index(_verbosity)
    _verbosity = order[(idx + 1) % len(order)]
    return _verbosity


_fs_ai_turn_rendered: bool = False


def clear_turn_history() -> None:
    """Clear current turn tool history. Called at start of each turn."""
    global _fs_ai_turn_rendered, _streaming_buffer
    _current_turn_tools.clear()
    _fs_ai_turn_rendered = False
    _streaming_buffer = []


def save_turn_history() -> None:
    """Save current turn tools to history. Called at end of each turn."""
    global _tool_batch_active
    _flush_dedup()
    _tool_batch_active = False
    if _current_turn_tools:
        _tool_history.clear()
        _tool_history.extend(_current_turn_tools)


_SEPARATOR_WIDTH = 60


def render_user_turn(text: str) -> None:
    """Render a styled user turn separator in fullscreen mode."""
    if not (_fullscreen_mode and _fullscreen_layout):
        return
    truncated = text if len(text) <= 60 else text[:57] + "..."
    remaining = max(0, _SEPARATOR_WIDTH - 6)  # 6 = "─── " + "You" + space before dashes
    fragments: list[tuple[str, str]] = [
        ("class:separator", "\u2500\u2500\u2500 "),
        ("class:turn.user", "You"),
        ("class:separator", " " + "\u2500" * remaining + "\n"),
        ("class:turn.user.text", f"  {truncated}\n"),
        ("class:output", "\n"),
    ]
    _fullscreen_layout.append_output_fragments(fragments)
    if _fullscreen_invalidate:
        _fullscreen_invalidate()


def render_ai_turn_start() -> None:
    """Render a styled AI turn separator in fullscreen mode.

    Guarded by ``_fs_ai_turn_rendered`` flag to avoid duplicate separators
    when ``start_thinking()`` is called multiple times per turn.
    """
    global _fs_ai_turn_rendered
    if not (_fullscreen_mode and _fullscreen_layout):
        return
    if _fs_ai_turn_rendered:
        return
    _fs_ai_turn_rendered = True
    remaining = max(0, _SEPARATOR_WIDTH - 5)  # 5 = "─── " + "AI" + space before dashes
    fragments: list[tuple[str, str]] = [
        ("class:separator", "\u2500\u2500\u2500 "),
        ("class:turn.ai", "AI"),
        ("class:separator", " " + "\u2500" * remaining + "\n"),
    ]
    _fullscreen_layout.append_output_fragments(fragments)
    if _fullscreen_invalidate:
        _fullscreen_invalidate()


# ---------------------------------------------------------------------------
# Tool call summary helpers
# ---------------------------------------------------------------------------


def _humanize_tool(tool_name: str, arguments: dict[str, Any]) -> str:
    """Convert tool_name + args into a human-readable breadcrumb."""
    name_lower = tool_name.lower()

    # Built-in tools: extract the key argument
    if name_lower == "bash":
        cmd = arguments.get("command", "")
        if len(cmd) > 100:
            cmd = cmd[:97] + "..."
        return f"bash {cmd}"
    elif name_lower in ("file_read", "read_file"):
        path = arguments.get("path", arguments.get("file_path", ""))
        return f"Reading {_short_path(path)}"
    elif name_lower in ("file_write", "write_file"):
        path = arguments.get("path", arguments.get("file_path", ""))
        return f"Writing {_short_path(path)}"
    elif name_lower in ("file_edit", "edit_file"):
        path = arguments.get("path", arguments.get("file_path", ""))
        return f"Editing {_short_path(path)}"
    elif name_lower in ("grep", "search", "ripgrep"):
        pattern = arguments.get("pattern", arguments.get("query", ""))
        return f"Searching for '{pattern}'"
    elif name_lower in ("glob", "glob_files", "find_files"):
        pattern = arguments.get("pattern", "")
        return f"Finding {pattern}"
    elif name_lower == "run_agent":
        prompt = arguments.get("prompt", "")
        if len(prompt) > 60:
            prompt = prompt[:57] + "..."
        return f"Sub-agent: {prompt}"
    elif name_lower == "list_directory":
        path = arguments.get("path", ".")
        return f"Listing {_short_path(path)}"

    # MCP / unknown tools: show name + first string arg
    first_str = ""
    for v in arguments.values():
        if isinstance(v, str) and v:
            first_str = v
            if len(first_str) > 40:
                first_str = first_str[:37] + "..."
            break
    if first_str:
        return f"{tool_name} {first_str}"
    return tool_name


def _dedup_key_from_summary(summary: str) -> str:
    """Extract a dedup grouping key from a humanized tool summary.

    Groups by the action verb (e.g. "Editing", "Reading", "Writing", "bash")
    so consecutive edits to different files collapse together.
    """
    # Known action prefixes from _humanize_tool
    for prefix in ("Editing", "Reading", "Writing", "Searching", "Finding", "Listing", "Sub-agent:"):
        if summary.startswith(prefix):
            return prefix
    # bash commands: group all bash calls together
    if summary.startswith("bash "):
        return "bash"
    # MCP / unknown tools: use the tool name (first word)
    return summary.split(" ", 1)[0] if " " in summary else summary


def _dedup_flush_label(key: str, count: int) -> str:
    """Build a human-readable summary for a flushed dedup group."""
    verb_map = {
        "Editing": "edited",
        "Reading": "read",
        "Writing": "wrote",
        "Searching": "searched",
        "Finding": "found patterns in",
        "Listing": "listed",
        "bash": "ran",
    }
    verb = verb_map.get(key, f"called {key}")
    noun = "files" if key in ("Editing", "Reading", "Writing") else "times"
    return f"... {verb} {count} {noun} total"


def _short_path(path: str) -> str:
    """Shorten absolute path using ~ for home and cwd-relative."""
    if not path:
        return path
    home = os.path.expanduser("~")
    cwd = os.getcwd()
    # Try cwd-relative first
    try:
        rel = os.path.relpath(path, cwd)
        if not rel.startswith(".."):
            return rel
    except ValueError:
        pass
    # Fall back to ~-relative
    if path.startswith(home):
        return "~" + path[len(home) :]
    return path


def _format_tokens(n: int) -> str:
    """Format token count: 1234 -> '1.2k', 128000 -> '128k'."""
    if n >= 1000:
        k = n / 1000
        if k >= 10:
            return f"{k:.0f}k"
        return f"{k:.1f}k"
    return str(n)


def _error_summary(output: Any) -> str:
    """Extract a one-line error summary from tool output."""
    if not isinstance(output, dict):
        return ""
    err = output.get("error", "")
    if err:
        # First line only, truncated
        first_line = str(err).split("\n")[0]
        if len(first_line) > 80:
            first_line = first_line[:77] + "..."
        return first_line
    return ""


def _output_summary(output: Any) -> str:
    """Extract a brief output summary for detailed mode."""
    if not isinstance(output, dict):
        return ""
    if "error" in output:
        return _error_summary(output)
    if "content" in output:
        content = output["content"]
        if isinstance(content, str):
            lines = content.count("\n") + 1
            chars = len(content)
            if chars > 80:
                return f"{lines} lines, {chars:,} chars"
            # Short enough to show inline
            oneline = content.replace("\n", " ").strip()
            if len(oneline) > 60:
                return oneline[:57] + "..."
            return oneline
    if "stdout" in output:
        stdout = output.get("stdout", "")
        if stdout:
            lines = stdout.count("\n") + 1
            oneline = stdout.split("\n")[0].strip()
            if lines > 1:
                if len(oneline) > 40:
                    oneline = oneline[:37] + "..."
                return f"{oneline} (+{lines - 1} lines)"
            if len(oneline) > 60:
                return oneline[:57] + "..."
            return oneline
    return ""


# ---------------------------------------------------------------------------
# Thinking spinner
# ---------------------------------------------------------------------------


_repl_mode: bool = False


async def _thinking_ticker() -> None:
    """Background task that updates the thinking spinner every 0.5s."""
    try:
        while True:
            await asyncio.sleep(0.5)
            if _thinking_start:
                elapsed = time.monotonic() - _thinking_start
                suffix = _phase_suffix(elapsed)
                if _spinner:
                    label = f"[{GOLD}]Thinking...[/] [{CHROME}]{elapsed:.0f}s[/{CHROME}]"
                    if suffix:
                        label += f"  [{MUTED}]{suffix}[/{MUTED}]"
                    _spinner.update(label)
                elif _repl_mode:
                    _write_thinking_line(elapsed)
    except asyncio.CancelledError:
        return


def start_thinking(*, newline: bool = False) -> None:
    """Show a spinner with timer while AI is generating.

    In REPL mode, pass ``newline=True`` on the first call per user message
    so the visual separator and "Thinking..." text are written as a single
    atomic write, preventing prompt_toolkit's cursor teardown from
    interleaving between them (#249).  Retry calls should omit it.
    """
    global _thinking_start, _spinner, _last_spinner_update, _tool_batch_active, _thinking_ticker_task
    global _thinking_phase, _thinking_tokens, _streaming_chars, _last_chunk_time, _phase_start_time, _retrying_info
    global _plan_written_lines, _streaming_checkpoint
    _flush_dedup()
    _tool_batch_active = False
    _thinking_start = time.monotonic()
    _thinking_phase = ""
    _thinking_tokens = 0
    _streaming_chars = 0
    _last_chunk_time = 0
    _phase_start_time = _thinking_start
    _retrying_info = {}
    _last_spinner_update = _thinking_start
    # Reset plan written lines — the block will be freshly written
    _plan_written_lines = 0
    if _fullscreen_mode and _fullscreen_layout:
        # Fullscreen: render AI separator on first call, update status line
        render_ai_turn_start()
        _write_thinking_line(0.0)
        _spinner = None
    elif _repl_mode:
        # Rich Status conflicts with prompt_toolkit's patch_stdout, so
        # we write a plain "Thinking..." line and overwrite it in-place
        # via ANSI escape codes as the timer ticks.
        if newline and _stdout:
            # Atomic \n + initial thinking block prevents prompt_toolkit race (#249).
            gold = "\033[38;2;197;160;89m"
            rst = "\033[0m"
            if _plan_visible and _plan_steps:
                # Write newline then full plan + thinking block
                _stdout.write("\n")
                _stdout.flush()
                _write_thinking_block(0.0)
            else:
                _stdout.write(f"\n\r\033[2K{gold}Thinking...{rst}")
                _stdout.flush()
        else:
            _write_thinking_line(0.0)
        _spinner = None
    else:
        _spinner = Status(f"[{GOLD}]Thinking...[/]", console=console, spinner="dots12")
        _spinner.start()
    # Cancel any existing ticker before creating a new one (prevents task leak)
    if _thinking_ticker_task is not None:
        _thinking_ticker_task.cancel()
        _thinking_ticker_task = None
    # Start background ticker
    try:
        loop = asyncio.get_running_loop()
        _thinking_ticker_task = loop.create_task(_thinking_ticker())
    except RuntimeError:
        _thinking_ticker_task = None


def _build_thinking_text(
    elapsed: float,
    *,
    error_msg: str = "",
    countdown: int = 0,
    cancel_msg: str = "",
) -> str:
    """Build the thinking line text (without cursor/clear prefixes)."""
    gold = "\033[38;2;197;160;89m"
    timer_c = "\033[38;2;107;114;128m"
    muted = "\033[38;2;139;139;139m"
    err_c = "\033[38;2;205;107;107m"  # ERROR_RED #CD6B6B
    rst = "\033[0m"

    if elapsed < 0.5 and not error_msg and not cancel_msg:
        return f"{gold}Thinking...{rst}"

    timer = f"{timer_c}{elapsed:.0f}s{rst}"
    if cancel_msg:
        return f"{gold}Thinking...{rst} {timer}  {muted}{cancel_msg}{rst}"
    if error_msg:
        err_text = f"  {err_c}{error_msg}{rst}"
        if countdown > 0:
            retry_text = f" \u00b7 {muted}retrying in {countdown}s{rst}"
            hint = f"  {muted}esc to give up{rst}"
            return f"{gold}Thinking...{rst} {timer}{err_text}{retry_text}{hint}"
        return f"{gold}Thinking...{rst} {timer}{err_text}"

    hint = f"  {muted}esc to cancel{rst}" if elapsed >= _ESC_HINT_DELAY else ""
    suffix = _phase_suffix(elapsed)
    phase_text = f"  {muted}{suffix}{rst}" if suffix else ""
    return f"{gold}Thinking...{rst} {timer}{phase_text}{hint}"


def _write_thinking_block(
    elapsed: float,
    *,
    error_msg: str = "",
    countdown: int = 0,
    cancel_msg: str = "",
) -> None:
    """Write the full thinking block: plan checklist (if active) + thinking line.

    Uses cursor-up ANSI codes to redraw the block in place.
    """
    global _plan_written_lines
    if not _stdout:
        return

    height = _plan_block_height()
    thinking_text = _build_thinking_text(elapsed, error_msg=error_msg, countdown=countdown, cancel_msg=cancel_msg)

    if height == 0:
        # No plan — single-line thinking only
        _stdout.write(f"\r\033[2K{thinking_text}")
        _stdout.flush()
        return

    # Multi-line block: plan header + steps + thinking line
    buf: list[str] = []

    # Move cursor up to the top of the block (if we've written it before)
    if _plan_written_lines > 0:
        up = _plan_written_lines  # lines above the thinking line
        buf.append(f"\033[{up}A")

    # ANSI colors
    green = "\033[32m"
    gold_c = "\033[38;2;197;160;89m"
    muted_c = "\033[38;2;139;139;139m"
    rst = "\033[0m"

    # Plan header
    buf.append(f"\r\033[2K  {muted_c}\U0001f4cb Plan{rst}\n")

    # Steps
    for step in _plan_steps:
        status = step["status"]
        if status == "complete":
            icon = f"{green}\u2713{rst}"
            text_style = green
        elif status == "in_progress":
            icon = f"{gold_c}\u2192{rst}"
            text_style = gold_c
        else:
            icon = f"{muted_c}\u25cb{rst}"
            text_style = muted_c
        buf.append(f"\r\033[2K    {icon} {text_style}{step['text']}{rst}\n")

    # Thinking line (no trailing newline — cursor stays here)
    buf.append(f"\r\033[2K{thinking_text}")

    _stdout.write("".join(buf))
    _stdout.flush()
    _plan_written_lines = height  # remember how many plan lines we wrote


def _build_status_fragments(
    elapsed: float,
    *,
    error_msg: str = "",
    countdown: int = 0,
    cancel_msg: str = "",
) -> list[tuple[str, str]]:
    """Build prompt_toolkit fragments for the fullscreen status line."""
    parts: list[tuple[str, str]] = [("class:status", " Thinking... ")]
    if elapsed >= 0.5:
        parts.append(("class:status.timer", f"{elapsed:.0f}s"))
    if cancel_msg:
        parts.append(("class:status.hint", f"  {cancel_msg}"))
    elif error_msg:
        parts.append(("class:status", f"  {error_msg}"))
        if countdown > 0:
            parts.append(("class:status.hint", f" \u00b7 retrying in {countdown}s"))
            parts.append(("class:status.hint", "  esc to give up"))
    else:
        suffix = _phase_suffix(elapsed)
        if suffix:
            parts.append(("class:status.phase", f"  {suffix}"))
        if elapsed >= _ESC_HINT_DELAY:
            parts.append(("class:status.hint", "  esc to cancel"))
    return parts


def _write_thinking_line(
    elapsed: float,
    *,
    error_msg: str = "",
    countdown: int = 0,
    cancel_msg: str = "",
) -> None:
    """Overwrite the current line with Thinking + elapsed timer + phase status.

    When a plan checklist is active, delegates to ``_write_thinking_block()``
    to render the full plan + thinking block.

    In fullscreen mode, updates the layout's status line instead of writing
    raw ANSI cursor codes.

    Optional keyword args for special states:
    - ``error_msg``: pale-red inline error replacing phase text
    - ``countdown``: seconds remaining for auto-retry (shown after error_msg)
    - ``cancel_msg``: muted message like "cancelled" (user-initiated, not error)
    """
    if _fullscreen_mode and _fullscreen_layout:
        fragments = _build_status_fragments(elapsed, error_msg=error_msg, countdown=countdown, cancel_msg=cancel_msg)
        _fullscreen_layout.set_status(fragments)
        if _fullscreen_invalidate:
            _fullscreen_invalidate()
        return

    if _plan_visible and _plan_steps:
        _write_thinking_block(elapsed, error_msg=error_msg, countdown=countdown, cancel_msg=cancel_msg)
        return

    text = f"\r\033[2K{_build_thinking_text(elapsed, error_msg=error_msg, countdown=countdown, cancel_msg=cancel_msg)}"
    if _stdout:
        _stdout.write(text)
        _stdout.flush()


def update_thinking() -> None:
    """Update the spinner timer (throttled to once per second).

    No-op when the background ticker is running — the ticker handles updates.
    """
    global _last_spinner_update
    if _thinking_ticker_task is not None:
        return
    if _spinner:
        now = time.monotonic()
        if now - _last_spinner_update >= 1.0:
            elapsed = now - _thinking_start
            _spinner.update(f"[{GOLD}]Thinking...[/] [{CHROME}]{elapsed:.0f}s[/{CHROME}]")
            _last_spinner_update = now
    elif _repl_mode:
        now = time.monotonic()
        if now - _last_spinner_update >= 1.0:
            elapsed = now - _thinking_start
            _write_thinking_line(elapsed)
            _last_spinner_update = now


async def stop_thinking(
    *,
    error_msg: str = "",
    cancel_msg: str = "",
    collapse_plan: bool = False,
) -> float:
    """Stop the spinner, return elapsed seconds.

    Awaits ticker task termination to prevent output races.

    Optional keyword args control the final thinking line:
    - ``error_msg``: pale-red inline error (system failure)
    - ``cancel_msg``: muted message (user-initiated cancel)
    - ``collapse_plan``: if True, collapse the plan to a one-line summary
    - Neither: clean final line (just "Thinking... Ns")
    """
    global _spinner, _thinking_ticker_task, _thinking_phase, _plan_written_lines, _thinking_start
    elapsed = 0.0
    # Await ticker termination to prevent race conditions
    if _thinking_ticker_task is not None:
        _thinking_ticker_task.cancel()
        try:
            await _thinking_ticker_task
        except (asyncio.CancelledError, Exception):
            pass
        _thinking_ticker_task = None
    if _fullscreen_mode and _fullscreen_layout:
        elapsed = time.monotonic() - _thinking_start
        _fullscreen_layout.clear_status()
        if _fullscreen_invalidate:
            _fullscreen_invalidate()
    elif _spinner:
        elapsed = time.monotonic() - _thinking_start
        _spinner.stop()
        _spinner = None
    else:
        elapsed = time.monotonic() - _thinking_start
        if _repl_mode and _stdout:
            # Clear the plan block if it's on screen
            if _plan_written_lines > 0:
                # Move cursor up to the top of the plan block
                _stdout.write(f"\033[{_plan_written_lines}A")
                # Clear all plan lines + thinking line
                for _ in range(_plan_written_lines + 1):
                    _stdout.write("\r\033[2K\n")
                # Move back up one line (we wrote one too many \n)
                _stdout.write("\033[1A")
                _plan_written_lines = 0

            if collapse_plan:
                _collapse_plan()

            if error_msg:
                _write_thinking_line(elapsed, error_msg=error_msg)
                _stdout.write("\n")
                _stdout.flush()
            elif cancel_msg:
                _write_thinking_line(elapsed, cancel_msg=cancel_msg)
                _stdout.write("\n")
                _stdout.flush()
            else:
                # Clean final line: just "Thinking... Ns" — no phase, no hint.
                _thinking_phase = ""
                gold = "\033[38;2;197;160;89m"
                timer_c = "\033[38;2;107;114;128m"
                rst = "\033[0m"
                _stdout.write(f"\r\033[2K{gold}Thinking...{rst} {timer_c}{elapsed:.0f}s{rst}\n")
                _stdout.flush()
    _thinking_start = 0
    return elapsed


def stop_thinking_sync() -> float:
    """Synchronous fallback for stop_thinking (KeyboardInterrupt handlers).

    Does not await the ticker — use only when an event loop is unavailable.
    """
    global _spinner, _thinking_ticker_task, _plan_written_lines, _thinking_start
    elapsed = 0.0
    if _thinking_ticker_task is not None:
        _thinking_ticker_task.cancel()
        _thinking_ticker_task = None
    if _fullscreen_mode and _fullscreen_layout:
        elapsed = time.monotonic() - _thinking_start
        _fullscreen_layout.clear_status()
        if _fullscreen_invalidate:
            _fullscreen_invalidate()
    elif _spinner:
        elapsed = time.monotonic() - _thinking_start
        _spinner.stop()
        _spinner = None
    else:
        elapsed = time.monotonic() - _thinking_start
        if _repl_mode and _stdout:
            # Clear plan block if present
            if _plan_written_lines > 0:
                _stdout.write(f"\033[{_plan_written_lines}A")
                for _ in range(_plan_written_lines + 1):
                    _stdout.write("\r\033[2K\n")
                _stdout.write("\033[1A")
                _plan_written_lines = 0
            _stdout.write("\r\033[2K")
            _stdout.flush()
    _thinking_start = 0
    return elapsed


async def thinking_countdown(
    delay: float,
    cancel_event: "asyncio.Event",
    error_msg: str,
) -> bool:
    """Show a retry countdown on the thinking line after a system error.

    Ticks once per second displaying ``error_msg · retrying in Ns``.
    Returns ``True`` if countdown completed (caller should retry),
    ``False`` if ``cancel_event`` fired (caller should give up).
    """
    global _thinking_ticker_task
    # Stop the background ticker so it doesn't race with countdown writes (#245)
    if _thinking_ticker_task is not None:
        _thinking_ticker_task.cancel()
        try:
            await _thinking_ticker_task
        except (asyncio.CancelledError, Exception):
            pass
        _thinking_ticker_task = None
    remaining = int(delay)
    while remaining > 0:
        elapsed = time.monotonic() - _thinking_start if _thinking_start else 0.0
        if _repl_mode and _stdout:
            _write_thinking_line(elapsed, error_msg=error_msg, countdown=remaining)
        try:
            await asyncio.wait_for(cancel_event.wait(), timeout=1.0)
            # cancel_event fired — give up
            if _repl_mode and _stdout:
                _write_thinking_line(elapsed, cancel_msg="cancelled")
                if not _fullscreen_mode:
                    _stdout.write("\n")
                    _stdout.flush()
            return False
        except asyncio.TimeoutError:
            remaining -= 1
    return True


_retrying_info: dict[str, Any] = {}


def set_thinking_phase(phase: str) -> None:
    """Update the current lifecycle phase displayed by the thinking ticker."""
    global _thinking_phase, _last_chunk_time, _phase_start_time
    _thinking_phase = phase
    _phase_start_time = time.monotonic()
    _last_chunk_time = time.monotonic()


def set_retrying(data: dict[str, Any]) -> None:
    """Update retry state displayed by the thinking ticker."""
    global _thinking_phase, _retrying_info
    _retrying_info = data
    _thinking_phase = "retrying"


def increment_thinking_tokens() -> None:
    """Increment the streaming token counter and mark chunk arrival time.

    Calling this implicitly transitions to the 'streaming' phase.
    """
    global _thinking_tokens, _thinking_phase, _last_chunk_time, _phase_start_time
    _thinking_tokens += 1
    # Set chunk time before phase to avoid a race with the background ticker:
    # if the ticker reads _thinking_phase=="streaming" before _last_chunk_time
    # is updated, it could briefly show "stalled" on a fresh phase transition.
    _last_chunk_time = time.monotonic()
    if _thinking_phase != "streaming":
        _phase_start_time = _last_chunk_time
    _thinking_phase = "streaming"


def increment_streaming_chars(n: int) -> None:
    """Accumulate character count during streaming for the health display."""
    global _streaming_chars
    _streaming_chars += n


def _phase_elapsed_str() -> str:
    """Return per-phase elapsed as ``(Ns)`` when > 1s, else empty string."""
    if not _phase_start_time:
        return ""
    phase_secs = time.monotonic() - _phase_start_time
    if phase_secs >= 1.5:
        return f" ({phase_secs:.0f}s)"
    return ""


def _phase_suffix(elapsed: float) -> str:
    """Build the dim phase text appended to the thinking line.

    Connection health status is always shown (all verbosity levels).
    Returns an empty string only when no phase is set.
    """
    if not _thinking_phase:
        return ""
    phase = _thinking_phase
    pe = _phase_elapsed_str()
    if phase == "connecting":
        return f"connecting{pe}"
    if phase == "waiting":
        return f"connected · waiting for first token{pe}"
    if phase == "streaming":
        now = time.monotonic()
        if _last_chunk_time and now - _last_chunk_time > _MID_STREAM_STALL:
            stall_secs = now - _last_chunk_time
            return f"streaming · {_streaming_chars:,} chars · stalled {stall_secs:.0f}s"
        return f"streaming · {_streaming_chars:,} chars"
    if phase == "retrying":
        attempt = _retrying_info.get("attempt", 2)
        max_attempts = _retrying_info.get("max_attempts", 3)
        return f"retry {attempt}/{max_attempts}"
    return phase


# ---------------------------------------------------------------------------
# Token / response rendering
# ---------------------------------------------------------------------------


def _make_markdown(text: str) -> Markdown:
    """Create a Markdown renderable with left-aligned headings."""
    _patch_heading_left()
    return Markdown(text)


_heading_patched = False


def _patch_heading_left() -> None:
    """Monkey-patch Rich's Heading to render left-aligned instead of centered."""
    global _heading_patched
    if _heading_patched:
        return
    from rich.markdown import Heading

    def _left_aligned(self: Any, console: Any, options: Any) -> Any:
        self.text.justify = "left"
        if self.tag == "h2":
            yield Text("")
        yield self.text

    Heading.__rich_console__ = _left_aligned  # type: ignore[method-assign]
    _heading_patched = True


def flush_buffered_text() -> None:
    """Flush any buffered AI text to screen immediately.

    Called before tool calls start so the AI's task explanation
    (e.g. 'Let me review your auth files') renders before the tool output.

    In fullscreen mode, stops the streaming cursor first to prevent
    stale checkpoint issues where subsequent token updates would truncate
    already-rendered content (tool frames, Markdown text).
    """
    global _streaming_buffer, _streaming_checkpoint, _tool_batch_active
    # Save whether streaming was active before stopping — we need this to
    # decide whether to truncate raw fragments. Cannot use _streaming_checkpoint
    # value alone since checkpoint 0 is valid (empty pane at conversation start).
    had_active_cursor = _streaming_cursor_active
    _stop_streaming_cursor()
    text = "".join(_streaming_buffer)
    _streaming_buffer = []
    if not text.strip():
        return

    # Add spacing after tool call block before narration text.
    # This handles mid-turn narration; render_response_end() handles end-of-turn.
    if _tool_batch_active:
        console.print()
        _tool_batch_active = False

    # In fullscreen, truncate raw streamed text before rendering Markdown.
    if _fullscreen_mode and _fullscreen_layout and had_active_cursor:
        _fullscreen_layout.output.truncate_to(_streaming_checkpoint)

    from rich.padding import Padding

    _stdout_console.print(Padding(_make_markdown(text), (0, 2, 0, 2)))

    # Update checkpoint so subsequent streaming starts after this rendered block
    if _fullscreen_mode and _fullscreen_layout:
        _streaming_checkpoint = _fullscreen_layout.output.checkpoint()


def _flush_dedup() -> None:
    """Flush accumulated dedup counter if needed."""
    global _dedup_key, _dedup_count, _dedup_first_summary, _dedup_summary
    if _dedup_count > 1:
        label = _dedup_flush_label(_dedup_key, _dedup_count)
        console.print(f"    [{MUTED}]{label}[/{MUTED}]")
    _dedup_key = ""
    _dedup_count = 0
    _dedup_first_summary = ""
    _dedup_summary = ""


# Streaming cursor state — checkpoint/truncate pattern for live cursor
_streaming_cursor_active: bool = False
_streaming_checkpoint: int = 0  # fragment count before cursor


def _start_streaming_cursor() -> None:
    """Begin showing a gold block cursor at the end of streaming text."""
    global _streaming_cursor_active, _streaming_checkpoint
    if not (_fullscreen_mode and _fullscreen_layout):
        return
    _streaming_cursor_active = True
    _streaming_checkpoint = _fullscreen_layout.output.fragment_count


def _update_streaming_cursor() -> None:
    """Update the streaming cursor position — truncate to checkpoint, re-append text + cursor."""
    if not (_streaming_cursor_active and _fullscreen_mode and _fullscreen_layout):
        return
    output = _fullscreen_layout.output
    output.truncate_to(_streaming_checkpoint)
    text = "".join(_streaming_buffer)
    if text:
        output.append_text(text, "class:output")
    output.append_text("\u258a", "class:streaming.cursor")
    if _fullscreen_invalidate:
        _fullscreen_invalidate()


def _stop_streaming_cursor() -> None:
    """Remove the streaming cursor glyph and raw text, preserving checkpoint for render_response_end."""
    global _streaming_cursor_active
    if not _streaming_cursor_active:
        return
    if _fullscreen_mode and _fullscreen_layout:
        _fullscreen_layout.output.truncate_to(_streaming_checkpoint)
    _streaming_cursor_active = False


def render_token(content: str) -> None:
    """Buffer token content silently (no streaming output).

    In fullscreen mode, also updates the live streaming cursor.
    """
    _streaming_buffer.append(content)
    if _fullscreen_mode and _fullscreen_layout:
        if not _streaming_cursor_active:
            _start_streaming_cursor()
        _update_streaming_cursor()


def render_response_end() -> None:
    """Render the complete buffered response with Rich Markdown."""
    global _streaming_buffer, _tool_batch_active, _streaming_checkpoint
    _stop_streaming_cursor()
    _flush_dedup()

    full_text = "".join(_streaming_buffer)
    _streaming_buffer = []

    if not full_text.strip():
        _streaming_checkpoint = 0
        _tool_batch_active = False
        return

    # Add spacing after tool call block before AI response
    if _tool_batch_active:
        console.print()
        _tool_batch_active = False

    _streaming_checkpoint = 0

    from rich.padding import Padding

    _stdout_console.print(Padding(_make_markdown(full_text), (0, 2, 0, 2)))


def render_newline() -> None:
    if _stdout:
        _stdout.write("\n")
        _stdout.flush()


# ---------------------------------------------------------------------------
# Inline diff rendering (Claude Code-style)
# ---------------------------------------------------------------------------

_DIFF_CONTEXT_LINES = 3  # lines of context around each change
_DIFF_RED_BG = "on #3d1418"  # dark red background for removed lines
_DIFF_GREEN_BG = "on #132a13"  # dark green background for added lines
_DIFF_LINE_NO = "#6b7280"  # dim line numbers


def _render_inline_diff(tool_name: str, output: dict[str, Any]) -> None:
    """Render Claude Code-style color-coded inline diff for file changes.

    Requires ``_old_content`` and/or ``_new_content`` keys in the output dict.
    """
    import difflib

    old_content: str | None = output.get("_old_content")
    new_content: str | None = output.get("_new_content")
    file_path = output.get("path", "")
    short = _short_path(file_path) if file_path else tool_name

    # Determine action label
    action = output.get("action", "")
    if action == "created":
        lines = output.get("lines", 0)
        header_text = Text()
        header_text.append("  ● ", style="green")
        header_text.append(f"Write({short})", style="bold")
        console.print(header_text)
        summary_text = Text()
        summary_text.append(f"  └ Created, {lines} lines", style=MUTED)
        console.print(summary_text)
        return

    if old_content is None or new_content is None:
        return

    # Compute diff
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    # Ensure trailing newline for clean diffing
    if old_lines and not old_lines[-1].endswith("\n"):
        old_lines[-1] += "\n"
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"

    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
    if len(diff) < 3:
        return  # no changes

    # Count added/removed
    added = sum(1 for line in diff[2:] if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff[2:] if line.startswith("-") and not line.startswith("---"))

    # Header
    label = "Update" if tool_name.lower() in ("edit_file", "file_edit") else "Write"
    header_text = Text()
    header_text.append("  ● ", style="green")
    header_text.append(f"{label}({short})", style="bold")
    console.print(header_text)

    summary_text = Text()
    summary_text.append("  └ ", style=MUTED)
    summary_text.append(f"Added {added} lines", style="green") if added else None
    if added and removed:
        summary_text.append(", ", style=MUTED)
    summary_text.append(f"removed {removed} lines", style="red") if removed else None
    if not added and not removed:
        summary_text.append("no line changes", style=MUTED)
    console.print(summary_text)

    # Parse hunks from unified diff and render with context collapsing
    _render_diff_hunks(diff, old_lines, new_lines)


def _render_diff_hunks(diff: list[str], old_lines: list[str], new_lines: list[str]) -> None:
    """Parse unified diff output and render color-coded hunks with line numbers."""
    import re

    hunk_header_re = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    hunks: list[tuple[int, int, list[tuple[str, str]]]] = []  # (old_start, new_start, lines)

    current_hunk: list[tuple[str, str]] = []
    old_start = new_start = 0

    for line in diff[2:]:  # skip --- and +++ headers
        m = hunk_header_re.match(line)
        if m:
            if current_hunk:
                hunks.append((old_start, new_start, current_hunk))
            old_start = int(m.group(1))
            new_start = int(m.group(2))
            current_hunk = []
        elif line.startswith("+"):
            current_hunk.append(("+", line[1:]))
        elif line.startswith("-"):
            current_hunk.append(("-", line[1:]))
        elif line.startswith(" "):
            current_hunk.append((" ", line[1:]))
    if current_hunk:
        hunks.append((old_start, new_start, current_hunk))

    for i, (old_start, new_start, hunk_lines) in enumerate(hunks):
        if i > 0:
            console.print(f"    [{MUTED}]...[/{MUTED}]")

        old_num = old_start
        new_num = new_start
        for tag, content in hunk_lines:
            # Truncate long lines for display
            display = content.rstrip("\n")
            if len(display) > 120:
                display = display[:117] + "..."

            if tag == "-":
                line_text = Text()
                line_text.append(f"    {old_num:>4} ", style=_DIFF_LINE_NO)
                line_text.append(f" {display} ", style=_DIFF_RED_BG)
                console.print(line_text)
                old_num += 1
            elif tag == "+":
                line_text = Text()
                line_text.append(f"    {new_num:>4} ", style=_DIFF_LINE_NO)
                line_text.append(f" {display} ", style=_DIFF_GREEN_BG)
                console.print(line_text)
                new_num += 1
            else:
                line_text = Text()
                line_text.append(f"    {new_num:>4} ", style=_DIFF_LINE_NO)
                line_text.append(f" {display}", style=MUTED)
                console.print(line_text)
                old_num += 1
                new_num += 1


def _has_diff_data(tool_name: str, output: Any) -> bool:
    """Check if tool output contains diff rendering data."""
    if not isinstance(output, dict):
        return False
    name = tool_name.lower()
    if name not in ("write_file", "file_write", "edit_file", "file_edit"):
        return False
    return "_new_content" in output or "_old_content" in output


# ---------------------------------------------------------------------------
# Tool elapsed timer (mirrors _thinking_ticker for tool execution)
# ---------------------------------------------------------------------------


async def _tool_ticker() -> None:
    """Background task that updates tool elapsed time every 0.5s."""
    try:
        while True:
            await asyncio.sleep(0.5)
            if _tool_start:
                elapsed = time.monotonic() - _tool_start
                if _fullscreen_mode and _fullscreen_layout:
                    _fullscreen_layout.set_status(
                        [
                            ("class:status.phase", f"  {_tool_ticker_summary}  "),
                            ("class:status.timer", f"{elapsed:.0f}s"),
                        ]
                    )
                    if _fullscreen_invalidate:
                        _fullscreen_invalidate()
                elif _tool_spinner:
                    label = f"  [{MUTED}]{escape(_tool_ticker_summary)}  {elapsed:.0f}s[/{MUTED}]"
                    _tool_spinner.update(label)
                elif _repl_mode and _stdout:
                    muted = "\033[38;2;139;139;139m"
                    rst = "\033[0m"
                    _stdout.write(f"\r\033[2K{muted}  {_tool_ticker_summary}  {elapsed:.0f}s{rst}")
                    _stdout.flush()
    except asyncio.CancelledError:
        return


def start_tool_ticker(summary: str) -> None:
    """Start a live elapsed timer for the current tool call."""
    global _tool_ticker_task, _tool_ticker_summary, _tool_spinner
    _tool_ticker_summary = summary
    if _tool_ticker_task is not None:
        _tool_ticker_task.cancel()
        _tool_ticker_task = None
    if not _repl_mode:
        _tool_spinner = Status(f"  [{MUTED}]{escape(summary)}[/{MUTED}]", console=console, spinner="dots12")
        _tool_spinner.start()
    try:
        loop = asyncio.get_running_loop()
        _tool_ticker_task = loop.create_task(_tool_ticker())
    except RuntimeError:
        _tool_ticker_task = None


def stop_tool_ticker_sync() -> None:
    """Stop the tool ticker synchronously (safe from sync render_tool_call_end)."""
    global _tool_ticker_task, _tool_spinner
    if _tool_ticker_task is not None:
        _tool_ticker_task.cancel()
        _tool_ticker_task = None
    if _fullscreen_mode and _fullscreen_layout:
        _fullscreen_layout.clear_status()
        if _fullscreen_invalidate:
            _fullscreen_invalidate()
    elif _tool_spinner:
        _tool_spinner.stop()
        _tool_spinner = None
    elif _repl_mode and _stdout:
        _stdout.write("\r\033[2K")
        _stdout.flush()


# ---------------------------------------------------------------------------
# Tool call rendering (verbosity-aware)
# ---------------------------------------------------------------------------


def render_tool_call_start(tool_name: str, arguments: dict[str, Any]) -> None:
    """Show tool call breadcrumb. Static print (no live spinner) for terminal compatibility."""
    global _tool_start, _tool_batch_active

    # Flush any buffered AI text so task explanations appear before tool output
    flush_buffered_text()

    summary = _humanize_tool(tool_name, arguments)

    _tool_start = time.monotonic()

    # Store for history — include start time per-tool so parallel tool
    # calls get correct elapsed times (the global _tool_start gets
    # overwritten by each subsequent start).
    _current_turn_tools.append(
        {
            "tool_name": tool_name,
            "arguments": arguments,
            "summary": summary,
            "status": "running",
            "output": None,
            "start_time": _tool_start,
        }
    )

    if _fullscreen_mode and _fullscreen_layout:
        # Fullscreen: defer all visual output to render_tool_call_end().
        # Parallel tool calls (asyncio.as_completed) cause starts to stack
        # before results arrive, disconnecting frames from their results.
        # Instead, render a single self-contained line per tool at completion.
        _tool_batch_active = True
        if tool_name not in ("ask_user", "ask_human"):
            start_tool_ticker(summary)
        return

    # Add spacing before the first tool call in a batch
    if not _tool_batch_active:
        console.print()
        _tool_batch_active = True

    if _verbosity == Verbosity.VERBOSE:
        # Full output: tool name + raw args
        args_str = json.dumps(arguments, indent=None, default=str)
        if len(args_str) > 200:
            args_str = args_str[:200] + "..."
        console.print(f"  [{CHROME}]> {escape(tool_name)}({escape(args_str)})[/{CHROME}]")

    # Start live elapsed timer — skip for interactive tools that use the terminal.
    # Stop any existing ticker first so it doesn't keep printing during input.
    if tool_name in ("ask_user", "ask_human"):
        stop_tool_ticker_sync()
    else:
        start_tool_ticker(summary)


def render_tool_call_end(tool_name: str, status: str, output: Any) -> None:
    """Show tool call result. Style depends on verbosity."""
    stop_tool_ticker_sync()

    # Update history — find the first *running* entry that matches tool_name.
    # Using [-1] would grab the wrong entry when parallel tools complete
    # out of order (asyncio.as_completed returns fastest-first).
    matched_entry = None
    for entry in _current_turn_tools:
        if entry["tool_name"] == tool_name and entry["status"] == "running":
            matched_entry = entry
            break
    if matched_entry is None and _current_turn_tools:
        # Fallback: no running match (e.g. duplicate tool names all completed)
        matched_entry = _current_turn_tools[-1]

    # Use per-tool start time for accurate parallel elapsed calculation
    start = matched_entry.get("start_time", _tool_start) if matched_entry else _tool_start
    elapsed = time.monotonic() - start if start else 0

    if matched_entry:
        matched_entry["status"] = status
        matched_entry["output"] = output
        matched_entry["elapsed"] = elapsed

    summary = matched_entry["summary"] if matched_entry else tool_name

    if _fullscreen_mode and _fullscreen_layout:
        # Fullscreen: single self-contained line per tool call.
        # No separate frame — parallel tools (asyncio.as_completed) would
        # stack all frames before any results, making them useless.
        if status == "success":
            icon_style, icon = "class:tool.ok", "  \u2713 "
        else:
            icon_style, icon = "class:tool.err", "  \u2717 "
        elapsed_str = f" ({elapsed:.1f}s)" if elapsed >= 0.1 else ""
        detail = _output_summary(output) if status == "success" else _error_summary(output)
        fragments: list[tuple[str, str]] = [
            (icon_style, icon),
            ("class:tool.name", summary),
            ("class:tool.elapsed", elapsed_str),
        ]
        if detail:
            fragments.append(("class:tool.detail", f" \u2014 {detail}"))
        fragments.append(("class:output", "\n"))
        _fullscreen_layout.append_output_fragments(fragments)
        # Inline diff rendering still works via OutputPaneWriter
        if status == "success" and _has_diff_data(tool_name, output):
            _render_inline_diff(tool_name, output)
        if _fullscreen_invalidate:
            _fullscreen_invalidate()
        return

    if _verbosity == Verbosity.VERBOSE:
        # Legacy-style
        if status == "success":
            style = "green"
        else:
            style = "red"
        output_str = ""
        if isinstance(output, dict):
            if "error" in output:
                output_str = f" - {output['error']}"
            elif "content" in output:
                content = output["content"]
                if isinstance(content, str) and len(content) > 200:
                    content = content[:200] + "..."
                output_str = f" - {content}"
            elif "stdout" in output:
                stdout = output["stdout"]
                if stdout and len(stdout) > 200:
                    stdout = stdout[:200] + "..."
                output_str = f" - {stdout}" if stdout else ""
        text = Text(f"  < {tool_name}: {status}{output_str}", style=style)
        console.print(text)
        return

    # Build the result line
    global _dedup_key, _dedup_count, _dedup_first_summary, _dedup_summary
    status_icon = "[green]  ✓[/green]" if status == "success" else "[red]  ✗[/red]"
    elapsed_str = f" {elapsed:.1f}s" if elapsed >= 0.1 else ""

    # Dedup: collapse consecutive similar tool calls (compact/detailed only)
    key = _dedup_key_from_summary(summary) if _tool_dedup_enabled else ""
    if _tool_dedup_enabled and status == "success" and key == _dedup_key and _dedup_count >= 1:
        _dedup_count += 1
        return

    # Different tool type or first occurrence — flush previous dedup, print new line
    _flush_dedup()

    # Inline diff for file-modifying tools (all verbosity levels)
    if status == "success" and _has_diff_data(tool_name, output):
        _render_inline_diff(tool_name, output)
        _dedup_key = ""
        _dedup_count = 0
        _dedup_summary = ""
        return

    if status != "success":
        console.print(f"{status_icon} {escape(summary)}{elapsed_str}")
        err = _error_summary(output)
        if err:
            console.print(f"    [red]{escape(err)}[/red]")
        _dedup_key = ""
        _dedup_count = 0
        _dedup_summary = ""
    elif _verbosity == Verbosity.DETAILED:
        detail = _output_summary(output)
        console.print(f"{status_icon} [{MUTED}]{escape(summary)}{elapsed_str}[/{MUTED}]")
        if detail:
            console.print(f"    [{CHROME}]{escape(detail)}[/{CHROME}]")
        _dedup_key = key
        _dedup_count = 1
        _dedup_first_summary = summary
        _dedup_summary = summary
    else:
        # Compact: just result line
        console.print(f"{status_icon} [{MUTED}]{escape(summary)}{elapsed_str}[/{MUTED}]")
        _dedup_key = key
        _dedup_count = 1
        _dedup_first_summary = summary
        _dedup_summary = summary


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def render_error(message: str) -> None:
    console.print(f"\n[red bold]Error:[/red bold] {escape(message)}")


def render_warning(message: str) -> None:
    console.print(f"\n[yellow bold]Warning:[/yellow bold] {escape(message)}")


def startup_step(message: str) -> Status:
    """Create a dim animated spinner for a startup step.

    Returns a **sync** context manager (Rich Status).  Use ``with``,
    not ``async with`` — ``await`` inside a sync ``with`` block is
    valid Python in async functions::

        with renderer.startup_step("Connecting to servers..."):
            await slow_operation()
    """
    return console.status(f"  [{MUTED}]{message}[/{MUTED}]", spinner="dots12", spinner_style=MUTED)


# ---------------------------------------------------------------------------
# Welcome / help
# ---------------------------------------------------------------------------


def _get_build_date() -> str:
    try:
        from datetime import datetime

        from .._build_info import BUILD_TIMESTAMP

        dt = datetime.fromisoformat(BUILD_TIMESTAMP)
        return dt.astimezone().strftime("%b %d, %Y %I:%M %p")
    except Exception:
        return ""


_BOX_TOP = "\u256d" + "\u2500" * 29 + "\u256e"
_BOX_BOT = "\u2570" + "\u2500" * 29 + "\u256f"
_SEP = " \u00b7 "


def render_welcome(
    model: str,
    tool_count: int,
    instructions_loaded: bool,
    working_dir: str,
    git_branch: str | None = None,
    version: str = "",
    build_date: str = "",
    skill_count: int = 0,
    pack_count: int = 0,
    pack_names: list[str] | None = None,
    is_first_run: bool = False,
) -> None:
    display_dir = _short_path(working_dir)
    branch = f" ({git_branch})" if git_branch else ""

    console.print()
    console.print(f"[{GOLD}]  {_BOX_TOP}[/]")
    console.print(f"[{GOLD}]  \u2502       [bold]A N T E R O O M[/bold]       \u2502[/]")
    console.print(f"[{GOLD}]  \u2502    [{SLATE}]the secure AI gateway[/]    \u2502[/]")
    console.print(f"[{GOLD}]  {_BOX_BOT}[/]")
    console.print()

    version_parts = []
    if version:
        version_parts.append(f"v{version}")
    if build_date:
        version_parts.append(f"Built {build_date}")
    if version_parts:
        console.print(f"  [{MUTED}]{_SEP.join(version_parts)}[/{MUTED}]")
    console.print(f"  [{MUTED}]github.com/troylar/anteroom[/{MUTED}]")
    console.print()

    console.print(f"  [{SLATE}]{escape(display_dir)}{branch}[/]")
    parts = [escape(model), f"{tool_count} tools"]
    if skill_count > 0:
        parts.append(f"{skill_count} skills")
    if pack_count > 0:
        parts.append(f"{pack_count} packs")
    if instructions_loaded:
        parts.append("instructions")
    console.print(f"  [{MUTED}]{_SEP.join(parts)}[/{MUTED}]")
    if pack_names:
        console.print(f"  [{MUTED}]Packs: {', '.join(pack_names)}[/{MUTED}]")
    console.print()
    if is_first_run:
        console.print(f"  [{MUTED}]New here? Type /help for commands, or ask me anything.[/{MUTED}]\n")
    else:
        console.print(f"  [{MUTED}]Type /help for commands[/{MUTED}]\n")


def render_update_available(current: str, latest: str) -> None:
    console.print(
        f"  [{GOLD}]Update available:[/] [{MUTED}]{current} \u2192 {latest}[/{MUTED}]"
        f" [{MUTED}]\u2014 pip install --upgrade anteroom[/{MUTED}]\n"
    )


def render_help() -> None:
    console.print()
    console.print("  /new  /last  /list [N]  /resume <N|id>  /search <query>  /delete <N|id>  /rewind")
    console.print("  /compact  /model <name>  /tools  /skills  /reload-skills  /mcp  /verbose  /detail")
    m = MUTED
    console.print(f"  @<path> [{m}]include file[/]  Alt+Enter [{m}]newline[/]  Esc [{m}]cancel[/]  /quit \u00b7 Ctrl+D")
    console.print()


def render_tools(tool_names: list[str]) -> None:
    console.print("\n[bold]Available tools:[/bold]")
    for name in sorted(tool_names):
        console.print(f"  - {name}")
    console.print()


def render_conversation_recap(messages: list[dict[str, Any]]) -> None:
    """Show the last user/assistant exchange for context on resume."""
    last_user = None
    last_assistant = None
    for msg in reversed(messages):
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content or not isinstance(content, str):
            continue
        if role == "assistant" and last_assistant is None:
            last_assistant = content
        elif role == "user" and last_user is None:
            last_user = content
        if last_user and last_assistant:
            break

    if not last_user and not last_assistant:
        return

    console.print(f"  [{MUTED}]Last exchange:[/{MUTED}]")
    if last_user:
        truncated = last_user[:200].replace("\n", " ")
        if len(last_user) > 200:
            truncated += "..."
        console.print(f"  [{SLATE}]You:[/] [{MUTED}]{escape(truncated)}[/{MUTED}]")
    if last_assistant:
        from rich.padding import Padding

        if len(last_assistant) > 500:
            # Truncate at a line boundary to preserve markdown structure
            cut = last_assistant[:500]
            last_newline = cut.rfind("\n")
            if last_newline > 100:
                truncated = cut[:last_newline] + "\n\n..."
            else:
                truncated = cut + "\n\n..."
        else:
            truncated = last_assistant
        console.print(f"  [{SLATE}]AI:[/{SLATE}]")
        _stdout_console.print(Padding(_make_markdown(truncated), (0, 2, 0, 4)))
    console.print()


def render_compact_done(original: int, compacted: int) -> None:
    console.print(f"\n[{CHROME}]Compacted {original} messages -> {compacted} messages[/{CHROME}]")


# ---------------------------------------------------------------------------
# Status toolbar
# ---------------------------------------------------------------------------


def format_status_toolbar(
    *,
    model: str = "",
    current_tokens: int = 0,
    max_context: int = 128_000,
    message_count: int = 0,
    approval_mode: str = "",
    tool_count: int = 0,
    mcp_statuses: dict[str, dict[str, Any]] | None = None,
) -> list[tuple[str, str]]:
    """Format the persistent bottom toolbar for the REPL.

    Returns a list of (style, text) tuples for prompt_toolkit FormattedText.
    """
    parts: list[tuple[str, str]] = [("class:bottom-toolbar", " ")]

    if model:
        parts.append(("class:bottom-toolbar.model", model))
        parts.append(("class:bottom-toolbar.sep", " \u00b7 "))

    if max_context > 0:
        pct = min(100, (current_tokens / max_context) * 100) if max_context else 0
        token_text = f"{_format_tokens(current_tokens)}/{_format_tokens(max_context)} ({pct:.0f}%)"
        if pct > 75:
            parts.append(("class:bottom-toolbar.tokens-danger", token_text))
        elif pct > 50:
            parts.append(("class:bottom-toolbar.tokens-warn", token_text))
        else:
            parts.append(("class:bottom-toolbar.tokens", token_text))
        parts.append(("class:bottom-toolbar.sep", " \u00b7 "))

    if message_count > 0:
        parts.append(("class:bottom-toolbar.dim", f"{message_count} msgs"))
        parts.append(("class:bottom-toolbar.sep", " \u00b7 "))

    if approval_mode:
        parts.append(("class:bottom-toolbar.dim", approval_mode))
        parts.append(("class:bottom-toolbar.sep", " \u00b7 "))

    if tool_count > 0:
        parts.append(("class:bottom-toolbar.dim", f"{tool_count} tools"))

    # Append MCP connecting status if any servers are still resolving
    if mcp_statuses:
        connecting = [n for n, s in mcp_statuses.items() if s.get("status") == "connecting"]
        if connecting:
            parts.append(("class:bottom-toolbar.sep", " \u00b7 "))
            parts.append(("class:bottom-toolbar.mcp", f"MCP: {', '.join(connecting)}"))

    # Strip trailing separator if present
    if parts and parts[-1][0] == "class:bottom-toolbar.sep":
        parts.pop()

    parts.append(("class:bottom-toolbar", " "))
    return parts


def format_mcp_toolbar(statuses: dict[str, dict[str, Any]]) -> list[tuple[str, str]] | None:
    """Format MCP server statuses for prompt_toolkit bottom_toolbar.

    Returns a list of (style, text) tuples for FormattedText, or None
    when all servers have resolved (toolbar should disappear).
    """
    if not statuses:
        return None

    # Check if all servers have resolved (no longer connecting)
    all_resolved = all(s.get("status") != "connecting" for s in statuses.values())
    if all_resolved:
        return None

    parts: list[tuple[str, str]] = [("class:mcp-label", " MCP: ")]
    for i, (name, info) in enumerate(statuses.items()):
        status = info.get("status", "unknown")
        if i > 0:
            parts.append(("", "  "))
        if status == "connecting":
            parts.append(("class:mcp-connecting", f"● {name}"))
        elif status == "connected":
            count = info.get("tool_count", 0)
            parts.append(("class:mcp-connected", f"✓ {name} ({count} tools)"))
        elif status == "error":
            err = info.get("error_message", "failed")
            if len(err) > 30:
                err = err[:27] + "..."
            parts.append(("class:mcp-error", f"✗ {name} ({err})"))
        else:
            parts.append(("class:mcp-connecting", f"○ {name}"))
    parts.append(("", " "))
    return parts


def render_mcp_status(statuses: dict[str, dict[str, Any]]) -> None:
    """Render MCP server status as a Rich table."""
    from rich.table import Table

    if not statuses:
        console.print(f"\n[{CHROME}]No MCP servers configured.[/{CHROME}]\n")
        return

    table = Table(title="MCP Servers", show_header=True, header_style="bold")
    table.add_column("Server", style="cyan")
    table.add_column("Transport")
    table.add_column("Status")
    table.add_column("Tools", justify="right")

    for name, info in statuses.items():
        status = info.get("status", "unknown")
        if status == "connected":
            status_text = "[green]● connected[/green]"
        elif status == "error":
            err = info.get("error_message", "")
            status_text = "[red]● error[/red]"
            if err:
                # Truncate long error messages in table
                if len(err) > 40:
                    err = err[:37] + "..."
                status_text += f" [{CHROME}]({err})[/{CHROME}]"
        elif status == "disconnected":
            status_text = f"[{CHROME}]○ disconnected[/{CHROME}]"
        else:
            status_text = f"[{CHROME}]○ {status}[/{CHROME}]"

        table.add_row(
            name,
            info.get("transport", "?"),
            status_text,
            str(info.get("tool_count", 0)),
        )

    console.print()
    console.print(table)
    console.print(f"  [{CHROME}]Usage: /mcp [status <name>|connect|disconnect|reconnect <name>][/{CHROME}]\n")


def render_mcp_server_detail(name: str, statuses: dict[str, dict[str, Any]], mcp_manager: Any) -> None:
    """Render detailed diagnostics for a single MCP server."""
    if name not in statuses:
        console.print(f"\n[red]Unknown server: {escape(name)}[/red]")
        known = ", ".join(statuses.keys())
        console.print(f"  [{CHROME}]Available: {known}[/{CHROME}]\n")
        return

    info = statuses[name]
    status = info.get("status", "unknown")

    if status == "connected":
        status_styled = "[green]● connected[/green]"
    elif status == "error":
        status_styled = "[red]● error[/red]"
    else:
        status_styled = f"[{CHROME}]○ {status}[/{CHROME}]"

    console.print(f"\n[bold]MCP Server: {escape(name)}[/bold]")
    console.print(f"  Status:    {status_styled}")
    console.print(f"  Transport: {info.get('transport', '?')}")

    config = mcp_manager._configs.get(name)
    if config:
        if config.command:
            cmd = f"{config.command} {' '.join(config.args)}" if config.args else config.command
            console.print(f"  Command:   {escape(cmd)}")
        if config.url:
            console.print(f"  URL:       {escape(config.url)}")
        if config.env:
            console.print(f"  Env keys:  {', '.join(config.env.keys())}")
        console.print(f"  Timeout:   {config.timeout}s")

    err = info.get("error_message")
    if err:
        console.print(f"  [red]Error:     {escape(err)}[/red]")

    tool_count = info.get("tool_count", 0)
    console.print(f"  Tools:     {tool_count}")
    if tool_count > 0:
        server_tools = mcp_manager._server_tools.get(name, [])
        for t in server_tools:
            desc = t.get("description", "")
            if desc and len(desc) > 60:
                desc = desc[:60] + "..."
            if desc:
                console.print(f"    - {t['name']} [{CHROME}]({desc})[/{CHROME}]")
            else:
                console.print(f"    - {t['name']}")

    console.print()


# ---------------------------------------------------------------------------
# /detail - replay last turn's tool calls with full output
# ---------------------------------------------------------------------------


def render_tool_detail() -> None:
    """Render full detail of the last turn's tool calls."""
    if not _tool_history:
        console.print(f"[{CHROME}]No tool calls in the last turn.[/{CHROME}]\n")
        return

    console.print(f"\n[bold]Last turn: {len(_tool_history)} tool call(s)[/bold]\n")
    for i, tc in enumerate(_tool_history, 1):
        status = tc.get("status", "unknown")
        elapsed = tc.get("elapsed", 0)
        status_icon = "[green]✓[/green]" if status == "success" else "[red]✗[/red]"
        elapsed_str = f" ({elapsed:.1f}s)" if elapsed >= 0.1 else ""

        console.print(f"  {status_icon} [bold]{escape(tc['tool_name'])}[/bold]{elapsed_str}")

        # Show full arguments
        args_str = json.dumps(tc["arguments"], indent=2, default=str)
        for line in args_str.split("\n"):
            console.print(f"    [{MUTED}]{escape(line)}[/{MUTED}]")

        # Show output
        output = tc.get("output")
        if output:
            if isinstance(output, dict):
                if "error" in output:
                    console.print(f"    [red]{escape(str(output['error'])[:500])}[/red]")
                elif "content" in output:
                    content = str(output["content"])
                    if len(content) > 500:
                        content = content[:500] + "..."
                    for line in content.split("\n")[:20]:
                        console.print(f"    [{CHROME}]{escape(line)}[/{CHROME}]")
                    total_lines = str(output["content"]).count("\n") + 1
                    if total_lines > 20:
                        console.print(f"    [{MUTED}]... ({total_lines - 20} more lines)[/{MUTED}]")
                elif "stdout" in output:
                    stdout = str(output.get("stdout", ""))
                    if len(stdout) > 500:
                        stdout = stdout[:500] + "..."
                    for line in stdout.split("\n")[:20]:
                        console.print(f"    [{CHROME}]{escape(line)}[/{CHROME}]")
            else:
                console.print(f"    [{CHROME}]{escape(str(output)[:200])}[/{CHROME}]")
        console.print()


# ---------------------------------------------------------------------------
# Verbosity display
# ---------------------------------------------------------------------------


def render_verbosity_change(v: Verbosity) -> None:
    labels = {
        Verbosity.COMPACT: "compact",
        Verbosity.DETAILED: "detailed",
        Verbosity.VERBOSE: "verbose",
    }
    console.print(f"[{CHROME}]Verbosity: {labels[v]}[/{CHROME}]\n")


# ---------------------------------------------------------------------------
# Context footer (compact)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Sub-agent rendering
# ---------------------------------------------------------------------------

_active_subagents: dict[str, dict[str, Any]] = {}


def clear_subagent_state() -> None:
    """Reset sub-agent tracking state between sessions."""
    _active_subagents.clear()


def render_subagent_start(agent_id: str, prompt: str, model: str, depth: int) -> None:
    """Show that a sub-agent has been launched."""
    _active_subagents[agent_id] = {
        "prompt": prompt,
        "model": model,
        "depth": depth,
        "tools": [],
        "start_time": time.monotonic(),
    }
    indent = "  " * depth
    truncated_prompt = prompt[:80] + "..." if len(prompt) > 80 else prompt
    console.print(f"{indent}[{GOLD}]▶ Agent[/] [bold]{escape(agent_id)}[/bold] [{MUTED}]({model})[/{MUTED}]")
    console.print(f"{indent}  [{CHROME}]{escape(truncated_prompt)}[/{CHROME}]")


def render_subagent_tool(agent_id: str, tool_name: str, arguments: dict[str, Any] | None = None) -> None:
    """Show a tool being used by a sub-agent (compact breadcrumb)."""
    info = _active_subagents.get(agent_id)
    if not info:
        return
    info["tools"].append(tool_name)
    depth = info.get("depth", 1)
    indent = "  " * depth
    summary = _humanize_tool(tool_name, arguments or {})
    console.print(f"{indent}  [{CHROME}]  ✓ {escape(summary)}[/{CHROME}]")


def render_subagent_end(agent_id: str, elapsed: float, tool_calls: list[str], error: str | None = None) -> None:
    """Show sub-agent completion."""
    info = _active_subagents.pop(agent_id, None)
    depth = info.get("depth", 1) if info else 1
    indent = "  " * depth
    tool_count = len(tool_calls)

    if error:
        console.print(f"{indent}[red]■ Agent {escape(agent_id)} failed ({elapsed:.1f}s): {escape(error)}[/red]")
    else:
        console.print(
            f"{indent}[green]■ Agent {escape(agent_id)}[/green] "
            f"[{MUTED}]done in {elapsed:.1f}s · {tool_count} tool call{'s' if tool_count != 1 else ''}[/{MUTED}]"
        )


def render_context_footer(
    current_tokens: int,
    auto_compact_threshold: int,
    response_tokens: int = 0,
    elapsed: float = 0.0,
    max_context: int = 128_000,
) -> None:
    """Render a compact footer showing context usage."""
    pct_full = min(100, (current_tokens / max_context) * 100)
    tokens_remaining = auto_compact_threshold - current_tokens

    if pct_full > 75:
        color = "red"
    elif pct_full > 50:
        color = "yellow"
    else:
        color = CHROME

    parts = [f"{_format_tokens(current_tokens)}/{_format_tokens(max_context)} ({pct_full:.0f}%)"]
    if response_tokens:
        parts.append(f"{_format_tokens(response_tokens)} resp")
    if elapsed > 0:
        parts.append(f"{elapsed:.1f}s")
    if pct_full > 50:
        parts.append(f"compact in {_format_tokens(max(0, tokens_remaining))}")

    console.print(f"[{color}]  ▪ {' · '.join(parts)}[/{color}]")
