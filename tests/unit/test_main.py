"""Tests for __main__.py: port conflict handling, --port flag, --debug flag, browser deferral."""

from __future__ import annotations

import errno
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(port: int = 8080, host: str = "127.0.0.1", tls: bool = False) -> MagicMock:
    config = MagicMock()
    config.app.port = port
    config.app.host = host
    config.app.data_dir = Path("/tmp/anteroom-test")
    config.app.tls = tls
    config.ai.base_url = "http://localhost:1234/v1"
    config.ai.model = "test-model"
    config.mcp_servers = []
    return config


# ---------------------------------------------------------------------------
# Port-in-use error handling
# ---------------------------------------------------------------------------

_PATCHES = [
    "anteroom.__main__.asyncio.run",
    "anteroom.app.create_app",
]


class TestPortInUse:
    def test_port_conflict_shows_message_and_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        """OSError with EADDRINUSE prints actionable message and exits."""
        from anteroom.__main__ import _run_web

        config = _make_config(port=8080)

        with (
            patch(_PATCHES[0]),
            patch(_PATCHES[1], return_value=MagicMock()),
            patch("anteroom.__main__.uvicorn.run", side_effect=OSError(errno.EADDRINUSE, "Address already in use")),
            patch("anteroom.__main__.threading.Thread") as mock_thread,
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_thread.return_value = MagicMock()
            _run_web(config, Path("/tmp/config.yaml"))

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Port 8080 is already in use" in captured.err
        assert "--port 8081" in captured.err
        assert "AI_CHAT_PORT" in captured.err

    def test_port_conflict_different_port(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Port number in error message must reflect the configured port."""
        from anteroom.__main__ import _run_web

        config = _make_config(port=9090)

        with (
            patch(_PATCHES[0]),
            patch(_PATCHES[1], return_value=MagicMock()),
            patch(
                "anteroom.__main__.uvicorn.run",
                side_effect=OSError(errno.EADDRINUSE, "Address already in use"),
            ),
            patch("anteroom.__main__.threading.Thread") as mock_thread,
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_thread.return_value = MagicMock()
            _run_web(config, Path("/tmp/config.yaml"))

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Port 9090 is already in use" in captured.err

    def test_addr_not_avail_shows_host_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        """EADDRNOTAVAIL should show a host-related message, not port-in-use."""
        from anteroom.__main__ import _run_web

        config = _make_config(port=8080)

        with (
            patch(_PATCHES[0]),
            patch(_PATCHES[1], return_value=MagicMock()),
            patch(
                "anteroom.__main__.uvicorn.run",
                side_effect=OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address"),
            ),
            patch("anteroom.__main__.threading.Thread") as mock_thread,
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_thread.return_value = MagicMock()
            _run_web(config, Path("/tmp/config.yaml"))

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "not available" in captured.err
        assert "app.host" in captured.err

    def test_port_65535_suggests_lower(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Port 65535 conflict should suggest 65534, not 65536."""
        from anteroom.__main__ import _run_web

        config = _make_config(port=65535)

        with (
            patch(_PATCHES[0]),
            patch(_PATCHES[1], return_value=MagicMock()),
            patch("anteroom.__main__.uvicorn.run", side_effect=OSError(errno.EADDRINUSE, "Address already in use")),
            patch("anteroom.__main__.threading.Thread") as mock_thread,
            pytest.raises(SystemExit),
        ):
            mock_thread.return_value = MagicMock()
            _run_web(config, Path("/tmp/config.yaml"))

        captured = capsys.readouterr()
        assert "--port 65534" in captured.err

    def test_other_oserror_reraises(self) -> None:
        """OSError with a different errno must not be swallowed."""
        from anteroom.__main__ import _run_web

        config = _make_config()

        with (
            patch(_PATCHES[0]),
            patch(_PATCHES[1], return_value=MagicMock()),
            patch("anteroom.__main__.uvicorn.run", side_effect=OSError(errno.EACCES, "Permission denied")),
            patch("anteroom.__main__.threading.Thread") as mock_thread,
            pytest.raises(OSError, match="Permission denied"),
        ):
            mock_thread.return_value = MagicMock()
            _run_web(config, Path("/tmp/config.yaml"))


# ---------------------------------------------------------------------------
# Browser deferral
# ---------------------------------------------------------------------------


class TestBrowserDeferral:
    def test_browser_opened_via_daemon_thread(self) -> None:
        """webbrowser.open must be called from a daemon thread, not before uvicorn."""
        from anteroom.__main__ import _run_web

        config = _make_config()

        with (
            patch(_PATCHES[0]),
            patch(_PATCHES[1], return_value=MagicMock()),
            patch("anteroom.__main__.uvicorn.run"),
            patch("anteroom.__main__.threading.Thread") as mock_thread_cls,
            patch("anteroom.__main__.webbrowser") as mock_wb,
        ):
            mock_thread_cls.return_value = MagicMock()
            _run_web(config, Path("/tmp/config.yaml"))

        mock_thread_cls.assert_called_once()
        call_kwargs = mock_thread_cls.call_args
        assert call_kwargs.kwargs.get("daemon") is True
        mock_thread_cls.return_value.start.assert_called_once()
        # webbrowser.open should NOT be called directly (only from the thread target)
        mock_wb.open.assert_not_called()

    def test_browser_not_opened_on_port_conflict(self) -> None:
        """If port is in use, browser thread should not reach webbrowser.open."""
        from anteroom.__main__ import _run_web

        config = _make_config()

        with (
            patch(_PATCHES[0]),
            patch(_PATCHES[1], return_value=MagicMock()),
            patch("anteroom.__main__.uvicorn.run", side_effect=OSError(errno.EADDRINUSE, "Address already in use")),
            patch("anteroom.__main__.threading.Thread") as mock_thread_cls,
            patch("anteroom.__main__.webbrowser") as mock_wb,
            pytest.raises(SystemExit),
        ):
            mock_thread_cls.return_value = MagicMock()
            _run_web(config, Path("/tmp/config.yaml"))

        # Direct call should not happen
        mock_wb.open.assert_not_called()


# ---------------------------------------------------------------------------
# --port CLI flag
# ---------------------------------------------------------------------------


class TestPortFlag:
    def test_port_flag_overrides_config(self) -> None:
        """--port flag must override config.app.port before _run_web is called."""
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web") as mock_run_web,
        ):
            config = _make_config(port=8080)
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "--port", "9999"]):
                main()

        assert config.app.port == 9999
        mock_run_web.assert_called_once()

    def test_port_flag_rejects_invalid_port(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--port with out-of-range value must exit with error."""
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
            pytest.raises(SystemExit) as exc_info,
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "--port", "99999"]):
                main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Invalid port" in captured.err

    def test_port_flag_rejects_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--port 0 must be rejected."""
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
            pytest.raises(SystemExit) as exc_info,
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "--port", "0"]):
                main()

        assert exc_info.value.code == 1

    def test_port_flag_not_provided_uses_config(self) -> None:
        """Without --port, config.app.port should remain unchanged."""
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web") as mock_run_web,
        ):
            config = _make_config(port=8080)
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom"]):
                main()

        assert config.app.port == 8080
        mock_run_web.assert_called_once()


