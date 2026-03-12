from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from textual import events
from textual.containers import VerticalScroll
from textual.geometry import Offset
from textual.selection import Selection
from textual.widgets import TextArea

from anteroom.cli.commands import CommandResult, ParsedSlashCommand
from anteroom.cli.textual_app import (
    AgentLoopTextualBackend,
    BoardWidget,
    Composer,
    SessionSnapshot,
    TextualChatApp,
    TranscriptPane,
)
from anteroom.db import init_db
from anteroom.services import storage
from anteroom.services.agent_loop import AgentEvent
from anteroom.services.artifact_registry import ArtifactRegistry
from anteroom.services.artifact_storage import create_artifact, get_artifact_by_fqn
from anteroom.services.pack_attachments import attach_pack
from anteroom.services.packs import ManifestArtifact, PackManifest, install_pack
from anteroom.services.space_storage import create_space


class ScriptedBackend:
    def __init__(
        self,
        history: list[tuple[str, str]] | None = None,
        *,
        prompt_history: list[str] | None = None,
        slash_results: dict[str, CommandResult] | None = None,
        skill_invocations: dict[str, str] | None = None,
    ) -> None:
        self.history = history or []
        self.prompt_history = prompt_history or []
        self.slash_results = slash_results or {}
        self.skill_invocations = skill_invocations or {}
        self.skill_registry = None
        self.active_model = "gpt-5.2"
        self.prompts: list[str] = []
        self.turns: list[asyncio.Queue[AgentEvent | None]] = []
        self.cancelled = 0

    def add_turn(self) -> asyncio.Queue[AgentEvent | None]:
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        self.turns.append(queue)
        return queue

    async def load_history(self) -> list[tuple[str, str]]:
        return list(self.history)

    async def load_prompt_history(self) -> list[str]:
        return list(self.prompt_history)

    async def append_prompt_history(self, prompt: str) -> None:
        self.prompt_history = [entry for entry in self.prompt_history if entry != prompt]
        self.prompt_history.insert(0, prompt)

    async def execute_slash_command(self, prompt: str) -> CommandResult | None:
        if prompt in self.skill_invocations:
            return CommandResult(
                kind="forward_prompt",
                command=ParsedSlashCommand(raw=prompt, name=prompt.split()[0].lower()),
                forward_prompt=self.skill_invocations[prompt],
                echo_user=False,
            )
        return self.slash_results.get(prompt)

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

    def start_new_conversation(self, *, conversation_type: str, title: str) -> None:
        self.history = []

    def set_active_model(self, model_name: str) -> None:
        self.active_model = model_name


def _session() -> SessionSnapshot:
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


def _backend_config(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        ai=SimpleNamespace(model="gpt-5.2"),
        app=SimpleNamespace(data_dir=data_dir),
        cli=SimpleNamespace(
            file_reference_max_chars=100_000,
            usage=SimpleNamespace(week_days=7, month_days=30),
        ),
        identity=None,
    )


class _FakeCompletionResponse:
    def __init__(self, content: str) -> None:
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self.content = content

    async def create(self, **kwargs):
        return _FakeCompletionResponse(self.content)


class _FakeMcpConfig:
    def __init__(self, command: str = "uvx", args: list[str] | None = None, timeout: int = 30, url: str | None = None):
        self.command = command
        self.args = args or []
        self.timeout = timeout
        self.url = url


class _FakeMcpManager:
    def __init__(self) -> None:
        self._statuses = {
            "docs": {
                "status": "connected",
                "transport": "stdio",
                "tool_count": 2,
                "error_message": "",
            },
            "broken": {
                "status": "error",
                "transport": "http",
                "tool_count": 0,
                "error_message": "timeout",
            },
        }
        self._configs = {
            "docs": _FakeMcpConfig(command="uvx", args=["docs-server"], timeout=45),
            "broken": _FakeMcpConfig(url="https://broken.example/mcp", timeout=10),
        }
        self._server_tools = {
            "docs": [
                {"name": "search_docs", "description": "Search internal docs"},
                {"name": "open_doc", "description": "Open a specific document"},
            ]
        }
        self.actions: list[tuple[str, str]] = []

    def get_server_statuses(self):
        return self._statuses

    def get_all_tools(self):
        return [{"name": "search_docs"}, {"name": "open_doc"}]

    async def connect_server(self, name: str):
        self.actions.append(("connect", name))
        self._statuses[name]["status"] = "connected"

    async def disconnect_server(self, name: str):
        self.actions.append(("disconnect", name))
        self._statuses[name]["status"] = "disconnected"

    async def reconnect_server(self, name: str):
        self.actions.append(("reconnect", name))
        self._statuses[name]["status"] = "connected"


async def _submit(pilot, app: TextualChatApp, prompt: str) -> None:
    composer = app.query_one("#composer", TextArea)
    composer.load_text(prompt)
    composer.focus()
    await pilot.press("enter")
    await pilot.pause()


@pytest.mark.asyncio
async def test_textual_app_renders_multi_tool_story() -> None:
    backend = ScriptedBackend()
    turn = backend.add_turn()
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        await _submit(pilot, app, 'Search for "def toggle" in src/ and read the match')
        assert backend.prompts == ['Search for "def toggle" in src/ and read the match']

        await turn.put(AgentEvent(kind="thinking", data={}))
        await turn.put(
            AgentEvent(
                kind="tool_call_start",
                data={"tool_name": "grep", "arguments": {"path": "src/", "pattern": "def toggle"}},
            )
        )
        await turn.put(AgentEvent(kind="tool_call_end", data={"tool_name": "grep", "status": "success", "output": {}}))
        await turn.put(
            AgentEvent(
                kind="tool_call_start",
                data={"tool_name": "read", "arguments": {"path": "src/anteroom/cli/renderer.py"}},
            )
        )
        await turn.put(AgentEvent(kind="tool_call_end", data={"tool_name": "read", "status": "success", "output": {}}))
        await turn.put(AgentEvent(kind="token", data={"content": "I found "}))
        await turn.put(AgentEvent(kind="token", data={"content": "`def toggle_last_fold()`"}))
        await turn.put(AgentEvent(kind="token", data={"content": " in `src/anteroom/cli/renderer.py`."}))
        await turn.put(
            AgentEvent(
                kind="assistant_message",
                data={"content": "I found `def toggle_last_fold()` in `src/anteroom/cli/renderer.py`."},
            )
        )
        await turn.put(AgentEvent(kind="done", data={}))
        await turn.put(None)
        await pilot.pause()

        procedure = app.query_one("#procedure-board", BoardWidget).plain_text
        tools = app.query_one("#tool-board", BoardWidget).plain_text
        transcript = app.query_one("#transcript-pane", TranscriptPane)

        assert "I'm identifying the files and symbols that matter." in procedure
        assert "I'm reading the relevant material closely." in procedure
        assert "I'm ready to answer with verified findings." in procedure
        assert 'Searching src/ for "def toggle"' in tools
        assert "Reading src/anteroom/cli/renderer.py" in tools
        assert transcript.read_only
        assert '###### YOU\n\n> Search for "def toggle" in src/ and read the match' in transcript.text
        assert "### AI" in transcript.text
        assert "`def toggle_last_fold()`" in transcript.text


