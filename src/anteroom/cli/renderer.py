"""Rich-based terminal output for the CLI chat."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import deque
from enum import Enum
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.status import Status
from rich.text import Text

from .themes import CliTheme

console = Console(stderr=True)
# Separate console for stdout markdown rendering (not stderr)
_stdout_console = Console()
_stdout = sys.stdout

# ---------------------------------------------------------------------------
# Theme — loaded from config, defaults to midnight.
# All color references go through _theme instead of hardcoded values.
# ---------------------------------------------------------------------------

_theme: CliTheme = CliTheme.load("midnight")


def set_theme(theme: CliTheme) -> None:
    """Set the active theme. Called during REPL/exec startup."""
    global _theme
    _theme = theme
    _refresh_aliases()


# Backward-compatible module-level aliases for code that imports these.
# These are properties that delegate to the active theme.
GOLD = _theme.accent
SLATE = _theme.secondary
BLUE = _theme.logo_blue
MUTED = _theme.muted
CHROME = _theme.chrome
ERROR_RED = _theme.error


def _refresh_aliases() -> None:
    """Update module-level color aliases after a theme change."""
    global GOLD, SLATE, BLUE, MUTED, CHROME, ERROR_RED
    GOLD = _theme.accent
    SLATE = _theme.secondary
    BLUE = _theme.logo_blue
    MUTED = _theme.muted
    CHROME = _theme.chrome
    ERROR_RED = _theme.error


_ESC_HINT_DELAY: float = 3.0  # seconds before showing "esc to cancel" hint
_STALL_THRESHOLD: float = 15.0  # seconds before showing API stall warning


def use_stdout_console() -> None:
    """Switch renderer to REPL-compatible mode.

    Routes Rich console output through ``sys.stdout`` (the ``patch_stdout``
    proxy) so prompt_toolkit can manage cursor positioning — the prompt and
    bottom toolbar stay anchored at the terminal bottom while output scrolls
    above.

    A duplicated stderr fd is kept as ``_stdout`` for raw ANSI escape writes
    (thinking spinner, tool ticker) that need direct terminal access without
    proxy buffering.

    Call from inside ``patch_stdout()`` context.
    """
    global console, _stdout_console, _stdout, _repl_mode
    # Rich consoles write through the patch_stdout proxy so prompt_toolkit
    # knows about output and can keep the prompt at the bottom.
    console = Console(file=sys.stdout, force_terminal=True)
    _stdout_console = Console(file=sys.stdout, force_terminal=True)
    # Raw ANSI writes (spinners, tickers) go to a real stderr fd to avoid
    # proxy buffering and allow carriage-return cursor manipulation.
    _real_stderr = os.fdopen(os.dup(sys.stderr.fileno()), "w", newline="")
    _stdout = _real_stderr
    _repl_mode = True


def write_raw(text: str) -> None:
    """Write text directly to the real terminal fd, bypassing patch_stdout.

    Used for sub-prompt text (approval prompts, ask_user) that must be
    visible immediately without buffering through prompt_toolkit's proxy.
    """
    if _stdout:
        _stdout.write(text)
        _stdout.flush()


def configure_thresholds(
    esc_hint_delay: float | None = None,
    stall_display: float | None = None,
    stall_warning: float | None = None,
    throughput_threshold: float | None = None,
) -> None:
    """Override default visual thresholds from config."""
    global _ESC_HINT_DELAY, _MID_STREAM_STALL, _STALL_THRESHOLD, _THROUGHPUT_STALL_THRESHOLD
    if esc_hint_delay is not None:
        _ESC_HINT_DELAY = esc_hint_delay
    if stall_display is not None:
        _MID_STREAM_STALL = stall_display
    if stall_warning is not None:
        _STALL_THRESHOLD = stall_warning
    if throughput_threshold is not None:
        _THROUGHPUT_STALL_THRESHOLD = throughput_threshold


# Response buffer (tokens collected silently, rendered on completion)
_streaming_buffer: list[str] = []

# Spinner state
_thinking_start: float = 0
_spinner: Status | None = None
_last_spinner_update: float = 0
_thinking_ticker_task: asyncio.Task[None] | None = None
_thinking_cancelled: bool = False  # guard flag to suppress stale ticker output (#937)

# Lifecycle phase tracking
_thinking_phase: str = ""  # current phase: connecting, waiting, streaming
_thinking_tokens: int = 0  # token counter during streaming
_streaming_chars: int = 0  # character counter during streaming
_last_chunk_time: float = 0  # monotonic time of last token (for stall detection)
_phase_start_time: float = 0  # monotonic time when current phase began
_MID_STREAM_STALL: float = 5.0  # seconds of silence before marking "stalled"

# Throughput-based stall detection (#774): catches slow-trickle streams where
# tiny chunks arrive often enough to avoid gap-based detection but overall
# throughput is extremely low (e.g. 6 chars/sec over 2 minutes).
_throughput_window: deque[tuple[float, int]] = deque()  # (monotonic_time, chars) entries
_THROUGHPUT_STALL_THRESHOLD: float = 30.0  # chars/sec below which "stalled" triggers
_THROUGHPUT_WINDOW_SECS: float = 10.0  # rolling window size
_THROUGHPUT_WARMUP_SECS: float = 8.0  # don't trigger throughput stall before this

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


# ---------------------------------------------------------------------------
# Plan checklist API
# ---------------------------------------------------------------------------


def start_plan(steps: list[str]) -> None:
    """Initialize the plan checklist with step descriptions.

    Call this when a plan is approved and execution begins.
    The checklist is rendered above the thinking line during agentic runs.
    """
    global _plan_steps, _plan_visible, _plan_written_lines
    _plan_steps = [{"text": s, "status": "pending"} for s in steps]
    _plan_visible = True
    _plan_written_lines = 0


def update_plan_step(index: int, status: str) -> None:
    """Update a plan step status: 'pending', 'in_progress', or 'complete'.

    Triggers a redraw if the thinking block is currently displayed.
    """
    if not _plan_steps or index < 0 or index >= len(_plan_steps):
        return
    _plan_steps[index]["status"] = status

    # Redraw if thinking block is on screen
    if _repl_mode and _thinking_start and _stdout and _plan_written_lines > 0:
        elapsed = time.monotonic() - _thinking_start
        _write_thinking_block(elapsed)


def clear_plan() -> None:
    """Clear plan state entirely (e.g. on /plan off or new conversation)."""
    global _plan_steps, _plan_visible, _plan_written_lines
    _plan_steps = []
    _plan_visible = False
    _plan_written_lines = 0


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

    if _repl_mode and _stdout:
        green = _theme.ansi_fg("success")
        muted = _theme.ansi_fg("muted")
        rst = _theme.ansi_reset
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


def clear_turn_history() -> None:
    """Clear current turn tool history. Called at start of each turn."""
    global _streaming_buffer
    _current_turn_tools.clear()
    _streaming_buffer = []


def save_turn_history() -> None:
    """Save current turn tools to history. Called at end of each turn."""
    global _tool_batch_active
    _flush_dedup()
    _tool_batch_active = False
    if _current_turn_tools:
        _tool_history.clear()
        _tool_history.extend(_current_turn_tools)


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
        stdout: str = output.get("stdout", "")
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
            if _thinking_cancelled:
                return
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
    global _plan_written_lines, _thinking_cancelled
    _flush_dedup()
    _thinking_cancelled = False
    # Emit spacing after tool call block before AI narration text (#680).
    # Must happen here because start_thinking() is called before
    # render_response_end(), which would otherwise handle this.
    if _tool_batch_active:
        console.print()
    _tool_batch_active = False
    _thinking_start = time.monotonic()
    _thinking_phase = ""
    _thinking_tokens = 0
    _streaming_chars = 0
    _last_chunk_time = 0
    _phase_start_time = _thinking_start
    _retrying_info = {}
    _throughput_window.clear()
    _last_spinner_update = _thinking_start
    # Reset plan written lines — the block will be freshly written
    _plan_written_lines = 0
    if _repl_mode:
        # Rich Status conflicts with prompt_toolkit's patch_stdout, so
        # we write a plain "Thinking..." line and overwrite it in-place
        # via ANSI escape codes as the timer ticks.
        if newline and _stdout:
            # Atomic \n + initial thinking block prevents prompt_toolkit race (#249).
            gold = _theme.ansi_fg("accent")
            rst = _theme.ansi_reset
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
    gold = _theme.ansi_fg("accent")
    timer_c = _theme.ansi_fg("chrome")
    muted = _theme.ansi_fg("muted")
    err_c = _theme.ansi_fg("error")
    rst = _theme.ansi_reset

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
    green = _theme.ansi_fg("success")
    gold_c = _theme.ansi_fg("accent")
    muted_c = _theme.ansi_fg("muted")
    rst = _theme.ansi_reset

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

    Optional keyword args for special states:
    - ``error_msg``: pale-red inline error replacing phase text
    - ``countdown``: seconds remaining for auto-retry (shown after error_msg)
    - ``cancel_msg``: muted message like "cancelled" (user-initiated, not error)
    """
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
    if _spinner:
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
                gold = _theme.ansi_fg("accent")
                timer_c = _theme.ansi_fg("chrome")
                rst = _theme.ansi_reset
                _stdout.write(f"\r\033[2K{gold}Thinking...{rst} {timer_c}{elapsed:.0f}s{rst}\n")
                _stdout.flush()
    _thinking_start = 0
    return elapsed


