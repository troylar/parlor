"""Tests for __main__.py: port conflict handling, --port flag, browser deferral."""

from __future__ import annotations

import errno
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
            mock_load.return_value = (Path("/tmp/config.yaml"), config)
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
            mock_load.return_value = (Path("/tmp/config.yaml"), config)
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
            mock_load.return_value = (Path("/tmp/config.yaml"), config)
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
            mock_load.return_value = (Path("/tmp/config.yaml"), config)
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
            config = load_config()

        assert config.app.port == 3000

    def test_config_file_overrides_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config file port takes precedence over AI_CHAT_PORT env var."""
        from anteroom.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text("ai:\n  base_url: http://localhost:1234\n  api_key: test\napp:\n  port: 7070\n")
        monkeypatch.setenv("AI_CHAT_PORT", "3000")

        with patch("anteroom.config._get_config_path", return_value=config_file):
            config = load_config()

        assert config.app.port == 7070

    def test_no_env_var_uses_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without AI_CHAT_PORT, default port 8080 is used."""
        from anteroom.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text("ai:\n  base_url: http://localhost:1234\n  api_key: test\n")
        monkeypatch.delenv("AI_CHAT_PORT", raising=False)

        with patch("anteroom.config._get_config_path", return_value=config_file):
            config = load_config()

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
            mock_load.return_value = (Path("/tmp/config.yaml"), config)
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
            mock_load.return_value = (Path("/tmp/config.yaml"), config)
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
            mock_load.return_value = (Path("/tmp/config.yaml"), config)
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
            mock_load.return_value = (Path("/tmp/config.yaml"), config)
            with patch("sys.argv", ["aroom", "exec", "hello"]):
                main()

        mock_run_exec.assert_called_once()
        mock_run_web.assert_not_called()
