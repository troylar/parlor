"""CLI integration tests for `aroom workflow` subcommands.

Tests real argparse dispatch via subprocess. Exercises the actual
`python -m anteroom workflow` command path — not direct handler calls.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_PYTHON = sys.executable


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Use an isolated HOME so workflow runs don't pollute the user's DB."""
    anteroom_dir = tmp_path / ".anteroom"
    anteroom_dir.mkdir()
    config_file = anteroom_dir / "config.yaml"
    config_file.write_text('ai:\n  base_url: "http://localhost:1/v1"\n  api_key: "test"\n  model: "test"\n')
    monkeypatch.setenv("HOME", str(tmp_path))
    yield


# A generic test workflow (shell-only, no AI needed, no GitHub concepts)
# Uses no required inputs so it can be run via `aroom workflow run <path>`
# without needing --issue or other flags.
_TEST_WORKFLOW_YAML = """\
kind: workflow
id: test_cli_pipeline
version: 0.1.0
inputs: {}
steps:
  - id: greet
    type: runner
    runner: shell
    command: "echo Hello from workflow"
    timeout: 10
  - id: validate
    type: runner
    runner: shell
    command: "echo Validation passed"
    timeout: 10
"""


def _run_aroom(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run `aroom` CLI via subprocess and capture output."""
    return subprocess.run(
        [_PYTHON, "-m", "anteroom", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Help and error handling (subprocess)
# ---------------------------------------------------------------------------


class TestWorkflowCLIHelp:
    """Help text renders correctly for workflow subcommands."""

    def test_workflow_help(self) -> None:
        result = _run_aroom("workflow", "--help")
        assert result.returncode == 0
        assert "run" in result.stdout
        assert "status" in result.stdout
        assert "list" in result.stdout
        assert "history" in result.stdout

    def test_workflow_run_help(self) -> None:
        result = _run_aroom("workflow", "run", "--help")
        assert result.returncode == 0
        assert "workflow_name" in result.stdout
        assert "--issue" in result.stdout
        assert "--dry-run" in result.stdout

    def test_workflow_status_help(self) -> None:
        result = _run_aroom("workflow", "status", "--help")
        assert result.returncode == 0
        assert "run_id" in result.stdout

    def test_workflow_list_help(self) -> None:
        result = _run_aroom("workflow", "list", "--help")
        assert result.returncode == 0
        assert "--status" in result.stdout
        assert "--limit" in result.stdout

    def test_workflow_history_help(self) -> None:
        result = _run_aroom("workflow", "history", "--help")
        assert result.returncode == 0
        assert "run_id" in result.stdout


class TestWorkflowCLIDryRun:
    """Dry run shows workflow plan without executing."""

    def test_dry_run_issue_delivery(self) -> None:
        result = _run_aroom("workflow", "run", "issue_delivery", "--dry-run", "--issue", "42")
        assert result.returncode == 0
        assert "issue_delivery" in result.stdout
        assert "v0.1.0" in result.stdout
        assert "issue:42" in result.stdout
        assert "gate_issue_current" in result.stdout
        assert "gate_plan" in result.stdout

    def test_dry_run_unknown_workflow(self) -> None:
        result = _run_aroom("workflow", "run", "nonexistent", "--dry-run")
        assert result.returncode == 0
        assert "Unknown workflow" in result.stdout or "Error" in result.stdout


# ---------------------------------------------------------------------------
# Real command path tests — run/status/list/history via subprocess
# ---------------------------------------------------------------------------


class TestWorkflowCLIRealCommands:
    """Drive real `python -m anteroom workflow` commands through subprocess.

    Uses a shell-only test workflow (no AI needed) written to a temp file.
    The actual argparse → config → handler → engine → storage path is
    exercised end-to-end.
    """

    @pytest.fixture()
    def workflow_file(self, tmp_path: Path) -> Path:
        """Write the test workflow YAML to a temp file."""
        wf = tmp_path / "test_pipeline.yaml"
        wf.write_text(_TEST_WORKFLOW_YAML)
        return wf

    def test_run_shell_workflow_completes(self, workflow_file: Path) -> None:
        """Run a shell-only workflow via the real CLI and verify completion."""
        result = _run_aroom(
            "workflow",
            "run",
            str(workflow_file),
            timeout=30,
        )
        # The workflow should complete (shell commands are simple echos)
        output = result.stdout + result.stderr
        assert "completed" in output.lower() or "Workflow completed" in output
        assert "Run ID:" in output

    def test_run_then_list_shows_run(self, workflow_file: Path) -> None:
        """After running a workflow, `list` shows it."""
        # Run the workflow first
        _run_aroom("workflow", "run", str(workflow_file), timeout=30)
        # List should show the run
        list_result = _run_aroom("workflow", "list")
        list_output = list_result.stdout
        assert list_result.returncode == 0
        # Should show either the workflow ID or the run table
        assert "test_cli_pipeline" in list_output or "Workflow Runs" in list_output

    def test_run_then_status_shows_details(self, workflow_file: Path) -> None:
        """After running, `status <run_id>` shows run details."""
        # Run the workflow and extract run ID
        run_result = _run_aroom(
            "workflow",
            "run",
            str(workflow_file),
            timeout=30,
        )
        run_output = run_result.stdout + run_result.stderr

        # Extract run ID from output
        run_id = None
        for line in run_output.splitlines():
            if "Run ID:" in line:
                run_id = line.split("Run ID:")[-1].strip()
                break

        if run_id:
            status_result = _run_aroom("workflow", "status", run_id)
            status_output = status_result.stdout
            assert status_result.returncode == 0
            assert "test_cli_pipeline" in status_output or run_id[:12] in status_output

    def test_run_then_history_shows_steps(self, workflow_file: Path) -> None:
        """After running, `history <run_id>` shows step details."""
        # Run the workflow and extract run ID
        run_result = _run_aroom(
            "workflow",
            "run",
            str(workflow_file),
            timeout=30,
        )
        run_output = run_result.stdout + run_result.stderr

        # Extract run ID
        run_id = None
        for line in run_output.splitlines():
            if "Run ID:" in line:
                run_id = line.split("Run ID:")[-1].strip()
                break

        if run_id:
            history_result = _run_aroom("workflow", "history", run_id)
            history_output = history_result.stdout
            assert history_result.returncode == 0
            # Should show step names from the workflow
            assert "greet" in history_output or "validate" in history_output

    def test_status_nonexistent_run(self) -> None:
        """Status with a fake run ID shows error, doesn't crash."""
        result = _run_aroom("workflow", "status", "nonexistent-fake-id")
        output = result.stdout + result.stderr
        assert "not found" in output.lower() or "Error" in output

    def test_list_empty_state(self) -> None:
        """List with no runs shows empty message."""
        result = _run_aroom("workflow", "list")
        assert result.returncode == 0
        output = result.stdout
        assert "No workflow runs" in output or "Workflow Runs" in output
