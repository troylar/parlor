from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Awaitable, Callable

from textual.pilot import Pilot
from textual.widgets import TextArea

import anteroom.cli.textual_app as textual_app_module
from anteroom.cli.commands import CommandResult, ParsedSlashCommand
from anteroom.cli.textual_app import BoardWidget, SessionSnapshot, TextualChatApp, TranscriptPane
from anteroom.services.agent_loop import AgentEvent


@dataclass(frozen=True)
class RenderedScenario:
    name: str
    svg: str
    transcript: str
    procedure: str
    tools: str
    trace: str

    def snapshot_payloads(self) -> dict[str, str]:
        return {
            "svg": self.svg,
            "transcript.md": self.transcript,
            "procedure.txt": _normalize_board_snapshot(self.procedure),
            "tools.txt": _normalize_board_snapshot(self.tools),
            "trace.txt": _normalize_trace_snapshot(_normalize_board_snapshot(self.trace)),
        }


def _normalize_board_snapshot(text: str) -> str:
    return re.sub(r"(?m)^[◜◠◝◞◡◟] ", "<spin> ", text)


def _normalize_trace_snapshot(text: str) -> str:
    text = re.sub(r"last chunk \d+\.\ds ago", "last chunk <time> ago", text)
    text = re.sub(r"waiting \d+\.\ds", "waiting <time>", text)
    return text


@dataclass(frozen=True)
class TextualScenario:
    name: str
    size: tuple[int, int]
    driver: Callable[[Pilot[None], TextualChatApp, "ScriptedBackend"], Awaitable[None]]
    backend_factory: Callable[[], "ScriptedBackend"] | None = None
    cleanup: Callable[[Pilot[None], TextualChatApp, "ScriptedBackend"], Awaitable[None]] | None = None


class ScriptedBackend:
    def __init__(
        self,
        history: list[tuple[str, str]] | None = None,
        *,
        slash_results: dict[str, CommandResult] | None = None,
    ) -> None:
        self.history = history or []
        self.slash_results = slash_results or {}
        self.prompts: list[str] = []
        self.turns: list[asyncio.Queue[AgentEvent | None]] = []
        self.cancelled = 0

    def add_turn(self) -> asyncio.Queue[AgentEvent | None]:
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        self.turns.append(queue)
        return queue

    async def load_history(self) -> list[tuple[str, str]]:
        return list(self.history)

    async def execute_slash_command(self, prompt: str) -> CommandResult | None:
        return self.slash_results.get(prompt)

    async def delete_conversation(self, target: str) -> str:
        return f"Deleted **{target}**."

    async def submit_turn(self, prompt: str):
        self.prompts.append(prompt)
        queue = self.turns[len(self.prompts) - 1]
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item

    def cancel_current_turn(self) -> None:
        self.cancelled += 1


def session_snapshot() -> SessionSnapshot:
    return SessionSnapshot(
        model="gpt-5.2",
        working_dir="/repo/project",
        tool_count=12,
        instructions_loaded=True,
        git_branch="issue-758-focus-fold-ui",
        version="1.100.0",
        skill_count=14,
        pack_count=2,
    )


async def submit_prompt(pilot: Pilot[None], app: TextualChatApp, prompt: str) -> None:
    composer = app.query_one("#composer", TextArea)
    composer.load_text(prompt)
    composer.focus()
    await pilot.press("enter")
    await pilot.pause()


