"""CLI integration tests for `aroom workflow resume` and `cancel`.

Drives real `python -m anteroom workflow` commands through subprocess.
Tests help, error handling, and real success paths for resume/cancel.

To test resume/cancel success paths, we:
1. Run a shell workflow (completes instantly)
2. Use a helper script to mark the run as paused in the DB
3. Resume or cancel via real CLI commands
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
id: test_resume_cli
version: 0.1.0
inputs: {}
steps:
  - id: step_one
    type: runner
    runner: shell
    command: "echo step one done"
    timeout: 10
  - id: step_two
    type: runner
    runner: shell
    command: "echo step two done"
    timeout: 10
"""

# Helper script that marks a completed run as paused in the DB.
# This simulates a crash — the run was interrupted and needs resume.
_PAUSE_RUN_SCRIPT = """\
import sys
from pathlib import Path
from anteroom.config import _resolve_data_dir
from anteroom.db import get_db
from anteroom.services.workflow_storage import update_workflow_run

db = get_db(_resolve_data_dir() / "chat.db")
run_id = sys.argv[1]
update_workflow_run(db, run_id, status="paused", stop_reason="test_simulated_crash")
print(f"PAUSED:{run_id}")
"""

# Helper script that marks a completed run as stale-running (for recovery test).
_MAKE_STALE_SCRIPT = """\
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from anteroom.config import _resolve_data_dir
from anteroom.db import get_db
from anteroom.services.workflow_storage import update_workflow_run

db = get_db(_resolve_data_dir() / "chat.db")
run_id = sys.argv[1]
old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
update_workflow_run(db, run_id, status="running", heartbeat_at=old_time)
print(f"STALE:{run_id}")
"""


