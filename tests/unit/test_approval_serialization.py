"""Tests for concurrent approval prompt serialization (#162).

Verifies that the asyncio.Lock pattern used by _confirm_destructive serializes
concurrent approval prompts, preventing stacked prompts and CPR warnings.

Since _confirm_destructive is a closure inside run_cli and cannot be imported
directly, these tests validate the serialization pattern and all approval
response paths that the lock must protect.
"""

from __future__ import annotations

import asyncio

import pytest

from anteroom.tools.safety import SafetyVerdict


def _make_verdict(tool_name: str = "bash", command: str = "rm -rf /tmp/test") -> SafetyVerdict:
    return SafetyVerdict(
        needs_approval=True,
        hard_denied=False,
        reason=f"Tool {tool_name} requires approval",
        tool_name=tool_name,
        details={"command": command},
    )


def _make_path_verdict(tool_name: str = "write_file", path: str = "/etc/passwd") -> SafetyVerdict:
    return SafetyVerdict(
        needs_approval=True,
        hard_denied=False,
        reason=f"Tool {tool_name} writes to sensitive path",
        tool_name=tool_name,
        details={"path": path},
    )


def _build_confirm_destructive(
    lock: asyncio.Lock,
    prompt_responses: list[str],
    call_log: list[tuple[str, str]] | None = None,
    prompt_delay: float = 0.0,
) -> tuple:
    """Build a _confirm_destructive replica that mirrors repl.py's pattern.

    Returns (callback, session_permissions set, persist_calls list).
    """
    session_permissions: set[str] = set()
    persist_calls: list[str] = []
    response_idx = 0

    async def _confirm_destructive(verdict: SafetyVerdict) -> bool:
        nonlocal response_idx

        async with lock:
            if call_log is not None:
                call_log.append(("enter", verdict.tool_name))

            try:
                if prompt_delay > 0:
                    await asyncio.sleep(prompt_delay)

                answer = prompt_responses[response_idx]
                response_idx += 1
                choice = answer.strip().lower()

                if choice in ("a", "always"):
                    session_permissions.add(verdict.tool_name)
                    persist_calls.append(verdict.tool_name)
                    if call_log is not None:
                        call_log.append(("exit", verdict.tool_name))
                    return True
                if choice in ("s", "session"):
                    session_permissions.add(verdict.tool_name)
                    if call_log is not None:
                        call_log.append(("exit", verdict.tool_name))
                    return True
                if choice in ("y", "yes"):
                    if call_log is not None:
                        call_log.append(("exit", verdict.tool_name))
                    return True

                if call_log is not None:
                    call_log.append(("exit", verdict.tool_name))
                return False
            except (EOFError, KeyboardInterrupt):
                if call_log is not None:
                    call_log.append(("exit", verdict.tool_name))
                return False

    return _confirm_destructive, session_permissions, persist_calls


@pytest.mark.asyncio
class TestApprovalSerialization:
    """Concurrent approval calls must be serialized by asyncio.Lock."""

    async def test_concurrent_approvals_are_serialized(self) -> None:
        """Two concurrent _confirm_destructive calls should not overlap."""
        call_log: list[tuple[str, str]] = []
        lock = asyncio.Lock()
        confirm, _, _ = _build_confirm_destructive(lock, ["y", "y"], call_log=call_log, prompt_delay=0.05)

        v1 = _make_verdict("tool_a")
        v2 = _make_verdict("tool_b")

        results = await asyncio.gather(confirm(v1), confirm(v2))
        assert results == [True, True]

        enters = [i for i, (action, _) in enumerate(call_log) if action == "enter"]
        exits = [i for i, (action, _) in enumerate(call_log) if action == "exit"]

        assert len(enters) == 2
        assert len(exits) == 2
        assert exits[0] < enters[1], f"Calls overlapped: {call_log}"

    async def test_three_concurrent_approvals_all_serialize(self) -> None:
        """Three concurrent calls should execute strictly in sequence."""
        call_log: list[tuple[str, str]] = []
        lock = asyncio.Lock()
        confirm, _, _ = _build_confirm_destructive(lock, ["y", "s", "a"], call_log=call_log, prompt_delay=0.03)

        results = await asyncio.gather(
            confirm(_make_verdict("tool_a")),
            confirm(_make_verdict("tool_b")),
            confirm(_make_verdict("tool_c")),
        )
        assert all(results)

        enters = [i for i, (action, _) in enumerate(call_log) if action == "enter"]
        exits = [i for i, (action, _) in enumerate(call_log) if action == "exit"]

        assert len(enters) == 3
        for j in range(len(enters) - 1):
            assert exits[j] < enters[j + 1], f"Call {j} and {j + 1} overlapped: {call_log}"

    async def test_lock_released_on_keyboard_interrupt(self) -> None:
        """Lock must be released when prompt raises KeyboardInterrupt (Ctrl+C)."""
        lock = asyncio.Lock()
        call_count = 0

        async def _confirm_with_lock(verdict: SafetyVerdict) -> bool:
            nonlocal call_count
            async with lock:
                call_count += 1
                if call_count == 1:
                    raise KeyboardInterrupt
                return True

        with pytest.raises(KeyboardInterrupt):
            await _confirm_with_lock(_make_verdict("tool_a"))

        result = await asyncio.wait_for(_confirm_with_lock(_make_verdict("tool_b")), timeout=1.0)
        assert result is True

    async def test_lock_released_on_eof(self) -> None:
        """Lock must be released when prompt raises EOFError (Escape key)."""
        lock = asyncio.Lock()
        call_count = 0

        async def _confirm_with_lock(verdict: SafetyVerdict) -> bool:
            nonlocal call_count
            async with lock:
                call_count += 1
                if call_count == 1:
                    raise EOFError
                return True

        with pytest.raises(EOFError):
            await _confirm_with_lock(_make_verdict("tool_a"))

        result = await asyncio.wait_for(_confirm_with_lock(_make_verdict("tool_b")), timeout=1.0)
        assert result is True


