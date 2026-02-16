"""Shell command execution tool."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from .security import sanitize_command

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


async def handle(command: str, timeout: int = _DEFAULT_TIMEOUT, **_: Any) -> dict[str, Any]:
    command, error = sanitize_command(command)
    if error:
        return {"error": error, "exit_code": -1}

    timeout = min(max(1, timeout), 600)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_working_dir,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"error": f"Command timed out after {timeout}s", "exit_code": -1}

        stdout_str = stdout.decode("utf-8", errors="replace")
        stderr_str = stderr.decode("utf-8", errors="replace")

        if len(stdout_str) > _MAX_OUTPUT:
            stdout_str = stdout_str[:_MAX_OUTPUT] + "\n... (truncated)"
        if len(stderr_str) > _MAX_OUTPUT:
            stderr_str = stderr_str[:_MAX_OUTPUT] + "\n... (truncated)"

        return {
            "stdout": stdout_str,
            "stderr": stderr_str,
            "exit_code": proc.returncode or 0,
        }
    except OSError as e:
        return {"error": str(e), "exit_code": -1}
