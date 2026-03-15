"""Tests for workflow runner execution and normalization.

Tests both agent and opaque runners to prove they are equally first-class.
Uses domain-neutral test data — no GitHub/PR-specific concepts.
"""

from __future__ import annotations

from typing import Any

import pytest

from anteroom.services.workflow_runners import (
    RunnerRegistry,
    RunnerResult,
    create_default_registry,
    execute_agent_runner,
    execute_opaque_runner,
)


class TestRunnerRegistry:
    def test_default_registry_has_four_types(self) -> None:
        reg = create_default_registry()
        runners = reg.list_runners()
        assert len(runners) == 4
        assert runners["cli_claude"] == "agent"
        assert runners["cli_codex"] == "agent"
        assert runners["shell"] == "opaque"
        assert runners["python_script"] == "opaque"

    def test_custom_runner_registration(self) -> None:
        reg = RunnerRegistry()
        reg.register("my_runner", "opaque")
        assert reg.is_opaque_runner("my_runner")
        assert not reg.is_agent_runner("my_runner")

    def test_invalid_category_raises(self) -> None:
        reg = RunnerRegistry()
        with pytest.raises(ValueError, match="Invalid runner category"):
            reg.register("bad", "unknown")


class TestRunnerResult:
    def test_to_dict(self) -> None:
        r = RunnerResult(
            status="success",
            summary="Done",
            artifacts={"count": 5},
            findings=[{"type": "info"}],
            duration_ms=100,
        )
        d = r.to_dict()
        assert d["status"] == "success"
        assert d["artifacts"]["count"] == 5
        assert d["duration_ms"] == 100

    def test_frozen(self) -> None:
        r = RunnerResult(status="success")
        with pytest.raises(AttributeError):
            r.status = "failed"  # type: ignore[misc]


class TestOpaqueRunner:
    @pytest.mark.asyncio
    async def test_shell_echo(self) -> None:
        """Shell runner executes a command and returns stdout as summary."""
        result = await execute_opaque_runner(
            mode="shell",
            command="echo hello world",
            timeout=10,
        )
        assert result.status == "success"
        assert "hello world" in result.summary
        assert result.artifacts.get("exit_code") == 0

    @pytest.mark.asyncio
    async def test_shell_failure(self) -> None:
        """Shell runner returns failed on non-zero exit code."""
        result = await execute_opaque_runner(
            mode="shell",
            command="exit 1",
            timeout=10,
        )
        assert result.status == "failed"
        assert result.artifacts.get("exit_code") == 1

    @pytest.mark.asyncio
    async def test_shell_timeout(self) -> None:
        """Shell runner kills process on timeout."""
        result = await execute_opaque_runner(
            mode="shell",
            command="sleep 60",
            timeout=1,
        )
        assert result.status == "failed"
        assert "timed out" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_shell_stderr_in_findings(self) -> None:
        """Stderr captured in findings for successful commands."""
        result = await execute_opaque_runner(
            mode="shell",
            command="echo output && echo warning >&2",
            timeout=10,
        )
        assert result.status == "success"
        if result.findings:
            assert any("warning" in str(f) for f in result.findings)

    @pytest.mark.asyncio
    async def test_shell_empty_output(self) -> None:
        """Empty output returns success with default summary."""
        result = await execute_opaque_runner(
            mode="shell",
            command="true",
            timeout=10,
        )
        assert result.status == "success"
        assert result.summary  # should have a default, not empty

    @pytest.mark.asyncio
    async def test_exec_mode_python_script(self, tmp_path: Any) -> None:
        """Python script runner executes via create_subprocess_exec."""
        script = tmp_path / "test_script.py"
        script.write_text("import sys; print('hello from script'); sys.exit(0)")
        result = await execute_opaque_runner(
            mode="exec",
            command=str(script),
            timeout=10,
        )
        assert result.status == "success"
        assert "hello from script" in result.summary

    @pytest.mark.asyncio
    async def test_exec_mode_with_argv(self, tmp_path: Any) -> None:
        """Python script runner passes argv correctly."""
        script = tmp_path / "args_script.py"
        script.write_text("import sys; print(' '.join(sys.argv[1:]))")
        result = await execute_opaque_runner(
            mode="exec",
            command=str(script),
            argv=["arg1", "arg2"],
            timeout=10,
        )
        assert result.status == "success"
        assert "arg1 arg2" in result.summary

    @pytest.mark.asyncio
    async def test_exec_mode_failure(self, tmp_path: Any) -> None:
        """Python script runner returns failed on non-zero exit."""
        script = tmp_path / "fail_script.py"
        script.write_text("import sys; print('error msg', file=sys.stderr); sys.exit(1)")
        result = await execute_opaque_runner(
            mode="exec",
            command=str(script),
            timeout=10,
        )
        assert result.status == "failed"
        assert "error msg" in result.summary

    @pytest.mark.asyncio
    async def test_invalid_mode_raises(self) -> None:
        """Unknown mode raises ValueError."""
        result = await execute_opaque_runner(
            mode="unknown",
            command="echo",
            timeout=10,
        )
        assert result.status == "failed"
        assert "Unknown" in result.summary

    @pytest.mark.asyncio
    async def test_env_vars_passed(self) -> None:
        """Additional env vars are available in the subprocess."""
        result = await execute_opaque_runner(
            mode="shell",
            command="echo $MY_TEST_VAR",
            env={"MY_TEST_VAR": "hello_env"},
            timeout=10,
        )
        assert result.status == "success"
        assert "hello_env" in result.summary


class TestAgentRunner:
    @pytest.mark.asyncio
    async def test_no_ai_service_raises(self) -> None:
        """Agent runner without AI service raises RuntimeError."""
        with pytest.raises(RuntimeError, match="ai_service"):
            await execute_agent_runner(prompt="Do something", timeout=10)