async def drive_multiturn_review(pilot: Pilot[None], app: TextualChatApp, backend: ScriptedBackend) -> None:
    turn_one = backend.add_turn()
    turn_two = backend.add_turn()

    await submit_prompt(pilot, app, "read my files in this folder and tell me what it is")
    await turn_one.put(
        AgentEvent(kind="tool_call_start", data={"tool_name": "glob", "arguments": {"path": ".", "glob": "**/*"}})
    )
    await turn_one.put(AgentEvent(kind="tool_call_end", data={"tool_name": "glob", "status": "success", "output": {}}))
    for path in [
        "pyproject.toml",
        "README.md",
        "src/anteroom/__main__.py",
        "src/anteroom/app.py",
        "src/anteroom/cli/repl.py",
    ]:
        await turn_one.put(
            AgentEvent(kind="tool_call_start", data={"tool_name": "read", "arguments": {"path": path}})
        )
        await turn_one.put(
            AgentEvent(kind="tool_call_end", data={"tool_name": "read", "status": "success", "output": {}})
        )
    await turn_one.put(
        AgentEvent(
            kind="assistant_message",
            data={
                "content": (
                    "This folder is the top-level of the Anteroom repository.\n\n"
                    "## What It Is\n\n"
                    "Anteroom is a self-hosted AI gateway that provides:\n\n"
                    "- a web UI (FastAPI backend + vanilla JS frontend)\n"
                    "- an agentic CLI REPL (Rich-based terminal app)\n\n"
                    "It connects to OpenAI-compatible APIs and is designed to be local-first.\n"
                )
            },
        )
    )
    await turn_one.put(AgentEvent(kind="done", data={}))
    await turn_one.put(None)
    await pilot.pause()

    await submit_prompt(pilot, app, "look at my /tmp folder and do the same thing")
    await turn_two.put(
        AgentEvent(kind="tool_call_start", data={"tool_name": "glob", "arguments": {"path": "/tmp", "glob": "*"}})
    )
    await turn_two.put(AgentEvent(kind="tool_call_end", data={"tool_name": "glob", "status": "success", "output": {}}))
    for path in [
        "/tmp/.env",
        "/tmp/test_summary.txt",
        "/tmp/coverage_report.txt",
        "/tmp/issue-798.md",
        "/tmp/pr799-review.md",
    ]:
        await turn_two.put(
            AgentEvent(kind="tool_call_start", data={"tool_name": "read", "arguments": {"path": path}})
        )
        await turn_two.put(
            AgentEvent(kind="tool_call_end", data={"tool_name": "read", "status": "success", "output": {}})
        )
    await turn_two.put(
        AgentEvent(
            kind="assistant_message",
            data={
                "content": (
                    "Here's what your `/tmp` folder looks like at a high level.\n\n"
                    "## What /tmp Is\n\n"
                    "`/tmp` is a temporary scratch directory used for short-lived files.\n\n"
                    "## What's In /tmp Right Now\n\n"
                    "I see a mix of:\n\n"
                    "1. issue and PR notes\n"
                    "2. summary and coverage reports\n"
                    "3. ad hoc environment and scratch files\n"
                )
            },
        )
    )
    await turn_two.put(AgentEvent(kind="done", data={}))
    await turn_two.put(None)
    await pilot.pause()


async def drive_interrupted_retry(pilot: Pilot[None], app: TextualChatApp, backend: ScriptedBackend) -> None:
    turn = backend.add_turn()
    await submit_prompt(pilot, app, "who is the president")
    await turn.put(
        AgentEvent(
            kind="token",
            data={
                "content": (
                    "If you mean the **President of the United States**, the current president is "
                    "**Donald J. Trump**."
                )
            },
        )
    )
    await pilot.pause()
    await turn.put(
        AgentEvent(
            kind="error",
            data={"message": "Stream ended unexpectedly — response may be incomplete", "retryable": True},
        )
    )
    await turn.put(AgentEvent(kind="retrying", data={"attempt": 2, "max_attempts": 2, "reason": "turn_retry"}))
    await turn.put(
        AgentEvent(
            kind="assistant_message",
            data={
                "content": (
                    "If you mean the **President of the United States**, it is **Donald J. Trump** "
                    "(current term began **January 20, 2025**)."
                )
            },
        )
    )
    await turn.put(AgentEvent(kind="done", data={}))
    await turn.put(None)
    await pilot.pause()