def _run_aroom(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_PYTHON, "-m", "anteroom", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _run_script(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_PYTHON, "-c", script, *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


def _extract_run_id(output: str) -> str | None:
    for line in output.splitlines():
        if "Run ID:" in line:
            return line.split("Run ID:")[-1].strip()
    return None


# ---------------------------------------------------------------------------
# Help and error handling
# ---------------------------------------------------------------------------


class TestResumeHelpAndErrors:
    def test_resume_help(self) -> None:
        result = _run_aroom("workflow", "resume", "--help")
        assert result.returncode == 0
        assert "run_id" in result.stdout
        assert "--from-step" in result.stdout
        assert "--definition" in result.stdout

    def test_cancel_help(self) -> None:
        result = _run_aroom("workflow", "cancel", "--help")
        assert result.returncode == 0
        assert "run_id" in result.stdout

    def test_resume_nonexistent_run(self) -> None:
        result = _run_aroom("workflow", "resume", "nonexistent-id")
        output = result.stdout + result.stderr
        assert "not found" in output.lower() or "Error" in output

    def test_cancel_nonexistent_run(self) -> None:
        result = _run_aroom("workflow", "cancel", "nonexistent-id")
        output = result.stdout + result.stderr
        assert "not found" in output.lower() or "Error" in output


# ---------------------------------------------------------------------------
# Real success paths: resume, cancel, and stale-run recovery
# ---------------------------------------------------------------------------


class TestResumeSuccessPath:
    """Test the full resume success path through real CLI commands."""

    @pytest.fixture()
    def workflow_file(self, tmp_path: Path) -> Path:
        wf = tmp_path / "test_resume.yaml"
        wf.write_text(_TEST_WORKFLOW)
        return wf

    def test_resume_paused_run_completes(self, workflow_file: Path) -> None:
        """Run → pause (simulated) → resume → completed."""
        # 1. Run the workflow (completes)
        run_result = _run_aroom("workflow", "run", str(workflow_file), timeout=30)
        output = run_result.stdout + run_result.stderr
        run_id = _extract_run_id(output)
        assert run_id, f"No run ID: {output}"

        # 2. Mark as paused (simulates crash)
        pause_result = _run_script(_PAUSE_RUN_SCRIPT, run_id)
        assert f"PAUSED:{run_id}" in pause_result.stdout

        # 3. Resume via real CLI
        resume_result = _run_aroom(
            "workflow", "resume", run_id,
            "--definition", str(workflow_file),
            timeout=30,
        )
        resume_output = resume_result.stdout + resume_result.stderr
        assert "completed" in resume_output.lower() or "Workflow completed" in resume_output

    def test_cancel_paused_run_succeeds(self, workflow_file: Path) -> None:
        """Run → pause (simulated) → cancel → cancelled."""
        # 1. Run
        run_result = _run_aroom("workflow", "run", str(workflow_file), timeout=30)
        output = run_result.stdout + run_result.stderr
        run_id = _extract_run_id(output)
        assert run_id

        # 2. Mark as paused
        pause_result = _run_script(_PAUSE_RUN_SCRIPT, run_id)
        assert f"PAUSED:{run_id}" in pause_result.stdout, (
            f"Pause helper failed: stdout={pause_result.stdout!r} stderr={pause_result.stderr!r}"
        )

        # 3. Cancel via real CLI
        cancel_result = _run_aroom("workflow", "cancel", run_id)
        cancel_output = cancel_result.stdout + cancel_result.stderr
        assert "cancelled" in cancel_output.lower(), f"Cancel output: {cancel_output}"

        # 4. Verify status shows cancelled
        status_result = _run_aroom("workflow", "status", run_id)
        assert "cancelled" in status_result.stdout.lower(), (
            f"Expected cancelled in status. Got: {status_result.stdout}"
        )

    def test_stale_run_recovered_on_list(self, workflow_file: Path) -> None:
        """Run → mark stale → list triggers recovery → status confirms paused."""
        # 1. Run
        run_result = _run_aroom("workflow", "run", str(workflow_file), timeout=30)
        output = run_result.stdout + run_result.stderr
        run_id = _extract_run_id(output)
        assert run_id

        # 2. Mark as stale running (simulates crash without heartbeat)
        stale_result = _run_script(_MAKE_STALE_SCRIPT, run_id)
        assert f"STALE:{run_id}" in stale_result.stdout

        # 3. Verify status before recovery shows "running" (stale)
        pre_status = _run_aroom("workflow", "status", run_id)
        assert "running" in pre_status.stdout.lower(), (
            f"Expected 'running' before recovery. Got: {pre_status.stdout}"
        )

        # 4. List triggers on-demand recovery
        list_result = _run_aroom("workflow", "list")
        list_output = list_result.stdout
        assert "Recovered" in list_output, (
            f"Expected 'Recovered' message in list output. Got: {list_output}"
        )

        # 5. Verify status AFTER recovery shows "paused" (the state transition)
        post_status = _run_aroom("workflow", "status", run_id)
        assert "paused" in post_status.stdout.lower(), (
            f"Expected 'paused' after recovery. Got: {post_status.stdout}"
        )


# ---------------------------------------------------------------------------
# Rejection cases
# ---------------------------------------------------------------------------


class TestRejectionCases:
    @pytest.fixture()
    def workflow_file(self, tmp_path: Path) -> Path:
        wf = tmp_path / "test_reject.yaml"
        wf.write_text(_TEST_WORKFLOW)
        return wf

    def test_cancel_completed_run_rejected(self, workflow_file: Path) -> None:
        run_result = _run_aroom("workflow", "run", str(workflow_file), timeout=30)
        run_id = _extract_run_id(run_result.stdout + run_result.stderr)
        assert run_id

        cancel_result = _run_aroom("workflow", "cancel", run_id)
        cancel_output = cancel_result.stdout + cancel_result.stderr
        assert "not cancellable" in cancel_output.lower() or "Error" in cancel_output

    def test_resume_completed_run_rejected(self, workflow_file: Path) -> None:
        run_result = _run_aroom("workflow", "run", str(workflow_file), timeout=30)
        run_id = _extract_run_id(run_result.stdout + run_result.stderr)
        assert run_id

        resume_result = _run_aroom(
            "workflow", "resume", run_id,
            "--definition", str(workflow_file),
        )
        resume_output = resume_result.stdout + resume_result.stderr
        assert "not resumable" in resume_output.lower() or "Error" in resume_output