# ---------------------------------------------------------------------------
# AI_CHAT_PORT env var
# ---------------------------------------------------------------------------


class TestPortEnvVar:
    def test_env_var_overrides_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """AI_CHAT_PORT env var must override the default port."""
        from anteroom.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text("ai:\n  base_url: http://localhost:1234\n  api_key: test\n")
        monkeypatch.setenv("AI_CHAT_PORT", "3000")

        with patch("anteroom.config._get_config_path", return_value=config_file):
            config, _ = load_config()

        assert config.app.port == 3000

    def test_config_file_overrides_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config file port takes precedence over AI_CHAT_PORT env var."""
        from anteroom.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text("ai:\n  base_url: http://localhost:1234\n  api_key: test\napp:\n  port: 7070\n")
        monkeypatch.setenv("AI_CHAT_PORT", "3000")

        with patch("anteroom.config._get_config_path", return_value=config_file):
            config, _ = load_config()

        assert config.app.port == 7070

    def test_no_env_var_uses_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without AI_CHAT_PORT, default port 8080 is used."""
        from anteroom.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text("ai:\n  base_url: http://localhost:1234\n  api_key: test\n")
        monkeypatch.delenv("AI_CHAT_PORT", raising=False)

        with patch("anteroom.config._get_config_path", return_value=config_file):
            config, _ = load_config()

        assert config.app.port == 8080


