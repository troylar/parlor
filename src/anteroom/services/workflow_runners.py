"""Workflow runner registry, execution, and result normalization.

Runner execution is domain-neutral. The engine calls execute_agent_runner()
or execute_opaque_runner() with generic parameters. Domain-specific behavior
(what prompt to send, what command to run) is defined in the workflow YAML,
not here.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunnerResult:
    """Normalized output from any runner type."""

    status: str  # "success", "failed", "blocked"
    summary: str = ""
    raw_output_path: str | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "raw_output_path": self.raw_output_path,
            "artifacts": self.artifacts,
            "findings": self.findings,
            "duration_ms": self.duration_ms,
        }


VALID_RESULT_STATUSES = frozenset({"success", "failed", "blocked"})

AGENT_RUNNER_TYPES = frozenset({"cli_claude", "cli_codex"})
OPAQUE_RUNNER_TYPES = frozenset({"shell", "python_script"})


class RunnerRegistry:
    """Registry for workflow runner types."""

    def __init__(self) -> None:
        self._runners: dict[str, str] = {}

    def register(self, runner_type: str, category: str = "opaque") -> None:
        if category not in ("agent", "opaque"):
            raise ValueError(f"Invalid runner category: {category!r}. Must be 'agent' or 'opaque'")
        self._runners[runner_type] = category

    def get_category(self, runner_type: str) -> str | None:
        return self._runners.get(runner_type)

    def is_agent_runner(self, runner_type: str) -> bool:
        return self._runners.get(runner_type) == "agent"

    def is_opaque_runner(self, runner_type: str) -> bool:
        return self._runners.get(runner_type) == "opaque"

    def is_registered(self, runner_type: str) -> bool:
        return runner_type in self._runners

    def list_runners(self) -> dict[str, str]:
        return dict(self._runners)


def create_default_registry() -> RunnerRegistry:
    """Create a registry with the four built-in V1 runner types."""
    registry = RunnerRegistry()
    registry.register("cli_claude", "agent")
    registry.register("cli_codex", "agent")
    registry.register("shell", "opaque")
    registry.register("python_script", "opaque")
    return registry


# ---------------------------------------------------------------------------
# Runner execution (domain-neutral)
# ---------------------------------------------------------------------------


async def execute_agent_runner(
    *,
    prompt: str,
    system_prompt: str | None = None,
    tools_filter: list[str] | None = None,
    timeout: int = 300,
    ai_service: Any | None = None,
    tool_executor: Any | None = None,
    tools_openai: list[dict[str, Any]] | None = None,
    pause_signal: Any | None = None,
) -> RunnerResult:
    """Execute an agent runner step through Anteroom's agent loop.

    Each invocation gets a fresh, independent message list (session isolation).
    Uses serialize_tools=True so the pause signal can be checked between
    tool calls. The agent runner is one runner category among equals — shell
    and python_script runners are equally first-class.
    """
    start = time.monotonic()

    if ai_service is None or tool_executor is None:
        raise RuntimeError(
            "Agent runner requires ai_service and tool_executor. "
            "Configure the WorkflowEngine with AI service dependencies."
        )

    from .agent_loop import run_agent_loop

    # Fresh message list — session isolation per step (FR-006)
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    assistant_content = ""
    tool_outputs: list[dict[str, Any]] = []
    paused = False

    async for event in run_agent_loop(
        ai_service=ai_service,
        messages=messages,
        tool_executor=tool_executor,
        tools_openai=tools_openai,
        serialize_tools=True,
        pause_signal=pause_signal,
        max_iterations=15,
    ):
        if event.kind == "token":
            assistant_content += event.data.get("content", "")
        elif event.kind == "tool_call_end":
            tool_outputs.append(event.data)
        elif event.kind == "workflow_pause":
            paused = True
            break
        elif event.kind == "error":
            duration_ms = int((time.monotonic() - start) * 1000)
            return RunnerResult(
                status="failed",
                summary=event.data.get("message", "Agent loop error"),
                duration_ms=duration_ms,
            )

    duration_ms = int((time.monotonic() - start) * 1000)

    if paused:
        return RunnerResult(
            status="blocked",
            summary="Paused for approval",
            duration_ms=duration_ms,
        )

    return RunnerResult(
        status="success",
        summary=assistant_content[:2000] if assistant_content else "Agent completed",
        artifacts={
            "tool_call_count": len(tool_outputs),
        },
        findings=[{"tool": tc.get("tool_name", ""), "status": tc.get("status", "")} for tc in tool_outputs],
        duration_ms=duration_ms,
    )


async def execute_opaque_runner(
    *,
    mode: str,
    command: str,
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
    working_dir: str | None = None,
    timeout: int = 300,
) -> RunnerResult:
    """Execute an opaque runner step as a subprocess.

    mode="shell": command is passed to /bin/sh -c via create_subprocess_shell().
    mode="exec": command is a script path, argv is positional args, via create_subprocess_exec().

    Domain-neutral — the engine doesn't know or care what the subprocess does.
    """
    from pathlib import Path

    start = time.monotonic()

    # Generic preflight: check working_dir exists if specified
    if working_dir and not Path(working_dir).is_dir():
        return RunnerResult(
            status="failed",
            summary=f"Working directory does not exist: {working_dir}",
            duration_ms=0,
        )

    merged_env: dict[str, str] | None = None
    if env:
        import os

        merged_env = {**os.environ, **env}

    try:
        if mode == "shell":
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
                env=merged_env,
            )
        elif mode == "exec":
            exec_args = [sys.executable, command, *(argv or [])]
            proc = await asyncio.create_subprocess_exec(
                *exec_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
                env=merged_env,
            )
        else:
            raise ValueError(f"Unknown opaque runner mode: {mode!r}")

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            duration_ms = int((time.monotonic() - start) * 1000)
            return RunnerResult(
                status="failed",
                summary=f"Timed out after {timeout}s",
                duration_ms=duration_ms,
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        stdout = stdout_bytes.decode("utf-8", errors="replace").strip() if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip() if stderr_bytes else ""

        if proc.returncode == 0:
            return RunnerResult(
                status="success",
                summary=stdout[:2000] if stdout else "Completed successfully",
                artifacts={"exit_code": 0},
                findings=[{"type": "stderr", "content": stderr}] if stderr else [],
                duration_ms=duration_ms,
            )
        else:
            return RunnerResult(
                status="failed",
                summary=stderr[:2000] if stderr else f"Exit code {proc.returncode}",
                artifacts={"exit_code": proc.returncode},
                findings=[{"type": "stdout", "content": stdout}] if stdout else [],
                duration_ms=duration_ms,
            )

    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return RunnerResult(
            status="failed",
            summary=f"Runner error: {exc}",
            duration_ms=duration_ms,
        )