@pytest.mark.asyncio
async def test_textual_app_keeps_completed_and_active_tools_visible() -> None:
    backend = ScriptedBackend()
    turn = backend.add_turn()
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        await _submit(pilot, app, "Find fold tests and then read them")

        await turn.put(
            AgentEvent(
                kind="tool_call_start",
                data={"tool_name": "glob", "arguments": {"path": "tests/unit", "glob": "*fold*.py"}},
            )
        )
        await pilot.pause()
        tools = app.query_one("#tool-board", BoardWidget).plain_text
        assert '◜ Searching tests/unit for "*fold*.py"' in tools

        await turn.put(AgentEvent(kind="tool_call_end", data={"tool_name": "glob", "status": "success", "output": {}}))
        await turn.put(
            AgentEvent(
                kind="tool_call_start",
                data={"tool_name": "read", "arguments": {"path": "tests/unit/test_focus_fold.py"}},
            )
        )
        await pilot.pause()

        tools = app.query_one("#tool-board", BoardWidget).plain_text
        assert '✓ Searching tests/unit for "*fold*.py"' in tools
        assert "◜ Reading tests/unit/test_focus_fold.py" in tools

        await turn.put(AgentEvent(kind="tool_call_end", data={"tool_name": "read", "status": "success", "output": {}}))
        await turn.put(AgentEvent(kind="assistant_message", data={"content": "Done."}))
        await turn.put(AgentEvent(kind="done", data={}))
        await turn.put(None)
        await pilot.pause()


@pytest.mark.asyncio
async def test_textual_app_tool_board_transitions_spinner_to_success_and_error() -> None:
    backend = ScriptedBackend()
    turn = backend.add_turn()
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        await _submit(pilot, app, "Inspect tool lifecycle")

        await turn.put(
            AgentEvent(
                kind="tool_call_start",
                data={"tool_name": "grep", "arguments": {"path": "src/", "pattern": "FoldGroup"}},
            )
        )
        await pilot.pause()

        tool_board = app.query_one("#tool-board", BoardWidget)
        first_frame = tool_board.plain_text
        assert '◜ Searching src/ for "FoldGroup"' in first_frame

        app._on_stream_heartbeat()
        await pilot.pause()
        second_frame = tool_board.plain_text
        assert '◠ Searching src/ for "FoldGroup"' in second_frame

        await turn.put(
            AgentEvent(kind="tool_call_end", data={"tool_name": "grep", "status": "success", "output": {}})
        )
        await pilot.pause()
        assert '✓ Searching src/ for "FoldGroup"' in tool_board.plain_text

        await turn.put(
            AgentEvent(
                kind="tool_call_start",
                data={"tool_name": "read", "arguments": {"path": "/root/secret.txt"}},
            )
        )
        await pilot.pause()
        assert "Reading /root/secret.txt" in tool_board.plain_text

        await turn.put(
            AgentEvent(
                kind="tool_call_end",
                data={"tool_name": "read", "status": "error", "output": {"error": "permission denied"}},
            )
        )
        await pilot.pause()
        assert "! Reading /root/secret.txt" in tool_board.plain_text

        await turn.put(AgentEvent(kind="assistant_message", data={"content": "Done."}))
        await turn.put(AgentEvent(kind="done", data={}))
        await turn.put(None)
        await pilot.pause()


@pytest.mark.asyncio
async def test_textual_app_handles_error_and_second_turn() -> None:
    backend = ScriptedBackend(history=[("assistant", "Previous answer.")])
    turn_one = backend.add_turn()
    turn_two = backend.add_turn()
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        await _submit(pilot, app, "Read a protected file")
        await turn_one.put(
            AgentEvent(
                kind="tool_call_start",
                data={"tool_name": "read", "arguments": {"path": "/root/secret.txt"}},
            )
        )
        await turn_one.put(
            AgentEvent(
                kind="tool_call_end",
                data={"tool_name": "read", "status": "error", "output": {"error": "permission denied"}},
            )
        )
        await turn_one.put(AgentEvent(kind="error", data={"message": "Permission denied", "retryable": False}))
        await turn_one.put(None)
        await pilot.pause()

        procedure = app.query_one("#procedure-board", BoardWidget).plain_text
        tools = app.query_one("#tool-board", BoardWidget).plain_text
        assert "failed, so I'm adjusting before I continue." in procedure
        assert "! Reading /root/secret.txt" in tools

        await _submit(pilot, app, "Try the public fixture instead")
        await turn_two.put(
            AgentEvent(
                kind="tool_call_start",
                data={"tool_name": "read", "arguments": {"path": "tests/unit/test_focus_fold.py"}},
            )
        )
        await turn_two.put(
            AgentEvent(kind="tool_call_end", data={"tool_name": "read", "status": "success", "output": {}})
        )
        await turn_two.put(AgentEvent(kind="assistant_message", data={"content": "The public fixture is readable."}))
        await turn_two.put(AgentEvent(kind="done", data={}))
        await turn_two.put(None)
        await pilot.pause()

        transcript = app.query_one("#transcript-pane", TranscriptPane).text
        assert "### AI\n\nPrevious answer." in transcript
        assert "### AI\n\nThe public fixture is readable." in transcript


@pytest.mark.asyncio
async def test_textual_app_shapes_markdown_in_transcript() -> None:
    backend = ScriptedBackend()
    turn = backend.add_turn()
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        await _submit(pilot, app, "Render markdown cleanly")
        await turn.put(
            AgentEvent(
                kind="assistant_message",
                data={
                    "content": "## Matches\n\n- **src/app.py**\n- `toggle_last_fold()`\n- [docs](https://example.com)"
                },
            )
        )
        await turn.put(AgentEvent(kind="done", data={}))
        await turn.put(None)
        await pilot.pause()

        transcript = app.query_one("#transcript-pane", TranscriptPane).text
        assert "## Matches" in transcript
        assert "**src/app.py**" in transcript
        assert "`toggle_last_fold()`" in transcript
        assert "[docs](https://example.com)" in transcript


@pytest.mark.asyncio
async def test_textual_app_native_question_and_approval_dialogs() -> None:
    backend = ScriptedBackend()
    backend.add_turn()
    ui_bridge: dict[str, object] = {}
    app = TextualChatApp(backend=backend, session=_session(), ui_bridge=ui_bridge)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert callable(ui_bridge["ask"])
        assert callable(ui_bridge["confirm"])

        ask_task = asyncio.create_task(ui_bridge["ask"]("Choose a path", ["tests", "src"]))  # type: ignore[index]
        await pilot.pause()
        await pilot.click("#option-2")
        assert await ask_task == "src"

        verdict = SimpleNamespace(
            reason="Destructive command detected",
            details={"command": "rm -rf ."},
            tool_name="bash",
        )
        confirm_task = asyncio.create_task(ui_bridge["confirm"](verdict))  # type: ignore[index]
        await pilot.pause()
        await pilot.click("#session")
        assert await confirm_task == "session"


@pytest.mark.asyncio
async def test_textual_app_focuses_composer_on_mount_and_after_modal() -> None:
    backend = ScriptedBackend()
    backend.add_turn()
    ui_bridge: dict[str, object] = {}
    app = TextualChatApp(backend=backend, session=_session(), ui_bridge=ui_bridge)

    async with app.run_test() as pilot:
        await pilot.pause()
        composer = app.query_one("#composer", TextArea)
        assert composer.has_focus

        ask_task = asyncio.create_task(ui_bridge["ask"]("Choose a path", ["tests", "src"]))  # type: ignore[index]
        await pilot.pause()
        await pilot.click("#option-1")
        assert await ask_task == "tests"
        await pilot.pause()

        composer = app.query_one("#composer", TextArea)
        assert composer.has_focus


@pytest.mark.asyncio
async def test_textual_app_composer_enter_sends_newline_shortcuts_work_and_ctrl_c_clears() -> None:
    backend = ScriptedBackend()
    turn = backend.add_turn()
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        composer = app.query_one("#composer", TextArea)
        composer.focus()
        composer.load_text("line one")
        composer.cursor_location = (0, len("line one"))
        await pilot.press("alt+enter")
        await pilot.press("l", "i", "n", "e", " ", "t", "w", "o")
        await pilot.pause()

        assert composer.text == "line one\nline two"
        assert backend.prompts == []

        await pilot.press("ctrl+c")
        await pilot.pause()

        assert composer.text == ""
        assert backend.prompts == []

        composer.load_text("line one\nline two")
        composer.focus()
        await pilot.press("enter")
        await pilot.pause()

        assert backend.prompts == ["line one\nline two"]
        await turn.put(AgentEvent(kind="assistant_message", data={"content": "ok"}))
        await turn.put(AgentEvent(kind="done", data={}))
        await turn.put(None)
        await pilot.pause()