def stop_thinking_sync() -> float:
    """Synchronous fallback for stop_thinking (KeyboardInterrupt handlers).

    Does not await the ticker — use only when an event loop is unavailable.
    """
    global _spinner, _thinking_ticker_task, _plan_written_lines, _thinking_start, _thinking_cancelled
    elapsed = 0.0
    _thinking_cancelled = True  # suppress stale ticker output before cancel propagates (#937)
    if _thinking_ticker_task is not None:
        _thinking_ticker_task.cancel()
        _thinking_ticker_task = None
    if _spinner:
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
    now = time.monotonic()
    _throughput_window.append((now, n))
    cutoff = now - _THROUGHPUT_WINDOW_SECS
    while _throughput_window and _throughput_window[0][0] < cutoff:
        _throughput_window.popleft()


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
        # Gap-based stall: no chunks at all for > threshold
        if _last_chunk_time and now - _last_chunk_time > _MID_STREAM_STALL:
            stall_secs = now - _last_chunk_time
            return f"streaming · {_streaming_chars:,} chars · stalled {stall_secs:.0f}s"
        # Throughput-based stall (#774): chunks trickle in but throughput is
        # extremely low (e.g. 6 chars/sec).  Only check after warmup period
        # so we have enough data for a meaningful measurement.
        if _phase_start_time and now - _phase_start_time > _THROUGHPUT_WARMUP_SECS and _throughput_window:
            window_span = now - _throughput_window[0][0]
            if window_span > 0:
                window_chars = sum(n for _, n in _throughput_window)
                throughput = window_chars / window_span
                if throughput < _THROUGHPUT_STALL_THRESHOLD:
                    return f"streaming · {_streaming_chars:,} chars · slow ({throughput:.0f} chars/s)"
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
    """
    global _streaming_buffer, _tool_batch_active
    text = "".join(_streaming_buffer)
    _streaming_buffer = []
    if not text.strip():
        return

    # Add spacing after tool call block before narration text.
    # This handles mid-turn narration; render_response_end() handles end-of-turn.
    if _tool_batch_active:
        console.print()
        _tool_batch_active = False

    from rich.padding import Padding

    _stdout_console.print(Padding(_make_markdown(text), (0, 2, 0, 2)))


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


def render_token(content: str) -> None:
    """Buffer token content silently (no streaming output)."""
    _streaming_buffer.append(content)


def render_response_end() -> None:
    """Render the complete buffered response with Rich Markdown."""
    global _streaming_buffer, _tool_batch_active
    _flush_dedup()

    full_text = "".join(_streaming_buffer)
    _streaming_buffer = []

    if not full_text.strip():
        _tool_batch_active = False
        return

    # Add spacing after tool call block before AI response
    if _tool_batch_active:
        console.print()
        _tool_batch_active = False

    from rich.padding import Padding

    _stdout_console.print(Padding(_make_markdown(full_text), (0, 2, 1, 2)))


def render_newline() -> None:
    console.print()


# ---------------------------------------------------------------------------
# Inline diff rendering (Claude Code-style)
# ---------------------------------------------------------------------------

_DIFF_CONTEXT_LINES = 3  # lines of context around each change


def _diff_remove_bg() -> str:
    return f"on {_theme.diff_remove_bg}" if _theme.diff_remove_bg else ""


def _diff_add_bg() -> str:
    return f"on {_theme.diff_add_bg}" if _theme.diff_add_bg else ""


def _diff_line_no() -> str:
    return _theme.chrome


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
        header_text.append("  ● ", style=_theme.success)
        header_text.append(f"Write({short})", style="bold")
        console.print(header_text)
        summary_text = Text()
        summary_text.append(f"  └ Created, {lines} lines", style=_theme.muted)
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
    header_text.append("  ● ", style=_theme.success)
    header_text.append(f"{label}({short})", style="bold")
    console.print(header_text)

    summary_text = Text()
    summary_text.append("  └ ", style=_theme.muted)
    summary_text.append(f"Added {added} lines", style=_theme.success) if added else None
    if added and removed:
        summary_text.append(", ", style=_theme.muted)
    summary_text.append(f"removed {removed} lines", style=_theme.error) if removed else None
    if not added and not removed:
        summary_text.append("no line changes", style=_theme.muted)
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
                line_text.append(f"    {old_num:>4} ", style=_diff_line_no())
                line_text.append(f" {display} ", style=_diff_remove_bg())
                console.print(line_text)
                old_num += 1
            elif tag == "+":
                line_text = Text()
                line_text.append(f"    {new_num:>4} ", style=_diff_line_no())
                line_text.append(f" {display} ", style=_diff_add_bg())
                console.print(line_text)
                new_num += 1
            else:
                line_text = Text()
                line_text.append(f"    {new_num:>4} ", style=_diff_line_no())
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
                if _tool_spinner:
                    label = f"  [{MUTED}]{escape(_tool_ticker_summary)}  {elapsed:.0f}s[/{MUTED}]"
                    _tool_spinner.update(label)
                elif _repl_mode and _stdout:
                    muted = _theme.ansi_fg("muted")
                    rst = _theme.ansi_reset
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
    if _tool_spinner:
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

    if _verbosity == Verbosity.VERBOSE:
        # Legacy-style
        if status == "success":
            style = _theme.success
        else:
            style = _theme.error
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
    _s = _theme.success
    _e = _theme.error
    status_icon = f"[{_s}]  ✓[/{_s}]" if status == "success" else f"[{_e}]  ✗[/{_e}]"
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
            console.print(f"    [{_theme.error}]{escape(err)}[/{_theme.error}]")
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
    e = _theme.error or "red"
    console.print(f"\n[{e} bold]Error:[/{e} bold] {escape(message)}")


def render_warning(message: str) -> None:
    w = _theme.warning or "yellow"
    console.print(f"\n[{w} bold]Warning:[/{w} bold] {escape(message)}")


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
    console.print(Text("      \u25b2", style=GOLD))
    console.print(Text("     / \\", style=GOLD))
    console.print(Text("    /   \\", style=GOLD))
    _logo4 = Text()
    _logo4.append("   / ", style=GOLD)
    _logo4.append("\u25a0\u25a0", style=BLUE)
    _logo4.append("  \\", style=GOLD)
    _logo4.append("   ")
    _logo4.append("A N T E R O O M", style="bold")
    console.print(_logo4)
    _logo5 = Text()
    _logo5.append("  /       \\", style=GOLD)
    _logo5.append("  ")
    _logo5.append("the secure AI gateway", style=SLATE)
    console.print(_logo5)
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
        console.print(f"  [{GOLD}]Getting started:[/{GOLD}]")
        console.print(f"  [{MUTED}]Just type a message to start chatting[/{MUTED}]")
        console.print(f"  [{MUTED}]/space init   \u2014 set up a workspace with custom instructions[/{MUTED}]")
        console.print(f"  [{MUTED}]/help         \u2014 see all commands[/{MUTED}]")
        console.print()
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
    space_name: str = "",
    plan_mode: bool = False,
    working_dir: str = "",
    git_branch: str = "",
    conversation_name: str = "",
) -> list[tuple[str, str]]:
    """Format the persistent bottom toolbar for the REPL.

    Returns a list of (style, text) tuples for prompt_toolkit FormattedText.
    """
    from .layout import _shorten_path

    parts: list[tuple[str, str]] = [("class:bottom-toolbar", " ")]

    if model:
        parts.append(("class:bottom-toolbar.model", model))
        parts.append(("class:bottom-toolbar.sep", " \u00b7 "))

    if working_dir:
        dir_text = _shorten_path(working_dir)
        if git_branch:
            dir_text += f" ({git_branch})"
        parts.append(("class:bottom-toolbar.dir", dir_text))
        parts.append(("class:bottom-toolbar.sep", " \u00b7 "))

    if conversation_name:
        parts.append(("class:bottom-toolbar.dir", conversation_name))
        parts.append(("class:bottom-toolbar.sep", " \u00b7 "))

    if space_name:
        parts.append(("class:bottom-toolbar.mcp", space_name))
        parts.append(("class:bottom-toolbar.sep", " \u00b7 "))

    if plan_mode:
        parts.append(("class:bottom-toolbar.tokens-warn", "PLAN"))
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
    table.add_column("Server", style=_theme.mcp_indicator or "cyan")
    table.add_column("Transport")
    table.add_column("Status")
    table.add_column("Tools", justify="right")

    for name, info in statuses.items():
        status = info.get("status", "unknown")
        if status == "connected":
            status_text = f"[{_theme.success}]● connected[/{_theme.success}]"
        elif status == "error":
            err = info.get("error_message", "")
            status_text = f"[{_theme.error}]● error[/{_theme.error}]"
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
        console.print(f"\n[{_theme.error}]Unknown server: {escape(name)}[/{_theme.error}]")
        known = ", ".join(statuses.keys())
        console.print(f"  [{CHROME}]Available: {known}[/{CHROME}]\n")
        return

    info = statuses[name]
    status = info.get("status", "unknown")

    if status == "connected":
        status_styled = f"[{_theme.success}]● connected[/{_theme.success}]"
    elif status == "error":
        status_styled = f"[{_theme.error}]● error[/{_theme.error}]"
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
        _s = _theme.success
        _e = _theme.error
        status_icon = f"[{_s}]✓[/{_s}]" if status == "success" else f"[{_e}]✗[/{_e}]"
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
                    console.print(f"    [{_theme.error}]{escape(str(output['error'])[:500])}[/{_theme.error}]")
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
        e = _theme.error
        console.print(f"{indent}[{e}]■ Agent {escape(agent_id)} failed ({elapsed:.1f}s): {escape(error)}[/{e}]")
    else:
        console.print(
            f"{indent}[{_theme.success}]■ Agent {escape(agent_id)}[/{_theme.success}] "
            f"[{MUTED}]done in {elapsed:.1f}s · {tool_count} tool call{'s' if tool_count != 1 else ''}[/{MUTED}]"
        )


def render_rag_sources(chunks: list[Any]) -> None:
    """Render a muted line listing which sources contributed RAG context.

    Accepts either RetrievedChunk objects (with attributes) or dicts (from persisted metadata).
    """
    if not chunks:
        return
    seen: set[str] = set()
    parts: list[str] = []
    for c in chunks:
        if isinstance(c, dict):
            label = c.get("label") or "?"
            stype = c.get("type") or "?"
        else:
            label = getattr(c, "source_label", None) or "?"
            stype = getattr(c, "source_type", None) or "?"
        key = f"{stype}:{label}"
        if key in seen:
            continue
        seen.add(key)
        badge = "knowledge" if stype == "source_chunk" else "conversation"
        parts.append(f'"{escape(label)}" ({badge})')
    if parts:
        console.print(f"  [{MUTED}]Sources: {', '.join(parts)}[/{MUTED}]")


def render_rag_status(status: str, chunk_count: int = 0, reason: str | None = None) -> None:
    """Render RAG retrieval status with consistent formatting."""
    if status == "ok" and chunk_count > 0:
        console.print(f"  [{MUTED}][RAG: {chunk_count} relevant chunk(s) retrieved][/{MUTED}]")
    elif status == "no_results":
        suffix = f" — {reason}" if reason else ""
        console.print(f"  [{MUTED}][RAG: no results{suffix}][/{MUTED}]")
    elif status == "failed":
        console.print(f"  [{MUTED}][RAG: retrieval failed][/{MUTED}]")
    elif status == "no_vec_support":
        console.print(f"  [{MUTED}][RAG: embedding service unavailable][/{MUTED}]")
    # Silent for: disabled, no_config, skipped_plan_mode, skipped


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
        color = _theme.danger
    elif pct_full > 50:
        color = _theme.warning
    else:
        color = _theme.chrome

    parts = [f"{_format_tokens(current_tokens)}/{_format_tokens(max_context)} ({pct_full:.0f}%)"]
    if response_tokens:
        parts.append(f"{_format_tokens(response_tokens)} resp")
    if elapsed > 0:
        parts.append(f"{elapsed:.1f}s")
    if pct_full > 50:
        parts.append(f"compact in {_format_tokens(max(0, tokens_remaining))}")

    console.print(f"[{color}]  ▪ {' · '.join(parts)}[/{color}]")