async def drive_tool_error_recovery(pilot: Pilot[None], app: TextualChatApp, backend: ScriptedBackend) -> None:
    turn = backend.add_turn()
    await submit_prompt(pilot, app, "read the protected file and recover safely")
    await turn.put(
        AgentEvent(
            kind="tool_call_start",
            data={"tool_name": "read", "arguments": {"path": "/root/secret.txt"}},
        )
    )
    await turn.put(
        AgentEvent(
            kind="tool_call_end",
            data={"tool_name": "read", "status": "error", "output": {"error": "permission denied"}},
        )
    )
    await turn.put(
        AgentEvent(
            kind="tool_call_start",
            data={"tool_name": "read", "arguments": {"path": "tests/unit/test_focus_fold.py"}},
        )
    )
    await turn.put(AgentEvent(kind="tool_call_end", data={"tool_name": "read", "status": "success", "output": {}}))
    await turn.put(
        AgentEvent(
            kind="assistant_message",
            data={
                "content": (
                    "I couldn't read `/root/secret.txt`, so I switched to a safe local fixture instead.\n\n"
                    "The fallback file `tests/unit/test_focus_fold.py` is readable and contains the fold UI tests."
                )
            },
        )
    )
    await turn.put(AgentEvent(kind="done", data={}))
    await turn.put(None)
    await pilot.pause()


async def drive_stream_stall(pilot: Pilot[None], app: TextualChatApp, backend: ScriptedBackend) -> None:
    turn = backend.add_turn()
    app._scenario_stream_stall_secs = textual_app_module._STREAM_STALL_SECS
    textual_app_module._STREAM_STALL_SECS = 0.05

    await submit_prompt(
        pilot,
        app,
        "Explain the answer, but pause mid-stream so I can inspect the UI",
    )
    await turn.put(
        AgentEvent(
            kind="token",
            data={"content": "I have the verified context and I'm starting the answer."},
        )
    )
    await pilot.pause()
    await asyncio.sleep(0.12)
    app._on_stream_heartbeat()
    await pilot.pause()


async def cleanup_stream_stall(pilot: Pilot[None], app: TextualChatApp, backend: ScriptedBackend) -> None:
    textual_app_module._STREAM_STALL_SECS = getattr(
        app,
        "_scenario_stream_stall_secs",
        textual_app_module._STREAM_STALL_SECS,
    )
    if backend.turns:
        turn = backend.turns[0]
        await turn.put(
            AgentEvent(
                kind="assistant_message",
                data={
                    "content": (
                        "I have the verified context and I'm starting the answer.\n\n"
                        "The stream recovered and the answer finished cleanly."
                    )
                },
            )
        )
        await turn.put(AgentEvent(kind="done", data={}))
        await turn.put(None)
        await pilot.pause(0.05)


def _delete_confirmation_backend() -> ScriptedBackend:
    return ScriptedBackend(
        history=[
            ("user", "Clean up this old thread."),
            ("assistant", "I can do that safely once you confirm."),
        ],
        slash_results={
            "/delete old-thread": CommandResult(
                kind="delete_conversation",
                command=ParsedSlashCommand(raw="/delete old-thread", name="/delete", arg="old-thread"),
                delete_target="old-thread",
                echo_user=False,
            )
        },
    )


async def drive_delete_confirmation(pilot: Pilot[None], app: TextualChatApp, backend: ScriptedBackend) -> None:
    await pilot.pause()
    composer = app.query_one("#composer", TextArea)
    composer.load_text("/delete old-thread")
    composer.focus()
    app._scenario_delete_submit_task = asyncio.create_task(app.action_submit_composer())
    await pilot.pause()


async def cleanup_delete_confirmation(pilot: Pilot[None], app: TextualChatApp, backend: ScriptedBackend) -> None:
    screen = app.screen_stack[-1]
    if isinstance(screen, textual_app_module.QuestionScreen):
        screen.dismiss("Cancel")
        await pilot.pause(0.05)
    submit_task = getattr(app, "_scenario_delete_submit_task", None)
    if submit_task is not None:
        await submit_task