@pytest.mark.asyncio
async def test_textual_app_up_and_down_recall_prompt_history() -> None:
    backend = ScriptedBackend(prompt_history=["most recent prompt", "older prompt"])
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        composer = app.query_one("#composer", TextArea)
        composer.focus()
        composer.load_text("")

        await pilot.press("up")
        await pilot.pause()
        assert composer.text == "most recent prompt"

        await pilot.press("up")
        await pilot.pause()
        assert composer.text == "older prompt"

        await pilot.press("down")
        await pilot.pause()
        assert composer.text == "most recent prompt"

        composer.load_text("")
        await pilot.press("d", "r", "a", "f", "t")
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        assert composer.text == "draft"


@pytest.mark.asyncio
async def test_textual_app_handles_slash_commands_before_model_submission() -> None:
    backend = ScriptedBackend(
        slash_results={
            "/help": CommandResult(
                kind="show_help",
                command=ParsedSlashCommand(raw="/help", name="/help"),
            ),
            "/new": CommandResult(
                kind="new_conversation",
                command=ParsedSlashCommand(raw="/new", name="/new"),
                echo_user=False,
            ),
        }
    )
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        await _submit(pilot, app, "/help")
        transcript = app.query_one("#transcript-pane", TranscriptPane).text
        assert backend.prompts == []
        assert "## Slash Commands" in transcript
        assert "###### YOU\n\n> /help" in transcript

        await _submit(pilot, app, "/new")
        transcript = app.query_one("#transcript-pane", TranscriptPane).text
        assert "Started a new conversation." in transcript
        assert "/help" not in transcript


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt", "result", "backend_method", "confirm_choice", "expected_called", "expected_text"),
    [
        (
            "/space delete beta",
            CommandResult(
                kind="delete_space",
                command=ParsedSlashCommand(raw="/space delete beta", name="/space", arg="delete beta"),
                space_target="beta",
                echo_user=False,
            ),
            "_delete_space",
            "Delete",
            ["beta"],
            "Deleted space **beta**.",
        ),
        (
            "/artifact delete @local/instruction/focus-fold",
            CommandResult(
                kind="delete_artifact",
                command=ParsedSlashCommand(
                    raw="/artifact delete @local/instruction/focus-fold",
                    name="/artifact",
                    arg="delete @local/instruction/focus-fold",
                ),
                artifact_fqn="@local/instruction/focus-fold",
                echo_user=False,
            ),
            "_delete_artifact",
            "Delete",
            ["@local/instruction/focus-fold"],
            "Deleted `@local/instruction/focus-fold`.",
        ),
        (
            "/pack remove demo/focus-fold",
            CommandResult(
                kind="delete_pack",
                command=ParsedSlashCommand(
                    raw="/pack remove demo/focus-fold",
                    name="/pack",
                    arg="remove demo/focus-fold",
                ),
                pack_ref="demo/focus-fold",
                echo_user=False,
            ),
            "_remove_pack",
            "Remove",
            ["demo/focus-fold"],
            "Removed `@demo/focus-fold`.",
        ),
    ],
)
async def test_textual_app_confirms_destructive_slash_commands(
    prompt: str,
    result: CommandResult,
    backend_method: str,
    confirm_choice: str,
    expected_called: list[str],
    expected_text: str,
) -> None:
    backend = ScriptedBackend(slash_results={prompt: result})
    app = TextualChatApp(backend=backend, session=_session())
    calls: list[str] = []

    def _handler(target: str) -> str:
        calls.append(target)
        return expected_text

    setattr(backend, backend_method, _handler)

    async def _fake_confirm(question: str, options: list[str] | None) -> str:
        assert confirm_choice in (options or [])
        return confirm_choice

    app._ask_user_dialog = _fake_confirm  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await _submit(pilot, app, prompt)
        transcript = app.query_one("#transcript-pane", TranscriptPane).text
        assert calls == expected_called
        assert expected_text in transcript


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt", "result", "backend_method"),
    [
        (
            "/space delete beta",
            CommandResult(
                kind="delete_space",
                command=ParsedSlashCommand(raw="/space delete beta", name="/space", arg="delete beta"),
                space_target="beta",
                echo_user=False,
            ),
            "_delete_space",
        ),
        (
            "/artifact delete @local/instruction/focus-fold",
            CommandResult(
                kind="delete_artifact",
                command=ParsedSlashCommand(
                    raw="/artifact delete @local/instruction/focus-fold",
                    name="/artifact",
                    arg="delete @local/instruction/focus-fold",
                ),
                artifact_fqn="@local/instruction/focus-fold",
                echo_user=False,
            ),
            "_delete_artifact",
        ),
        (
            "/pack remove demo/focus-fold",
            CommandResult(
                kind="delete_pack",
                command=ParsedSlashCommand(
                    raw="/pack remove demo/focus-fold",
                    name="/pack",
                    arg="remove demo/focus-fold",
                ),
                pack_ref="demo/focus-fold",
                echo_user=False,
            ),
            "_remove_pack",
        ),
    ],
)
async def test_textual_app_cancels_destructive_slash_commands(
    prompt: str,
    result: CommandResult,
    backend_method: str,
) -> None:
    backend = ScriptedBackend(slash_results={prompt: result})
    app = TextualChatApp(backend=backend, session=_session())
    calls: list[str] = []

    def _handler(target: str) -> str:
        calls.append(target)
        return "should not run"

    setattr(backend, backend_method, _handler)

    async def _fake_cancel(_question: str, _options: list[str] | None) -> str:
        return "Cancel"

    app._ask_user_dialog = _fake_cancel  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await _submit(pilot, app, prompt)
        transcript = app.query_one("#transcript-pane", TranscriptPane).text
        assert calls == []
        assert "Cancelled." in transcript


@pytest.mark.asyncio
async def test_textual_app_custom_skill_invocation_submits_expanded_prompt() -> None:
    backend = ScriptedBackend(
        skill_invocations={
            "/deploy-check staging": "Check that staging is healthy, run the deploy checklist, and report blockers."
        }
    )
    turn = backend.add_turn()
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        await _submit(pilot, app, "/deploy-check staging")
        assert backend.prompts == ["Check that staging is healthy, run the deploy checklist, and report blockers."]

        await turn.put(AgentEvent(kind="assistant_message", data={"content": "Staging looks healthy."}))
        await turn.put(AgentEvent(kind="done", data={}))
        await turn.put(None)
        await pilot.pause()

        transcript = app.query_one("#transcript-pane", TranscriptPane).text
        assert "###### YOU\n\n> /deploy-check staging" in transcript
        assert "### AI\n\nStaging looks healthy." in transcript


@pytest.mark.asyncio
async def test_textual_app_streams_progressively_between_tokens() -> None:
    backend = ScriptedBackend()
    turn = backend.add_turn()
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        await _submit(pilot, app, "Stream a short answer")

        await turn.put(AgentEvent(kind="token", data={"content": "As "}))
        await pilot.pause()
        transcript = app.query_one("#transcript-pane", TranscriptPane).text
        assert "### AI\n\nAs" in transcript
        assert "_waiting for continuation..._" in transcript

        await turn.put(AgentEvent(kind="token", data={"content": "of today"}))
        await pilot.pause()
        transcript = app.query_one("#transcript-pane", TranscriptPane).text
        assert "### AI\n\nAs of today" in transcript
        assert "_waiting for continuation..._" in transcript

        await turn.put(AgentEvent(kind="token", data={"content": ", the answer is streaming."}))
        await pilot.pause()
        transcript = app.query_one("#transcript-pane", TranscriptPane).text
        assert "### AI\n\nAs of today, the answer is streaming." in transcript
        assert "_waiting for continuation..._" in transcript

        await turn.put(AgentEvent(kind="assistant_message", data={"content": "As of today, the answer is streaming."}))
        await turn.put(AgentEvent(kind="done", data={}))
        await turn.put(None)
        await pilot.pause()
        assert "_waiting for continuation..._" not in app.query_one("#transcript-pane", TranscriptPane).text


