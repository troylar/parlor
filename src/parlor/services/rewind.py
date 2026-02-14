"""Rewind service: delete messages and optionally revert file changes via git."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..tools.security import validate_path
from . import storage

logger = logging.getLogger(__name__)

_FILE_MODIFYING_TOOLS = {"write_file", "edit_file"}


@dataclass
class RewindResult:
    deleted_messages: int = 0
    reverted_files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)


async def check_git_repo(working_dir: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "--is-inside-work-tree",
            cwd=working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0
    except OSError:
        return False


def collect_file_paths(db: Any, message_ids: list[str]) -> set[str]:
    """Extract unique file paths from file-modifying tool calls on the given messages."""
    paths: set[str] = set()
    for mid in message_ids:
        tool_calls = storage.list_tool_calls(db, mid)
        for tc in tool_calls:
            if tc["tool_name"] in _FILE_MODIFYING_TOOLS:
                path = tc["input"].get("path", "")
                if path:
                    paths.add(path)
    return paths


async def revert_files(file_paths: set[str], working_dir: str) -> tuple[list[str], list[str]]:
    """Revert files to git HEAD state. Returns (reverted, skipped) lists."""
    reverted: list[str] = []
    skipped: list[str] = []

    if not file_paths:
        return reverted, skipped

    is_git = await check_git_repo(working_dir)

    for file_path in sorted(file_paths):
        resolved, error = validate_path(file_path, working_dir)
        if error:
            skipped.append(f"{file_path} (validation failed)")
            continue

        if not is_git:
            skipped.append(f"{resolved} (not a git repo)")
            continue

        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "checkout",
                "HEAD",
                "--",
                resolved,
                cwd=working_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0:
                reverted.append(resolved)
            else:
                err_msg = stderr.decode().strip() if stderr else "unknown error"
                skipped.append(f"{resolved} ({err_msg})")
        except OSError as e:
            skipped.append(f"{resolved} ({e})")

    return reverted, skipped


async def rewind_conversation(
    db: Any,
    conversation_id: str,
    to_position: int,
    undo_files: bool = False,
    data_dir: Path | None = None,
    working_dir: str | None = None,
) -> RewindResult:
    """Rewind a conversation to a given position, optionally reverting file changes.

    Returns a RewindResult with counts of deleted messages and file revert outcomes.
    """
    result = RewindResult()

    if undo_files:
        if working_dir is None:
            working_dir = os.getcwd()

        msgs = storage.list_messages(db, conversation_id)
        msgs_to_delete = [m for m in msgs if m["position"] > to_position]
        msg_ids = [m["id"] for m in msgs_to_delete]

        file_paths = collect_file_paths(db, msg_ids)
        result.reverted_files, result.skipped_files = await revert_files(file_paths, working_dir)

    result.deleted_messages = storage.delete_messages_after_position(db, conversation_id, to_position, data_dir)
    return result