# ---------------------------------------------------------------------------
# Exec subcommand dispatch
# ---------------------------------------------------------------------------


class TestExecDispatch:
    def test_exec_dispatches_to_run_exec(self) -> None:
        """'aroom exec' dispatches to _run_exec with correct args."""
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_exec") as mock_run_exec,
        ):
            config = _make_config()
            config.safety.approval_mode = "ask_for_writes"
            config.safety.allowed_tools = []
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "exec", "say hello"]):
                main()

        mock_run_exec.assert_called_once()
        _, kwargs = mock_run_exec.call_args
        assert kwargs["prompt"] == "say hello"

    def test_exec_json_flag(self) -> None:
        """'aroom exec --json' passes output_json=True."""
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_exec") as mock_run_exec,
        ):
            config = _make_config()
            config.safety.approval_mode = "ask_for_writes"
            config.safety.allowed_tools = []
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "exec", "--json", "test"]):
                main()

        mock_run_exec.assert_called_once()
        _, kwargs = mock_run_exec.call_args
        assert kwargs["output_json"] is True

    def test_exec_timeout_flag(self) -> None:
        """'aroom exec --timeout 60' passes timeout=60.0."""
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_exec") as mock_run_exec,
        ):
            config = _make_config()
            config.safety.approval_mode = "ask_for_writes"
            config.safety.allowed_tools = []
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "exec", "--timeout", "60", "prompt"]):
                main()

        mock_run_exec.assert_called_once()
        _, kwargs = mock_run_exec.call_args
        assert kwargs["timeout"] == 60.0

    def test_exec_does_not_dispatch_to_web(self) -> None:
        """'aroom exec' must not fall through to _run_web."""
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_exec") as mock_run_exec,
            patch("anteroom.__main__._run_web") as mock_run_web,
        ):
            config = _make_config()
            config.safety.approval_mode = "ask_for_writes"
            config.safety.allowed_tools = []
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "exec", "hello"]):
                main()

        mock_run_exec.assert_called_once()
        mock_run_web.assert_not_called()


# ---------------------------------------------------------------------------
# --debug flag and AI_CHAT_LOG_LEVEL
# ---------------------------------------------------------------------------