async def drive_warning_recovery(pilot: Pilot[None], app: TextualChatApp, backend: ScriptedBackend) -> None:
    turn = backend.add_turn()
    await submit_prompt(pilot, app, "Summarize the output, but account for a safety warning on the way")
    await turn.put(AgentEvent(kind="thinking", data={}))
    await turn.put(
        AgentEvent(
            kind="tool_call_start",
            data={"tool_name": "read", "arguments": {"path": "reports/build-output.txt"}},
        )
    )
    await turn.put(AgentEvent(kind="tool_call_end", data={"tool_name": "read", "status": "success", "output": {}}))
    await turn.put(
        AgentEvent(
            kind="budget_warning",
            data={"message": "The output was large, so I trimmed the plan before continuing."},
        )
    )
    await turn.put(
        AgentEvent(
            kind="token",
            data={"content": "I trimmed the path and kept only the verified findings."},
        )
    )
    await pilot.pause()


async def cleanup_warning_recovery(pilot: Pilot[None], app: TextualChatApp, backend: ScriptedBackend) -> None:
    if backend.turns:
        turn = backend.turns[0]
        await turn.put(
            AgentEvent(
                kind="assistant_message",
                data={
                    "content": (
                        "I trimmed the path and kept only the verified findings.\n\n"
                        "The warning was non-blocking, and the answer completed safely."
                    )
                },
            )
        )
        await turn.put(AgentEvent(kind="done", data={}))
        await turn.put(None)
        await pilot.pause(0.05)


async def drive_approval_modal(pilot: Pilot[None], app: TextualChatApp, backend: ScriptedBackend) -> None:
    await pilot.pause()
    await app._append_bubble("user", "Clean the generated build output and prepare a fresh release run.")
    await app._append_bubble(
        "assistant",
        "I can do that, but I need approval before I run a destructive shell command.",
    )
    verdict = SimpleNamespace(
        reason="Destructive command detected",
        details={"command": "rm -rf ./dist"},
        tool_name="bash",
    )
    app._scenario_approval_task = asyncio.create_task(app._confirm_dialog(verdict))
    await pilot.pause()


async def cleanup_approval_modal(pilot: Pilot[None], app: TextualChatApp, backend: ScriptedBackend) -> None:
    screen = app.screen_stack[-1]
    if isinstance(screen, textual_app_module.ApprovalScreen):
        screen.dismiss("session")
        await pilot.pause(0.05)
    confirm_task = getattr(app, "_scenario_approval_task", None)
    if confirm_task is not None:
        await confirm_task


async def drive_ask_user_modal(pilot: Pilot[None], app: TextualChatApp, backend: ScriptedBackend) -> None:
    await pilot.pause()
    await app._append_bubble("user", "Prepare the release, but check which environment to use.")
    await app._append_bubble(
        "assistant",
        "I need one input before I continue with the release steps.",
    )
    app._scenario_ask_task = asyncio.create_task(
        app._ask_user_dialog("Which environment should I deploy to?", ["staging", "production"])
    )
    await pilot.pause()


async def cleanup_ask_user_modal(pilot: Pilot[None], app: TextualChatApp, backend: ScriptedBackend) -> None:
    screen = app.screen_stack[-1]
    if isinstance(screen, textual_app_module.QuestionScreen):
        screen.dismiss("staging")
        await pilot.pause(0.05)
    ask_task = getattr(app, "_scenario_ask_task", None)
    if ask_task is not None:
        await ask_task


async def drive_subagent_progress(pilot: Pilot[None], app: TextualChatApp, backend: ScriptedBackend) -> None:
    turn = backend.add_turn()
    await submit_prompt(pilot, app, "Audit the repo with a sub-agent and tell me the outcome.")
    await turn.put(
        AgentEvent(
            kind="tool_call_start",
            data={"tool_name": "run_agent", "arguments": {"prompt": "Audit the repo for risky changes"}},
        )
    )
    await turn.put(
        AgentEvent(
            kind="tool_call_start",
            data={"tool_name": "grep", "arguments": {"pattern": "rm -rf"}},
        )
    )
    await turn.put(
        AgentEvent(kind="tool_call_end", data={"tool_name": "grep", "status": "success", "output": {}})
    )
    await pilot.pause()


