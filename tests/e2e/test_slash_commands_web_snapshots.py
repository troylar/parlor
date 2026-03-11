"""Golden snapshots for key web slash-command UI states."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest

from anteroom.db import get_db
from anteroom.services.artifact_storage import create_artifact
from anteroom.services.storage import create_message

try:
    import playwright  # noqa: F401

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

requires_playwright = pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed")

pytestmark = [pytest.mark.e2e]


def _e2e_db(app_server: tuple[str, Path]):
    return get_db(app_server[1] / "chat.db")


def _snapshot_root() -> Path:
    return Path(__file__).resolve().parents[1] / "golden" / "web_slash_ui"


def _actual_root() -> Path:
    return Path("tmp/web_slash_ui_actual")


def _snapshot_paths(name: str) -> dict[str, Path]:
    root = _snapshot_root()
    return {
        "png": root / f"{name}.png",
        "html": root / f"{name}.html",
    }


def _normalize_picker_html(html: str) -> str:
    html = html.replace(' style="display: flex;"', "")
    html = html.replace(' style=""', "")
    html = re.sub(r'data-command-item-key="[^"]+"', 'data-command-item-key="<key>"', html)
    html = re.sub(r"snapshot-space-delete-[0-9a-f]{8}", "snapshot-space-delete-<id>", html)
    html = re.sub(r"snapshot-space-[0-9a-f]{8}", "snapshot-space-<id>", html)
    html = re.sub(r"snapshot-[0-9a-f]{8}", "snapshot-<id>", html)
    html = re.sub(r"<div class=\"command-item-meta\">.*?</div>", "", html)
    html = re.sub(r"(<div class=\"command-item-title\">)\d+\. ", r"\1", html)
    return "\n".join(line.rstrip() for line in html.strip().splitlines())


def _normalize_surface_html(html: str) -> str:
    html = html.replace(' style=""', "")
    html = re.sub(r"snapshot-space-delete-[0-9a-f]{8}", "snapshot-space-delete-<id>", html)
    html = re.sub(r"snapshot-space-[0-9a-f]{8}", "snapshot-space-<id>", html)
    html = re.sub(r"snapshot-[0-9a-f]{8}", "snapshot-<id>", html)
    return "\n".join(line.rstrip() for line in html.strip().splitlines())


def _assert_snapshot(name: str, *, png_bytes: bytes, html: str, update: bool) -> None:
    paths = _snapshot_paths(name)
    paths["png"].parent.mkdir(parents=True, exist_ok=True)

    payloads = {
        "html": _normalize_picker_html(html).encode("utf-8"),
    }

    missing = [suffix for suffix, path in paths.items() if not path.exists()]
    if update:
        paths["png"].write_bytes(png_bytes)
        for suffix, payload in payloads.items():
            paths[suffix].write_bytes(payload)
    elif not paths["png"].exists():
        paths["png"].write_bytes(png_bytes)
    if missing and not update:
        raise AssertionError(
            f"Missing web UI snapshot(s) for {name}: {', '.join(missing)}. "
            "Re-run with UPDATE_WEB_UI_GOLDENS=1."
        )

    mismatches: list[str] = []
    for suffix, payload in payloads.items():
        expected = paths[suffix].read_bytes()
        if payload != expected:
            mismatches.append(suffix)
            if update:
                paths[suffix].write_bytes(payload)
    if mismatches:
        actual_root = _actual_root()
        actual_root.mkdir(parents=True, exist_ok=True)
        (actual_root / f"{name}.png").write_bytes(png_bytes)
        (actual_root / f"{name}.html").write_bytes(payloads["html"])
        raise AssertionError(
            f"Web UI snapshot {name} diverged: {', '.join(mismatches)}. "
            f"Current payloads written to {actual_root / f'{name}.html'} and {actual_root / f'{name}.png'}. "
            "Re-run with UPDATE_WEB_UI_GOLDENS=1 to accept."
        )


def _assert_surface_snapshot(name: str, *, png_bytes: bytes, html: str, update: bool) -> None:
    paths = _snapshot_paths(name)
    paths["png"].parent.mkdir(parents=True, exist_ok=True)

    payloads = {
        "html": _normalize_surface_html(html).encode("utf-8"),
    }

    missing = [suffix for suffix, path in paths.items() if not path.exists()]
    if update:
        paths["png"].write_bytes(png_bytes)
        for suffix, payload in payloads.items():
            paths[suffix].write_bytes(payload)
    elif not paths["png"].exists():
        paths["png"].write_bytes(png_bytes)
    if missing and not update:
        raise AssertionError(
            f"Missing web UI snapshot(s) for {name}: {', '.join(missing)}. "
            "Re-run with UPDATE_WEB_UI_GOLDENS=1."
        )

    mismatches: list[str] = []
    for suffix, payload in payloads.items():
        expected = paths[suffix].read_bytes()
        if payload != expected:
            mismatches.append(suffix)
            if update:
                paths[suffix].write_bytes(payload)
    if mismatches:
        actual_root = _actual_root()
        actual_root.mkdir(parents=True, exist_ok=True)
        (actual_root / f"{name}.png").write_bytes(png_bytes)
        (actual_root / f"{name}.html").write_bytes(payloads["html"])
        raise AssertionError(
            f"Web UI snapshot {name} diverged: {', '.join(mismatches)}. "
            f"Current payloads written to {actual_root / f'{name}.html'} and {actual_root / f'{name}.png'}. "
            "Re-run with UPDATE_WEB_UI_GOLDENS=1 to accept."
        )


def _prepare_snapshot_page(page) -> None:
    page.set_viewport_size({"width": 1440, "height": 1100})
    page.add_style_tag(
        content="""
        *, *::before, *::after {
            animation: none !important;
            transition: none !important;
            caret-color: transparent !important;
        }
        .command-item-meta {
            display: none !important;
        }
        """
    )


def _install_chat_stream_mock(page, calls: list[list[dict]]) -> None:
    page.evaluate(
        """(calls) => {
            if (!window.__anteroomOrigFetchForE2E) {
                window.__anteroomOrigFetchForE2E = window.fetch.bind(window);
            }
            const encoder = new TextEncoder();
            let callIndex = 0;
            window.fetch = (input, init) => {
                const url = typeof input === 'string' ? input : input.url;
                if (!url.includes('/chat')) {
                    return window.__anteroomOrigFetchForE2E(input, init);
                }
                const plan = calls[Math.min(callIndex, calls.length - 1)] || [];
                callIndex += 1;
                const stream = new ReadableStream({
                    start(controller) {
                        (async () => {
                            for (const step of plan) {
                                if (step.delayMs) {
                                    await new Promise(resolve => setTimeout(resolve, step.delayMs));
                                }
                                if (step.close) {
                                    controller.close();
                                    return;
                                }
                                const payload =
                                    `event: ${step.event}\\n` +
                                    `data: ${JSON.stringify(step.data || {})}\\n\\n`;
                                controller.enqueue(encoder.encode(payload));
                            }
                            controller.close();
                        })().catch(error => controller.error(error));
                    },
                });
                return Promise.resolve(
                    new Response(stream, {
                        status: 200,
                        headers: { 'Content-Type': 'text/event-stream' },
                    })
                );
            };
        }""",
        calls,
    )


def _restore_chat_stream_mock(page) -> None:
    page.evaluate(
        """() => {
            if (window.__anteroomOrigFetchForE2E) {
                window.fetch = window.__anteroomOrigFetchForE2E;
                delete window.__anteroomOrigFetchForE2E;
            }
        }"""
    )


def _install_app_api_mock(page) -> None:
    page.evaluate(
        """() => {
            if (!window.__anteroomOrigAppApiForE2E) {
                window.__anteroomOrigAppApiForE2E = App.api.bind(App);
            }
            App.api = async () => ({ ok: true });
        }"""
    )


def _restore_app_api_mock(page) -> None:
    page.evaluate(
        """() => {
            if (window.__anteroomOrigAppApiForE2E) {
                App.api = window.__anteroomOrigAppApiForE2E;
                delete window.__anteroomOrigAppApiForE2E;
            }
        }"""
    )


@requires_playwright
class TestWebSlashCommandSnapshots:
    def test_approval_prompt_snapshot(self, authenticated_page) -> None:
        update = os.getenv("UPDATE_WEB_UI_GOLDENS") == "1"
        page = authenticated_page
        _prepare_snapshot_page(page)

        page.evaluate(
            """() => {
                Chat.showApprovalPrompt({
                    approval_id: 'snapshot-approval',
                    reason: 'Approval needed to run a destructive shell command.',
                    details: { command: 'rm -rf /tmp/project-cache' },
                });
            }"""
        )

        card = page.locator('.approval-prompt[data-approval-id="snapshot-approval"]')
        _assert_surface_snapshot(
            "approval_prompt",
            png_bytes=card.screenshot(),
            html=card.evaluate("el => el.outerHTML"),
            update=update,
        )

    def test_ask_user_prompt_snapshot(self, authenticated_page) -> None:
        update = os.getenv("UPDATE_WEB_UI_GOLDENS") == "1"
        page = authenticated_page
        _prepare_snapshot_page(page)

        page.evaluate(
            """() => {
                Chat.showAskUserPrompt({
                    ask_id: 'snapshot-ask',
                    question: 'Which environment should I deploy to?',
                    options: ['staging', 'production'],
                });
            }"""
        )

        card = page.locator('.ask-user-prompt[data-ask-id="snapshot-ask"]')
        _assert_surface_snapshot(
            "ask_user_prompt",
            png_bytes=card.screenshot(),
            html=card.evaluate("el => el.outerHTML"),
            update=update,
        )

    def test_approval_allowed_snapshot(self, authenticated_page) -> None:
        update = os.getenv("UPDATE_WEB_UI_GOLDENS") == "1"
        page = authenticated_page
        _prepare_snapshot_page(page)
        _install_app_api_mock(page)
        try:
            page.evaluate(
                """() => {
                    Chat.showApprovalPrompt({
                        approval_id: 'snapshot-approval-allowed',
                        reason: 'Approval needed to run a destructive shell command.',
                        details: { command: 'rm -rf /tmp/project-cache' },
                    });
                }"""
            )
            page.locator('.approval-prompt[data-approval-id="snapshot-approval-allowed"] .approval-session').click()
            page.wait_for_function(
                """
                () => {
                    const el = document.querySelector(
                        '.approval-prompt[data-approval-id="snapshot-approval-allowed"] .approval-status'
                    );
                    return !!el && el.textContent.includes('Allowed for Session');
                }
                """,
                timeout=5000,
            )

            card = page.locator('.approval-prompt[data-approval-id="snapshot-approval-allowed"]')
            _assert_surface_snapshot(
                "approval_allowed",
                png_bytes=card.screenshot(),
                html=card.evaluate("el => el.outerHTML"),
                update=update,
            )
        finally:
            _restore_app_api_mock(page)

    def test_ask_user_answered_snapshot(self, authenticated_page) -> None:
        update = os.getenv("UPDATE_WEB_UI_GOLDENS") == "1"
        page = authenticated_page
        _prepare_snapshot_page(page)
        _install_app_api_mock(page)
        try:
            page.evaluate(
                """() => {
                    Chat.showAskUserPrompt({
                        ask_id: 'snapshot-ask-answered',
                        question: 'Which environment should I deploy to?',
                        options: ['staging', 'production'],
                    });
                }"""
            )
            page.locator(
                '.ask-user-prompt[data-ask-id="snapshot-ask-answered"] '
                + '.ask-user-option:has-text("staging")'
            ).click()
            page.wait_for_function(
                """
                () => {
                    const el = document.querySelector(
                        '.ask-user-prompt[data-ask-id="snapshot-ask-answered"] .ask-user-status'
                    );
                    return !!el && el.textContent.includes('staging');
                }
                """,
                timeout=5000,
            )

            card = page.locator('.ask-user-prompt[data-ask-id="snapshot-ask-answered"]')
            _assert_surface_snapshot(
                "ask_user_answered",
                png_bytes=card.screenshot(),
                html=card.evaluate("el => el.outerHTML"),
                update=update,
            )
        finally:
            _restore_app_api_mock(page)

    def test_retrying_phase_snapshot(self, authenticated_page, api_client) -> None:
        update = os.getenv("UPDATE_WEB_UI_GOLDENS") == "1"
        page = authenticated_page
        _prepare_snapshot_page(page)

        conv = api_client.post("/api/conversations", json={"title": "Snapshot Retry Phase"}).json()
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(300)

        _install_chat_stream_mock(
            page,
            [[
                {"event": "retrying", "data": {"attempt": 2, "max_attempts": 3}},
                {"delayMs": 1000, "event": "token", "data": {"content": "Recovered."}},
                {"event": "done", "data": {}},
            ]],
        )
        try:
            page.locator("#message-input").fill("Trigger retry phase")
            page.locator("#message-input").press("Enter")
            page.wait_for_function(
                "() => (document.getElementById('thinking-phase')?.textContent || '').includes('retry 2/3')",
                timeout=10000,
            )

            thinking = page.locator("#thinking")
            _assert_surface_snapshot(
                "retrying_phase",
                png_bytes=thinking.screenshot(),
                html=thinking.evaluate("el => el.outerHTML"),
                update=update,
            )
        finally:
            _restore_chat_stream_mock(page)

    def test_watchdog_timeout_snapshot(self, authenticated_page, api_client) -> None:
        update = os.getenv("UPDATE_WEB_UI_GOLDENS") == "1"
        page = authenticated_page
        _prepare_snapshot_page(page)

        conv = api_client.post("/api/conversations", json={"title": "Snapshot Watchdog Timeout"}).json()
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(300)
        page.evaluate(
            """() => {
                if (window.__origSetTimeoutForE2E) return;
                window.__origSetTimeoutForE2E = window.setTimeout.bind(window);
                window.setTimeout = (fn, ms, ...args) => {
                    const clamped = ms >= 45000 ? 250 : ms;
                    return window.__origSetTimeoutForE2E(fn, clamped, ...args);
                };
            }"""
        )

        _install_chat_stream_mock(page, [[{"delayMs": 1000, "close": True}]])
        try:
            page.locator("#message-input").fill("Trigger timeout snapshot")
            page.locator("#message-input").press("Enter")
            page.wait_for_function(
                """
                () => Array.from(document.querySelectorAll('.toast')).some(
                    el => (el.textContent || '').includes('No response from server after')
                )
                """,
                timeout=10000,
            )

            toast = page.locator(".toast").last
            _assert_surface_snapshot(
                "watchdog_timeout_toast",
                png_bytes=toast.screenshot(),
                html=toast.evaluate("el => el.outerHTML"),
                update=update,
            )
        finally:
            _restore_chat_stream_mock(page)
            page.evaluate(
                """() => {
                    if (window.__origSetTimeoutForE2E) {
                        window.setTimeout = window.__origSetTimeoutForE2E;
                        delete window.__origSetTimeoutForE2E;
                    }
                }"""
            )

    def test_tool_batch_running_snapshot(self, authenticated_page, api_client) -> None:
        update = os.getenv("UPDATE_WEB_UI_GOLDENS") == "1"
        page = authenticated_page
        _prepare_snapshot_page(page)

        conv = api_client.post("/api/conversations", json={"title": "Snapshot Tool Batch"}).json()
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(300)

        _install_chat_stream_mock(
            page,
            [[
                {"event": "tool_batch_start", "data": {"call_count": 2}},
                {
                    "event": "tool_call_start",
                    "data": {"id": "tool-read-snap", "tool_name": "read_file", "input": {"path": "src/app.py"}},
                },
                {
                    "event": "tool_call_end",
                    "data": {"id": "tool-read-snap", "status": "success", "output": {"text": "ok"}},
                },
                {
                    "event": "tool_call_start",
                    "data": {"id": "tool-search-snap", "tool_name": "grep", "input": {"pattern": "TODO"}},
                },
                {
                    "delayMs": 800,
                    "event": "tool_call_end",
                    "data": {"id": "tool-search-snap", "status": "success", "output": {"matches": 3}},
                },
                {"event": "tool_batch_end", "data": {"call_count": 2, "elapsed_seconds": 1.4}},
                {"event": "done", "data": {}},
            ]],
        )
        try:
            page.locator("#message-input").fill("Trigger tool batch snapshot")
            page.locator("#message-input").press("Enter")
            page.wait_for_function(
                """
                () => {
                    const batch = document.querySelector('.tool-batch[data-status="running"]');
                    return !!batch && batch.textContent.includes("Searching 'TODO'");
                }
                """,
                timeout=10000,
            )

            batch = page.locator(".tool-batch").last
            _assert_surface_snapshot(
                "tool_batch_running",
                png_bytes=batch.screenshot(),
                html=batch.evaluate("el => el.outerHTML"),
                update=update,
            )
        finally:
            _restore_chat_stream_mock(page)

    def test_subagent_progress_snapshot(self, authenticated_page, api_client) -> None:
        update = os.getenv("UPDATE_WEB_UI_GOLDENS") == "1"
        page = authenticated_page
        _prepare_snapshot_page(page)

        conv = api_client.post("/api/conversations", json={"title": "Snapshot Subagent"}).json()
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(300)

        _install_chat_stream_mock(
            page,
            [[
                {
                    "event": "tool_call_start",
                    "data": {"id": "run-agent-snap", "tool_name": "run_agent", "input": {"prompt": "Audit the config"}},
                },
                {
                    "event": "subagent_event",
                    "data": {
                        "kind": "subagent_start",
                        "agent_id": "agent-1",
                        "model": "gpt-5-mini",
                        "prompt": "Audit the config",
                    },
                },
                {
                    "event": "subagent_event",
                    "data": {"kind": "tool_call_start", "agent_id": "agent-1", "tool_name": "grep"},
                },
                {"delayMs": 900, "event": "subagent_event", "data": {
                    "kind": "subagent_end",
                    "agent_id": "agent-1",
                    "elapsed_seconds": 1.2,
                    "tool_calls": [{"tool_name": "grep"}],
                }},
                {
                    "event": "tool_call_end",
                    "data": {"id": "run-agent-snap", "status": "success", "output": {"result": "ok"}},
                },
                {"event": "done", "data": {}},
            ]],
        )
        try:
            page.locator("#message-input").fill("Trigger subagent snapshot")
            page.locator("#message-input").press("Enter")
            page.wait_for_function(
                """
                () => {
                    const card = document.querySelector('.subagent-card');
                    return !!card && card.textContent.includes('Audit the config') && card.textContent.includes('grep');
                }
                """,
                timeout=10000,
            )

            card = page.locator(".tool-call-subagent").last
            _assert_surface_snapshot(
                "subagent_progress",
                png_bytes=card.screenshot(),
                html=card.evaluate("el => el.outerHTML"),
                update=update,
            )
        finally:
            _restore_chat_stream_mock(page)

    def test_interrupted_stream_error_snapshot(self, authenticated_page, api_client) -> None:
        update = os.getenv("UPDATE_WEB_UI_GOLDENS") == "1"
        page = authenticated_page
        _prepare_snapshot_page(page)

        conv = api_client.post("/api/conversations", json={"title": "Snapshot Interrupted Stream"}).json()
        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(300)

        _install_chat_stream_mock(
            page,
            [[
                {"event": "token", "data": {"content": "Partial answer"}},
                {"delayMs": 200, "event": "error", "data": {"message": "stream interrupted for test"}},
            ]],
        )
        try:
            page.locator("#message-input").fill("Trigger interrupted snapshot")
            page.locator("#message-input").press("Enter")
            page.wait_for_selector(".message.assistant .error-message", timeout=10000)

            bubble = page.locator(".message.assistant").last
            _assert_surface_snapshot(
                "interrupted_stream_error",
                png_bytes=bubble.screenshot(),
                html=bubble.evaluate("el => el.outerHTML"),
                update=update,
            )
        finally:
            _restore_chat_stream_mock(page)

    def test_conversation_picker_snapshot(self, authenticated_page, api_client) -> None:
        update = os.getenv("UPDATE_WEB_UI_GOLDENS") == "1"
        page = authenticated_page
        _prepare_snapshot_page(page)

        for title in ("Snapshot Alpha Thread", "Snapshot Beta Thread", "Snapshot Gamma Thread"):
            resp = api_client.post("/api/conversations", json={"title": title})
            resp.raise_for_status()

        page.locator("#message-input").fill("/search Snapshot Beta Thread")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)

        overlay = page.locator(".command-picker-overlay")
        _assert_snapshot(
            "conversation_picker",
            png_bytes=overlay.screenshot(),
            html=overlay.evaluate("el => el.outerHTML"),
            update=update,
        )

    def test_delete_confirmation_snapshot(self, authenticated_page, api_client) -> None:
        update = os.getenv("UPDATE_WEB_UI_GOLDENS") == "1"
        page = authenticated_page
        _prepare_snapshot_page(page)

        resp = api_client.post("/api/conversations", json={"title": "Snapshot Delete Thread"})
        resp.raise_for_status()

        page.locator("#message-input").fill("/search Snapshot Delete Thread")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)

        target_card = page.locator('.command-item:has-text("Snapshot Delete Thread")').first
        target_card.locator('.command-item-action:has-text("Delete")').click()
        page.wait_for_timeout(150)

        overlay = page.locator(".command-picker-overlay")
        _assert_snapshot(
            "delete_confirmation",
            png_bytes=overlay.screenshot(),
            html=overlay.evaluate("el => el.outerHTML"),
            update=update,
        )

    def test_skill_picker_snapshot(self, authenticated_page) -> None:
        update = os.getenv("UPDATE_WEB_UI_GOLDENS") == "1"
        page = authenticated_page
        _prepare_snapshot_page(page)

        page.locator("#message-input").fill("/skills")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)
        page.locator(".command-picker-input").fill("cleanup")
        page.wait_for_timeout(200)

        overlay = page.locator(".command-picker-overlay")
        _assert_snapshot(
            "skill_picker",
            png_bytes=overlay.screenshot(),
            html=overlay.evaluate("el => el.outerHTML"),
            update=update,
        )

    def test_space_picker_snapshot(self, authenticated_page, api_client) -> None:
        update = os.getenv("UPDATE_WEB_UI_GOLDENS") == "1"
        page = authenticated_page
        _prepare_snapshot_page(page)

        suffix = uuid.uuid4().hex[:8]
        api_client.post("/api/spaces", json={"name": f"snapshot-space-{suffix}"}).raise_for_status()

        page.locator("#message-input").fill("/spaces")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)
        page.locator(".command-picker-input").fill(f"snapshot-space-{suffix}")
        page.wait_for_timeout(200)

        overlay = page.locator(".command-picker-overlay")
        _assert_snapshot(
            "space_picker",
            png_bytes=overlay.screenshot(),
            html=overlay.evaluate("el => el.outerHTML"),
            update=update,
        )

    def test_artifact_delete_confirmation_snapshot(self, authenticated_page, app_server) -> None:
        update = os.getenv("UPDATE_WEB_UI_GOLDENS") == "1"
        page = authenticated_page
        _prepare_snapshot_page(page)

        suffix = uuid.uuid4().hex[:8]
        fqn = f"@e2e/skill/snapshot-{suffix}"
        create_artifact(_e2e_db(app_server), fqn, "skill", "e2e", f"snapshot-{suffix}", "content", source="local")

        page.locator("#message-input").fill("/artifacts")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)
        page.locator(".command-picker-input").fill(f"snapshot-{suffix}")
        page.wait_for_timeout(200)

        target_card = page.locator(f'.command-item:has-text("{fqn}")').first
        target_card.locator('.command-item-action:has-text("Delete")').click()
        page.wait_for_timeout(150)

        overlay = page.locator(".command-picker-overlay")
        _assert_snapshot(
            "artifact_delete_confirmation",
            png_bytes=overlay.screenshot(),
            html=overlay.evaluate("el => el.outerHTML"),
            update=update,
        )

    def test_space_delete_confirmation_snapshot(self, authenticated_page, api_client) -> None:
        update = os.getenv("UPDATE_WEB_UI_GOLDENS") == "1"
        page = authenticated_page
        _prepare_snapshot_page(page)

        suffix = uuid.uuid4().hex[:8]
        target_space = api_client.post("/api/spaces", json={"name": f"snapshot-space-delete-{suffix}"}).json()

        page.locator("#message-input").fill("/spaces")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)
        page.locator(".command-picker-input").fill(target_space["name"])
        page.wait_for_timeout(200)

        target_card = page.locator(f'.command-item:has-text("{target_space["name"]}")').first
        target_card.locator('.command-item-action:has-text("Delete")').click()
        page.wait_for_timeout(150)

        overlay = page.locator(".command-picker-overlay")
        _assert_snapshot(
            "space_delete_confirmation",
            png_bytes=overlay.screenshot(),
            html=overlay.evaluate("el => el.outerHTML"),
            update=update,
        )

    def test_space_switch_toast_snapshot(self, authenticated_page, api_client) -> None:
        update = os.getenv("UPDATE_WEB_UI_GOLDENS") == "1"
        page = authenticated_page
        _prepare_snapshot_page(page)

        suffix = uuid.uuid4().hex[:8]
        space = api_client.post("/api/spaces", json={"name": f"snapshot-space-{suffix}"}).json()
        conv = api_client.post("/api/conversations", json={"title": f"Snapshot Space Target {suffix}"}).json()

        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(500)

        page.locator("#message-input").fill("/spaces")
        page.locator("#message-input").press("Enter")
        page.wait_for_selector(".command-picker-overlay", state="visible", timeout=10000)
        target_card = page.locator(f'.command-item:has-text("{space["name"]}")').first
        target_card.locator('.command-item-action:has-text("Switch")').click()
        page.wait_for_selector(".command-picker-overlay", state="hidden", timeout=10000)
        page.wait_for_timeout(300)

        toast = page.locator(".toast").last
        _assert_surface_snapshot(
            "space_switch_toast",
            png_bytes=toast.screenshot(),
            html=toast.evaluate("el => el.outerHTML"),
            update=update,
        )

    def test_rewind_handoff_snapshot(self, authenticated_page, api_client, app_server) -> None:
        update = os.getenv("UPDATE_WEB_UI_GOLDENS") == "1"
        page = authenticated_page
        _prepare_snapshot_page(page)

        conv = api_client.post("/api/conversations", json={"title": "Snapshot Rewind Thread"}).json()
        db = _e2e_db(app_server)
        create_message(db, conv["id"], "user", "Question one.")
        create_message(db, conv["id"], "assistant", "Answer one.")
        create_message(db, conv["id"], "user", "Question two.")
        create_message(db, conv["id"], "assistant", "Answer two.")

        page.evaluate(
            """(convId) => {
                if (typeof App !== 'undefined' && App.loadConversation) {
                    return App.loadConversation(convId);
                }
                return null;
            }""",
            conv["id"],
        )
        page.wait_for_timeout(500)

        page.locator("#message-input").fill("/rewind 1")
        page.locator("#message-input").press("Enter")
        page.wait_for_timeout(500)

        messages = page.locator("#messages-container")
        _assert_surface_snapshot(
            "rewind_handoff",
            png_bytes=messages.screenshot(),
            html=messages.evaluate("el => el.outerHTML"),
            update=update,
        )
