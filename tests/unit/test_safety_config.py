"""Tests for safety config parsing."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

import yaml

from anteroom.config import SafetyConfig, load_config


def _write_config(tmpdir: Path, config_dict: dict) -> Path:
    config_path = tmpdir / "config.yaml"
    config_path.write_text(yaml.dump(config_dict))
    return config_path


class TestSafetyConfig:
    def test_default_safety_config(self) -> None:
        cfg = SafetyConfig()
        assert cfg.enabled is True
        assert cfg.approval_timeout == 120
        assert cfg.bash.enabled is True
        assert cfg.write_file.enabled is True
        assert cfg.custom_patterns == []
        assert cfg.sensitive_paths == []

    def test_app_config_has_safety(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _write_config(
                Path(tmpdir),
                {"ai": {"base_url": "http://localhost:1234", "api_key": "test"}},
            )
            cfg = load_config(config_path)
            assert isinstance(cfg.safety, SafetyConfig)
            assert cfg.safety.enabled is True

    def test_safety_section_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _write_config(
                Path(tmpdir),
                {
                    "ai": {"base_url": "http://localhost:1234", "api_key": "test"},
                    "safety": {
                        "enabled": True,
                        "approval_timeout": 60,
                        "bash": {"enabled": False},
                        "write_file": {"enabled": True},
                        "custom_patterns": ["docker system prune"],
                        "sensitive_paths": ["~/.my_secret"],
                    },
                },
            )
            cfg = load_config(config_path)
            assert cfg.safety.approval_timeout == 60
            assert cfg.safety.bash.enabled is False
            assert cfg.safety.write_file.enabled is True
            assert "docker system prune" in cfg.safety.custom_patterns
            assert "~/.my_secret" in cfg.safety.sensitive_paths

    def test_missing_safety_section_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _write_config(
                Path(tmpdir),
                {"ai": {"base_url": "http://localhost:1234", "api_key": "test"}},
            )
            cfg = load_config(config_path)
            assert cfg.safety.enabled is True
            assert cfg.safety.approval_timeout == 120

    def test_timeout_clamped_low(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _write_config(
                Path(tmpdir),
                {
                    "ai": {"base_url": "http://localhost:1234", "api_key": "test"},
                    "safety": {"approval_timeout": 1},
                },
            )
            cfg = load_config(config_path)
            assert cfg.safety.approval_timeout == 10

    def test_timeout_clamped_high(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _write_config(
                Path(tmpdir),
                {
                    "ai": {"base_url": "http://localhost:1234", "api_key": "test"},
                    "safety": {"approval_timeout": 9999},
                },
            )
            cfg = load_config(config_path)
            assert cfg.safety.approval_timeout == 600

    def test_env_var_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _write_config(
                Path(tmpdir),
                {"ai": {"base_url": "http://localhost:1234", "api_key": "test"}},
            )
            with mock.patch.dict(os.environ, {"AI_CHAT_SAFETY_ENABLED": "false"}):
                cfg = load_config(config_path)
                assert cfg.safety.enabled is False

    def test_safety_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _write_config(
                Path(tmpdir),
                {
                    "ai": {"base_url": "http://localhost:1234", "api_key": "test"},
                    "safety": {"enabled": False},
                },
            )
            cfg = load_config(config_path)
            assert cfg.safety.enabled is False

    def test_default_approval_mode(self) -> None:
        cfg = SafetyConfig()
        assert cfg.approval_mode == "ask_for_writes"

    def test_approval_mode_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _write_config(
                Path(tmpdir),
                {
                    "ai": {"base_url": "http://localhost:1234", "api_key": "test"},
                    "safety": {"approval_mode": "ask"},
                },
            )
            cfg = load_config(config_path)
            assert cfg.safety.approval_mode == "ask"

    def test_approval_mode_env_var(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _write_config(
                Path(tmpdir),
                {"ai": {"base_url": "http://localhost:1234", "api_key": "test"}},
            )
            with mock.patch.dict(os.environ, {"AI_CHAT_SAFETY_APPROVAL_MODE": "auto"}):
                cfg = load_config(config_path)
                assert cfg.safety.approval_mode == "auto"

    def test_allowed_tools_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _write_config(
                Path(tmpdir),
                {
                    "ai": {"base_url": "http://localhost:1234", "api_key": "test"},
                    "safety": {"allowed_tools": ["write_file", "edit_file"]},
                },
            )
            cfg = load_config(config_path)
            assert cfg.safety.allowed_tools == ["write_file", "edit_file"]

    def test_denied_tools_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _write_config(
                Path(tmpdir),
                {
                    "ai": {"base_url": "http://localhost:1234", "api_key": "test"},
                    "safety": {"denied_tools": ["bash"]},
                },
            )
            cfg = load_config(config_path)
            assert cfg.safety.denied_tools == ["bash"]

    def test_tool_tiers_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _write_config(
                Path(tmpdir),
                {
                    "ai": {"base_url": "http://localhost:1234", "api_key": "test"},
                    "safety": {"tool_tiers": {"create_canvas": "read", "my_mcp_tool": "destructive"}},
                },
            )
            cfg = load_config(config_path)
            assert cfg.safety.tool_tiers == {"create_canvas": "read", "my_mcp_tool": "destructive"}

    def test_defaults_for_new_fields(self) -> None:
        cfg = SafetyConfig()
        assert cfg.allowed_tools == []
        assert cfg.denied_tools == []
        assert cfg.tool_tiers == {}

    def test_invalid_list_types_default_to_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _write_config(
                Path(tmpdir),
                {
                    "ai": {"base_url": "http://localhost:1234", "api_key": "test"},
                    "safety": {"allowed_tools": "not_a_list", "denied_tools": 42},
                },
            )
            cfg = load_config(config_path)
            assert cfg.safety.allowed_tools == []
            assert cfg.safety.denied_tools == []


class TestWriteAllowedTool:
    def test_write_to_new_config(self) -> None:
        from anteroom.config import write_allowed_tool

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.dump({"ai": {"base_url": "http://localhost", "api_key": "test"}}))
            write_allowed_tool("bash", config_path)

            with open(config_path) as f:
                raw = yaml.safe_load(f)
            assert "bash" in raw["safety"]["allowed_tools"]

    def test_append_to_existing_list(self) -> None:
        from anteroom.config import write_allowed_tool

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                yaml.dump(
                    {
                        "ai": {"base_url": "http://localhost", "api_key": "test"},
                        "safety": {"allowed_tools": ["write_file"]},
                    }
                )
            )
            write_allowed_tool("bash", config_path)

            with open(config_path) as f:
                raw = yaml.safe_load(f)
            assert raw["safety"]["allowed_tools"] == ["write_file", "bash"]

    def test_no_duplicate(self) -> None:
        from anteroom.config import write_allowed_tool

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                yaml.dump(
                    {
                        "ai": {"base_url": "http://localhost", "api_key": "test"},
                        "safety": {"allowed_tools": ["bash"]},
                    }
                )
            )
            write_allowed_tool("bash", config_path)

            with open(config_path) as f:
                raw = yaml.safe_load(f)
            assert raw["safety"]["allowed_tools"] == ["bash"]

    def test_preserves_other_config(self) -> None:
        from anteroom.config import write_allowed_tool

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                yaml.dump(
                    {
                        "ai": {"base_url": "http://localhost", "api_key": "test"},
                        "safety": {"enabled": True, "approval_mode": "ask"},
                    }
                )
            )
            write_allowed_tool("bash", config_path)

            with open(config_path) as f:
                raw = yaml.safe_load(f)
            assert raw["safety"]["enabled"] is True
            assert raw["safety"]["approval_mode"] == "ask"
            assert raw["ai"]["base_url"] == "http://localhost"


class TestCliSafetyOverrides:
    """Test that CLI flags properly override SafetyConfig values."""

    def test_approval_mode_override(self) -> None:
        cfg = SafetyConfig()
        assert cfg.approval_mode == "ask_for_writes"
        cfg.approval_mode = "auto"
        assert cfg.approval_mode == "auto"

    def test_allowed_tools_merge(self) -> None:
        cfg = SafetyConfig(allowed_tools=["write_file"])
        extra = [t.strip() for t in "bash,edit_file".split(",") if t.strip()]
        existing = set(cfg.allowed_tools)
        cfg.allowed_tools.extend(t for t in extra if t not in existing)
        assert cfg.allowed_tools == ["write_file", "bash", "edit_file"]

    def test_allowed_tools_no_duplicates(self) -> None:
        cfg = SafetyConfig(allowed_tools=["bash"])
        extra = [t.strip() for t in "bash,write_file".split(",") if t.strip()]
        existing = set(cfg.allowed_tools)
        cfg.allowed_tools.extend(t for t in extra if t not in existing)
        assert cfg.allowed_tools == ["bash", "write_file"]


class TestWriteAllowedToolConcurrency:
    def test_concurrent_writes_no_data_loss(self) -> None:
        """Two concurrent write_allowed_tool calls should both persist."""
        import threading

        from anteroom.config import write_allowed_tool

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.dump({"ai": {"base_url": "http://localhost", "api_key": "test"}}))

            barrier = threading.Barrier(2)

            def write_tool(name: str) -> None:
                barrier.wait()
                write_allowed_tool(name, config_path)

            t1 = threading.Thread(target=write_tool, args=("bash",))
            t2 = threading.Thread(target=write_tool, args=("write_file",))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            with open(config_path) as f:
                raw = yaml.safe_load(f)
            allowed = raw["safety"]["allowed_tools"]
            assert "bash" in allowed
            assert "write_file" in allowed
