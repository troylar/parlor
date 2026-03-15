"""Workflow runner registry and result normalization."""

from __future__ import annotations

import logging
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