@pytest.mark.asyncio
async def test_textual_app_surfaces_stream_stall(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("anteroom.cli.textual_app._STREAM_STALL_SECS", 0.05)

    backend = ScriptedBackend()
    turn = backend.add_turn()
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        await _submit(pilot, app, "Stream and then stall")

        await turn.put(AgentEvent(kind="token", data={"content": "Partial answer"}))
        await pilot.pause()
        await asyncio.sleep(0.12)
        app._on_stream_heartbeat()
        await pilot.pause()

        procedure = app.query_one("#procedure-board", BoardWidget).plain_text
        trace = app.query_one("#trace-board", BoardWidget).plain_text

        assert "gone quiet" in procedure
        assert "stream ·" in trace
        assert "1 chunks" in trace

        await turn.put(AgentEvent(kind="assistant_message", data={"content": "Partial answer, now complete."}))
        await turn.put(AgentEvent(kind="done", data={}))
        await turn.put(None)
        await pilot.pause(0.1)


@pytest.mark.asyncio
async def test_textual_app_marks_partial_stream_as_interrupted() -> None:
    backend = ScriptedBackend()
    turn = backend.add_turn()
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        await _submit(pilot, app, "Give me a streamed answer")

        await turn.put(AgentEvent(kind="token", data={"content": "As of March 7, 2026:"}))
        await turn.put(AgentEvent(kind="token", data={"content": "\n\n- United States: Donald J. Trump"}))
        await pilot.pause()
        await turn.put(
            AgentEvent(
                kind="error",
                data={"message": "Stream timed out after 30s — response may be incomplete", "retryable": True},
            )
        )
        await turn.put(None)
        await pilot.pause()

        transcript = app.query_one("#transcript-pane", TranscriptPane).text
        procedure = app.query_one("#procedure-board", BoardWidget).plain_text

        assert "stream interrupted" in transcript
        assert "response may be incomplete" in transcript
        assert "timed out" in procedure


@pytest.mark.asyncio
async def test_textual_app_retry_starts_fresh_ai_bubble() -> None:
    backend = ScriptedBackend()
    turn = backend.add_turn()
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        await _submit(pilot, app, "Retry the interrupted answer")

        await turn.put(AgentEvent(kind="token", data={"content": "If"}))
        await turn.put(
            AgentEvent(
                kind="error",
                data={"message": "Stream ended unexpectedly — response may be incomplete", "retryable": True},
            )
        )
        await turn.put(AgentEvent(kind="retrying", data={"attempt": 2, "max_attempts": 2, "reason": "turn_retry"}))
        await turn.put(AgentEvent(kind="token", data={"content": "Final answer."}))
        await turn.put(AgentEvent(kind="assistant_message", data={"content": "Final answer."}))
        await turn.put(AgentEvent(kind="done", data={}))
        await turn.put(None)
        await pilot.pause()

        transcript = app.query_one("#transcript-pane", TranscriptPane).text
        assert transcript.count("### AI") == 2
        assert "stream interrupted" in transcript
        assert transcript.rstrip().endswith("### AI\n\nFinal answer.")


@pytest.mark.asyncio
async def test_textual_backend_lists_and_searches_conversations(tmp_path) -> None:
    db = init_db(tmp_path / "textual.db")
    conv_a = storage.create_conversation(db, title="Focus Fold", working_dir=str(tmp_path))
    conv_b = storage.create_conversation(
        db,
        title="Renderer Cleanup",
        working_dir=str(tmp_path),
    )
    storage.update_conversation_slug(db, conv_a["id"], "focus-fold")
    storage.update_conversation_slug(db, conv_b["id"], "renderer-cleanup")
    storage.create_message(db, conv_a["id"], "user", "Need to improve fold UX.")
    storage.create_message(db, conv_b["id"], "user", "Need to clean up renderer output.")

    backend = AgentLoopTextualBackend(
        config=_backend_config(tmp_path),
        db=db,
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=None,
        extra_system_prompt="",
        working_dir=str(tmp_path),
    )

    list_result = await backend.execute_slash_command("/list")
    search_result = await backend.execute_slash_command("/search fold")

    assert list_result is not None
    assert list_result.kind == "list_conversations"
    listing = backend._list_conversations_markdown(list_result.list_limit or 20)
    assert "Recent Conversations" in listing
    assert "Focus Fold" in listing
    assert "Renderer Cleanup" in listing

    assert search_result is not None
    assert search_result.kind == "search_conversations"
    search_listing = backend._search_conversations_markdown(search_result.search_query or "")
    assert "Search results" in search_listing
    assert "Focus Fold" in search_listing


@pytest.mark.asyncio
async def test_textual_backend_resumes_renames_and_updates_slug(tmp_path) -> None:
    db = init_db(tmp_path / "textual_resume.db")
    conv = storage.create_conversation(db, title="Old Title", working_dir=str(tmp_path))
    storage.update_conversation_slug(db, conv["id"], "old-title")
    storage.create_message(db, conv["id"], "user", "Original prompt.")
    storage.create_message(db, conv["id"], "assistant", "Original answer.")

    backend = AgentLoopTextualBackend(
        config=_backend_config(tmp_path),
        db=db,
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=None,
        extra_system_prompt="",
        working_dir=str(tmp_path),
    )

    resume_result = await backend.execute_slash_command("/resume old-title")
    assert resume_result is not None
    assert resume_result.kind == "resume_conversation"

    summary = await backend.resume_conversation(resume_result.resume_target)
    history = await backend.load_history()
    assert "Resumed **Old Title**" in summary
    assert history == [("user", "Original prompt."), ("assistant", "Original answer.")]

    rename_result = await backend.execute_slash_command("/rename New Title")
    assert rename_result is not None
    assert rename_result.kind == "rename_conversation"
    assert backend._rename_conversation(rename_result.command.arg) == "Renamed conversation to **New Title**."

    slug_result = await backend.execute_slash_command("/slug new-title")
    assert slug_result is not None
    assert slug_result.kind == "set_slug"
    assert backend._slug_command(slug_result.slug_value or "") == "Slug set to `new-title`."

    updated = storage.get_conversation(db, conv["id"])
    assert updated is not None
    assert updated["title"] == "New Title"
    assert updated["slug"] == "new-title"


@pytest.mark.asyncio
async def test_textual_backend_deletes_conversation(tmp_path) -> None:
    db = init_db(tmp_path / "textual_delete.db")
    conv = storage.create_conversation(db, title="Delete Me", working_dir=str(tmp_path))
    storage.update_conversation_slug(db, conv["id"], "delete-me")

    backend = AgentLoopTextualBackend(
        config=_backend_config(tmp_path),
        db=db,
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=None,
        extra_system_prompt="",
        working_dir=str(tmp_path),
        resume_conversation_id=conv["id"],
    )
    await backend.load_history()

    message = await backend.delete_conversation("delete-me")
    assert message == "Deleted **Delete Me**."
    assert storage.get_conversation(db, conv["id"]) is None


@pytest.mark.asyncio
async def test_textual_backend_compacts_current_conversation(tmp_path) -> None:
    db = init_db(tmp_path / "textual_compact.db")
    conv = storage.create_conversation(db, title="Compact Me", working_dir=str(tmp_path))
    storage.create_message(db, conv["id"], "user", "First question.")
    storage.create_message(db, conv["id"], "assistant", "First answer.")
    storage.create_message(db, conv["id"], "user", "Second question.")
    storage.create_message(db, conv["id"], "assistant", "Second answer.")

    ai_service = SimpleNamespace(
        config=SimpleNamespace(model="gpt-5.2"),
        client=SimpleNamespace(
            chat=SimpleNamespace(
                completions=_FakeCompletions("Condensed summary of the prior conversation."),
            )
        ),
    )
    backend = AgentLoopTextualBackend(
        config=_backend_config(tmp_path),
        db=db,
        ai_service=ai_service,
        tool_executor=None,
        tools_openai=None,
        extra_system_prompt="",
        working_dir=str(tmp_path),
        resume_conversation_id=conv["id"],
    )

    await backend.load_history()
    message = await backend.compact_current_conversation()

    assert message == "Compacted **4** messages into a working summary."
    assert backend._messages == [
        {
            "role": "system",
            "content": (
                "Previous conversation summary (auto-compacted from 4 messages):\n\n"
                "Condensed summary of the prior conversation."
            ),
        }
    ]


@pytest.mark.asyncio
async def test_textual_backend_cycles_verbosity(tmp_path) -> None:
    backend = AgentLoopTextualBackend(
        config=_backend_config(tmp_path),
        db=init_db(tmp_path / "textual_verbose.db"),
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=None,
        extra_system_prompt="",
        working_dir=str(tmp_path),
    )

    first = await backend.execute_slash_command("/verbose")
    second = await backend.execute_slash_command("/verbose")
    third = await backend.execute_slash_command("/verbose")

    assert first is not None and first.message == "Verbosity: **detailed**"
    assert second is not None and second.message == "Verbosity: **verbose**"
    assert third is not None and third.message == "Verbosity: **compact**"


@pytest.mark.asyncio
async def test_textual_backend_renders_last_turn_tool_detail(tmp_path) -> None:
    backend = AgentLoopTextualBackend(
        config=_backend_config(tmp_path),
        db=init_db(tmp_path / "textual_detail.db"),
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=None,
        extra_system_prompt="",
        working_dir=str(tmp_path),
    )
    backend._last_turn_tools = [
        {
            "tool_name": "read",
            "arguments": {"path": "tests/unit/test_focus_fold.py"},
            "status": "success",
            "output": {"content": "def test_focus_fold():\n    pass\n"},
            "elapsed": 0.2,
        }
    ]

    result = await backend.execute_slash_command("/detail")

    assert result is not None
    assert result.kind == "show_message"
    assert "Last Turn Tool Calls" in (result.message or "")
    assert "**read**" in (result.message or "")
    assert "tests/unit/test_focus_fold.py" in (result.message or "")


@pytest.mark.asyncio
async def test_textual_backend_plan_mode_commands(tmp_path) -> None:
    db = init_db(tmp_path / "textual_plan.db")
    backend = AgentLoopTextualBackend(
        config=_backend_config(tmp_path),
        db=db,
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=[
            {"function": {"name": "read_file"}},
            {"function": {"name": "edit_file"}},
            {"function": {"name": "bash"}},
        ],
        extra_system_prompt="base prompt",
        working_dir=str(tmp_path),
    )

    on_result = await backend.execute_slash_command("/plan on")
    assert on_result is not None
    assert on_result.kind == "set_plan_mode"
    assert backend._set_plan_mode(on_result.plan_mode_enabled is True).startswith("Planning mode active")
    assert backend.plan_mode_active() is True
    assert backend._pending_plan_activation is True

    status_result = await backend.execute_slash_command("/plan status")
    assert status_result is not None
    assert status_result.kind == "show_plan_status"
    assert "Planning Mode" in backend._plan_status_markdown()

    inline_result = await backend.execute_slash_command("/plan inspect renderer handoff")
    assert inline_result is not None
    assert inline_result.kind == "forward_prompt"
    assert inline_result.forward_prompt == "inspect renderer handoff"

    conv = storage.create_conversation(db, title="Planning Thread", working_dir=str(tmp_path))
    backend._conversation = conv
    backend.resume_conversation_id = conv["id"]
    backend._apply_plan_mode(conv["id"])
    assert backend._plan_file is not None
    assert "planning_mode" in backend.extra_system_prompt
    assert [tool["function"]["name"] for tool in (backend.tools_openai or [])] == ["read_file", "bash"]

    off_result = await backend.execute_slash_command("/plan off")
    assert off_result is not None
    assert off_result.kind == "set_plan_mode"
    assert backend._set_plan_mode(off_result.plan_mode_enabled is True) == "Planning mode off. Full tools restored."
    assert backend.plan_mode_active() is False
    assert "planning_mode" not in backend.extra_system_prompt


@pytest.mark.asyncio
async def test_textual_app_syncs_plan_mode_into_session(tmp_path) -> None:
    backend = AgentLoopTextualBackend(
        config=_backend_config(tmp_path),
        db=init_db(tmp_path / "textual_plan_app.db"),
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=None,
        extra_system_prompt="",
        working_dir=str(tmp_path),
    )
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        await _submit(pilot, app, "/plan on")
        assert app.session.plan_mode is True

        await _submit(pilot, app, "/plan off")
        assert app.session.plan_mode is False


@pytest.mark.asyncio
async def test_textual_backend_mcp_commands(tmp_path) -> None:
    backend = AgentLoopTextualBackend(
        config=_backend_config(tmp_path),
        db=init_db(tmp_path / "textual_mcp.db"),
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=None,
        extra_system_prompt="",
        working_dir=str(tmp_path),
        mcp_manager=_FakeMcpManager(),
    )

    status_result = await backend.execute_slash_command("/mcp")
    detail_result = await backend.execute_slash_command("/mcp status docs")
    action_result = await backend.execute_slash_command("/mcp reconnect broken")

    assert status_result is not None
    assert status_result.kind == "show_mcp_status"
    status_message = backend._mcp_status_markdown()
    assert "MCP Servers" in status_message
    assert "docs" in status_message
    assert "broken" in status_message

    assert detail_result is not None
    assert detail_result.kind == "show_mcp_server_detail"
    detail_message = backend._mcp_server_detail_markdown(detail_result.mcp_server_name or "")
    assert "MCP Server: docs" in detail_message
    assert "search_docs" in detail_message

    assert action_result is not None
    assert action_result.kind == "run_mcp_action"
    assert action_result.mcp_action == "reconnect"
    action_message = await backend._run_mcp_action(action_result.mcp_action or "", action_result.mcp_server_name or "")
    assert "reconnect" in action_message
    assert backend.mcp_manager.actions == [("reconnect", "broken")]


@pytest.mark.asyncio
async def test_textual_backend_mcp_commands_without_manager(tmp_path) -> None:
    backend = AgentLoopTextualBackend(
        config=_backend_config(tmp_path),
        db=init_db(tmp_path / "textual_mcp_none.db"),
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=None,
        extra_system_prompt="",
        working_dir=str(tmp_path),
    )

    result = await backend.execute_slash_command("/mcp")

    assert result is not None
    assert result.kind == "show_mcp_status"
    assert backend._mcp_status_markdown() == "No MCP servers configured."


@pytest.mark.asyncio
async def test_textual_backend_mcp_unknown_server_reports_available_names(tmp_path) -> None:
    backend = AgentLoopTextualBackend(
        config=_backend_config(tmp_path),
        db=init_db(tmp_path / "textual_mcp_unknown.db"),
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=None,
        extra_system_prompt="",
        working_dir=str(tmp_path),
        mcp_manager=_FakeMcpManager(),
    )

    result = await backend.execute_slash_command("/mcp status missing")

    assert result is not None
    assert result.kind == "show_mcp_server_detail"
    message = backend._mcp_server_detail_markdown(result.mcp_server_name or "")
    assert "Unknown MCP server `missing`." in message
    assert "broken" in message
    assert "docs" in message


@pytest.mark.asyncio
async def test_textual_backend_rewind_lists_and_executes(tmp_path) -> None:
    db = init_db(tmp_path / "textual_rewind.db")
    conv = storage.create_conversation(db, title="Rewind Me", working_dir=str(tmp_path))
    storage.create_message(db, conv["id"], "user", "First prompt.")
    storage.create_message(db, conv["id"], "assistant", "First answer.")
    storage.create_message(db, conv["id"], "user", "Second prompt.")
    storage.create_message(db, conv["id"], "assistant", "Second answer.")

    backend = AgentLoopTextualBackend(
        config=_backend_config(tmp_path),
        db=db,
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=None,
        extra_system_prompt="",
        working_dir=str(tmp_path),
        resume_conversation_id=conv["id"],
    )

    await backend.load_history()
    listing = await backend.execute_slash_command("/rewind")
    rewind = await backend.execute_slash_command("/rewind 1")

    assert listing is not None
    assert listing.kind == "rewind_conversation"
    listing_message = await backend.rewind_current_conversation(listing.rewind_arg)
    assert "Rewind Conversation" in listing_message
    assert "Use `/rewind <position>`" in listing_message

    assert rewind is not None
    assert rewind.kind == "rewind_conversation"
    assert "Rewound **2** message(s) to position `1`." in (await backend.rewind_current_conversation(rewind.rewind_arg))
    history = await backend.load_history()
    assert history == [("user", "First prompt."), ("assistant", "First answer.")]


@pytest.mark.asyncio
async def test_textual_app_reloads_transcript_after_rewind(tmp_path) -> None:
    db = init_db(tmp_path / "textual_rewind_app.db")
    conv = storage.create_conversation(db, title="Transcript Rewind", working_dir=str(tmp_path))
    storage.create_message(db, conv["id"], "user", "Question one.")
    storage.create_message(db, conv["id"], "assistant", "Answer one.")
    storage.create_message(db, conv["id"], "user", "Question two.")
    storage.create_message(db, conv["id"], "assistant", "Answer two.")

    backend = AgentLoopTextualBackend(
        config=_backend_config(tmp_path),
        db=db,
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=None,
        extra_system_prompt="",
        working_dir=str(tmp_path),
        resume_conversation_id=conv["id"],
    )
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        await pilot.pause()
        transcript = app.query_one("#transcript-pane", TranscriptPane)
        assert "Question two." in transcript.text

        await _submit(pilot, app, "/rewind 1")
        await pilot.pause(0.1)

        assert "Question two." not in transcript.text
        assert "Answer two." not in transcript.text
        assert "Rewound **2** message(s) to position `1`." in transcript.text


@pytest.mark.asyncio
async def test_textual_backend_space_commands(tmp_path) -> None:
    db = init_db(tmp_path / "textual_space.db")
    alpha_source = tmp_path / "alpha-space.yaml"
    alpha_source.write_text(
        "name: alpha\nversion: '1'\ninstructions: Refreshed alpha rules.\nconfig:\n  model: gpt-5.4-mini\n"
    )
    alpha = create_space(
        db,
        "alpha",
        instructions="Use alpha rules.",
        model="gpt-5",
        source_file=str(alpha_source),
    )
    create_space(db, "beta", instructions="Use beta rules.")
    conv = storage.create_conversation(db, title="Space Thread", working_dir=str(tmp_path))

    backend = AgentLoopTextualBackend(
        config=_backend_config(tmp_path),
        db=db,
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=None,
        extra_system_prompt="base prompt",
        working_dir=str(tmp_path),
        resume_conversation_id=conv["id"],
    )

    await backend.load_history()
    created = await backend.execute_slash_command("/space create gamma")
    listing = await backend.execute_slash_command("/space")
    detail = await backend.execute_slash_command("/space show alpha")
    switched = await backend.execute_slash_command("/space switch alpha")
    sources = await backend.execute_slash_command("/space sources alpha")
    edited = await backend.execute_slash_command("/space edit model gpt-5.4-mini")
    refreshed = await backend.execute_slash_command("/space refresh alpha")
    exported = await backend.execute_slash_command("/space export alpha")

    assert created is not None
    assert created.kind == "create_space"
    assert backend._create_space(created.space_target or "") == "Created space **gamma**."

    assert listing is not None
    assert listing.kind == "show_spaces"
    listing_message = backend._space_list_markdown()
    assert "## Spaces" in listing_message
    assert "alpha" in listing_message
    assert "beta" in listing_message
    assert "gamma" in listing_message

    assert detail is not None
    assert detail.kind == "show_space"
    detail_message = backend._space_show_markdown(detail.space_target or "")
    assert "## Space: alpha" in detail_message
    assert "Use alpha rules." in detail_message

    assert switched is not None
    assert switched.kind == "set_space"
    assert backend._switch_space(switched.space_target or "") == "Active space: **alpha**"
    assert backend._active_space is not None and backend._active_space["id"] == alpha["id"]
    assert "space_instructions" in backend.extra_system_prompt
    refreshed_conv = storage.get_conversation(db, conv["id"])
    assert refreshed_conv is not None and refreshed_conv["space_id"] == alpha["id"]

    assert sources is not None
    assert sources.kind == "show_space_sources"
    assert backend._space_sources_markdown(sources.space_target or "") == "No sources linked to space `alpha`."

    assert edited is not None
    assert edited.kind == "update_space"
    assert backend._edit_space(edited.space_edit_field or "", edited.space_edit_value or "") == (
        "Updated model for **alpha**: `gpt-5.4-mini`"
    )

    assert refreshed is not None
    assert refreshed.kind == "refresh_space"
    assert backend._refresh_space(refreshed.space_target or "") == "Refreshed space **alpha**."
    assert backend._active_space is not None
    assert backend._active_space["model"] == "gpt-5.4-mini"
    assert "Refreshed alpha rules." in backend.extra_system_prompt

    assert exported is not None
    assert exported.kind == "export_space"
    exported_message = backend._export_space(exported.space_target or "")
    assert "## Space YAML: alpha" in exported_message
    assert "instructions: Refreshed alpha rules." in exported_message

    cleared = await backend.execute_slash_command("/space clear")
    assert cleared is not None
    assert cleared.kind == "set_space"
    assert backend._clear_space() == "Cleared space: **alpha**"
    assert backend._active_space is None
    assert "space_instructions" not in backend.extra_system_prompt
    refreshed_conv = storage.get_conversation(db, conv["id"])
    assert refreshed_conv is not None and refreshed_conv["space_id"] is None

    deleted = await backend.execute_slash_command("/space delete beta")
    assert deleted is not None
    assert deleted.kind == "delete_space"
    assert backend._delete_space(deleted.space_target or "") == "Deleted space **beta**."


@pytest.mark.asyncio
async def test_textual_backend_artifact_commands_refresh_registry_and_prompt(tmp_path) -> None:
    db = init_db(tmp_path / "textual_artifact.db")
    create_artifact(
        db,
        fqn="@local/instruction/focus-fold",
        artifact_type="instruction",
        namespace="local",
        name="focus-fold",
        content="Always explain the fold story clearly.",
        source="local",
    )
    create_artifact(
        db,
        fqn="@local/skill/debug-trace",
        artifact_type="skill",
        namespace="local",
        name="debug-trace",
        content="name: debug-trace\ndescription: Debug trace helper\nprompt: Help debug traces.\n",
        source="local",
    )
    registry = ArtifactRegistry()
    registry.load_from_db(db)

    backend = AgentLoopTextualBackend(
        config=_backend_config(tmp_path),
        db=db,
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=[],
        extra_system_prompt="base prompt",
        working_dir=str(tmp_path),
        artifact_registry=registry,
    )
    backend._refresh_artifact_prompt()

    listing = await backend.execute_slash_command("/artifact")
    detail = await backend.execute_slash_command("/artifact show @local/instruction/focus-fold")

    assert listing is not None
    assert listing.kind == "show_artifacts"
    listing_message = backend._artifact_list_markdown("")
    assert "## Artifacts" in listing_message
    assert "@local/instruction/focus-fold" in listing_message
    assert "@local/skill/debug-trace" in listing_message

    assert detail is not None
    assert detail.kind == "show_artifact"
    detail_message = backend._artifact_show_markdown(detail.artifact_fqn or "")
    assert "## Artifact: @local/instruction/focus-fold" in detail_message
    assert "Always explain the fold story clearly." in detail_message

    assert "Always explain the fold story clearly." in backend.extra_system_prompt
    deleted = await backend.execute_slash_command("/artifact delete @local/instruction/focus-fold")
    assert deleted is not None
    assert deleted.kind == "delete_artifact"
    assert backend._delete_artifact(deleted.artifact_fqn or "") == "Deleted `@local/instruction/focus-fold`."
    assert get_artifact_by_fqn(db, "@local/instruction/focus-fold") is None
    assert registry.get("@local/instruction/focus-fold") is None
    assert "Always explain the fold story clearly." not in backend.extra_system_prompt


@pytest.mark.asyncio
async def test_textual_backend_pack_commands_refresh_registry_and_prompt(tmp_path) -> None:
    db = init_db(tmp_path / "textual_pack.db")
    pack_dir = tmp_path / "demo-pack"
    (pack_dir / "instructions").mkdir(parents=True)
    (pack_dir / "pack.yaml").write_text(
        "\n".join(
            [
                "name: focus-fold",
                "namespace: demo",
                "version: 1.2.3",
                "description: Focus fold guidance",
                "artifacts:",
                "  - type: instruction",
                "    name: fold-guidance",
            ]
        ),
        encoding="utf-8",
    )
    (pack_dir / "instructions" / "fold-guidance.md").write_text(
        "Explain fold transitions clearly.",
        encoding="utf-8",
    )
    manifest = PackManifest(
        name="focus-fold",
        namespace="demo",
        version="1.2.3",
        description="Focus fold guidance",
        artifacts=(ManifestArtifact(type="instruction", name="fold-guidance"),),
    )
    install_pack(db, manifest, pack_dir)
    pack_row = db.execute("SELECT id FROM packs WHERE namespace = ? AND name = ?", ("demo", "focus-fold")).fetchone()
    assert pack_row is not None
    attach_pack(db, pack_row["id"])

    registry = ArtifactRegistry()
    registry.load_from_db(db)
    backend = AgentLoopTextualBackend(
        config=_backend_config(tmp_path),
        db=db,
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=[],
        extra_system_prompt="base prompt",
        working_dir=str(tmp_path),
        artifact_registry=registry,
    )
    backend._refresh_artifact_prompt()

    listing = await backend.execute_slash_command("/pack")
    detail = await backend.execute_slash_command("/pack show demo/focus-fold")
    detached = await backend.execute_slash_command("/pack detach demo/focus-fold")
    reattached = await backend.execute_slash_command("/pack attach demo/focus-fold")

    assert listing is not None
    assert listing.kind == "show_packs"
    listing_message = backend._pack_list_markdown()
    assert "## Installed Packs" in listing_message
    assert "@demo/focus-fold" in listing_message

    assert detail is not None
    assert detail.kind == "show_pack"
    detail_message = backend._pack_show_markdown(detail.pack_ref or "")
    assert "## @demo/focus-fold" in detail_message
    assert "fold-guidance" in detail_message
    assert "Explain fold transitions clearly." in backend.extra_system_prompt

    assert detached is not None
    assert detached.kind == "detach_pack"
    assert backend._detach_pack(detached.pack_ref or "") == "Detached `@demo/focus-fold` (global)."

    assert reattached is not None
    assert reattached.kind == "attach_pack"
    assert backend._attach_pack(reattached.pack_ref or "") == "Attached `@demo/focus-fold` (global)."

    removed = await backend.execute_slash_command("/pack remove demo/focus-fold")
    assert removed is not None
    assert removed.kind == "delete_pack"
    assert backend._remove_pack(removed.pack_ref or "") == "Removed `@demo/focus-fold`."
    assert "Explain fold transitions clearly." not in backend.extra_system_prompt
    assert registry.get("@demo/instruction/fold-guidance") is None


@pytest.mark.asyncio
async def test_textual_backend_pack_refresh_runs_worker(tmp_path) -> None:
    db = init_db(tmp_path / "textual_pack_refresh.db")
    config = _backend_config(tmp_path)
    config.pack_sources = [SimpleNamespace(url="https://example.com/packs.git", branch="main")]
    registry = ArtifactRegistry()
    backend = AgentLoopTextualBackend(
        config=config,
        db=db,
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=[],
        extra_system_prompt="base prompt",
        working_dir=str(tmp_path),
        artifact_registry=registry,
    )
    refresh_result = SimpleNamespace(
        url="https://example.com/packs.git",
        success=True,
        packs_installed=1,
        packs_updated=2,
        changed=True,
        error=None,
    )

    with patch("anteroom.services.pack_refresh.PackRefreshWorker") as mock_worker:
        mock_worker.return_value.refresh_all.return_value = [refresh_result]

        result = await backend.execute_slash_command("/pack refresh")

        assert result is not None
        assert result.kind == "refresh_pack_sources"
        message = backend._refresh_pack_sources_markdown()
        assert "## Pack Refresh" in message
        assert "installed `1`" in message
        assert "updated `2`" in message


@pytest.mark.asyncio
async def test_textual_backend_pack_install_and_update_commands(tmp_path) -> None:
    db = init_db(tmp_path / "textual_pack_install.db")
    config = _backend_config(tmp_path)
    registry = ArtifactRegistry()
    backend = AgentLoopTextualBackend(
        config=config,
        db=db,
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=[],
        extra_system_prompt="base prompt",
        working_dir=str(tmp_path),
        artifact_registry=registry,
    )

    pack_dir = tmp_path / "installable-pack"
    (pack_dir / "skills").mkdir(parents=True)
    (pack_dir / "skills" / "hello.yaml").write_text("content: Hello from pack\n", encoding="utf-8")
    (pack_dir / "pack.yaml").write_text(
        "\n".join(
            [
                "name: demo-pack",
                "namespace: demo",
                "version: 1.0.0",
                "artifacts:",
                "  - type: skill",
                "    name: hello",
            ]
        ),
        encoding="utf-8",
    )

    install_result = await backend.execute_slash_command(
        f"/pack install {pack_dir} --project --attach --priority 10"
    )
    assert install_result is not None
    assert install_result.kind == "install_pack"
    install_message = backend._install_or_update_pack(
        install_result.pack_path or "",
        update=False,
        project_scope=install_result.pack_project_scope,
        attach_after_install=install_result.pack_attach_after_install,
        priority=install_result.pack_priority or 50,
    )
    assert "Installed `@demo/demo-pack` v1.0.0" in install_message
    assert "Attached `@demo/demo-pack` (project, p10)." in install_message
    attachment = db.execute(
        (
            "SELECT project_path, priority FROM pack_attachments pa "
            "JOIN packs p ON p.id = pa.pack_id "
            "WHERE p.namespace = ? AND p.name = ?"
        ),
        ("demo", "demo-pack"),
    ).fetchone()
    assert attachment is not None
    assert attachment["project_path"] == str(tmp_path)
    assert attachment["priority"] == 10

    (pack_dir / "pack.yaml").write_text(
        "\n".join(
            [
                "name: demo-pack",
                "namespace: demo",
                "version: 1.1.0",
                "artifacts:",
                "  - type: skill",
                "    name: hello",
            ]
        ),
        encoding="utf-8",
    )
    update_result = await backend.execute_slash_command(f"/pack update {pack_dir} --project")
    assert update_result is not None
    assert update_result.kind == "update_pack"
    update_message = backend._install_or_update_pack(
        update_result.pack_path or "",
        update=True,
        project_scope=update_result.pack_project_scope,
    )
    assert "Updated `@demo/demo-pack` v1.1.0" in update_message
    pack_row = db.execute(
        "SELECT version, source_path FROM packs WHERE namespace = ? AND name = ?",
        ("demo", "demo-pack"),
    ).fetchone()
    assert pack_row is not None
    assert pack_row["version"] == "1.1.0"


@pytest.mark.asyncio
async def test_textual_backend_pack_add_source_updates_config(tmp_path) -> None:
    db = init_db(tmp_path / "textual_pack_source.db")
    config = _backend_config(tmp_path)
    config.pack_sources = []
    backend = AgentLoopTextualBackend(
        config=config,
        db=db,
        ai_service=SimpleNamespace(config=SimpleNamespace(model="gpt-5.2")),
        tool_executor=None,
        tools_openai=[],
        extra_system_prompt="base prompt",
        working_dir=str(tmp_path),
        artifact_registry=ArtifactRegistry(),
    )

    with patch("anteroom.services.pack_sources.add_pack_source") as mock_add_pack_source:
        mock_add_pack_source.return_value = SimpleNamespace(ok=True, message="")

        result = await backend.execute_slash_command("/pack add-source https://example.com/packs.git")

        assert result is not None
        assert result.kind == "add_pack_source"
        assert backend._add_pack_source(result.pack_source_url or "") == "Added pack source: https://example.com/packs.git"
        assert len(config.pack_sources) == 1
        assert config.pack_sources[0].url == "https://example.com/packs.git"


@pytest.mark.asyncio
async def test_textual_app_keeps_transcript_scrolled_to_bottom() -> None:
    backend = ScriptedBackend()
    turn = backend.add_turn()
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        await _submit(pilot, app, "Stream a very long answer")

        await turn.put(AgentEvent(kind="thinking", data={}))
        long_lines = [f"Line {i}: detailed transcript content" for i in range(1, 80)]
        for line in long_lines:
            await turn.put(AgentEvent(kind="token", data={"content": line + "\n"}))
        await turn.put(AgentEvent(kind="assistant_message", data={"content": "\n".join(long_lines)}))
        await turn.put(AgentEvent(kind="done", data={}))
        await turn.put(None)
        await pilot.pause(0.2)

        transcript_scroll = app.query_one("#transcript-scroll", VerticalScroll)
        assert transcript_scroll.max_scroll_y > 0
        assert transcript_scroll.scroll_y == transcript_scroll.max_scroll_y


@pytest.mark.asyncio
async def test_textual_app_preserves_manual_transcript_scroll_position() -> None:
    backend = ScriptedBackend()
    turn = backend.add_turn()
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        await _submit(pilot, app, "Stream a very long answer")

        await turn.put(AgentEvent(kind="thinking", data={}))
        long_lines = [f"Line {i}: detailed transcript content" for i in range(1, 80)]
        for line in long_lines[:60]:
            await turn.put(AgentEvent(kind="token", data={"content": line + "\n"}))
        await pilot.pause(0.2)

        transcript_scroll = app.query_one("#transcript-scroll", VerticalScroll)
        transcript_scroll.scroll_to(y=0, animate=False, immediate=True, force=True)
        app._transcript_auto_follow = False
        await pilot.pause()

        manual_scroll_y = transcript_scroll.scroll_y
        assert manual_scroll_y == 0

        for line in long_lines[60:]:
            await turn.put(AgentEvent(kind="token", data={"content": line + "\n"}))
        await turn.put(AgentEvent(kind="assistant_message", data={"content": "\n".join(long_lines)}))
        await turn.put(AgentEvent(kind="done", data={}))
        await turn.put(None)
        await pilot.pause(0.2)

        transcript_scroll = app.query_one("#transcript-scroll", VerticalScroll)
        assert transcript_scroll.max_scroll_y > 0
        assert transcript_scroll.scroll_y == manual_scroll_y


@pytest.mark.asyncio
async def test_textual_app_meta_c_copies_selected_transcript_text(monkeypatch) -> None:
    backend = ScriptedBackend(history=[("assistant", "Copy this exact answer.")])
    app = TextualChatApp(backend=backend, session=_session())
    copied: list[str] = []

    async with app.run_test() as pilot:
        await pilot.pause()
        transcript = app.query_one("#transcript-pane", TranscriptPane)
        needle = "Copy this exact answer."
        paragraph = next(widget for widget in transcript.query("*").results() if str(widget._render()) == needle)
        transcript.screen.selections[paragraph] = Selection.from_offsets(
            Offset(0, 0),
            Offset(len(needle), 0),
        )
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)

        await pilot.press("meta+c")
        await pilot.pause()

        assert copied == [needle]