async def cleanup_subagent_progress(pilot: Pilot[None], app: TextualChatApp, backend: ScriptedBackend) -> None:
    if backend.turns:
        turn = backend.turns[0]
        await turn.put(
            AgentEvent(
                kind="tool_call_end",
                data={"tool_name": "run_agent", "status": "success", "output": {}},
            )
        )
        await turn.put(
            AgentEvent(
                kind="assistant_message",
                data={"content": "The sub-agent completed cleanly and found no destructive shell usage."},
            )
        )
        await turn.put(AgentEvent(kind="done", data={}))
        await turn.put(None)
        await pilot.pause(0.05)


async def drive_partial_error_recovery(pilot: Pilot[None], app: TextualChatApp, backend: ScriptedBackend) -> None:
    turn = backend.add_turn()
    await submit_prompt(pilot, app, "Start an answer, fail partway through, then show the recovery state.")
    await turn.put(
        AgentEvent(
            kind="token",
            data={"content": "I have the first verified finding and I'm drafting the answer."},
        )
    )
    await turn.put(
        AgentEvent(
            kind="error",
            data={"message": "The stream dropped before I could finish safely.", "retryable": True},
        )
    )
    await pilot.pause()


async def cleanup_partial_error_recovery(pilot: Pilot[None], app: TextualChatApp, backend: ScriptedBackend) -> None:
    if backend.turns:
        turn = backend.turns[0]
        await turn.put(AgentEvent(kind="retrying", data={"attempt": 2, "max_attempts": 2, "reason": "turn_retry"}))
        await turn.put(
            AgentEvent(
                kind="assistant_message",
                data={"content": "The stream recovered and I completed the answer from verified context."},
            )
        )
        await turn.put(AgentEvent(kind="done", data={}))
        await turn.put(None)
        await pilot.pause(0.05)


SCENARIOS: dict[str, TextualScenario] = {
    "multiturn_review": TextualScenario(
        name="multiturn_review",
        size=(160, 42),
        driver=drive_multiturn_review,
    ),
    "interrupted_retry": TextualScenario(
        name="interrupted_retry",
        size=(140, 32),
        driver=drive_interrupted_retry,
    ),
    "tool_error_recovery": TextualScenario(
        name="tool_error_recovery",
        size=(140, 34),
        driver=drive_tool_error_recovery,
    ),
    "stream_stall": TextualScenario(
        name="stream_stall",
        size=(140, 32),
        driver=drive_stream_stall,
        cleanup=cleanup_stream_stall,
    ),
    "delete_confirmation": TextualScenario(
        name="delete_confirmation",
        size=(140, 34),
        driver=drive_delete_confirmation,
        backend_factory=_delete_confirmation_backend,
        cleanup=cleanup_delete_confirmation,
    ),
    "warning_recovery": TextualScenario(
        name="warning_recovery",
        size=(140, 32),
        driver=drive_warning_recovery,
        cleanup=cleanup_warning_recovery,
    ),
    "approval_modal": TextualScenario(
        name="approval_modal",
        size=(140, 34),
        driver=drive_approval_modal,
        cleanup=cleanup_approval_modal,
    ),
    "ask_user_modal": TextualScenario(
        name="ask_user_modal",
        size=(140, 34),
        driver=drive_ask_user_modal,
        cleanup=cleanup_ask_user_modal,
    ),
    "subagent_progress": TextualScenario(
        name="subagent_progress",
        size=(140, 34),
        driver=drive_subagent_progress,
        cleanup=cleanup_subagent_progress,
    ),
    "partial_error_recovery": TextualScenario(
        name="partial_error_recovery",
        size=(140, 32),
        driver=drive_partial_error_recovery,
        cleanup=cleanup_partial_error_recovery,
    ),
}


def scenario_names() -> list[str]:
    return sorted(SCENARIOS)


