"""CLI integration tests for workflow progress output.

Tests that `aroom workflow run` shows step-by-step progress with
status indicators and final summary. One-shot commands, not REPL.
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
    config_file.write_text(
        'ai:\n  base_url: "http://localhost:1/v1"\n  api_key: "test"\n  model: "test"\n'
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    yield

_TEST_WORKFLOW = """\
kind: workflow
id: test_progress
version: 0.1.0
inputs: {}
steps:
  - id: first_step
    type: runner
    runner: shell
    command: "echo first step output"
    timeout: 10
  - id: second_step
    type: runner
    runner: shell
    command: "echo second step output"
    timeout: 10
"""

_FAILING_WORKFLOW = """\
kind: workflow
id: test_fail_progress
version: 0.1.0
inputs: {}
steps:
  - id: good_step
    type: runner
    runner: shell
    command: "echo good"
    timeout: 10
  - id: bad_step
    type: runner
    runner: shell
    command: "exit 1"
    timeout: 10
"""


def _run_aroom(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_PYTHON, "-m", "anteroom", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestProgressOutput:
    """Verify `aroom workflow run` shows step progress."""

    @pytest.fixture()
    def workflow_file(self, tmp_path: Path) -> Path:
        wf = tmp_path / "test_progress.yaml"
        wf.write_text(_TEST_WORKFLOW)
        return wf

    @pytest.fixture()
    def failing_workflow(self, tmp_path: Path) -> Path:
        wf = tmp_path / "test_fail.yaml"
        wf.write_text(_FAILING_WORKFLOW)
        return wf

    def test_shows_step_names(self, workflow_file: Path) -> None:
        """Output includes step names from the workflow definition."""
        result = _run_aroom("workflow", "run", str(workflow_file), timeout=30)
        output = result.stdout + result.stderr
        assert "first_step" in output
        assert "second_step" in output

    def test_shows_completion_status(self, workflow_file: Path) -> None:
        """Output includes completion markers for each step."""
        result = _run_aroom("workflow", "run", str(workflow_file), timeout=30)
        output = result.stdout + result.stderr
        assert "done" in output.lower() or "completed" in output.lower()

    def test_shows_final_status(self, workflow_file: Path) -> None:
        """Output ends with the final workflow status."""
        result = _run_aroom("workflow", "run", str(workflow_file), timeout=30)
        output = result.stdout + result.stderr
        assert "Workflow completed" in output or "completed" in output.lower()

    def test_shows_run_id(self, workflow_file: Path) -> None:
        """Output includes the run ID for follow-up commands."""
        result = _run_aroom("workflow", "run", str(workflow_file), timeout=30)
        output = result.stdout + result.stderr
        assert "Run ID:" in output

    def test_failed_workflow_shows_error(self, failing_workflow: Path) -> None:
        """Failed workflow shows error status and the failing step."""
        result = _run_aroom("workflow", "run", str(failing_workflow), timeout=30)
        output = result.stdout + result.stderr
        assert "failed" in output.lower()
        assert "bad_step" in output