@pytest.mark.asyncio
class TestApprovalResponses:
    """All approval response paths work correctly under the lock."""

    async def test_allow_once_returns_true(self) -> None:
        lock = asyncio.Lock()
        confirm, perms, persisted = _build_confirm_destructive(lock, ["y"])
        assert await confirm(_make_verdict("bash")) is True
        assert "bash" not in perms
        assert persisted == []

    async def test_allow_session_grants_permission(self) -> None:
        lock = asyncio.Lock()
        confirm, perms, persisted = _build_confirm_destructive(lock, ["s"])
        assert await confirm(_make_verdict("bash")) is True
        assert "bash" in perms
        assert persisted == []

    async def test_allow_always_grants_and_persists(self) -> None:
        lock = asyncio.Lock()
        confirm, perms, persisted = _build_confirm_destructive(lock, ["a"])
        assert await confirm(_make_verdict("bash")) is True
        assert "bash" in perms
        assert persisted == ["bash"]

    async def test_deny_returns_false(self) -> None:
        lock = asyncio.Lock()
        confirm, perms, _ = _build_confirm_destructive(lock, ["n"])
        assert await confirm(_make_verdict("bash")) is False
        assert "bash" not in perms

    async def test_empty_input_denies(self) -> None:
        lock = asyncio.Lock()
        confirm, _, _ = _build_confirm_destructive(lock, [""])
        assert await confirm(_make_verdict("bash")) is False

    async def test_full_word_responses(self) -> None:
        """'yes', 'always', 'session' should work same as shortcuts."""
        lock = asyncio.Lock()
        confirm, perms, persisted = _build_confirm_destructive(lock, ["yes", "session", "always"])
        assert await confirm(_make_verdict("tool1")) is True
        assert "tool1" not in perms

        assert await confirm(_make_verdict("tool2")) is True
        assert "tool2" in perms

        assert await confirm(_make_verdict("tool3")) is True
        assert "tool3" in perms
        assert persisted == ["tool3"]

    async def test_mixed_concurrent_allow_and_deny(self) -> None:
        """One allow and one deny in concurrent calls — both complete correctly."""
        call_log: list[tuple[str, str]] = []
        lock = asyncio.Lock()
        confirm, _, _ = _build_confirm_destructive(lock, ["y", "n"], call_log=call_log, prompt_delay=0.03)

        results = await asyncio.gather(
            confirm(_make_verdict("tool_a")),
            confirm(_make_verdict("tool_b")),
        )

        assert True in results
        assert False in results
        enters = [i for i, (action, _) in enumerate(call_log) if action == "enter"]
        exits = [i for i, (action, _) in enumerate(call_log) if action == "exit"]
        assert exits[0] < enters[1], f"Calls overlapped: {call_log}"

    async def test_session_permission_shared_across_calls(self) -> None:
        """After granting session permission, the set reflects it for later checks."""
        lock = asyncio.Lock()
        confirm, perms, _ = _build_confirm_destructive(lock, ["s", "y"])

        await confirm(_make_verdict("mcp__server__tool"))
        assert "mcp__server__tool" in perms

        await confirm(_make_verdict("bash"))
        assert "mcp__server__tool" in perms
        assert "bash" not in perms


@pytest.mark.asyncio
class TestApprovalVerdictTypes:
    """Approval handles different verdict detail types."""

    async def test_command_verdict(self) -> None:
        lock = asyncio.Lock()
        confirm, _, _ = _build_confirm_destructive(lock, ["y"])
        verdict = _make_verdict("bash", command="git reset --hard")
        assert await confirm(verdict) is True

    async def test_path_verdict(self) -> None:
        lock = asyncio.Lock()
        confirm, _, _ = _build_confirm_destructive(lock, ["y"])
        verdict = _make_path_verdict("write_file", path="/etc/hosts")
        assert await confirm(verdict) is True