class TestDebugFlag:
    def test_debug_flag_sets_logging_to_debug(self) -> None:
        """--debug flag must configure root logger to DEBUG level."""
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
            patch("anteroom.__main__.logging.basicConfig") as mock_basic,
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "--debug"]):
                main()

        mock_basic.assert_called_once()
        assert mock_basic.call_args.kwargs["level"] == logging.DEBUG

    def test_no_debug_flag_defaults_to_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without --debug or env var, root logger defaults to WARNING."""
        from anteroom.__main__ import main

        monkeypatch.delenv("AI_CHAT_LOG_LEVEL", raising=False)

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
            patch("anteroom.__main__.logging.basicConfig") as mock_basic,
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom"]):
                main()

        mock_basic.assert_called_once()
        assert mock_basic.call_args.kwargs["level"] == logging.WARNING

    def test_env_var_sets_log_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AI_CHAT_LOG_LEVEL=INFO must set root logger to INFO."""
        from anteroom.__main__ import main

        monkeypatch.setenv("AI_CHAT_LOG_LEVEL", "INFO")

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
            patch("anteroom.__main__.logging.basicConfig") as mock_basic,
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom"]):
                main()

        mock_basic.assert_called_once()
        assert mock_basic.call_args.kwargs["level"] == logging.INFO

    def test_debug_flag_overrides_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--debug flag takes priority over AI_CHAT_LOG_LEVEL env var."""
        from anteroom.__main__ import main

        monkeypatch.setenv("AI_CHAT_LOG_LEVEL", "ERROR")

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
            patch("anteroom.__main__.logging.basicConfig") as mock_basic,
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "--debug"]):
                main()

        mock_basic.assert_called_once()
        assert mock_basic.call_args.kwargs["level"] == logging.DEBUG

    def test_invalid_env_var_falls_back_to_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid AI_CHAT_LOG_LEVEL value must fall back to WARNING."""
        from anteroom.__main__ import main

        monkeypatch.setenv("AI_CHAT_LOG_LEVEL", "GARBAGE")

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
            patch("anteroom.__main__.logging.basicConfig") as mock_basic,
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom"]):
                main()

        mock_basic.assert_called_once()
        assert mock_basic.call_args.kwargs["level"] == logging.WARNING

    def test_debug_flag_passes_to_run_web(self) -> None:
        """--debug must be forwarded to _run_web for uvicorn log_level."""
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web") as mock_run_web,
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "--debug"]):
                main()

        mock_run_web.assert_called_once()
        _, kwargs = mock_run_web.call_args
        assert kwargs["debug"] is True

    def test_uvicorn_debug_log_level(self) -> None:
        """_run_web with debug=True must pass log_level='debug' to uvicorn."""
        from anteroom.__main__ import _run_web

        config = _make_config()

        with (
            patch(_PATCHES[0]),
            patch(_PATCHES[1], return_value=MagicMock()),
            patch("anteroom.__main__.uvicorn.run") as mock_uvicorn,
            patch("anteroom.__main__.threading.Thread") as mock_thread,
        ):
            mock_thread.return_value = MagicMock()
            _run_web(config, Path("/tmp/config.yaml"), debug=True)

        mock_uvicorn.assert_called_once()
        assert mock_uvicorn.call_args.kwargs.get("log_level") == "debug"

    def test_uvicorn_default_log_level(self) -> None:
        """_run_web without debug must pass log_level='info' to uvicorn."""
        from anteroom.__main__ import _run_web

        config = _make_config()

        with (
            patch(_PATCHES[0]),
            patch(_PATCHES[1], return_value=MagicMock()),
            patch("anteroom.__main__.uvicorn.run") as mock_uvicorn,
            patch("anteroom.__main__.threading.Thread") as mock_thread,
        ):
            mock_thread.return_value = MagicMock()
            _run_web(config, Path("/tmp/config.yaml"))

        mock_uvicorn.assert_called_once()
        assert mock_uvicorn.call_args.kwargs.get("log_level") == "info"


# ---------------------------------------------------------------------------
# --team-config flag and enforcement
# ---------------------------------------------------------------------------


class TestTeamConfigFlag:
    def test_team_config_flag_passed_to_load(self) -> None:
        """--team-config flag must be forwarded to _load_config_or_exit."""
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
        ):
            config = _make_config()
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "--team-config", "/tmp/team.yaml"]):
                main()

        mock_load.assert_called_once()
        assert mock_load.call_args.kwargs["team_config_path"] == Path("/tmp/team.yaml")

    def test_approval_mode_suppressed_when_enforced(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--approval-mode must be ignored when safety.approval_mode is enforced."""
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
        ):
            config = _make_config()
            config.safety.approval_mode = "ask"
            config.safety.allowed_tools = []
            mock_load.return_value = (Path("/tmp/config.yaml"), config, ["safety.approval_mode"])
            with patch("sys.argv", ["aroom", "--approval-mode", "auto"]):
                main()

        # Should NOT have changed to auto
        assert config.safety.approval_mode == "ask"
        captured = capsys.readouterr()
        assert "enforced by team config" in captured.err

    def test_approval_mode_applied_when_not_enforced(self) -> None:
        """--approval-mode applies normally when not enforced."""
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
        ):
            config = _make_config()
            config.safety.approval_mode = "ask"
            config.safety.allowed_tools = []
            mock_load.return_value = (Path("/tmp/config.yaml"), config, [])
            with patch("sys.argv", ["aroom", "--approval-mode", "auto"]):
                main()

        assert config.safety.approval_mode == "auto"

    def test_port_suppressed_when_enforced(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--port must be ignored when app.port is enforced."""
        from anteroom.__main__ import main

        with (
            patch("anteroom.__main__._load_config_or_exit") as mock_load,
            patch("anteroom.__main__._run_web"),
        ):
            config = _make_config(port=8080)
            mock_load.return_value = (Path("/tmp/config.yaml"), config, ["app.port"])
            with patch("sys.argv", ["aroom", "--port", "9999"]):
                main()

        assert config.app.port == 8080
        captured = capsys.readouterr()
        assert "enforced by team config" in captured.err
