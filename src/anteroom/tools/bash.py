"""Shell command execution tool."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import TYPE_CHECKING, Any

from .security import (
    check_blocked_path,
    check_custom_patterns,
    check_network_command,
    check_package_install,
    sanitize_command,
)

if TYPE_CHECKING:
    from ..config import BashSandboxConfig

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("anteroom.security")

_MAX_OUTPUT = 100_000
_DEFAULT_TIMEOUT = 120

_working_dir: str = os.getcwd()

DEFINITION: dict[str, Any] = {
    "name": "bash",
    "description": (
        "Execute a shell command and return stdout, stderr, and exit code. "
        "Commands run in the working directory. Default timeout is 120 seconds."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to execute"},
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 120, max 600)",
                "default": 120,
            },
        },
        "required": ["command"],
    },
}


def set_working_dir(d: str) -> None:
    global _working_dir
    _working_dir = d


def _check_sandbox(command: str, config: BashSandboxConfig) -> str | None:
    """Run all sandbox checks. Returns error message if blocked, None if allowed."""
    if not config.allow_network:
        desc = check_network_command(command)
        if desc:
            return f"Network commands are blocked ({desc})"

    if not config.allow_package_install:
        desc = check_package_install(command)
        if desc:
            return f"Package installation is blocked ({desc})"

    if config.blocked_paths:
        desc = check_blocked_path(command, config.blocked_paths)
        if desc:
            return f"Blocked: {desc}"

    if config.blocked_commands:
        desc = check_custom_patterns(command, config.blocked_commands)
        if desc:
            return f"Blocked: {desc}"

    return None


async def handle(
    command: str,
    timeout: int = _DEFAULT_TIMEOUT,
    _bypass_hard_block: bool = False,
    _sandbox_config: BashSandboxConfig | None = None,
    **_: Any,
) -> dict[str, Any]:
    # Null byte check runs unconditionally — never bypassable.
    if "\x00" in command:
        return {"error": "Command contains null bytes", "exit_code": -1}
    if not _bypass_hard_block:
        command, error = sanitize_command(command)
        if error:
            return {"error": error, "exit_code": -1}

    # Apply sandbox restrictions (runs even when hard-block is bypassed)
    if _sandbox_config is not None:
        sandbox_error = _check_sandbox(command, _sandbox_config)
        if sandbox_error:
            security_logger.warning("Sandbox blocked: %s — %s", sandbox_error, command[:100])
            return {"error": sandbox_error, "exit_code": -1}
        max_timeout = _sandbox_config.timeout
        max_output = _sandbox_config.max_output_chars
    else:
        max_timeout = 600
        max_output = _MAX_OUTPUT

    timeout = min(max(1, timeout), max_timeout)

    # Log command if configured
    if _sandbox_config is not None and _sandbox_config.log_all_commands:
        security_logger.info("bash command: %s", command[:500])

    # Set up OS-level sandbox (Win32 Job Object) if configured
    use_os_sandbox = sys.platform == "win32" and _sandbox_config is not None and _sandbox_config.sandbox.is_enabled

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_working_dir,
        )

        # Assign process to Job Object for kernel-level resource limits
        job_handle: int | None = None
        if use_os_sandbox and proc.pid is not None:
            from .sandbox_win32 import setup_job_for_process

            job_handle = setup_job_for_process(_sandbox_config.sandbox, proc.pid)  # type: ignore[union-attr]
            if job_handle is None:
                security_logger.warning("Job Object setup failed, running without OS sandbox")

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            if job_handle is not None:
                from .sandbox_win32 import terminate_job

                terminate_job(job_handle)
            proc.kill()
            await proc.wait()
            return {"error": f"Command timed out after {timeout}s", "exit_code": -1}
        finally:
            if job_handle is not None:
                from .sandbox_win32 import close_job

                close_job(job_handle)

        stdout_str = stdout.decode("utf-8", errors="replace")
        stderr_str = stderr.decode("utf-8", errors="replace")

        if len(stdout_str) > max_output:
            stdout_str = stdout_str[:max_output] + "\n... (truncated)"
        if len(stderr_str) > max_output:
            stderr_str = stderr_str[:max_output] + "\n... (truncated)"

        return {
            "stdout": stdout_str,
            "stderr": stderr_str,
            "exit_code": proc.returncode or 0,
        }
    except OSError as e:
        return {"error": str(e), "exit_code": -1}
