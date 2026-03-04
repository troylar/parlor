"""Tests for startup progress file reading/writing."""

from __future__ import annotations

import json
from pathlib import Path

from anteroom.__main__ import _STEP_LABELS, _read_last_progress
from anteroom.app import _write_progress


class TestReadLastProgress:
    def test_missing_file(self, tmp_path: Path) -> None:
        assert _read_last_progress(tmp_path / "nonexistent") is None

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "progress"
        f.write_text("")
        assert _read_last_progress(f) is None

    def test_single_event(self, tmp_path: Path) -> None:
        f = tmp_path / "progress"
        f.write_text(json.dumps({"step": "database", "status": "running"}) + "\n")
        result = _read_last_progress(f)
        assert result == {"step": "database", "status": "running"}

    def test_multiple_events_returns_last(self, tmp_path: Path) -> None:
        f = tmp_path / "progress"
        lines = [
            json.dumps({"step": "database", "status": "running"}),
            json.dumps({"step": "database", "status": "done"}),
            json.dumps({"step": "mcp_servers", "status": "running", "detail": "3 servers"}),
        ]
        f.write_text("\n".join(lines) + "\n")
        result = _read_last_progress(f)
        assert result == {"step": "mcp_servers", "status": "running", "detail": "3 servers"}

    def test_partial_line_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "progress"
        f.write_text(
            json.dumps({"step": "database", "status": "done"}) + "\n"
            + '{"step": "mcp_servers", "sta'  # truncated
        )
        result = _read_last_progress(f)
        assert result == {"step": "database", "status": "done"}

    def test_non_dict_json_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "progress"
        f.write_text(
            json.dumps({"step": "database", "status": "done"}) + "\n"
            + json.dumps([1, 2, 3]) + "\n"
        )
        result = _read_last_progress(f)
        assert result == {"step": "database", "status": "done"}

    def test_blank_lines_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "progress"
        f.write_text(
            "\n\n"
            + json.dumps({"step": "tools", "status": "running"}) + "\n"
            + "\n"
        )
        result = _read_last_progress(f)
        assert result == {"step": "tools", "status": "running"}


class TestWriteProgress:
    def test_appends_ndjson(self, tmp_path: Path) -> None:
        f = tmp_path / "progress"
        _write_progress(f, "database", "running")
        _write_progress(f, "database", "done")
        lines = f.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"step": "database", "status": "running"}
        assert json.loads(lines[1]) == {"step": "database", "status": "done"}

    def test_includes_detail_when_set(self, tmp_path: Path) -> None:
        f = tmp_path / "progress"
        _write_progress(f, "mcp_servers", "running", detail="3 servers")
        event = json.loads(f.read_text().strip())
        assert event["detail"] == "3 servers"

    def test_omits_detail_when_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "progress"
        _write_progress(f, "database", "running")
        event = json.loads(f.read_text().strip())
        assert "detail" not in event

    def test_none_path_is_noop(self) -> None:
        _write_progress(None, "database", "running")  # should not raise

    def test_bad_path_does_not_raise(self) -> None:
        _write_progress(Path("/nonexistent/dir/file"), "database", "running")


class TestStepLabels:
    def test_all_known_steps_have_labels(self) -> None:
        known_steps = {"database", "mcp_servers", "tools", "embeddings", "packs", "artifacts", "ready"}
        assert known_steps == set(_STEP_LABELS.keys())
