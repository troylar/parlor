"""PTY-level smoke tests for the legacy prompt-toolkit CLI."""

from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Generator
from urllib.parse import urlparse

import pytest

pexpect = pytest.importorskip("pexpect")

pytestmark = [pytest.mark.e2e]


class _FakeOpenAIHandler(BaseHTTPRequestHandler):
    """Tiny fake OpenAI-compatible endpoint for CLI startup validation."""

    model = "gpt-5.2"
    answer_chunks = ["Donald J. Trump"]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") not in {"/v1/models", "/models"}:
            self.send_response(404)
            self.end_headers()
            return

        body = json.dumps({"object": "list", "data": [{"id": self.model, "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") not in {"/v1/chat/completions", "/chat/completions"}:
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length else b"{}"
        payload = json.loads(raw_body.decode("utf-8") or "{}")
        if not payload.get("stream"):
            self.send_response(400)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        for idx, content in enumerate(self.answer_chunks):
            chunk = {
                "id": f"chatcmpl-test-{idx}",
                "object": "chat.completion.chunk",
                "choices": [{"delta": {"content": content}, "index": 0, "finish_reason": None}],
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
            self.wfile.flush()
            time.sleep(0.05)

        done_chunk = {
            "id": "chatcmpl-test-done",
            "object": "chat.completion.chunk",
            "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}],
        }
        self.wfile.write(f"data: {json.dumps(done_chunk)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


@pytest.fixture()
def fake_openai_base_url() -> Generator[str, None, None]:
    server = HTTPServer(("127.0.0.1", 0), _FakeOpenAIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _write_cli_config(home: Path, base_url: str) -> None:
    data_dir = home / ".anteroom"
    data_dir.mkdir()
    (data_dir / "config.yaml").write_text(
        "\n".join(
            [
                "ai:",
                f"  base_url: {base_url}",
                "  api_key: test-key",
                "  model: gpt-5.2",
                "embeddings:",
                "  enabled: false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


@contextmanager
def _spawn_legacy_cli(
    home: Path, *, extra_pythonpath: Path | None = None
) -> Generator[tuple[pexpect.spawn, io.StringIO], None, None]:
    transcript = io.StringIO()
    env = os.environ.copy()
    env["HOME"] = str(home)
    pythonpath_parts = ["src"]
    if extra_pythonpath is not None:
        pythonpath_parts.insert(0, str(extra_pythonpath))
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    env["TERM"] = "xterm-256color"

    child = pexpect.spawn(
        sys.executable,
        ["-m", "anteroom", "chat", "--ui", "legacy", "--no-project-context"],
        cwd=str(Path(__file__).resolve().parents[2]),
        env=env,
        encoding="utf-8",
        timeout=20,
    )
    child.logfile = transcript
    try:
        yield child, transcript
    finally:
        if child.isalive():
            child.terminate(force=True)


@contextmanager
def _spawn_textual_cli(
    home: Path,
    *args: str,
    extra_pythonpath: Path | None = None,
) -> Generator[tuple[pexpect.spawn, io.StringIO], None, None]:
    transcript = io.StringIO()
    env = os.environ.copy()
    env["HOME"] = str(home)
    pythonpath_parts = ["src"]
    if extra_pythonpath is not None:
        pythonpath_parts.insert(0, str(extra_pythonpath))
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    env["TERM"] = "xterm-256color"

    child = pexpect.spawn(
        sys.executable,
        ["-m", "anteroom", "chat", "--ui", "textual", "--no-project-context", *args],
        cwd=str(Path(__file__).resolve().parents[2]),
        env=env,
        encoding="utf-8",
        timeout=25,
    )
    child.logfile = transcript
    try:
        yield child, transcript
    finally:
        if child.isalive():
            child.terminate(force=True)


def test_legacy_cli_up_arrow_recalls_previous_command(tmp_path: Path, fake_openai_base_url: str) -> None:
    _write_cli_config(tmp_path, fake_openai_base_url)

    with _spawn_legacy_cli(tmp_path) as (child, transcript):
        child.expect("❯")
        child.sendline("/help")
        child.expect("/new")
        child.expect("❯")

        child.send("\x1b[A")
        child.expect("/help")

    assert transcript.getvalue().count("/help") >= 2


def test_legacy_cli_tab_completes_slash_commands(tmp_path: Path, fake_openai_base_url: str) -> None:
    _write_cli_config(tmp_path, fake_openai_base_url)

    with _spawn_legacy_cli(tmp_path) as (child, transcript):
        child.expect("❯")
        child.send("/he")
        child.send("\t")
        child.expect("/help")

        # The first Ctrl+C clears the completed buffer; after that, use the
        # normal empty-prompt exit guard.
        child.sendcontrol("c")
        child.expect("❯")
        time.sleep(2.1)
        child.sendcontrol("c")
        child.expect("Press Ctrl\\+C again to exit")
        child.sendcontrol("c")
        child.expect(pexpect.EOF)

    assert "/help" in transcript.getvalue()


def test_legacy_cli_empty_prompt_requires_double_ctrl_c_to_exit(
    tmp_path: Path, fake_openai_base_url: str
) -> None:
    _write_cli_config(tmp_path, fake_openai_base_url)

    with _spawn_legacy_cli(tmp_path) as (child, transcript):
        child.expect("❯")
        child.sendcontrol("c")
        child.expect("Press Ctrl\\+C again to exit")
        child.sendcontrol("c")
        child.expect(pexpect.EOF)

    assert "Press Ctrl+C again to exit" in transcript.getvalue()


def test_legacy_cli_ctrl_c_clears_buffer_without_polluting_history(
    tmp_path: Path, fake_openai_base_url: str
) -> None:
    _write_cli_config(tmp_path, fake_openai_base_url)

    with _spawn_legacy_cli(tmp_path) as (child, transcript):
        child.expect("❯")
        child.sendline("/help")
        child.expect("/new")
        child.expect("❯")

        child.send("partial draft")
        child.sendcontrol("c")
        child.send("\x1b[A")
        child.expect("/help")

        # Let the Ctrl+C exit guard reset; the first Ctrl+C above only cleared input.
        time.sleep(2.1)
        child.sendcontrol("c")
        child.expect("Press Ctrl")
        child.sendcontrol("c")
        child.expect(pexpect.EOF)

    assert transcript.getvalue().count("/help") >= 2


def test_legacy_cli_down_arrow_restores_draft_after_history_navigation(
    tmp_path: Path, fake_openai_base_url: str
) -> None:
    _write_cli_config(tmp_path, fake_openai_base_url)

    with _spawn_legacy_cli(tmp_path) as (child, transcript):
        child.expect("❯")
        child.sendline("/help")
        child.expect("/new")
        child.expect("❯")
        child.send("draft follow-up")
        child.send("\x1b[A")
        child.expect("/help")
        child.send("\x1b[B")
        child.expect("draft follow-up")

    assert "draft follow-up" in transcript.getvalue()
    assert transcript.getvalue().count("/help") >= 2


def test_textual_cli_stays_open_after_interactive_response(
    tmp_path: Path, fake_openai_base_url: str
) -> None:
    _write_cli_config(tmp_path, fake_openai_base_url)

    with _spawn_textual_cli(tmp_path) as (child, transcript):
        child.expect("Ask for the next step")
        child.send("who is the president")
        child.send("\r")
        time.sleep(5)
        assert child.isalive(), transcript.getvalue()[-4000:]
        child.sendcontrol("q")
        child.expect(pexpect.EOF)


def test_textual_cli_stays_open_after_initial_prompt_response(
    tmp_path: Path, fake_openai_base_url: str
) -> None:
    _write_cli_config(tmp_path, fake_openai_base_url)

    with _spawn_textual_cli(tmp_path, "who is the president") as (child, transcript):
        time.sleep(5)
        assert child.isalive(), transcript.getvalue()[-4000:]
        child.sendcontrol("q")
        child.expect(pexpect.EOF)