async def render_scenario(name: str) -> RenderedScenario:
    scenario = SCENARIOS[name]
    backend = scenario.backend_factory() if scenario.backend_factory is not None else ScriptedBackend()
    app = TextualChatApp(backend=backend, session=session_snapshot())

    async with app.run_test(size=scenario.size) as pilot:
        await scenario.driver(pilot, app, backend)
        svg = app.export_screenshot(title=name, simplify=True)
        transcript = app.query_one("#transcript-pane", TranscriptPane).text
        procedure = app.query_one("#procedure-board", BoardWidget).plain_text
        tools = app.query_one("#tool-board", BoardWidget).plain_text
        trace = app.query_one("#trace-board", BoardWidget).plain_text
        if scenario.cleanup is not None:
            await scenario.cleanup(pilot, app, backend)

    return RenderedScenario(
        name=name,
        svg=svg,
        transcript=transcript,
        procedure=procedure,
        tools=tools,
        trace=trace,
    )


def golden_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "golden" / "textual_ui"


def snapshot_paths(name: str, root: Path | None = None) -> dict[str, Path]:
    base = root or golden_dir()
    return {
        suffix: base / f"{name}.{suffix}"
        for suffix in ("svg", "transcript.md", "procedure.txt", "tools.txt", "trace.txt")
    }


def write_snapshots(rendered: RenderedScenario, root: Path | None = None) -> dict[str, Path]:
    paths = snapshot_paths(rendered.name, root)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    for suffix, payload in rendered.snapshot_payloads().items():
        paths[suffix].write_text(payload, encoding="utf-8")
    return paths


def assert_matches_golden(rendered: RenderedScenario, *, update: bool = False, root: Path | None = None) -> None:
    paths = snapshot_paths(rendered.name, root)
    missing = [suffix for suffix, path in paths.items() if not path.exists()]
    if update:
        write_snapshots(rendered, root)
    if missing and not update:
        raise AssertionError(
            f"Missing snapshot files for {rendered.name}: {', '.join(missing)}. "
            "Re-run with UPDATE_TEXTUAL_GOLDENS=1."
        )

    mismatches: list[str] = []
    for suffix, payload in rendered.snapshot_payloads().items():
        expected = paths[suffix].read_text(encoding="utf-8")
        if payload != expected:
            mismatches.append(suffix)
            if update:
                paths[suffix].write_text(payload, encoding="utf-8")
    if mismatches:
        raise AssertionError(
            f"Scenario {rendered.name} diverged from golden snapshots: {', '.join(mismatches)}. "
            "Re-run with UPDATE_TEXTUAL_GOLDENS=1 to accept the new output."
        )


async def export_scenarios(names: list[str], output_dir: Path) -> dict[str, dict[str, Path]]:
    written: dict[str, dict[str, Path]] = {}
    for name in names:
        rendered = await render_scenario(name)
        written[name] = write_snapshots(rendered, output_dir)
    return written


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render canned Textual UI scenarios for snapshot inspection.")
    parser.add_argument("names", nargs="*", help="Scenario names to render. Defaults to all scenarios.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tmp/textual_ui"),
        help="Directory where SVG and text snapshots will be written.",
    )
    parser.add_argument(
        "--golden",
        action="store_true",
        help="Write directly to tests/golden/textual_ui instead of a temporary directory.",
    )
    return parser.parse_args()


async def _main_async() -> int:
    args = _parse_args()
    names = args.names or scenario_names()
    unknown = [name for name in names if name not in SCENARIOS]
    if unknown:
        raise SystemExit(f"Unknown scenario(s): {', '.join(unknown)}")
    target = golden_dir() if args.golden else args.output_dir
    written = await export_scenarios(names, target)
    for name in names:
        print(f"{name}:")
        for suffix, path in sorted(written[name].items()):
            print(f"  {suffix:<14} {path}")
    return 0


def main() -> int:
    return asyncio.run(_main_async())


if __name__ == "__main__":
    raise SystemExit(main())