@pytest.mark.asyncio
async def test_textual_app_warp_mouse_selection_auto_copies_transcript_text(monkeypatch) -> None:
    backend = ScriptedBackend(history=[("assistant", "Copy this exact answer.")])
    app = TextualChatApp(backend=backend, session=_session())
    copied: list[str] = []

    async with app.run_test() as pilot:
        await pilot.pause()
        transcript = app.query_one("#transcript-pane", TranscriptPane)
        needle = "Copy this exact answer."
        paragraph = next(widget for widget in transcript.query("*").results() if str(widget._render()) == needle)
        transcript.screen.selections[paragraph] = Selection.from_offsets(
            Offset(0, 0),
            Offset(len(needle), 0),
        )
        monkeypatch.setenv("TERM_PROGRAM", "WarpTerminal")
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)

        await app.on_mouse_up(
            events.MouseUp(
                widget=paragraph,
                x=0,
                y=0,
                delta_x=0,
                delta_y=0,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
                screen_x=0,
                screen_y=0,
            )
        )

        assert copied == [needle]


@pytest.mark.asyncio
async def test_textual_app_submit_shortcut_works_when_transcript_has_focus() -> None:
    backend = ScriptedBackend()
    turn = backend.add_turn()
    app = TextualChatApp(backend=backend, session=_session(), initial_prompt="Initial history")

    async with app.run_test() as pilot:
        await turn.put(AgentEvent(kind="assistant_message", data={"content": "Initial response"}))
        await turn.put(AgentEvent(kind="done", data={}))
        await turn.put(None)
        await pilot.pause(0.2)

        composer = app.query_one("#composer", Composer)
        composer.load_text("Send from transcript focus")
        app.action_focus_transcript()
        await pilot.pause()
        await pilot.press("ctrl+enter")
        await pilot.pause()

        assert backend.prompts[-1] == "Send from transcript focus"


@pytest.mark.asyncio
async def test_textual_app_tools_panel_stays_visible_with_empty_state() -> None:
    backend = ScriptedBackend()
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        await pilot.pause()
        tool_board = app.query_one("#tool-board", BoardWidget)
        assert tool_board.plain_text == "The instruments will appear when the agent reaches for them."


@pytest.mark.asyncio
async def test_textual_app_focus_shortcuts_reach_side_surfaces_and_return_to_composer() -> None:
    backend = ScriptedBackend(history=[("assistant", "Focus map.")])
    app = TextualChatApp(backend=backend, session=_session())

    async with app.run_test() as pilot:
        await pilot.pause()

        await pilot.press("ctrl+2")
        await pilot.pause()
        assert app.screen.focused is app.query_one("#procedure-board", BoardWidget)

        await pilot.press("ctrl+3")
        await pilot.pause()
        assert app.screen.focused is app.query_one("#tool-board", BoardWidget)

        await pilot.press("ctrl+4")
        await pilot.pause()
        assert app.screen.focused is app.query_one("#trace-board", BoardWidget)

        await pilot.press("ctrl+l")
        await pilot.pause()
        assert app.screen.focused is app.query_one("#composer", Composer)
